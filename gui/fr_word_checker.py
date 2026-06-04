"""French word checker — data/french_words.txt (hermitdave/FrequencyWords)."""
from gui._word_checker_base import WordChecker as _WC
from typing import Optional

_checker = _WC("french_words.txt", "French")

def word_is_french(word: str) -> Optional[bool]:
    return _checker.word_in(word)

def text_has_french_words(text: str, threshold: int = 4) -> bool:
    return _checker.text_has_words(text, threshold)

def dict_loaded() -> bool:
    return _checker.is_loaded()

def preload() -> None:
    _checker.preload()
