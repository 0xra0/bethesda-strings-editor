"""Read-only companion reference for a Bethesda string-file *triplet*.

A localized plugin ships up to three companion files:

* ``.strings``    — null-terminated UI strings (names, menus…)
* ``.dlstrings``  — length-prefixed descriptions (item/perk text…)
* ``.ilstrings``  — length-prefixed dialogue lines

**Each file has its own, INDEPENDENT string-ID space.**  The same numeric ID is
a *different* string in each file — the Creation Engine picks which file to read
from the *field type* that references the ID, not from the ID itself.  So e.g.
ID ``0x14FC`` is a UI label in ``shatteredspace.strings`` *and* an unrelated
dialogue line in ``starfield.ilstrings``.

Because of that, the three files must **never** be merged into one
:class:`~bethesda_strings.core.BethesdaStringFile`:

* deduping the flat list by ID silently drops same-ID / different-type strings,
* and saving the merged list writes a file *contaminated* with IDs that belong
  to a different ID space (the game then shows ``<Error: Unknown lstring ID …>``
  or the wrong text).

:class:`TripletReference` holds companion strings *for reference only*.  It
copies the decoded text out of the source files and keeps the three ID spaces
separate; it never retains or mutates the underlying ``StringDataObject``s, so
the file the user is actually editing can be saved with zero risk of
cross-contamination.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class CompanionEntry:
    """One companion string held for reference."""

    ext: str          # 'strings' | 'dlstrings' | 'ilstrings'
    string_id: int
    text: str
    source: str       # file the entry came from (name or path)


class TripletReference:
    """Companion strings from the sibling files of a triplet, kept read-only.

    IDs are stored per file *extension* so the three independent ID spaces never
    collide.  Look-ups are therefore always qualified by extension.
    """

    def __init__(self) -> None:
        # ext -> {string_id -> text}; independent ID spaces kept separate.
        self._by_ext: dict[str, dict[int, str]] = {}
        self.entries: list[CompanionEntry] = []

    # ── Building ────────────────────────────────────────────────────────────
    def add_file(self, path, string_file, encoding: Optional[str] = None) -> int:
        """Copy every string out of *string_file* into the reference.

        *string_file* is a :class:`~bethesda_strings.core.BethesdaStringFile`.
        The objects themselves are **not** retained — only their decoded text —
        so nothing here can ever affect a later save of those files.

        Returns the number of entries added.
        """
        ext = (
            getattr(string_file, "file_extension", "")
            or Path(str(path)).suffix.lstrip(".")
        ).lower()
        enc = encoding or getattr(string_file, "encoding", "utf-8") or "utf-8"
        table = self._by_ext.setdefault(ext, {})
        name = Path(str(path)).name
        added = 0
        for s in string_file.strings:
            try:
                text = s.get_string(enc)
            except Exception:
                text = s.get_string("utf-8", errors="replace")
            table[s.id] = text
            self.entries.append(CompanionEntry(ext, s.id, text, name))
            added += 1
        return added

    # ── Look-up ─────────────────────────────────────────────────────────────
    def lookup(self, ext: str, string_id: int) -> Optional[str]:
        """Return the companion text for *string_id* within *ext*'s ID space."""
        return self._by_ext.get((ext or "").lower(), {}).get(string_id)

    def extensions(self) -> list[str]:
        """Return the companion file extensions present, sorted."""
        return sorted(self._by_ext)

    def file_count(self) -> int:
        """Number of distinct companion files loaded."""
        return len({e.source for e in self.entries})

    def iter_entries(self) -> Iterable[CompanionEntry]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)
