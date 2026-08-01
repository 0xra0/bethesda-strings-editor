"""
Tests that the Ukrainian gender checker reads one shared word list.

``gender_checker`` used to load ``data/ukrainian_words.txt`` into a private set
of its own while ``uk_word_checker`` held the very same 271 k words in another.
A session that ran the gender check as well as Ukrainian leak detection paid
~78 MB for two identical copies.  The sets were interchangeable — the file has
no uppercase entries and none shorter than three characters, which is all the
checker's loader normalises away — so the fix was simply to look words up
through ``uk_word_checker``.

These tests pin the read-through: they replace the shared dictionary with a
handful of words and require the gender checker's verdicts to follow it.  A
module that had gone back to loading its own copy would ignore the replacement
and keep answering from the real file, failing every case here.

Pure Python; no Qt, and the real word list is never read.
"""

import pytest

import gui.gender_checker as gc
import gui.uk_word_checker as ukw


@pytest.fixture
def shared_dict(monkeypatch):
    """Install a tiny stand-in for the one shared Ukrainian dictionary."""

    def install(*words):
        monkeypatch.setattr(ukw, "_uk_dict", frozenset(words))
        monkeypatch.setattr(ukw, "_load_failed", False)

    return install


def test_neuter_adjective_resolves_through_the_shared_dictionary(shared_dict):
    # 'нове' is neuter only because 'новий' is a real word.
    shared_dict("новий")
    assert gc._is_neut_adj("нове") is True
    assert gc._adj_gender("нове") == gc.N


def test_soft_stem_is_accepted_too(shared_dict):
    shared_dict("синій")
    assert gc._is_neut_adj("синє") is True


def test_verb_form_is_rejected(shared_dict):
    # 'переможе' is a verb: neither 'переможий' nor 'переможій' exists.
    shared_dict("новий", "синій")
    assert gc._is_neut_adj("переможе") is False
    assert gc._adj_gender("переможе") is None


def test_verdict_follows_the_shared_dictionary_not_a_private_copy(shared_dict):
    """The load-bearing one: empty the shared dict and the answer must change."""
    shared_dict("новий")
    assert gc._is_neut_adj("нове") is True

    shared_dict()   # same word, dictionary now empty
    assert gc._is_neut_adj("нове") is False


def test_known_neuter_noun_never_counts_as_an_adjective(shared_dict):
    # 'місце' is in _NEUT_NOUN_E and must short-circuit before any lookup,
    # even when a matching stem would otherwise be found.
    shared_dict("місций", "місцій")
    assert gc._is_neut_adj("місце") is False


def test_missing_word_list_means_not_an_adjective_rather_than_a_crash(monkeypatch):
    """A missing file made the old code fall back to an empty set; the shared
    lookup returns None instead, which must be treated the same way."""
    monkeypatch.setattr(ukw, "_uk_dict", None)
    monkeypatch.setattr(ukw, "_load_failed", True)   # as if the file were absent
    assert ukw.word_is_ukrainian("новий") is None
    assert gc._is_neut_adj("нове") is False


def test_gender_checker_keeps_no_word_set_of_its_own():
    """No module-level container large enough to be a second copy of the list."""
    big = [
        name for name, value in vars(gc).items()
        if isinstance(value, (set, frozenset, dict, list)) and len(value) > 5_000
    ]
    assert big == []
