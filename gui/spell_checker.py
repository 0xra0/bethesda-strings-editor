"""
Hunspell spell-checker wrapper for translated game strings.

Tries three backends in priority order:
  1. ``hunspell`` pip package  — fastest (C bindings): pip install hunspell
  2. ``spylls`` pip package    — pure Python Hunspell:  pip install spylls
  3. hunspell CLI subprocess   — no Python package needed; uses system hunspell

Dictionary installation (one-time per language):
  Arch:          pacman -S hunspell-uk hunspell-de hunspell-fr hunspell-es ...
  Debian/Ubuntu: apt install hunspell-uk hunspell-de hunspell-fr hunspell-es ...
  Fedora:        dnf install hunspell-uk hunspell-de hunspell-fr ...

If no backend or dictionary is available the checker silently returns [].
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Language code → hunspell dictionary name ──────────────────────────────────
LANG_TO_DICT: Dict[str, str] = {
    "uk": "uk_UA", "ukrainian": "uk_UA",
    "de": "de_DE", "german": "de_DE",
    "fr": "fr_FR", "french": "fr_FR",
    "es": "es_ES", "spanish": "es_ES",
    "pl": "pl_PL", "polish": "pl_PL",
    "cs": "cs_CZ", "czech": "cs_CZ",
    "ru": "ru_RU", "russian": "ru_RU",
    "it": "it_IT", "italian": "it_IT",
    "en": "en_US", "english": "en_US",
    "ptbr": "pt_BR", "portuguese": "pt_BR",
}

# ── Standard paths to search for .dic / .aff files ────────────────────────────
def _build_dict_search_paths() -> List[Path]:
    """Locations to search for Hunspell .dic/.aff pairs, per platform.

    Order: app-bundled dicts → per-user dir → OS system locations.  Linux keeps
    its long-standing paths; Windows and macOS add their conventional Hunspell /
    LibreOffice directories so spell-check works there too.
    """
    home = Path.home()
    paths: List[Path] = []
    # App-bundled dictionaries (a packaged build can ship its own under dicts/).
    paths.append(Path(__file__).resolve().parent.parent / "dicts")
    if getattr(sys, "frozen", False):  # PyInstaller / frozen build
        paths.append(Path(sys.executable).resolve().parent / "dicts")
    # Per-user (works on every OS via Path.home()).
    paths += [home / ".local/share/hunspell", home / ".local/share/myspell"]

    if sys.platform == "win32":
        for base in (os.environ.get("PROGRAMFILES"),
                     os.environ.get("PROGRAMFILES(X86)")):
            if base:
                paths.append(Path(base) / "LibreOffice" / "share" / "extensions")
        appdata = os.environ.get("APPDATA")
        if appdata:
            paths.append(Path(appdata) / "hunspell")
    elif sys.platform == "darwin":
        paths += [
            Path("/Library/Spelling"),
            home / "Library" / "Spelling",
            Path("/opt/homebrew/share/hunspell"),
            Path("/usr/local/share/hunspell"),
            Path("/Applications/LibreOffice.app/Contents/Resources/extensions"),
        ]
    else:  # Linux / other Unix
        paths += [
            Path("/usr/share/hunspell"),
            Path("/usr/share/myspell/dicts"),
            Path("/usr/share/myspell"),
            Path("/usr/lib/hunspell"),
            Path("/usr/lib/libreoffice/share/extensions"),
            Path("/usr/share/libreoffice/share/extensions"),
        ]
    return paths


_DICT_SEARCH_PATHS: List[Path] = _build_dict_search_paths()

# ── Strip game tokens before spell-checking ───────────────────────────────────
_STRIP_RE = re.compile(
    r"<[^>]+>"                                       # <Alias=...>, <br/>, etc.
    r"|\[[A-Z][A-Za-z0-9_/]*\]"                     # [Attack], [OPTIMIZED], [DataMenu]
    r"|\[tk_[A-Za-z0-9_]*\]"                        # [tk_something]
    r"|%[-+0 #]*\d*(?:\.\d+)?[sdfoxXciuFeEgGp%]"   # printf: %s, %.0f, etc.
    r"|\{[^}]+\}"                                    # {variable}
)

_PUNCT = ".,!?-:;«»\"'()[]{}…—–\n\t\r"


def _find_dict_files(dict_name: str) -> Optional[Tuple[str, str]]:
    """Return (dic_path, aff_path) strings if found on disk, else None."""
    for base in _DICT_SEARCH_PATHS:
        if not base.exists():
            continue
        dic = base / f"{dict_name}.dic"
        aff = base / f"{dict_name}.aff"
        if dic.exists() and aff.exists():
            return str(dic), str(aff)
        # Search one level deep (LibreOffice extension layout: dict-uk/uk_UA.dic)
        try:
            for sub in base.iterdir():
                if not sub.is_dir():
                    continue
                dic = sub / f"{dict_name}.dic"
                aff = sub / f"{dict_name}.aff"
                if dic.exists() and aff.exists():
                    return str(dic), str(aff)
        except PermissionError:
            pass
    return None


def _candidate_words(text: str, source_words: frozenset) -> List[str]:
    """
    Extract lowercase words from *text* that are worth spell-checking.

    Excluded:
    - ALL-CAPS tokens (acronyms, game codes)
    - Words starting with uppercase (proper nouns — enormous FP rate in game text
      where every NPC, location, and faction name is capitalised)
    - Words shorter than 3 characters
    - Words containing digits
    - Words already present verbatim in the source text (game terms, brand names)
    """
    clean = _STRIP_RE.sub(" ", text)
    seen: set = set()
    result: List[str] = []
    for token in clean.split():
        word = token.strip(_PUNCT)
        if not word or len(word) < 3:
            continue
        if word.isupper():
            continue
        if word[0].isupper():
            continue  # proper noun / sentence-start — skip to avoid FPs
        if any(c.isdigit() for c in word):
            continue
        wl = word.lower()
        if wl in seen or wl in source_words:
            continue
        seen.add(wl)
        result.append(word)
    return result


# ── Backend implementations ───────────────────────────────────────────────────

class _HunspellLibBackend:
    """Uses the `hunspell` pip package (C bindings — fastest)."""

    def __init__(self, dic: str, aff: str) -> None:
        import hunspell as _hl  # type: ignore[import-untyped]
        self._h = _hl.HunSpell(dic, aff)

    def check(self, word: str) -> bool:
        return bool(self._h.spell(word))

    def suggest(self, word: str) -> List[str]:
        return list(self._h.suggest(word))[:3]


class _SpyllsBackend:
    """Uses the `spylls` pip package (pure Python Hunspell)."""

    def __init__(self, dic: str, _aff: str) -> None:
        from spylls.hunspell import Dictionary  # type: ignore[import-untyped]
        self._d = Dictionary.from_files(dic.removesuffix(".dic"))

    def check(self, word: str) -> bool:
        return bool(self._d.lookup(word))

    def suggest(self, word: str) -> List[str]:
        return list(self._d.suggest(word))[:3]


class _CliBackend:
    """
    Uses the hunspell CLI subprocess via ispell pipe protocol (-a flag).

    No Python package needed; works if hunspell is installed system-wide.
    Batches all words into a single subprocess call per check() invocation.
    """

    def __init__(self, dict_name: str) -> None:
        self._dict_name = dict_name
        # Probe: ensure the dictionary is actually installed
        probe = subprocess.run(
            ["hunspell", "-d", dict_name, "-a"],
            input="test\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "Can't open" in probe.stderr or "Can't open" in probe.stdout:
            raise RuntimeError(f"Dictionary not found: {dict_name}")

    def check_batch(self, words: List[str]) -> Dict[str, List[str]]:
        """Return {misspelled_word: [suggestions]} for each error in *words*."""
        if not words:
            return {}
        try:
            result = subprocess.run(
                ["hunspell", "-d", self._dict_name, "-a"],
                input="\n".join(words) + "\n",
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return {}

        errors: Dict[str, List[str]] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("& "):
                # Format: & WORD count offset: s1, s2, s3
                parts = line[2:].split(" ", 1)
                word = parts[0] if parts else ""
                suggs_raw = line.split(":", 1)[1].strip() if ":" in line else ""
                suggs = [s.strip() for s in suggs_raw.split(",")][:3] if suggs_raw else []
                if word:
                    errors[word] = suggs
            elif line.startswith("# "):
                # Format: # WORD offset
                parts = line[2:].split(" ", 1)
                word = parts[0] if parts else ""
                if word:
                    errors[word] = []
        return errors


# ── SpellChecker ──────────────────────────────────────────────────────────────

class SpellChecker:
    """
    Language-aware spell checker with lazy per-language dictionary loading.
    Thread-safe singleton (see module-level ``check_spelling`` / ``is_available``).
    """

    def __init__(self) -> None:
        self._backends: Dict[str, object] = {}  # lang_key → backend or None
        self._lock = threading.Lock()

    def _load(self, lang: str):
        """Load and return a backend for *lang*, or None if unavailable."""
        dict_name = LANG_TO_DICT.get(lang)
        if not dict_name:
            return None  # CJK or unknown — no hunspell dictionary exists

        dict_files = _find_dict_files(dict_name)

        if dict_files:
            dic, aff = dict_files
            for cls in (_HunspellLibBackend, _SpyllsBackend):
                try:
                    b = cls(dic, aff)
                    logger.info(
                        "Spell checker: %s loaded via %s from %s",
                        dict_name, cls.__name__, Path(dic).parent,
                    )
                    return b
                except ImportError:
                    pass
                except Exception as exc:
                    logger.debug("%s failed for %s: %s", cls.__name__, dict_name, exc)

        # Fall back to CLI — uses the system hunspell search path
        try:
            b = _CliBackend(dict_name)
            logger.info("Spell checker: %s loaded via hunspell CLI", dict_name)
            return b
        except Exception as exc:
            logger.debug("hunspell CLI unavailable for %s: %s", dict_name, exc)

        pkg = dict_name.split("_")[0].lower()
        logger.debug(
            "No spell-check dictionary for '%s'. "
            "Install with: pacman -S hunspell-%s  or  apt install hunspell-%s",
            lang, pkg, pkg,
        )
        return None

    def _get(self, lang: str):
        lang_key = lang.lower()
        if lang_key in self._backends:
            return self._backends[lang_key]
        with self._lock:
            if lang_key not in self._backends:
                self._backends[lang_key] = self._load(lang_key)
        return self._backends[lang_key]

    def check(
        self,
        text: str,
        lang: str,
        source_text: str = "",
    ) -> List[Tuple[str, List[str]]]:
        """
        Spell-check *text* in *lang*.

        Words found verbatim in *source_text* are excluded so game terms that
        survive translation unchanged are not flagged.

        Returns list of ``(misspelled_word, [suggestions])``.
        Returns ``[]`` if no dictionary is loaded for *lang*.
        """
        backend = self._get(lang)
        if backend is None:
            return []

        source_words = frozenset(
            t.strip(_PUNCT).lower()
            for t in source_text.split()
            if t.strip(_PUNCT)
        )
        candidates = _candidate_words(text, source_words)
        if not candidates:
            return []

        errors: List[Tuple[str, List[str]]] = []

        if isinstance(backend, _CliBackend):
            batch = backend.check_batch(candidates)
            for word in candidates:
                if word in batch:
                    errors.append((word, batch[word]))
        else:
            for word in candidates:
                try:
                    if not backend.check(word):
                        errors.append((word, backend.suggest(word)))
                except Exception:
                    pass

        return errors

    def is_available(self, lang: str) -> bool:
        """Return True if a dictionary is loaded for *lang*."""
        return self._get(lang) is not None


# ── Module-level convenience API ──────────────────────────────────────────────

_instance = SpellChecker()


def check_spelling(
    text: str,
    lang: str,
    source_text: str = "",
) -> List[Tuple[str, List[str]]]:
    """Check spelling of *text* in *lang*. Returns (word, suggestions) pairs."""
    return _instance.check(text, lang, source_text)


def is_available(lang: str) -> bool:
    """Return True if a spell-check dictionary is loaded for *lang*."""
    return _instance.is_available(lang)
