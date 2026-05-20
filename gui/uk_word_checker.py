"""
Ukrainian word dictionary for validating Ukrainian translations.

Loads data/ukrainian_words.txt (built by scripts/build_uk_dict.py) lazily on
first use and caches as a module-level frozenset so repeated lookups are O(1).
"""

import logging
import threading
from pathlib import Path
from typing import FrozenSet, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Russian-only characters — a word containing any of these is definitely not
# Ukrainian and should be skipped during Ukrainian validation.
# ---------------------------------------------------------------------------
_RU_ONLY: frozenset = frozenset("ыэёъЫЭЁЪ")

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------
_uk_dict: Optional[FrozenSet[str]] = None
_load_lock = threading.Lock()
_load_failed = False


def _dict_path() -> Path:
    return Path(__file__).parent.parent / "data" / "ukrainian_words.txt"


def _load_dict() -> Optional[FrozenSet[str]]:
    """Load and cache the Ukrainian word list (called at most once)."""
    global _uk_dict, _load_failed

    if _uk_dict is not None or _load_failed:
        return _uk_dict

    with _load_lock:
        if _uk_dict is not None or _load_failed:
            return _uk_dict

        path = _dict_path()
        if not path.exists():
            logger.warning(
                f"Ukrainian word list not found at {path}. "
                "Run scripts/build_uk_dict.py to build it. "
                "Ukrainian word validation disabled."
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
                    if len(ln.strip()) >= 3
                )
            elapsed = time.monotonic() - t0
            logger.info(
                f"Loaded {len(words):,} Ukrainian words in {elapsed:.1f}s "
                f"(~{len(words) * 20 // 1_048_576} MB)"
            )
            _uk_dict = words
            return words
        except Exception as exc:
            logger.error(f"Failed to load Ukrainian word list: {exc}")
            _load_failed = True
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def word_is_ukrainian(word: str) -> Optional[bool]:
    """
    Return True if *word* is in the Ukrainian dictionary, False if not,
    None if the dictionary has not been loaded (file missing).

    The lookup is case-insensitive and strips leading/trailing punctuation.
    """
    uk = _load_dict()
    if uk is None:
        return None
    cleaned = word.strip(".,!?-:;«»\"'()[]{}…—–").lower()
    if not cleaned:
        return None
    return cleaned in uk


def dict_loaded() -> bool:
    """Return True if the dictionary is available."""
    return _load_dict() is not None


def preload() -> None:
    """Trigger dictionary load in a background thread."""
    threading.Thread(target=_load_dict, daemon=True, name="uk-dict-preload").start()
