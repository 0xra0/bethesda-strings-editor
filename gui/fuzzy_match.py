"""
Fuzzy string matching algorithms ported from xTranslator (Pascal) by McGuffin.
Source: TESVT_HeuristicSearch.pas, TESVT_TranslateFunc.pas,
        TESVT_RegexUtils.pas, TESVT_Const.pas

Ported algorithms:
  levenshtein_distance       — char-level edit distance
  longest_common_substring   — length of longest shared substring
  longest_common_prefix      — length of shared prefix
  words_distance             — word-level edit distance (Levenshtein on word lists)
  string_proxy               — 0-3 penalty: uppercase/number/punctuation mismatch
  remove_unicode_control_chars
  is_arabic_letter
  fuzzy_score                — main heuristic: lower = closer match (0 = identical)
  best_fuzzy_match           — find the closest string in a list

Primary use: fuzzy lookup in TranslationMemory when no exact source match exists.
"""

import math
import re
from typing import Iterable, Optional

# ── constants (mirror TESVT_RegexUtils.pas / TESVT_Const.pas) ───────────────

PROXY_BASE_RATIO: float = 0.05       # proxybaseRatio
LD_WORD_THRESHOLD_MAX: int = 10      # iLDWordSearchThresholdMax
LD_MAX_BREAK: int = 25               # iLDMaxBreak

# Accepted score ceiling for a "match" (matches with score > this are ignored).
SCORE_CEILING: float = 99.0

# ── unicode helpers ──────────────────────────────────────────────────────────

# Unicode control-character ranges (from IsUnicodeControleChar in Pascal)
_CTRL_RANGES = [
    (0x0000, 0x001F),
    (0x007F, 0x009F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
]
_CTRL_SINGLES = {0x200E, 0x200F}


def is_unicode_control(ch: str) -> bool:
    cp = ord(ch)
    if cp in _CTRL_SINGLES:
        return True
    return any(lo <= cp <= hi for lo, hi in _CTRL_RANGES)


def remove_unicode_control_chars(s: str) -> str:
    """Strip Unicode format/control characters (ported from RemoveUnicodeControlChars)."""
    return "".join(ch for ch in s if not is_unicode_control(ch))


def is_arabic_letter(ch: str) -> bool:
    """True if ch falls in an Arabic Unicode range (ported from IsArabicLetter)."""
    cp = ord(ch)
    return (
        0x0600 <= cp <= 0x06FF
        or 0x0750 <= cp <= 0x077F
        or 0xFB50 <= cp <= 0xFDFF
        or 0xFE70 <= cp <= 0xFEFF
    )


# ── char-level algorithms ────────────────────────────────────────────────────

def levenshtein_distance(s: str, t: str) -> int:
    """Classic Levenshtein edit distance (ported from LevenshteinDistance).

    Uses the standard DP matrix; O(n·m) time, O(n·m) space.
    For long strings the word-level variant (words_distance) is cheaper.
    """
    n, m = len(s), len(t)
    if n == 0:
        return m
    if m == 0:
        return n

    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if s[i - 1] == t[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)

    return d[n][m]


def longest_common_substring(s1: str, s2: str) -> int:
    """Length of the longest common substring (ported from getLongestCommonStrInt).

    Unlike longest common subsequence this requires contiguous characters.
    """
    l1, l2 = len(s1), len(s2)
    if l1 == 0 or l2 == 0:
        return 0

    t = [[0] * l2 for _ in range(l1)]
    best = 0
    for i in range(l1):
        for j in range(l2):
            if s1[i] != s2[j]:
                t[i][j] = 0
            else:
                t[i][j] = 1 + (t[i - 1][j - 1] if i > 0 and j > 0 else 0)
                if t[i][j] > best:
                    best = t[i][j]
    return best


def longest_common_prefix(s1: str, s2: str) -> int:
    """Length of the longest common prefix (ported from getLongestCommonStrInt_Header)."""
    limit = min(len(s1), len(s2))
    for i in range(limit):
        if s1[i] != s2[i]:
            return i
    return limit


# ── word-level algorithms ────────────────────────────────────────────────────

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def tokenize(s: str) -> list[int]:
    """Split *s* into words and return a list of their hash values.

    Using hash() of each lowercased word mirrors xTranslator's aWords cardinal list.
    We mask to 32-bit unsigned to stay comparable with Pascal cardinals.
    """
    mask = 0xFFFF_FFFF
    return [hash(w.lower()) & mask for w in _WORD_RE.findall(s)]


def words_distance(words1: list[int], words2: list[int]) -> int:
    """Levenshtein distance on word-hash lists (ported from WordsDistance).

    Each element is a word hash; cost=1 when two hashes differ.
    """
    n, m = len(words1), len(words2)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if words1[i - 1] == words2[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)

    return d[n][m]


# ── threshold helpers (ported from TESVT_RegexUtils.pas) ────────────────────

def _define_heuristic_threshold(word_count: int) -> int:
    """Max allowed word-level edit distance for a given source word count.

    defineHeuristicThreshold in Pascal:
      0 words → 0
      1 word  → 1
      else    → ceil(n/3)+1, capped at LD_MAX_BREAK
    """
    if word_count == 0:
        return 0
    if word_count == 1:
        return 1
    return min(math.ceil(word_count / 3) + 1, LD_MAX_BREAK)


def _adjust_heuristic_result(word_count: int, ld: float) -> float:
    """Compress small distances for long strings (adjustHeuristicResult).

    If ld <= floor(word_count/15), collapse it to 0.55+(ld/10) to treat
    near-identical long strings as good matches.
    """
    if ld == 0:
        return 0.0
    tmp = word_count // 15
    if ld <= tmp:
        return 0.55 + ld / 10.0
    return ld


def _substring_threshold_inc(length: int) -> int:
    """How many extra chars s2 may have over s1 (getSubTringThresholdInc)."""
    if length <= 3:
        return 0
    if length <= 5:
        return 1
    if length <= 8:
        return 2
    if length <= 12:
        return 3
    return 4


def _substring_threshold_dec(length: int) -> int:
    """How many chars may be absent from the common run (getSubTringThresholdDec)."""
    if length <= 4:
        return 0
    if length <= 6:
        return 1
    if length <= 8:
        return 2
    return 3


# ── string proxy (ported from GetStringProxy in TESVT_Const.pas) ─────────────

def string_proxy(source: str, translation: str) -> int:
    """Return a 0-3 penalty for structural mismatches between source and translation.

    Checks: ALL-CAPS consistency, digit presence, punctuation presence.
    Lower is better (0 = all three agree).
    """
    penalty = 3

    # Uppercase agreement
    if source.isupper() == translation.isupper():
        penalty -= 1

    has_digit_src = any(ch.isdigit() for ch in source)
    has_digit_tr  = any(ch.isdigit() for ch in translation)
    if has_digit_src == has_digit_tr:
        penalty -= 1

    punctuation = set('.,!?;:-—–()[]{}"\'/\\')
    has_punc_src = any(ch in punctuation for ch in source)
    has_punc_tr  = any(ch in punctuation for ch in translation)
    if has_punc_src == has_punc_tr:
        penalty -= 1

    return penalty


# ── main heuristic (ported from getWordsMatchHash) ───────────────────────────

def _word_near(words: list[int], start: int, margin: int, target: int) -> bool:
    """True if *target* appears within *margin* positions of *start* in *words*."""
    lo = max(0, start - margin)
    hi = min(len(words) - 1, start + margin)
    return any(words[i] == target for i in range(lo, hi + 1))


def fuzzy_score(
    source: str,
    candidate: str,
    candidate_translation: str = "",
) -> Optional[float]:
    """Compute a heuristic similarity score between *source* and *candidate*.

    Returns None if the pair is too different to be useful.
    Returns 0.0–1.0 for very close matches; higher values = worse match.

    Ported from getWordsMatchHash + getSubStringMatch in xTranslator.

    Args:
        source:               the string we want to translate
        candidate:            a string in the translation memory
        candidate_translation: the translation of *candidate* (used for proxy penalty)
    """
    src_lower = source.lower()
    cnd_lower = candidate.lower()

    # ── fast-path: identical (same hash in Pascal) ─────────────────────────
    if src_lower == cnd_lower:
        prx = string_proxy(source, candidate_translation) if candidate_translation else 0
        return 0.01 + prx * PROXY_BASE_RATIO

    src_words = tokenize(source)
    cnd_words = tokenize(candidate)

    # ── single-word path: use substring matching ───────────────────────────
    if len(src_words) == 1 and len(cnd_words) == 1:
        return _substring_score(source, candidate, candidate_translation)

    # ── multi-word path ────────────────────────────────────────────────────
    threshold = _define_heuristic_threshold(len(src_words))

    # Hard break: candidate has too many more words than source
    if len(cnd_words) - len(src_words) > threshold:
        return None

    # Count words in source not found near their position in candidate
    misses = 0
    for i, w in enumerate(src_words):
        if not _word_near(cnd_words, i, threshold + 1, w):
            misses += 1
            if misses >= threshold:
                return None

    ld: float = SCORE_CEILING
    if misses < min(LD_WORD_THRESHOLD_MAX, threshold + 1):
        ld = float(words_distance(src_words, cnd_words))

    if ld > threshold:
        return None

    ld = _adjust_heuristic_result(len(src_words), ld)

    if ld == 0:
        ld = 0.5

    prx = string_proxy(source, candidate_translation) if candidate_translation else 0
    if ld < threshold + 1:
        ld += prx * PROXY_BASE_RATIO

    if ld > threshold:
        return None

    return ld


def _substring_score(
    s1: str, s2: str, s2_translation: str = ""
) -> Optional[float]:
    """Score for single-word strings using substring overlap (getSubStringMatch)."""
    l1, l2 = len(s1), len(s2)
    s1l, s2l = s1.lower(), s2.lower()

    if l1 < 8:
        lcs = longest_common_prefix(s1l, s2l)
    else:
        lcs = longest_common_substring(s1l, s2l)

    missing   = l1 - lcs          # chars from s1 absent in common run
    size_diff = abs(l2 - l1)      # length difference

    if missing > _substring_threshold_dec(l1):
        return None
    if size_diff > _substring_threshold_inc(l1):
        return None

    s_size = max(l1, l2) - lcs
    prx = string_proxy(s1, s2_translation) if s2_translation else 0
    base = 0.1 if s_size == 0 else 0.55
    return s_size * 0.1 + base + prx * PROXY_BASE_RATIO


# ── public convenience API ───────────────────────────────────────────────────

_DIGIT_RUN_RE = re.compile(r"\d+")


def _digit_runs(s: str) -> list[str]:
    """Ordered list of consecutive-digit runs in *s* (e.g. '28LY→30LY' → ['28','30'])."""
    return _DIGIT_RUN_RE.findall(s)


def best_fuzzy_match(
    source: str,
    candidates: Iterable[tuple[str, str]],
    max_score: float = 5.0,
) -> Optional[tuple[str, float]]:
    """Return the best (translation, score) pair for *source* from *candidates*.

    Args:
        source:     the source string to look up
        candidates: iterable of (candidate_source, candidate_translation) pairs
        max_score:  ignore matches with score above this threshold

    Returns:
        (translation, score) of the best match, or None if nothing is close enough.

    A digit guard rejects any candidate whose numeric runs differ from the
    source's, in order: a fuzzy match is textually close but semantically wrong
    when the numbers disagree (e.g. reusing "28LY" for "30LY", or "Level 3" for
    "Level 5"). Candidates with mismatched digits are skipped entirely.
    """
    src_digits = _digit_runs(source)
    best_score: float = max_score + 1.0
    best_translation: Optional[str] = None

    for cnd_src, cnd_tr in candidates:
        if _digit_runs(cnd_src) != src_digits:
            continue
        score = fuzzy_score(source, cnd_src, cnd_tr)
        if score is not None and score < best_score:
            best_score = score
            best_translation = cnd_tr

    if best_translation is None:
        return None
    return best_translation, best_score


class FuzzyIndex:
    """Candidate pre-filter for :func:`best_fuzzy_match` over a large pool.

    ``best_fuzzy_match`` is a linear scan and every candidate costs a digit-run
    regex, two tokenisations and a word-level edit distance.  Against a
    translation memory of the size the Official-TM miner produces (Starfield's
    own localizations run to six figures) that is seconds per lookup, on the
    *miss* path that every uncached string takes.  This narrows the pool first.

    The filter is **sound** — it only drops candidates ``fuzzy_score`` would
    have rejected anyway — which rests on two properties of that function:

    * **Multi-word sources.** A candidate survives only when fewer than
      ``threshold`` of the ``n`` source words are missing from it, so it must
      match at least ``n - threshold + 1`` source *positions*.  A position can
      only match when its word appears somewhere in the candidate, so summing
      the query-side multiplicity of every word the two share is an upper bound
      on the positions that can match — candidates whose bound falls short are
      unreachable.  ``fuzzy_score`` also breaks outright when the candidate has
      more than ``threshold`` words more than the source, which bounds the
      candidate's word count too.
    * **Single-word sources.** These take the substring path, which can match
      without sharing a word ("Reload" / "Reloads") — so word indexing alone
      would be unsound.  But that path also rejects any candidate whose length
      differs by more than ``_substring_threshold_inc(len(source))``, so only a
      narrow band of lengths can match.  Single-word sources are therefore
      indexed by length as well as by word.

    Sources that tokenise to no words at all (punctuation-only) can only ever
    match each other: a worded source misses every one of its words against
    them, and they hit the ``len(cnd_words) - len(src_words) > threshold``
    break against a worded candidate.  They are kept in their own bucket.
    """

    def __init__(self, sources: Iterable[str] = ()) -> None:
        self._by_word: dict[int, list[str]] = {}
        self._single_by_len: dict[int, list[str]] = {}
        self._tokenless: list[str] = []
        # Insertion ordinal per source, so candidates() can hand back the pool in
        # the order a full scan would have walked it.  best_fuzzy_match keeps the
        # *first* candidate at the best score, so ties are broken by position —
        # returning them in set order would pick a different translation from run
        # to run (PYTHONHASHSEED), for the same memory and the same string.
        # source → (insertion ordinal, word count).  One dict rather than two:
        # both fields are written together and read together, and a translation
        # memory carries six figures of entries.
        self._meta: dict[str, tuple[int, int]] = {}
        for source in sources:
            self.add(source)

    def add(self, source: str) -> None:
        """Index one candidate source string."""
        if source in self._meta:
            return
        words = tokenize(source)
        self._meta[source] = (len(self._meta), len(words))
        if not words:
            self._tokenless.append(source)
            return
        # A single-word source is reachable both ways: by word (when the query
        # has several words, which forces the word-distance path) and by length
        # (when the query is itself one word, which forces the substring path).
        if len(words) == 1:
            self._single_by_len.setdefault(len(source), []).append(source)
        for w in set(words):
            self._by_word.setdefault(w, []).append(source)

    def candidates(self, source: str) -> list[str]:
        """Every indexed source that could possibly score against *source*.

        Returned in insertion order, so scoring this list picks exactly the same
        winner a full scan would — including among equal-scoring candidates.
        """
        words = tokenize(source)
        if not words:
            return list(self._tokenless)

        n = len(words)
        threshold = _define_heuristic_threshold(n)
        required = n - threshold + 1   # positions that must match; >= 1 for all n
        max_words = n + threshold      # fuzzy_score's outright length break

        multiplicity: dict[int, int] = {}
        for w in words:
            multiplicity[w] = multiplicity.get(w, 0) + 1

        # Upper bound, per candidate, on how many source positions could match.
        reach: dict[str, int] = {}
        for w, mult in multiplicity.items():
            for cand in self._by_word.get(w, ()):
                reach[cand] = reach.get(cand, 0) + mult

        out = {
            cand
            for cand, bound in reach.items()
            if bound >= required and self._meta[cand][1] <= max_words
        }

        if n == 1:
            # The substring path can match without sharing a word at all, so the
            # bound above does not apply — fall back to its own length window.
            span = _substring_threshold_inc(len(source))
            for length in range(len(source) - span, len(source) + span + 1):
                out.update(self._single_by_len.get(length, ()))

        return sorted(out, key=lambda cand: self._meta[cand][0])


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cases = [
        ("Hello world", "Hello world",  0.0),
        ("Hello world", "Hello worlds", None),  # should be close
        ("Привіт світ", "Привіт світ",  0.0),
        ("short",       "shorts",        None),
        ("",            "anything",      None),
    ]
    print("levenshtein_distance:")
    for a, b, _ in [("kitten", "sitting", 3), ("abc", "abc", 0), ("", "abc", 3)]:
        print(f"  {a!r:12} ↔ {b!r:10} = {levenshtein_distance(a, b)}")

    print("\nfuzzy_score:")
    for src, cnd, _ in cases:
        s = fuzzy_score(src, cnd)
        print(f"  {src!r:20} ↔ {cnd!r:20} → {s}")

    print("\nbest_fuzzy_match:")
    pool = [
        ("Hello world",   "Привіт світ"),
        ("Hello worlds",  "Привіт світи"),
        ("Completely different", "Абсолютно інше"),
    ]
    result = best_fuzzy_match("Hello world", pool)
    print(f"  query='Hello world' → {result}")
