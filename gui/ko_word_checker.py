"""Korean word checker — data/korean_words.txt (hermitdave/FrequencyWords or plain list).

Korean is written in Hangul (syllabic blocks, U+AC00–U+D7A3).  The base
WordChecker already handles multi-token word lists; the only additional helper
exposed here is has_hangul(), which is a fast script-presence check used by the
quality checker independently of the frequency dictionary.
"""
from typing import Optional

from gui._word_checker_base import WordChecker as _WC

_checker = _WC("korean_words.txt", "Korean")


def word_is_korean(word: str) -> Optional[bool]:
    return _checker.word_in(word)


def text_has_korean_words(text: str, threshold: int = 3) -> bool:
    return _checker.text_has_words(text, threshold)


def dict_loaded() -> bool:
    return _checker.is_loaded()


def preload() -> None:
    _checker.preload()


def has_hangul(text: str) -> bool:
    """Return True if *text* contains at least one Hangul syllable character."""
    return any("가" <= c <= "힣" for c in text)
