"""
Term Protector v3 — Highly optimized for Starfield Ukrainian localization
Uses combined regex patterns for massive performance gains with 8000+ terms.
"""

import hashlib
import logging
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Categories that represent game-world proper nouns and lore terms.
# By default these are NOT protected — the AI is expected to translate or
# handle faction/company/ship/character names appropriately.  Users can
# opt in via Settings → "Protect proper nouns and lore terms".
SOFT_CATEGORIES: frozenset = frozenset({
    "faction", "company", "ship", "character", "location",
    "creature", "resource", "system", "ui", "game_term",
    "company_prefix", "company_full", "location_suffix",
    "lore",  # lore terms are proper nouns — translate unless user opts in to protect them
})


@dataclass
class ProtectedTerm:
    """Represents a protected term that should not be translated."""

    term: str
    category: str = "custom"
    case_sensitive: bool = True
    # If True, enforce word boundaries (\b) around the term
    word_boundary: bool = True


@dataclass
class TermMatch:
    """A single match of a protected term in text."""

    start: int
    end: int
    original_text: str
    token: str
    category: str


class TermProtector:
    """
    Position-aware term protection with overlap resolution.
    Optimized for speed with 8000+ terms by using combined regex patterns.
    """

    # Maximum number of (text, exclude_set) → spans entries to keep.
    # Cleared entirely on each recompile so entries never go stale.
    _SPAN_CACHE_MAXSIZE = 512

    # ── Structural patterns that must ALWAYS be protected ──
    STRUCTURAL_PATTERNS = [
        # Only protect single-word ASCII bracket tokens (game codes like [Attack], [OPTIMIZED], [DataMenu]).
        # Cyrillic content ([Ложь], [Соврать]) is left unprotected so the AI translates it.
        # Bracket spans that contain spaces (prose sentences or dialogue choices) are also left
        # unprotected so the AI translates the English text inside them.
        (r"\[[^\]\sЀ-ӿ]+\]", "bracket_id"),
        (r"\b[0-9A-Fa-f]{8}\b", "form_id"),
        (r"\{[^}]*\}", "brace_var"),
        (r"%[-+0#]*\d*(?:\.\d+)?[sdfoxXciuFeEgGp%]", "printf_var"),
        (r"</?[a-zA-Z][^>]*/?>", "xml_tag"),
        (r"</?alias[^>]*>", "alias_tag"),
        (
            r"<(?:Alias|Token|TokenAlias|Global|CurrentName|relat|basename|repetitions)[^>]*>",
            "bethesda_placeholder",
        ),
        # xTranslator rxPatternAliasStrict: magnitude/duration placeholders
        (r"<mag>", "mag_tag"),
        (r"<dur>", "dur_tag"),
        (r"<\d+\.[A-Za-z]+>", "numeric_placeholder"),
        (r"\\n", "newline"),
        (r"\\t", "tab"),
        (r'\\"', "escaped_quote"),
        (
            r"(?<=[\s>])([A-Za-zА-Яа-яЄєІіЇїҐґ]{1,5})/([A-Za-zА-Яа-яЄєІіЇїҐґ]{1,5})(?=[\s<])",
            "unit_abbrev",
        ),
        (r"/([A-Za-zА-Яа-яЄєІіЇїҐґ])\b", "slash_unit"),
        (r"\b(?=[A-Za-z]*\d)[A-Z][a-z]?(?:\d*[A-Z][a-z]?)*\d*\b", "chemical_formula"),
        (r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9*]*)+(?=\s|$|"|<|\[)', "asset_id"),
        (
            r"\b[А-ЯЁа-яёЄєІіЇїҐґ]+(?:\s+[А-ЯЁа-яёЄєІіЇїҐґ]+)*\s+[IVXLCDM]+(?:[-–—][a-zA-Zа-яА-ЯЁёЄєІіЇїҐґ]+)?\b",
            "star_system_name",
        ),
    ]

    # ── Contextual patterns for auto-detecting proper nouns ──
    CONTEXT_PATTERNS = [
        (
            r"\b([A-Z][a-zA-Z]*)(?= (?:Employee|Worker|Staff|Dialogue|Corp|Inc|Ltd|LLC|Company))",
            "company_prefix",
        ),
        (
            r"\b([A-Z][a-zA-Z]*(?: [A-Z][a-zA-Z]*)*) (?:Industries|Technologies|Corporation)\b",
            "company_full",
        ),
        (
            r"\b([A-Z][a-zA-Z]*(?: [A-Z][a-zA-Z]*)*) (?:City|Station|Port|Yard|Depot|Outpost)\b",
            "location_suffix",
        ),
    ]

    # ── Default protected terms (Starfield-specific) ──
    DEFAULT_PROTECTED_TERMS = [
        ("United Colonies", "faction"),
        ("Freestar Collective", "faction"),
        ("House Va'ruun", "faction"),
        ("Freestar Rangers", "faction"),
        ("UC Vanguard", "faction"),
        ("UC SysDef", "faction"),
        ("UC Security", "faction"),
        ("Ecliptic", "faction"),
        ("Constellation", "faction"),
        ("SysDef", "faction"),
        ("UC", "faction"),
        ("Ryujin Industries", "company"),
        ("Hopetech", "company"),
        ("Stroud-Eklund", "company"),
        ("GalBank", "company"),
        ("Taiyo", "company"),
        ("Terrabrew", "company"),
        ("Chunks", "company"),
        ("Deimos", "company"),
        ("Masako", "company"),
        ("Generdyne", "company"),
        ("New Atlantis", "location"),
        ("Akila City", "location"),
        ("Cydonia", "location"),
        ("The Lodge", "location"),
        ("The Key", "location"),
        ("The Well", "location"),
        ("Neon", "location"),
        ("Jemison", "location"),
        ("Vectera", "location"),
        ("Porrima", "location"),
        ("Andraphon", "location"),
        ("Paradiso", "location"),
        ("Gagarin", "location"),
        ("Mars", "location"),
        ("Earth", "location"),
        ("Procyon", "location"),
        ("Narion", "location"),
        ("Charybdis", "location"),
        ("ComSpike", "company"),
        ("Sarah Morgan", "character"),
        ("Sam Coe", "character"),
        ("Walter Stroud", "character"),
        ("Barrett", "character"),
        ("Andreja", "character"),
        ("Vladimir", "character"),
        ("Noel", "character"),
        ("Delgado", "character"),
        ("Imogene", "character"),
        ("Cora", "character"),
        ("Heller", "character"),
        ("Akechi", "character"),
        ("Starborn Guardian", "ship"),
        ("Frontier", "ship"),
        ("Razorleaf", "ship"),
        ("Aurora", "ship"),
        ("New Game Plus", "system"),
        ("Unity", "system"),
        ("Terrormorph", "creature"),
        ("Terrormorphs", "creature"),
        ("Ashta", "creature"),
        ("Helium-3", "resource"),
        ("Aluminum", "resource"),
        ("Titanium", "resource"),
        ("Tungsten", "resource"),
        ("Cobalt", "resource"),
        ("Zirconium", "resource"),
        ("Neodymium", "resource"),
        ("Europium", "resource"),
        ("Ruthenium", "resource"),
        ("Yttrium", "resource"),
        ("Beryllium", "resource"),
        ("Vanadium", "resource"),
        ("Rhodium", "resource"),
        ("Iridium", "resource"),
        ("Palladium", "resource"),
        ("Osmium", "resource"),
        ("HUD", "ui"),
        ("GPS", "ui"),
        ("NPC", "ui"),
        ("CEO", "ui"),
        ("LTD", "ui"),
        ("VIP", "ui"),
        ("VFX", "ui"),
        ("SFX", "ui"),
        ("FX", "ui"),
    ]

    NEVER_PROTECT = frozenset(
        (
            "a",
            "an",
            "the",
            "and",
            "or",
            "but",
            "not",
            "no",
            "so",
            "yet",
            "for",
            "of",
            "in",
            "on",
            "at",
            "to",
            "by",
            "from",
            "with",
            "about",
            "i",
            "me",
            "my",
            "you",
            "your",
            "he",
            "him",
            "his",
            "she",
            "her",
            "it",
            "its",
            "we",
            "us",
            "our",
            "they",
            "them",
            "their",
            "is",
            "am",
            "are",
            "was",
            "were",
            "be",
            "been",
            "being",
            "have",
            "has",
            "had",
            "do",
            "does",
            "did",
            "will",
            "would",
            "shall",
            "should",
            "may",
            "might",
            "must",
            "can",
            "could",
            "this",
            "that",
            "these",
            "those",
            "what",
            "which",
            "who",
            "whom",
            "if",
            "then",
            "than",
            "too",
            "very",
            "just",
            "also",
            "even",
            "only",
            "still",
            "already",
            "always",
            "never",
            "often",
            "now",
            "here",
            "there",
            "when",
            "where",
            "why",
            "how",
            "all",
            "each",
            "every",
            "both",
            "few",
            "more",
            "most",
            "other",
            "some",
            "such",
            "any",
            "same",
            "well",
            "back",
            "again",
            "up",
            "down",
            "out",
            "off",
            "over",
            "under",
            "into",
            "through",
            "new",
            "old",
            "big",
            "small",
            "good",
            "bad",
            "right",
            "left",
            "first",
            "last",
            "next",
            "final",
            "main",
            "key",
            "one",
            "two",
            "get",
            "got",
            "go",
            "went",
            "come",
            "came",
            "make",
            "made",
            "take",
            "took",
            "give",
            "gave",
            "know",
            "knew",
            "think",
            "thought",
            "say",
            "said",
            "see",
            "saw",
            "look",
            "looked",
            "want",
            "wanted",
            "use",
            "used",
            "try",
            "tried",
            "find",
            "found",
            "tell",
            "told",
            "ask",
            "asked",
            "work",
            "worked",
            "call",
            "called",
            "need",
            "needed",
            "like",
            "help",
        )
    )

    def __init__(
        self,
        protected_terms: Optional[List[ProtectedTerm]] = None,
        structural_patterns: Optional[List[Tuple[str, str]]] = None,
        context_patterns: Optional[List[Tuple[str, str]]] = None,
        game_terms_file: Optional[Path] = None,
        custom_terms_file: Optional[Path] = None,
    ):
        self.protected_terms: Dict[str, ProtectedTerm] = {}
        self.ci_term_map: Dict[str, str] = {}  # lowercase term -> original category
        self._lock = threading.Lock()
        self._combined_regex_cs: Optional[re.Pattern] = None
        self._combined_regex_ci: Optional[re.Pattern] = None
        # threading.Event instead of a plain bool:
        #   set()   → dirty (needs recompile)
        #   clear() → clean (compiled and ready)
        # Event.set()/clear() go through an internal Condition lock, so writes are
        # visible across threads even under free-threading CPython (PEP 703).
        self._dirty = threading.Event()
        self._dirty.set()  # initially dirty
        # Counts how many times the inner double-check saved a redundant recompile.
        self._contention_count = 0

        # ── Batch-update support ──────────────────────────────────────────────
        # When _update_depth > 0 (inside batch_update()), add_term / remove_term
        # set _batch_dirtied instead of _dirty so recompilation is deferred to
        # the context-manager exit.
        self._update_depth: int = 0
        self._batch_dirtied: bool = False

        # ── Content-hash cache ────────────────────────────────────────────────
        # Frozen fingerprint of the term set that was last compiled.
        # If _dirty fires but the fingerprint hasn't changed (e.g. add + remove
        # of the same term), we skip the expensive re.compile() call.
        self._compiled_term_key: Optional[frozenset] = None

        # Monotonically incremented on each successful recompile.
        # Not used as a cache key here — the span cache is simply cleared on
        # recompile, which is cheaper than per-entry generation checks.
        self._compile_generation: int = 0

        # ── Compilation profiling ─────────────────────────────────────────────
        self._compile_count: int = 0       # total recompiles performed
        self._compile_total_ns: int = 0    # cumulative time spent in re.compile()

        # ── Span cache ────────────────────────────────────────────────────────
        # Maps (text, exclude_frozenset) → tuple of (start, end, text, category)
        # spans.  Cleared on every recompile so it never holds stale results.
        # Uses plain dict with FIFO eviction (Python 3.7+ dicts are ordered).
        self._span_cache: Dict[tuple, tuple] = {}
        self._span_cache_hits: int = 0
        self._span_cache_misses: int = 0

        # Compile patterns
        self.structural_patterns: List[Tuple[re.Pattern, str]] = []
        self.context_patterns: List[Tuple[re.Pattern, str]] = []

        for pattern, category in structural_patterns or self.STRUCTURAL_PATTERNS:
            try:
                self.structural_patterns.append((re.compile(pattern), category))
            except re.error as e:
                logger.error(f"Invalid structural pattern '{pattern}': {e}")

        for pattern, category in context_patterns or self.CONTEXT_PATTERNS:
            try:
                self.context_patterns.append((re.compile(pattern), category))
            except re.error as e:
                logger.error(f"Invalid context pattern '{pattern}': {e}")

        # Load terms
        for term, category in self.DEFAULT_PROTECTED_TERMS:
            self.add_term(term, category, case_sensitive=True)
        if protected_terms:
            for t in protected_terms:
                self.add_protected_term(t)
        if game_terms_file and game_terms_file.exists():
            self._load_game_terms(game_terms_file)
        if custom_terms_file and custom_terms_file.exists():
            self.load_custom_terms(custom_terms_file)

    @staticmethod
    def _make_token(term_text: str, occurrence: int) -> str:
        """Deterministic token: [[TK_{md5[:6]}_{occurrence}]].

        The same term always produces the same token prefix across retries,
        so restored text is stable regardless of global call order.
        """
        h = hashlib.md5(term_text.encode("utf-8")).hexdigest()[:6]
        return f"[[TK_{h}_{occurrence}]]"

    # ── Batch-update context manager ─────────────────────────────────────────

    @contextmanager
    def batch_update(self) -> Generator[None, None, None]:
        """Defer regex recompilation until the end of a bulk term operation.

        Usage::

            with term_protector.batch_update():
                for term in large_list:
                    term_protector.add_term(term, "game_term")
            # exactly one recompile happens here, not N

        Calls can be nested: recompilation is deferred until the outermost
        ``batch_update`` exits.  Thread-safe: each thread tracks the depth
        independently via the shared ``_update_depth`` counter under
        ``_lock``.
        """
        with self._lock:
            self._update_depth += 1
        try:
            yield
        finally:
            should_dirty = False
            with self._lock:
                self._update_depth -= 1
                if self._update_depth == 0 and self._batch_dirtied:
                    self._batch_dirtied = False
                    should_dirty = True
            if should_dirty:
                # Set outside the lock: Event.set() acquires its own Condition
                # lock, and we must not hold two locks simultaneously.
                self._dirty.set()

    # ── Content-hash helper ───────────────────────────────────────────────────

    def _make_term_key(self) -> frozenset:
        """Return a frozen fingerprint of the current term set.

        Only the fields that affect the compiled regex pattern are included
        (term text, case_sensitive, word_boundary).  Category is intentionally
        excluded because it plays no role in the regex.

        Called under ``_lock`` inside ``_recompile_if_needed()``.
        """
        return frozenset(
            (pt.term, pt.case_sensitive, pt.word_boundary)
            for pt in self.protected_terms.values()
        )

    def _recompile_if_needed(self) -> None:
        """Recompile combined regex patterns if any terms have been added/removed.

        Optimisations layered on top of the double-check pattern:

        1. Content-hash check — if ``_dirty`` is set but the term fingerprint
           matches the last compiled state (e.g. add + immediate remove of the
           same term), we clear the flag and return without calling
           ``re.compile()``.

        2. Length-descending sort — terms are ordered longest-first in the
           alternation so the regex engine always commits to the longest
           possible match.  This fixes a correctness issue with overlapping
           terms (``"UC"`` vs ``"UC Vanguard"``) and slightly reduces
           backtracking.

        3. Span cache cleared — the cached match spans from the previous
           compiled state are discarded so ``_find_spans()`` always reflects
           the new patterns.

        4. Compile timing — elapsed nanoseconds are accumulated in
           ``_compile_total_ns`` and exposed via ``get_statistics()``.
        """
        # Fast path: no recompile needed — skip lock acquisition entirely.
        if not self._dirty.is_set():
            return

        with self._lock:
            # Inner check: another thread may have recompiled while we waited.
            if not self._dirty.is_set():
                self._contention_count += 1
                logger.debug(
                    "Thread %s: recompile already done by a concurrent thread "
                    "(total contention events: %d)",
                    threading.current_thread().name,
                    self._contention_count,
                )
                return

            # Content-hash check: skip compilation if the term set is unchanged.
            current_key = self._make_term_key()
            if current_key == self._compiled_term_key:
                self._dirty.clear()
                logger.debug(
                    "Thread %s: term set unchanged — skipped recompile "
                    "(dirty was set by a net-zero add/remove cycle)",
                    threading.current_thread().name,
                )
                return

            # Build pattern lists, longest term first so alternation is greedy
            # in the right direction (e.g. "UC Vanguard" before "UC").
            cs_terms: list[str] = []
            ci_terms: list[str] = []
            for pt in sorted(
                self.protected_terms.values(), key=lambda p: len(p.term), reverse=True
            ):
                pattern = re.escape(pt.term)
                if pt.word_boundary:
                    pattern = r"\b" + pattern + r"\b"
                if pt.case_sensitive:
                    cs_terms.append(pattern)
                else:
                    ci_terms.append(pattern)

            t_start = time.perf_counter_ns()

            self._combined_regex_cs = (
                re.compile("|".join(cs_terms)) if cs_terms else None
            )
            self._combined_regex_ci = (
                re.compile("|".join(ci_terms), re.IGNORECASE) if ci_terms else None
            )

            elapsed_ns = time.perf_counter_ns() - t_start
            self._compile_count += 1
            self._compile_total_ns += elapsed_ns
            self._compile_generation += 1
            self._compiled_term_key = current_key

            # Invalidate span cache: entries built against the old patterns are stale.
            self._span_cache.clear()

            # Clear *after* regexes and cache are updated so any thread that
            # subsequently calls protect_text sees a fully consistent state.
            self._dirty.clear()
            logger.debug(
                "Thread %s: recompiled — %d CS + %d CI terms in %.1f ms "
                "(compile #%d, avg %.1f ms)",
                threading.current_thread().name,
                len(cs_terms),
                len(ci_terms),
                elapsed_ns / 1_000_000,
                self._compile_count,
                (self._compile_total_ns / self._compile_count) / 1_000_000,
            )

    def _mark_dirty(self) -> None:
        """Set the dirty flag or accumulate it for the active batch_update().

        Must be called while holding ``self._lock``.
        """
        if self._update_depth > 0:
            self._batch_dirtied = True
        else:
            self._dirty.set()

    def add_term(
        self, term: str, category: str = "custom", case_sensitive: bool = True
    ):
        if term.lower() in self.NEVER_PROTECT:
            return
        with self._lock:
            self.protected_terms[term] = ProtectedTerm(
                term=term, category=category, case_sensitive=case_sensitive
            )
            if not case_sensitive:
                self.ci_term_map[term.lower()] = category
            self._mark_dirty()

    def add_protected_term(self, pt: ProtectedTerm):
        if pt.term.lower() in self.NEVER_PROTECT:
            return
        with self._lock:
            self.protected_terms[pt.term] = pt
            if not pt.case_sensitive:
                self.ci_term_map[pt.term.lower()] = pt.category
            self._mark_dirty()

    def remove_term(self, term: str):
        with self._lock:
            if term in self.protected_terms:
                pt = self.protected_terms[term]
                if not pt.case_sensitive:
                    lower_term = term.lower()
                    if lower_term in self.ci_term_map:
                        del self.ci_term_map[lower_term]
                del self.protected_terms[term]
                self._mark_dirty()

    def _load_game_terms(self, file_path: Path):
        count = 0
        try:
            with self.batch_update():
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        term = line.strip()
                        if term and not term.startswith("#") and len(term) >= 3:
                            self.add_term(term, "game_term", case_sensitive=False)
                            count += 1
            logger.info(f"Loaded {count} game terms from {file_path}")
        except Exception as e:
            logger.error(f"Failed to load game terms from {file_path}: {e}")

    def load_custom_terms(self, file_path: Path):
        count = 0
        try:
            with self.batch_update():
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(",", 1)
                        if len(parts) >= 2:
                            self.add_term(
                                parts[0].strip(), parts[1].strip(), case_sensitive=True
                            )
                            count += 1
            logger.info(f"Loaded {count} custom terms from {file_path}")
        except Exception as e:
            logger.error(f"Failed to load custom terms from {file_path}: {e}")

    def _find_spans(
        self,
        text: str,
        exclude_frozen: frozenset,
        regex_cs: Optional[re.Pattern],
        regex_ci: Optional[re.Pattern],
    ) -> tuple:
        """Return a cached tuple of ``(start, end, original_text, category)`` spans.

        The result describes *where* to place tokens but contains no tokens
        themselves — tokens are assigned by ``protect_text()`` after this call.
        This separation lets us cache the expensive regex-scanning work while
        keeping every ``protect_text()`` call's tokens globally unique.

        The cache is keyed by ``(text, exclude_frozen)`` and is cleared entirely
        on each recompile, so entries never reflect a stale compiled state.
        """
        cache_key = (text, exclude_frozen)
        cached = self._span_cache.get(cache_key)
        if cached is not None:
            self._span_cache_hits += 1
            return cached

        self._span_cache_misses += 1

        spans: list[tuple[int, int, str, str]] = []

        # 1. Structural patterns
        for pattern, category in self.structural_patterns:
            if category in exclude_frozen:
                continue
            for m in pattern.finditer(text):
                spans.append((m.start(), m.end(), m.group(0), category))

        # 2. Context patterns
        for pattern, category in self.context_patterns:
            if category in exclude_frozen:
                continue
            for m in pattern.finditer(text):
                spans.append((m.start(), m.end(), m.group(0), category))

        # 3. CS terms
        if regex_cs:
            for m in regex_cs.finditer(text):
                term_text = m.group(0)
                pt = self.protected_terms.get(term_text)
                category = pt.category if pt else "protected"
                if category not in exclude_frozen:
                    spans.append((m.start(), m.end(), term_text, category))

        # 4. CI terms
        if regex_ci:
            for m in regex_ci.finditer(text):
                term_text = m.group(0)
                category = self.ci_term_map.get(term_text.lower(), "protected")
                if category not in exclude_frozen:
                    spans.append((m.start(), m.end(), term_text, category))

        result = tuple(spans)

        # FIFO eviction: remove the oldest entry when capacity is reached.
        if len(self._span_cache) >= self._SPAN_CACHE_MAXSIZE:
            self._span_cache.pop(next(iter(self._span_cache)))
        self._span_cache[cache_key] = result
        return result

    def protect_text(
        self, text: str, exclude_categories: Optional[List[str]] = None
    ) -> Tuple[str, Dict[str, str]]:
        if not text:
            return text, {}

        self._recompile_if_needed()
        # Snapshot regex references together for a consistent pair (one
        # compilation run).  re.Pattern is immutable once compiled, so holding
        # an older snapshot is safe if a recompile happens concurrently.
        regex_cs = self._combined_regex_cs
        regex_ci = self._combined_regex_ci

        exclude_frozen = frozenset(exclude_categories) if exclude_categories else frozenset()

        # Retrieve (or compute and cache) the match spans for this text.
        spans = self._find_spans(text, exclude_frozen, regex_cs, regex_ci)

        # Assign deterministic tokens keyed on the term text + occurrence index.
        # The same term always gets the same token prefix, making retranslation
        # results identical in structure (no counter drift between calls).
        occurrence_counter: Dict[str, int] = {}
        matches: List[TermMatch] = []
        for start, end, original, category in spans:
            h = hashlib.md5(original.encode("utf-8")).hexdigest()[:6]
            n = occurrence_counter.get(h, 0)
            occurrence_counter[h] = n + 1
            matches.append(TermMatch(start, end, original, self._make_token(original, n), category))

        # Phase 2: Resolve overlaps (sort already done by _find_spans order,
        # but we sort here to guarantee correctness after spans are converted).
        matches.sort(key=lambda m: (m.start, -(m.end - m.start)))
        resolved: List[TermMatch] = []
        last_end = -1
        for m in matches:
            if m.start >= last_end:
                resolved.append(m)
                last_end = m.end

        # Phase 3: Replace right-to-left so earlier offsets stay valid.
        token_map: Dict[str, str] = {}
        result = text
        for m in reversed(resolved):
            result = result[: m.start] + m.token + result[m.end :]
            token_map[m.token] = m.original_text

        return result, token_map

    def _merge_whitespace(self, original: str, translated: str) -> str:
        """
        Preserves original's prefix/suffix whitespace while taking translated's core content.
        If translated content is missing, falls back to original content.
        """
        if not original:
            return translated

        # Extract leading and trailing whitespace from original
        pre_match = re.match(r"^(\s*)", original)
        pre_ws = pre_match.group(1) if pre_match else ""

        post_match = re.search(r"(\s*)$", original)
        post_ws = post_match.group(1) if post_match else ""

        original_content = original.strip()
        translated_content = translated.strip()

        # If original is only whitespace, just return it
        if not original_content:
            return original

        # If translation is empty, preserve only surrounding whitespace.
        # Do NOT fall back to the source-language original — that injects English
        # text into Ukrainian output when the model drops a protected token and
        # the template's text slot has no translated content to fill it.
        if not translated_content:
            return pre_ws + post_ws

        return pre_ws + translated_content + post_ws

    def restore_text(
        self, text: str, token_map: Dict[str, str], protected_text: str = ""
    ) -> str:
        """
        Restore tokens to their original text using protected_text as a structural template.
        Guarantees exact whitespace and paragraph preservation using an anchor-based mapping.
        Handles mangled tokens, reordering, and clumping.
        """
        if not token_map and not protected_text:
            return text

        # 1. Normalize mangled tokens back to standard format
        normalized_translated = self._normalize_tokens(text, token_map)

        # Sort tokens by length descending to avoid partial matches
        all_token_ids = sorted(token_map.keys(), key=len, reverse=True)

        # If no template is provided, fall back to simple iterative replacement
        if not protected_text:
            result = normalized_translated
            for _ in range(10):  # Iterative pass for nested tokens
                changed = False
                for token in all_token_ids:
                    if token in result:
                        result = result.replace(token, token_map[token])
                        changed = True
                if not changed:
                    break
            return result

        # 2. Template Tokenization: Identify Markers (tokens and newlines) and Content Slots
        if all_token_ids:
            marker_pattern = re.compile(
                "(" + "|".join(re.escape(t) for t in all_token_ids) + r"|\n+)"
            )
        else:
            marker_pattern = re.compile(r"(\n+)")

        template_parts = []  # List of parts with metadata
        last_pos = 0
        marker_counts = {}

        for m in marker_pattern.finditer(protected_text):
            # Text slot before marker
            if m.start() > last_pos:
                template_parts.append(
                    {"type": "text", "content": protected_text[last_pos : m.start()]}
                )

            # The marker itself
            content = m.group(0)
            # Group all newline sequences under the same key to allow matching \n with \n\n
            marker_key = "\n+" if content.startswith("\n") else content

            marker_counts[marker_key] = marker_counts.get(marker_key, 0) + 1
            template_parts.append(
                {
                    "type": "marker",
                    "content": content,
                    "marker_key": marker_key,
                    "index": marker_counts[marker_key] - 1,
                }
            )
            last_pos = m.end()

        # Trailing text slot
        if last_pos < len(protected_text):
            template_parts.append(
                {"type": "text", "content": protected_text[last_pos:]}
            )

        # 3. Anchor Mapping: Find all occurrences of these markers in the translated text
        translated_markers = {}  # marker_key -> list of positions
        for m in marker_pattern.finditer(normalized_translated):
            content = m.group(0)
            marker_key = "\n+" if content.startswith("\n") else content
            if marker_key not in translated_markers:
                translated_markers[marker_key] = []
            translated_markers[marker_key].append({"start": m.start(), "end": m.end()})

        # If model dropped ALL tokens (none found in translation), skip the template
        # approach entirely.  The template would inject source-language text from
        # empty content slots via _merge_whitespace; simple replacement is cleaner:
        # tags come up missing → QC flags MISSING_TAG → user can retranslate.
        token_markers_found = any(
            mk in translated_markers
            for mk in translated_markers
            if not mk.startswith("\n")
        )
        if all_token_ids and not token_markers_found:
            result = normalized_translated
            for _ in range(10):
                changed = False
                for token in all_token_ids:
                    if token in result:
                        result = result.replace(token, token_map[token])
                        changed = True
                if not changed:
                    break
            return result

        # 4. Content Extraction and Layout Restoration
        final_parts = []
        used_indices = (
            set()
        )  # Track indices in normalized_translated to prevent duplication

        # Pre-mark all found markers in translated text as used
        for positions in translated_markers.values():
            for pos in positions:
                for j in range(pos["start"], pos["end"]):
                    used_indices.add(j)

        def get_anchor_pos(current_idx, direction):
            """Search for the nearest available anchor in the template that exists in translation."""
            curr = current_idx + direction
            while 0 <= curr < len(template_parts):
                part = template_parts[curr]
                if part["type"] == "marker":
                    m_list = translated_markers.get(part["marker_key"], [])
                    if part["index"] < len(m_list):
                        pos = m_list[part["index"]]
                        return pos["end"] if direction == -1 else pos["start"]
                curr += direction
            # If no anchor found, return boundary of the text
            return 0 if direction == -1 else len(normalized_translated)

        for i, part in enumerate(template_parts):
            if part["type"] == "marker":
                # Always use the original marker content from the template
                final_parts.append(part["content"])
            else:
                # Content Slot: extract translated text between flanking anchors
                start_pos = get_anchor_pos(i, -1)
                end_pos = get_anchor_pos(i, 1)

                # Handle cases where markers might have been reordered by the LLM
                if start_pos > end_pos:
                    start_pos, end_pos = end_pos, start_pos

                # Extract any characters between anchors that haven't been used yet
                slot_content = []
                for idx in range(start_pos, end_pos):
                    if idx not in used_indices:
                        slot_content.append(normalized_translated[idx])
                        used_indices.add(idx)

                translated_segment = "".join(slot_content)

                # Merge template segment whitespace/fallback with extracted translation
                final_parts.append(
                    self._merge_whitespace(part["content"], translated_segment)
                )

        result = "".join(final_parts)

        # 5. Final iterative pass to resolve all tokens (including nested ones)
        for _ in range(10):
            changed = False
            for token in all_token_ids:
                if token in result:
                    result = result.replace(token, token_map[token])
                    changed = True
            if not changed:
                break

        return result

    # Cyrillic homoglyphs that translation models substitute for the ASCII "TK" prefix.
    # Т (U+0422) looks like T; К (U+041A) looks like K; lower-case variants included.
    _TK_HOMOGLYPHS = str.maketrans("ТКтк", "TKtk")

    @staticmethod
    def _norm_inner(raw: str) -> tuple[str, str]:
        """Return (strip-spaces key, space-as-underscore key) for lookup, both lowercase.

        Also transliterates Cyrillic Т/К homoglyphs to ASCII T/K so tokens mangled
        by the model (e.g. ``[[ТК_67cbbf_0]]``) still resolve correctly.
        """
        ascii_raw = raw.translate(TermProtector._TK_HOMOGLYPHS)
        strip_key = re.sub(r"\s+", "",  ascii_raw).lower()
        under_key = re.sub(r"\s+", "_", ascii_raw).lower()
        return strip_key, under_key

    def _normalize_tokens(self, text: str, token_map: Dict[str, str]) -> str:
        """Repair LLM-mangled tokens back to their canonical form.

        Handles two token formats:
        - New: ``[[TK_a3f9b2_0]]`` — double-bracket deterministic hash tokens
        - Legacy: ``[TK:000001]`` — single-bracket sequential tokens (backwards compat)

        Also fixes:
        - Uppercase hex hash (``[[TK_A3F9B2_0]]``)
        - Space substituted for underscore before index (``[[TK_a3f9b2 0]]``)
        - Cyrillic Т/К homoglyphs in the prefix (``[[ТК_a3f9b2_0]]``)
        - Tokens truncated at end of model output
        """
        if not token_map:
            return text

        # 1. Fix truncated tokens at end of output — allow both lowercase and uppercase hex.
        text = re.sub(r"\[\[TK_[0-9a-fA-F]{6}_\d+$", lambda m: m.group(0) + "]]", text)
        text = re.sub(r"\[TK:\d+$", lambda m: m.group(0) + "]", text)

        # 2. Build lookup: multiple normalised keys → canonical token.
        #    Each token gets two entries: strip-whitespace and space→underscore variants,
        #    both lowercased with Cyrillic homoglyphs mapped to ASCII.
        lookup: Dict[str, str] = {}
        for token in token_map:
            m_dbl = re.match(r"^\[\[(.+)\]\]$", token)
            if m_dbl:
                strip_k, under_k = self._norm_inner(m_dbl.group(1))
                lookup.setdefault(strip_k, token)
                lookup.setdefault(under_k, token)
                # Hash+index fallback: matches even if the TK prefix is completely garbled.
                # Token format: TK_{6hex}_{int} — extract the numeric parts only.
                m_hash = re.search(r"([0-9a-fA-F]{6})[_\s]+(\d+)$", m_dbl.group(1))
                if m_hash:
                    lookup.setdefault(
                        f"tk_{m_hash.group(1).lower()}_{m_hash.group(2)}", token
                    )
            else:
                m_sgl = re.match(r"^\[(.+)\]$", token)
                if m_sgl:
                    strip_k, _ = self._norm_inner(m_sgl.group(1))
                    lookup.setdefault(strip_k, token)

        if not lookup:
            return text

        def _fix_dbl(m: re.Match) -> str:
            raw = m.group(1)
            strip_k, under_k = self._norm_inner(raw)
            found = lookup.get(strip_k) or lookup.get(under_k)
            if found is None:
                # Last resort: match only the 6-hex hash + index digits
                m_hash = re.search(r"([0-9a-fA-F]{6})[_\s]+(\d+)", raw)
                if m_hash:
                    found = lookup.get(f"tk_{m_hash.group(1).lower()}_{m_hash.group(2)}")
            return found if found is not None else m.group(0)

        def _fix_sgl(m: re.Match) -> str:
            strip_k, _ = self._norm_inner(m.group(1))
            # Only substitute when the normalised content matches a known token
            # (prevents game bracket-tags like [Attack] from being mis-identified).
            found = lookup.get(strip_k)
            return found if found is not None else m.group(0)

        # Strip backticks that LLMs sometimes wrap around tokens (e.g. `[[TK_a3f9b2_0]]`).
        text = re.sub(r"`(\[\[TK_[0-9a-fA-F]{6}_\d+\]\])`", r"\1", text, flags=re.IGNORECASE)
        text = re.sub(r"`(\[TK:\d+\])`", r"\1", text)

        # Fix double-bracket tokens — also catches Cyrillic-prefix variants like [[ТК_...]].
        text = re.sub(r"\[\[\s*([^\]]*?)\s*\]\]", _fix_dbl, text)
        # Fix single-bracket tokens (negative lookbehind prevents matching inner [[ bracket).
        # Pattern accepts both ASCII TK and Cyrillic ТК as prefix.
        text = re.sub(r"(?<!\[)\[\s*([ТTтt][КKкk][^\]]*?)\s*\]", _fix_sgl, text)

        return text

    def get_statistics(self) -> Dict:
        by_cat: Dict[str, int] = {}
        for term in self.protected_terms.values():
            by_cat[term.category] = by_cat.get(term.category, 0) + 1

        span_lookups = self._span_cache_hits + self._span_cache_misses
        cache_hit_rate = (
            self._span_cache_hits / span_lookups if span_lookups else 0.0
        )
        avg_compile_ms = (
            (self._compile_total_ns / self._compile_count) / 1_000_000
            if self._compile_count
            else 0.0
        )
        return {
            "total_terms": len(self.protected_terms),
            "by_category": dict(sorted(by_cat.items(), key=lambda x: -x[1])),
            # ── Compilation profiling ──────────────────────────────────────
            "compile_count": self._compile_count,
            "compile_total_ms": round(self._compile_total_ns / 1_000_000, 2),
            "compile_avg_ms": round(avg_compile_ms, 2),
            "compile_generation": self._compile_generation,
            # ── Span cache ─────────────────────────────────────────────────
            "span_cache_size": len(self._span_cache),
            "span_cache_hits": self._span_cache_hits,
            "span_cache_misses": self._span_cache_misses,
            "span_cache_hit_rate": round(cache_hit_rate, 3),
            # ── Thread contention ──────────────────────────────────────────
            "contention_count": self._contention_count,
        }

    def export_terms(self, file_path: Path):
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Protected Terms for Bethesda AI Translator\n# Format: Term,Category\n\n"
                )
                for term in sorted(self.protected_terms.keys(), key=str.lower):
                    f.write(f"{term},{self.protected_terms[term].category}\n")
        except Exception as e:
            logger.error(f"Failed to export terms: {e}")
