"""
Post-translation quality checker for Bethesda/Starfield game strings.

Detects issues that would break text in-game:
  - Missing/extra game tags (<Alias=...>, <br>, [PLYR], %s, etc.)
  - Empty or untranslated strings
  - Encoding failures for the target file format
  - Suspicious length ratios (hallucination / truncation)
  - Source-language leakage (untranslated Russian in Ukrainian output)
  - AI repetition artifacts and commentary prefixes
  - Missing numbers, URLs, sentence count drift
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

SEVERITY_ERROR = "error"      # Will break in game
SEVERITY_WARNING = "warning"  # May look wrong in game
SEVERITY_INFO = "info"        # Informational only

# ── Issue classification ───────────────────────────────────────────────────────

# Codes fixable by mechanical text manipulation (no AI needed).
AUTOFIX_CODES: frozenset = frozenset({
    "LEADING_WHITESPACE_REMOVED",
    "TRAILING_WHITESPACE_MISMATCH",
    "MISSING_NEWLINES",
    # Newline count wrong but non-zero → restore missing ones proportionally
    "NEWLINE_COUNT_MISMATCH",
    "SOURCE_LANGUAGE_LEAK",
    "SPURIOUS_QUOTES",
    "ENCODING_ERROR",
    "EXTRA_TAG",
    "MISSING_TAG",
    "CASE_MISMATCH",
    # AI output artifacts that can be stripped deterministically
    "AI_ARTIFACT",
    "REPETITIVE_CONTENT",
    # Non-Cyrillic originals with empty translation → copy original
    "EMPTY_TRANSLATION",
    # Translation is a truncated prefix of the original (AI stopped mid-text)
    # → copy original when source is already in target language
    "TRANSLATION_TRUNCATED",
})

# Codes that require AI retranslation to properly fix.
RETRANSLATE_CODES: frozenset = frozenset({
    "EMPTY_TRANSLATION",
    "UNTRANSLATED",
    "SUSPICIOUSLY_SHORT",
    "SUSPICIOUSLY_LONG",
    "REPETITIVE_CONTENT",
    "ENGLISH_LEAK",
    "MISSING_NUMBER",
    "MISSING_URL",
    "SENTENCE_COUNT_MISMATCH",
    "GLOSSARY_MISMATCH",
    "LOW_UKRAINIAN_COVERAGE",
    # Truncated Russian originals need AI retranslation
    "TRANSLATION_TRUNCATED",
})


@dataclass
class QualityIssue:
    severity: str
    code: str
    message: str
    detail: str = ""


@dataclass
class QualityReport:
    row_index: int
    string_id: int
    original: str
    translated: str
    issues: List[QualityIssue] = field(default_factory=list)

    @property
    def severity(self) -> str:
        if any(i.severity == SEVERITY_ERROR for i in self.issues):
            return SEVERITY_ERROR
        if any(i.severity == SEVERITY_WARNING for i in self.issues):
            return SEVERITY_WARNING
        if any(i.severity == SEVERITY_INFO for i in self.issues):
            return SEVERITY_INFO
        return ""

    @property
    def has_issues(self) -> bool:
        return bool(self.issues)


# ── Game tag patterns that must survive translation intact ─────────────────────
#
# Order matters: more specific patterns first so they don't overlap with
# the generic xml_close catch-all.
_TAG_PATTERNS: List[Tuple[str, str]] = [
    # Bethesda/Starfield special tags — dot-notation covered: <Alias.Name=...>, <Token.ValueInt=...>
    (r"<Alias(?:[.=][^>]*)?>",           "alias"),
    (r"<TokenAlias(?:[.=][^>]*)?>",      "token_alias"),
    (r"<Token(?:[.=][^>]*)?>",           "token"),
    (r"<Global(?:=[^>]*)?>",             "global"),
    (r"<CurrentName>",                   "current_name"),
    # Numeric-prefix alias references: <0.Name>, <1.ValueInt>, <2.Title>, etc.
    (r"<\d+\.(?:Name|Title|ValueInt|PluralName|ShortName|Pronoun[A-Za-z]*)>",
                                         "num_alias"),
    # xTranslator rxPatternAliasStrict additions: magnitude/duration/relative/basename
    (r"<mag>",                           "mag"),
    (r"<dur>",                           "dur"),
    (r"<relat[^>]*>",                    "relat"),
    (r"<basename[^>]*>",                 "basename"),
    (r"<repetitions>",                   "repetitions"),
    (r"<area>",                          "area"),
    # HTML-like formatting — opening inline tags (bold/italic/underline)
    (r"<[biuBIU]>",                      "inline_fmt"),
    (r"<br\s*/?>",                       "br"),
    (r"<p(?:\s[^>]*)?>",                 "paragraph"),
    (r"<font[^>]*>",                     "font_open"),
    (r"<image[^>]*>",                    "image"),
    (r"</font>",                         "font_close"),
    # xml_close excludes </font> (already counted by font_close above)
    (r"</(?!font>)[A-Za-z][A-Za-z0-9]*>", "xml_close"),
    # Bethesda bracket tags: [MALE] [FEMALE] [M] [F] [N] etc. (* not + so single-char matches)
    (r"\[[A-Z][A-Za-z0-9_/]*\]",        "bracket_tag"),
    # xTranslator / toolkit tokens: [tk_Something]
    (r"\[tk_[A-Za-z0-9_]*\]",           "tk_tag"),
    # Printf format specifiers
    (r"%[sdfoxXceEgGpn%]",              "printf_var"),
    # Brace variables  {variable}
    (r"\{[^}]+\}",                       "brace_var"),
    # Escape sequences used as inline formatting
    (r"\\n",                             "escape_newline"),
    (r"\\t",                             "escape_tab"),
    (r'\\"',                             "escape_quote"),
]

_COMPILED_PATTERNS = [
    (re.compile(pat, re.IGNORECASE), _label) for pat, _label in _TAG_PATTERNS
]


def _extract_tags(text: str) -> Counter:
    """Return a Counter of all game tags found in *text*, case-normalised.

    Deduplicates by (start, end) span so a tag matched by multiple overlapping
    patterns (e.g. </font> by both font_close and xml_close) is counted once.
    """
    seen_spans: set[tuple[int, int]] = set()
    found = []
    for pat, _ in _COMPILED_PATTERNS:
        for m in pat.finditer(text):
            span = (m.start(), m.end())
            if span not in seen_spans:
                seen_spans.add(span)
                found.append(m.group(0).lower())
    return Counter(found)


# ── Russian-only characters (signal for source-language leakage) ───────────────
# ы/Ы included: most common Russian-leakage char; ё/Ё, э/Э, ъ/Ъ don't exist in Ukrainian
_RUSSIAN_ONLY = re.compile(r"[ёЁэЭъЪыЫ]")

# ── Ukrainian-specific characters (absent from Russian) ────────────────────────
_UKRAINIAN_SPECIFIC = re.compile(r"[іїєґІЇЄҐ]")

# ── Standalone numbers (2+ digits, not embedded in IDs/paths/tags) ─────────────
_STANDALONE_NUM_RE = re.compile(r"(?<![/#:\w])\d{2,}(?![/:\w])")

# ── URL / email detector ────────────────────────────────────────────────────────
_URL_RE = re.compile(
    r"https?://\S+"
    r"|www\.\S+"
    r"|(?<!\w)[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?!\w)"
)

# ── AI commentary artifact prefixes ────────────────────────────────────────────
_AI_ARTIFACT_RE = re.compile(
    r"^(?:"
    r"Note[:\s–—]|Примітка[:\s–—]|"
    r"Translation[:\s]|Translated[:\s]|Переклад[:\s]|Перевод[:\s]|"
    r"Ukrainian[:\s]|Українська[:\s]|"
    r"Here(?:'s| is) the translation[:\s]|"
    r"The translation(?:\s+is)?[:\s]|"
    r"Ось переклад[:\s]|"
    r"Ось мій переклад[:\s]"
    r")",
    re.IGNORECASE,
)

# Source-language labels that legitimately translate to Ukrainian note/translation labels.
# When the original starts with one of these, a translated label in the output is correct
# and must not be flagged as an AI artifact.
_SOURCE_LABEL_RE = re.compile(
    r"^(?:Note[:\s–—]|Примечание[:\s–—]|ПРИМЕЧАНИЕ|Перевод[:\s]|"
    r"Translation[:\s]|Translated[:\s])",
    re.IGNORECASE,
)

# ── Sentence-ending punctuation ─────────────────────────────────────────────────
_SENT_END_RE = re.compile(r"[.!?…]+")


# ── Repetition / hallucination detection ──────────────────────────────────────

def _find_repeated_ngram(text: str) -> Optional[str]:
    """Return a repeated n-gram if suspicious repetition is detected."""
    words = text.split()
    for n in (3, 4, 5):
        if len(words) < n * 3:
            continue
        seen: Counter = Counter()
        for i in range(len(words) - n + 1):
            gram = " ".join(words[i : i + n])
            seen[gram] += 1
        for gram, cnt in seen.items():
            if cnt >= 3:
                return gram
    return None


# ── Checker ────────────────────────────────────────────────────────────────────

class QualityChecker:
    """
    Validates translated Starfield strings for game compatibility.

    Instantiate once per session and call check_all() or check() per string.
    """

    def __init__(
        self,
        target_encoding: str = "utf-8",
        target_language: str = "Ukrainian",
        source_language: str = "Russian",
    ) -> None:
        self.target_encoding = target_encoding
        self.target_language = target_language
        self.source_language = source_language

    # ── Public API ─────────────────────────────────────────────────────────────

    def check(
        self,
        row_index: int,
        string_id: int,
        original: str,
        translated: str,
    ) -> QualityReport:
        """Check a single string pair and return a QualityReport."""
        report = QualityReport(
            row_index=row_index,
            string_id=string_id,
            original=original,
            translated=translated,
        )

        if not translated or not translated.strip():
            if original and original.strip():
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_ERROR,
                        code="EMPTY_TRANSLATION",
                        message="Translation is empty",
                    )
                )
            return report

        self._check_untranslated(original, translated, report)
        self._check_case_consistency(original, translated, report)
        self._check_tags(original, translated, report)
        self._check_numbers(original, translated, report)
        self._check_url_preservation(original, translated, report)
        self._check_length_ratio(original, translated, report)
        self._check_newlines(original, translated, report)
        self._check_truncation(original, translated, report)
        self._check_sentence_structure(original, translated, report)
        self._check_encoding(translated, report)
        self._check_source_leak(translated, report)
        self._check_ukrainian_coverage(translated, report)
        self._check_english_leak(original, translated, report)
        self._check_repetition(translated, report)
        self._check_ai_artifacts(original, translated, report)
        self._check_whitespace_frame(original, translated, report)
        self._check_spurious_quotes(original, translated, report)

        return report

    def check_all(
        self, rows: List[dict], encoding: Optional[str] = None
    ) -> List[QualityReport]:
        """
        Run checks on every row from StringTableModel._data.
        Returns only reports that have at least one issue.
        """
        if encoding:
            self.target_encoding = encoding

        reports = []
        for i, row in enumerate(rows):
            original = row.get("original", "")
            translated = row.get("translated", "")
            string_id = row.get("id", 0)
            report = self.check(i, string_id, original, translated)
            if report.has_issues:
                reports.append(report)
        return reports

    # ── Auto-fix ───────────────────────────────────────────────────────────────

    def auto_fix(
        self,
        original: str,
        translated: str,
        report: QualityReport,
    ) -> Tuple[str, List[str]]:
        """
        Mechanically repair fixable issues in *translated* identified by *report*.

        Returns (fixed_text, applied_fix_descriptions).
        """
        if not report.has_issues:
            return translated, []

        text = translated
        applied: List[str] = []
        codes = {issue.code for issue in report.issues}

        # Non-Cyrillic original with empty translation → copy original verbatim.
        # These are developer labels, English codes, etc. that don't need AI translation.
        if "EMPTY_TRANSLATION" in codes and not text:
            if not any("Ѐ" <= c <= "ӿ" for c in original):
                text = original
                applied.append("copied original (non-Cyrillic source)")

        if "AI_ARTIFACT" in codes:
            text, msg = self._fix_ai_artifact(text)
            if msg:
                applied.append(msg)

        if "REPETITIVE_CONTENT" in codes:
            text, msg = self._fix_repetitive_content(text)
            if msg:
                applied.append(msg)

        if "CASE_MISMATCH" in codes:
            text, msg = self._fix_case_mismatch(text)
            if msg:
                applied.append(msg)

        if "LEADING_WHITESPACE_REMOVED" in codes:
            text, msg = self._fix_leading_whitespace(original, text)
            if msg:
                applied.append(msg)

        if "TRAILING_WHITESPACE_MISMATCH" in codes:
            text, msg = self._fix_trailing_whitespace(original, text)
            if msg:
                applied.append(msg)

        if "MISSING_NEWLINES" in codes or "NEWLINE_COUNT_MISMATCH" in codes:
            text, msg = self._fix_newlines(original, text)
            if msg:
                applied.append(msg)
            elif "NEWLINE_COUNT_MISMATCH" in codes and original != text:
                # _fix_newlines found nothing to insert (e.g. spurious newlines in
                # the translation offset a truncated tail → counts appear equal).
                # If the source is already in the target language it IS the correct
                # translation — copy it verbatim.
                if _UKRAINIAN_SPECIFIC.search(original) and not _RUSSIAN_ONLY.search(original):
                    text = original
                    applied.append("restored original (source already in target language)")

        if "SPURIOUS_QUOTES" in codes:
            text, msg = self._fix_spurious_quotes(text)
            if msg:
                applied.append(msg)

        if "SOURCE_LANGUAGE_LEAK" in codes:
            text, msg = self._fix_russian_chars(text)
            if msg:
                applied.append(msg)

        if "MISSING_TAG" in codes:
            missing_issues = [i for i in report.issues if i.code == "MISSING_TAG"]
            text, msgs = self._fix_missing_tags(original, text, missing_issues)
            applied.extend(msgs)

        if "EXTRA_TAG" in codes:
            extra_issues = [i for i in report.issues if i.code == "EXTRA_TAG"]
            text, msgs = self._fix_extra_tags(original, text, extra_issues)
            applied.extend(msgs)

        if "ENCODING_ERROR" in codes:
            text, msg = self._fix_encoding(text)
            if msg:
                applied.append(msg)

        if "TRANSLATION_TRUNCATED" in codes:
            # Source is already in target language → the original IS the correct
            # translation; restore it verbatim.  For Russian originals only
            # retranslation can fix this; leave text unchanged here.
            if (
                text != original
                and _UKRAINIAN_SPECIFIC.search(original)
                and not _RUSSIAN_ONLY.search(original)
            ):
                text = original
                applied.append("restored original (truncated translation replaced with source)")

        return text, applied

    def fix_all(
        self,
        rows: List[dict],
        reports: Optional[List[QualityReport]] = None,
        encoding: Optional[str] = None,
    ) -> List[Tuple[int, str, List[str]]]:
        """
        Auto-fix all fixable issues across every row.

        If *reports* is None, runs check_all() first.  Returns a list of
        (row_index, fixed_translated_text, applied_fix_descriptions) for every
        row where at least one fix was applied; does NOT modify *rows* in-place.
        """
        if encoding:
            self.target_encoding = encoding
        if reports is None:
            reports = self.check_all(rows)

        report_map = {r.row_index: r for r in reports}

        results: List[Tuple[int, str, List[str]]] = []
        for i, row in enumerate(rows):
            if i not in report_map:
                continue
            original = row.get("original", "")
            translated = row.get("translated", "")
            fixed, applied = self.auto_fix(original, translated, report_map[i])
            if applied:
                results.append((i, fixed, applied))
        return results

    # ── Individual checks ──────────────────────────────────────────────────────

    # Compiled once at class level for reuse across all calls.
    _SKIP_TAGS_RE = re.compile(r'^(?:<[^>]*>|\[[^\]]*\]|\s)+')

    def _check_case_consistency(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Flag when source starts uppercase but translation starts lowercase."""
        orig = original.strip()
        trans = translated.strip()
        if not orig or not trans or len(orig) < 3:
            return
        orig_text = self._SKIP_TAGS_RE.sub("", orig)
        trans_text = self._SKIP_TAGS_RE.sub("", trans)
        if not orig_text or not trans_text:
            return
        if orig_text[0].isupper() and trans_text[0].islower():
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="CASE_MISMATCH",
                    message="Translation starts lowercase but source starts uppercase",
                )
            )

    def _check_tags(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        orig_tags = _extract_tags(original)
        trans_tags = _extract_tags(translated)

        # Tags in original missing from translation
        for tag, orig_count in orig_tags.items():
            trans_count = trans_tags.get(tag, 0)
            if trans_count < orig_count:
                short = orig_count - trans_count
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_ERROR,
                        code="MISSING_TAG",
                        message="Game tag missing from translation"
                        + (f" ({short}× short)" if short > 1 else ""),
                        detail=tag,
                    )
                )

        # Tags in translation not present in original
        for tag, trans_count in trans_tags.items():
            orig_count = orig_tags.get(tag, 0)
            if trans_count > orig_count:
                extra = trans_count - orig_count
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_ERROR,
                        code="EXTRA_TAG",
                        message="Extra game tag in translation not present in original"
                        + (f" ({extra}× extra)" if extra > 1 else ""),
                        detail=tag,
                    )
                )

    def _check_length_ratio(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        orig_len = len(original.strip())
        if orig_len == 0:
            return
        trans_len = len(translated.strip())
        ratio = trans_len / orig_len

        if ratio < 0.20:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="SUSPICIOUSLY_SHORT",
                    message=(
                        f"Translation is much shorter than original "
                        f"({trans_len} vs {orig_len} chars, {ratio:.2f}×)"
                    ),
                )
            )
        elif ratio > 5.0:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="SUSPICIOUSLY_LONG",
                    message=(
                        f"Translation is much longer than original "
                        f"({trans_len} vs {orig_len} chars, {ratio:.2f}×)"
                    ),
                )
            )
        elif ratio > 2.5:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="LENGTH_INCREASE",
                    message=(
                        f"Translation is notably longer than original "
                        f"({trans_len} vs {orig_len} chars, {ratio:.2f}×)"
                    ),
                )
            )

    def _check_newlines(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        # Count both escaped \\n and real newlines (they represent the same thing
        # in different contexts — game files use \\n, AI may output \n).
        orig_nl = original.count("\\n") + original.count("\n")
        trans_nl = translated.count("\\n") + translated.count("\n")
        if orig_nl > 0 and trans_nl == 0:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="MISSING_NEWLINES",
                    message=f"Original has {orig_nl} newline(s), translation has none",
                )
            )
        elif orig_nl != trans_nl:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="NEWLINE_COUNT_MISMATCH",
                    message=f"Newline count changed: {orig_nl} → {trans_nl}",
                )
            )

    @staticmethod
    def _normalize_for_truncation(s: str) -> str:
        """Collapse all whitespace and newline tokens into single spaces."""
        return re.sub(r'\s+', ' ', s.replace('\\n', ' ').replace('\n', ' ')).strip()

    def _check_truncation(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """
        Detect when the translation is a truncated prefix of the original.

        This happens when the AI stops generating mid-text — the result shares
        the start of the original but is cut off before the end.  Newline
        displacement (a misplaced \\n compensating for missing content) makes
        the count-based check blind to this case, so we do a content check.

        Only fires when:
          - translated is ≥25 chars (too-short strings are unreliable to test)
          - translated is at least 5% shorter than original (after normalization)
          - the full normalized translated text is a prefix of the normalized original
        """
        norm_orig  = self._normalize_for_truncation(original)
        norm_trans = self._normalize_for_truncation(translated)
        if len(norm_trans) < 25:
            return
        if len(norm_trans) >= len(norm_orig) * 0.95:
            return
        # Allow up to 3 trailing chars of drift (word-boundary or punctuation)
        prefix = norm_trans[: max(len(norm_trans) - 3, len(norm_trans) * 9 // 10)]
        if prefix and norm_orig.startswith(prefix):
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="TRANSLATION_TRUNCATED",
                    message=(
                        f"Translation appears truncated: "
                        f"{len(norm_trans)} of {len(norm_orig)} source chars present"
                    ),
                )
            )

    def _check_encoding(self, translated: str, report: QualityReport) -> None:
        enc = self.target_encoding
        if enc.replace("-", "").lower() in ("utf8", "utf16", "utf16le", "utf16be"):
            return  # Unicode encodings can represent all text
        try:
            translated.encode(enc)
        except (UnicodeEncodeError, LookupError) as exc:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_ERROR,
                    code="ENCODING_ERROR",
                    message=f"Characters cannot be encoded as {enc} (game will crash or show garbage)",
                    detail=str(exc)[:120],
                )
            )

    def _check_source_leak(self, translated: str, report: QualityReport) -> None:
        if self.target_language.lower() != "ukrainian":
            return

        # Pass 1: Russian-exclusive characters (ы э ё ъ).
        found = _RUSSIAN_ONLY.findall(translated)
        if found:
            count = len(found)
            # Avoid false positives from proper nouns or occasional model slippage.
            # Flag only if: unambiguously many chars (>=8), OR a notable fraction of the
            # text (>5%) combined with a minimum absolute count (>=3) to prevent short-string
            # noise (a single ё in a 20-char name = 5% but is not real leakage).
            ratio = count / max(len(translated), 1)
            if count >= 8 or (count >= 3 and ratio > 0.05):
                chars = "".join(sorted(set(found)))
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_WARNING,
                        code="SOURCE_LANGUAGE_LEAK",
                        message=(
                            f"Translation may contain untranslated Russian text "
                            f"(found Russian-only chars: {chars})"
                        ),
                    )
                )
                return  # char-level detection is definitive; skip dict check

        # Pass 2: Russian vocabulary — catches "cleaned" Russian where exclusive chars
        # were substituted (ы→и) but the words themselves are still Russian.
        # text_has_russian_words internally returns False when Ukrainian-specific chars
        # (і/ї/є/ґ) are present, so real Ukrainian translations never trigger this.
        try:
            from gui.ru_word_checker import text_has_russian_words
            if text_has_russian_words(translated, threshold=5):
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_WARNING,
                        code="SOURCE_LANGUAGE_LEAK",
                        message=(
                            "Translation appears to contain Russian vocabulary "
                            "(possibly incompletely translated)"
                        ),
                    )
                )
        except ImportError:
            pass

    def _check_ukrainian_coverage(
        self, translated: str, report: QualityReport
    ) -> None:
        """
        Warn when the Ukrainian translation has low Ukrainian dictionary coverage.

        Uses the Ukrainian word list to count how many meaningful Cyrillic words
        are recognized as Ukrainian. Coverage below 25% with ≥8 sample words
        suggests the text is not properly translated (echoed source, wrong language,
        or heavily corrupted output).

        Skips proper nouns (capitalised), words shorter than 4 chars, non-Cyrillic
        tokens, and words containing Russian-exclusive chars (already caught by
        _check_source_leak). Only fires when the dictionary is available.
        """
        if self.target_language.lower() != "ukrainian":
            return

        try:
            from gui.uk_word_checker import word_is_ukrainian, dict_loaded
        except ImportError:
            return
        if not dict_loaded():
            return

        _ru_only_chars = frozenset("ыэёъЫЭЁЪ")
        cyrillic_words: list = []
        for token in translated.split():
            raw = token.strip(".,!?-:;«»\"'()[]{}…—–")
            if not raw or len(raw) < 4:
                continue
            if not any("Ѐ" <= c <= "ӿ" for c in raw):
                continue  # not Cyrillic
            if raw[0].isupper():
                continue  # proper noun — skip
            if any(c in _ru_only_chars for c in raw):
                continue  # already flagged by _check_source_leak
            cyrillic_words.append(raw)

        if len(cyrillic_words) < 8:
            return  # sample too small for reliable coverage estimate

        uk_count = sum(1 for w in cyrillic_words if word_is_ukrainian(w) is True)
        coverage = uk_count / len(cyrillic_words)

        if coverage < 0.25:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="LOW_UKRAINIAN_COVERAGE",
                    message=(
                        f"Low Ukrainian vocabulary coverage "
                        f"({uk_count}/{len(cyrillic_words)} words recognized, "
                        f"{coverage:.0%}) — may be untranslated or wrong language"
                    ),
                )
            )

    def _check_english_leak(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """
        Detect untranslated English words remaining in a Ukrainian translation
        when the source text was English.

        Skips: proper nouns (capitalised), all-uppercase tokens (game codes /
        acronyms), function words, tokens shorter than 4 characters, and any
        English words that were already present in the original (game terms,
        untranslatable brand names, etc.).
        Fires only when the English dictionary is loaded and source is English.
        """
        if self.source_language.lower() != "english":
            return
        if self.target_language.lower() != "ukrainian":
            return

        try:
            from gui.en_word_checker import word_is_english, dict_loaded, EN_FUNCTION_WORDS
        except ImportError:
            return
        if not dict_loaded():
            return

        # First pass: collect English words present in the original.
        # These may legitimately remain in the translation (game terms, brand names).
        original_en: set = set()
        for token in original.split():
            raw = token.strip(".,!?-:;«»\"'()[]{}…—–")
            if raw and raw.isascii() and raw.replace("-", "").isalpha() and len(raw) >= 4:
                original_en.add(raw.lower())

        # Second pass: flag English words in the translation that weren't in the original.
        hits = []
        for token in translated.split():
            raw = token.strip(".,!?-:;«»\"'()[]{}…—–")
            if not raw or not raw.isascii() or not raw.replace("-", "").isalpha():
                continue
            if len(raw) < 4 or raw[0].isupper() or raw.isupper():
                continue
            if raw.lower() in EN_FUNCTION_WORDS:
                continue
            if raw.lower() in original_en:
                continue  # word was in original — may legitimately stay
            if word_is_english(raw):
                hits.append(raw.lower())

        if len(hits) >= 2:  # require at least 2 new English words to reduce noise
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="ENGLISH_LEAK",
                    message="Translation may contain untranslated English words",
                    detail=", ".join(dict.fromkeys(hits[:6])),
                )
            )

    def _check_repetition(self, translated: str, report: QualityReport) -> None:
        gram = _find_repeated_ngram(translated)
        if gram:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="REPETITIVE_CONTENT",
                    message="Translation contains repeated phrases (possible AI hallucination)",
                    detail=f'Repeated: "{gram[:60]}"',
                )
            )

    def _check_whitespace_frame(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        orig_lead = len(original) - len(original.lstrip(" \t"))
        trans_lead = len(translated) - len(translated.lstrip(" \t"))
        if orig_lead > 0 and trans_lead != orig_lead:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="LEADING_WHITESPACE_REMOVED",
                    message=(
                        f"Leading whitespace changed: {orig_lead} → {trans_lead} space(s)"
                    ),
                )
            )

        orig_trail = len(original) - len(original.rstrip(" \t"))
        trans_trail = len(translated) - len(translated.rstrip(" \t"))
        if orig_trail > 0 and trans_trail != orig_trail:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="TRAILING_WHITESPACE_MISMATCH",
                    message=(
                        f"Trailing whitespace changed: {orig_trail} → {trans_trail} space(s)"
                    ),
                )
            )

    # Matches any standard quotation character (not apostrophe — used as ь in Ukrainian)
    _QUOTE_CHARS_RE = re.compile(r'[«»""„‟''"]')
    # Guillemets wrapping a word or phrase, including extra inner whitespace
    _INLINE_GUILLEMET_RE = re.compile(r'«\s*([^»\n]+?)\s*»')

    def _check_spurious_quotes(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Flag guillemets in the translation when the original has no quotes.

        Ukrainian AI models often wrap game proper nouns in «» as per Ukrainian
        typography rules, but game UI strings must never gain quotes the source
        lacked (e.g. "Спейсеров" → "«Спейсерів»" is wrong).
        """
        if self._QUOTE_CHARS_RE.search(original):
            return  # original already has quotes — translated quotes may be intentional
        if self._INLINE_GUILLEMET_RE.search(translated):
            count = len(self._INLINE_GUILLEMET_RE.findall(translated))
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="SPURIOUS_QUOTES",
                    message=f"Translation contains {count} guillemet pair(s) not present in original",
                )
            )

    @staticmethod
    def _fix_spurious_quotes(translated: str) -> Tuple[str, str]:
        """Remove guillemets added around words not quoted in the original."""
        _re = re.compile(r'«\s*([^»\n]+?)\s*»')
        fixed, count = _re.subn(r"\1", translated)
        if count:
            return fixed, f"removed {count} spurious guillemet pair(s)"
        return translated, ""

    # ── Auto-fix helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _fix_ai_artifact(translated: str) -> Tuple[str, str]:
        """Strip AI commentary prefix (e.g. 'Note: ', 'Translation: ') from output."""
        stripped = translated.strip()
        m = _AI_ARTIFACT_RE.match(stripped)
        if not m:
            return translated, ""
        remainder = stripped[m.end():].lstrip(": \t\n\r")
        if not remainder:
            return translated, ""
        return remainder, "stripped AI commentary prefix"

    @staticmethod
    def _fix_repetitive_content(translated: str) -> Tuple[str, str]:
        """Truncate at the start of the second occurrence of a repeated phrase."""
        gram = _find_repeated_ngram(translated)
        if not gram:
            return translated, ""
        first = translated.find(gram)
        if first == -1:
            return translated, ""
        second = translated.find(gram, first + len(gram))
        if second == -1:
            return translated, ""
        truncated = translated[:second].rstrip(" \t,;:")
        if len(truncated) < 10:
            return translated, ""
        return truncated, "truncated repetitive content"

    def _fix_case_mismatch(self, translated: str) -> Tuple[str, str]:
        """Capitalize the first real character of *translated* (skip leading tags/spaces)."""
        m = self._SKIP_TAGS_RE.match(translated)
        prefix = translated[: m.end()] if m else ""
        rest = translated[len(prefix):]
        if not rest:
            return translated, ""
        fixed = prefix + rest[0].upper() + rest[1:]
        if fixed == translated:
            return translated, ""
        return fixed, "capitalized first letter to match source"

    @staticmethod
    def _fix_leading_whitespace(original: str, translated: str) -> Tuple[str, str]:
        orig_lead = len(original) - len(original.lstrip(" \t"))
        trans_lead = len(translated) - len(translated.lstrip(" \t"))
        if orig_lead == trans_lead:
            return translated, ""
        # Strip whatever leading whitespace translation has, then prepend original's
        trimmed = translated.lstrip(" \t")
        prefix = original[:orig_lead]
        msg = (
            f"restored {orig_lead - trans_lead} leading space(s)"
            if orig_lead > trans_lead
            else f"removed {trans_lead - orig_lead} extra leading space(s)"
        )
        return prefix + trimmed, msg

    @staticmethod
    def _fix_trailing_whitespace(original: str, translated: str) -> Tuple[str, str]:
        orig_trail = len(original) - len(original.rstrip(" \t"))
        trans_trail = len(translated) - len(translated.rstrip(" \t"))
        if orig_trail == trans_trail:
            return translated, ""
        # Strip translation's trailing whitespace, then append original's exactly
        trimmed = translated.rstrip(" \t")
        suffix = original[len(original) - orig_trail :] if orig_trail else ""
        msg = (
            f"restored {orig_trail - trans_trail} trailing space(s)"
            if orig_trail > trans_trail
            else f"removed {trans_trail - orig_trail} extra trailing space(s)"
        )
        return trimmed + suffix, msg

    @staticmethod
    def _fix_newlines(original: str, translated: str) -> Tuple[str, str]:
        """
        Restore newlines missing from the translation.

        Handles two cases:
        - MISSING_NEWLINES: translation has zero newlines → insert all proportionally.
        - NEWLINE_COUNT_MISMATCH: translation has some but fewer than original →
          insert the missing ones proportionally, skipping positions already covered.
        """
        nl_pat = re.compile(r"\\n|\n")
        markers = [(m.start(), m.group()) for m in nl_pat.finditer(original)]
        if not markers:
            return translated, ""

        trans_nl_count = len(nl_pat.findall(translated))
        orig_nl_count = len(markers)
        if trans_nl_count >= orig_nl_count:
            return translated, ""  # already has enough

        orig_len = len(original)
        trans_len = len(translated)
        if orig_len == 0 or trans_len == 0:
            return translated, ""

        # Positions already covered by newlines in the translation (±10 char window).
        existing_positions = {m.start() for m in nl_pat.finditer(translated)}

        insertions: List[Tuple[int, str]] = []
        for orig_pos, token in markers:
            pos = min(int(orig_pos / orig_len * trans_len), trans_len)
            # Snap forward to the end of the current word
            while pos < trans_len and translated[pos] not in (" ", "\t", ".", ",", "!", "?", "\n"):
                pos += 1
            # Skip positions already near an existing newline (count-mismatch case)
            if any(abs(pos - ep) <= 10 for ep in existing_positions):
                continue
            insertions.append((pos, token))

        if not insertions:
            return translated, ""

        result = translated
        for pos, token in sorted(insertions, key=lambda x: x[0], reverse=True):
            result = result[:pos] + token + result[pos:]

        added = len(insertions)
        needed = orig_nl_count - trans_nl_count
        suffix = f" ({added} of {needed} gaps found)" if added < needed else ""
        return result, f"restored {added} missing newline(s){suffix}"

    @staticmethod
    def _fix_russian_chars(text: str) -> Tuple[str, str]:
        """Apply character-level Russian→Ukrainian substitutions."""
        _UK_VOWELS = "аеиоуєіїюяАЕИОУЄІЇЮЯ"
        original = text
        # Position-aware ё: йо at word-start or after vowel, ьо after consonant
        text = re.sub(r"\bё", "йо", text)
        text = re.sub(r"\bЁ", "Йо", text)
        text = re.sub(f"(?<=[{_UK_VOWELS}])ё", "йо", text)
        text = re.sub(f"(?<=[{_UK_VOWELS}])Ё", "Йо", text)
        text = text.replace("ё", "ьо").replace("Ё", "Ьо")
        for ru, uk in [("ы", "и"), ("Ы", "И"), ("э", "е"), ("Э", "Е"), ("ъ", ""), ("Ъ", "")]:
            if ru in text:
                text = text.replace(ru, uk)
        return text, "fixed Russian character leakage (ы/э/ё/ъ)" if text != original else ""

    @staticmethod
    def _fix_missing_tags(
        original: str, translated: str, issues: List[QualityIssue]
    ) -> Tuple[str, List[str]]:
        """Append tags present in the original but absent from the translation."""
        msgs: List[str] = []
        text = translated
        orig_lower = original.lower()
        for issue in issues:
            tag = issue.detail  # already lowercase from _extract_tags
            if not tag:
                continue
            orig_count = orig_lower.count(tag)
            trans_count = text.lower().count(tag)
            missing = orig_count - trans_count
            if missing <= 0:
                continue
            text = text.rstrip() + " " + " ".join([tag] * missing)
            msgs.append(f"appended missing tag {tag!r} ({missing}×)")
        return text, msgs

    @staticmethod
    def _fix_extra_tags(
        original: str, translated: str, issues: List[QualityIssue]
    ) -> Tuple[str, List[str]]:
        """Remove tag occurrences that exceed the original count."""
        msgs: List[str] = []
        text = translated
        orig_lower = original.lower()
        for issue in issues:
            tag = issue.detail  # already lowercase from _extract_tags
            if not tag:
                continue
            orig_count = orig_lower.count(tag)
            extra = text.lower().count(tag) - orig_count
            if extra <= 0:
                continue
            for _ in range(extra):
                pos = text.lower().rfind(tag)
                if pos == -1:
                    break
                end = pos + len(tag)
                # Remove adjacent space to avoid double-spaces
                if pos > 0 and text[pos - 1] == " " and end < len(text) and text[end] == " ":
                    text = text[:pos] + text[end + 1:]
                else:
                    text = text[:pos] + text[end:]
            msgs.append(f"removed {extra} extra occurrence(s) of tag {tag!r}")
        return text, msgs

    def _check_untranslated(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Flag strings where the translation is identical to the source text."""
        if not original or not translated:
            return
        orig_s = original.strip()
        trans_s = translated.strip()
        if orig_s != trans_s:
            return
        # Only flag when there is meaningful alphabetic content to translate.
        clean = re.sub(r"[^\w]", "", orig_s)
        if len(clean) < 4 or not any(c.isalpha() for c in clean):
            return
        # English game terms (no Cyrillic in source) are correctly left unchanged.
        if not any("Ѐ" <= c <= "ӿ" for c in orig_s):
            return
        report.issues.append(
            QualityIssue(
                severity=SEVERITY_ERROR,
                code="UNTRANSLATED",
                message="Translation is identical to the original — text was not translated",
            )
        )

    def _check_numbers(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Warn when standalone numbers from the original are absent in the translation."""
        orig_nums = Counter(_STANDALONE_NUM_RE.findall(original))
        trans_nums = Counter(_STANDALONE_NUM_RE.findall(translated))
        for num, count in orig_nums.items():
            if trans_nums.get(num, 0) < count:
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_WARNING,
                        code="MISSING_NUMBER",
                        message=f"Number '{num}' from original is not preserved in translation",
                        detail=num,
                    )
                )

    def _check_url_preservation(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Error when URLs or email addresses from the original are dropped."""
        orig_urls = set(_URL_RE.findall(original))
        if not orig_urls:
            return
        trans_urls = set(_URL_RE.findall(translated))
        for url in orig_urls:
            if url not in trans_urls:
                report.issues.append(
                    QualityIssue(
                        severity=SEVERITY_ERROR,
                        code="MISSING_URL",
                        message="URL or email address from original is missing in translation",
                        detail=url[:80],
                    )
                )

    def _check_ai_artifacts(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Warn when the translation starts with an AI commentary label."""
        if _AI_ARTIFACT_RE.match(translated.strip()):
            # Skip when the source also starts with a label — the translated label
            # is a correct translation of the original heading, not AI commentary.
            if _SOURCE_LABEL_RE.match(original.strip()):
                return
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="AI_ARTIFACT",
                    message="Translation begins with an AI commentary label rather than the actual translation",
                    detail=translated.strip()[:80],
                )
            )

    def _check_sentence_structure(
        self, original: str, translated: str, report: QualityReport
    ) -> None:
        """Info when sentence count changes significantly (possible truncation or expansion)."""
        orig_sents = len(_SENT_END_RE.findall(original))
        trans_sents = len(_SENT_END_RE.findall(translated))
        if orig_sents < 3:
            return  # Not meaningful for very short strings
        ratio = trans_sents / max(orig_sents, 1)
        if ratio < 0.4 or ratio > 2.5:
            report.issues.append(
                QualityIssue(
                    severity=SEVERITY_INFO,
                    code="SENTENCE_COUNT_MISMATCH",
                    message=(
                        f"Sentence count changed significantly: "
                        f"{orig_sents} → {trans_sents}"
                    ),
                )
            )

    # ── Retranslation support ──────────────────────────────────────────────────

    @staticmethod
    def issue_needs_retranslation(code: str) -> bool:
        """Return True if this issue code requires AI retranslation to fix."""
        return code in RETRANSLATE_CODES

    @staticmethod
    def issue_can_autofix(code: str) -> bool:
        """Return True if this issue code can be fixed mechanically without AI."""
        return code in AUTOFIX_CODES

    @staticmethod
    def build_retry_hint(issues: List[QualityIssue]) -> str:
        """
        Build a feedback prompt snippet that explains what was wrong in the
        previous translation, to guide a retranslation attempt.
        """
        hints: List[str] = []

        # Collect tag-related issues together for a single structured error block.
        missing_tags = [i.detail for i in issues if i.code == "MISSING_TAG" and i.detail]
        extra_tags   = [i.detail for i in issues if i.code == "EXTRA_TAG"   and i.detail]
        if missing_tags or extra_tags:
            parts: List[str] = ["[SYSTEM ERROR REPORT]",
                                 "Previous Translation Failed Integrity Check."]
            if missing_tags:
                parts.append("Missing Tags (must appear in output): " + ", ".join(missing_tags))
            if extra_tags:
                parts.append("Extra Tags (must NOT appear in output): " + ", ".join(extra_tags))
            total = len(missing_tags) + len(extra_tags)
            parts.append(f"TAG MISMATCH DETECTED ({total} issue{'s' if total > 1 else ''})")
            parts.append(
                "Your Task: Translate again, preserving EVERY tag from the original exactly. "
                "Do NOT add tags that are not in the source. "
                "Do NOT omit tags that are in the source."
            )
            hints.append("\n".join(parts))

        for issue in issues:
            code = issue.code
            if code in ("MISSING_TAG", "EXTRA_TAG"):
                continue  # handled above
            elif code == "REPETITIVE_CONTENT":
                hints.append(
                    "Your previous translation repeated phrases multiple times. "
                    "Translate concisely — no repeated words or phrases."
                )
            elif code == "SOURCE_LANGUAGE_LEAK":
                hints.append(
                    "Your previous translation contained Russian-only characters "
                    "(ы, э, ё, ъ). Use ONLY Ukrainian characters."
                )
            elif code == "SUSPICIOUSLY_SHORT":
                hints.append(
                    "Your previous translation was far too short. "
                    "Provide a COMPLETE, full-length translation of the entire source text."
                )
            elif code == "SUSPICIOUSLY_LONG":
                hints.append(
                    "Your previous translation was excessively long. "
                    "Be concise and match the scope of the original."
                )
            elif code == "EMPTY_TRANSLATION":
                hints.append(
                    "Your previous attempt produced an empty result. "
                    "You MUST provide a non-empty translation."
                )
            elif code == "UNTRANSLATED":
                hints.append(
                    "Your previous attempt returned the source text unchanged. "
                    "You MUST translate the text — do not return the original."
                )
            elif code == "AI_ARTIFACT":
                hints.append(
                    "Your previous output started with a label like 'Translation:' "
                    "or 'Ukrainian:'. Output ONLY the translated text — no labels or commentary."
                )
            elif code == "ENGLISH_LEAK":
                detail = f" ({issue.detail})" if issue.detail else ""
                hints.append(
                    f"Your previous translation left English words untranslated{detail}. "
                    "Translate ALL words fully."
                )
            elif code == "MISSING_NUMBER":
                hints.append(
                    f"Your previous translation omitted the number '{issue.detail}'. "
                    "Preserve all numbers exactly as they appear in the original."
                )
            elif code == "MISSING_URL":
                hints.append(
                    f"Your previous translation dropped a URL or email address. "
                    f"Preserve it exactly: {issue.detail}"
                )
            elif code == "GLOSSARY_MISMATCH":
                hints.append(issue.detail if issue.detail else issue.message)
            elif code == "NEWLINE_COUNT_MISMATCH":
                hints.append(
                    "Preserve the same number of line breaks (\\n) as the original."
                )
            elif code == "SENTENCE_COUNT_MISMATCH":
                hints.append(
                    "Your previous translation changed the number of sentences significantly. "
                    "Match the sentence structure of the original."
                )

        if not hints:
            return ""
        return (
            "\n\nRetranslation feedback — previous attempt had issues:\n"
            + "\n".join(f"• {h}" for h in hints)
        )

    def check_glossary_compliance(
        self,
        source: str,
        translation: str,
        glossary_manager: Any,  # GlossaryManager — avoid circular import
    ) -> List[QualityIssue]:
        """Return GLOSSARY_MISMATCH issues for prescribed terms absent from the translation."""
        if not translation or glossary_manager is None:
            return []
        issues: List[QualityIssue] = []
        for entry, _ in glossary_manager.validate_translation(source, translation):
            issues.append(
                QualityIssue(
                    severity=SEVERITY_WARNING,
                    code="GLOSSARY_MISMATCH",
                    message=f'"{entry.source_term}" should be "{entry.target_term}"',
                    detail=(
                        f"Glossary prescribes «{entry.target_term}» "
                        f"for «{entry.source_term}», but this was not found in the translation."
                    ),
                )
            )
        return issues

    def _fix_encoding(self, text: str) -> Tuple[str, str]:
        """Drop characters that cannot be encoded in the target encoding."""
        enc = self.target_encoding
        if enc.replace("-", "").lower() in ("utf8", "utf16", "utf16le", "utf16be"):
            return text, ""
        fixed = text.encode(enc, errors="ignore").decode(enc)
        if fixed != text:
            return fixed, f"removed unencodable characters for {enc}"
        return text, ""
