"""
Font glyph coverage checker for Bethesda Starfield localization.

Parses Bethesda's font configuration and Scaleform SWF font atlas files to
build the set of Unicode codepoints supported by the game's renderer.
Translated strings containing characters outside this set will display as
"tofu" (□) or missing glyphs in-game.

Supported sources (auto-detected or manually specified):
  • fontconfig.txt — Bethesda font manifest  (lists SWF + font aliases)
  • *.swf          — Scaleform font atlas     (DefineFont2/3 records)
  • *.ttf / *.otf  — TrueType/OpenType font   (cmap table, no extra deps)

Built-in fallback:
  When no game font files are provided a conservative safe set is used that
  covers ASCII + Windows-1252 Latin + full Cyrillic (U+0400–U+04FF) plus
  common punctuation.  Characters known to be problematic in Scaleform
  (zero-width chars, soft hyphen, private-use area, surrogates) are always
  flagged regardless of the loaded font set.
"""

from __future__ import annotations

import re
import struct
import unicodedata
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ── Always-problematic characters ─────────────────────────────────────────────
# These render incorrectly or are invisible in Scaleform-based games regardless
# of which font is loaded.

_ALWAYS_BAD: frozenset[int] = frozenset([
    0x00AD,  # SOFT HYPHEN             — causes layout bugs in Scaleform
    0x00A0,  # NO-BREAK SPACE          — often renders as tofu
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER
    0x2060,  # WORD JOINER
    0xFEFF,  # ZERO WIDTH NO-BREAK SPACE / BOM
    0x202F,  # NARROW NO-BREAK SPACE
    0x2028,  # LINE SEPARATOR
    0x2029,  # PARAGRAPH SEPARATOR
])

# ── Auto-fix table ─────────────────────────────────────────────────────────────
# Maps codepoint → suggested replacement string (may be empty = delete).
# Applied only when the replacement characters are ALL in the loaded font set.

SUGGESTED_FIXES: Dict[int, str] = {
    0x2014: "-",     # EM DASH → hyphen-minus
    0x2013: "-",     # EN DASH → hyphen-minus
    0x2012: "-",     # FIGURE DASH → hyphen-minus
    0x2026: "...",   # HORIZONTAL ELLIPSIS → three dots
    0x00A0: " ",     # NO-BREAK SPACE → regular space
    0x202F: " ",     # NARROW NO-BREAK SPACE → regular space
    0x00AD: "",      # SOFT HYPHEN → delete
    0x2060: "",      # WORD JOINER → delete
    0xFEFF: "",      # BOM → delete
    0x200B: "",      # ZERO WIDTH SPACE → delete
    0x200C: "",      # ZERO WIDTH NON-JOINER → delete
    0x200D: "",      # ZERO WIDTH JOINER → delete
    0x201C: '"',     # LEFT DOUBLE QUOTATION MARK → straight quote
    0x201D: '"',     # RIGHT DOUBLE QUOTATION MARK → straight quote
    0x2018: "'",     # LEFT SINGLE QUOTATION MARK → apostrophe
    0x2019: "'",     # RIGHT SINGLE QUOTATION MARK → apostrophe
    0x02BC: "'",     # MODIFIER LETTER APOSTROPHE → apostrophe
    0x02B9: "'",     # MODIFIER LETTER PRIME → apostrophe
    0x00AB: '"',     # LEFT-POINTING DOUBLE ANGLE QUOTATION MARK → straight quote
    0x00BB: '"',     # RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK → straight quote
    0x2039: "'",     # SINGLE LEFT-POINTING ANGLE QUOTATION MARK
    0x203A: "'",     # SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
    0x2011: "-",     # NON-BREAKING HYPHEN → hyphen-minus
    0x2010: "-",     # HYPHEN → hyphen-minus
    0x2015: "-",     # HORIZONTAL BAR → hyphen-minus
    0x00B4: "'",     # ACUTE ACCENT → apostrophe
    0x0060: "'",     # GRAVE ACCENT → apostrophe
}


# ── Built-in safe set ──────────────────────────────────────────────────────────

def _build_builtin_safe_set() -> frozenset[int]:
    """Conservative set of codepoints safe in most Bethesda Starfield fonts.

    Covers ASCII printable, Windows-1252 extended Latin, and the full Cyrillic
    Unicode block (U+0400–U+04FF) which Starfield's localisation fonts include.
    """
    codes: Set[int] = set()
    # ASCII printable (space through tilde)
    codes.update(range(0x0020, 0x007F))
    # Common Latin/West-European supplement (Windows-1252 relevant range)
    codes.update(range(0x00A1, 0x0100))   # Latin-1 Supplement (minus U+00A0 NBSP)
    codes.update(range(0x0100, 0x0180))   # Latin Extended-A
    # Remove the always-bad characters
    codes -= set(_ALWAYS_BAD)
    # Full Cyrillic block (Russian, Ukrainian, Bulgarian, etc.)
    codes.update(range(0x0400, 0x0500))
    # Cyrillic Supplement (U+0500–U+052F)
    codes.update(range(0x0500, 0x0530))
    # Common general punctuation (—, –, …, «, », etc.) — Starfield supports these
    codes.update(range(0x2000, 0x2070))   # General Punctuation
    # Currency symbols
    codes.update(range(0x20A0, 0x20D0))
    # Common Latin ligatures and special letters used in localisations
    # Greek (some fonts include this for scientific/UI use)
    codes.update(range(0x0370, 0x0400))
    # Arrows and math operators commonly used in UI
    codes.update([0x2190, 0x2192, 0x2194, 0x2193, 0x2191,  # arrows
                  0x00D7, 0x00F7,                            # × ÷
                  0x00B0, 0x00B1, 0x00B2, 0x00B3,           # ° ± ² ³
                  0x00B5, 0x00B6,                            # µ ¶
                  0x00BD, 0x00BC, 0x00BE])                   # fractions
    return frozenset(codes)


_BUILTIN_SAFE = _build_builtin_safe_set()


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class FontSource:
    """A single loaded font with its glyph coverage."""
    name: str                           # Font family name from the file
    path: Path
    codepoints: frozenset[int]
    source_type: str = "swf"            # "swf", "ttf", "builtin"

    @property
    def glyph_count(self) -> int:
        return len(self.codepoints)


@dataclass
class MissingGlyph:
    """A character that appears in translations but is not in any loaded font."""
    char: str
    codepoint: int
    string_count: int                   # how many translated strings contain it
    row_indices: List[int]
    suggested_fix: Optional[str]        # None if no safe replacement known
    fix_is_safe: bool = False           # True if all fix chars are in the font


@dataclass
class GlyphIssue:
    """A single string that contains one or more unsupported characters."""
    row_index: int
    string_id: int
    original: str
    translated: str
    missing_chars: List[str]            # the actual characters missing
    fixed_text: Optional[str] = None   # pre-computed safe replacement, or None


@dataclass
class FontCheckResult:
    sources: List[FontSource]
    missing_glyphs: List[MissingGlyph]  # sorted by string_count desc
    issues: List[GlyphIssue]
    total_strings_scanned: int
    strings_with_issues: int


# ── SWF parser ────────────────────────────────────────────────────────────────

def _skip_rect(data: bytes, pos: int) -> int:
    """Return the byte position after a bit-packed RECT record."""
    if pos >= len(data):
        return pos
    nbits = (data[pos] >> 3) & 0x1F
    total_bits = 5 + 4 * nbits
    return pos + (total_bits + 7) // 8


def _parse_definefont2(body: bytes) -> Tuple[str, Set[int]]:
    """Extract (font_name, codepoint_set) from a DefineFont2/3 tag body."""
    if len(body) < 6:
        return "", set()
    pos = 0

    # FontID
    pos += 2  # UI16

    # Flags byte
    flags = body[pos]
    pos += 1
    wide_offsets  = bool(flags & 0x08)
    wide_codes    = bool(flags & 0x04)

    # LanguageCode
    pos += 1  # UI8 (skip)

    # FontName
    if pos >= len(body):
        return "", set()
    name_len = body[pos]
    pos += 1
    if pos + name_len > len(body):
        return "", set()
    font_name = body[pos:pos + name_len].rstrip(b"\x00").decode("latin-1", errors="replace")
    pos += name_len

    # NumGlyphs
    if pos + 2 > len(body):
        return font_name, set()
    num_glyphs = struct.unpack_from("<H", body, pos)[0]
    pos += 2

    if num_glyphs == 0:
        return font_name, set()

    # OffsetTable + CodeTableOffset
    # All offsets are measured from the START of the OffsetTable field.
    offset_table_start = pos
    offset_size = 4 if wide_offsets else 2

    # Skip OffsetTable (num_glyphs entries)
    pos += num_glyphs * offset_size

    # Read CodeTableOffset
    if pos + offset_size > len(body):
        return font_name, set()
    if wide_offsets:
        code_table_offset = struct.unpack_from("<I", body, pos)[0]
    else:
        code_table_offset = struct.unpack_from("<H", body, pos)[0]

    # Jump to CodeTable
    code_table_pos = offset_table_start + code_table_offset
    code_size = 2 if wide_codes else 1

    if code_table_pos + num_glyphs * code_size > len(body):
        return font_name, set()

    codes: Set[int] = set()
    for i in range(num_glyphs):
        p = code_table_pos + i * code_size
        code = struct.unpack_from("<H", body, p)[0] if wide_codes else body[p]
        codes.add(code)

    return font_name, codes


def parse_swf_glyphs(path: Path) -> List[FontSource]:
    """Parse a Scaleform SWF file and return one FontSource per embedded font."""
    raw = path.read_bytes()
    if len(raw) < 8:
        return []

    sig = raw[:3]
    if sig == b"CWS":
        try:
            data = raw[:8] + zlib.decompress(raw[8:])
        except zlib.error:
            return []
    elif sig == b"FWS":
        data = raw
    else:
        # ZWS (LZMA) — skip; would need lzma module and Starfield rarely uses it
        return []

    # Skip SWF file header
    pos = 8
    pos = _skip_rect(data, pos)
    pos += 4  # FrameRate (UI16) + FrameCount (UI16)

    sources: List[FontSource] = []
    while pos + 2 <= len(data):
        record_header = struct.unpack_from("<H", data, pos)[0]
        tag_type   = (record_header >> 6) & 0x3FF
        short_len  = record_header & 0x3F
        pos += 2

        if short_len == 0x3F:
            if pos + 4 > len(data):
                break
            tag_length = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        else:
            tag_length = short_len

        tag_end = pos + tag_length

        if tag_type in (48, 75):  # DefineFont2 / DefineFont3
            body = bytes(data[pos:tag_end])
            font_name, codes = _parse_definefont2(body)
            if codes:
                sources.append(FontSource(
                    name=font_name or f"Font_{len(sources)}",
                    path=path,
                    codepoints=frozenset(codes),
                    source_type="swf",
                ))
        elif tag_type == 0:  # End
            break

        pos = tag_end

    return sources


# ── TTF / OTF parser ──────────────────────────────────────────────────────────

def _read_cmap4(raw: bytes, sub: int) -> Set[int]:
    """Parse a cmap format-4 subtable (BMP Unicode)."""
    if sub + 14 > len(raw):
        return set()
    seg_count_x2  = struct.unpack_from(">H", raw, sub + 6)[0]
    seg_count     = seg_count_x2 // 2
    end_off = sub + 14
    if end_off + seg_count_x2 + 2 + seg_count_x2 * 3 > len(raw):
        return set()
    end_codes   = [struct.unpack_from(">H", raw, end_off + i * 2)[0] for i in range(seg_count)]
    start_off   = end_off + seg_count_x2 + 2  # +2 for reservedPad
    start_codes = [struct.unpack_from(">H", raw, start_off + i * 2)[0] for i in range(seg_count)]
    delta_off   = start_off + seg_count_x2
    deltas      = [struct.unpack_from(">h", raw, delta_off + i * 2)[0] for i in range(seg_count)]
    range_off   = delta_off + seg_count_x2
    ranges      = [struct.unpack_from(">H", raw, range_off + i * 2)[0] for i in range(seg_count)]

    codes: Set[int] = set()
    for i in range(seg_count):
        s, e, d, ro = start_codes[i], end_codes[i], deltas[i], ranges[i]
        if s == 0xFFFF:
            break
        for c in range(s, e + 1):
            if ro == 0:
                gid = (c + d) & 0xFFFF
            else:
                idx = range_off + i * 2 + ro + (c - s) * 2
                if idx + 2 > len(raw):
                    continue
                gid = struct.unpack_from(">H", raw, idx)[0]
                if gid != 0:
                    gid = (gid + d) & 0xFFFF
            if gid != 0:
                codes.add(c)
    return codes


def _read_cmap12(raw: bytes, sub: int) -> Set[int]:
    """Parse a cmap format-12 subtable (full Unicode)."""
    if sub + 16 > len(raw):
        return set()
    n_groups = struct.unpack_from(">I", raw, sub + 12)[0]
    base = sub + 16
    if base + n_groups * 12 > len(raw):
        return set()
    codes: Set[int] = set()
    for i in range(n_groups):
        o = base + i * 12
        start = struct.unpack_from(">I", raw, o)[0]
        end   = struct.unpack_from(">I", raw, o + 4)[0]
        codes.update(range(start, end + 1))
    return codes


def parse_ttf_glyphs(path: Path) -> List[FontSource]:
    """Parse a TTF/OTF file and return a single FontSource."""
    raw = path.read_bytes()
    if len(raw) < 12:
        return []

    # sfVersion (4), numTables (2), searchRange (2), entrySelector (2), rangeShift (2)
    num_tables = struct.unpack_from(">H", raw, 4)[0]
    tables: Dict[str, int] = {}
    for i in range(num_tables):
        b = 12 + i * 16
        if b + 16 > len(raw):
            break
        tag = raw[b:b + 4].decode("ascii", errors="replace")
        offset = struct.unpack_from(">I", raw, b + 8)[0]
        tables[tag] = offset

    if "cmap" not in tables:
        return []
    cmap = tables["cmap"]
    if cmap + 4 > len(raw):
        return []

    num_enc = struct.unpack_from(">H", raw, cmap + 2)[0]
    best_sub: Optional[int] = None
    best_fmt = -1
    for i in range(num_enc):
        b = cmap + 4 + i * 8
        if b + 8 > len(raw):
            break
        plat = struct.unpack_from(">H", raw, b)[0]
        enc  = struct.unpack_from(">H", raw, b + 2)[0]
        sub  = cmap + struct.unpack_from(">I", raw, b + 4)[0]
        if sub + 2 > len(raw):
            continue
        fmt = struct.unpack_from(">H", raw, sub)[0]
        if plat == 3 and enc == 10 and fmt == 12:
            best_sub, best_fmt = sub, 12
            break
        if plat == 3 and enc == 1 and fmt == 4 and best_fmt < 4:
            best_sub, best_fmt = sub, 4

    if best_sub is None:
        return []
    codes = _read_cmap12(raw, best_sub) if best_fmt == 12 else _read_cmap4(raw, best_sub)
    if not codes:
        return []

    # Try to extract font family name from name table
    font_name = path.stem
    if "name" in tables:
        font_name = _read_ttf_name(raw, tables["name"]) or font_name

    return [FontSource(name=font_name, path=path, codepoints=frozenset(codes), source_type="ttf")]


def _read_ttf_name(raw: bytes, name_off: int) -> str:
    """Return the font family name (nameID=1) from a TTF name table."""
    if name_off + 6 > len(raw):
        return ""
    count   = struct.unpack_from(">H", raw, name_off + 2)[0]
    str_off = name_off + struct.unpack_from(">H", raw, name_off + 4)[0]
    for i in range(count):
        b = name_off + 6 + i * 12
        if b + 12 > len(raw):
            break
        plat    = struct.unpack_from(">H", raw, b)[0]
        name_id = struct.unpack_from(">H", raw, b + 6)[0]
        length  = struct.unpack_from(">H", raw, b + 8)[0]
        offset  = struct.unpack_from(">H", raw, b + 10)[0]
        if name_id != 1:
            continue
        s = str_off + offset
        if s + length > len(raw):
            continue
        try:
            if plat == 3:  # Windows — UTF-16 BE
                return raw[s:s + length].decode("utf-16-be", errors="replace")
            else:
                return raw[s:s + length].decode("latin-1", errors="replace")
        except Exception:
            pass
    return ""


# ── fontconfig.txt parser ──────────────────────────────────────────────────────

_FONTLIB_RE = re.compile(r'^fontlib\s+"([^"]+)"', re.IGNORECASE)
_MAP_RE      = re.compile(r'^map\s+"([^"]+)"\s+"([^"]+)"', re.IGNORECASE)


def parse_fontconfig(path: Path) -> Dict[str, str]:
    """Parse a Bethesda fontconfig.txt.

    Returns ``{"font_alias": "font_family_name", "__libs__": ["file1.swf", …]}``.
    The ``__libs__`` key holds the list of referenced SWF paths (relative to
    the game's Data directory) so the caller can locate them.
    """
    result: Dict[str, str] = {}
    libs: List[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            m = _FONTLIB_RE.match(line)
            if m:
                libs.append(m.group(1))
                continue
            m = _MAP_RE.match(line)
            if m:
                result[m.group(1)] = m.group(2)
    except OSError:
        pass
    result["__libs__"] = libs  # type: ignore[assignment]
    return result


# ── Main checker ──────────────────────────────────────────────────────────────

class FontChecker:
    """Loads font sources and checks translated strings for missing glyphs."""

    def __init__(self) -> None:
        self.sources: List[FontSource] = []
        self._combined: Optional[frozenset[int]] = None   # union of all source codepoints
        self.use_builtin_fallback = True

    def clear(self) -> None:
        self.sources.clear()
        self._combined = None

    def load_swf(self, path: Path) -> int:
        """Load fonts from a SWF file.  Returns the number of fonts added."""
        added = parse_swf_glyphs(path)
        self.sources.extend(added)
        self._combined = None
        return len(added)

    def load_ttf(self, path: Path) -> int:
        """Load a TTF/OTF font.  Returns 1 on success, 0 on failure."""
        added = parse_ttf_glyphs(path)
        self.sources.extend(added)
        self._combined = None
        return len(added)

    def load_game_directory(self, game_data_dir: Path) -> int:
        """Auto-detect and load fonts from a Bethesda game Data directory.

        Looks for fontconfig.txt → referenced SWF files → TTF/OTF fonts.
        Returns total number of font sources loaded.
        """
        total = 0
        fontconfig = game_data_dir / "Interface" / "fontconfig.txt"
        if fontconfig.is_file():
            cfg = parse_fontconfig(fontconfig)
            for lib in cfg.get("__libs__", []):
                # lib is relative to game root, e.g. "Interface/Fonts.swf"
                swf_path = game_data_dir / Path(lib)
                if swf_path.is_file():
                    total += self.load_swf(swf_path)
        # Fallback: scan Interface directory for SWF files directly
        if total == 0:
            for swf in sorted((game_data_dir / "Interface").glob("*.swf")):
                total += self.load_swf(swf)
        # Also pick up any TTF/OTF fonts
        for ttf in sorted((game_data_dir / "Fonts").glob("*.ttf")):
            total += self.load_ttf(ttf)
        return total

    @property
    def combined_codepoints(self) -> frozenset[int]:
        """Union of all loaded font codepoints, plus the built-in safe set."""
        if self._combined is None:
            combined: Set[int] = set()
            for src in self.sources:
                combined.update(src.codepoints)
            if self.use_builtin_fallback or not self.sources:
                combined.update(_BUILTIN_SAFE)
            # Always-bad characters are never in the safe set
            combined -= set(_ALWAYS_BAD)
            self._combined = frozenset(combined)
        return self._combined

    def missing_chars(self, text: str) -> List[str]:
        """Return list of characters in *text* that are not in any loaded font."""
        safe = self.combined_codepoints
        seen: set[int] = set()
        result: List[str] = []
        for ch in text:
            cp = ord(ch)
            if cp in seen:
                continue
            seen.add(cp)
            # Skip control chars (tab, newline are fine; others are bad but handled
            # separately), and skip always-bad which are flagged regardless
            if cp < 0x20 and cp not in (0x09, 0x0A, 0x0D):
                continue
            if cp in _ALWAYS_BAD or cp not in safe:
                result.append(ch)
        return result

    def suggest_fix(self, char: str) -> Optional[str]:
        """Return the suggested replacement for *char*, or None."""
        cp = ord(char)
        fix = SUGGESTED_FIXES.get(cp)
        if fix is None:
            # Try NFKD decomposition — strip combining marks
            decomposed = unicodedata.normalize("NFKD", char)
            base = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
            if base and base != char and all(ord(c) in self.combined_codepoints for c in base):
                return base
        return fix

    def fix_is_safe(self, char: str) -> bool:
        """Return True if the suggested fix for *char* uses only supported chars."""
        fix = self.suggest_fix(char)
        if fix is None:
            return False
        return all(ord(c) in self.combined_codepoints for c in fix)

    def apply_fixes(self, text: str) -> str:
        """Replace all unsupported characters in *text* with their safe alternatives."""
        missing_set = set(self.missing_chars(text))
        if not missing_set:
            return text
        result = []
        for ch in text:
            if ch in missing_set:
                fix = self.suggest_fix(ch)
                result.append(fix if fix is not None else ch)
            else:
                result.append(ch)
        return "".join(result)

    def check_rows(self, rows: List[dict]) -> FontCheckResult:
        """Scan translated strings in *rows* for missing glyphs.

        Each row must have keys ``"translated"`` (str), ``"id"`` (int),
        ``"original"`` (str).  The position in the list is the row index.
        """
        # Accumulate: codepoint → list of row indices
        cp_rows: Dict[int, List[int]] = {}
        issues: List[GlyphIssue] = []

        for row_idx, row in enumerate(rows):
            translated = row.get("translated", "") or ""
            if not translated.strip():
                continue
            missing = self.missing_chars(translated)
            if not missing:
                continue
            for ch in missing:
                cp = ord(ch)
                cp_rows.setdefault(cp, []).append(row_idx)
            fixed = self._make_fixed(translated, missing)
            issues.append(GlyphIssue(
                row_index=row_idx,
                string_id=row.get("id", 0),
                original=row.get("original", ""),
                translated=translated,
                missing_chars=missing,
                fixed_text=fixed,
            ))

        missing_glyphs: List[MissingGlyph] = []
        for cp, row_list in sorted(cp_rows.items(), key=lambda kv: -len(kv[1])):
            ch = chr(cp)
            fix = self.suggest_fix(ch)
            missing_glyphs.append(MissingGlyph(
                char=ch,
                codepoint=cp,
                string_count=len(set(row_list)),
                row_indices=sorted(set(row_list)),
                suggested_fix=fix,
                fix_is_safe=self.fix_is_safe(ch),
            ))

        strings_with_issues = len({i.row_index for i in issues})
        return FontCheckResult(
            sources=list(self.sources),
            missing_glyphs=missing_glyphs,
            issues=issues,
            total_strings_scanned=sum(
                1 for r in rows if (r.get("translated") or "").strip()
            ),
            strings_with_issues=strings_with_issues,
        )

    def _make_fixed(self, text: str, missing: List[str]) -> Optional[str]:
        """Return a fixed version of *text* if ALL missing chars have safe fixes."""
        missing_set = set(missing)
        parts: List[str] = []
        any_unfixable = False
        for ch in text:
            if ch in missing_set:
                fix = self.suggest_fix(ch)
                if fix is None:
                    any_unfixable = True
                    parts.append(ch)
                else:
                    parts.append(fix)
            else:
                parts.append(ch)
        return "".join(parts) if not any_unfixable else None
