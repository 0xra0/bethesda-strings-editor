"""
Consistency checker: finds identical source strings rendered with different
translations across the loaded string table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass
class InconsistencyGroup:
    """One source string that has been translated two or more different ways."""

    source: str
    variants: Dict[str, List[int]]  # translation text → list of row indices

    @property
    def total_rows(self) -> int:
        return sum(len(v) for v in self.variants.values())

    @property
    def variant_count(self) -> int:
        return len(self.variants)


def find_inconsistencies(
    rows: List[dict],
    max_source_len: int = 300,
    min_occurrences: int = 2,
    max_results: int = 500,
) -> List[InconsistencyGroup]:
    """Scan translated rows for the same source text rendered differently.

    Only considers rows with status == "translated" and a non-empty translation.
    Skips rows where original == translated (untouched / language-neutral strings).

    Returns groups sorted by (variant count desc, total rows desc), capped at
    max_results.
    """
    source_map: Dict[str, Dict[str, List[int]]] = {}

    for i, row in enumerate(rows):
        original = (row.get("original") or "").strip()
        translated = (row.get("translated") or "").strip()
        status = row.get("status", "pending")

        if status != "translated" or not original or not translated:
            continue
        if len(original) > max_source_len:
            continue
        if original == translated:
            continue

        variants = source_map.setdefault(original, {})
        variants.setdefault(translated, []).append(i)

    results: List[InconsistencyGroup] = []
    for source, variants in source_map.items():
        non_empty = {k: v for k, v in variants.items() if k.strip()}
        if len(non_empty) < 2:
            continue
        if sum(len(v) for v in non_empty.values()) < min_occurrences:
            continue
        results.append(InconsistencyGroup(source=source, variants=non_empty))

    results.sort(key=lambda g: (-g.variant_count, -g.total_rows))
    return results[:max_results]
