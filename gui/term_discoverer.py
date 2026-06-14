"""
Term Discoverer — heuristic extraction of candidate protected terms from loaded strings.

Algorithm:
  1. Walk every source string, tokenize on word/punctuation boundaries.
  2. Keep tokens that are Titlecase or ALL-CAPS and appear mid-sentence (not the
     first token after a sentence-ending punctuation or at position 0).
  3. Boost score when the identical token appears unchanged in the paired translation
     — the strongest signal that it is a proper noun the model should leave alone.
  4. Filter out tokens already known to the TermProtector and short common words.
  5. Return candidates sorted by score (frequency × cross-match weight).
"""

from __future__ import annotations

import re
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# Word-like tokens: letters + optional internal hyphens/apostrophes
_TOKEN_RE = re.compile(r"[A-Za-zЀ-ӿ][\w'\-]*[A-Za-zЀ-ӿ\w]|[A-Za-zЀ-ӿ]")

# Sentence-ending characters — what precedes a sentence-initial position
_SENT_END_RE = re.compile(r"[.!?…]\s*$")

# Tags we should never treat as candidate terms
_TAG_RE = re.compile(
    r"^(?:<[^>]+>|\[(?:MALE|FEMALE|PLYR)\]"
    r"|%[sd]|\{\w+\}|\[\[.*?\]\])$",
    re.IGNORECASE,
)

# Very common English words that appear capitalized purely due to sentence position
# or UI convention — not proper nouns.
_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "of", "for",
    "with", "by", "from", "as", "is", "was", "are", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall", "should",
    "may", "might", "must", "can", "could", "that", "this", "these", "those",
    "it", "its", "you", "your", "he", "she", "they", "them", "their", "we", "our",
    "i", "me", "my", "not", "no", "yes", "all", "any", "each", "every", "some",
    "new", "old", "good", "bad", "more", "most", "other", "own", "same", "so",
    "than", "then", "there", "very", "just", "also", "only", "up", "out", "about",
    "after", "before", "here", "now", "when", "where", "who", "which", "what",
    "how", "if", "because", "while", "although", "though", "log", "note",
    "warning", "error", "info", "level", "type", "name", "item", "entry",
    "press", "hold", "select", "return", "back", "next", "save", "load",
    # Russian common words that appear in source strings
    "не", "на", "и", "в", "с", "к", "о", "по", "из", "за", "от", "до",
    "но", "как", "что", "это", "все", "для", "при", "без", "или", "уже",
    "так", "есть", "был", "нет", "да", "вы", "мы", "он", "она", "они",
}


@dataclass
class CandidateTerm:
    term: str
    category: str = "discovered"
    frequency: int = 0        # total occurrences in source strings
    cross_matches: int = 0    # times it appears unchanged in paired translation
    score: float = 0.0        # composite rank


def discover_terms(
    rows: Sequence[dict],
    existing_terms: Optional[Set[str]] = None,
    min_length: int = 3,
    max_candidates: int = 300,
    source_lang: str = "Russian",
) -> List[CandidateTerm]:
    """
    Analyse *rows* (dicts with keys 'original' and 'translated') and return
    a ranked list of candidate protected terms not already in *existing_terms*.

    Parameters
    ----------
    rows : sequence of row dicts from StringTableModel._data
    existing_terms : set of term strings already in TermProtector (lowercased)
    min_length : ignore tokens shorter than this
    max_candidates : cap on returned candidates
    source_lang : used only for log messages
    """
    existing_lower: Set[str] = {t.lower() for t in (existing_terms or set())}

    # freq[token] = (total_occurrences, cross_match_count)
    freq: Dict[str, list] = defaultdict(lambda: [0, 0])

    for row in rows:
        original: str = row.get("original", "") or ""
        translated: str = row.get("translated", "") or ""
        if not original:
            continue

        src_tokens = _extract_mid_sentence_tokens(original)
        if not src_tokens:
            continue

        translated_lower = translated.lower() if translated else ""

        seen_in_row: Set[str] = set()
        for token in src_tokens:
            t_lower = token.lower()
            if t_lower in _STOPWORDS or t_lower in existing_lower:
                continue
            if len(token) < min_length:
                continue
            if _TAG_RE.match(token):
                continue

            freq[token][0] += 1
            # Cross-match: token appears verbatim (case-insensitive) in translation
            if token not in seen_in_row and translated and t_lower in translated_lower:
                freq[token][1] += 1
                seen_in_row.add(token)

    if not freq:
        return []

    candidates: List[CandidateTerm] = []
    for token, (total, cross) in freq.items():
        # Require at least 2 occurrences OR at least 1 cross-match to reduce noise
        if total < 2 and cross == 0:
            continue
        # Score: cross-matches are worth 3× a plain frequency count
        score = cross * 3.0 + total * 1.0
        candidates.append(CandidateTerm(
            term=token,
            frequency=total,
            cross_matches=cross,
            score=score,
        ))

    candidates.sort(key=lambda c: (-c.score, c.term))
    logger.info(
        "Term discovery: scanned %d rows, found %d raw candidates, "
        "returning top %d",
        len(rows), len(candidates), min(max_candidates, len(candidates)),
    )
    return candidates[:max_candidates]


def _extract_mid_sentence_tokens(text: str) -> List[str]:
    """
    Return tokens from *text* that are Titlecase or ALL-CAPS and appear
    mid-sentence (not at position 0 or immediately after sentence-ending
    punctuation + whitespace).
    """
    tokens: List[str] = []
    # Split on whitespace to reason about sentence-initial positions
    words = text.split()
    sentence_start = True  # first word is always sentence-initial

    for word in words:
        # Strip trailing punctuation for the token we test
        clean = word.strip("\"'«»()[]{}.,;:!?…-—–")
        if not clean:
            # Update sentence_start based on the raw word
            sentence_start = bool(_SENT_END_RE.search(word))
            continue

        for m in _TOKEN_RE.finditer(clean):
            token = m.group()
            if not sentence_start and _is_proper_candidate(token):
                tokens.append(token)

        # Was this word sentence-ending?
        sentence_start = bool(_SENT_END_RE.search(word))

    return tokens


def _is_proper_candidate(token: str) -> bool:
    """True when token looks like a proper noun (Titlecase or ALL-CAPS, ASCII or Latin)."""
    if not token or not token[0].isalpha():
        return False
    # Must contain at least one ASCII letter (Cyrillic words in source are not
    # candidates — we want terms that survive unchanged into the target script)
    has_ascii_alpha = any(c.isascii() and c.isalpha() for c in token)
    if not has_ascii_alpha:
        return False
    # Titlecase: first letter upper, rest contain at least one lower
    # ALL-CAPS: all letters upper (abbreviations like "UC", "SSNN")
    uppers = sum(1 for c in token if c.isupper())
    lowers = sum(1 for c in token if c.islower())
    if lowers > 0 and token[0].isupper():
        return True   # Titlecase
    if uppers >= 2 and lowers == 0:
        return True   # ALL-CAPS abbreviation (min 2 to skip "I", "A")
    return False
