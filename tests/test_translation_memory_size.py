"""
Tests for TranslationMemory size/truthiness across both of its indexes.

``__len__``/``__bool__`` used to read only ``_by_id``, so a memory keyed purely
by source text — everything the Official-TM miner produces, everything a TMX
import produces — reported itself as empty.  Five separate gates test exactly
that, and every one of them silently switched the memory off: worker
attachment, the lookup gate in both translation backends, the save-on-exit
snapshot, the browser dialog and the status-bar indicator.

Pure Python; no Qt.
"""

from gui.translation_memory import TranslationMemory


# ── truthiness / size ─────────────────────────────────────────────────────────

def test_source_keyed_memory_is_not_empty():
    tm = TranslationMemory()
    added = tm.add_pairs([("Emergency Kit", "Аптечка"), ("Reload", "Перезарядити")])

    assert added == 2
    assert bool(tm) is True          # the gate every consumer checks
    assert len(tm) == 2
    assert tm.loaded_count == 2
    assert tm.get_by_source("Reload") == "Перезарядити"


def test_id_keyed_memory_is_not_empty():
    tm = TranslationMemory()
    tm._by_id = {0x1A: "Привіт"}

    assert bool(tm) is True
    assert len(tm) == 1


def test_empty_memory_is_falsy():
    tm = TranslationMemory()

    assert bool(tm) is False
    assert len(tm) == 0


def test_cleared_memory_is_falsy():
    tm = TranslationMemory()
    tm.add_pairs([("Reload", "Перезарядити")])
    tm.clear()

    assert bool(tm) is False
    assert len(tm) == 0


def test_paired_entries_are_counted_once():
    """A TXT load fills both maps for one logical entry — not two."""
    tm = TranslationMemory()
    tm._by_id = {1: "Привіт", 2: "Світ"}
    tm._by_src = {"Hello": "Привіт", "World": "Світ"}

    assert len(tm) == 2


def test_tmx_only_memory_survives_a_snapshot_roundtrip(tmp_path):
    tm = TranslationMemory()
    tm.add_pairs([("Grav drive", "Грав-двигун")])

    snap = tmp_path / "tm.json"
    tm.save_snapshot(snap)

    restored = TranslationMemory()
    restored.load_snapshot(snap)

    # load_snapshot returns (and loaded_count records) the real size, so the
    # caller that only persists a non-empty TM keeps persisting it.
    assert bool(restored) is True
    assert len(restored) == 1
    assert restored.loaded_count == 1
    assert restored.get_by_source("Grav drive") == "Грав-двигун"


# ── fuzzy index invalidation ─────────────────────────────────────────────────

def test_fuzzy_lookup_sees_pairs_added_after_the_first_lookup():
    """Every write to the source map must drop the cached pre-filter."""
    tm = TranslationMemory()
    tm.add_pairs([("Open the airlock door", "Відчинити двері шлюзу")])

    assert tm.get_fuzzy("Open the airlock doors") is not None
    assert tm.get_fuzzy("Close the cargo bay hatch") is None

    tm.add_pairs([("Close the cargo bay hatch", "Закрити вантажний люк")])

    assert tm.get_fuzzy("Close the cargo bay hatches") == "Закрити вантажний люк"


def test_fuzzy_lookup_is_empty_after_clear():
    tm = TranslationMemory()
    tm.add_pairs([("Open the airlock door", "Відчинити двері шлюзу")])
    assert tm.get_fuzzy("Open the airlock doors") is not None

    tm.clear()

    assert tm.get_fuzzy("Open the airlock doors") is None
