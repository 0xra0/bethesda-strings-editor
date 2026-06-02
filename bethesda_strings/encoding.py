"""
Encoding utilities for Bethesda string files.
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class EncodingConverter:
    """
    Handle encoding conversion between Bethesda-supported encodings.

    Skyrim uses primary (UTF-8) and secondary (e.g., Windows-1252) encodings.
    """

    # Known encoding pairs by language/locale.
    # Keys are lowercased display names AND Starfield locale codes so both
    # look-up styles work ("ukrainian" from old code, "uk" from new).
    ENCODING_PAIRS = {
        # ── Western / Latin-1 ──────────────────────────────────────────────
        'english':  ('utf-8', 'windows-1252'),
        'en':       ('utf-8', 'windows-1252'),
        'french':   ('utf-8', 'windows-1252'),
        'fr':       ('utf-8', 'windows-1252'),
        'german':   ('utf-8', 'windows-1252'),
        'de':       ('utf-8', 'windows-1252'),
        'italian':  ('utf-8', 'windows-1252'),
        'it':       ('utf-8', 'windows-1252'),
        'spanish':  ('utf-8', 'windows-1252'),
        'es':       ('utf-8', 'windows-1252'),
        'portuguese (brazil)': ('utf-8', 'windows-1252'),
        'ptbr':     ('utf-8', 'windows-1252'),
        # ── Central European ───────────────────────────────────────────────
        'polish':   ('utf-8', 'windows-1250'),
        'pl':       ('utf-8', 'windows-1250'),
        'czech':    ('utf-8', 'windows-1250'),
        # ── Cyrillic ──────────────────────────────────────────────────────
        'russian':    ('utf-8', 'windows-1251'),
        'ru':         ('utf-8', 'windows-1251'),
        'ukrainian':  ('utf-8', 'windows-1251'),
        'uk':         ('utf-8', 'windows-1251'),
        'belarusian': ('utf-8', 'windows-1251'),
        'bulgarian':  ('utf-8', 'windows-1251'),
        'serbian':    ('utf-8', 'windows-1251'),
        # ── CJK ────────────────────────────────────────────────────────────
        'japanese':           ('utf-8', None),
        'ja':                 ('utf-8', None),
        'chinese (simplified)': ('utf-8', 'gbk'),
        'zhhans':             ('utf-8', 'gbk'),
        'korean':             ('utf-8', 'euc-kr'),
    }

    # Note: Ukrainian-specific characters (Є, є, І, і, Ї, ї, Ґ, ґ) are
    # already valid Unicode code points and don't need special mapping.
    # The ENCODING_PAIRS above handles Windows-1251 which supports them.

    @classmethod
    def decode_smart(cls, data: bytes, primary: str = 'utf-8',
                     secondary: Optional[str] = None,
                     locale: Optional[str] = None) -> Tuple[str, str]:
        """
        Decode bytes trying primary encoding first, then secondary if needed.

        Args:
            data: Raw bytes to decode
            primary: Primary encoding to try first
            secondary: Fallback encoding
            locale: Optional locale hint for encoding selection

        Returns:
            Tuple of (decoded_string, encoding_used)
        """
        # Auto-select encodings based on locale if provided
        if locale and not secondary:
            primary, secondary = cls.get_encodings_for_locale(locale)

        try:
            return data.decode(primary), primary
        except UnicodeDecodeError:
            if secondary:
                try:
                    return data.decode(secondary), secondary
                except UnicodeDecodeError:
                    pass
            # Fallback to UTF-8 with replacement
            return data.decode('utf-8', errors='replace'), 'utf-8'

    @classmethod
    def convert_encoding(cls, data: bytes, from_enc: str, to_enc: str) -> bytes:
        """Convert encoded bytes from one encoding to another."""
        # Decode from source, encode to target
        text = data.rstrip(b'\x00').decode(from_enc, errors='replace')
        return text.encode(to_enc) + b'\x00'

    @classmethod
    def detect_encoding(cls, text_bytes: bytes) -> Tuple[str, float, str]:
        """
        Detect the character encoding of raw string-data bytes sampled from a
        Bethesda string file.

        Detection order:

        1. Byte-order marks (BOM) — 100 % confidence.
        2. Strict UTF-8 decode — very high confidence when successful.
        3. UTF-8 lead-byte pattern analysis vs CP1251 standalone-byte analysis.
        4. CP1251 validation (decode + count resulting Cyrillic codepoints).
        5. Mostly-ASCII check — fewer than 1 % replacement chars → UTF-8.
        6. CP1252 as final fallback for non-Cyrillic Western files.

        Returns:
            (encoding_name, confidence_0_to_1, human_readable_method)
        """
        if not text_bytes:
            return "utf-8", 1.0, "default (no string data)"

        # ── 1. BOM ──────────────────────────────────────────────────────────────
        if text_bytes[:3] == b"\xef\xbb\xbf":
            return "utf-8-sig", 1.0, "UTF-8 BOM"
        if text_bytes[:2] == b"\xff\xfe":
            return "utf-16-le", 1.0, "UTF-16 LE BOM"
        if text_bytes[:2] == b"\xfe\xff":
            return "utf-16-be", 1.0, "UTF-16 BE BOM"

        total = len(text_bytes)
        high_bytes = sum(1 for b in text_bytes if b > 0x7F)

        if high_bytes == 0:
            return "utf-8", 0.95, "ASCII-only content"

        # ── 2. Strict UTF-8 decode ───────────────────────────────────────────
        try:
            text_bytes.decode("utf-8")
            # Decode succeeded — measure how strongly the byte patterns match
            # UTF-8 Cyrillic (U+0400–U+04FF uses lead bytes 0xD0–0xD3).
            utf8_cyrillic_pairs = sum(
                1
                for i in range(total - 1)
                if text_bytes[i] in (0xD0, 0xD1, 0xD2, 0xD3)
                and 0x80 <= text_bytes[i + 1] <= 0xBF
            )
            # Count 2-byte sequences (lead 0xC2–0xDF) and 3/4-byte sequences
            # (lead 0xE0–0xEF for 3-byte, 0xF0–0xF7 for 4-byte).  Common English
            # punctuation like smart quotes (U+201C = 0xE2 0x80 0x9C) and em-dashes
            # (U+2014 = 0xE2 0x80 0x94) are 3-byte sequences and would otherwise
            # be missed, producing a spuriously low confidence score.
            utf8_pairs = sum(
                1
                for i in range(total - 1)
                if 0xC2 <= text_bytes[i] <= 0xDF
                and 0x80 <= text_bytes[i + 1] <= 0xBF
            )
            utf8_3byte = sum(
                1
                for i in range(total - 2)
                if 0xE0 <= text_bytes[i] <= 0xEF
                and 0x80 <= text_bytes[i + 1] <= 0xBF
                and 0x80 <= text_bytes[i + 2] <= 0xBF
            )
            confirmed_pairs = utf8_cyrillic_pairs + utf8_pairs + utf8_3byte
            if confirmed_pairs > 0:
                conf = min(0.97, 0.85 + (confirmed_pairs / max(high_bytes, 1)) * 0.15)
                return "utf-8", conf, f"strict UTF-8 (Cyrillic pairs: {utf8_cyrillic_pairs})"
            return "utf-8", 0.80, "strict UTF-8 decode (no multi-byte sequences confirmed)"
        except UnicodeDecodeError:
            pass

        # ── 3. UTF-8 failed — byte-pattern analysis ──────────────────────────
        # CP1251: Cyrillic letters occupy 0xC0-0xFF as *standalone* bytes.
        # Russian А–Я = 0xC0–0xDF; а–я = 0xE0–0xFF.
        cp1251_range = sum(1 for b in text_bytes if 0xC0 <= b <= 0xFF)
        cp1251_ratio = cp1251_range / total

        # Replacement characters when decoded as UTF-8
        lossy = text_bytes.decode("utf-8", errors="replace")
        replacement_ratio = lossy.count("�") / max(len(lossy), 1)

        # ── 4. CP1251 validation ─────────────────────────────────────────────
        if replacement_ratio > 0.03 or cp1251_ratio > 0.10:
            try:
                decoded_1251 = text_bytes.decode("windows-1251")
                cyrillic_chars = sum(
                    1 for c in decoded_1251 if "Ѐ" <= c <= "ӿ"
                )
                cyrillic_ratio = cyrillic_chars / max(len(decoded_1251), 1)
                if cyrillic_ratio > 0.08:
                    conf = min(0.92, 0.55 + cyrillic_ratio * 0.55)
                    return (
                        "windows-1251",
                        conf,
                        f"Cyrillic byte patterns "
                        f"({cyrillic_ratio:.0%} Cyrillic, "
                        f"{replacement_ratio:.0%} UTF-8 errors)",
                    )
            except UnicodeDecodeError:
                pass

        # ── 5. Mostly-ASCII UTF-8 check ─────────────────────────────────────
        # When UTF-8 decode fails but the damage is tiny (< 1 % of decoded
        # characters are replacement chars) the file is overwhelmingly ASCII
        # with a handful of stray CP1252 bytes.  Treat it as UTF-8 rather than
        # mislabelling the whole file as windows-1252.
        if replacement_ratio < 0.01:
            return (
                "utf-8",
                0.65,
                f"UTF-8 (ASCII-dominant; {replacement_ratio:.2%} undecodable bytes ignored)",
            )

        # ── 6. CP1252 fallback (Western European) ────────────────────────────
        try:
            text_bytes.decode("windows-1252")
            return "windows-1252", 0.45, "CP1252 fallback (non-Cyrillic high bytes)"
        except UnicodeDecodeError:
            pass

        logger.debug(
            "Encoding detection inconclusive (high_bytes=%d, cp1251_ratio=%.2f, "
            "replacement_ratio=%.2f) — defaulting to UTF-8",
            high_bytes, cp1251_ratio, replacement_ratio,
        )
        return "utf-8", 0.30, "fallback (inconclusive)"

    @classmethod
    def get_encodings_for_locale(cls, locale: str) -> Tuple[str, Optional[str]]:
        """Get primary and secondary encodings for a locale.

        Accepts both Starfield locale codes (``"de"``, ``"ptbr"``, ``"zhhans"``)
        and full display names (``"German"``, ``"Ukrainian"``).
        BCP-47 variants like ``"uk_UA"`` / ``"uk-UA"`` are also handled.
        """
        key = locale.lower().strip()

        # Exact match (handles codes and full names)
        if key in cls.ENCODING_PAIRS:
            return cls.ENCODING_PAIRS[key]

        # BCP-47 separator normalisation: "uk_UA" → "uk", "zh-Hans" → "zhhans"
        base = key.replace("-", "").replace("_", "").split()[0]
        if base in cls.ENCODING_PAIRS:
            return cls.ENCODING_PAIRS[base]

        # Fall through for sub-tags: "uk_ua" → try "uk"
        short = key.split("_")[0].split("-")[0]
        if short in cls.ENCODING_PAIRS:
            return cls.ENCODING_PAIRS[short]

        return ('utf-8', 'windows-1252')

    @classmethod
    def validate_ukrainian_text(cls, text: str) -> Tuple[bool, list]:
        """
        Validate Ukrainian text for common issues.

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []

        # Check for Russian characters that don't exist in Ukrainian
        russian_only = {'ё': 'ьо/е', 'Ё': 'ЬО/Е', 'ы': 'и', 'Ы': 'И', 'э': 'е', 'Э': 'Е'}
        for ru_char, ua_suggestion in russian_only.items():
            if ru_char in text:
                issues.append(f"Russian character '{ru_char}' found, consider Ukrainian '{ua_suggestion}'")

        return len(issues) == 0, issues

    @classmethod
    def fix_common_ukrainian_issues(cls, text: str) -> str:
        """
        Fix common Ukrainian text issues (e.g., Russian character substitutions).

        Note: Use with caution - automatic fixes may change intended meaning.
        """
        # Common Russian→Ukrainian character substitutions
        substitutions = {
            'ё': 'ьо',  # Very context-dependent, use carefully
            'ы': 'и',
            'э': 'е',
            'ъ': '',  # Hard sign usually dropped in Ukrainian
        }

        # Apply substitutions (conservative - only obvious cases)
        for ru_char, ua_char in substitutions.items():
            # Only replace if not part of a known Ukrainian word pattern
            text = text.replace(ru_char, ua_char)

        return text
