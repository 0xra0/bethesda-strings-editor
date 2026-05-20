"""
Core classes for parsing Bethesda string files.
"""

import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class StringDataObject:
    """
    Represents a single string entry from a Bethesda string file.

    Mirrors the TypeScript interface from the original library.
    """

    id: int  # String ID used by game files
    address: int  # Absolute file offset of directory entry
    relative_offset: int  # Offset from start of string data section
    absolute_offset: int  # Absolute file offset of string data
    null_point: int  # Position of null terminator
    length: int  # Length of string (excluding length prefix for dl/ilstrings)
    string_array: bytearray  # Raw bytes of the string (with encoding)
    has_length_prefix: bool = False  # True for .dlstrings/.ilstrings

    def get_string(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        """Decode the string array to a Python string.

        Args:
            encoding: Character encoding to use for decoding
            errors: Error handling scheme passed to bytes.decode()
        """
        data = self.string_array
        # Skip 4-byte length prefix for dlstrings/ilstrings if present
        if self.has_length_prefix and len(data) >= 4:
            data = data[4:]
        # Remove null terminator
        if data and data[-1] == 0:
            data = data[:-1]
        return data.decode(encoding, errors=errors)

    def set_string(self, text: str, encoding: str = "utf-8") -> None:
        """Encode a Python string and update string_array."""
        encoded = text.encode(encoding) + b"\x00"  # Add null terminator
        if self.has_length_prefix:
            # Prepend 4-byte little-endian length (including null)
            length = len(encoded)
            self.string_array = bytearray(struct.pack("<I", length) + encoded)
            self.length = length
        else:
            self.string_array = bytearray(encoded)
            self.length = len(encoded) - 1  # Exclude null terminator
        # null_point is an absolute file offset; it is recalculated by _rebuild().


class BethesdaStringFile:
    """
    Main class for reading and writing Bethesda string files.

    Supports .strings, .dlstrings, and .ilstrings formats.
    """

    HEADER_SIZE = 8
    DIRECTORY_ENTRY_SIZE = 8

    def __init__(
        self,
        file_path: Optional[str] = None,
        file_extension: Optional[str] = None,
        buffer: Optional[bytes] = None,
    ):
        """
        Initialize from file path or raw buffer.

        Args:
            file_path: Path to .strings/.dlstrings/.ilstrings file
            file_extension: File extension (without dot), e.g., 'dlstrings'
            buffer: Raw bytes buffer (alternative to file_path)
        """
        self.file_extension = (file_extension or "").lower().lstrip(".")
        self.strings: List[StringDataObject] = []
        self._header_count: int = 0
        self._header_data_size: int = 0
        self._raw_buffer: Optional[bytearray] = None
        self._id_index: Optional[dict[int, int]] = None  # id -> index in self.strings

        # Encoding state — set by _detect_encoding() or set_encoding().
        self.encoding: str = "utf-8"
        self._encoding_confidence: float = 1.0
        self._encoding_source: str = "default"  # "default" | "detected" | "manual"
        self._encoding_method: str = "not yet detected"

        if buffer is not None:
            self._raw_buffer = bytearray(buffer)
            self._parse()
            self._detect_encoding()
        elif file_path:
            self.load(file_path)

    def load(self, file_path: str) -> None:
        """Load a string file from disk."""
        path = Path(file_path)
        if not self.file_extension:
            self.file_extension = path.suffix.lstrip(".").lower()

        with open(path, "rb") as f:
            self._raw_buffer = bytearray(f.read())
        self._parse()
        self._detect_encoding()

    def save(self, file_path: str) -> None:
        """Write the modified string file to disk, preserving the detected/set encoding."""
        self._rebuild()
        if self._raw_buffer is None:
            return
        with open(file_path, "wb") as f:
            f.write(self._raw_buffer)
        logger.debug("Saved %s with encoding %s", file_path, self.encoding)

    def set_encoding(self, encoding: str) -> None:
        """
        Manually override the detected encoding.

        Call this before ``apply_changes_to_file()`` / ``save()`` to control
        how translated text is encoded into the binary string arrays.
        """
        self.encoding = encoding
        self._encoding_source = "manual"
        self._encoding_confidence = 1.0
        self._encoding_method = f"manually set to {encoding}"
        logger.info("Encoding manually overridden to %s", encoding)

    def encoding_info(self) -> Tuple[str, float, str, str]:
        """Return (encoding, confidence, source, method) for display purposes."""
        return (
            self.encoding,
            self._encoding_confidence,
            self._encoding_source,
            self._encoding_method,
        )

    def _detect_encoding(self) -> None:
        """
        Auto-detect the character encoding of the string data in this file.

        Samples up to 64 KB of raw string bytes (excluding length prefixes and
        null terminators) and delegates to ``EncodingConverter.detect_encoding()``.
        Sets ``self.encoding``, ``self._encoding_confidence``,
        ``self._encoding_source``, and ``self._encoding_method``.

        Does nothing if the encoding was already set manually.
        """
        if self._encoding_source == "manual":
            return
        if not self.strings:
            self._encoding_source = "default"
            self._encoding_method = "default (no strings)"
            return

        from bethesda_strings.encoding import EncodingConverter

        # Collect raw text bytes from each string (strip length prefix + null).
        MAX_SAMPLE = 65536
        sample = bytearray()
        for s in self.strings:
            data = s.string_array
            if s.has_length_prefix and len(data) >= 4:
                data = data[4:]
            if data and data[-1] == 0:
                data = data[:-1]
            sample.extend(data)
            if len(sample) >= MAX_SAMPLE:
                break

        enc, conf, method = EncodingConverter.detect_encoding(bytes(sample))
        self.encoding = enc
        self._encoding_confidence = conf
        self._encoding_source = "detected"
        self._encoding_method = method
        logger.info(
            "Detected encoding: %s (confidence %.0f%%, method: %s)",
            enc, conf * 100, method,
        )

    def _parse(self) -> None:
        """Parse the binary buffer into StringDataObject entries."""
        if not self._raw_buffer or len(self._raw_buffer) < self.HEADER_SIZE:
            raise ValueError("Invalid or empty string file")

        # Parse header
        entry_count, data_size = struct.unpack(
            "<II", self._raw_buffer[: self.HEADER_SIZE]
        )
        self._header_count = entry_count
        self._header_data_size = data_size

        # Calculate offsets
        directory_start = self.HEADER_SIZE
        data_start = directory_start + (entry_count * self.DIRECTORY_ENTRY_SIZE)

        # Has length prefix for dlstrings/ilstrings
        has_length_prefix = self.file_extension in ("dlstrings", "ilstrings")

        # Parse directory entries
        self.strings = []
        for i in range(entry_count):
            entry_offset = directory_start + (i * self.DIRECTORY_ENTRY_SIZE)
            string_id, rel_offset = struct.unpack(
                "<II",
                self._raw_buffer[
                    entry_offset : entry_offset + self.DIRECTORY_ENTRY_SIZE
                ],
            )

            abs_offset = data_start + rel_offset

            # Parse string data
            if has_length_prefix:
                # Read length prefix
                if abs_offset + 4 > len(self._raw_buffer):
                    continue
                str_length = struct.unpack(
                    "<I", self._raw_buffer[abs_offset : abs_offset + 4]
                )[0]
                null_point = abs_offset + 4 + str_length - 1
                string_array = bytearray(
                    self._raw_buffer[abs_offset : abs_offset + 4 + str_length]
                )
            else:
                # Find null terminator for .strings files
                null_point = abs_offset
                # Bounds check: don't scan past end of buffer
                if abs_offset >= len(self._raw_buffer):
                    continue
                while (
                    null_point < len(self._raw_buffer)
                    and self._raw_buffer[null_point] != 0
                ):
                    null_point += 1
                # If we hit end of buffer without finding null, skip this entry
                if null_point >= len(self._raw_buffer):
                    continue
                string_array = bytearray(self._raw_buffer[abs_offset : null_point + 1])

            string_obj = StringDataObject(
                id=string_id,
                address=entry_offset,
                relative_offset=rel_offset,
                absolute_offset=abs_offset,
                null_point=null_point,
                length=len(string_array) - (4 if has_length_prefix else 1),
                string_array=string_array,
                has_length_prefix=has_length_prefix,
            )
            self.strings.append(string_obj)

    def _rebuild(self) -> None:
        """Rebuild the binary buffer from modified StringDataObjects.

        Updates all StringDataObject offset fields to match the new buffer layout.
        Identical strings share a single data entry (mirrors TES5Edit's ReuseDup).
        """
        if not self._raw_buffer:
            return

        # Build deduplicated data section: identical raw string_arrays share an offset.
        text_to_offset: dict[bytes, int] = {}
        data_bytes = bytearray()
        new_rel_offsets: List[int] = []

        for s in self.strings:
            key = bytes(s.string_array)
            if key and key in text_to_offset:
                new_rel_offsets.append(text_to_offset[key])
            else:
                offset = len(data_bytes)
                if key:
                    text_to_offset[key] = offset
                new_rel_offsets.append(offset)
                data_bytes.extend(s.string_array)

        # Build new buffer
        new_buffer = bytearray()

        # Write header placeholder — data size updated at the end
        new_buffer.extend(struct.pack("<II", len(self.strings), 0))

        # Write directory entries
        for i, s in enumerate(self.strings):
            new_buffer.extend(struct.pack("<II", s.id, new_rel_offsets[i]))

        # Append data section and update all StringDataObject offset fields
        data_start = len(new_buffer)
        new_buffer.extend(data_bytes)
        for i, s in enumerate(self.strings):
            s.relative_offset = new_rel_offsets[i]
            s.absolute_offset = data_start + new_rel_offsets[i]
            if s.string_array:
                s.null_point = s.absolute_offset + len(s.string_array) - 1

        # Update header with actual data size
        struct.pack_into("<I", new_buffer, 4, len(data_bytes))

        self._raw_buffer = new_buffer

    def filter_and_modify(
        self,
        condition_fx: Callable[[StringDataObject], bool],
        modification_fx: Callable[[bytearray, Optional[StringDataObject]], bytearray],
    ) -> int:
        """
        Apply filter and modification functions to strings.

        Mirrors the original library's pipeline approach.

        Args:
            condition_fx: Filter function returning True for strings to modify
            modification_fx: Function that takes string_array and optional StringDataObject,
                           returns modified bytearray

        Returns:
            Number of strings that were modified
        """
        modified_count = 0
        for s in self.strings:
            if condition_fx(s):
                original = s.string_array
                modified = modification_fx(bytearray(original), s)
                if modified != original:
                    s.string_array = modified
                    # Update length field if needed
                    if s.has_length_prefix and len(modified) >= 4:
                        # Length prefix excludes the 4-byte prefix itself
                        new_len = len(modified) - 4
                        struct.pack_into("<I", s.string_array, 0, new_len)
                        s.length = new_len
                    else:
                        s.length = len(modified) - 1
                    modified_count += 1
        return modified_count

    def get_by_id(self, string_id: int) -> Optional[StringDataObject]:
        """Get a string by its ID (O(1) lookup via cached index)."""
        if self._id_index is None:
            self._build_id_index()
        assert self._id_index is not None
        idx = self._id_index.get(string_id)
        if idx is not None and idx < len(self.strings):
            return self.strings[idx]
        return None

    def get_string_by_id(self, string_id: int, encoding: str = "utf-8") -> str:
        """Return decoded string text, or a TES5Edit-style error placeholder if not found."""
        obj = self.get_by_id(string_id)
        if obj is not None:
            return obj.get_string(encoding)
        return f"<Error: Unknown lstring ID {string_id:08X}>"

    def _build_id_index(self) -> None:
        """Build the id -> index mapping for O(1) lookups."""
        self._id_index = {}
        for i, s in enumerate(self.strings):
            self._id_index[s.id] = i

    def _invalidate_index(self) -> None:
        """Invalidate the ID index (call after modifying string list)."""
        self._id_index = None

    def __len__(self) -> int:
        return len(self.strings)

    def __iter__(self):
        return iter(self.strings)
