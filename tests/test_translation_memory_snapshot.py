"""
Tests for TranslationMemory JSON-snapshot persistence and the browser feed.
"""

import json

from gui.translation_memory import TranslationMemory


def test_snapshot_roundtrip(tmp_path):
    tm = TranslationMemory()
    tm._by_id = {0x1A: "Привіт", 0xFF: "Світ"}
    tm._by_src = {"Hello": "Привіт", "World": "Світ"}
    tm.source_path = "orig.txt"

    snap = tmp_path / "tm.json"
    tm.save_snapshot(snap)
    assert snap.exists()

    tm2 = TranslationMemory()
    n = tm2.load_snapshot(snap)
    assert n == 2
    assert tm2.get_by_id(0x1A) == "Привіт"
    assert tm2.get_by_id(0xFF) == "Світ"
    assert tm2.get_by_source("Hello") == "Привіт"
    assert tm2.source_path == "orig.txt"


def test_snapshot_merges_into_existing(tmp_path):
    tm = TranslationMemory()
    tm._by_id = {1: "one"}
    snap = tmp_path / "tm.json"
    tm.save_snapshot(snap)

    other = TranslationMemory()
    other._by_id = {2: "two"}
    other.load_snapshot(snap)
    assert other.get_by_id(1) == "one"
    assert other.get_by_id(2) == "two"


def test_load_missing_snapshot_returns_zero(tmp_path):
    tm = TranslationMemory()
    assert tm.load_snapshot(tmp_path / "nope.json") == 0


def test_load_malformed_snapshot_is_safe(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not json", encoding="utf-8")
    tm = TranslationMemory()
    assert tm.load_snapshot(bad) == 0  # no raise


def test_snapshot_is_valid_json_with_hex_ids(tmp_path):
    tm = TranslationMemory()
    tm._by_id = {0xABCD: "x"}
    snap = tmp_path / "tm.json"
    tm.save_snapshot(snap)
    data = json.loads(snap.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "abcd" in data["by_id"]


def test_entries_merges_id_and_source():
    tm = TranslationMemory()
    tm._by_id = {0x10: "Привіт"}
    tm._by_src = {"Hello": "Привіт", "Extra": "Додатково"}
    rows = tm.entries()
    # ID-keyed row carries its source; source-only row has a blank ID.
    by_tr = {tr: (sid, src) for sid, src, tr in rows}
    assert by_tr["Привіт"] == ("0x00000010", "Hello")
    assert by_tr["Додатково"][0] == ""
    assert by_tr["Додатково"][1] == "Extra"
