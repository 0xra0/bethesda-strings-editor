"""
Pure-Python BA2 archive handler for Fallout 4 and Starfield.

Supports GNRL (general) archives only — this is the archive type that contains
.strings/.dlstrings/.ilstrings files. DX10 texture archives are detected and
rejected with a clear error.

Compression: zlib for both FO4 v1 and Starfield v2 GNRL archives.
LZ4 (Starfield v3 DX10 textures) is intentionally not implemented because
texture archives never contain string files.

Format notes
------------
FO4 v1 header (24 bytes):  BTDX + version(u32) + type(4s) + count(u32) + names_offset(u64)
Starfield v2/v3 header (32 bytes): same + two unknown u32s (saved and round-tripped)

GNRL file record (36 bytes per file):
  file_hash(u32) + ext(4s) + dir_hash(u32) + flags(u32) +
  data_offset(u64) + packed_size(u32) + unpacked_size(u32) + sentinel(u32=0xBAADF00D)

Name table at names_offset: for each file — u16 length + UTF-8 name bytes.
"""

import logging
import shutil
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Optional

logger = logging.getLogger(__name__)

_MAGIC = b"BTDX"
_GNRL = b"GNRL"
_DX10 = b"DX10"
_SENTINEL = 0xBAADF00D

_HDR_BASE_FMT = "<4sI4sIQ"   # magic, version, type, count, names_offset  (24 bytes)
_HDR_BASE_SIZE = struct.calcsize(_HDR_BASE_FMT)  # 24
_HDR_SF_EXTRA_FMT = "<II"   # two unknowns present in Starfield (v2+)  (8 bytes)

_GNRL_ENTRY_FMT = "<I4sIIQIII"  # 36 bytes
_GNRL_ENTRY_SIZE = struct.calcsize(_GNRL_ENTRY_FMT)  # 36

_STRINGS_EXTS = frozenset({".strings", ".dlstrings", ".ilstrings"})


@dataclass
class _GnrlEntry:
    file_hash: int
    ext: bytes          # 4-byte extension field, e.g. b"stri"
    dir_hash: int
    flags: int
    data_offset: int
    packed_size: int    # 0 means uncompressed
    unpacked_size: int
    name: str = field(default="", compare=False)


class BA2File:
    """
    Read and repack Bethesda BA2 archives (GNRL type).

    Keeps the underlying file open for lazy data reads. Call :meth:`close`
    (or use as a context manager) when done.
    """

    def __init__(self, path: "str | Path"):
        self._path = Path(path)
        self._version: int = 0
        self._archive_type: bytes = b""
        self._sf_unknowns: tuple[int, int] = (0, 0)  # Starfield header extras
        self._entries: list[_GnrlEntry] = []
        self._fh: Optional[IO[bytes]] = None
        self._parse()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_files(self) -> list[str]:
        """Return all internal file paths stored in the archive."""
        return [e.name for e in self._entries]

    def list_strings_files(self) -> list[str]:
        """Return internal paths whose extension is .strings/.dlstrings/.ilstrings."""
        return [
            n for n in self.list_files()
            if Path(n.replace("\\", "/")).suffix.lower() in _STRINGS_EXTS
        ]

    def extract(self, name: str) -> bytes:
        """Return the decompressed bytes for *name*."""
        entry = self._find_entry(name)
        if entry is None:
            raise KeyError(f"{name!r} not found in {self._path.name}")
        return self._read_entry(entry)

    def save_with_replacement(
        self,
        out_path: "str | Path",
        replacements: "dict[str, bytes]",
    ) -> None:
        """Write a new BA2 archive replacing specific files with new content.

        *replacements* maps internal archive names to new uncompressed bytes.
        All other files are copied verbatim (re-read and re-compressed if they
        were originally compressed).  The method is safe to call with
        *out_path* == ``self._path`` (writes to a temporary file first).
        """
        out_path = Path(out_path)
        norm_rep: dict[str, bytes] = {
            _norm(k): v for k, v in replacements.items()
        }
        same_file = out_path.resolve() == self._path.resolve()

        if same_file:
            tmp_fd, tmp_name = tempfile.mkstemp(suffix=".ba2", dir=out_path.parent)
            tmp_path = Path(tmp_name)
            try:
                import os
                os.close(tmp_fd)
                self._write_archive(tmp_path, norm_rep)
                # Re-open original before replacing (flush OS cache)
                self.close()
                shutil.move(str(tmp_path), str(out_path))
                self._path = out_path
                self._reopen()
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
        else:
            self._write_archive(out_path, norm_rep)

    @property
    def version(self) -> int:
        return self._version

    @property
    def archive_type(self) -> str:
        return self._archive_type.decode("ascii", errors="replace").rstrip("\x00")

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # Internal: parsing
    # ------------------------------------------------------------------

    def _parse(self) -> None:
        fh = open(self._path, "rb")
        self._fh = fh

        # Base header (24 bytes)
        raw = fh.read(_HDR_BASE_SIZE)
        if len(raw) < _HDR_BASE_SIZE:
            raise ValueError(f"{self._path.name}: file too small to be a BA2 archive")
        magic, version, arch_type, file_count, names_offset = struct.unpack(
            _HDR_BASE_FMT, raw
        )
        if magic != _MAGIC:
            raise ValueError(
                f"{self._path.name}: bad magic {magic!r} (expected b'BTDX')"
            )
        if arch_type == _DX10:
            raise ValueError(
                f"{self._path.name} is a DX10 (texture) archive — "
                "only GNRL archives contain string files."
            )
        if arch_type != _GNRL:
            raise ValueError(
                f"{self._path.name}: unknown archive type {arch_type!r}"
            )

        self._version = version
        self._archive_type = arch_type

        # Starfield (version >= 2) has two extra uint32 fields in the header
        if version >= 2:
            extra = fh.read(struct.calcsize(_HDR_SF_EXTRA_FMT))
            if len(extra) == struct.calcsize(_HDR_SF_EXTRA_FMT):
                self._sf_unknowns = struct.unpack(_HDR_SF_EXTRA_FMT, extra)

        # File records
        self._entries = []
        for _ in range(file_count):
            raw_rec = fh.read(_GNRL_ENTRY_SIZE)
            if len(raw_rec) < _GNRL_ENTRY_SIZE:
                break
            (
                file_hash, ext, dir_hash, flags,
                data_offset, packed_size, unpacked_size, _,
            ) = struct.unpack(_GNRL_ENTRY_FMT, raw_rec)
            self._entries.append(
                _GnrlEntry(
                    file_hash=file_hash,
                    ext=ext,
                    dir_hash=dir_hash,
                    flags=flags,
                    data_offset=data_offset,
                    packed_size=packed_size,
                    unpacked_size=unpacked_size,
                )
            )

        # Name table
        fh.seek(names_offset)
        for entry in self._entries:
            len_raw = fh.read(2)
            if len(len_raw) < 2:
                break
            (name_len,) = struct.unpack("<H", len_raw)
            name_bytes = fh.read(name_len)
            entry.name = name_bytes.decode("utf-8", errors="replace")

        logger.debug(
            "Parsed BA2 %s: v%d GNRL, %d files",
            self._path.name, version, len(self._entries),
        )

    def _reopen(self) -> None:
        self._fh = open(self._path, "rb")

    # ------------------------------------------------------------------
    # Internal: data reading
    # ------------------------------------------------------------------

    def _find_entry(self, name: str) -> Optional[_GnrlEntry]:
        target = _norm(name)
        for entry in self._entries:
            if _norm(entry.name) == target:
                return entry
        return None

    def _read_entry(self, entry: _GnrlEntry) -> bytes:
        if self._fh is None:
            raise IOError("BA2 file is closed")
        self._fh.seek(entry.data_offset)
        if entry.packed_size == 0:
            return self._fh.read(entry.unpacked_size)
        compressed = self._fh.read(entry.packed_size)
        return zlib.decompress(compressed)

    # ------------------------------------------------------------------
    # Internal: writing
    # ------------------------------------------------------------------

    def _write_archive(self, out_path: Path, norm_rep: "dict[str, bytes]") -> None:
        """Low-level: write a complete new BA2 to *out_path*."""
        with open(out_path, "wb") as out:
            # --- Header ---
            out.write(_MAGIC)
            out.write(struct.pack("<I", self._version))
            out.write(self._archive_type)
            out.write(struct.pack("<I", len(self._entries)))
            names_offset_pos = out.tell()
            out.write(struct.pack("<Q", 0))  # placeholder; patched at end
            if self._version >= 2:
                out.write(struct.pack(_HDR_SF_EXTRA_FMT, *self._sf_unknowns))

            # --- File record placeholders ---
            record_positions: list[int] = []
            for entry in self._entries:
                record_positions.append(out.tell())
                out.write(
                    struct.pack(
                        _GNRL_ENTRY_FMT,
                        entry.file_hash, entry.ext, entry.dir_hash, entry.flags,
                        0, 0, entry.unpacked_size, _SENTINEL,  # offsets filled below
                    )
                )

            # --- File data ---
            final_offsets: list[int] = []
            final_packed: list[int] = []
            final_unpacked: list[int] = []

            for entry in self._entries:
                data_pos = out.tell()
                final_offsets.append(data_pos)

                key = _norm(entry.name)
                if key in norm_rep:
                    raw = norm_rep[key]
                    packed = zlib.compress(raw, 6)
                    if len(packed) < len(raw):
                        out.write(packed)
                        final_packed.append(len(packed))
                    else:
                        out.write(raw)
                        final_packed.append(0)
                    final_unpacked.append(len(raw))
                else:
                    # Copy original (re-read from source archive)
                    raw = self._read_entry(entry)
                    if entry.packed_size > 0:
                        packed = zlib.compress(raw, 6)
                        out.write(packed)
                        final_packed.append(len(packed))
                    else:
                        out.write(raw)
                        final_packed.append(0)
                    final_unpacked.append(len(raw))

            # --- Name table ---
            names_offset = out.tell()
            for entry in self._entries:
                name_bytes = entry.name.encode("utf-8")
                out.write(struct.pack("<H", len(name_bytes)))
                out.write(name_bytes)

            # --- Patch header: names_offset ---
            out.seek(names_offset_pos)
            out.write(struct.pack("<Q", names_offset))

            # --- Patch file records: offsets / sizes ---
            for i, (entry, rec_pos) in enumerate(
                zip(self._entries, record_positions)
            ):
                # data_offset starts at byte 16 within the record
                out.seek(rec_pos + 16)
                out.write(
                    struct.pack(
                        "<QIII",
                        final_offsets[i],
                        final_packed[i],
                        final_unpacked[i],
                        _SENTINEL,
                    )
                )

        logger.info(
            "Wrote BA2 %s (%d files, %d replaced)",
            out_path.name, len(self._entries), len(norm_rep),
        )


def _norm(name: str) -> str:
    """Normalize an internal BA2 path for case-insensitive comparison."""
    return name.replace("\\", "/").lower()
