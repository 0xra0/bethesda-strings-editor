# Benchmarks

Standalone micro-benchmarks for the performance-critical, pure-Python hot paths.
Each script is self-contained (no `pytest-benchmark` dependency) and prints a
human-readable report. Run any one directly:

```bash
python benchmarks/bench_core_io.py
python benchmarks/bench_term_protector.py
python benchmarks/bench_fuzzy_match.py
python benchmarks/bench_translation_cache.py
```

| Script | Module under test | What it measures |
|--------|-------------------|------------------|
| `bench_core_io.py` | `bethesda_strings/core.py` | `.strings`/`.dlstrings` parse + serialize throughput, `get_by_id` lookup, dedup-on-save. The path every file open/save hits. |
| `bench_term_protector.py` | `gui/term_protector.py` | `batch_update()` recompile avoidance, content-hash skip, span cache, longest-match-first correctness. |
| `bench_fuzzy_match.py` | `gui/fuzzy_match.py` | Levenshtein / longest-common-substring primitives, `fuzzy_score` throughput, and `best_fuzzy_match` translation-memory lookup. |
| `bench_translation_cache.py` | `gui/translation_cache.py` | `make_key` (sha256), LRU set+evict, get hit/miss, and concurrent throughput under a 10-thread pool. |

These are diagnostics, not pass/fail tests — they have no assertions on timing,
so absolute numbers depend on your hardware. Use them to compare before/after an
optimisation, or to spot a regression (e.g. an O(n²) creeping into a hot loop).

Notes worth knowing when reading the output:

- `.strings` parsing is several times slower than `.dlstrings` because the
  null-terminated format requires a byte-by-byte terminator scan in Python,
  while length-prefixed entries are sliced directly.
- `levenshtein_distance` / `longest_common_substring` are O(n·m) DP in pure
  Python, so their cost grows with the *square* of the string length — the
  fuzzy benchmark deliberately uses short, game-realistic strings.
