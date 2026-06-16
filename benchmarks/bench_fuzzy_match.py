"""
Benchmark for the fuzzy-matching primitives (gui/fuzzy_match.py).

Run with:
    python benchmarks/bench_fuzzy_match.py

These functions back the translation-memory lookup, advanced search, and the
consistency checker.  best_fuzzy_match() is O(N) in the candidate set and is
called once per untranslated string, so its per-candidate cost multiplied by
the translation-memory size is what the user feels when a large TM is loaded.

Measures three scenarios:

  A) primitives      — raw levenshtein_distance / longest_common_substring /
                       words_distance throughput at a few string lengths.

  B) fuzzy_score     — scored pairs per second on a realistic mix (identical,
                       near-miss, and unrelated strings exercise every branch).

  C) best_fuzzy_match— end-to-end TM lookup: score one source against a whole
                       translation memory and return the best hit.
"""

import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from gui.fuzzy_match import (
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


# ── Benchmark C: best_fuzzy_match (TM lookup) ─────────────────────────────────

def bench_best_match(tm_sizes=(500, 2000, 8000), n_queries: int = 50) -> None:
    _hr(f"C  best_fuzzy_match  —  {n_queries} queries vs TM of {tm_sizes}")
    rng = random.Random(99)

    for tm_size in tm_sizes:
        tm = [
            (_rand_string(rng.randint(2, 8), rng), "<translation>")
            for _ in range(tm_size)
        ]
        # Queries are near-misses of random TM entries so some actually hit.
        queries = [_mutate(tm[rng.randrange(tm_size)][0], rng) for _ in range(n_queries)]

        t0 = time.perf_counter()
        hits = 0
        for q in queries:
            if best_fuzzy_match(q, tm) is not None:
                hits += 1
        elapsed = _elapsed_ms(t0)

        per_query = elapsed / n_queries                  # ms
        per_candidate_us = per_query / tm_size * 1000     # ms → µs
        print(f"  TM {tm_size:6,} entries | {elapsed:8.1f} ms total  "
              f"({per_query:6.2f} ms/query, "
              f"{per_candidate_us:4.2f} µs/candidate)  hits={hits}/{n_queries}")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("fuzzy_match benchmarks")
    bench_primitives(n=1000)
    bench_fuzzy_score(n=20000)
    bench_best_match(tm_sizes=(500, 2000, 8000), n_queries=50)
