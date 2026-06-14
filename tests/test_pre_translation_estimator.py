"""
Tests for the pre-translation quality estimator.

Run with:
    python -m pytest tests/test_pre_translation_estimator.py -v
or directly:
    python tests/test_pre_translation_estimator.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gui.pre_translation_estimator import (
    ComplexityReport,
    PreTranslationEstimator,
    _DEFAULT_WEIGHTS,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def make_est(source_lang: str = "English") -> PreTranslationEstimator:
    return PreTranslationEstimator(source_lang=source_lang)


# ── basic scoring ─────────────────────────────────────────────────────────────

def test_empty_string_is_trivial():
    est = make_est()
    r = est.estimate("")
    assert r.score == 100
    assert not r.issues
    assert not r.suggest_review
    print("  PASS test_empty_string_is_trivial")


def test_short_code_is_trivial():
    est = make_est()
    r = est.estimate("UC")
    assert r.score >= 95
    assert not r.issues
    print("  PASS test_short_code_is_trivial")


def test_simple_short_string_is_easy():
    est = make_est()
    r = est.estimate("Go to New Atlantis.")
    assert r.score >= 80
    assert r.level == "easy"
    assert not r.suggest_review
    print(f"  PASS test_simple_short_string_is_easy  (score={r.score})")


def test_score_range():
    est = make_est()
    for text in ["", "UC", "Hello world", "x" * 600, "A " * 200]:
        r = est.estimate(text)
        assert 0 <= r.score <= 100, f"score out of range for text len={len(text)}: {r.score}"
    print("  PASS test_score_range")


# ── individual checks ──────────────────────────────────────────────────────────

def test_long_text_penalised():
    est = make_est()
    short_r = est.estimate("Short sentence.")
    long_r = est.estimate("This is a very long sentence. " * 25)
    assert long_r.score < short_r.score
    codes = [i.code for i in long_r.issues]
    assert any("SOURCE" in c for c in codes), f"Expected LONG_SOURCE issue, got {codes}"
    print(f"  PASS test_long_text_penalised  (short={short_r.score}, long={long_r.score})")


def test_high_tag_density_penalised():
    est = make_est()
    dense = "<Alias=Player> <font color='red'>[PLYR]</font> %s %s {var} \\n \\t more text"
    r = est.estimate(dense)
    codes = [i.code for i in r.issues]
    assert any("TAG_DENSITY" in c for c in codes), f"No tag density issue in {codes}"
    assert r.score < 85
    print(f"  PASS test_high_tag_density_penalised  (score={r.score})")



def test_multiple_printf_penalised():
    est = make_est()
    r = est.estimate("Complete %s out of %s missions before %s.")
    codes = [i.code for i in r.issues]
    assert "MULTIPLE_FORMAT_VARS" in codes, f"Expected MULTIPLE_FORMAT_VARS, got {codes}"
    print(f"  PASS test_multiple_printf_penalised  (score={r.score})")


def test_false_friends_english():
    est = make_est(source_lang="English")
    r = est.estimate("Check the actual figure in the magazine.")
    codes = [i.code for i in r.issues]
    assert "FALSE_FRIENDS" in codes, f"Expected FALSE_FRIENDS, got {codes}"
    detail = next(i.detail for i in r.issues if i.code == "FALSE_FRIENDS")
    assert any(w in detail for w in ("actual", "figure", "magazine")), detail
    print(f"  PASS test_false_friends_english  (score={r.score}, detail={detail})")


def test_no_false_friends_for_russian_source():
    est = make_est(source_lang="Russian")
    r = est.estimate("Check the actual figure in the magazine.")
    codes = [i.code for i in r.issues]
    # English false-friend list should NOT apply to Russian source
    assert "FALSE_FRIENDS" not in codes, f"Unexpected FALSE_FRIENDS for Russian source: {codes}"
    print("  PASS test_no_false_friends_for_russian_source")


def test_idiomatic_expression():
    est = make_est(source_lang="English")
    r = est.estimate("The mission is not my cup of tea.")
    codes = [i.code for i in r.issues]
    assert "IDIOMATIC_EXPRESSION" in codes, f"Expected IDIOMATIC_EXPRESSION, got {codes}"
    print(f"  PASS test_idiomatic_expression  (score={r.score})")


def test_pronoun_ambiguity_short_string():
    est = make_est(source_lang="English")
    # Short string, "he" with no named anchor
    r = est.estimate("He found it.")
    codes = [i.code for i in r.issues]
    assert "AMBIGUOUS_PRONOUN" in codes, f"Expected AMBIGUOUS_PRONOUN, got {codes}"
    print(f"  PASS test_pronoun_ambiguity_short_string  (score={r.score})")


def test_pronoun_ambiguity_suppressed_with_proper_noun():
    est = make_est(source_lang="English")
    # Proper noun "Commander" anchors the pronoun → should NOT fire
    r = est.estimate("Commander Barrett said he will arrive soon.")
    codes = [i.code for i in r.issues]
    assert "AMBIGUOUS_PRONOUN" not in codes, (
        f"AMBIGUOUS_PRONOUN should not fire when proper noun is present; got {codes}"
    )
    print("  PASS test_pronoun_ambiguity_suppressed_with_proper_noun")


def test_control_characters():
    est = make_est()
    r = est.estimate("Normal text\x07with bell char")
    codes = [i.code for i in r.issues]
    assert "CONTROL_CHARS" in codes, f"Expected CONTROL_CHARS, got {codes}"
    print(f"  PASS test_control_characters  (score={r.score})")


def test_suggest_review_for_hard():
    est = make_est()
    # Build a string that hits multiple penalties
    hard_text = (
        "[MALE]Commander[FEMALE]Officer, your mission: %s %s — "
        "<Alias=Target> has been found at <font color='red'>[PLYR]</font> "
        "marker. This operation costs an arm and a leg but the actual "
        "figures in the magazine confirm it. " * 4
    )
    r = est.estimate(hard_text)
    assert r.score < 60, f"Expected hard score, got {r.score}"
    assert r.suggest_review
    print(f"  PASS test_suggest_review_for_hard  (score={r.score})")


# ── level property ────────────────────────────────────────────────────────────

def test_levels():
    assert ComplexityReport(score=100).level == "easy"
    assert ComplexityReport(score=80).level == "easy"
    assert ComplexityReport(score=79).level == "medium"
    assert ComplexityReport(score=60).level == "medium"
    assert ComplexityReport(score=59).level == "hard"
    assert ComplexityReport(score=0).level == "hard"
    print("  PASS test_levels")


def test_status_icons():
    assert ComplexityReport(score=80).status_icon == "○"
    assert ComplexityReport(score=60).status_icon == "◑"
    assert ComplexityReport(score=59).status_icon == "●"
    print("  PASS test_status_icons")


# ── learning / weight persistence ─────────────────────────────────────────────

def test_record_correction_bumps_weight():
    est = make_est()
    initial_w = est.get_weights()["ambiguous_pronoun"]
    for _ in range(PreTranslationEstimator._CORRECTION_THRESHOLD):
        est.record_correction("He found it.", "English")
    new_w = est.get_weights()["ambiguous_pronoun"]
    assert new_w > initial_w, f"Weight should have increased: {initial_w} → {new_w}"
    print(f"  PASS test_record_correction_bumps_weight  ({initial_w:.2f}→{new_w:.2f})")


def test_weight_bumped_only_for_active_features():
    est = make_est()
    # A string that has NOTHING ambiguous — only length penalty
    long_but_clear = "Sarah Morgan briefed the crew on the mission details. " * 5
    for _ in range(PreTranslationEstimator._CORRECTION_THRESHOLD):
        est.record_correction(long_but_clear, "English")
    # length_chars should be bumped; ambiguous_pronoun should stay at default
    w = est.get_weights()
    assert w["length_chars"] > _DEFAULT_WEIGHTS["length_chars"], "length_chars should be bumped"
    assert w["ambiguous_pronoun"] == _DEFAULT_WEIGHTS["ambiguous_pronoun"], (
        "ambiguous_pronoun should not be bumped"
    )
    print(f"  PASS test_weight_bumped_only_for_active_features  (length_chars={w['length_chars']:.2f})")


def test_weight_capped_at_max():
    est = make_est()
    # Repeatedly correct the same pattern far beyond threshold
    for _ in range(100):
        est.record_correction("He found it.", "English")
    w = est.get_weights()
    assert w["ambiguous_pronoun"] <= PreTranslationEstimator._MAX_WEIGHT
    print(f"  PASS test_weight_capped_at_max  (max={w['ambiguous_pronoun']:.3f})")


def test_reset_weights():
    est = make_est()
    for _ in range(PreTranslationEstimator._CORRECTION_THRESHOLD):
        est.record_correction("He found it.", "English")
    est.reset_weights()
    w = est.get_weights()
    assert w == _DEFAULT_WEIGHTS, f"Weights should be defaults after reset: {w}"
    print("  PASS test_reset_weights")


def test_weight_persistence():
    with tempfile.TemporaryDirectory() as tmpdir:
        weights_path = Path(tmpdir) / "weights.json"
        est1 = PreTranslationEstimator(
            source_lang="English", weights_path=weights_path
        )
        for _ in range(PreTranslationEstimator._CORRECTION_THRESHOLD):
            est1.record_correction("He found it.", "English")
        saved_w = est1.get_weights()

        # Load in a new instance
        est2 = PreTranslationEstimator(
            source_lang="English", weights_path=weights_path
        )
        loaded_w = est2.get_weights()
        assert abs(saved_w["ambiguous_pronoun"] - loaded_w["ambiguous_pronoun"]) < 1e-6
    print("  PASS test_weight_persistence")


# ── runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_empty_string_is_trivial,
        test_short_code_is_trivial,
        test_simple_short_string_is_easy,
        test_score_range,
        test_long_text_penalised,
        test_high_tag_density_penalised,
        test_multiple_printf_penalised,
        test_false_friends_english,
        test_no_false_friends_for_russian_source,
        test_idiomatic_expression,
        test_pronoun_ambiguity_short_string,
        test_pronoun_ambiguity_suppressed_with_proper_noun,
        test_control_characters,
        test_suggest_review_for_hard,
        test_levels,
        test_status_icons,
        test_record_correction_bumps_weight,
        test_weight_bumped_only_for_active_features,
        test_weight_capped_at_max,
        test_reset_weights,
        test_weight_persistence,
    ]

    print(f"Running {len(tests)} tests...\n")
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            print(f"  FAIL {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {test.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'All tests passed.' if not failed else f'{failed} test(s) failed.'}")
    sys.exit(0 if not failed else 1)
