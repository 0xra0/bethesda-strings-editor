"""
Tests for FuzzyIndex — the candidate pre-filter in front of best_fuzzy_match.

The pre-filter exists for speed, so the property that matters is not "it finds
good matches" but "it finds *exactly* what scanning everything would have
found".  Most tests here are therefore differential: build a pool, run both
paths, demand the same answer.  A pre-filter that quietly drops a viable
candidate would return a worse translation — or none — with no error anywhere.

Pure Python; no Qt, no game files.
"""

import random

from gui.fuzzy_match import FuzzyIndex, best_fuzzy_match


# ── helpers ───────────────────────────────────────────────────────────────────

def full_scan(query, pool, max_score=3.0):
    return best_fuzzy_match(query, list(pool.items()), max_score=max_score)


def prefiltered(query, pool, index, max_score=3.0):
    cand = [(s, pool[s]) for s in index.candidates(query)]
    return best_fuzzy_match(query, cand, max_score=max_score) if cand else None


def build(sources):
    pool = {s: "T:" + s for s in sources}
    return pool, FuzzyIndex(pool)


# ── equivalence with a full scan ─────────────────────────────────────────────

def test_multi_word_match_survives_the_filter():
    pool, idx = build([
        "Open the airlock door",
        "Close the cargo bay hatch",
        "Completely unrelated string here",
    ])
    query = "Open the airlock doors"

    assert full_scan(query, pool) is not None
    assert prefiltered(query, pool, idx) == full_scan(query, pool)


def test_single_word_substring_match_survives_the_filter():
    """"Reload"/"Reloads" share no whole word — the word index alone would miss it."""
    pool, idx = build(["Reloads", "Airlock", "Hyperdrive"])

    assert full_scan("Reload", pool) is not None
    assert prefiltered("Reload", pool, idx) == full_scan("Reload", pool)
    assert "Reloads" in idx.candidates("Reload")


def test_punctuation_only_sources_reach_each_other():
    pool, idx = build(["...", "!!!", "Open the door"])

    assert prefiltered("..", pool, idx) == full_scan("..", pool)


def test_worded_query_does_not_need_punctuation_only_candidates():
    _, idx = build(["...", "Open the door"])

    assert "..." not in idx.candidates("Open the door")


def test_unrelated_candidates_are_filtered_out():
    _, idx = build(["Open the airlock door", "Completely different phrasing"])

    assert idx.candidates("Open the airlock door") == ["Open the airlock door"]


# ── determinism ──────────────────────────────────────────────────────────────

def test_candidates_come_back_in_insertion_order():
    """best_fuzzy_match keeps the *first* candidate at the best score.

    Returning the pool in set order made tie-breaking depend on hash
    randomisation: the same memory and the same string could resolve to a
    different translation from one launch to the next.
    """
    sources = [f"module {i} calibration" for i in range(40)]
    _pool, idx = build(sources)

    cand = idx.candidates("module 7 calibration")
    assert cand == sorted(cand, key=sources.index)


def test_ties_resolve_the_same_way_as_a_full_scan():
    # Two candidates that score identically against the query.
    pool, idx = build([
        "reactor power coupling",
        "reactor power conduit",
    ])
    query = "reactor power coolant"

    assert prefiltered(query, pool, idx) == full_scan(query, pool)


# ── differential fuzz ────────────────────────────────────────────────────────

def test_matches_a_full_scan_over_a_randomised_pool():
    """The property, checked broadly: same winner, every query, every threshold."""
    rng = random.Random(20260718)
    words = [
        "reload", "fuel", "box", "play", "the", "music", "emergency", "kit",
        "open", "door", "ship", "crew", "location", "level", "scan", "repair",
        "weapon", "ammo", "on", "off", "quest", "complete", "failed", "engine",
    ]

    def rand_source():
        n = rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 7, 12])
        return " ".join(rng.choice(words) for _ in range(n))

    sources = {rand_source() for _ in range(1500)}
    # Repetition, near-misses and tokenless entries all take separate paths.
    sources.update([
        "...", "!!!", "Reloads", "Reloading", "OFF", "ONN",
        "the the", "the the the", "the the the the the",
    ])
    pool, idx = build(sources)

    queries = [rand_source() for _ in range(400)] + [
        "Reload", "reload", "OFF", "ON", "...", "The Fuel Box",
        "play the music box", "the the", "level 3", "level 5",
    ]

    for query in queries:
        for max_score in (3.0, 5.0, 10.0):
            assert prefiltered(query, pool, idx, max_score) == \
                   full_scan(query, pool, max_score), \
                   f"diverged on {query!r} at max_score={max_score}"


def test_filter_actually_narrows_the_pool():
    """Guard against a 'sound' filter that just returns everything."""
    rng = random.Random(99)
    sources = {
        " ".join(f"w{rng.randrange(4000)}" for _ in range(rng.choice([3, 4, 5, 6])))
        for _ in range(4000)
    }
    pool, idx = build(sources)

    query = next(iter(sources))
    assert len(idx.candidates(query)) < len(pool) / 100


# ── concurrent use ───────────────────────────────────────────────────────────

def test_concurrent_lookups_build_the_index_once():
    """get_fuzzy runs on every translation worker thread at once.

    Without a lock each thread builds its own copy of the same index before any
    of them can use one — ten redundant passes over the whole memory at the
    start of every batch.
    """
    import threading
    from gui.translation_memory import TranslationMemory
    import gui.fuzzy_match as fm

    tm = TranslationMemory()
    tm.add_pairs([(f"open the airlock door {i}", f"T{i}") for i in range(300)])

    builds = []
    real = fm.FuzzyIndex

    class Counting(real):
        def __init__(self, sources=()):
            builds.append(1)
            super().__init__(sources)

    fm.FuzzyIndex = Counting
    try:
        results = []
        barrier = threading.Barrier(8)

        def lookup():
            barrier.wait()
            results.append(tm.get_fuzzy("open the airlock door 42"))

        threads = [threading.Thread(target=lookup) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        fm.FuzzyIndex = real

    assert len(builds) == 1
    assert results == ["T42"] * 8


def test_lookup_survives_a_clear_from_another_thread():
    """Clearing the memory mid-batch must not crash an in-flight lookup."""
    from gui.translation_memory import TranslationMemory

    tm = TranslationMemory()
    tm.add_pairs([(f"open the airlock door {i}", f"T{i}") for i in range(200)])
    tm.get_fuzzy("open the airlock door 1")     # build the index

    tm._by_src.clear()                          # index now names dropped sources

    assert tm.get_fuzzy("open the airlock door 42") is None
