"""German word checker — data/german_words.txt (hermitdave/FrequencyWords)."""
from gui._word_checker_base import WordChecker as _WC
from typing import Optional

_checker = _WC("german_words.txt", "German")

def word_is_german(word: str) -> Optional[bool]:
    return _checker.word_in(word)

def text_has_german_words(text: str, threshold: int = 4) -> bool:
    return _checker.text_has_words(text, threshold)

def dict_loaded() -> bool:
    return _checker.is_loaded()

def preload() -> None:
    _checker.preload()
