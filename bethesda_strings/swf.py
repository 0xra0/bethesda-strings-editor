"""
Low-level SWF container primitives.

Shared by ``font_checker`` (which reads font glyphs + advance widths out of
DefineFont2/3 tags) and ``swf_widgets`` (which reads real UI widget bounds out
of DefineEditText tags).  Both need the same three things — decompress the file,
walk its tag stream, and decode SWF's bit-packed primitive types — so they live
here once rather than being reimplemented per consumer.

Coordinates
───────────
SWF stores nearly every geometric value in **twips** (1/20 of a pixel).  Helpers
here return raw twips; converting to pixels is the caller's job via ``TWIPS``,
so nobody silently double-divides.

None of these functions raise on malformed input — game files get truncated,
re-packed and modded, and a font/widget scan must degrade rather than crash.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterator, Optional, Tuple

# SWF's fixed-point unit: 20 twips == 1 pixel.
TWIPS = 20

# Tag types we care about (SWF spec numbering).
TAG_END = 0
TAG_DEFINE_FONT2 = 48
TAG_DEFINE_FONT3 = 75
TAG_DEFINE_EDIT_TEXT = 37
TAG_DEFINE_SPRITE = 39
TAG_PLACE_OBJECT2 = 26
TAG_PLACE_OBJECT3 = 70
TAG_EXPORT_ASSETS = 56
TAG_SYMBOL_CLASS = 76


def decompress_swf(raw: bytes) -> Optional[bytes]:
    """Return the uncompressed SWF body, or None if it cannot be read.

    ``FWS`` is already plain; ``CWS`` is zlib-compressed after the 8-byte header.
    ``ZWS`` (LZMA) is not supported — Bethesda does not ship it, and guessing
    would be worse than saying so.
    """
    if len(raw) < 8:
        return None
    sig = raw[:3]
    if sig == b"FWS":
        return raw
    if sig == b"CWS":
        try:
            return raw[:8] + zlib.decompress(raw[8:])
        except zlib.error:
            return None
    return None


def skip_rect(data: bytes, pos: int) -> int:
    """Return the byte position just past a bit-packed RECT."""
    if pos >= len(data):
        return pos
    nbits = (data[pos] >> 3) & 0x1F
    return pos + (5 + 4 * nbits + 7) // 8


def read_rect(data: bytes, pos: int) -> Tuple[Tuple[int, int, int, int], int]:
    """Read a RECT → ((xmin, xmax, ymin, ymax) in twips, new_pos).

    RECT packs four signed values of a shared, self-describing bit width, so it
    has to be decoded bit-by-bit rather than with struct.
    """
    if pos >= len(data):
        return (0, 0, 0, 0), pos
    nbits = (data[pos] >> 3) & 0x1F
    nbytes = (5 + 4 * nbits + 7) // 8
    chunk = data[pos:pos + nbytes]
    if len(chunk) < nbytes:
        return (0, 0, 0, 0), pos + nbytes

    bits = "".join(f"{b:08b}" for b in chunk)
    vals = []
    off = 5
    for _ in range(4):
        vals.append(_signed_bits(bits[off:off + nbits]))
        off += nbits
    return (vals[0], vals[1], vals[2], vals[3]), pos + nbytes


def _signed_bits(s: str) -> int:
    """Interpret a bit-string as a two's-complement signed integer."""
    if not s:
        return 0
    val = int(s, 2)
    if s[0] == "1":
        val -= 1 << len(s)
    return val


def skip_matrix(data: bytes, pos: int) -> int:
    """Return the byte position just past a bit-packed MATRIX."""
    window = data[pos:pos + 16]
    if not window:
        return pos
    bits = "".join(f"{b:08b}" for b in window)
    i = 0
    for _ in range(2):          # scale, then rotate/skew — each optional
        if i >= len(bits):
            return pos + len(window)
        if bits[i] == "1":
            i += 1
            nb = int(bits[i:i + 5] or "0", 2)
            i += 5 + 2 * nb
        else:
            i += 1
    nb = int(bits[i:i + 5] or "0", 2)   # translate — always present
    i += 5 + 2 * nb
    return pos + (i + 7) // 8


def read_cstring(data: bytes, pos: int) -> Tuple[str, int]:
    """Read a null-terminated string → (text, position after the terminator)."""
    end = data.find(b"\x00", pos)
    if end < 0:
        return data[pos:].decode("utf-8", "replace"), len(data)
    return data[pos:end].decode("utf-8", "replace"), end + 1


def iter_tags(data: bytes, start: int = 0) -> Iterator[Tuple[int, bytes]]:
    """Yield ``(tag_type, body)`` for each tag in a SWF tag stream.

    With ``start=0`` the SWF file header (signature, stage RECT, frame rate and
    count) is skipped first.  Pass an explicit *start* to walk a nested stream —
    a DefineSprite body is itself a tag stream, beginning 4 bytes in.

    Stops at the End tag or on a malformed length, so a truncated file yields
    what it can instead of raising.
    """
    pos = start
    if start == 0:
        pos = skip_rect(data, 8)    # 8-byte file header, then the stage RECT
        pos += 4                    # FrameRate UI16 + FrameCount UI16

    while pos + 2 <= len(data):
        record = struct.unpack_from("<H", data, pos)[0]
        tag_type = (record >> 6) & 0x3FF
        length = record & 0x3F
        pos += 2
        if length == 0x3F:          # long-form length
            if pos + 4 > len(data):
                return
            length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        if tag_type == TAG_END:
            return
        end = pos + length
        if end > len(data):
            return
        yield tag_type, bytes(data[pos:end])
        pos = end


def stage_size(data: bytes) -> Tuple[float, float]:
    """Return the SWF's stage size in pixels."""
    (xmin, xmax, ymin, ymax), _ = read_rect(data, 8)
    return (xmax - xmin) / TWIPS, (ymax - ymin) / TWIPS
