"""
Pre-translation quality estimator for Bethesda/Starfield game strings.

Predicts how difficult a source string will be for the AI to translate
correctly. Returns a ComplexityReport (score 0-100, issues list) that is
displayed in the string table before any AI call is made.

Learning: when the user manually edits an AI translation, call
record_correction() with the source text. Feature weights are nudged upward
for patterns that correlate with manual corrections, so similar strings score
lower (harder) in future sessions. Weights are persisted as JSON.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from gui.quality_checker import SEVERITY_ERROR, SEVERITY_INFO, SEVERITY_WARNING, QualityIssue

logger = logging.getLogger(__name__)


# ── False friends ─────────────────────────────────────────────────────────────
# English words whose Ukrainian cognates carry different meanings; AI models
# trained predominantly on English-Ukrainian parallel corpora may conflate them.

_EN_UK_FALSE_FRIENDS: Dict[str, str] = {
    "accurate": "акуратний = neat/tidy (not accurate)",
    "fabric": "фабрика = factory (not fabric/cloth)",
    "magazine": "магазин = store (not a periodical)",
    "sympathetic": "симпатичний = attractive (not empathetic)",
    "pathetic": "патетичний = theatrical/passionate (not pitiful)",
    "baton": "батон = loaf of bread (not a stick)",
    "cabinet": "кабінет = office/study (not a cupboard)",
    "decade": "декада = 10-day period (not 10 years)",
    "eventual": "евентуальний = possible/contingent (not final)",
    "actual": "актуальний = current/relevant (not factual)",
    "intelligent": "інтелігентний = cultured/educated (not smart)",
    "argument": "аргумент = evidence/reasoning (not a quarrel)",
    "figure": "фігура = shape/form (not a number)",
    "pretend": "претендувати = to claim/aspire (not to fake)",
    "control": "контроль = oversight/checking (not to operate)",
    "realize": "реалізувати = to implement/sell (not to become aware)",
    "novel": "новела = short story/novella (not a full novel)",
    "lecture": "лекція = university lecture; нотація = scolding",
    "original": "оригінальний = unusual/eccentric (not the source copy)",
}

# Russian words whose Ukrainian cognates differ; used for RU→UK source analysis.
_RU_UK_FALSE_FRIENDS: Dict[str, str] = {
    "неділя": "RU: week → UK: Sunday",
    "луна": "RU: moon → UK: echo",
    "уродливый": "RU: beautiful → UK: ugly (уродливий)",
    "черствый": "RU: stale (bread) → UK: callous/heartless",
    "неловко": "RU: awkward → UK: inappropriate (незручно = uncomfortable)",
    "позор": "RU: shame → UK: display/show (ганьба = shame in UK)",
    "живот": "RU: belly → archaic UK: life",
}


# ── Compiled patterns ─────────────────────────────────────────────────────────

# All game-format tokens (tags, printf vars, escape sequences)
_TAG_RE = re.compile(
    r"<[A-Za-z][^>]*>"          # opening tags: <Alias=…>, <font …>
    r"|</[A-Za-z][^>]*>"        # closing tags
    r"|\[[A-Z][A-Za-z0-9_/]+\]" # bracket tags: [ATTACK] [OPTIMIZED] [DataMenu]
    r"|%[sdfoxXceEgGpn%]"       # printf specifiers
    r"|\{[^}]+\}"               # brace variables
    r"|\\[nt\"]",               # escape sequences
    re.IGNORECASE,
)


# Multiple printf vars that may change order in translation
_PRINTF_RE = re.compile(r"%[sdfoxXceEgGpn]")

# Common English idiomatic / figurative phrases that resist literal translation
_IDIOM_PATTERNS = [
    r"\bbeat around the bush\b",
    r"\bbreak a leg\b",
    r"\bcold turkey\b",
    r"\bcost an arm and a leg\b",
    r"\bhit the nail on the head\b",
    r"\bkick the bucket\b",
    r"\blet the cat out of the bag\b",
    r"\bonce in a blue moon\b",
    r"\bspill the beans\b",
    r"\bunder the weather\b",
    r"\bup in the air\b",
    r"\bbite the bullet\b",
    r"\bdrop the ball\b",
    r"\bget cold feet\b",
    r"\bjump on the bandwagon\b",
    r"\bkill two birds with one stone\b",
    r"\bpull someone.{0,5}s leg\b",
    r"\bthrow in the towel\b",
    r"\bwhen pigs fly\b",
    r"\bthe ball is in your court\b",
    r"\bover the moon\b",
    r"\bnot my cup of tea\b",
    r"\bon the fence\b",
    r"\bcut corners\b",
    r"\bburn (your |their |the )?bridges\b",
]
_IDIOM_RE = re.compile("|".join(_IDIOM_PATTERNS), re.IGNORECASE)

# Ambiguous English subject/object pronouns in short strings without context
_PRONOUN_RE = re.compile(r"\b(he|she|it|they|him|her|them|his|their|its)\b", re.IGNORECASE)

# Characters from non-Latin, non-Cyrillic script blocks (Arabic, CJK, etc.)
_UNUSUAL_SCRIPT_RE = re.compile(
    r"[^\x00-\x7FЀ-ӿ -⁯℀-⅏\s\d\W]"
)

# Five or more identical consecutive characters (likely formatting artifact)
_REPEATED_CHAR_RE = re.compile(r"(.)\1{4,}")

# C0/C1 control characters that should never appear in game strings
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ── Default weights ────────────────────────────────────────────────────────────

_DEFAULT_WEIGHTS: Dict[str, float] = {
    "length_chars":       1.0,
    "tag_density":        1.0,
    "multi_printf":       1.0,
    "false_friends":      1.0,
    "idioms":             1.0,
    "ambiguous_pronoun":  1.0,
    "mixed_script":       1.0,
    "weird_format":       1.0,
    "multi_sentence":     1.0,
    "conditional_blocks": 1.0,
}

_CONDITIONAL_TAG_RE = re.compile(r'\[(?:MALE|FEMALE|PLYR|PC|NPC)\]', re.IGNORECASE)


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ComplexityReport:
    """Pre-translation complexity estimate for one source string."""

    score: int                               # 0-100  (100 = trivially easy)
    issues: List[QualityIssue] = field(default_factory=list)
    suggest_review: bool = False

    @property
    def level(self) -> str:
        """Easy / medium / hard tier."""
        if self.score >= 80:
            return "easy"
        if self.score >= 60:
            return "medium"
        return "hard"

    @property
    def status_icon(self) -> str:
        """Single icon used in the Status column for pending rows."""
        if self.score >= 80:
            return "○"   # open circle — easy
        if self.score >= 60:
            return "◑"   # half-filled — medium
        return "●"       # filled — hard / flagged for review


# ── Estimator ─────────────────────────────────────────────────────────────────

class PreTranslationEstimator:
    """
    Estimates source-string complexity before AI translation.

    Thread-safety: estimate() is read-only and safe to call from multiple
    threads. record_correction() mutates weights and must only be called from
    the main thread (or under external locking).
    """

    _CORRECTION_THRESHOLD = 5   # corrections needed before a weight is bumped
    _MAX_WEIGHT = 3.0
    _MIN_WEIGHT = 0.3
    _LEARNING_RATE = 0.15       # EMA-style step toward MAX_WEIGHT

    def __init__(
        self,
        source_lang: str = "English",
        weights_path: Optional[Path] = None,
    ) -> None:
        self.source_lang = source_lang.lower()
        self._weights_path = weights_path
        self._weights: Dict[str, float] = dict(_DEFAULT_WEIGHTS)
        self._correction_counts: Dict[str, int] = {k: 0 for k in _DEFAULT_WEIGHTS}
        if weights_path and weights_path.exists():
            self._load_weights()

    # ── Public API ─────────────────────────────────────────────────────────────

    def estimate(self, text: str, source_lang: Optional[str] = None) -> ComplexityReport:
        """Return a ComplexityReport for *text*. Never raises."""
        try:
            return self._estimate_inner(text, source_lang)
        except Exception as exc:
            logger.debug("Pre-estimation error (ignored): %s", exc)
            return ComplexityReport(score=75)

    def record_correction(self, text: str, source_lang: Optional[str] = None) -> None:
        """
        Signal that the AI translation of *text* was manually corrected by the
        user. Increments per-feature correction counters; when a feature's
        counter reaches _CORRECTION_THRESHOLD its weight is nudged upward so
        future strings exhibiting the same feature score lower.
        """
        lang = (source_lang or self.source_lang).lower()
        stripped = (text or "").strip()
        if not stripped:
            return

        active = self._active_features(stripped, lang)
        bumped: List[str] = []
        for feature, is_active in active.items():
            if not is_active:
                continue
            self._correction_counts[feature] = self._correction_counts.get(feature, 0) + 1
            if self._correction_counts[feature] >= self._CORRECTION_THRESHOLD:
                old = self._weights[feature]
                self._weights[feature] = min(
                    self._MAX_WEIGHT,
                    old + self._LEARNING_RATE * (self._MAX_WEIGHT - old),
                )
                self._correction_counts[feature] = 0
                bumped.append(f"{feature}: {old:.2f}→{self._weights[feature]:.2f}")

        if bumped:
            logger.info("Estimator weights updated: %s", "; ".join(bumped))
            self._save_weights()

    def get_weights(self) -> Dict[str, float]:
        return dict(self._weights)

    def reset_weights(self) -> None:
        """Restore factory defaults and clear correction counts."""
        self._weights = dict(_DEFAULT_WEIGHTS)
        self._correction_counts = {k: 0 for k in _DEFAULT_WEIGHTS}
        self._save_weights()

    # ── Core estimation ────────────────────────────────────────────────────────

    def _estimate_inner(self, text: str, source_lang: Optional[str]) -> ComplexityReport:
        lang = (source_lang or self.source_lang).lower()
        stripped = (text or "").strip()

        if not stripped:
            return ComplexityReport(score=100)
        # Single words and short codes are trivial
        if len(stripped) <= 4:
            return ComplexityReport(score=98)

        issues: List[QualityIssue] = []
        penalty: float = 0.0

        def _add(feature: str, p: float, issue: Optional[QualityIssue]) -> None:
            nonlocal penalty
            if p:
                penalty += p * self._weights[feature]
            if issue:
                issues.append(issue)

        _add("length_chars",       *self._check_length(stripped))
        _add("tag_density",        *self._check_tag_density(stripped))
        _add("multi_printf",       *self._check_multi_printf(stripped))

        ff_penalty, ff_found = self._check_false_friends(stripped, lang)
        _add("false_friends", ff_penalty,
             QualityIssue(
                 severity=SEVERITY_WARNING,
                 code="FALSE_FRIENDS",
                 message="Contains potential false-friend terms",
                 detail=", ".join(ff_found[:4]),
             ) if ff_found else None)

        if lang == "english":
            _add("idioms", *self._check_idioms(stripped))

        _add("ambiguous_pronoun",  *self._check_pronoun_ambiguity(stripped, lang))
        _add("mixed_script",       *self._check_mixed_script(stripped, lang))
        _add("weird_format",       *self._check_weird_format(stripped))
        _add("multi_sentence",     *self._check_multi_sentence(stripped))
        _add("conditional_blocks", *self._check_conditional_blocks(stripped))

        score = max(0, min(100, 100 - int(penalty)))
        suggest = score < 60 or any(i.severity == SEVERITY_ERROR for i in issues)
        return ComplexityReport(score=score, issues=issues, suggest_review=suggest)

    # ── Feature active-set (used by record_correction) ─────────────────────────

    def _active_features(self, text: str, lang: str) -> Dict[str, bool]:
        return {
            "length_chars":       len(text) > 200,
            "tag_density":        self._tag_density_ratio(text) > 0.25,
            "multi_printf":       len(_PRINTF_RE.findall(text)) >= 2,
            "false_friends":      bool(self._check_false_friends(text, lang)[1]),
            "idioms":             lang == "english" and bool(_IDIOM_RE.search(text)),
            "ambiguous_pronoun":  self._has_pronoun_ambiguity(text, lang),
            "mixed_script":       bool(
                _UNUSUAL_SCRIPT_RE.search(text) or _CONTROL_RE.search(text)
                or (lang == "english"
                    and re.search(r"[Ѐ-ӿ]", text)
                    and re.search(r"[A-Za-z]", text))
            ),
            "weird_format":        bool(_REPEATED_CHAR_RE.search(text)),
            "multi_sentence":      self._sentence_count(text) > 2,
            "conditional_blocks":  len(_CONDITIONAL_TAG_RE.findall(text)) >= 2,
        }

    # ── Individual checks ──────────────────────────────────────────────────────

    @staticmethod
    def _check_length(text: str) -> Tuple[float, Optional[QualityIssue]]:
        n = len(text)
        if n <= 80:
            return 0, None
        if n <= 200:
            return 5, None
        if n <= 500:
            return 12, QualityIssue(
                severity=SEVERITY_INFO,
                code="LONG_SOURCE",
                message=f"Long source text ({n} chars) — AI may drift near the end",
            )
        return 22, QualityIssue(
            severity=SEVERITY_WARNING,
            code="VERY_LONG_SOURCE",
            message=f"Very long source text ({n} chars) — AI may truncate or lose tags",
        )

    @staticmethod
    def _tag_density_ratio(text: str) -> float:
        tag_chars = sum(len(m.group()) for m in _TAG_RE.finditer(text))
        return tag_chars / max(len(text), 1)

    def _check_tag_density(self, text: str) -> Tuple[float, Optional[QualityIssue]]:
        ratio = self._tag_density_ratio(text)
        count = len(_TAG_RE.findall(text))
        if count == 0:
            return 0, None
        if ratio < 0.20 and count <= 3:
            return 0, None
        if ratio < 0.35 and count <= 6:
            return 8, QualityIssue(
                severity=SEVERITY_INFO,
                code="MODERATE_TAG_DENSITY",
                message=f"Moderate tag density ({count} tags, {ratio:.0%}) — verify tags survive",
            )
        return 18, QualityIssue(
            severity=SEVERITY_WARNING,
            code="HIGH_TAG_DENSITY",
            message=f"High tag density ({count} tags, {ratio:.0%}) — AI likely to drop or corrupt tags",
        )

    @staticmethod
    def _check_multi_printf(text: str) -> Tuple[float, Optional[QualityIssue]]:
        vars_ = _PRINTF_RE.findall(text)
        if len(vars_) < 2:
            return 0, None
        return 10, QualityIssue(
            severity=SEVERITY_WARNING,
            code="MULTIPLE_FORMAT_VARS",
            message=f"Multiple format variables ({len(vars_)}) — AI may reorder or drop them",
        )

    @staticmethod
    def _check_false_friends(text: str, lang: str) -> Tuple[float, List[str]]:
        lower = text.lower()
        ff = _EN_UK_FALSE_FRIENDS if lang in ("english", "en") else (
            _RU_UK_FALSE_FRIENDS if lang in ("russian", "ru") else {}
        )
        found = [w for w in ff if re.search(r"\b" + re.escape(w) + r"\b", lower)]
        if not found:
            return 0, []
        return min(15.0, len(found) * 5.0), found

    @staticmethod
    def _check_idioms(text: str) -> Tuple[float, Optional[QualityIssue]]:
        m = _IDIOM_RE.search(text)
        if not m:
            return 0, None
        return 15, QualityIssue(
            severity=SEVERITY_WARNING,
            code="IDIOMATIC_EXPRESSION",
            message=f'Contains English idiom: "{m.group(0)}" -- literal translation will be wrong',
        )

    @staticmethod
    def _has_pronoun_ambiguity(text: str, lang: str) -> bool:
        if lang not in ("english", "en"):
            return False
        words = text.split()
        if len(words) < 2 or len(words) > 15:
            return False
        pronouns = _PRONOUN_RE.findall(text)
        if not pronouns:
            return False
        # Only flag if there are no capitalised proper nouns to anchor the pronoun
        anchors = [w for w in words if w[0].isupper() and len(w) > 2 and not w.isupper()]
        return not anchors

    def _check_pronoun_ambiguity(self, text: str, lang: str) -> Tuple[float, Optional[QualityIssue]]:
        if not self._has_pronoun_ambiguity(text, lang):
            return 0, None
        pronouns = list(dict.fromkeys(p.lower() for p in _PRONOUN_RE.findall(text)))
        return 8, QualityIssue(
            severity=SEVERITY_INFO,
            code="AMBIGUOUS_PRONOUN",
            message=f"Short string uses pronoun(s) {pronouns} without a named antecedent — "
                    "AI may guess the wrong gender form",
        )

    @staticmethod
    def _check_mixed_script(text: str, lang: str) -> Tuple[float, Optional[QualityIssue]]:
        has_latin = bool(re.search(r"[A-Za-z]", text))
        has_cyrillic = bool(re.search(r"[Ѐ-ӿ]", text))

        ctrl = _CONTROL_RE.search(text)
        if ctrl:
            return 15, QualityIssue(
                severity=SEVERITY_WARNING,
                code="CONTROL_CHARS",
                message=f"Contains control character U+{ord(ctrl.group()):04X} — "
                        "AI may silently drop it",
            )

        if lang in ("english", "en") and has_cyrillic and has_latin:
            return 10, QualityIssue(
                severity=SEVERITY_INFO,
                code="MIXED_SCRIPT",
                message="English source contains Cyrillic — verify intentional (brand name, proper noun?)",
            )

        if _UNUSUAL_SCRIPT_RE.search(text):
            return 12, QualityIssue(
                severity=SEVERITY_INFO,
                code="UNUSUAL_SCRIPT",
                message="Contains characters from an unusual Unicode block",
            )
        return 0, None

    @staticmethod
    def _check_weird_format(text: str) -> Tuple[float, Optional[QualityIssue]]:
        m = _REPEATED_CHAR_RE.search(text)
        if m:
            return 10, QualityIssue(
                severity=SEVERITY_INFO,
                code="REPEATED_CHARS",
                message=f'Contains 5+ identical consecutive characters: "{m.group(0)[:12]}"',
            )
        return 0, None

    @staticmethod
    def _sentence_count(text: str) -> int:
        return max(1, len(re.findall(r"[.!?]+(?:\s|$)", text)))

    def _check_multi_sentence(self, text: str) -> Tuple[float, Optional[QualityIssue]]:
        n = self._sentence_count(text)
        if n <= 2:
            return 0, None
        if n <= 4:
            return 5, QualityIssue(
                severity=SEVERITY_INFO,
                code="MULTI_SENTENCE",
                message=f"Multi-sentence string ({n} sentences) — more surface area for errors",
            )
        return 12, QualityIssue(
            severity=SEVERITY_WARNING,
            code="MANY_SENTENCES",
            message=f"Long multi-sentence string ({n} sentences)",
        )

    @staticmethod
    def _check_conditional_blocks(text: str) -> Tuple[float, Optional[QualityIssue]]:
        """Detect Bethesda gender/player conditional tags ([MALE]/[FEMALE]/[PLYR] etc.)."""
        matches = _CONDITIONAL_TAG_RE.findall(text)
        if len(matches) < 2:
            return 0, None
        return 12, QualityIssue(
            severity=SEVERITY_WARNING,
            code="CONDITIONAL_BLOCKS",
            message=f"Contains gender/player conditional tags ({', '.join(dict.fromkeys(matches))}) — both branches must be translated",
        )

    # ── Weight persistence ─────────────────────────────────────────────────────

    def _load_weights(self) -> None:
        path = self._weights_path
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for k in _DEFAULT_WEIGHTS:
                if k in data.get("weights", {}):
                    v = float(data["weights"][k])
                    self._weights[k] = max(self._MIN_WEIGHT, min(self._MAX_WEIGHT, v))
                if k in data.get("correction_counts", {}):
                    self._correction_counts[k] = int(data["correction_counts"][k])
            logger.debug("Loaded estimator weights from %s", self._weights_path)
        except Exception as exc:
            logger.warning("Could not load estimator weights (%s), using defaults", exc)

    def _save_weights(self) -> None:
        path = self._weights_path
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "weights": self._weights,
                        "correction_counts": self._correction_counts,
                    },
                    f,
                    indent=2,
                )
            tmp.replace(path)
        except Exception as exc:
            logger.warning("Could not save estimator weights: %s", exc)
