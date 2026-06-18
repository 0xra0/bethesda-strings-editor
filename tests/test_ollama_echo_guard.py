"""
Tests for OllamaWorker's untranslated-echo guard and dropped-designator restore.

Background — these come straight from quality_report_20260618_182442 over a real
ru→uk starfield_ru.xml mamaylm run:

  * 1,228 flagged rows collapsed to 423 unique source strings the model left blank
    or echoed.  The echoes split into ~24 unique that are STILL Russian (carry
    ы/э/ё/ъ or distinctly-Russian words — a genuine "model echoed the source"
    defect) versus ~160 unique that are spelled identically in both languages
    (proper nouns/loanwords like "Ставка на ремонт") and are correct as-is.
  * dedup amplified each failure: one echoed primary was copied onto every
    duplicate row (the run had 21,909 dedup followers), and an echo that reached
    the translation cache was replayed on every subsequent run.

So the guard must (a) block a verbatim echo only when the source carries
source-language-specific evidence, (b) leave genuinely-identical strings alone,
and (c) the dropped leading record-code designator (`FB 441 :: `, `GLB-222 `)
must be restored.

_is_untranslated_echo only touches class-level attributes, and the restores are
class/staticmethods, so all run without constructing an OllamaWorker (needs a
QThread).

Run with:
    python -m pytest tests/test_ollama_echo_guard.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.ollama_worker import OllamaWorker as W  # noqa: E402


def _echo(src, tgt, sl="ru", tl="uk"):
    # _is_untranslated_echo is a classmethod (only reads class-level state).
    return W._is_untranslated_echo(src, tgt, sl, tl)


# ── echo guard: BLOCK still-Russian verbatim copies (real report rows) ──────────

def test_blocks_echo_with_russian_only_letter():
    # 'ё', 'э', 'ы' do not exist in Ukrainian → a verbatim copy was not translated.
    assert _echo("Шкура ходока четырёхногих карпов C", "Шкура ходока четырёхногих карпов C")
    assert _echo("Брэдбери I", "Брэдбери I")
    assert _echo("Малый Коперник I-b", "Малый Коперник I-b")


def test_blocks_english_echo_for_en_uk():
    assert _echo("Open the door", "Open the door", "en", "uk")


# ── echo guard: KEEP correct output ─────────────────────────────────────────────

def test_keeps_strings_identical_in_both_languages():
    # No Russian-only letters and no distinctly-Russian words → valid Ukrainian as-is.
    for s in ("Ставка на ремонт", "Графин для вина", "Пакет агента Плато",
              "Ракета, Атлатл 270К"):
        assert not _echo(s, s), s


def test_keeps_actual_translation():
    assert not _echo("Миротворец", "Миротворець")          # ru→uk, really translated
    assert not _echo("Open the door", "Відчинити двері", "en", "uk")


def test_empty_or_none_is_not_an_echo():
    assert not _echo("", "")
    assert not _echo("Текст", "")
    assert not _echo("ы", None)  # type: ignore[arg-type]


def test_whitespace_and_case_insensitive_match():
    # A copy that differs only by surrounding/collapsed whitespace or case is
    # still an echo when the source carries Russian-only evidence.
    assert _echo("Брэдбери  I", "  брэдбери i ")


# ── dropped leading designator restore ──────────────────────────────────────────

def test_restores_double_colon_designator():
    assert (W._restore_dropped_designator("Масив комунікацій", "FB 441 :: Массив коммуникаций")
            == "FB 441 :: Масив комунікацій")


def test_restores_quoted_name_designator():
    assert (W._restore_dropped_designator("«Перевага»", 'GLB-222 "Превосходство"')
            == "GLB-222 «Перевага»")


def test_designator_no_double_insert():
    # Code already present → unchanged.
    assert (W._restore_dropped_designator("FB 441 :: Масив", "FB 441 :: Массив")
            == "FB 441 :: Масив")


def test_designator_ignores_ordinary_prose():
    assert (W._restore_dropped_designator("Просто текст", "Обычный текст")
            == "Просто текст")
    # Lowercase / sentence start is not a record code.
    assert (W._restore_dropped_designator("Привіт світ", "Привет мир")
            == "Привіт світ")
