"""
Benchmark for the BethesdaStringFile binary parser/writer (bethesda_strings/core.py).

Run with:
    python benchmarks/bench_core_io.py

This is the hottest path in the whole app: every file open calls _parse() and
every save calls _rebuild().  A large Starfield .strings file holds tens of
thousands of entries, so parse/serialize throughput directly drives how long the
user waits when opening or saving.

Measures four scenarios:

  A) parse        — decode a freshly built buffer into StringDataObjects, for
                    both .strings (null-terminated) and .dlstrings (length-
                    prefixed) layouts.

  B) serialize    — _rebuild() the binary buffer from in-memory objects
                    (get_bytes()), the work done on every save.

  C) get_by_id    — first lookup builds the id->index map; subsequent lookups
                    are O(1).  Measures index build + warm lookup throughput.

  D) dedup        — _rebuild() collapses identical strings into a single data
                    entry (mirrors TES5Edit's ReuseDup).  A file full of
                    duplicate dialogue lines should serialize much smaller.
"""

import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bethesda_strings.core import BethesdaStringFile

# ── helpers ──────────────────────────────────────────────────────────────────

def _elapsed_ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000


def _hr(label: str, width: int = 60) -> None:
    print(f"\n{'─' * width}")
    print(f"  {label}")
    print(f"{'─' * width}")


# Representative mix of short UI labels and longer dialogue/book strings.
_SAMPLE_TEXTS = [
    "Activate",
    "New Atlantis",
    "Welcome to the Lodge, Constellation needs you.",
    "The UC Vanguard requests your presence at the MAST district immediately.",
    "You have <mag> credits remaining. Spend them at any vendor before <dur> days "
    "pass, or the contract with [PLYR] is void and the bounty transfers to a rival.",
]


def _make_entries(n: int, unique: bool = True) -> list[tuple[int, str]]:
    """Build n (id, text) pairs.  unique=False repeats a small text pool."""
    out: list[tuple[int, str]] = []
    for i in range(n):
        if unique:
            text = f"{_SAMPLE_TEXTS[i % len(_SAMPLE_TEXTS)]} (#{i})"
        else:
            text = _SAMPLE_TEXTS[i % len(_SAMPLE_TEXTS)]
        out.append((0x00010000 + i, text))
    return out


def _build_buffer(entries: list[tuple[int, str]], dlstrings: bool = False) -> bytes:
    """Hand-encode a valid .strings/.dlstrings buffer (header + directory + data).

    Matches the exact layout _parse() expects, so the benchmark exercises the
    real parser rather than a from-scratch object graph.
    """
    data = bytearray()
    rel_offsets: list[int] = []
    for _sid, text in entries:
        rel_offsets.append(len(data))
        encoded = text.encode("utf-8") + b"\x00"
        if dlstrings:
            data.extend(struct.pack("<I", len(encoded)) + encoded)
        else:
            data.extend(encoded)

    buf = bytearray()
    buf.extend(struct.pack("<II", len(entries), len(data)))           # header
    for (sid, _text), rel in zip(entries, rel_offsets):
        buf.extend(struct.pack("<II", sid, rel))                      # directory
    buf.extend(data)                                                  # data section
    return bytes(buf)


# ── Benchmark A: parse ────────────────────────────────────────────────────────

def bench_parse(n: int = 20000) -> None:
    _hr(f"A  parse  —  {n:,} entries")

    for ext, dl in (("strings", False), ("dlstrings", True)):
        buf = _build_buffer(_make_entries(n), dlstrings=dl)

        t0 = time.perf_counter()
        sf = BethesdaStringFile(buffer=buf, file_extension=ext)
        elapsed = _elapsed_ms(t0)

        assert len(sf) == n, f"{ext}: parsed {len(sf)} of {n}"
        per_entry = elapsed / n * 1000  # µs
        print(f"  .{ext:<10} {len(buf)/1024:8.1f} KB  →  {elapsed:7.1f} ms  "
              f"({per_entry:.2f} µs/entry, {n/elapsed*1000:,.0f} entries/s)")


# ── Benchmark B: serialize (_rebuild / get_bytes) ─────────────────────────────

def bench_serialize(n: int = 20000) -> None:
    _hr(f"B  serialize (get_bytes)  —  {n:,} entries")

    for ext, dl in (("strings", False), ("dlstrings", True)):
        buf = _build_buffer(_make_entries(n), dlstrings=dl)
        sf = BethesdaStringFile(buffer=buf, file_extension=ext)

        # Touch every string so _rebuild has real work (no untouched fast path).
        for s in sf.strings:
            s.set_string(s.get_string() + "!")

        t0 = time.perf_counter()
        out = sf.get_bytes()
        elapsed = _elapsed_ms(t0)

        assert len(out) > 0
        per_entry = elapsed / n * 1000  # µs
        print(f"  .{ext:<10} {len(out)/1024:8.1f} KB  →  {elapsed:7.1f} ms  "
              f"({per_entry:.2f} µs/entry, {n/elapsed*1000:,.0f} entries/s)")


# ── Benchmark C: get_by_id lookup ─────────────────────────────────────────────

def bench_lookup(n: int = 50000, n_lookups: int = 200000) -> None:
    _hr(f"C  get_by_id  —  {n:,} entries, {n_lookups:,} lookups")

    buf = _build_buffer(_make_entries(n), dlstrings=False)
    sf = BethesdaStringFile(buffer=buf, file_extension="strings")
    ids = [s.id for s in sf.strings]

    # First lookup builds the index lazily.
    t0 = time.perf_counter()
    sf.get_by_id(ids[0])
    index_build_ms = _elapsed_ms(t0)

    # Warm lookups: O(1) dict hits.
    t0 = time.perf_counter()
    for i in range(n_lookups):
        sf.get_by_id(ids[i % n])
    warm_ms = _elapsed_ms(t0)

    print(f"  Index build (first lookup) : {index_build_ms:7.2f} ms  ({n:,} entries)")
    print(f"  {n_lookups:,} warm lookups        : {warm_ms:7.2f} ms  "
          f"({warm_ms/n_lookups*1e6:.0f} ns/lookup, {n_lookups/warm_ms*1000:,.0f}/s)")


# ── Benchmark D: dedup on serialize ───────────────────────────────────────────

def bench_dedup(n: int = 20000) -> None:
    _hr(f"D  dedup on serialize  —  {n:,} entries, all duplicates of {len(_SAMPLE_TEXTS)} texts")

    # Every entry reuses one of a handful of texts → _rebuild should collapse them.
    buf = _build_buffer(_make_entries(n, unique=False), dlstrings=False)
    sf = BethesdaStringFile(buffer=buf, file_extension="strings")

    t0 = time.perf_counter()
    out = sf.get_bytes()
    elapsed = _elapsed_ms(t0)

    # Header (8) + directory (8/entry) is fixed; the data section is what dedup shrinks.
    directory_bytes = 8 + 8 * n
    data_bytes = len(out) - directory_bytes
    naive_data = len(buf) - (8 + 8 * n)
    ratio = naive_data / max(1, data_bytes)

    print(f"  Serialize  : {elapsed:7.1f} ms")
    print(f"  Data section: {data_bytes/1024:8.1f} KB deduped  vs  "
          f"{naive_data/1024:.1f} KB naive   ({ratio:.1f}× smaller)")


# ── main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("BethesdaStringFile core I/O benchmarks")
    bench_parse(n=20000)
    bench_serialize(n=20000)
    bench_lookup(n=50000, n_lookups=200000)
    bench_dedup(n=20000)
