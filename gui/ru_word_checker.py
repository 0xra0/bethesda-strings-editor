"""
Russian word dictionary for detecting Russian text in Ukrainian translations.

Loads data/russian_words.txt (1.5 M inflected forms from Poliklot/russian-words)
lazily on first use and caches the result as a module-level frozenset so that
subsequent calls pay only an O(1) hash-set lookup per word.
"""

import logging
import threading
from pathlib import Path
from typing import FrozenSet, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ukrainian-specific characters — any word containing one of these cannot be
# a Russian word, so we skip it during lookup.
# ---------------------------------------------------------------------------
_UK_CHARS: frozenset = frozenset("іїєґІЇЄҐ")

# ---------------------------------------------------------------------------
# Ukrainian words without і/ї/є/ґ that still appear in the Russian dictionary
# (because Russian has Ukrainian loanwords, cognates, or archaic forms).
# Excluding them prevents false-positive Russian detection in Ukrainian text.
# ---------------------------------------------------------------------------
_UK_EXCLUSION: frozenset = frozenset({
    # Common function words / adverbs
    "дуже", "лише", "добре", "добра", "добро", "добрий",
    "щоб", "тому", "також", "теж", "хоча", "зараз", "поки",
    "доки", "куди", "мабуть", "просто", "ще", "треба", "хтось",
    "щось", "колись", "десь", "кудись", "разом", "одразу",
    "справді", "звідки", "досі", "завжди", "швидко",
    # Modal / impersonal
    "можна", "варто", "слід", "потрібно", "потрібна", "потрібний", "потрібне",
    # Adjectives (shared-looking forms that are still Ukrainian)
    "гарний", "гарна", "гарне", "новий", "нова", "нове",
    "великий", "велика", "велике", "малий", "мала", "мале",
    "старий", "стара", "старе", "старого", "старому", "старим",
    "молодий", "молода", "молоде", "спокійний", "спокійна",
    "складний", "складна", "складно",
    "перший", "перша", "перше", "першого", "першому",
    "другий", "другого", "другому",
    # Nouns (inflected forms found in test sentences)
    "боку", "моря", "морю", "шторму", "шторм", "страх", "страху",
    "доби", "справа", "справи", "справою", "команда", "команди",
    "команди", "командою", "мета", "метою", "робота", "роботи",
    "роботою", "школа", "школи", "школою", "думка",
    "угоду", "угоди", "угодою", "сторонам", "сторонами",
    "старту",
    # Verb forms (3rd sg / past that look the same in Russian)
    "входить", "виходить", "готова", "готово", "готові",
    "завершена", "стояв", "стояла", "стояло", "стоять",
    "була", "було", "були",
    "того",
})

# ---------------------------------------------------------------------------
# Module-level singleton state
# ---------------------------------------------------------------------------
_ru_dict: Optional[FrozenSet[str]] = None
_load_lock = threading.Lock()
_load_failed = False


def _dict_path() -> Path:
    return Path(__file__).parent.parent / "data" / "russian_words.txt"


def _load_dict() -> Optional[FrozenSet[str]]:
    """Load and cache the Russian word list (called at most once)."""
    global _ru_dict, _load_failed

    if _ru_dict is not None or _load_failed:
        return _ru_dict

    with _load_lock:
        if _ru_dict is not None or _load_failed:
            return _ru_dict

        path = _dict_path()
        if not path.exists():
            logger.warning(
                f"Russian word list not found at {path}. "
                "Dictionary-based Russian detection disabled."
            )
            _load_failed = True
            return None

        import time
        t0 = time.monotonic()
        try:
            with open(path, encoding="utf-8") as fh:
                words: FrozenSet[str] = frozenset(
                    line.strip().lower()
                    for line in fh
                    if len(line.strip()) >= 4 and line.strip().replace("-", "").isalpha()
                )
            elapsed = time.monotonic() - t0
            logger.info(
                f"Loaded {len(words):,} Russian words in {elapsed:.1f}s "
                f"(~{len(words) * 70 // 1_048_576} MB)"
            )
            _ru_dict = words
            return words
        except Exception as exc:
            logger.error(f"Failed to load Russian word list: {exc}")
            _load_failed = True
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def text_has_russian_words(text: str, threshold: int = 3) -> bool:
    """
    Return True if *text* contains at least *threshold* words that are
    exclusively Russian (present in the Russian dictionary, absent from the
    Ukrainian exclusion list, and containing no Ukrainian-specific characters).

    Proper nouns (words whose first letter is uppercase in the original text)
    are ignored to avoid false positives from character names.

    This check is designed to catch Russian text that has been partially
    "cleaned" (e.g. ы→и substitution) and therefore bypasses simple
    character-based detection.  It fires only when the text contains no
    Ukrainian-specific characters at all (і / ї / є / ґ), since their
    presence already confirms the text is at least partially Ukrainian.
    """
    if not text:
        return False

    # Quick pre-check: if the text already has Ukrainian-specific chars,
    # the dictionary check is not needed (RU-exclusive chars catch the rest).
    if any(c in _UK_CHARS for c in text):
        return False

    ru = _load_dict()
    if ru is None:
        return False

    hits = 0
    for token in text.split():
        # Strip punctuation
        raw = token.strip(".,!?-:;«»\"'()[]{}…—–")
        if not raw:
            continue
        # Skip proper nouns (names, locations — they appear in the Russian dict too)
        if raw[0].isupper():
            continue
        word = raw.lower()
        if len(word) < 4:
            continue
        # Skip words with Ukrainian-specific characters
        if any(c in _UK_CHARS for c in word):
            continue
        if word in ru and word not in _UK_EXCLUSION:
            hits += 1
            if hits >= threshold:
                return True

    return False


def preload() -> None:
    """Trigger dictionary load in a background thread so the first translation
    request doesn't pay the ~1 s startup cost."""
    threading.Thread(target=_load_dict, daemon=True, name="ru-dict-preload").start()
