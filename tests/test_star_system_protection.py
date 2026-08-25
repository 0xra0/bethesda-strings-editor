"""
Star-system-name protection must lock a *designation*, never a sentence.

Background — from ``quality_report_20260810_093056`` over a real ru→uk
``starfield_ru.STRINGS.xml`` run: 343 rows exported with an empty ``<Dest>``.
338 of them had been matched by the ``star_system_name`` structural pattern,
which was

    \\b[А-ЯЁа-яёЄєІіЇїҐґ]+(?:\\s+[А-ЯЁа-яёЄєІіЇїҐґ]+)*\\s+[IVXLCDM]+(?:[-–—]\\w+)?\\b

— *any* run of Cyrillic words followed by anything spelled out of I V X L C D M.
Because a lone ``C`` or ``D`` qualifies (and ``CD`` does too), that swallowed
ordinary translatable phrases whole: 6,051 rows — 12 % of the shipped file —
reached the model as a single ``[[TK_…]]`` token with nothing left to translate.

    'Портативный CD-плеер'                          (a portable CD player)
    'Шкура ходока четырёхногих карпов C'
    'Активировать вспомогательное питание блока D'
    'Отправиться на Арктур II'                      (the verb never translated)

A real designation is one capitalised Cyrillic word (internal hyphens allowed,
for ``Аль-Баттани``), a well-formed Roman numeral, and an optional single-letter
body suffix — ``a``–``f`` are the only suffix letters the shipped file uses.

The category also belongs in ``SOFT_CATEGORIES``: a star system name is a proper
noun, so the "Protect named entities" setting must govern it.  Leaving it among
the structural patterns (``<Alias=…>``, ``%s``) made it unconditional, which is
why a ru→uk run could not ask for «Брэдбери I» → «Бредбері I».

Pure regex/set assertions — no Qt, no model, no game files.

Run with:
    python -m pytest tests/test_star_system_protection.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from gui.term_protector import SOFT_CATEGORIES, TermProtector  # noqa: E402


def _pattern():
    for pat, category in TermProtector.STRUCTURAL_PATTERNS:
        if category == "star_system_name":
            import re
            return re.compile(pat)
    raise AssertionError("star_system_name pattern not found")


def _match(text):
    m = _pattern().search(text)
    return m.group(0) if m else None


# ── real designations stay protected in full ───────────────────────────────────

@pytest.mark.parametrize("designation", [
    "Брэдбери I",
    "Эридани VIII-c",
    "Желязны VII-a",
    "Аль-Баттани I-b",       # internal hyphen is part of the name
    "Эридани IX",
    "Энлиль VI-d",
    "Кита II",
])
def test_real_designation_is_protected_whole(designation):
    assert _match(designation) == designation


# ── a sentence around a designation keeps its translatable words ───────────────

@pytest.mark.parametrize("sentence,expected", [
    ("Отправиться на Арктур II",             "Арктур II"),
    ("Отправиться к Килю III-a",             "Килю III-a"),
    ("Отправиться на планету Фрейя III",     "Фрейя III"),
    ("Доберитесь до Горнила на Харибде III", "Харибде III"),
    # A descriptive leading word is translatable: «Малый» → «Малий».
    ("Малый Коперник I-b",                   "Коперник I-b"),
    ("Проксима Центавра III",                "Центавра III"),
])
def test_only_the_designation_is_protected(sentence, expected):
    assert _match(sentence) == expected


# ── a lone C/D/L/M is not a planet designation ─────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "Шкура ходока четырёхногих карпов C",
    "СБ Содружество Транспортный C",
    "Авангард ОК Боевой C",
    "Активировать вспомогательное питание блока D",
    "Войти в сторожевую башню блока D",
    "Пройти в блок D",
    "Портативный CD-плеер",          # 'CD' is spelled from IVXLCDM but is not a numeral
    "Рабочее место поста охраны D-Block",
])
def test_stray_capital_letter_is_not_a_designation(phrase):
    assert _match(phrase) is None


@pytest.mark.parametrize("phrase", [
    "Сопротивление",
    "Станция Хаб",
    "Стрельбище",
])
def test_plain_phrases_are_untouched(phrase):
    assert _match(phrase) is None


# ── malformed numerals are rejected ────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "Эридани IIII",     # not a well-formed Roman numeral
    "Эридани Vault",    # 'V' followed by more letters is a word, not a numeral
    "Эридани XXXX",
])
def test_malformed_numeral_is_not_a_designation(phrase):
    assert _match(phrase) is None


# ── the whole string is only tokenised when it really is only a designation ────

def test_protect_text_leaves_verb_translatable():
    tp = TermProtector()
    protected, token_map = tp.protect_text("Отправиться на Арктур II")
    assert "Отправиться на " in protected
    assert len(token_map) == 1
    assert tp.restore_text(protected, token_map) == "Отправиться на Арктур II"


# ── the category is a named entity, so the user setting must reach it ──────────

def test_star_system_name_is_a_soft_category():
    assert "star_system_name" in SOFT_CATEGORIES


def test_excluding_soft_categories_lets_a_designation_through():
    """With "Protect named entities" off, «Брэдбери I» must reach the model so a
    ru→uk run can transliterate it to «Бредбері I» instead of echoing Russian."""
    tp = TermProtector()
    protected, token_map = tp.protect_text(
        "Брэдбери I", exclude_categories=list(SOFT_CATEGORIES)
    )
    assert protected == "Брэдбери I"
    assert token_map == {}
