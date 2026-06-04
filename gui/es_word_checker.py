"""Spanish word checker — data/spanish_words.txt (hermitdave/FrequencyWords)."""
from gui._word_checker_base import WordChecker as _WC
from typing import Optional

_checker = _WC("spanish_words.txt", "Spanish")

def word_is_spanish(word: str) -> Optional[bool]:
    return _checker.word_in(word)

def text_has_spanish_words(text: str, threshold: int = 4) -> bool:
    return _checker.text_has_words(text, threshold)

def dict_loaded() -> bool:
    return _checker.is_loaded()

def preload() -> None:
    _checker.preload()
