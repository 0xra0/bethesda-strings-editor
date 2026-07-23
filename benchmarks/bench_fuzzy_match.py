"""
Benchmark for the fuzzy-matching primitives (gui/fuzzy_match.py).

Run with:
    python benchmarks/bench_fuzzy_match.py

These functions back the translation-memory lookup, advanced search, and the
consistency checker.  best_fuzzy_match() is O(N) in the candidate set and is
called once per untranslated string, so its per-candidate cost multiplied by
the translation-memory size is what the user feels when a large TM is loaded.

That is exactly why production never calls it over the whole memory:
TranslationMemory.get_fuzzy narrows the pool through a FuzzyIndex first (a
lazily-built, *sound* pre-filter — it only drops candidates best_fuzzy_match
would have rejected anyway), then scores the survivors.  Section C measures
both, so the number that matters is the production one and the speedup is
visible next to it.

Measures three scenarios:

  A) primitives      — raw levenshtein_distance / longest_common_substring /
                       words_distance throughput at a few string lengths.

  B) fuzzy_score     — scored pairs per second on a realistic mix (identical,
                       near-miss, and unrelated strings exercise every branch).

  C) best_fuzzy_match— end-to-end TM lookup, full scan vs the FuzzyIndex-narrowed
                       path production actually runs (mirrors
                       TranslationMemory.get_fuzzy): score one source against a
                       whole translation memory and return the best hit.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gui.fuzzy_match import (
    FuzzyIndex,
    best_fuzzy_match,
    fuzzy_score,
    levenshtein_distance,
    longest_common_substring,
    tokenize,
    words_distance,
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _hr(label: str, width: int = 60) -> None:
    print(f"\n{'─' * width}")
    print(f"  {label}")
    print(f"{'─' * width}")


_WORDS = (
    "the New Atlantis UC Vanguard Constellation Lodge credits bounty contract "
    "Akila City Neon Cydonia Mantis ship grav drive reactor shielded cargo hold "
    "mission objective complete failed activate terminal locked encrypted data"
).split()


def _rand_string(n_words: int, rng: random.Random) -> str:
    return " ".join(rng.choice(_WORDS) for _ in range(n_words))


def _mutate(s: str, rng: random.Random) -> str:
    """Return a near-miss variant of *s* (swap/insert/drop a few chars)."""
    chars = list(s)
    for _ in range(max(1, len(chars) // 12)):
        i = rng.randrange(len(chars))
        chars[i] = rng.choice("abcdefghijk ")
    return "".join(chars)


# ── Benchmark A: raw primitives ───────────────────────────────────────────────

def bench_primitives(n: int = 1000) -> None:
    # levenshtein/lcs are pure-Python O(n·m) DP, so cost is dominated by string
    # length squared; game strings are short, so keep lengths realistic.
    _hr(f"A  primitives  —  {n:,} calls each")
    rng = random.Random(42)

    for length in (16, 48, 96):
        pairs = [
            (_rand_string(max(2, length // 6), rng), _rand_string(max(2, length // 6), rng))
            for _ in range(n)
        ]

        t0 = time.perf_counter()
        for a, b in pairs:
            levenshtein_distance(a, b)
        ld_ms = _elapsed_ms(t0)

        t0 = time.perf_counter()
        for a, b in pairs:
            longest_common_substring(a, b)
        lcs_ms = _elapsed_ms(t0)

        tok = [(tokenize(a), tokenize(b)) for a, b in pairs]
        t0 = time.perf_counter()
        for a, b in tok:
            words_distance(a, b)
        wd_ms = _elapsed_ms(t0)

        print(f"  ~{length:3d}-char strings | "
              f"levenshtein {ld_ms:6.1f} ms  "
              f"lcs {lcs_ms:6.1f} ms  "
              f"words_distance {wd_ms:6.1f} ms  "
              f"({n/ld_ms*1000:,.0f} ld/s)")


# ── Benchmark B: fuzzy_score throughput ───────────────────────────────────────

def bench_fuzzy_score(n: int = 20000) -> None:
    _hr(f"B  fuzzy_score  —  {n:,} pairs (1/3 identical, 1/3 near-miss, 1/3 unrelated)")
    rng = random.Random(7)

    pairs: list[tuple[str, str]] = []
    for i in range(n):
        base = _rand_string(rng.randint(2, 8), rng)
        bucket = i % 3
        if bucket == 0:
            cand = base                       # identical → fast path
        elif bucket == 1:
            cand = _mutate(base, rng)         # near-miss → full scoring
        else:
            cand = _rand_string(rng.randint(2, 8), rng)  # unrelated → early reject
        pairs.append((base, cand))

    t0 = time.perf_counter()
    matched = 0
    for src, cand in pairs:
        if fuzzy_score(src, cand) is not None:
            matched += 1
    elapsed = _elapsed_ms(t0)

    print(f"  {n:,} pairs scored in {elapsed:7.1f} ms  "
          f"({n/elapsed*1000:,.0f} pairs/s, {elapsed/n*1000:.2f} µs/pair)")
    print(f"  Matched (score not None): {matched:,}  ({matched/n:.0%})")


# ── Benchmark C: best_fuzzy_match (TM lookup), full scan vs FuzzyIndex ─────────

def _build_tm(tm_size: int, rng: random.Random) -> dict[str, str]:
    """A source→translation memory of *tm_size* unique sources.

    Deduped like the real map (TranslationMemory._by_src is keyed by source),
    so both paths below scan the same pool and the correctness cross-check is
    exact.  A limited word vocabulary means random generation collides, so we
    top up until the dict actually holds tm_size distinct sources.
    """
    by_src: dict[str, str] = {}
    while len(by_src) < tm_size:
        by_src[_rand_string(rng.randint(2, 8), rng)] = "<translation>"
    return by_src


def _lookup_indexed(index: FuzzyIndex, by_src: dict[str, str], query: str):
    """One narrowed lookup, exactly as TranslationMemory.get_fuzzy does it."""
    candidates = []
    for src in index.candidates(query):
        translation = by_src.get(src)
        if translation:
            candidates.append((src, translation))
    if not candidates:
        return None, 0
    result = best_fuzzy_match(query, candidates, max_score=3.0)
    return (result[0] if result else None), len(candidates)


def bench_best_match(tm_sizes=(500, 2000, 8000), n_queries: int = 50,
                     indexed_only_size: int = 50000) -> None:
    _hr(f"C  best_fuzzy_match  —  full scan vs FuzzyIndex, {n_queries} queries")
    print("  (the FuzzyIndex column is the path production runs — "
          "TranslationMemory.get_fuzzy)")
    rng = random.Random(99)

    for tm_size in tm_sizes:
        by_src = _build_tm(tm_size, rng)
        pool = list(by_src.items())
        # Queries are near-misses of random TM entries so some actually hit.
        sources = list(by_src.keys())
        queries = [_mutate(rng.choice(sources), rng) for _ in range(n_queries)]

        # Full scan — best_fuzzy_match over the entire memory.
        t0 = time.perf_counter()
        full_results = [
            (r[0] if (r := best_fuzzy_match(q, pool, max_score=3.0)) else None)
            for q in queries
        ]
        full_ms = _elapsed_ms(t0)

        # FuzzyIndex — build once (production builds it lazily, once per TM),
        # then narrow every query through it before scoring.
        t0 = time.perf_counter()
        index = FuzzyIndex(by_src)
        build_ms = _elapsed_ms(t0)

        t0 = time.perf_counter()
        idx_results = []
        total_cands = 0
        for q in queries:
            res, n_cands = _lookup_indexed(index, by_src, q)
            idx_results.append(res)
            total_cands += n_cands
        idx_ms = _elapsed_ms(t0)

        full_q = full_ms / n_queries
        idx_q = idx_ms / n_queries
        speedup = full_ms / idx_ms if idx_ms else float("inf")
        agree = "yes" if full_results == idx_results else "NO ⚠"
        avg_cands = total_cands / n_queries
        hits = sum(r is not None for r in idx_results)
        print(f"  TM {tm_size:6,} | full {full_q:7.2f} ms/q | "
              f"index {idx_q:6.3f} ms/q (build {build_ms:5.1f} ms, "
              f"{avg_cands:5.1f}/{tm_size} cands) | "
              f"{speedup:5.1f}× | same={agree} hits={hits}/{n_queries}")

    # One indexed-only row at a size where the full scan is too slow to bother
    # with — this is where the pre-filter earns its keep (the miner's TM runs to
    # six figures).  Full scan is skipped, not because it would differ, but
    # because n_queries × tm_size scans would dominate the whole benchmark.
    by_src = _build_tm(indexed_only_size, rng)
    sources = list(by_src.keys())
    queries = [_mutate(rng.choice(sources), rng) for _ in range(n_queries)]
    t0 = time.perf_counter()
    index = FuzzyIndex(by_src)
    build_ms = _elapsed_ms(t0)
    t0 = time.perf_counter()
    total_cands = 0
    hits = 0
    for q in queries:
        res, n_cands = _lookup_indexed(index, by_src, q)
        total_cands += n_cands
        hits += res is not None
    idx_ms = _elapsed_ms(t0)
    print(f"  TM {indexed_only_size:6,} | full   (skipped)    | "
          f"index {idx_ms / n_queries:6.3f} ms/q (build {build_ms:5.1f} ms, "
          f"{total_cands / n_queries:5.1f}/{indexed_only_size} cands) | "
          f"    — | same=  —  hits={hits}/{n_queries}")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("fuzzy_match benchmarks")
    bench_primitives(n=1000)
    bench_fuzzy_score(n=20000)
    bench_best_match(tm_sizes=(500, 2000, 8000), n_queries=50)
