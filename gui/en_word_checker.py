"""
English word dictionary for validating and detecting English text.

Loads data/english_words.txt (370 k words from dwyl/english-words, all
lowercase alphabetic) lazily on first use, then caches as a frozenset for
O(1) lookups.

Primary use-cases:
  1. _protect_english_text (ollama_worker): confirm a Latin-script segment
     is a real English word before protecting it from translation.
  2. QualityChecker._check_english_leak: detect untranslated English words
     left in Ukrainian output when source was English.
"""

import logging
import threading
from pathlib import Path
from typing import FrozenSet, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# High-frequency English function words that legitimately appear even in fully
# translated Ukrainian game text (e.g. "OK", "to", "in" inside tags/codes).
# These are excluded from the English-leak quality check.
# ---------------------------------------------------------------------------
EN_FUNCTION_WORDS: frozenset = frozenset({
    # articles / determiners
    "a", "an", "the",
    # conjunctions
    "and", "or", "but", "nor", "so", "yet", "for", "both", "either",
    # prepositions
    "in", "on", "at", "to", "by", "of", "up", "as", "off", "out",
    "into", "onto", "from", "with", "over", "than", "via", "per",
    # pronouns
    "i", "me", "we", "us", "you", "he", "she", "it", "they", "them",
    "my", "our", "your", "his", "her", "its", "their",
    # auxiliary verbs
    "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "shall", "should", "may", "might", "must",
    "can", "could", "need", "dare", "ought",
    # negation / particles
    "not", "no", "nor",
    # very common short words
    "if", "then", "else", "when", "where", "what", "who", "how",
    "all", "any", "few", "more", "most", "other", "some", "such",
    "own", "same", "too", "very", "just", "also", "only",
    # common words that appear inside game strings
    "ok", "yes", "log", "id", "ui",
})

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------
_en_dict: Optional[FrozenSet[str]] = None
_load_lock = threading.Lock()
_load_failed = False


def _dict_path() -> Path:
    return Path(__file__).parent.parent / "data" / "english_words.txt"


def _load_dict() -> Optional[FrozenSet[str]]:
    """Load and cache the English word list (called at most once)."""
    global _en_dict, _load_failed

    if _en_dict is not None or _load_failed:
        return _en_dict

    with _load_lock:
        if _en_dict is not None or _load_failed:
            return _en_dict

        path = _dict_path()
        if not path.exists():
            logger.warning(
                f"English word list not found at {path}. "
                "Run scripts/download_en_dict.py to download it. "
                "English word validation disabled."
            )
            _load_failed = True
            return None

        import time
        t0 = time.monotonic()
        try:
            with open(path, encoding="utf-8") as fh:
                words: FrozenSet[str] = frozenset(
                    ln.strip().lower()
                    for ln in fh
                    if 2 <= len(ln.strip()) <= 30 and ln.strip().isalpha()
                )
            elapsed = time.monotonic() - t0
            logger.info(
                f"Loaded {len(words):,} English words in {elapsed:.1f}s"
            )
            _en_dict = words
            return words
        except Exception as exc:
            logger.error(f"Failed to load English word list: {exc}")
            _load_failed = True
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def word_is_english(word: str) -> Optional[bool]:
    """
    Return True if *word* is in the English dictionary.
    Returns None if the dictionary is not loaded (file missing).

    Strips common punctuation and lowercases before lookup.
    """
    en = _load_dict()
    if en is None:
        return None
    cleaned = word.strip(".,!?-:;«»\"'()[]{}…—–").lower()
    if not cleaned:
        return None
    return cleaned in en


def text_has_english_words(text: str, threshold: int = 4) -> bool:
    """
    Return True if *text* contains at least *threshold* non-trivial English
    words (i.e., in the dictionary, not a function word, purely ASCII alpha,
    length >= 4, not all-uppercase).

    Designed to detect untranslated English content in Ukrainian output:
      - Proper nouns (first-letter uppercase) are skipped.
      - All-uppercase tokens (game codes/acronyms) are skipped.
      - English function words are skipped.
      - Words not in the dictionary are skipped.
    """
    if not text:
        return False

    en = _load_dict()
    if en is None:
        return False

    hits = 0
    for token in text.split():
        # Strip punctuation
        raw = token.strip(".,!?-:;«»\"'()[]{}…—–")
        if not raw:
            continue
        # Only ASCII alpha sequences can be English words
        if not raw.isascii() or not raw.replace("-", "").isalpha():
            continue
        # Skip short tokens (noise)
        if len(raw) < 4:
            continue
        # Skip proper nouns (capitalised in running text)
        if raw[0].isupper():
            continue
        # Skip acronyms / game codes (ALL CAPS)
        if raw.isupper():
            continue
        word = raw.lower()
        if word in EN_FUNCTION_WORDS:
            continue
        if word in en:
            hits += 1
            if hits >= threshold:
                return True

    return False


def dict_loaded() -> bool:
    """Return True if the dictionary is available in memory."""
    return _load_dict() is not None


def preload() -> None:
    """Trigger dictionary load in a background thread."""
    threading.Thread(target=_load_dict, daemon=True, name="en-dict-preload").start()
