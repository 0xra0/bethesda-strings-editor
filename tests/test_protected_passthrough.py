"""
A deliberate term-protection passthrough must never be mistaken for a model failure.

Background — ``quality_report_20260810_093056`` over a real ru→uk
``starfield_ru.STRINGS.xml`` run exported 343 rows with an empty ``<Dest>``.
296 of them came out of this chain, in which every step is locally correct:

  1. ``TermProtector`` matches a term and replaces it with ``[[TK_…]]``.  When the
     match covers the *whole* string, the model is handed a bare token.
  2. The model returns the token untouched — there is nothing else to do.
  3. ``restore_text`` puts the original term back, so the result is byte-identical
     to the source *by construction*.
  4. ``_is_untranslated_echo`` sees a verbatim copy carrying Russian-only letters
     (ы/э/ё/ъ) and concludes the model never translated it, so ``_translate_single``
     returns ``None`` and the row is left blank — for good, since a re-run repeats
     the same steps.

Neither subsystem is wrong on its own; they simply had no way to tell each other
that the verbatim-ness was intended.  So protection coverage is computed once and
consulted at both echo-guard call sites, and a fully-covered string short-circuits
before the network call (there is nothing to ask the model).

Also covers the second, independent blanking path in ``_clean_translation``: its
"garbage detection" shrink guards were not gated on ``closely_related`` the way
their immediate neighbours are, so a correct and legitimately shorter East-Slavic
translation was discarded — «Сопротивление» → «Опір» became "".

Everything here runs on ``OllamaWorker.__new__`` (no QThread, no QApplication) or
on class/staticmethods, and makes no network calls.

Run with:
    python -m pytest tests/test_protected_passthrough.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from gui.ollama_worker import OllamaWorker as W  # noqa: E402
from gui.term_protector import SOFT_CATEGORIES, TermProtector  # noqa: E402


# ── protection coverage: a pure predicate over (protected text, token map) ─────

@pytest.mark.parametrize("protected,token_map", [
    ("[[TK_3f503f_0]]",                    {"[[TK_3f503f_0]]": "Брэдбери I"}),
    ("  [[TK_3f503f_0]]  ",                {"[[TK_3f503f_0]]": "Брэдбери I"}),
    # Nothing but tokens and punctuation is left to translate.
    ("[[TK_a_0]] - [[TK_b_0]]",            {"[[TK_a_0]]": "Эридани", "[[TK_b_0]]": "VIII"}),
    ("__EN900000__",                       {"__EN900000__": "Sarah Morgan"}),
])
def test_coverage_detected(protected, token_map):
    assert W._protection_covers_everything(protected, token_map) is True


@pytest.mark.parametrize("protected,token_map", [
    ("Отправиться на [[TK_x_0]]",          {"[[TK_x_0]]": "Арктур II"}),
    ("Аль-[[TK_x_0]]",                     {"[[TK_x_0]]": "Баттани I-b"}),
    ("Сопротивление",                      {}),
    ("",                                   {}),
])
def test_coverage_not_claimed_when_words_remain(protected, token_map):
    assert W._protection_covers_everything(protected, token_map) is False


# ── the echo guard itself still does its job ──────────────────────────────────

def test_echo_guard_still_blocks_a_genuine_untranslated_echo():
    """The guard exists because an echoed primary used to be fanned out to every
    dedup follower and replayed from cache.  Narrowing it must not disarm it."""
    assert W._is_untranslated_echo("Брэдбери I", "Брэдбери I", "ru", "uk")
    assert W._is_untranslated_echo("Open the door", "Open the door", "en", "uk")


def test_protected_verbatim_is_not_treated_as_an_echo():
    w = W.__new__(W)
    w.term_protector = TermProtector()
    w.enable_term_protection = True
    w.protect_named_entities = True          # designations locked

    # Fully covered → the verbatim result is intended, not a failure.
    assert w._is_protected_verbatim("Брэдбери I") is True
    # Only part of the string is covered → a verbatim copy really is a failure.
    assert w._is_protected_verbatim("Отправиться на Арктур II") is False


def test_no_protection_means_no_passthrough_claim():
    """With protection off nothing is covered, so the guard keeps full authority."""
    w = W.__new__(W)
    w.term_protector = TermProtector()
    w.enable_term_protection = False
    w.protect_named_entities = True
    assert w._is_protected_verbatim("Брэдбери I") is False


def test_named_entity_setting_reaches_the_predicate():
    """With "Protect named entities" off, «Брэдбери I» is sent to the model, so a
    verbatim reply is a real failure and must stay blockable."""
    w = W.__new__(W)
    w.term_protector = TermProtector()
    w.enable_term_protection = True
    w.protect_named_entities = False         # soft categories excluded
    assert SOFT_CATEGORIES  # sanity: the exclusion list is non-empty
    assert w._is_protected_verbatim("Брэдбери I") is False


# ── _clean_translation must not discard correct, shorter East-Slavic output ────

@pytest.mark.parametrize("source,translated", [
    ("Сопротивление", "Опір"),        # 13 -> 4  (resistance)
    ("Стрельбище",    "Тир"),         # 10 -> 3  (shooting range)
    ("Станция Хаб",   "Хаб"),         # 11 -> 3
    ("Помещение",     "Зала"),
    ("Способности",   "Хист"),
])
def test_short_east_slavic_translation_is_kept(source, translated):
    w = W.__new__(W)
    assert w._clean_translation(translated, "uk", source, 0, source_lang="ru") == translated


def test_long_east_slavic_source_may_still_shrink():
    """A 0.12× floor on a >15-char source blanked valid compressions too."""
    w = W.__new__(W)
    src = "Сопротивление электрическому току"     # 33 chars
    assert w._clean_translation("Опір", "uk", src, 0, source_lang="ru") == "Опір"


@pytest.mark.parametrize("source,translated", [
    # Unrelated language pair: a 3-char reply to a 13-char source is garbage,
    # and the guard must still say so.
    ("Reinforcements", "Пі"),
    ("Communications", "aa"),
])
def test_unrelated_pair_garbage_is_still_blanked(source, translated):
    w = W.__new__(W)
    assert w._clean_translation(translated, "uk", source, 0, source_lang="en") == ""
