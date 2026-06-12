"""Korean word checker — data/korean_words.txt (hermitdave/FrequencyWords ko_50k).

Korean is written in Hangul syllabic blocks (U+AC00–U+D7A3).  Each block is
one Unicode character but represents a full syllable, so meaningful words can
be 1–2 characters long.  min_word_len=1 is therefore used instead of the
Latin-script default of 3.

text_has_korean_words() uses Hangul script presence as the primary signal
(fast, no word-list load required) and falls back to the frequency list for
a stricter count-based check.
"""
from typing import Optional

from gui._word_checker_base import WordChecker as _WC

_checker = _WC("korean_words.txt", "Korean", min_word_len=1)


def word_is_korean(word: str) -> Optional[bool]:
    return _checker.word_in(word)


def text_has_korean_words(text: str, threshold: int = 3) -> bool:
    """Return True if *text* appears to be Korean.

    Counts Hangul syllable characters directly — more reliable than frequency
    matching for a script where common words are 1–2 characters long.
    Falls back to the frequency list if Hangul count is below threshold.
    """
    hangul_count = sum(1 for c in text if "가" <= c <= "힣")
    if hangul_count >= threshold:
        return True
    return _checker.text_has_words(text, threshold)


def dict_loaded() -> bool:
    return _checker.is_loaded()


def preload() -> None:
    _checker.preload()


def has_hangul(text: str) -> bool:
    """Return True if *text* contains at least one Hangul syllable character."""
    return any("가" <= c <= "힣" for c in text)
