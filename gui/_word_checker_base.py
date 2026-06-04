"""
Shared base class for per-language word frequency checkers.

Each language module (de_word_checker, fr_word_checker, …) creates one
WordChecker instance and exposes thin module-level wrappers around it.

Word list format (hermitdave/FrequencyWords and plain lists):
  Either "word" or "word count" per line — both are handled.
"""

import logging
import threading
from pathlib import Path
from typing import FrozenSet, Optional

logger = logging.getLogger(__name__)


class WordChecker:
    """Thread-safe lazy-loading word list checker for a single language."""

    def __init__(self, filename: str, lang: str) -> None:
        self._filename = filename
        self._lang = lang
        self._dict: Optional[FrozenSet[str]] = None
        self._lock = threading.Lock()
        self._failed = False

    # ── loading ────────────────────────────────────────────────────────────

    def _dict_path(self) -> Path:
        return Path(__file__).parent.parent / "data" / self._filename

    def _load(self) -> Optional[FrozenSet[str]]:
        if self._dict is not None or self._failed:
            return self._dict

        with self._lock:
            if self._dict is not None or self._failed:
                return self._dict

            path = self._dict_path()
            if not path.exists():
                logger.warning(
                    "%s word list not found at %s. "
                    "Run scripts/download_lang_dicts.py to download it. "
                    "%s word validation disabled.",
                    self._lang, path, self._lang,
                )
                self._failed = True
                return None

            import time
            t0 = time.monotonic()
            try:
                words: set = set()
                with open(path, encoding="utf-8") as fh:
                    for line in fh:
                        stripped = line.strip()
                        if not stripped:
                            continue
                        # Accept "word" or "word count" format
                        word = stripped.split()[0].lower()
                        if len(word) >= 3 and word.isalpha():
                            words.add(word)
                result: FrozenSet[str] = frozenset(words)
                elapsed = time.monotonic() - t0
                logger.info(
                    "Loaded %s %s words in %.1fs (~%d MB)",
                    f"{len(result):,}", self._lang, elapsed,
                    len(result) * 20 // 1_048_576,
                )
                self._dict = result
                return result
            except Exception as exc:
                logger.error("Failed to load %s word list: %s", self._lang, exc)
                self._failed = True
                return None

    # ── public API ─────────────────────────────────────────────────────────

    def word_in(self, word: str) -> Optional[bool]:
        """Return True/False if word is/isn't in the dict, None if dict unavailable."""
        d = self._load()
        if d is None:
            return None
        cleaned = word.strip(".,!?-:;«»\"'()[]{}…—–").lower()
        return (cleaned in d) if cleaned else None

    def text_has_words(self, text: str, threshold: int = 4) -> bool:
        """Return True if *text* contains at least *threshold* recognised words.

        Skips proper nouns (first char uppercase), ALL-CAPS tokens, tokens
        shorter than 4 chars, and non-alphabetic tokens.
        """
        if not text:
            return False
        d = self._load()
        if d is None:
            return False
        hits = 0
        for token in text.split():
            raw = token.strip(".,!?-:;«»\"'()[]{}…—–")
            if not raw or len(raw) < 4:
                continue
            if not raw.isalpha():
                continue
            if raw[0].isupper() or raw.isupper():
                continue
            if raw.lower() in d:
                hits += 1
                if hits >= threshold:
                    return True
        return False

    def is_loaded(self) -> bool:
        return self._load() is not None

    def preload(self) -> None:
        """Start a background thread to load the dictionary."""
        threading.Thread(
            target=self._load,
            daemon=True,
            name=f"{self._lang.lower()}-dict-preload",
        ).start()
