"""
Benchmark for TranslationCache (gui/translation_cache.py).

Run with:
    python benchmarks/bench_translation_cache.py

The cache is consulted before every model call, so a miss must be cheap and a
hit must be near-free.  It is also hammered from the OllamaWorker thread pool
(default 10 workers), so the per-entry lock must not serialise throughput into
the ground.

Measures four scenarios:

  A) make_key      — sha256 key derivation throughput (done once per lookup).

  B) set / evict   — fill past capacity so every insert evicts the LRU entry;
                     measures the OrderedDict popitem(last=False) hot path.

  C) get hit/miss  — warm-cache hit rate and miss cost.

  D) concurrent    — N worker threads doing mixed get/set, matching the real
                     translation pool, to expose lock contention.
"""

import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gui.translation_cache import TranslationCache

# ── helpers ──────────────────────────────────────────────────────────────────

def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _hr(label: str, width: int = 60) -> None:
    print(f"\n{'─' * width}")
    print(f"  {label}")
    print(f"{'─' * width}")


def _new_cache(max_size: int) -> TranslationCache:
    # cache_path=None + autosave_interval=0 → never touches disk.
    return TranslationCache(cache_path=None, max_size=max_size, autosave_interval=0)


_SAMPLE = "The UC Vanguard requests your presence at the MAST district. Entry #{}"


# ── Benchmark A: make_key ─────────────────────────────────────────────────────

def bench_make_key(n: int = 200000) -> None:
    _hr(f"A  make_key (sha256)  —  {n:,} keys")
    texts = [_SAMPLE.format(i) for i in range(n)]

    t0 = time.perf_counter()
    for txt in texts:
        TranslationCache.make_key(txt, "mamaylm", "en", "uk")
    elapsed = _elapsed_ms(t0)

    print(f"  {n:,} keys in {elapsed:7.1f} ms  "
          f"({n/elapsed*1000:,.0f} keys/s, {elapsed/n*1000:.2f} µs/key)")


# ── Benchmark B: set + LRU eviction ───────────────────────────────────────────

def bench_set_evict(max_size: int = 50000, n_inserts: int = 200000) -> None:
    _hr(f"B  set + evict  —  cap {max_size:,}, {n_inserts:,} inserts ({n_inserts-max_size:,} evictions)")
    cache = _new_cache(max_size)
    keys = [TranslationCache.make_key(_SAMPLE.format(i), "m", "en", "uk") for i in range(n_inserts)]

    t0 = time.perf_counter()
    for k in keys:
        cache.set(k, "переклад")
    elapsed = _elapsed_ms(t0)

    assert len(cache) == max_size, f"expected {max_size}, got {len(cache)}"
    print(f"  {n_inserts:,} inserts in {elapsed:7.1f} ms  "
          f"({n_inserts/elapsed*1000:,.0f} inserts/s)  final size={len(cache):,}")


# ── Benchmark C: get hit / miss ───────────────────────────────────────────────

def bench_get(n_entries: int = 50000, n_lookups: int = 500000) -> None:
    _hr(f"C  get  —  {n_entries:,} entries, {n_lookups:,} lookups (~90% hit)")
    cache = _new_cache(n_entries * 2)
    keys = [TranslationCache.make_key(_SAMPLE.format(i), "m", "en", "uk") for i in range(n_entries)]
    for k in keys:
        cache.set(k, "переклад")

    rng = random.Random(5)
    miss_key = TranslationCache.make_key("never stored", "m", "en", "uk")
    lookups = [
        keys[rng.randrange(n_entries)] if rng.random() < 0.9 else miss_key
        for _ in range(n_lookups)
    ]

    t0 = time.perf_counter()
    for k in lookups:
        cache.get(k)
    elapsed = _elapsed_ms(t0)

    stats = cache.stats()
    print(f"  {n_lookups:,} lookups in {elapsed:7.1f} ms  "
          f"({n_lookups/elapsed*1000:,.0f} lookups/s, {elapsed/n_lookups*1000:.2f} µs/lookup)")
    print(f"  hits={stats.get('hits'):,}  misses={stats.get('misses'):,}")


# ── Benchmark D: concurrent get/set ───────────────────────────────────────────

def bench_concurrent(n_threads: int = 10, ops_per_thread: int = 100000) -> None:
    total = n_threads * ops_per_thread
    _hr(f"D  concurrent  —  {n_threads} threads × {ops_per_thread:,} mixed ops = {total:,}")
    cache = _new_cache(max_size=100000)
    # Pre-seed so gets mostly hit.
    base_keys = [TranslationCache.make_key(_SAMPLE.format(i), "m", "en", "uk") for i in range(20000)]
    for k in base_keys:
        cache.set(k, "переклад")

    barrier = threading.Barrier(n_threads + 1)

    def worker(seed: int) -> None:
        rng = random.Random(seed)
        barrier.wait()
        for _ in range(ops_per_thread):
            if rng.random() < 0.8:
                cache.get(base_keys[rng.randrange(len(base_keys))])
            else:
                cache.set(TranslationCache.make_key(str(rng.random()), "m", "en", "uk"), "x")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    barrier.wait()                       # release all workers together
    t0 = time.perf_counter()
    for t in threads:
        t.join()
    elapsed = _elapsed_ms(t0)

    print(f"  {total:,} ops in {elapsed:7.1f} ms  "
          f"({total/elapsed*1000:,.0f} ops/s across {n_threads} threads)")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("TranslationCache benchmarks")
    bench_make_key(n=200000)
    bench_set_evict(max_size=50000, n_inserts=200000)
    bench_get(n_entries=50000, n_lookups=500000)
    bench_concurrent(n_threads=10, ops_per_thread=100000)
