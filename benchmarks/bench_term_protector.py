"""
Benchmark for TermProtector optimisations.

Run with:
    python benchmarks/bench_term_protector.py

Measures four scenarios that directly correspond to the optimisations added:

  A) batch_update()  — bulk add with vs without the context manager under
                       concurrent protect_text() pressure.

  B) content-hash    — recompile that finds the term set unchanged (add + remove
                       of the same term) is a no-op.

  C) span cache      — repeated protect_text() calls on the same strings reuse
                       cached span positions.

  D) sort order      — verify longest-match-first correctness (UC vs UC Vanguard).
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gui.term_protector import TermProtector

# ── helpers ──────────────────────────────────────────────────────────────────

def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _make_terms(n: int) -> list[tuple[str, str]]:
    """Generate n unique fake game terms."""
    return [(f"StarfieldTerm{i:05d}", "game_term") for i in range(n)]


def _hr(label: str, width: int = 60) -> None:
    print(f"\n{'─' * width}")
    print(f"  {label}")
    print(f"{'─' * width}")


# ── Benchmark A: batch_update() ───────────────────────────────────────────────

def bench_batch_update(n_terms: int = 2000, n_interleaved_protects: int = 200) -> None:
    """
    Scenario: a worker thread keeps calling protect_text() while terms are
    being loaded one by one from a file.  Without batch_update() each
    protect_text() between two add_term() calls triggers a full recompile.
    With batch_update() only one recompile happens at the end.

    The two code paths compared:

      Without  protect_text() is called after every 10 add_term() calls
               → n_interleaved_protects recompiles
      With     all add_term() calls are wrapped in batch_update()
               → exactly 1 recompile
    """
    _hr(f"A  batch_update()  —  {n_terms} terms, {n_interleaved_protects} interleaved protects")

    sample_text = (
        "StarfieldTerm00001 is the target. StarfieldTerm00002 confirmed. "
        "UC Vanguard dispatched to New Atlantis for the operation."
    )
    step = max(1, n_terms // n_interleaved_protects)

    # ── Without batch_update ─────────────────────────────────────────────────
    tp_slow = TermProtector()
    t0 = time.perf_counter()
    for i, (term, cat) in enumerate(_make_terms(n_terms)):
        tp_slow.add_term(term, cat, case_sensitive=False)
        if i % step == 0:
            tp_slow.protect_text(sample_text)   # forces recompile if dirty
    elapsed_slow = _elapsed_ms(t0)
    stats_slow = tp_slow.get_statistics()

    # ── With batch_update ─────────────────────────────────────────────────────
    tp_fast = TermProtector()
    t0 = time.perf_counter()
    with tp_fast.batch_update():
        for term, cat in _make_terms(n_terms):
            tp_fast.add_term(term, cat, case_sensitive=False)
    # Simulate the same number of protect_text() calls (all post-load, all cached)
    for i in range(n_interleaved_protects):
        tp_fast.protect_text(sample_text)
    elapsed_fast = _elapsed_ms(t0)
    stats_fast = tp_fast.get_statistics()

    print(f"  Without batch_update : {elapsed_slow:8.1f} ms  "
          f"({stats_slow['compile_count']} recompiles, "
          f"avg {stats_slow['compile_avg_ms']:.1f} ms each)")
    print(f"  With    batch_update : {elapsed_fast:8.1f} ms  "
          f"({stats_fast['compile_count']} recompiles, "
          f"avg {stats_fast['compile_avg_ms']:.1f} ms each)")

    if stats_slow['compile_count'] > 0:
        speedup = elapsed_slow / max(elapsed_fast, 0.001)
        print(f"\n  Speedup: {speedup:.1f}×  "
              f"({stats_slow['compile_count']} → {stats_fast['compile_count']} recompiles)")
    else:
        print("\n  (no recompile triggered — increase n_interleaved_protects)")


# ── Benchmark B: content-hash skips redundant recompile ──────────────────────

def bench_content_hash_skip(n_terms: int = 1000, n_cycles: int = 100) -> None:
    """
    Scenario: rapid add-then-remove of the same term (e.g. a UI settings dialog
    that previews a term before saving it, then reverts on cancel).

    The key pattern is:  add_term(X)  →  remove_term(X)  →  protect_text()
    Both mutations set _dirty.  But after the remove, the term set is back to
    what was last compiled.  The content-hash check detects this and skips the
    expensive re.compile() call entirely.

    Without the check: n_cycles recompiles.
    With    the check: 0 extra recompiles.
    """
    _hr(f"B  content-hash skip  —  {n_terms} base terms, {n_cycles} add+remove+protect cycles")

    tp = TermProtector()
    with tp.batch_update():
        for term, cat in _make_terms(n_terms):
            tp.add_term(term, cat, case_sensitive=False)
    tp.protect_text("warmup")   # force initial compile; establishes _compiled_term_key
    baseline_stats = tp.get_statistics()
    baseline_count = baseline_stats["compile_count"]
    avg_ms = baseline_stats["compile_avg_ms"]

    toggle_term = "ToggleTerm_BENCH"
    t0 = time.perf_counter()
    for _ in range(n_cycles):
        # Add then immediately remove: net change = zero.
        tp.add_term(toggle_term, "test")
        tp.remove_term(toggle_term)
        # _dirty is set, but current key == _compiled_term_key → should skip.
        tp.protect_text(f"Check {toggle_term} status")
    elapsed = _elapsed_ms(t0)

    stats = tp.get_statistics()
    extra_compiles = stats["compile_count"] - baseline_count
    # What it would cost without the hash check:
    avoided_ms = n_cycles * avg_ms

    print(f"  {n_cycles} (add+remove)+protect cycles on {n_terms}-term set: {elapsed:.1f} ms")
    print(f"  Extra recompiles: {extra_compiles}  "
          f"(target: 0 — content-hash skipped all; {n_cycles} without optimisation)")
    print(f"  Compile time avoided: ~{avoided_ms:.0f} ms  "
          f"({n_cycles} × {avg_ms:.1f} ms/compile)")


# ── Benchmark C: span cache ────────────────────────────────────────────────────

def bench_span_cache(n_unique: int = 50, n_repeats: int = 500) -> None:
    """
    Scenario: the Bethesda string file has many duplicate or near-duplicate
    strings (e.g. the same NPC greeting appears in hundreds of dialogue entries).
    Repeated protect_text() calls on identical strings should hit the span cache.
    """
    _hr(f"C  span cache  —  {n_unique} unique strings × {n_repeats} repetitions")

    texts = [
        f"Go to New Atlantis and meet UC Vanguard agent {i} at [PLYR] marker — ref %s."
        for i in range(n_unique)
    ]

    tp = TermProtector()
    tp.protect_text("warmup")

    # First pass: all misses (populate the cache)
    t0 = time.perf_counter()
    for text in texts:
        tp.protect_text(text)
    first_pass_ms = _elapsed_ms(t0)

    # Subsequent passes: all hits
    t0 = time.perf_counter()
    for _ in range(n_repeats - 1):
        for text in texts:
            tp.protect_text(text)
    cached_pass_ms = _elapsed_ms(t0)

    stats = tp.get_statistics()
    per_call_first = first_pass_ms / n_unique * 1000   # µs
    per_call_cached = cached_pass_ms / (n_unique * (n_repeats - 1)) * 1000  # µs

    print(f"  First pass  (cache cold) : {first_pass_ms:7.1f} ms  "
          f"({per_call_first:.1f} µs/call)")
    print(f"  {n_repeats-1} more passes (cache warm) : {cached_pass_ms:7.1f} ms  "
          f"({per_call_cached:.1f} µs/call)")
    print(f"  Cache hits: {stats['span_cache_hits']:,}  "
          f"misses: {stats['span_cache_misses']:,}  "
          f"hit-rate: {stats['span_cache_hit_rate']:.1%}")
    if per_call_first > 0:
        print(f"  Per-call speedup (warm vs cold): {per_call_first/per_call_cached:.1f}×")


# ── Benchmark D: sort-order correctness ───────────────────────────────────────

def bench_sort_order_correctness() -> None:
    """
    Verify that longer terms win over shorter prefixes.

    'UC Vanguard' must be protected as a single token, not as 'UC' + ' Vanguard'.
    This is guaranteed by the longest-first sort in _recompile_if_needed().
    """
    _hr("D  longest-match-first  —  correctness check")

    tp = TermProtector()
    # Both "UC" and "UC Vanguard" are in DEFAULT_PROTECTED_TERMS.
    text = "The UC Vanguard and UC Security forces assembled."
    result, token_map = tp.protect_text(text)

    # Restore and verify round-trip
    restored = tp.restore_text(result, token_map)
    ok = restored == text

    # Count how many tokens replaced multi-word vs single-word terms
    multi_word = sum(1 for v in token_map.values() if " " in v)
    single_word = sum(1 for v in token_map.values() if " " not in v and v.isalpha())

    print(f"  Input    : {text}")
    print(f"  Protected: {result}")
    print(f"  Restored : {restored}")
    print(f"  Round-trip correct  : {'YES' if ok else 'NO — BUG'}")
    print(f"  Multi-word tokens   : {multi_word}  "
          f"(e.g. 'UC Vanguard', 'UC Security')")
    print(f"  Single-word tokens  : {single_word}")

    # UC should NOT appear as a standalone token if both UC-prefixed multi-word
    # terms were found; confirm by checking token_map values.
    standalone_uc = [v for v in token_map.values() if v == "UC"]
    if standalone_uc:
        print(f"  WARNING: 'UC' was protected standalone {len(standalone_uc)} time(s) "
              f"— check sort order")
    else:
        print(f"  'UC' not tokenised standalone — multi-word terms took priority  ✓")


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(tp: TermProtector) -> None:
    _hr("Summary — final get_statistics()")
    stats = tp.get_statistics()
    for key, value in stats.items():
        if key == "by_category":
            continue
        print(f"  {key:<28} {value}")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("TermProtector optimisation benchmarks")
    print(f"Python span-cache maxsize: {TermProtector._SPAN_CACHE_MAXSIZE}")

    bench_batch_update(n_terms=2000, n_interleaved_protects=200)
    bench_content_hash_skip(n_terms=1000, n_cycles=50)
    bench_span_cache(n_unique=50, n_repeats=300)
    bench_sort_order_correctness()

    # Final stats on a representative instance
    tp_final = TermProtector()
    with tp_final.batch_update():
        for term, cat in _make_terms(3000):
            tp_final.add_term(term, cat, case_sensitive=False)
    sample = "UC Vanguard and New Atlantis — StarfieldTerm00001 active [PLYR]."
    for _ in range(200):
        tp_final.protect_text(sample)
    print_summary(tp_final)
