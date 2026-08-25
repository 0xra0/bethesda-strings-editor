"""
printf conversion characters are case-significant; the tag scanner treated them as
case-insensitive.

Background — the single MISSING_TAG error in ``quality_report_20260810_093056``
(string 45533) reported a missing ``%u``.  The source has no ``%u``.  What it has
is Starfield's deliberately-scrambled terminal text:

    Я .MG%UYOL;{M&:AF все генетические образцы, которые смогу получить.

``_COMPILED_PATTERNS`` compiles every entry with ``re.IGNORECASE``, so the printf
pattern's conversion class ``[diouxXeEfFgGcsSp%]`` also accepted ``U``, and
``%UYOL`` registered as the format specifier ``%u``.

In C, case *is* the conversion: ``%u`` is unsigned decimal and ``%U`` is not a
conversion at all; ``%x`` and ``%X`` are different conversions (lower- vs
upper-case hex).  Folding case invents tags that were never there and merges two
that differ.  ``IGNORECASE`` still applies to every other pattern, where it is
correct — ``<BR>``, ``<ALIAS=…>`` and ``</FONT>`` are all real spellings.

Measured over the shipped ``starfield_ru.STRINGS.xml``: case-folding produced 3
phantom matches (``%U`` ×2, ``%O`` ×1) that the case-sensitive pattern does not.

Pure ``_extract_tags`` / ``QualityChecker.check`` assertions — no Qt, no model.

Run with:
    python -m pytest tests/test_printf_tag_case.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from gui.quality_checker import QualityChecker, _extract_tags  # noqa: E402


# ── obfuscated in-game codes must not masquerade as format specifiers ──────────

@pytest.mark.parametrize("garbage", [
    "Я .MG%UYOL;{M&:AF все образцы",     # the real string 45533 fragment
    "код %OQ7 недоступен",
    "%DZBX",
    "%IKQ2",
])
def test_uppercase_pseudo_specifier_is_not_a_tag(garbage):
    assert not any(t.startswith("%") for t in _extract_tags(garbage))


# ── genuine specifiers, lower and upper, are still counted ────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Осталось %s единиц",   "%s"),
    ("Уровень %d",           "%d"),
    ("%u кредитов",          "%u"),
    ("%o режим",             "%o"),
    ("Точность %.0f%%",      "%.0f"),
    ("Адрес %X",             "%X"),      # valid C conversion (upper-case hex)
    ("Значение %E",          "%E"),
    ("Значение %G",          "%G"),
])
def test_real_specifier_is_still_a_tag(text, expected):
    assert expected in _extract_tags(text)


def test_hex_case_variants_stay_distinct():
    """%x and %X print differently, so swapping them is a real change."""
    tags = _extract_tags("%x and %X")
    assert tags["%x"] == 1
    assert tags["%X"] == 1


# ── other tags keep their case-insensitive matching ───────────────────────────

@pytest.mark.parametrize("text", ["<BR>", "<br>", "<Br>"])
def test_markup_tags_remain_case_insensitive(text):
    assert _extract_tags(text)["<br>"] == 1


def test_alias_tag_remains_case_insensitive():
    assert _extract_tags("<ALIAS=Player>") == _extract_tags("<Alias=Player>")


# ── end to end through the checker ────────────────────────────────────────────

def test_scrambled_source_raises_no_missing_tag():
    qc = QualityChecker(target_language="Ukrainian", source_language="Russian")
    report = qc.check(
        0, 45533,
        "Я .MG%UYOL;{M&:AF все генетические образцы, которые смогу получить.",
        "Я .MG%UYOL;{M&:AF всі генетичні зразки, які зможу отримати.",
    )
    assert "MISSING_TAG" not in {i.code for i in report.issues}


def test_a_genuinely_dropped_specifier_is_still_reported():
    """Narrowing the pattern must not disarm the check."""
    qc = QualityChecker(target_language="Ukrainian", source_language="Russian")
    report = qc.check(0, 1, "Осталось %s единиц", "Залишилось одиниць")
    assert "MISSING_TAG" in {i.code for i in report.issues}
