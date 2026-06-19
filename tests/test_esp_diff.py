"""
Tests for ESP/ESM mod-update diff + translation migration (bethesda_strings.esp_diff).

Pure functions over synthetic EspStringEntry lists — no game files, no Qt.

Scenario modelled throughout: a mod author ships MyMod v1.0 (English), the user
translates it, then v1.2 lands with one string added, one changed, one removed,
and the rest unchanged.  Migration must carry the prior translation onto every
unchanged string and leave added/changed ones for the translator.

Run with:
    python -m pytest tests/test_esp_diff.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bethesda_strings.esp_handler import EspStringEntry  # noqa: E402
from bethesda_strings.esp_diff import (  # noqa: E402
    DiffStatus,
    build_migration_items,
    compute_esp_diff,
    esp_diff_summary,
    esp_to_csv,
    esp_to_html,
    index_by_key,
)


def _e(form_id, field, original, edid="", record="ACTI", translation=""):
    return EspStringEntry(
        form_id=form_id, edid=edid, record_sig=record, field_sig=field,
        list_index=0, string_id=0, original=original, translation=translation,
    )


# ── classification ──────────────────────────────────────────────────────────────

def test_classifies_added_removed_modified_unchanged():
    old = [
        _e(0x100, "FULL", "Iron Sword"),
        _e(0x200, "FULL", "Health Potion"),     # will change
        _e(0x300, "FULL", "Old Quest Item"),    # will be removed
    ]
    new = [
        _e(0x100, "FULL", "Iron Sword"),         # unchanged
        _e(0x200, "FULL", "Greater Health Potion"),  # modified
        _e(0x400, "FULL", "New Shiny Thing"),    # added
    ]
    diff = compute_esp_diff(old, new)
    by_fid = {(e.form_id, e.field_sig): e.status for e in diff}
    assert by_fid[(0x100, "FULL")] == DiffStatus.UNCHANGED
    assert by_fid[(0x200, "FULL")] == DiffStatus.MODIFIED
    assert by_fid[(0x300, "FULL")] == DiffStatus.REMOVED
    assert by_fid[(0x400, "FULL")] == DiffStatus.ADDED

    summary = esp_diff_summary(diff)
    assert summary == {"added": 1, "removed": 1, "modified": 1, "unchanged": 1}


def test_same_formid_different_fields_are_separate_entries():
    # A record with both FULL and DESC must yield two independent diff rows.
    old = [_e(0x10, "FULL", "Sword"), _e(0x10, "DESC", "A sharp blade.")]
    new = [_e(0x10, "FULL", "Sword"), _e(0x10, "DESC", "A very sharp blade.")]
    diff = compute_esp_diff(old, new)
    status = {e.field_sig: e.status for e in diff}
    assert status["FULL"] == DiffStatus.UNCHANGED
    assert status["DESC"] == DiffStatus.MODIFIED


def test_index_by_key_disambiguates_duplicate_field_occurrences():
    entries = [_e(0x5, "CNAM", "first"), _e(0x5, "CNAM", "second")]
    keys = index_by_key(entries)
    assert (0x5, "ACTI", "CNAM", 0) in keys
    assert (0x5, "ACTI", "CNAM", 1) in keys
    assert keys[(0x5, "ACTI", "CNAM", 0)].original == "first"
    assert keys[(0x5, "ACTI", "CNAM", 1)].original == "second"


# ── migration ─────────────────────────────────────────────────────────────────

def test_migrates_unchanged_translations():
    old = [_e(0x100, "FULL", "Iron Sword"), _e(0x200, "FULL", "Old Text")]
    new = [_e(0x100, "FULL", "Iron Sword"), _e(0x200, "FULL", "New Text")]
    # Prior translated plugin: translated text lives in .original on reload.
    prior = [_e(0x100, "FULL", "Залізний меч"), _e(0x200, "FULL", "Старий текст")]

    diff = compute_esp_diff(old, new, prior)
    unchanged = next(e for e in diff if e.form_id == 0x100)
    modified = next(e for e in diff if e.form_id == 0x200)

    assert unchanged.status == DiffStatus.UNCHANGED
    assert unchanged.existing_translation == "Залізний меч"
    assert unchanged.can_migrate()

    # The changed string still carries the OLD translation as context, but is NOT
    # migrated automatically (source text differs → needs the translator).
    assert modified.status == DiffStatus.MODIFIED
    assert modified.existing_translation == "Старий текст"
    assert not modified.can_migrate()
    assert modified.needs_translation()


def test_build_migration_items_only_unchanged():
    old = [_e(0x1, "FULL", "A"), _e(0x2, "FULL", "B")]
    new = [_e(0x1, "FULL", "A"), _e(0x2, "FULL", "B-changed")]
    prior = [_e(0x1, "FULL", "А-пер"), _e(0x2, "FULL", "Б-пер")]
    items = build_migration_items(compute_esp_diff(old, new, prior))
    assert items == [(0x1, "ACTI", "FULL", 0, "А-пер")]


def test_added_string_has_no_existing_translation():
    diff = compute_esp_diff([], [_e(0x7, "FULL", "Brand New")],
                            [_e(0x7, "FULL", "should-be-ignored")])
    # No old entry → ADDED; the prior plugin has no matching old source so the
    # entry is genuinely new, but a key match still surfaces a prior translation.
    e = diff[0]
    assert e.status == DiffStatus.ADDED
    assert e.needs_translation()
    assert not e.can_migrate()  # ADDED never auto-migrates


def test_no_prior_translation_means_nothing_to_migrate():
    old = [_e(0x1, "FULL", "Same")]
    new = [_e(0x1, "FULL", "Same")]
    diff = compute_esp_diff(old, new)            # no prior plugin
    assert diff[0].status == DiffStatus.UNCHANGED
    assert not diff[0].can_migrate()
    assert build_migration_items(diff) == []


# ── reports ─────────────────────────────────────────────────────────────────────

def test_csv_contains_all_columns_and_rows():
    diff = compute_esp_diff([_e(0x100, "FULL", "Iron Sword", edid="WeapSword")],
                            [_e(0x100, "FULL", "Iron Sword", edid="WeapSword")],
                            [_e(0x100, "FULL", "Залізний меч", edid="WeapSword")])
    csv_text = esp_to_csv(diff)
    assert "FormID" in csv_text and "EditorID" in csv_text
    assert "0x00000100" in csv_text
    assert "WeapSword" in csv_text
    assert "Залізний меч" in csv_text


def test_html_report_renders():
    diff = compute_esp_diff([_e(0x100, "FULL", "Iron Sword")],
                            [_e(0x100, "FULL", "Steel Sword")])
    html = esp_to_html(diff, old_label="MyMod_v1.0.esp", new_label="MyMod_v1.2.esp")
    assert "<table>" in html
    assert "MyMod_v1.0.esp" in html and "MyMod_v1.2.esp" in html
    assert "modified" in html
