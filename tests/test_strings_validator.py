"""Tests for the translation-folder validator (pure, no Qt / no game files)."""

from bethesda_strings.strings_validator import (
    validate_translation,
    summarize,
    strip_lang_suffix,
    FileReport,
)


def _by_name(reports):
    return {(r.base, r.ext): r for r in reports}


def test_missing_empty_parse_incomplete_ok_orphan():
    source = {
        ("starfield", "strings"): frozenset({1, 2, 3}),
        ("starfield", "ilstrings"): frozenset({10, 11}),
        ("mod", "strings"): frozenset({5}),
        ("mod2", "dlstrings"): frozenset({7}),
        ("mod3", "strings"): frozenset({8}),
    }
    translated = {
        ("starfield", "strings"): frozenset({1, 2, 3}),   # ok
        ("starfield", "ilstrings"): frozenset({10}),       # incomplete (missing 11)
        # ("mod", "strings")  -> absent -> missing
        ("mod2", "dlstrings"): None,                        # parse_error
        ("mod3", "strings"): frozenset(),                   # empty
        ("extra", "strings"): frozenset({9}),              # orphan (no source)
    }
    reports = _by_name(validate_translation(source, translated))

    assert reports[("starfield", "strings")].status == "ok"
    inc = reports[("starfield", "ilstrings")]
    assert inc.status == "incomplete"
    assert inc.missing_ids == [11]
    assert inc.will_error_in_game is True
    assert reports[("mod", "strings")].status == "missing"
    assert reports[("mod2", "dlstrings")].status == "parse_error"
    assert reports[("mod3", "strings")].status == "empty"
    assert reports[("extra", "strings")].status == "orphan"
    assert reports[("extra", "strings")].will_error_in_game is False


def test_extra_ids_flagged_as_possible_contamination_on_ok_file():
    # The real bug signature: translated ilstrings holds IDs the source lacks.
    source = {("starfield", "ilstrings"): frozenset({100, 101})}
    translated = {("starfield", "ilstrings"): frozenset({100, 101, 500, 501, 502})}
    reports = _by_name(validate_translation(source, translated))
    r = reports[("starfield", "ilstrings")]
    assert r.status == "ok"                       # nothing missing → game won't error
    assert r.extra_ids == [500, 501, 502]
    assert "contamination" in r.detail.lower()
    assert r.will_error_in_game is False


def test_reports_sorted_worst_first():
    source = {
        ("a", "strings"): frozenset({1}),
        ("b", "strings"): frozenset({1, 2}),
        ("c", "strings"): frozenset({1}),
    }
    translated = {
        ("a", "strings"): frozenset({1}),          # ok
        ("b", "strings"): frozenset({1}),          # incomplete
        # ("c", "strings") absent -> missing
    }
    reports = validate_translation(source, translated)
    statuses = [r.status for r in reports]
    # missing < incomplete < ok
    assert statuses == ["missing", "incomplete", "ok"]


def test_summarize_counts():
    source = {("a", "strings"): frozenset({1}), ("b", "strings"): frozenset({1})}
    translated = {("a", "strings"): frozenset({1})}  # a ok, b missing
    reports = validate_translation(source, translated)
    assert summarize(reports) == {"ok": 1, "missing": 1}


def test_missing_id_cap_limits_list():
    source = {("big", "strings"): frozenset(range(1000))}
    translated = {("big", "strings"): frozenset()}   # empty -> not incomplete, so
    # use incomplete path instead:
    translated = {("big", "strings"): frozenset({0})}
    reports = validate_translation(source, translated, missing_id_cap=10)
    r = reports[0]
    assert r.status == "incomplete"
    assert len(r.missing_ids) == 10  # capped


def test_strip_lang_suffix():
    assert strip_lang_suffix("starfield_uk", "uk") == "starfield"
    assert strip_lang_suffix("starfield_UK", "uk") == "starfield"   # case-insensitive
    assert strip_lang_suffix("starfield_en", "uk") == "starfield_en"  # no match
    assert strip_lang_suffix("shatteredspace_ptbr", "ptbr") == "shatteredspace"


def test_empty_indexes_produce_no_reports():
    assert validate_translation({}, {}) == []


def test_source_present_translation_identical_is_ok_no_extra():
    source = {("x", "dlstrings"): frozenset({1, 2})}
    translated = {("x", "dlstrings"): frozenset({1, 2})}
    r = validate_translation(source, translated)[0]
    assert isinstance(r, FileReport)
    assert r.status == "ok"
    assert r.extra_ids == []
    assert r.will_error_in_game is False
