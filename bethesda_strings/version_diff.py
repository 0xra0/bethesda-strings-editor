"""
Version-to-version diff for Bethesda string files.

Compares two source-language dictionaries (old game build vs new game build)
and classifies every string as added, removed, modified, or unchanged.
Optionally annotates each entry with an existing translation from a prior
translation file so unchanged strings can be migrated automatically.
"""

from __future__ import annotations

import csv
import html as _html
import io
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


class DiffStatus(str, Enum):
    ADDED = "added"         # present in new, absent in old
    REMOVED = "removed"     # present in old, absent in new
    MODIFIED = "modified"   # same ID, source text changed
    UNCHANGED = "unchanged" # same ID, source text identical


@dataclass
class VersionDiffEntry:
    string_id: int
    status: DiffStatus
    old_text: str           # source text from old build ("" if added)
    new_text: str           # source text from new build ("" if removed)
    existing_translation: str = ""  # from old translation file, if provided

    def needs_translation(self) -> bool:
        return self.status in (DiffStatus.ADDED, DiffStatus.MODIFIED)

    def can_migrate(self) -> bool:
        """True when the existing translation can be carried forward as-is."""
        return self.status == DiffStatus.UNCHANGED and bool(self.existing_translation)


def compute_version_diff(
    old_strings: Dict[int, str],
    new_strings: Dict[int, str],
    old_translation: Optional[Dict[int, str]] = None,
) -> List[VersionDiffEntry]:
    """Compare two source-language string dicts and return a diff list.

    Parameters
    ----------
    old_strings:
        id → source text from the old game build.
    new_strings:
        id → source text from the new game build.
    old_translation:
        id → translated text from the prior translation file (optional).

    Returns
    -------
    List of VersionDiffEntry sorted by string_id.
    """
    old_trans = old_translation or {}
    entries: List[VersionDiffEntry] = []

    for sid in sorted(set(old_strings) | set(new_strings)):
        in_old = sid in old_strings
        in_new = sid in new_strings
        old_text = old_strings.get(sid, "")
        new_text = new_strings.get(sid, "")
        existing = old_trans.get(sid, "")

        if in_new and not in_old:
            status = DiffStatus.ADDED
        elif in_old and not in_new:
            status = DiffStatus.REMOVED
        elif old_text == new_text:
            status = DiffStatus.UNCHANGED
        else:
            status = DiffStatus.MODIFIED

        entries.append(VersionDiffEntry(
            string_id=sid,
            status=status,
            old_text=old_text,
            new_text=new_text,
            existing_translation=existing,
        ))

    return entries


def diff_summary(entries: List[VersionDiffEntry]) -> Dict[str, int]:
    """Return {status_value: count} for all statuses."""
    counts: Dict[str, int] = {s.value: 0 for s in DiffStatus}
    for e in entries:
        counts[e.status.value] += 1
    return counts


# ── Report generators ──────────────────────────────────────────────────────────

def to_csv(
    entries: List[VersionDiffEntry],
    old_label: str = "Old Source",
    new_label: str = "New Source",
    translation_label: str = "Existing Translation",
) -> str:
    """Render entries as CSV text (UTF-8, returned as string)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Status", old_label, new_label, translation_label])
    for e in entries:
        writer.writerow([
            f"0x{e.string_id:08X}",
            e.status.value,
            e.old_text,
            e.new_text,
            e.existing_translation,
        ])
    return buf.getvalue()


_REPORT_CSS = """
body{font-family:'Segoe UI',sans-serif;background:#f8fafc;color:#1e293b;margin:0}
h1{background:#1e293b;color:#f1f5f9;padding:16px 24px;margin:0;font-size:18px}
.meta{background:#e2e8f0;padding:8px 24px;font-size:12px;color:#64748b;
      border-bottom:1px solid #cbd5e1}
.summary{display:flex;gap:32px;padding:16px 24px;background:#fff;
         border-bottom:1px solid #e2e8f0}
.stat{text-align:center}
.stat-n{font-size:28px;font-weight:700;line-height:1}
.stat-label{font-size:11px;color:#64748b;margin-top:4px;text-transform:uppercase;
            letter-spacing:.04em}
.added    .stat-n{color:#16a34a}
.removed  .stat-n{color:#dc2626}
.modified .stat-n{color:#d97706}
.unchanged .stat-n{color:#94a3b8}
table{width:calc(100% - 48px);border-collapse:collapse;margin:16px 24px;
      font-size:12px}
th{background:#334155;color:#f1f5f9;padding:8px 12px;text-align:left;
   font-size:11px;text-transform:uppercase;letter-spacing:.04em}
td{padding:6px 10px;vertical-align:top;border-bottom:1px solid #e2e8f0;
   font-family:'DejaVu Sans Mono',monospace;white-space:pre-wrap;
   word-break:break-word;max-width:300px}
.badge{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;
       font-weight:600;font-family:sans-serif;text-transform:uppercase}
.badge-added    {background:#dcfce7;color:#16a34a}
.badge-removed  {background:#fee2e2;color:#dc2626}
.badge-modified {background:#fef9c3;color:#b45309}
.badge-unchanged{background:#f1f5f9;color:#64748b}
tr.added    td{background:#f0fdf4}
tr.removed  td{background:#fef2f2}
tr.modified td{background:#fffbeb}
"""


def to_html(
    entries: List[VersionDiffEntry],
    old_label: str = "Old Version",
    new_label: str = "New Version",
    translation_label: str = "Existing Translation",
    changed_only: bool = False,
    title: str = "Game Version Diff Report",
) -> str:
    """Render entries as a self-contained HTML report."""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = diff_summary(entries)

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
        badge = (
            f'<span class="badge badge-{e.status.value}">'
            f"{e.status.value}</span>"
        )
        rows_html.append(
            f'<tr class="{e.status.value}">'
            f"<td>0x{e.string_id:08X}</td>"
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
        f'<div class="meta">Generated: {now}</div>'
        f'<div class="summary">{stat_html}</div>'
        f"<table>"
        f"<tr><th>ID</th><th>Status</th>"
        f"<th>{_html.escape(old_label)}</th>"
        f"<th>{_html.escape(new_label)}</th>"
        f"<th>{_html.escape(translation_label)}</th>"
        f"</tr>"
        + "\n".join(rows_html)
        + "</table></body></html>"
    )


# ── File loader helper ─────────────────────────────────────────────────────────

def load_strings_file(path: str, encoding: str = "utf-8") -> Dict[int, str]:
    """Load a .strings/.dlstrings/.ilstrings file into an id→text dict."""
    from bethesda_strings.core import BethesdaStringFile
    bf = BethesdaStringFile(path)
    result: Dict[int, str] = {}
    for s in bf.strings:
        try:
            result[s.id] = s.get_string(encoding)
        except UnicodeDecodeError:
            result[s.id] = s.get_string("utf-8", errors="replace")
    return result
