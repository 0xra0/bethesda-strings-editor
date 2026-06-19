"""
Tests for TermProtector's obfuscated-code protection (obf_code / obf_acronym).

Background: Starfield ships deliberately-obfuscated strings — encrypted notes,
passwords, scrambled terminal output — that *look* like garbage on purpose
(the in-game fiction is hidden/encrypted information).  The translatable frame
around them must still become Ukrainian, but the literal code has to survive the
AI call byte-for-byte.  The canonical example from the user:

    Смена фокуса WWFX - это VH1QCR4P$KU
      → Зміна фокусу WWFX — це VH1QCR4P$KU   (frame translated, codes verbatim)

Before this, `WWFX` (all-caps, no vowel) and `VH1QCR4P$KU` (mixed alnum + $, no
underscore) matched none of the structural patterns (form_id needs 8 hex,
asset_id needs an underscore), so the model was free to mangle the secret.

These tests assert the protector replaces those tokens with placeholders and
restores them unchanged, while leaving ordinary words and pronounceable/short
tokens alone.

Run with:
    python -m pytest tests/test_term_protector_obfuscated_codes.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402

from gui.term_protector import TermProtector  # noqa: E402


@pytest.fixture
def tp():
    return TermProtector()


def _protected_originals(tp, text):
    """Return the set of source substrings that got tokenised."""
    _, token_map = tp.protect_text(text)
    return set(token_map.values())


# ── the canonical user example ──────────────────────────────────────────────────

def test_user_example_locks_both_codes(tp):
    text = "Смена фокуса WWFX - это VH1QCR4P$KU"
    protected, token_map = tp.protect_text(text)
    originals = set(token_map.values())
    assert "WWFX" in originals
    assert "VH1QCR4P$KU" in originals
    # The natural-language frame is left untouched for the AI to translate.
    assert "Смена" in protected and "фокуса" in protected and "это" in protected
    # And neither code survives literally in the text sent to the model.
    assert "WWFX" not in protected
    assert "VH1QCR4P$KU" not in protected


def test_codes_restore_byte_for_byte(tp):
    text = "Смена фокуса WWFX - это VH1QCR4P$KU"
    protected, token_map = tp.protect_text(text)
    # Simulate the model translating only the frame, leaving placeholders intact.
    model_out = protected.replace("Смена фокуса", "Зміна фокусу").replace("это", "це")
    restored = tp.restore_text(model_out, token_map, protected_text=protected)
    assert "WWFX" in restored
    assert "VH1QCR4P$KU" in restored


# ── obf_code: mixed alphanumeric / embedded code symbols ─────────────────────────

def test_protects_mixed_alnum_codes(tp):
    for code in ("VH1QCR4P$KU", "WWFX2", "A1B2C3", "KX7$9Q"):
        assert code in _protected_originals(tp, f"код {code} тут"), code


def test_protects_codes_with_weak_symbols(tp):
    # Real report codes carry ^ ! ' @ — these must lock whole (the trailing/embedded
    # weak symbol is part of the obfuscation).
    cases = {
        "Смена фокуса WWFX - это VH1QCR4P$KU^ симуляции": "VH1QCR4P$KU^",
        "остаточно YE3F^@TTSOZKQA, що": "YE3F^@TTSOZKQA",
        "краще Y!JQ71CD кінець": "Y!JQ71CD",
        "код YE3F^@TTSOZKQA'OBC тут": "YE3F^@TTSOZKQA'OBC",
    }
    for text, code in cases.items():
        assert code in _protected_originals(tp, text), text


def test_weak_symbols_alone_do_not_qualify(tp):
    # A weak symbol (^ ! ' ~) never turns a punctuated word or bare price into a code:
    # qualification still requires a digit or a strong symbol ($ # @).
    for s in ("I don't know", "Hello! Wow!!!", "Сумма $500 кредитов",
              "OK! Поехали", "Уровень 5! Победа"):
        assert _protected_originals(tp, s) == set(), s


def test_pure_numbers_are_not_codes(tp):
    # No ASCII letter in the run → not a code token (prices, quantities).
    for s in ("Цена 270 кредитов", "Сумма $500", "Атлатл 270К"):
        assert _protected_originals(tp, s) == set(), s


def test_known_limitation_bare_roman_letter_before_symbol(tp):
    # A code that is a *bare* Roman-numeral letter (C/D/I/L/M/V/X) immediately
    # followed by a non-word char, right after a Cyrillic word, is claimed by the
    # star_system_name protector first ("код C" → "Bradbury I"-style name), so the
    # trailing "$1000" is left unprotected.  Accepted: real obfuscated codes are
    # multi-char alphanumerics (VH1QCR4P$KU) that never hit this.
    assert "C$1000" not in _protected_originals(tp, "код C$1000 тут")


# ── obf_acronym: vowel-less uppercase codes ─────────────────────────────────────

def test_protects_vowelless_uppercase(tp):
    assert "WWFX" in _protected_originals(tp, "метка WWFX тут")
    assert "TBD" in _protected_originals(tp, "статус TBD поки")


def test_leaves_pronounceable_acronyms_for_ai(tp):
    # Has a vowel → could be a localisable acronym (DNA → ДНК); not locked.
    assert "DNA" not in _protected_originals(tp, "тест DNA зразок")
    # Too short → ordinary game stat, not a scrambled code.
    assert "HP" not in _protected_originals(tp, "поточне HP персонажа")


# ── ordinary prose is untouched ─────────────────────────────────────────────────

def test_ordinary_russian_prose_untouched(tp):
    text = "Смена фокуса это обычный текст без кодов"
    assert _protected_originals(tp, text) == set()
