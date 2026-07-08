"""
Tests for the fuzzy-match digit guard.

A fuzzy TM match that is textually close but has different numbers is
semantically wrong ("28LY" ≠ "30LY", "Level 3" ≠ "Level 5"). best_fuzzy_match
must reject candidates whose digit runs differ from the source.
"""

from gui.fuzzy_match import _digit_runs, best_fuzzy_match


def test_digit_runs_extraction():
    assert _digit_runs("28LY") == ["28"]
    assert _digit_runs("Level 3 Sector 5") == ["3", "5"]
    assert _digit_runs("no digits here") == []
    assert _digit_runs("O2 CO2") == ["2", "2"]


def test_match_rejected_when_digits_differ():
    # Only candidate has different number → no match.
    assert best_fuzzy_match("28LY", [("30LY", "30 світлових років")]) is None


def test_match_kept_when_digits_agree():
    result = best_fuzzy_match("28LY", [("28LY", "28 світлових років")])
    assert result is not None
    assert result[0] == "28 світлових років"


def test_digitless_strings_unaffected():
    result = best_fuzzy_match("The Fuel Box", [("The Fuel Box", "Паливний ящик")])
    assert result is not None
    assert result[0] == "Паливний ящик"


def test_digit_order_matters():
    # Same digits, swapped order → different meaning → rejected.
    assert best_fuzzy_match("Level 3 Sector 5", [("Level 5 Sector 3", "x")]) is None


def test_good_candidate_chosen_over_digit_mismatch():
    # The digit-mismatched (closer-looking) candidate is skipped in favour of the
    # correct one.
    cands = [
        ("30LY", "30 світлових років"),   # wrong number — must be skipped
        ("28LY", "28 світлових років"),   # correct
    ]
    result = best_fuzzy_match("28LY", cands)
    assert result is not None
    assert result[0] == "28 світлових років"
