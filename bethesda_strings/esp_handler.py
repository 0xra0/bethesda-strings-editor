"""
Parser for Bethesda ESP/ESM plugin files (Starfield format).

Extracts translatable strings from non-localized plugins (text stored
directly in field buffers).  Localized plugins contain 4-byte string IDs;
for those, use BethesdaStringFile with the companion .strings files instead.
"""

from __future__ import annotations

import struct
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Record/field definitions (Starfield _recorddefs.txt) ────────────────────
# Each entry: (field_sig, record_sig, list_index, ignored, proc_id)
#   record_sig "*" = any record type
#   list_index 0 = .strings, 1 = .dlstrings, 2 = .ilstrings
#   ignored    = skip this combination even if field_sig matches a wildcard
#   proc_id    = special validation procedure (0 = none, 1 = GMST, …)
_FIELD_DEFS: list[tuple[str, str, int, bool, int]] = [
    # Wildcard — any record
    ("FULL", "*",    0, False, 0),
    ("DESC", "*",    1, False, 0),
    # Explicit ignores (override the wildcard)
    ("FULL", "IMAD", 0, True,  0),
    # GMST — only DATA fields whose EDID starts with 's'
    ("DATA", "GMST", 0, False, 1),
    # Specific field+record pairs
    ("DNAM", "MGEF", 0, False, 0),
    ("NAM1", "INFO", 2, False, 0),
    ("SHRT", "NPC_", 0, False, 0),
    ("CNAM", "QUST", 1, False, 0),
    ("CNAM", "BOOK", 1, False, 0),
    ("TNAM", "WOOP", 0, False, 0),
    ("NNAM", "QUST", 0, False, 0),
    ("NNAM", "MESG", 0, False, 0),
    ("ITXT", "MESG", 0, False, 0),
    ("RDMP", "REGN", 0, False, 0),
    ("RNAM", "ACTI", 0, False, 0),
    ("RNAM", "FLOR", 0, False, 0),
    ("RNAM", "INFO", 0, False, 0),
    ("BPTN", "BPTD", 0, False, 0),
    ("MNAM", "FACT", 0, False, 0),
    ("FNAM", "FACT", 0, False, 0),
    ("DESC", "LSCR", 0, False, 0),
    ("ONAM", "AMMO", 0, False, 0),
    ("ONAM", "LVLI", 0, False, 0),
    ("ANAM", "AVIF", 0, False, 0),
    ("WNAM", "INNR", 0, False, 0),
    ("FMRN", "RACE", 0, False, 0),
    ("BTXT", "TERM", 0, False, 0),
    ("ITXT", "TERM", 0, False, 0),
    ("RNAM", "TERM", 0, False, 0),
    ("UNAM", "TERM", 0, False, 0),
    ("WNAM", "TERM", 0, False, 0),
    ("DNAM", "ALCH", 0, False, 0),
    ("ONAM", "DOOR", 0, False, 0),
    ("TTGP", "RACE", 0, False, 0),
    ("MPPN", "RACE", 0, False, 0),
    ("NAM0", "TERM", 0, False, 0),
    ("SNAM", "RACE", 0, False, 0),
    ("NNAM", "ENTM", 0, False, 0),
    ("HNAM", "COBJ", 0, False, 0),
    ("SNAM", "CNCY", 0, False, 0),
    ("ONAM", "LVLN", 0, False, 0),
    ("NNAM", "COEN", 0, False, 0),
    ("LSST", "LSCR", 0, False, 0),
    ("BTXT", "TMLM", 0, False, 0),
    ("UNAM", "TMLM", 0, False, 0),
    ("ITXT", "TMLM", 0, False, 0),
    ("INAM", "TMLM", 0, False, 0),
    ("ISTX", "TMLM", 0, False, 0),
    ("LNAM", "NPC_", 0, False, 0),
    ("HULL", "GBFM", 0, False, 0),
    ("QMDP", "QUST", 0, False, 0),
    ("QMDT", "QUST", 0, False, 0),
    ("QMDS", "QUST", 0, False, 0),
    ("ENAM", "BOOK", 0, False, 0),
    ("FNAM", "BOOK", 0, False, 0),
    ("WABB", "WEAP", 0, False, 0),
    ("UNAM", "REFR", 0, False, 0),
    ("FDSL", "RACE", 0, False, 0),
    # Added from TES5Edit wbDefinitionsSF1 audit
    ("ATTX", "ACTI", 0, False, 0),  # Activate Text Override
    ("ATTX", "FURN", 0, False, 0),  # Activate Text Override
    ("ATTX", "FLOR", 0, False, 0),  # Activate Text Override
    ("ATTX", "TERM", 0, False, 0),  # Activate Text Override
    ("CNAM", "DOOR", 0, False, 0),  # Alternate Text - Close
    ("NNAM", "KEYM", 0, False, 0),  # Key short name
    ("NNAM", "MISC", 0, False, 0),  # Misc item short name
    ("EPF2", "PERK", 0, False, 0),  # Perk entry-point button label (EPFT=4)
    ("LNAM", "INGR", 0, False, 0),  # Ingredient plural name
    ("SHRT", "INGR", 0, False, 0),  # Ingredient short name
]

# Fast lookup tables built at import time
_IGNORED_COMBOS: frozenset[tuple[str, str]] = frozenset(
    (fsig, rsig) for fsig, rsig, _, ign, _ in _FIELD_DEFS if ign
)
_WILDCARD: dict[str, tuple[int, int]] = {}      # field_sig → (list_index, proc_id)
_SPECIFIC: dict[tuple[str, str], tuple[int, int]] = {}  # (field_sig, rec_sig) → (list_index, proc_id)
for _fsig, _rsig, _li, _ign, _proc in _FIELD_DEFS:
    if _ign:
        continue
    if _rsig == "*":
        _WILDCARD[_fsig] = (_li, _proc)
    else:
        _SPECIFIC[(_fsig, _rsig)] = (_li, _proc)

# Records whose data we never parse (raw pass-through)
_SKIP_RECORDS: frozenset[bytes] = frozenset([b"NAVM", b"NAVI", b"NOCM", b"RFGP"])

_FLAG_COMPRESSED = 0x00040000
_FLAG_LOCALIZED   = 0x00000080

# Records that override scene/camera/dialogue staging — dangerous in a localization plugin
_DANGEROUS_RECORD_SIGS: frozenset[bytes] = frozenset([
    b"DIAL",  # Dialogue Topic — contains scene staging refs
    b"SCEN",  # Scene — directly controls camera & actor positioning
    b"INFO",  # Dialogue Response — per-response staging data
])


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class EspStringEntry:
    """A single translatable string extracted from an ESP/ESM plugin."""

    form_id:    int   # 32-bit FormID
    edid:       str   # Editor ID (empty if record has none)
    record_sig: str   # Record type, e.g. "ACTI"
    field_sig:  str   # Field type, e.g. "FULL"
    list_index: int   # 0 / 1 / 2 (string type hint; informational for non-localized)
    string_id:  int   # String ID (localized plugins only; 0 otherwise)
    original:   str   # Source text as decoded from the ESP
    translation: str = ""
    # Starfield NLDT field: developer context note explaining variables/usage
    context_note: str = ""

    # Internal bookkeeping — not part of the public API
    _raw: bytes = field(default=b"", repr=False)


# ── Public class ──────────────────────────────────────────────────────────────

class EspFile:
    """
    Read and write Bethesda ESP/ESM plugin files (Starfield format).

    Usage::

        esp = EspFile()
        esp.load(Path("Starfield.esm"))
        for entry in esp.strings:
            entry.translation = translate(entry.original)
        esp.save(Path("Starfield_translated.esm"))
    """

    def __init__(self) -> None:
        self.strings:      list[EspStringEntry] = []
        self.is_localized: bool = False
        self.plugin_name:  str  = ""
        self._data:        bytearray = bytearray()

    # ── Loading ───────────────────────────────────────────────────────────────

    def load(self, path: Path, encoding: str = "utf-8") -> None:
        """Parse an ESP/ESM file and populate :attr:`strings`."""
        self.plugin_name = path.name
        self._data = bytearray(path.read_bytes())
        self.strings.clear()
        self._parse(encoding)

    def _parse(self, encoding: str) -> None:
        data = self._data
        size = len(data)

        if size < 24 or data[0:4] != b"TES4":
            raise ValueError("Not a valid ESP/ESM file (missing TES4 header)")

        tes4_data_size = struct.unpack_from("<I", data, 4)[0]
        tes4_flags = struct.unpack_from("<I", data, 8)[0]
        self.is_localized = bool(tes4_flags & _FLAG_LOCALIZED)

        pos = 24 + tes4_data_size  # skip TES4 body

        # Stack of (grup_end_position) so we know where each GRUP ends
        grup_stack: list[int] = []

        while pos < size:
            # Pop expired groups
            while grup_stack and pos >= grup_stack[-1]:
                grup_stack.pop()

            if pos + 8 > size:
                break

            sig   = bytes(data[pos:pos + 4])
            dsize = struct.unpack_from("<I", data, pos + 4)[0]

            if sig == b"GRUP":
                if pos + 24 > size:
                    break
                grup_stack.append(pos + dsize)   # dsize = total GRUP size inc. header
                pos += 24
            elif sig in _SKIP_RECORDS:
                pos += 24 + dsize
            else:
                rec_end = pos + 24 + dsize
                if rec_end > size:
                    break
                flags   = struct.unpack_from("<I", data, pos + 8)[0]
                form_id = struct.unpack_from("<I", data, pos + 12)[0]
                body    = bytes(data[pos + 24:rec_end])
                try:
                    self._parse_record(sig, form_id, flags, body, encoding)
                except Exception:
                    pass
                pos = rec_end

    def _parse_record(
        self,
        rec_sig: bytes,
        form_id: int,
        flags: int,
        body: bytes,
        encoding: str,
    ) -> None:
        compressed = bool(flags & _FLAG_COMPRESSED)
        if compressed:
            if len(body) < 4:
                return
            decompressed_size = struct.unpack_from("<I", body, 0)[0]
            if decompressed_size == 0:
                return
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                return

        rec_str = rec_sig.decode("ascii", errors="replace")
        edid = ""
        next_size = 0
        pos = 0
        start_idx = len(self.strings)  # track entries added by this record
        context_note = ""

        while pos < len(body):
            if pos + 6 > len(body):
                break

            fsig  = body[pos:pos + 4]
            fsize = struct.unpack_from("<H", body, pos + 4)[0]
            pos  += 6

            actual = next_size if next_size else fsize
            next_size = 0

            if pos + actual > len(body):
                break

            fdata = body[pos:pos + actual]
            pos  += actual

            if fsig == b"XXXX":
                if len(fdata) >= 4:
                    next_size = struct.unpack_from("<I", fdata, 0)[0]
                continue

            if fsig == b"EDID" and fdata:
                edid = fdata.rstrip(b"\x00").decode("ascii", errors="replace")
                continue

            if fsig == b"NLDT" and fdata:
                raw = fdata.rstrip(b"\x00")
                if raw:
                    try:
                        context_note = raw.decode(encoding, errors="replace")
                    except Exception:
                        context_note = raw.decode("latin-1", errors="replace")
                continue

            fsig_str = fsig.decode("ascii", errors="replace")
            result = _field_list_index(fsig_str, rec_str)
            if result is None:
                continue
            list_index, proc_id = result

            # proc1: GMST DATA only if EDID starts with 's'
            if proc_id == 1 and not edid.startswith("s"):
                continue

            if self.is_localized:
                if len(fdata) == 4:
                    string_id = struct.unpack_from("<I", fdata, 0)[0]
                    self.strings.append(EspStringEntry(
                        form_id=form_id, edid=edid,
                        record_sig=rec_str, field_sig=fsig_str,
                        list_index=list_index, string_id=string_id,
                        original=f"[StringID:{string_id:08X}]",
                        _raw=bytes(fdata),
                    ))
                continue

            # Non-localized: null-terminated text
            raw = fdata.rstrip(b"\x00")
            if not raw:
                continue
            try:
                text = raw.decode(encoding, errors="replace")
            except Exception:
                text = raw.decode("latin-1", errors="replace")

            self.strings.append(EspStringEntry(
                form_id=form_id, edid=edid,
                record_sig=rec_str, field_sig=fsig_str,
                list_index=list_index, string_id=0,
                original=text, _raw=bytes(fdata),
            ))

        # Attach NLDT context note to all entries added by this record
        if context_note:
            for entry in self.strings[start_idx:]:
                entry.context_note = context_note

    # ── Saving ────────────────────────────────────────────────────────────────

    def save(self, path: Path, encoding: str = "utf-8") -> None:
        """
        Write a translated copy of the ESP/ESM file.

        For localized plugins the string IDs in field buffers are unchanged;
        save the companion .strings/.dlstrings/.ilstrings files instead.
        For non-localized plugins, translatable fields are re-encoded with
        the provided *encoding* and GRUP/record sizes are recomputed.
        """
        if self.is_localized:
            raise NotImplementedError(
                "Localized plugins store text in separate .strings files — "
                "save those via BethesdaStringFile instead."
            )

        # Build translation map: (form_id, field_sig, occurrence) → translated text
        trans_map: dict[tuple[int, str, int], str] = {}
        occ: dict[tuple[int, str], int] = {}
        for entry in self.strings:
            if entry.translation and entry.translation != entry.original:
                key2 = (entry.form_id, entry.field_sig)
                idx  = occ.get(key2, 0)
                occ[key2] = idx + 1
                trans_map[(entry.form_id, entry.field_sig, idx)] = entry.translation

        # Fast set of form_ids that have any translation (for O(1) lookup in _write_chunks)
        translated_form_ids: frozenset[int] = frozenset(
            entry.form_id for entry in self.strings
            if entry.translation and entry.translation != entry.original
        )

        out = bytearray()
        occ_counter: dict[tuple[int, str], int] = {}
        self._write_chunks(
            self._data, 0, len(self._data), out,
            encoding, trans_map, occ_counter, translated_form_ids,
        )
        path.write_bytes(out)

    def _write_chunks(
        self,
        data:               bytes | bytearray,
        pos:                int,
        end:                int,
        out:                bytearray,
        encoding:           str,
        trans_map:          dict,
        occ_counter:        dict,
        translated_form_ids: frozenset[int] = frozenset(),
    ) -> int:
        """
        Recursively copy chunks from *data[pos:end]* into *out*,
        patching translatable fields along the way.
        Returns the position after the last byte consumed.
        """
        # Skip TES4 record unchanged on first call (pos == 0 means we're at root)
        if pos == 0:
            if len(data) < 8:
                out += data
                return len(data)
            tes4_body = struct.unpack_from("<I", data, 4)[0]
            tes4_end  = 24 + tes4_body
            out += data[:tes4_end]
            pos = tes4_end

        while pos < end:
            if pos + 8 > len(data):
                out += data[pos:end]
                return end

            sig   = bytes(data[pos:pos + 4])
            dsize = struct.unpack_from("<I", data, pos + 4)[0]

            if sig == b"GRUP":
                if pos + 24 > len(data):
                    out += data[pos:end]
                    return end

                grup_input_end  = pos + dsize
                size_field_pos  = len(out) + 4   # where we'll patch the size
                grup_out_start  = len(out)
                out += sig                         # "GRUP"
                out += b"\x00\x00\x00\x00"        # size placeholder
                out += data[pos + 8:pos + 24]     # rest of GRUP header (16 bytes)
                pos = self._write_chunks(
                    data, pos + 24, grup_input_end, out,
                    encoding, trans_map, occ_counter, translated_form_ids,
                )
                new_size = len(out) - grup_out_start
                struct.pack_into("<I", out, size_field_pos, new_size)

            elif sig in _SKIP_RECORDS:
                out += data[pos:pos + 24 + dsize]
                pos += 24 + dsize

            else:
                rec_end  = pos + 24 + dsize
                rec_flags = struct.unpack_from("<I", data, pos + 8)[0]
                form_id   = struct.unpack_from("<I", data, pos + 12)[0]
                compressed = bool(rec_flags & _FLAG_COMPRESSED)
                rec_str   = sig.decode("ascii", errors="replace")

                has_trans = (not compressed) and (form_id in translated_form_ids)

                if not has_trans:
                    out += data[pos:rec_end]
                else:
                    field_buf = bytes(data[pos + 24:rec_end])
                    new_fields = _patch_fields(
                        field_buf, form_id, rec_str, encoding, trans_map, occ_counter
                    )
                    new_header = bytearray(data[pos:pos + 24])
                    struct.pack_into("<I", new_header, 4, len(new_fields))
                    out += new_header
                    out += new_fields

                pos = rec_end

        return pos


# ── Module-level helpers ──────────────────────────────────────────────────────

def _field_list_index(
    field_sig: str, rec_sig: str
) -> Optional[tuple[int, int]]:
    """Return (list_index, proc_id) if this field+record is translatable, else None."""
    if (field_sig, rec_sig) in _IGNORED_COMBOS:
        return None
    hit = _SPECIFIC.get((field_sig, rec_sig))
    if hit is not None:
        return hit
    hit = _WILDCARD.get(field_sig)
    return hit  # None if not found


def _patch_fields(
    buf:         bytes,
    form_id:     int,
    rec_sig:     str,
    encoding:    str,
    trans_map:   dict[tuple[int, str, int], str],
    occ_counter: dict[tuple[int, str], int],
) -> bytearray:
    """Return a rebuilt field buffer with translated strings substituted."""
    out       = bytearray()
    pos       = 0
    next_size = 0
    edid      = ""

    while pos < len(buf):
        if pos + 6 > len(buf):
            out += buf[pos:]
            break

        fsig  = buf[pos:pos + 4]
        fsize = struct.unpack_from("<H", buf, pos + 4)[0]
        frame_start = pos
        pos  += 6

        actual    = next_size if next_size else fsize
        next_size = 0

        if pos + actual > len(buf):
            out += buf[frame_start:]
            break

        fdata = buf[pos:pos + actual]
        pos  += actual

        if fsig == b"XXXX":
            if len(fdata) >= 4:
                next_size = struct.unpack_from("<I", fdata, 0)[0]
            out += buf[frame_start:pos]
            continue

        if fsig == b"EDID" and fdata:
            edid = fdata.rstrip(b"\x00").decode("ascii", errors="replace")
            out += buf[frame_start:pos]
            continue

        fsig_str = fsig.decode("ascii", errors="replace")
        result = _field_list_index(fsig_str, rec_sig)

        replaced = False
        if result is not None:
            _, proc_id = result
            if proc_id == 1 and not edid.startswith("s"):
                pass  # GMST proc: skip if EDID doesn't start with 's'
            elif fdata and fdata != b"\x00":
                key2 = (form_id, fsig_str)
                idx  = occ_counter.get(key2, 0)
                occ_counter[key2] = idx + 1
                trans = trans_map.get((form_id, fsig_str, idx))
                if trans:
                    new_data = trans.encode(encoding, errors="replace") + b"\x00"
                    if len(new_data) <= 0xFFFF:
                        out += fsig
                        out += struct.pack("<H", len(new_data))
                        out += new_data
                    else:
                        # Oversized: prepend XXXX field
                        out += b"XXXX"
                        out += struct.pack("<H", 4)
                        out += struct.pack("<I", len(new_data))
                        out += fsig
                        out += struct.pack("<H", 0)
                        out += new_data
                    replaced = True

        if not replaced:
            out += buf[frame_start:pos]

    return out


# ── Plugin validation ─────────────────────────────────────────────────────────

SEVERITY_ERROR   = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO    = "info"

_SEV_RANK = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


@dataclass
class ValidationIssue:
    severity: str   # SEVERITY_* constant
    code: str
    message: str
    detail: str = ""


@dataclass
class PluginValidationResult:
    plugin_name: str
    is_localized: bool
    masters: list[str]
    record_counts: dict[str, int]   # record sig → count of top-level records
    onam_count: int                 # overridden forms listed in TES4 ONAM
    issues: list[ValidationIssue]

    @property
    def has_errors(self) -> bool:
        return any(i.severity == SEVERITY_ERROR for i in self.issues)


def validate_plugin(path: Path) -> PluginValidationResult:
    """
    Scan an ESP/ESM file and return a PluginValidationResult.

    Checks performed (derived from xEdit wbDefinitionsSF1 + wbImplementation):
      • Localized flag (TES4 bit 7 / 0x080) must be set for a localization plugin
      • DIAL / SCEN / INFO records in the plugin override scene staging → camera bug
      • ONAM in TES4 lists explicitly registered overrides — any entry is suspicious
      • Master file list is reported for load-order review
    """
    data = path.read_bytes()
    size = len(data)

    if size < 24 or data[0:4] != b"TES4":
        raise ValueError("Not a valid ESP/ESM file (missing TES4 header)")

    tes4_data_size = struct.unpack_from("<I", data, 4)[0]
    tes4_flags     = struct.unpack_from("<I", data, 8)[0]
    is_localized   = bool(tes4_flags & _FLAG_LOCALIZED)

    # ── Parse TES4 subfields for MAST (masters) and ONAM (overridden forms) ──
    masters: list[str] = []
    onam_count = 0
    sub_pos = 24
    sub_end = 24 + tes4_data_size
    while sub_pos + 6 <= sub_end:
        fsig  = data[sub_pos:sub_pos + 4]
        fsize = struct.unpack_from("<H", data, sub_pos + 4)[0]
        fdata = data[sub_pos + 6: sub_pos + 6 + fsize]
        sub_pos += 6 + fsize
        if fsig == b"MAST":
            masters.append(fdata.rstrip(b"\x00").decode("utf-8", errors="replace"))
        elif fsig == b"ONAM":
            # Each entry is a 4-byte FormID
            onam_count = len(fdata) // 4

    # ── Scan top-level record signatures ─────────────────────────────────────
    record_counts: Counter[str] = Counter()
    pos = 24 + tes4_data_size

    grup_stack: list[int] = []
    while pos < size:
        while grup_stack and pos >= grup_stack[-1]:
            grup_stack.pop()
        if pos + 8 > size:
            break
        sig   = data[pos:pos + 4]
        dsize = struct.unpack_from("<I", data, pos + 4)[0]
        if sig == b"GRUP":
            if pos + 24 > size:
                break
            grup_stack.append(pos + dsize)
            pos += 24
        else:
            sig_str = sig.decode("ascii", errors="replace")
            record_counts[sig_str] += 1
            pos += 24 + dsize

    # ── Build issues list ─────────────────────────────────────────────────────
    issues: list[ValidationIssue] = []

    # 1. Localized flag
    if not is_localized:
        issues.append(ValidationIssue(
            severity=SEVERITY_ERROR,
            code="NO_LOCALIZED_FLAG",
            message="TES4 header is missing the Localized flag (bit 7 / 0x080)",
            detail=(
                "The game uses this flag to decide whether to load text from "
                "companion .strings files. Without it, string IDs in the plugin "
                "are read as raw numbers and all dialogue will be blank or broken."
            ),
        ))

    # 2. Dangerous record types (DIAL / SCEN / INFO)
    for sig_bytes in sorted(_DANGEROUS_RECORD_SIGS):
        sig_str = sig_bytes.decode()
        count   = record_counts.get(sig_str, 0)
        if count == 0:
            continue
        labels = {
            "DIAL": ("Dialog Topic",   "scene staging references and camera presets"),
            "SCEN": ("Scene",          "actor positions, camera angles, and event timings"),
            "INFO": ("Dialog Response","per-line staging, camera, and condition data"),
        }
        label, content = labels[sig_str]
        issues.append(ValidationIssue(
            severity=SEVERITY_ERROR,
            code=f"OVERRIDE_{sig_str}",
            message=f"Plugin contains {count} {sig_str} ({label}) record(s)",
            detail=(
                f"{sig_str} records hold {content}. "
                f"Overriding them without the full original data causes the "
                f"conversation camera to enter the NPC. "
                f"A localization plugin should only ship companion .strings files "
                f"— remove all {sig_str} records from this plugin."
            ),
        ))

    # 3. ONAM overrides
    if onam_count > 0:
        issues.append(ValidationIssue(
            severity=SEVERITY_WARNING,
            code="ONAM_OVERRIDES",
            message=f"TES4 ONAM field lists {onam_count} explicitly overridden form(s)",
            detail=(
                "ONAM registers forms whose master-file versions this plugin overrides. "
                "xEdit (wbDefinitionsSF1) recognises DIAL, INFO and SCEN as valid ONAM "
                "candidates — their presence here usually means those records exist in "
                "the plugin body as well. Verify with the DIAL/SCEN/INFO checks above."
            ),
        ))

    # 4. Master-file report (info)
    if masters:
        issues.append(ValidationIssue(
            severity=SEVERITY_INFO,
            code="MASTERS",
            message=f"Plugin declares {len(masters)} master file(s)",
            detail="\n".join(masters),
        ))
    else:
        issues.append(ValidationIssue(
            severity=SEVERITY_WARNING,
            code="NO_MASTERS",
            message="Plugin declares no master files",
            detail=(
                "A Starfield localization plugin must list Starfield.esm (and any "
                "other relevant DLC masters) so that overridden FormIDs resolve correctly."
            ),
        ))

    # 5. Starfield.esm master check
    esm_masters = [m for m in masters if m.lower() == "starfield.esm"]
    if masters and not esm_masters:
        issues.append(ValidationIssue(
            severity=SEVERITY_WARNING,
            code="MISSING_STARFIELD_ESM",
            message="Starfield.esm is not listed as a master",
            detail=(
                "Almost all Starfield plugins should depend on Starfield.esm. "
                "Without it, FormID references will be remapped incorrectly and "
                "dialogue conditions may fail silently."
            ),
        ))

    issues.sort(key=lambda i: _SEV_RANK[i.severity])

    return PluginValidationResult(
        plugin_name=path.name,
        is_localized=is_localized,
        masters=masters,
        record_counts=dict(record_counts),
        onam_count=onam_count,
        issues=issues,
    )


def patch_localized_flag(path: Path) -> Path:
    """
    Set the Localized flag (bit 7 / 0x080) in the TES4 header of *path*.

    A backup is written to *path*.bak before any modification.
    Returns the backup path.
    Raises ValueError if the file is not a valid ESP/ESM or the flag is already set.
    """
    data = bytearray(path.read_bytes())

    if len(data) < 12 or data[0:4] != b"TES4":
        raise ValueError("Not a valid ESP/ESM file (missing TES4 header)")

    flags = struct.unpack_from("<I", data, 8)[0]
    if flags & _FLAG_LOCALIZED:
        raise ValueError("Localized flag is already set — no change needed")

    backup = path.with_suffix(path.suffix + ".bak")
    backup.write_bytes(data)          # save original before touching anything

    new_flags = flags | _FLAG_LOCALIZED
    struct.pack_into("<I", data, 8, new_flags)
    path.write_bytes(data)

    return backup
