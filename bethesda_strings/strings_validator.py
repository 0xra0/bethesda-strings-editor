"""Validate a folder of translated Bethesda string files against their sources.

The Creation Engine reads localized text from ``.strings`` / ``.dlstrings`` /
``.ilstrings`` files named ``<plugin>_<lang>.<ext>``.  When the game runs in a
language whose file is **missing, empty, unparseable, or missing some IDs the
source has**, every affected string shows in-game as
``<Error: Unknown lstring ID XXXXXXXX>``.

This module is pure (no Qt, no I/O of its own): the caller builds an *index* of
which IDs each source and translated file contains — from loose files and/or BA2
archives — and :func:`validate_translation` classifies every file/ID so the UI
can list exactly what will error before the user launches the game.

An *index* is a mapping ``{(base, ext): ids}`` where:

* ``base``  — lowercase plugin base name **without** the ``_<lang>`` suffix
  (e.g. ``"starfield"``, ``"shatteredspace"``), so a source ``starfield_en``
  lines up with a translated ``starfield_uk``.
* ``ext``   — ``"strings"`` / ``"dlstrings"`` / ``"ilstrings"``.
* ``ids``   — ``frozenset[int]`` of the string IDs the file contains,
  ``None`` if the file exists but failed to parse, or the key is **absent**
  entirely if there is no such file on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


# Ordered worst-first so callers can sort by (severity_rank, base, ext).
SEVERITY_RANK = {
    "missing": 0,
    "parse_error": 1,
    "empty": 2,
    "incomplete": 3,
    "orphan": 4,
    "ok": 5,
}


@dataclass
class FileReport:
    """Validation result for a single ``(base, ext)`` file."""

    base: str
    ext: str
    status: str                      # key of SEVERITY_RANK
    source_count: int = 0
    translated_count: int = 0
    missing_ids: list[int] = field(default_factory=list)   # in source, not in translation
    extra_ids: list[int] = field(default_factory=list)     # in translation, not in source
    detail: str = ""

    @property
    def severity(self) -> int:
        return SEVERITY_RANK.get(self.status, 99)

    @property
    def will_error_in_game(self) -> bool:
        """True if this file causes at least one in-game 'Unknown lstring ID'."""
        return self.status in ("missing", "parse_error", "empty", "incomplete")

    @property
    def filename(self) -> str:
        return f"{self.base}.{self.ext}"


IdSet = Optional[frozenset]


def validate_translation(
    source_index: Mapping[tuple, IdSet],
    translated_index: Mapping[tuple, IdSet],
    *,
    missing_id_cap: int = 100,
) -> list[FileReport]:
    """Classify every source/translated file pair.

    Returns one :class:`FileReport` per ``(base, ext)`` seen in either index,
    sorted worst-first.  ``missing_id_cap`` limits how many IDs are recorded in
    ``missing_ids`` / ``extra_ids`` (0 = unlimited) to keep reports light.
    """
    reports: list[FileReport] = []
    keys = set(source_index) | set(translated_index)

    for key in keys:
        base, ext = key
        src = source_index.get(key)
        src_ids: frozenset = frozenset(src) if isinstance(src, (set, frozenset)) else frozenset()
        has_src = key in source_index and src is not None and len(src_ids) > 0
        src_count = len(src_ids)

        tgt_present = key in translated_index
        tgt = translated_index.get(key)
        tgt_ids: frozenset = frozenset(tgt) if isinstance(tgt, (set, frozenset)) else frozenset()
        tgt_count = len(tgt_ids)

        # No meaningful source → any translated file here is an orphan (harmless).
        if not has_src:
            if tgt_present:
                reports.append(FileReport(
                    base, ext, "orphan",
                    source_count=src_count, translated_count=tgt_count,
                    detail="No source file with these IDs — translation not needed.",
                ))
            continue

        if not tgt_present:
            reports.append(FileReport(
                base, ext, "missing",
                source_count=src_count,
                detail=f"No translated file — all {src_count} strings show 'Unknown lstring ID'.",
            ))
            continue

        if tgt is None:
            reports.append(FileReport(
                base, ext, "parse_error",
                source_count=src_count,
                detail="Translated file could not be parsed (corrupt).",
            ))
            continue

        if tgt_count == 0:
            reports.append(FileReport(
                base, ext, "empty",
                source_count=src_count, translated_count=0,
                detail=f"Translated file is empty — all {src_count} strings will error.",
            ))
            continue

        missing = sorted(src_ids - tgt_ids)
        extra = sorted(tgt_ids - src_ids)
        if missing:
            reports.append(FileReport(
                base, ext, "incomplete",
                source_count=src_count, translated_count=tgt_count,
                missing_ids=_cap(missing, missing_id_cap),
                extra_ids=_cap(extra, missing_id_cap),
                detail=f"{len(missing)} of {src_count} strings missing — those show 'Unknown lstring ID'.",
            ))
        else:
            detail = "All source IDs present."
            if extra:
                detail += (f"  Note: {len(extra)} ID(s) not in the source "
                           "(possible cross-file-type contamination).")
            reports.append(FileReport(
                base, ext, "ok",
                source_count=src_count, translated_count=tgt_count,
                extra_ids=_cap(extra, missing_id_cap),
                detail=detail,
            ))

    reports.sort(key=lambda r: (r.severity, r.base, r.ext))
    return reports


def summarize(reports: list[FileReport]) -> dict[str, int]:
    """Count reports by status (handy for a one-line summary)."""
    out: dict[str, int] = {}
    for r in reports:
        out[r.status] = out.get(r.status, 0) + 1
    return out


def strip_lang_suffix(base_with_lang: str, lang: str) -> str:
    """``'starfield_uk' , 'uk'`` -> ``'starfield'`` (case-insensitive suffix)."""
    suffix = f"_{lang}".lower()
    b = base_with_lang
    if b.lower().endswith(suffix):
        return b[: -len(suffix)]
    return b


def _cap(items: list, cap: int) -> list:
    if cap and len(items) > cap:
        return items[:cap]
    return items
