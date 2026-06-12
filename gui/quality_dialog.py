"""
Quality Check Results Dialog.

Shows per-string quality issues with severity filtering, detail panel,
jump-to-string navigation, auto-fix, retranslation queuing, and multi-format
report export (CSV, text log, HTML).
"""

import csv
import html
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from gui.micro_animations import FadeInMixin
from gui.quality_checker import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    QualityChecker,
    QualityReport,
)

logger = logging.getLogger(__name__)

_SEV_LABEL = {
    SEVERITY_ERROR: "✗ Error",
    SEVERITY_WARNING: "⚠ Warning",
    SEVERITY_INFO: "ℹ Info",
}
_SEV_COLOR = {
    SEVERITY_ERROR: QColor("#dc2626"),
    SEVERITY_WARNING: QColor("#d97706"),
    SEVERITY_INFO: QColor("#2563eb"),
}
_SEV_ROW_BG = {
    SEVERITY_ERROR: QColor("#fff1f2"),
    SEVERITY_WARNING: QColor("#fffbeb"),
    SEVERITY_INFO: QColor("#eff6ff"),
}

# ── Export helpers (pure functions, no Qt) ────────────────────────────────────

def _stats(reports: List[QualityReport]):
    errors   = sum(1 for r in reports if r.severity == SEVERITY_ERROR)
    warnings = sum(1 for r in reports if r.severity == SEVERITY_WARNING)
    infos    = sum(1 for r in reports if r.severity == SEVERITY_INFO)
    return errors, warnings, infos


def build_csv(reports: List[QualityReport]) -> str:
    import io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Severity", "String ID", "Code", "Message", "Detail",
        "Original", "Translation",
    ])
    for report in reports:
        for issue in report.issues:
            writer.writerow([
                issue.severity.upper(),
                f"0x{report.string_id:08X}",
                issue.code,
                issue.message,
                issue.detail,
                report.original,
                report.translated,
            ])
    return buf.getvalue()


def build_txt_log(reports: List[QualityReport]) -> str:
    errors, warnings, infos = _stats(reports)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: List[str] = []

    lines.append("QUALITY REPORT — Bethesda Strings AI Translator")
    lines.append(f"Generated : {now}")
    lines.append(f"Strings with issues : {len(reports)}")
    lines.append(f"  Errors   : {errors}")
    lines.append(f"  Warnings : {warnings}")
    lines.append(f"  Info     : {infos}")
    lines.append("=" * 80)
    lines.append("")

    for idx, report in enumerate(reports, 1):
        sev_label = report.severity.upper() if report.severity else "OK"
        lines.append(
            f"[{idx}/{len(reports)}]  String 0x{report.string_id:08X}  "
            f"SEVERITY: {sev_label}"
        )
        lines.append("")

        lines.append("  ORIGINAL:")
        for line in report.original.splitlines() or [report.original]:
            lines.append(f"    {line}")
        lines.append("")

        lines.append("  TRANSLATION:")
        if report.translated:
            for line in report.translated.splitlines() or [report.translated]:
                lines.append(f"    {line}")
        else:
            lines.append("    <empty>")
        lines.append("")

        lines.append("  ISSUES:")
        for issue in report.issues:
            sev_str = issue.severity.upper()
            lines.append(f"    [{sev_str}] {issue.code}: {issue.message}")
            if issue.detail:
                lines.append(f"            → {issue.detail}")
        lines.append("")
        lines.append("─" * 80)
        lines.append("")

    return "\n".join(lines)


def build_html(reports: List[QualityReport]) -> str:
    errors, warnings, infos = _stats(reports)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _SEV_BG   = {SEVERITY_ERROR: "#fee2e2", SEVERITY_WARNING: "#fef3c7", SEVERITY_INFO: "#eff6ff", "": "#f9fafb"}
    _SEV_BADGE = {
        SEVERITY_ERROR:   ('<span style="background:#dc2626;color:#fff;padding:2px 7px;border-radius:4px;font-size:.8em">ERROR</span>',),
        SEVERITY_WARNING: ('<span style="background:#d97706;color:#fff;padding:2px 7px;border-radius:4px;font-size:.8em">WARNING</span>',),
        SEVERITY_INFO:    ('<span style="background:#2563eb;color:#fff;padding:2px 7px;border-radius:4px;font-size:.8em">INFO</span>',),
    }
    _ISSUE_COLOR = {SEVERITY_ERROR: "#dc2626", SEVERITY_WARNING: "#b45309", SEVERITY_INFO: "#1d4ed8"}

    def badge(sev: str) -> str:
        return _SEV_BADGE.get(sev, ('',))[0]

    def pre(text: str) -> str:
        return f'<pre style="margin:4px 0 4px 16px;white-space:pre-wrap;word-break:break-word">{html.escape(text or "<empty>")}</pre>'

    parts: List[str] = []
    parts.append("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Quality Report — Bethesda Strings AI Translator</title>
<style>
  body{font-family:system-ui,sans-serif;font-size:14px;line-height:1.5;
       color:#111;background:#f9fafb;margin:0;padding:16px}
  h1{font-size:1.3em;margin-bottom:4px}
  .meta{color:#6b7280;font-size:.85em;margin-bottom:16px}
  .summary{display:flex;gap:16px;margin-bottom:20px}
  .badge-box{padding:8px 18px;border-radius:8px;text-align:center;font-weight:bold}
  .be{background:#fee2e2;color:#dc2626}.bw{background:#fef3c7;color:#b45309}
  .bi{background:#eff6ff;color:#2563eb}.bt{background:#f3f4f6;color:#374151}
  .card{border-radius:8px;border:1px solid #e5e7eb;margin-bottom:16px;
        overflow:hidden}
  .card-header{display:flex;gap:10px;align-items:center;
               padding:8px 14px;font-weight:600;font-size:.95em}
  .card-body{padding:10px 14px}
  .label{color:#6b7280;font-size:.8em;font-weight:700;
         text-transform:uppercase;letter-spacing:.05em;margin:6px 0 2px}
  pre{background:#f3f4f6;border-radius:4px;padding:6px 10px;font-size:.85em;
      margin:0 0 8px;white-space:pre-wrap;word-break:break-word}
  .issue-row{border-left:3px solid;padding:4px 8px;margin:4px 0;
             border-radius:0 4px 4px 0;font-size:.88em}
  .ie{border-color:#dc2626;background:#fef2f2;color:#7f1d1d}
  .iw{border-color:#d97706;background:#fffbeb;color:#78350f}
  .ii{border-color:#2563eb;background:#eff6ff;color:#1e3a8a}
  .detail{font-size:.82em;color:#6b7280;margin-left:8px}
</style>
</head>
<body>
""")
    parts.append("<h1>Quality Report — Bethesda Strings AI Translator</h1>")
    parts.append(f'<p class="meta">Generated: {html.escape(now)}</p>')
    parts.append('<div class="summary">')
    parts.append(f'<div class="badge-box be">{errors}<br><small>Error(s)</small></div>')
    parts.append(f'<div class="badge-box bw">{warnings}<br><small>Warning(s)</small></div>')
    parts.append(f'<div class="badge-box bi">{infos}<br><small>Info</small></div>')
    parts.append(f'<div class="badge-box bt">{len(reports)}<br><small>String(s) affected</small></div>')
    parts.append('</div>')

    for idx, report in enumerate(reports, 1):
        sev = report.severity or ""
        bg = _SEV_BG.get(sev, "#f9fafb")
        parts.append('<div class="card">')
        parts.append(
            f'<div class="card-header" style="background:{bg}">'
            f'<span style="color:#6b7280">#{idx}</span>'
            f'<code style="font-size:.9em">0x{report.string_id:08X}</code>'
            f'{badge(sev)}'
            f'</div>'
        )
        parts.append('<div class="card-body">')

        parts.append('<div class="label">Original</div>')
        parts.append(pre(report.original))

        parts.append('<div class="label">Translation</div>')
        parts.append(pre(report.translated))

        parts.append('<div class="label">Issues</div>')
        for issue in report.issues:
            cls = {"error": "ie", "warning": "iw", "info": "ii"}.get(issue.severity, "ii")
            color = _ISSUE_COLOR.get(issue.severity, "#374151")
            parts.append(
                f'<div class="issue-row {cls}">'
                f'<strong style="color:{color}">[{issue.severity.upper()}]</strong> '
                f'<strong>{html.escape(issue.code)}</strong>: {html.escape(issue.message)}'
            )
            if issue.detail:
                parts.append(f'<div class="detail">→ {html.escape(issue.detail)}</div>')
            parts.append('</div>')

        parts.append('</div></div>')  # card-body, card

    parts.append("</body></html>")
    return "\n".join(parts)


# ── JSON serialisation / deserialisation ─────────────────────────────────────

_JSON_VERSION = 1


def build_json(reports: List[QualityReport]) -> str:
    """Serialise reports to a JSON string that can be reimported later."""
    errors, warnings, infos = _stats(reports)
    payload = {
        "version": _JSON_VERSION,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "summary": {"errors": errors, "warnings": warnings, "infos": infos,
                    "total": len(reports)},
        "reports": [
            {
                "row_index": r.row_index,
                "string_id": r.string_id,
                "original":  r.original,
                "translated": r.translated,
                "issues": [
                    {"severity": i.severity, "code": i.code,
                     "message": i.message, "detail": i.detail}
                    for i in r.issues
                ],
            }
            for r in reports
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def load_json(
    path: str,
    table_data: Optional[List[Dict]] = None,
) -> Tuple[List[QualityReport], List[str]]:
    """
    Load a JSON quality report and remap row indices to the current table.

    *table_data* is ``StringTableModel._data`` (list of row dicts with keys
    ``id`` and ``original``).  When supplied, stale ``row_index`` values are
    corrected by matching first on ``string_id``, then on ``original`` text.

    Returns ``(reports, warnings)`` where *warnings* is a list of strings
    describing any rows that could not be remapped.
    """
    from gui.quality_checker import QualityIssue, QualityReport

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    version = data.get("version", 0)
    if version != _JSON_VERSION:
        raise ValueError(
            f"Unsupported quality report version {version} (expected {_JSON_VERSION})"
        )

    raw_reports: List[dict] = data.get("reports", [])
    warnings: List[str] = []

    # Build lookup maps from the live table (if provided)
    id_to_row:  Dict[int, int] = {}
    txt_to_row: Dict[str, int] = {}
    if table_data:
        for idx, row in enumerate(table_data):
            sid = row.get("id", 0)
            if sid:
                id_to_row[sid] = idx
            orig = row.get("original", "")
            if orig:
                txt_to_row[orig] = idx

    reports: List[QualityReport] = []
    for raw in raw_reports:
        saved_row  = raw.get("row_index", -1)
        string_id  = raw.get("string_id", 0)
        original   = raw.get("original", "")
        translated = raw.get("translated", "")

        # Remap row_index to current table position
        if table_data:
            row_index = id_to_row.get(string_id)
            if row_index is None:
                row_index = txt_to_row.get(original)
            if row_index is None:
                warnings.append(
                    f"String 0x{string_id:08X} not found in current table — skipped"
                )
                continue
        else:
            row_index = saved_row

        issues = [
            QualityIssue(
                severity=i.get("severity", "info"),
                code=i.get("code", ""),
                message=i.get("message", ""),
                detail=i.get("detail", ""),
            )
            for i in raw.get("issues", [])
        ]
        reports.append(QualityReport(
            row_index=row_index,
            string_id=string_id,
            original=original,
            translated=translated,
            issues=issues,
        ))

    return reports, warnings


def load_csv(
    path: str,
    table_data: Optional[List[Dict]] = None,
) -> Tuple[List[QualityReport], List[str]]:
    """
    Load a CSV quality report (exported by build_csv) and remap row indices.

    The CSV may have multiple rows per string (one per issue); they are grouped
    back into one ``QualityReport`` per ``string_id``.  Severity is lowercased
    to match internal constants.  Files without a ``.csv`` extension are
    accepted as long as the header row matches.

    Returns ``(reports, warnings)`` — same contract as ``load_json``.
    """
    from gui.quality_checker import QualityIssue, QualityReport
    from collections import OrderedDict

    # Try UTF-8-sig first (BOM written by Excel), fall back to plain UTF-8
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            with open(path, newline="", encoding=enc) as f:
                sample = f.read(256)
            if sample:
                break
        except UnicodeDecodeError:
            continue

    with open(path, newline="", encoding=enc) as f:  # type: ignore[possibly-undefined]
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV file is empty or has no header row")

        # Normalise header names (strip whitespace, case-insensitive)
        norm = {h.strip().lower(): h for h in reader.fieldnames}
        required = {"severity", "string id", "code", "message"}
        missing = required - set(norm.keys())
        if missing:
            raise ValueError(
                f"CSV is missing required columns: {', '.join(sorted(missing))}\n"
                f"Expected: Severity, String ID, Code, Message, Detail, Original, Translation"
            )

        # Group rows by string_id, preserving insertion order
        grouped: OrderedDict = OrderedDict()
        for row in reader:
            raw_id = row.get(norm.get("string id", ""), "0").strip()
            try:
                string_id = int(raw_id, 16) if raw_id.startswith("0x") else int(raw_id, 0)
            except ValueError:
                string_id = 0
            if string_id not in grouped:
                grouped[string_id] = {
                    "original":   row.get(norm.get("original", ""), ""),
                    "translated": row.get(norm.get("translation", ""), ""),
                    "issues":     [],
                }
            sev_raw = row.get(norm.get("severity", ""), "info").strip().lower()
            grouped[string_id]["issues"].append(QualityIssue(
                severity=sev_raw,
                code=row.get(norm.get("code", ""), "").strip(),
                message=row.get(norm.get("message", ""), "").strip(),
                detail=row.get(norm.get("detail", ""), "").strip(),
            ))

    # Build lookup maps from the live table (same logic as load_json)
    id_to_row:  Dict[int, int] = {}
    txt_to_row: Dict[str, int] = {}
    if table_data:
        for idx, trow in enumerate(table_data):
            sid = trow.get("id", 0)
            if sid:
                id_to_row[sid] = idx
            orig = trow.get("original", "")
            if orig:
                txt_to_row[orig] = idx

    reports: List[QualityReport] = []
    warnings: List[str] = []
    for string_id, info in grouped.items():
        original   = info["original"]
        translated = info["translated"]
        if table_data:
            row_index = id_to_row.get(string_id)
            if row_index is None:
                row_index = txt_to_row.get(original)
            if row_index is None:
                warnings.append(
                    f"String 0x{string_id:08X} not found in current table — skipped"
                )
                continue
        else:
            row_index = -1  # unknown without a live table

        reports.append(QualityReport(
            row_index=row_index,
            string_id=string_id,
            original=original,
            translated=translated,
            issues=info["issues"],
        ))

    return reports, warnings


# ── Dialog ────────────────────────────────────────────────────────────────────


class QualityDialog(FadeInMixin, QDialog):
    """
    Modal dialog presenting quality check results with auto-fix and
    retranslation queuing.

    After exec(), check ``pending_retranslations`` for a list of
    ``(row_index, retry_hint)`` pairs that the user requested to retranslate.
    """

    jump_to_row = Signal(int)  # row_index in StringTableModel

    def __init__(
        self,
        reports: List[QualityReport],
        table_model=None,
        checker: Optional[QualityChecker] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Quality Check Results"))
        self.setMinimumSize(1050, 680)
        self._all_reports = reports
        self._shown_reports: List[QualityReport] = list(reports)
        self._table_model = table_model
        self._checker = checker

        # Rows the user wants to retranslate: list of (row_index, retry_hint)
        self.pending_retranslations: List[Tuple[int, str]] = []

        self._setup_ui()
        self._populate()
        self._update_action_buttons()

    # ── Setup ──────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Stats bar ──────────────────────────────────────────────────────────
        errors, warnings, infos = _stats(self._all_reports)
        total = len(self._all_reports)

        n_fixable = sum(
            1 for r in self._all_reports
            if any(QualityChecker.issue_can_autofix(i.code) for i in r.issues)
        )
        n_retranslatable = sum(
            1 for r in self._all_reports
            if any(QualityChecker.issue_needs_retranslation(i.code) for i in r.issues)
        )

        if total == 0:
            stats_text = self.tr("No quality issues found — all translations look good.")
        else:
            stats_text = self.tr(
                "{errors} error(s)  ·  {warnings} warning(s)  ·  {infos} info  "
                "across {total} string(s)  "
                "({fixable} auto-fixable  ·  {retrans} need retranslation)"
            ).format(
                errors=errors, warnings=warnings, infos=infos, total=total,
                fixable=n_fixable, retrans=n_retranslatable,
            )

        stats_lbl = QLabel(stats_text)
        stats_lbl.setStyleSheet("font-weight: bold; padding: 4px 0;")
        stats_lbl.setWordWrap(True)
        root.addWidget(stats_lbl)

        # ── Filter / export bar ────────────────────────────────────────────────
        filter_bar = QHBoxLayout()
        filter_bar.addWidget(QLabel(self.tr("Severity:")))

        self.combo_filter = QComboBox()
        self.combo_filter.addItem(self.tr("All"), "all")
        self.combo_filter.addItem(self.tr("Errors"), SEVERITY_ERROR)
        self.combo_filter.addItem(self.tr("Warnings"), SEVERITY_WARNING)
        self.combo_filter.addItem(self.tr("Info"), SEVERITY_INFO)
        self.combo_filter.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(self.combo_filter)

        filter_bar.addSpacing(12)
        filter_bar.addWidget(QLabel(self.tr("Error code:")))

        self.combo_code_filter = QComboBox()
        self.combo_code_filter.setMinimumWidth(200)
        self.combo_code_filter.setToolTip(self.tr(
            "Filter rows by a specific issue code.\n"
            "Only codes that appear in the current results are listed."
        ))
        self.combo_code_filter.currentIndexChanged.connect(self._apply_filter)
        filter_bar.addWidget(self.combo_code_filter)
        self._populate_code_filter()   # fill from reports

        filter_bar.addStretch()

        btn_export = QPushButton(self.tr("Export Report…"))
        btn_export.setToolTip(self.tr(
            "Export the full quality report.\n"
            "Choose format by file extension:\n"
            "  .json — reimportable report (use after reload)\n"
            "  .csv  — spreadsheet, one row per issue, full text\n"
            "  .txt  — human-readable log, full text\n"
            "  .html — formatted HTML report"
        ))
        btn_export.clicked.connect(self._export_report)
        filter_bar.addWidget(btn_export)
        root.addLayout(filter_bar)

        # ── Action bar ─────────────────────────────────────────────────────────
        action_bar = QHBoxLayout()
        action_bar.setSpacing(4)

        btn_sel_err = QPushButton(self.tr("Select Errors"))
        btn_sel_err.setToolTip(self.tr("Select all error-severity rows"))
        btn_sel_err.clicked.connect(lambda: self._select_by_severity(SEVERITY_ERROR))
        action_bar.addWidget(btn_sel_err)

        btn_sel_warn = QPushButton(self.tr("Select Warnings"))
        btn_sel_warn.setToolTip(self.tr("Select all warning-severity rows"))
        btn_sel_warn.clicked.connect(lambda: self._select_by_severity(SEVERITY_WARNING))
        action_bar.addWidget(btn_sel_warn)

        btn_sel_all = QPushButton(self.tr("Select All"))
        btn_sel_all.clicked.connect(self.table.selectAll if hasattr(self, "table") else lambda: None)
        action_bar.addWidget(btn_sel_all)

        btn_clear = QPushButton(self.tr("Clear"))
        btn_clear.clicked.connect(lambda: self.table.clearSelection() if hasattr(self, "table") else None)
        action_bar.addWidget(btn_clear)

        action_bar.addSpacing(12)

        sep = QLabel("|")
        sep.setStyleSheet("color: #9ca3af;")
        action_bar.addWidget(sep)

        action_bar.addSpacing(4)

        self.btn_autofix = QPushButton(self.tr("Auto-Fix Selected"))
        self.btn_autofix.setToolTip(self.tr(
            "Apply mechanical fixes to selected strings:\n"
            "• Restore missing newlines\n"
            "• Fix leading whitespace\n"
            "• Remove Russian character leakage\n"
            "• Append missing game tags\n"
            "• Remove extra game tags\n"
            "• Drop unencodable characters"
        ))
        self.btn_autofix.setEnabled(False)
        self.btn_autofix.clicked.connect(self._auto_fix_selected)
        action_bar.addWidget(self.btn_autofix)

        self.btn_queue_retrans = QPushButton(self.tr("Queue Retranslation"))
        self.btn_queue_retrans.setToolTip(self.tr(
            "Queue selected strings for AI retranslation.\n"
            "The model will receive feedback about what went wrong.\n"
            "Retranslation starts after you close this dialog."
        ))
        self.btn_queue_retrans.setEnabled(False)
        self.btn_queue_retrans.clicked.connect(self._queue_retranslation)
        action_bar.addWidget(self.btn_queue_retrans)

        self.btn_queue_all_errors = QPushButton(self.tr("Queue All Errors"))
        self.btn_queue_all_errors.setToolTip(self.tr(
            "Queue ALL strings with errors for retranslation (no selection needed)"
        ))
        self.btn_queue_all_errors.setEnabled(
            any(r.severity == SEVERITY_ERROR for r in self._all_reports)
        )
        self.btn_queue_all_errors.clicked.connect(self._queue_all_errors)
        action_bar.addWidget(self.btn_queue_all_errors)

        action_bar.addStretch()
        root.addLayout(action_bar)

        # ── Issue table ────────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            self.tr("Severity"),
            self.tr("String ID"),
            self.tr("Original"),
            self.tr("Translation"),
            self.tr("Issue codes"),
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(False)  # We colour by severity instead

        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)

        self.table.currentCellChanged.connect(self._on_current_cell_changed)
        self.table.cellDoubleClicked.connect(self._jump_current)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        splitter.addWidget(self.table)

        # ── Detail panel ───────────────────────────────────────────────────────
        detail_grp = QGroupBox(self.tr("Issue Details"))
        detail_layout = QVBoxLayout(detail_grp)
        self.txt_detail = QTextEdit()
        self.txt_detail.setReadOnly(True)
        self.txt_detail.setFixedHeight(130)
        detail_layout.addWidget(self.txt_detail)
        splitter.addWidget(detail_grp)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter)

        # ── Button bar ─────────────────────────────────────────────────────────
        btn_bar = QHBoxLayout()

        self.btn_jump = QPushButton(self.tr("Jump to String in Table"))
        self.btn_jump.setEnabled(False)
        self.btn_jump.clicked.connect(self._jump_current)
        btn_bar.addWidget(self.btn_jump)

        btn_bar.addStretch()

        self.lbl_queue_status = QLabel("")
        self.lbl_queue_status.setStyleSheet("color: #059669; font-weight: bold;")
        btn_bar.addWidget(self.lbl_queue_status)

        btn_close = QPushButton(self.tr("Close"))
        btn_close.setDefault(True)
        btn_close.clicked.connect(self.accept)
        btn_bar.addWidget(btn_close)
        root.addLayout(btn_bar)

        # Wire up "Select All" / "Clear" now that self.table exists
        btn_sel_all.clicked.disconnect()
        btn_sel_all.clicked.connect(self.table.selectAll)
        btn_clear.clicked.disconnect()
        btn_clear.clicked.connect(self.table.clearSelection)

    # ── Table population ───────────────────────────────────────────────────────

    def _populate(self) -> None:
        self.table.setRowCount(0)
        for report in self._shown_reports:
            r = self.table.rowCount()
            self.table.insertRow(r)

            sev = report.severity
            row_bg = _SEV_ROW_BG.get(sev)

            sev_item = QTableWidgetItem(_SEV_LABEL.get(sev, sev))
            sev_item.setForeground(_SEV_COLOR.get(sev, QColor()))
            sev_item.setData(Qt.ItemDataRole.UserRole, report.row_index)
            self.table.setItem(r, 0, sev_item)

            self.table.setItem(r, 1, QTableWidgetItem(f"0x{report.string_id:08X}"))
            self.table.setItem(
                r, 2,
                QTableWidgetItem(
                    report.original[:80] + ("…" if len(report.original) > 80 else "")
                ),
            )
            self.table.setItem(
                r, 3,
                QTableWidgetItem(
                    (report.translated or "<empty>")[:80]
                    + ("…" if len(report.translated or "") > 80 else "")
                ),
            )
            codes = "  ".join(f"[{i.code}]" for i in report.issues)
            self.table.setItem(r, 4, QTableWidgetItem(codes))

            if row_bg:
                for col in range(self.table.columnCount()):
                    item = self.table.item(r, col)
                    if item:
                        item.setBackground(row_bg)

    # ── Action helpers ─────────────────────────────────────────────────────────

    def _selected_report_indices(self) -> List[int]:
        """Return sorted unique row indices of currently selected table rows."""
        return sorted({item.row() for item in self.table.selectedItems()})

    def _update_action_buttons(self) -> None:
        selected = self._selected_report_indices()
        n_fixable = 0
        n_retranslatable = 0
        for row in selected:
            if row >= len(self._shown_reports):
                continue
            report = self._shown_reports[row]
            if any(QualityChecker.issue_can_autofix(i.code) for i in report.issues):
                n_fixable += 1
            if any(QualityChecker.issue_needs_retranslation(i.code) for i in report.issues):
                n_retranslatable += 1

        can_fix = n_fixable > 0 and self._table_model is not None and self._checker is not None
        self.btn_autofix.setEnabled(can_fix)
        self.btn_autofix.setText(
            self.tr("Auto-Fix Selected ({n})").format(n=n_fixable)
            if n_fixable else self.tr("Auto-Fix Selected")
        )

        self.btn_queue_retrans.setEnabled(n_retranslatable > 0)
        self.btn_queue_retrans.setText(
            self.tr("Queue Retranslation ({n})").format(n=n_retranslatable)
            if n_retranslatable else self.tr("Queue Retranslation")
        )

    def _select_by_severity(self, severity: str) -> None:
        self.table.clearSelection()
        for row, report in enumerate(self._shown_reports):
            if report.severity == severity:
                for col in range(self.table.columnCount()):
                    item = self.table.item(row, col)
                    if item:
                        item.setSelected(True)

    # ── Auto-fix ───────────────────────────────────────────────────────────────

    @Slot()
    def _auto_fix_selected(self) -> None:
        table_model = self._table_model
        checker = self._checker
        if not table_model or not checker:
            return
        selected = self._selected_report_indices()
        fix_log: List[str] = []

        for row in selected:
            if row >= len(self._shown_reports):
                continue
            report = self._shown_reports[row]
            # Read the live translation from the table model — report.translated
            # may be stale if the string was edited or partially fixed already.
            if 0 <= report.row_index < len(table_model._data):
                current_translated = table_model._data[report.row_index].get(
                    "translated", report.translated
                )
            else:
                current_translated = report.translated
            fixed, applied = checker.auto_fix(
                report.original, current_translated, report
            )
            if applied:
                table_model.set_translated_text(report.row_index, fixed)
                fix_log.append(
                    f"  Row {report.row_index} (0x{report.string_id:08X}): "
                    + "; ".join(applied)
                )

        if fix_log:
            self._refresh_reports()
            QMessageBox.information(
                self,
                self.tr("Auto-Fix Applied"),
                self.tr("Fixed {n} string(s):\n{log}").format(
                    n=len(fix_log),
                    log="\n".join(fix_log[:25])
                    + ("\n…" if len(fix_log) > 25 else ""),
                ),
            )
        else:
            QMessageBox.information(
                self,
                self.tr("Auto-Fix"),
                self.tr("No automatically fixable issues found in the selected strings."),
            )

    # ── Retranslation queuing ──────────────────────────────────────────────────

    @Slot()
    def _queue_retranslation(self) -> None:
        """Queue selected strings for retranslation with quality feedback."""
        selected = self._selected_report_indices()
        self._add_to_queue([
            self._shown_reports[r]
            for r in selected
            if r < len(self._shown_reports)
            and any(
                QualityChecker.issue_needs_retranslation(i.code)
                for i in self._shown_reports[r].issues
            )
        ])

    @Slot()
    def _queue_all_errors(self) -> None:
        """Queue every error-severity string for retranslation."""
        self._add_to_queue([
            r for r in self._all_reports if r.severity == SEVERITY_ERROR
        ])

    def _add_to_queue(self, reports: List[QualityReport]) -> None:
        existing = {ri for ri, _ in self.pending_retranslations}
        added = 0
        for report in reports:
            if report.row_index in existing:
                continue
            hint = QualityChecker.build_retry_hint(report.issues)
            self.pending_retranslations.append((report.row_index, hint))
            existing.add(report.row_index)
            added += 1

        if added == 0:
            QMessageBox.information(
                self,
                self.tr("Retranslation Queue"),
                self.tr("All selected strings are already in the queue."),
            )
            return

        total = len(self.pending_retranslations)
        self.lbl_queue_status.setText(
            self.tr("{total} string(s) queued for retranslation").format(total=total)
        )
        QMessageBox.information(
            self,
            self.tr("Queued for Retranslation"),
            self.tr(
                "{added} string(s) added to retranslation queue.\n"
                "Total queued: {total}\n\n"
                "Close this dialog to start retranslation."
            ).format(added=added, total=total),
        )

    # ── Report refresh ─────────────────────────────────────────────────────────

    def _refresh_reports(self) -> None:
        """Re-run quality checks on the (now-updated) table data and redisplay."""
        if not self._table_model or not self._checker:
            return
        fresh = self._checker.check_all(self._table_model._data)
        self._all_reports = fresh
        self._populate_code_filter()
        self._apply_filter()

    # ── Slots ──────────────────────────────────────────────────────────────────

    @Slot()
    def _on_selection_changed(self) -> None:
        self._update_action_buttons()

    @Slot(int, int, int, int)
    def _on_current_cell_changed(
        self, cur_row: int, _cur_col: int, _prev_row: int, _prev_col: int
    ) -> None:
        self.btn_jump.setEnabled(cur_row >= 0)
        if cur_row < 0 or cur_row >= len(self._shown_reports):
            self.txt_detail.clear()
            return

        report = self._shown_reports[cur_row]
        lines: List[str] = []
        for issue in report.issues:
            fixable = "✔ auto-fixable" if QualityChecker.issue_can_autofix(issue.code) else \
                      "↻ needs retranslation" if QualityChecker.issue_needs_retranslation(issue.code) else ""
            suffix = f"  [{fixable}]" if fixable else ""
            lines.append(f"[{issue.severity.upper()}] {issue.code}: {issue.message}{suffix}")
            if issue.detail:
                lines.append(f"   → {issue.detail}")
        self.txt_detail.setPlainText("\n".join(lines))

    @Slot()
    def _jump_current(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._shown_reports):
            self.jump_to_row.emit(self._shown_reports[row].row_index)

    def _populate_code_filter(self) -> None:
        """Rebuild the code combo from all codes present in _all_reports."""
        codes: list = sorted({
            i.code
            for r in self._all_reports
            for i in r.issues
            if i.code
        })
        current = self.combo_code_filter.currentData()
        self.combo_code_filter.blockSignals(True)
        self.combo_code_filter.clear()
        self.combo_code_filter.addItem(self.tr("All codes"), "all")
        for code in codes:
            self.combo_code_filter.addItem(code, code)
        # Restore previous selection if still available
        idx = self.combo_code_filter.findData(current)
        self.combo_code_filter.setCurrentIndex(max(0, idx))
        self.combo_code_filter.blockSignals(False)

    @Slot()
    def _apply_filter(self) -> None:
        sev_val = self.combo_filter.currentData()
        code_val = self.combo_code_filter.currentData()

        def _matches(report: "QualityReport") -> bool:
            if sev_val != "all" and report.severity != sev_val:
                return False
            if code_val != "all":
                if not any(i.code == code_val for i in report.issues):
                    return False
            return True

        self._shown_reports = [r for r in self._all_reports if _matches(r)]
        self._populate()

    # ── Export ─────────────────────────────────────────────────────────────────

    @Slot()
    def _export_report(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_name = f"quality_report_{now_str}"

        path, selected_filter = get_save_filename(
            self,
            self.tr("Export Quality Report"),
            default_name,
            self.tr(
                "JSON Report — reimportable (*.json);;"
                "CSV Spreadsheet (*.csv);;"
                "Text Log (*.txt);;"
                "HTML Report (*.html);;"
                "All Files (*)"
            ),
        )
        if not path:
            return

        path_lower = path.lower()
        if "json" in selected_filter or path_lower.endswith(".json"):
            self._write_file(path, build_json(self._all_reports), encoding="utf-8")
        elif "csv" in selected_filter or path_lower.endswith(".csv"):
            self._write_file(path, build_csv(self._all_reports), encoding="utf-8-sig")
        elif "html" in selected_filter or path_lower.endswith((".html", ".htm")):
            self._write_file(path, build_html(self._all_reports), encoding="utf-8")
        else:
            self._write_file(path, build_txt_log(self._all_reports), encoding="utf-8")

    def _write_file(self, path: str, content: str, encoding: str = "utf-8") -> None:
        try:
            with open(path, "w", encoding=encoding, newline="") as f:
                f.write(content)
            logger.info(f"Exported quality report to {path}")
        except Exception as exc:
            logger.error(f"Failed to export quality report to {path}: {exc}")
            QMessageBox.critical(
                self,
                self.tr("Export Failed"),
                self.tr("Could not write report:\n{error}").format(error=exc),
            )
