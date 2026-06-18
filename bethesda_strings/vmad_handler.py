"""
Parser / safe editor for VMAD (Papyrus script attachment) subrecords.

Starfield records carry a ``VMAD`` field — the "Virtual Machine Adapter" —
that stores compiled-script attachments.  Each attached script can have
*properties*, and a property may be a **string** (type 2) or an **array of
strings** (type 12).  A handful of those strings are display text the player
sees (a button caption set by a terminal script, a custom message, …) and are
worth translating; the overwhelming majority are script identifiers, event
names, keyword EditorIDs or resource paths that **break the mod if edited**.

This mirrors what xTranslator does: it parses VMAD, surfaces the embedded
strings, classifies the risky ones and *locks* them, and only lets the
translator touch the ones that look like real display text.

Design — *safety first*:

* :func:`parse_vmad` walks the header + every top-level script + its
  properties, recording the exact byte span (length prefix + characters) of
  each string-property value.  Unknown property types or truncated data stop
  the walk gracefully (``fully_parsed = False``) — whatever was decoded before
  the failure is still valid.
* :func:`replace_strings` rewrites **only** the byte spans of the strings you
  edit (updating their 2-byte length prefix) and copies every other byte —
  script fragments, QUST aliases, object refs, everything — verbatim.  Nothing
  outside an edited value is ever re-encoded, so we cannot corrupt the parts we
  don't fully model.

VMAD wstring layout: ``uint16 length`` followed by ``length`` bytes (NOT
null-terminated).  Property/script *names* are wstrings too but are never
collected — translating them would break the script binding.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import Optional


# ── Risk classification ────────────────────────────────────────────────────────

RISK_TRANSLATABLE = "translatable"  # reads like display text — safe to edit
RISK_REVIEW       = "review"        # ambiguous — translator should verify
RISK_LOCKED       = "locked"        # identifier / path / empty — do NOT edit

# Resource extensions that mark a value as a file/asset path (never text).
_RESOURCE_EXTS: frozenset[str] = frozenset([
    "swf", "dds", "nif", "wav", "xwm", "fuz", "wem", "lip", "hkx", "seq",
    "psc", "pex", "esp", "esm", "esl", "ba2", "txt", "json", "ini", "bgsm",
    "bgem", "mat", "tga", "png", "dlstrings", "ilstrings", "strings",
])

# Property-name fragments that imply the value is an identifier / resource.
_NAME_LOCK_HINTS: tuple[str, ...] = (
    "path", "file", "event", "keyword", "sound", "model", "script", "func",
    "form", "anim", "node", "graph", "marker", "editorid", "edid", "ref",
    "global", "quest", "stage", "alias", "actorvalue", "av", "perk", "spell",
)
# Property-name fragments that imply the value is shown to the player.
_NAME_TEXT_HINTS: tuple[str, ...] = (
    "text", "name", "title", "message", "msg", "label", "desc", "caption",
    "tooltip", "header", "button", "display", "greeting", "line", "prompt",
    "note", "subtitle", "string",
)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CAMEL_RE = re.compile(r"[a-z][A-Z]")


def classify_string_property(prop_name: str, value: str) -> tuple[str, str]:
    """
    Decide whether a script-property string is safe display text.

    Returns ``(risk, reason)`` where *risk* is one of :data:`RISK_TRANSLATABLE`,
    :data:`RISK_REVIEW`, :data:`RISK_LOCKED`.
    """
    name = (prop_name or "").lower()
    v = value or ""

    if not v.strip():
        return RISK_LOCKED, "Empty value — not display text."

    # Hard resource / path markers always win.
    if "/" in v or "\\" in v:
        return RISK_LOCKED, "Contains a path separator — looks like a resource path."
    if "." in v:
        ext = v.rsplit(".", 1)[-1].lower()
        if ext in _RESOURCE_EXTS:
            return RISK_LOCKED, f"Ends in .{ext} — looks like a file / asset name."

    name_text = any(h in name for h in _NAME_TEXT_HINTS)
    name_lock = any(h in name for h in _NAME_LOCK_HINTS)

    # An explicit text-property name (and no conflicting lock hint) → translate.
    if name_text and not name_lock:
        return RISK_TRANSLATABLE, "Property name marks it as display text."
    # An explicit identifier/resource name → lock.
    if name_lock and not name_text:
        return RISK_LOCKED, "Property name marks it as an identifier / resource."

    has_space = " " in v.strip()
    if has_space:
        # A multi-word phrase is almost always real text.
        return RISK_TRANSLATABLE, "Reads like display text (multiple words)."

    # Single token: identifier-shaped values are locked.
    if _IDENT_RE.match(v) and ("_" in v or v.isupper() or _CAMEL_RE.search(v)):
        return RISK_LOCKED, "Looks like a script identifier (single token, no spaces)."

    return RISK_REVIEW, "Single word — verify it is shown to the player before editing."


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class VmadString:
    """A single string-typed property value extracted from a VMAD buffer."""

    script_name: str
    prop_name:   str
    array_index: int       # -1 for a scalar String property; >=0 for array element
    value:       str
    risk:        str       # RISK_* constant
    reason:      str

    # Byte locations within the VMAD field buffer (for safe splicing).
    len_pos:     int       # offset of the uint16 length prefix
    value_start: int       # offset of the first character byte
    value_end:   int       # offset just past the last character byte


@dataclass
class VmadInfo:
    """Result of parsing a VMAD field buffer."""

    version:       int
    object_format: int
    script_names:  list[str]
    strings:       list[VmadString]
    fully_parsed:  bool                 # False if an unknown type / truncation stopped the walk
    raw:           bytes = field(default=b"", repr=False)

    @property
    def translatable(self) -> list[VmadString]:
        return [s for s in self.strings if s.risk == RISK_TRANSLATABLE]


# Property type codes (Papyrus VMAD property union).
_T_OBJECT       = 1
_T_STRING       = 2
_T_INT32        = 3
_T_FLOAT        = 4
_T_BOOL         = 5
_T_ARR_OBJECT   = 11
_T_ARR_STRING   = 12
_T_ARR_INT32    = 13
_T_ARR_FLOAT    = 14
_T_ARR_BOOL     = 15

_OBJECT_SIZE = 8  # both objectFormat 1 and 2 encode an Object in 8 bytes


class _VmadTruncated(Exception):
    """Raised internally when the walk runs past the end of the buffer."""


# ── Parsing ────────────────────────────────────────────────────────────────────

def parse_vmad(data: bytes, encoding: str = "utf-8") -> VmadInfo:
    """
    Parse a VMAD field buffer and extract every string-property value.

    *data* is the raw field payload (the bytes after the 6-byte field header).
    Never raises: malformed input yields ``fully_parsed = False`` with whatever
    was decoded before the problem.
    """
    strings: list[VmadString] = []
    script_names: list[str] = []
    info = VmadInfo(
        version=0, object_format=0, script_names=script_names,
        strings=strings, fully_parsed=False, raw=bytes(data),
    )
    n = len(data)
    if n < 6:
        return info

    try:
        version       = struct.unpack_from("<h", data, 0)[0]
        object_format = struct.unpack_from("<h", data, 2)[0]
        script_count  = struct.unpack_from("<H", data, 4)[0]
        info.version = version
        info.object_format = object_format
        has_status = version >= 4

        pos = 6
        for _ in range(script_count):
            name, pos = _read_wstr(data, pos, encoding)
            script_names.append(name)
            if has_status:
                pos = _need(pos + 1, n)
            prop_count, pos = _read_u16(data, pos, n)
            for _ in range(prop_count):
                pname, pos = _read_wstr(data, pos, encoding)
                ptype, pos = _read_u8(data, pos, n)
                if has_status:
                    pos = _need(pos + 1, n)
                pos = _consume_value(
                    data, pos, n, ptype, name, pname, encoding, strings,
                )
        info.fully_parsed = True
    except (_VmadTruncated, struct.error):
        info.fully_parsed = False

    return info


def _consume_value(
    data: bytes, pos: int, n: int, ptype: int,
    script_name: str, prop_name: str, encoding: str,
    out: list[VmadString],
) -> int:
    """Consume one property value, collecting string values into *out*."""
    if ptype == _T_OBJECT:
        return _need(pos + _OBJECT_SIZE, n)
    if ptype == _T_INT32 or ptype == _T_FLOAT:
        return _need(pos + 4, n)
    if ptype == _T_BOOL:
        return _need(pos + 1, n)
    if ptype == _T_STRING:
        text, len_pos, vstart, vend, pos = _read_wstr_span(data, pos, encoding)
        risk, reason = classify_string_property(prop_name, text)
        out.append(VmadString(
            script_name=script_name, prop_name=prop_name, array_index=-1,
            value=text, risk=risk, reason=reason,
            len_pos=len_pos, value_start=vstart, value_end=vend,
        ))
        return pos
    if ptype == _T_ARR_STRING:
        count, pos = _read_u32(data, pos, n)
        for i in range(count):
            text, len_pos, vstart, vend, pos = _read_wstr_span(data, pos, encoding)
            risk, reason = classify_string_property(prop_name, text)
            out.append(VmadString(
                script_name=script_name, prop_name=prop_name, array_index=i,
                value=text, risk=risk, reason=reason,
                len_pos=len_pos, value_start=vstart, value_end=vend,
            ))
        return pos
    if ptype == _T_ARR_OBJECT:
        count, pos = _read_u32(data, pos, n)
        return _need(pos + count * _OBJECT_SIZE, n)
    if ptype == _T_ARR_INT32 or ptype == _T_ARR_FLOAT:
        count, pos = _read_u32(data, pos, n)
        return _need(pos + count * 4, n)
    if ptype == _T_ARR_BOOL:
        count, pos = _read_u32(data, pos, n)
        return _need(pos + count * 1, n)

    # Unknown property type — we can no longer locate the next field reliably.
    raise _VmadTruncated(f"unknown VMAD property type {ptype}")


# ── Safe editing ────────────────────────────────────────────────────────────────

def replace_strings(
    data: bytes, new_values: dict[int, str], encoding: str = "utf-8"
) -> bytes:
    """
    Return a new VMAD buffer with selected string values replaced.

    *new_values* maps a string index (position in :attr:`VmadInfo.strings`) to
    its new text.  Only the affected wstrings (their length prefix + bytes) are
    rewritten; every other byte of *data* is preserved exactly, so script
    fragments, aliases and unmodelled tails are never disturbed.
    """
    if not new_values:
        return bytes(data)

    info = parse_vmad(data, encoding)
    edits: list[tuple[int, int, bytes]] = []  # (len_pos, value_end, new_bytes)
    for idx, val in new_values.items():
        if idx < 0 or idx >= len(info.strings):
            continue
        s = info.strings[idx]
        nb = (val or "").encode(encoding, errors="replace")
        if len(nb) > 0xFFFF:
            nb = nb[:0xFFFF]
        edits.append((s.len_pos, s.value_end, nb))

    if not edits:
        return bytes(data)

    edits.sort()
    out = bytearray()
    prev = 0
    for len_pos, value_end, nb in edits:
        out += data[prev:len_pos]          # everything up to this string's prefix
        out += struct.pack("<H", len(nb))  # fresh length prefix
        out += nb                          # fresh bytes
        prev = value_end                   # skip the old prefix + bytes
    out += data[prev:]
    return bytes(out)


# ── Low-level readers (bounds-checked) ──────────────────────────────────────────

def _need(pos: int, n: int) -> int:
    if pos > n or pos < 0:
        raise _VmadTruncated("ran past end of VMAD buffer")
    return pos


def _read_u8(data: bytes, pos: int, n: int) -> tuple[int, int]:
    end = _need(pos + 1, n)
    return data[pos], end


def _read_u16(data: bytes, pos: int, n: int) -> tuple[int, int]:
    end = _need(pos + 2, n)
    return struct.unpack_from("<H", data, pos)[0], end


def _read_u32(data: bytes, pos: int, n: int) -> tuple[int, int]:
    end = _need(pos + 4, n)
    return struct.unpack_from("<I", data, pos)[0], end


def _read_wstr(data: bytes, pos: int, encoding: str) -> tuple[str, int]:
    """Read a wstring, returning (text, next_pos).  Used for names."""
    text, _len_pos, _vs, _ve, nxt = _read_wstr_span(data, pos, encoding)
    return text, nxt


def _read_wstr_span(
    data: bytes, pos: int, encoding: str
) -> tuple[str, int, int, int, int]:
    """
    Read a wstring, returning (text, len_pos, value_start, value_end, next_pos).

    *len_pos* is the offset of the 2-byte length prefix; the character bytes
    occupy ``[value_start, value_end)``.
    """
    n = len(data)
    _need(pos + 2, n)
    length = struct.unpack_from("<H", data, pos)[0]
    value_start = pos + 2
    value_end = value_start + length
    _need(value_end, n)
    text = data[value_start:value_end].decode(encoding, errors="replace")
    return text, pos, value_start, value_end, value_end


# ── Optional convenience: locate VMAD inside a record body ──────────────────────

def find_vmad_field(body: bytes) -> Optional[bytes]:
    """
    Return the VMAD field payload from a (decompressed) record body, or None.

    Handles the ``XXXX`` oversize-length override used when a VMAD exceeds the
    16-bit field size limit.
    """
    pos = 0
    next_size = 0
    n = len(body)
    while pos + 6 <= n:
        fsig = body[pos:pos + 4]
        fsize = struct.unpack_from("<H", body, pos + 4)[0]
        pos += 6
        actual = next_size if next_size else fsize
        next_size = 0
        if pos + actual > n:
            break
        fdata = body[pos:pos + actual]
        pos += actual
        if fsig == b"XXXX":
            if len(fdata) >= 4:
                next_size = struct.unpack_from("<I", fdata, 0)[0]
            continue
        if fsig == b"VMAD":
            return fdata
    return None
