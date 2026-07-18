"""
Tests for language-gated word-list preloading.

All nine frequency lists used to load on every launch regardless of the language
pair.  They are not small — Russian alone is a 35 MB file, and the in-memory set
costs several times that — so a German→English session paid for Ukrainian,
Polish, Portuguese and the rest, permanently, for nothing.

Pure Python; the preloaders are replaced with recorders so no dictionary is
actually read.
"""

import pytest

import gui.ollama_worker as ow


@pytest.fixture
def loaded(monkeypatch):
    """Record which languages get warmed, without touching the real lists."""
    calls = []
    fake = {
        lang: (lambda lang=lang: calls.append(lang))
        for lang in ow._DICT_PRELOADERS
    }
    monkeypatch.setattr(ow, "_DICT_PRELOADERS", fake)

    def run(source_lang, target_lang):
        calls.clear()
        ow.preload_language_dictionaries(source_lang, target_lang)
        return set(calls)

    return run


def test_loads_only_the_pair_plus_english(loaded):
    assert loaded("de", "pl") == {"de", "pl", "en"}


def test_does_not_load_russian_for_an_unrelated_pair(loaded):
    """The 35 MB list is the whole reason this gate exists."""
    assert "ru" not in loaded("de", "pl")
    assert "ru" not in loaded("en", "ko")


def test_english_is_always_loaded(loaded):
    """It backs the English-leak check and the English-protection pass."""
    assert "en" in loaded("de", "pl")
    assert "en" in loaded("ru", "uk")
    assert "en" in loaded("ko", "ja")


def test_russian_is_loaded_for_a_ukrainian_target(loaded):
    """Ukrainian output is checked for Russian-word leakage."""
    assert loaded("en", "uk") == {"en", "uk", "ru"}


def test_korean_target_loads_korean(loaded):
    assert loaded("en", "ko") == {"en", "ko"}


def test_languages_without_a_word_list_are_skipped(loaded):
    """ja and zhhans ship no frequency list — asking for them must not raise."""
    assert loaded("ja", "zhhans") == {"en"}


def test_language_codes_are_case_insensitive(loaded):
    assert loaded("DE", "PL") == {"de", "pl", "en"}


def test_missing_language_codes_are_tolerated(loaded):
    assert loaded("", "") == {"en"}
    assert loaded(None, None) == {"en"}


def test_preload_is_idempotent_once_loaded(monkeypatch):
    """Called at every batch start, so repeating it must not spawn threads."""
    from gui._word_checker_base import WordChecker

    checker = WordChecker("nonexistent_words.txt", "Test")
    spawned = []
    monkeypatch.setattr(
        "gui._word_checker_base.threading.Thread",
        lambda **kw: type("T", (), {"start": lambda self: spawned.append(1)})(),
    )

    checker.preload()
    assert len(spawned) == 1

    checker._failed = True          # as _load() would leave it
    checker.preload()
    checker.preload()
    assert len(spawned) == 1
