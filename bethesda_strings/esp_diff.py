"""
Version-to-version diff and translation migration for ESP/ESM plugin files.

The string-file equivalent lives in :mod:`bethesda_strings.version_diff`, which
keys every string on a single integer ID.  ESP/ESM plugins have no such flat ID:
a translatable string is identified by *which record* it lives in and *which
field* of that record holds it, so the stable key here is

    (form_id, record_sig, field_sig, occurrence)

FormIDs are preserved by mod authors across releases, so this key matches the
same logical string between, say, ``MyMod.esp`` v1.0 and v1.2 even when records
are reordered.  ``occurrence`` disambiguates the rare case of two same-typed
fields in one record.

Typical mod-update workflow (mirrors xTranslator's "compare + apply previous"):

    old      = load_esp_entries("MyMod_v1.0.esp")        # previous English
    new      = load_esp_entries("MyMod_v1.2.esp")        # updated English
    prior    = load_esp_entries("MyMod_v1.0_UK.esp")     # your prior translation
    diff     = compute_esp_diff(old, new, prior)
    migrate  = build_migration_items(diff)               # unchanged → carry over

The "prior translation" plugin is a previously-saved translated plugin: its
field buffers already hold the translated text, so on reload that text is in each
entry's ``.original``.  We read it from there and carry it onto the matching
*unchanged* string in the new version.
"""

from __future__ import annotations

import csv
import html as _html
import io
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from bethesda_strings.version_diff import DiffStatus, _REPORT_CSS

if TYPE_CHECKING:  # pragma: no cover - typing only
    from bethesda_strings.esp_handler import EspStringEntry

# (form_id, record_sig, field_sig, occurrence)
EspKey = Tuple[int, str, str, int]


@dataclass
class EspDiffEntry:
    """One translatable ESP/ESM string, classified against the other version."""

    form_id: int
    edid: str
    record_sig: str
    field_sig: str
    occurrence: int
    status: DiffStatus
    old_text: str = ""              # source text in the old plugin ("" if added)
    new_text: str = ""              # source text in the new plugin ("" if removed)
    existing_translation: str = ""  # translated text from the prior plugin, if any

    @property
    def key(self) -> EspKey:
        return (self.form_id, self.record_sig, self.field_sig, self.occurrence)

    def needs_translation(self) -> bool:
        """A string the translator still has to handle in the new version."""
        return self.status in (DiffStatus.ADDED, DiffStatus.MODIFIED)

    def can_migrate(self) -> bool:
        """True when the prior translation carries forward verbatim (source is
        unchanged and a prior translation exists)."""
        return self.status == DiffStatus.UNCHANGED and bool(self.existing_translation)


def index_by_key(entries: Sequence["EspStringEntry"]) -> Dict[EspKey, "EspStringEntry"]:
    """Map each entry to its stable ``(form_id, record, field, occurrence)`` key.

    ``occurrence`` is assigned in file order per ``(form_id, record, field)`` so
    two same-typed fields in one record stay distinguishable and align across
    versions.
    """
    occ: Dict[Tuple[int, str, str], int] = {}
    out: Dict[EspKey, "EspStringEntry"] = {}
    for e in entries:
        base = (e.form_id, e.record_sig, e.field_sig)
        n = occ.get(base, 0)
        occ[base] = n + 1
        out[(e.form_id, e.record_sig, e.field_sig, n)] = e
    return out


def compute_esp_diff(
    old_entries: Sequence["EspStringEntry"],
    new_entries: Sequence["EspStringEntry"],
    translated_entries: Optional[Sequence["EspStringEntry"]] = None,
) -> List[EspDiffEntry]:
    """Diff two versions of a plugin and annotate with prior translations.

    Parameters
    ----------
    old_entries:
        Strings parsed from the previous English plugin.
    new_entries:
        Strings parsed from the updated English plugin.
    translated_entries:
        Strings parsed from a previously-translated plugin (optional). Their
        ``.original`` holds the delivered translation; it is matched by key and
        attached as ``existing_translation``.

    Returns
    -------
    List of :class:`EspDiffEntry` sorted by ``(form_id, record, field, occ)``.
    """
    old_map = index_by_key(old_entries)
    new_map = index_by_key(new_entries)
    trans_map = index_by_key(translated_entries) if translated_entries else {}

    entries: List[EspDiffEntry] = []
    for key in set(old_map) | set(new_map):
        form_id, rec, fld, occ = key
        o = old_map.get(key)
        n = new_map.get(key)
        t = trans_map.get(key)

        old_text = o.original if o else ""
        new_text = n.original if n else ""
        existing = t.original if t else ""
        edid = (n.edid if n else (o.edid if o else (t.edid if t else ""))) or ""

        if n is not None and o is None:
            status = DiffStatus.ADDED
        elif o is not None and n is None:
            status = DiffStatus.REMOVED
        elif old_text == new_text:
            status = DiffStatus.UNCHANGED
        else:
            status = DiffStatus.MODIFIED

        entries.append(EspDiffEntry(
            form_id=form_id, edid=edid, record_sig=rec, field_sig=fld,
            occurrence=occ, status=status,
            old_text=old_text, new_text=new_text, existing_translation=existing,
        ))

    entries.sort(key=lambda e: (e.form_id, e.record_sig, e.field_sig, e.occurrence))
    return entries


def esp_diff_summary(entries: Sequence[EspDiffEntry]) -> Dict[str, int]:
    """Return ``{status_value: count}`` for all statuses."""
    counts: Dict[str, int] = {s.value: 0 for s in DiffStatus}
    for e in entries:
        counts[e.status.value] += 1
    return counts


def build_migration_items(
    entries: Sequence[EspDiffEntry],
) -> List[Tuple[int, str, str, int, str]]:
    """Return ``(form_id, record_sig, field_sig, occurrence, translation)`` tuples
    for every entry whose prior translation can be carried forward unchanged.

    Tuples (not a dict) so the payload survives a Qt signal without needing
    string keys, and so the caller can match each item to a loaded table row by
    the same composite key.
    """
    return [
        (e.form_id, e.record_sig, e.field_sig, e.occurrence, e.existing_translation)
        for e in entries
        if e.can_migrate()
    ]


def load_esp_entries(path: str, encoding: str = "utf-8") -> List["EspStringEntry"]:
    """Parse an ESP/ESM/ESL plugin and return its translatable string entries."""
    from bethesda_strings.esp_handler import EspFile
    esp = EspFile()
    esp.load(Path(path), encoding)
    return list(esp.strings)


# ── Report generators ───────────────────────────────────────────────────────────

_COLUMNS = ("FormID", "EditorID", "Record", "Field", "Status",
            "Old Source", "New Source", "Existing Translation")


def esp_to_csv(entries: Sequence[EspDiffEntry]) -> str:
    """Render entries as CSV text (UTF-8, returned as a string)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(_COLUMNS)
    for e in entries:
        writer.writerow([
            f"0x{e.form_id:08X}",
            e.edid,
            e.record_sig,
            e.field_sig,
            e.status.value,
            e.old_text,
            e.new_text,
            e.existing_translation,
        ])
    return buf.getvalue()


def esp_to_html(
    entries: Sequence[EspDiffEntry],
    old_label: str = "Old Version",
    new_label: str = "New Version",
    changed_only: bool = False,
    title: str = "Mod Update Migration Report",
) -> str:
    """Render entries as a self-contained HTML report (reuses the shared CSS)."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = esp_diff_summary(entries)

    stat_html = "".join(
        f'<div class="stat {k}">'
        f'<div class="stat-n">{v}</div>'
        f'<div class="stat-label">{k}</div>'
        f'</div>'
        for k, v in summary.items()
    )

    rows_html: List[str] = []
    for e in entries:
        if changed_only and e.status == DiffStatus.UNCHANGED:
            continue
        badge = f'<span class="badge badge-{e.status.value}">{e.status.value}</span>'
        rows_html.append(
            f'<tr class="{e.status.value}">'
            f"<td>0x{e.form_id:08X}</td>"
            f"<td>{_html.escape(e.edid)}</td>"
            f"<td>{_html.escape(e.record_sig)} {_html.escape(e.field_sig)}</td>"
            f"<td>{badge}</td>"
            f"<td>{_html.escape(e.old_text)}</td>"
            f"<td>{_html.escape(e.new_text)}</td>"
            f"<td>{_html.escape(e.existing_translation)}</td>"
            f"</tr>"
        )

    return (
        f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<title>{_html.escape(title)}</title>"
        f"<style>{_REPORT_CSS}</style>"
        f"</head><body>"
        f"<h1>{_html.escape(title)}</h1>"
        f'<div class="meta">Generated: {now} &nbsp;·&nbsp; '
        f"{_html.escape(old_label)} → {_html.escape(new_label)}</div>"
        f'<div class="summary">{stat_html}</div>'
        f"<table>"
        f"<tr><th>FormID</th><th>EditorID</th><th>Record·Field</th><th>Status</th>"
        f"<th>{_html.escape(old_label)}</th>"
        f"<th>{_html.escape(new_label)}</th>"
        f"<th>Existing Translation</th></tr>"
        + "\n".join(rows_html)
        + "</table></body></html>"
    )
