"""
Tests for OllamaWorker._protect_english_text — the RU→UK "Protect English text"
safety net — and its glossary-aware localization (_glossary_localize).

Regression target: character and planet names left in a Russian source used to
be blanket-protected (kept English) by the old "Rule 4: any Capitalised Latin
word is a proper noun → protect".  A full localization wants those transliterated
into the target script instead.  New behaviour:

  * Capitalised proper nouns with NO glossary entry are NOT protected (the AI
    transliterates them).
  * ALL-CAPS game codes / acronyms are still protected (stay English).
  * Lowercase English content words are still protected (existing behaviour).
  * Glossary terms are substituted with their prescribed target-language form
    *deterministically* (no model call) and hidden behind a restore token, so a
    user can pin an exact spelling (e.g. "Sarah Morgan" → "Сара Морган") and
    multi-word names are handled.

_protect_english_text only touches self.glossary_manager and the class-level
regex, so we exercise it on an un-__init__'d instance (no QThread / QApplication).

Run with:
    python -m pytest tests/test_ollama_english_protection.py -v
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.ollama_worker import OllamaWorker  # noqa: E402


# ── test doubles ────────────────────────────────────────────────────────────────

class _FakeEntry:
    def __init__(self, source_term: str, target_term: str):
        self.source_term = source_term
        self.target_term = target_term


class _FakeGlossary:
    """Minimal stand-in for GlossaryManager.find_terms_in_text.

    Word-boundary, case-insensitive matching over a {source: target} map — mirrors
    the real manager closely enough for these unit tests.
    """

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def find_terms_in_text(self, text: str):
        hits = []
        for src, tgt in self._mapping.items():
            for m in re.finditer(r"\b" + re.escape(src) + r"\b", text, re.IGNORECASE):
                hits.append((m.start(), m.end(), _FakeEntry(src, tgt)))
        hits.sort(key=lambda h: h[0])
        return hits


def _worker(glossary=None) -> OllamaWorker:
    w = OllamaWorker.__new__(OllamaWorker)  # skip QThread.__init__
    w.glossary_manager = glossary
    return w


def _restore(text: str, token_map: dict) -> str:
    """Mirror the worker's fallback token→value restore."""
    for tok, val in token_map.items():
        text = text.replace(tok, val)
    return text


# ── capitalised proper nouns are NOT protected (the core fix) ────────────────────

def test_capitalised_name_not_protected_without_glossary():
    text = "Найди Sarah Morgan на New Atlantis."
    out, tm = _worker()._protect_english_text(text)
    # Nothing tokenised — every name flows through to the AI for transliteration.
    assert tm == {}
    assert out == text


def test_planet_name_not_protected():
    text = "Отправляйся на Jemison сейчас."
    out, tm = _worker()._protect_english_text(text)
    assert "Jemison" in out
    assert tm == {}


# ── ALL-CAPS codes / acronyms ARE still protected ───────────────────────────────

def test_all_caps_code_protected():
    text = "Открой HUD немедленно."
    out, tm = _worker()._protect_english_text(text)
    assert "HUD" not in out
    assert len(tm) == 1
    (tok, val), = tm.items()
    assert val == "HUD"
    assert tok in out
    # Round-trips back to the original English code.
    assert "HUD" in _restore(out, tm)


def test_mixed_name_and_code():
    # "Sarah" (name) translates; "SysDef" is not all-caps → also translates;
    # "UC" (all-caps acronym) is protected.
    text = "Sarah из UC связалась с SysDef."
    out, tm = _worker()._protect_english_text(text)
    assert len(tm) == 1
    (_, val), = tm.items()
    assert val == "UC"
    assert "Sarah" in out
    assert "SysDef" in out
    assert "UC" not in out


# ── lowercase English content words are still protected ─────────────────────────

def test_lowercase_content_word_protected():
    text = "Активируй reactor немедленно."
    out, tm = _worker()._protect_english_text(text)
    assert "reactor" not in out
    assert list(tm.values()) == ["reactor"]
    assert "reactor" in _restore(out, tm)


# ── glossary localization ───────────────────────────────────────────────────────

def test_glossary_substitutes_target_form():
    gm = _FakeGlossary({"Sarah Morgan": "Сара Морган"})
    text = "Найди Sarah Morgan быстро."
    out, tm = _worker(gm)._protect_english_text(text)
    assert "Sarah Morgan" not in out
    assert list(tm.values()) == ["Сара Морган"]
    assert _restore(out, tm) == "Найди Сара Морган быстро."


def test_glossary_pins_only_listed_names_others_translate():
    gm = _FakeGlossary({"Sarah Morgan": "Сара Морган"})
    text = "Иди к Sarah Morgan в New Atlantis сейчас."
    out, tm = _worker(gm)._protect_english_text(text)
    # Pinned name → one token to its Ukrainian form.
    assert list(tm.values()) == ["Сара Морган"]
    # Un-pinned capitalised place is left for the AI (not tokenised).
    assert "New Atlantis" in out
    assert "Sarah Morgan" not in out


def test_glossary_beats_all_caps_protection():
    # A glossary-pinned ALL-CAPS acronym is localized, not kept English.
    gm = _FakeGlossary({"UC": "ОК"})
    text = "Флот UC атакует."
    out, tm = _worker(gm)._protect_english_text(text)
    assert "UC" not in out
    assert list(tm.values()) == ["ОК"]
    assert _restore(out, tm) == "Флот ОК атакует."


def test_glossary_empty_target_not_substituted():
    # Protect-only entry (blank target) is ignored here; the capitalised word
    # then follows Rule 4 and translates (no token created).
    gm = _FakeGlossary({"Xbox": ""})
    text = "Запусти Xbox."
    out, tm = _worker(gm)._protect_english_text(text)
    assert tm == {}
    assert out == text


def test_glossary_missing_manager_is_safe():
    # No glossary manager attribute at all → no crash, names still translate.
    w = OllamaWorker.__new__(OllamaWorker)
    out, tm = w._protect_english_text("Позови Barrett.")
    assert tm == {}
    assert out == "Позови Barrett."


def test_glossary_lookup_error_is_swallowed():
    class _Boom:
        def find_terms_in_text(self, _text):
            raise RuntimeError("boom")

    out, tm = _worker(_Boom())._protect_english_text("Позови Sam Coe.")
    # Falls back gracefully: capitalised names untouched, no crash.
    assert tm == {}
    assert out == "Позови Sam Coe."


def test_all_caps_still_protected_with_unrelated_glossary():
    gm = _FakeGlossary({"Constellation": "Сузір'я"})
    text = "Открой HUD."
    out, tm = _worker(gm)._protect_english_text(text)
    assert list(tm.values()) == ["HUD"]
    assert "HUD" not in out
