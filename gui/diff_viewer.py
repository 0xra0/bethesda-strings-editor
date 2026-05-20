"""
Side-by-side diff viewer for Bethesda string translation pairs.

Two modes depending on what data is available:
  • Source-vs-Translation  (no comparison file loaded)
  • Comparison-vs-Current  (comparison file loaded via "Compare with File…")

Granularity: word-level (default) or character-level.
Navigation:  string-level (Prev/Next row) and segment-level (Prev/Next change).
Editing:     right pane is editable; diff updates live on a 250 ms debounce.
Export:      self-contained HTML report.
"""

import difflib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)


# ── Color constants ────────────────────────────────────────────────────────────
# Dual-mode: light (#RRGGBB) and dark variants used at runtime.

_COLORS = {
    "delete": {"light": "#fee2e2", "dark": "#3b1212"},   # red-100 / dark red
    "insert": {"light": "#dcfce7", "dark": "#0f2b14"},   # green-100 / dark green
    "replace_del": {"light": "#fff3cd", "dark": "#2d2000"},  # amber-100 / dark amber
    "replace_ins": {"light": "#fef9c3", "dark": "#251e00"},  # yellow-100
    "equal":  {"light": "", "dark": ""},
}


def _is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return app.palette().base().color().lightness() < 128  # type: ignore[union-attr]



# ── Tokenisers ─────────────────────────────────────────────────────────────────

def _tokenise_word(text: str) -> List[str]:
    """Split into alternating word and whitespace/punctuation tokens."""
    return re.findall(r"\S+|\s+", text) or [""]


def _tokenise_char(text: str) -> List[str]:
    return list(text) or [""]


# ── Diff helpers ───────────────────────────────────────────────────────────────

@dataclass
class DiffSegment:
    """One non-equal opcode region for navigation."""
    tag: str           # replace | delete | insert
    right_start: int   # char offset in right plain text
    right_end: int
    left_anchor: str   # HTML anchor name in left pane


@dataclass
class DiffResult:
    left_html: str
    right_formats: List[Tuple[int, int, str]]  # (start, end, color_key)
    segments: List[DiffSegment]
    stats: "DiffStats"


@dataclass
class DiffStats:
    words_deleted: int = 0
    words_inserted: int = 0
    words_replaced: int = 0
    chars_deleted: int = 0
    chars_inserted: int = 0
    similarity: float = 0.0

    def summary(self) -> str:
        parts = []
        if self.words_replaced:
            parts.append(f"{self.words_replaced} word(s) changed")
        if self.words_deleted:
            parts.append(f"{self.words_deleted} word(s) removed")
        if self.words_inserted:
            parts.append(f"{self.words_inserted} word(s) added")
        if self.chars_deleted:
            parts.append(f"-{self.chars_deleted} chars")
        if self.chars_inserted:
            parts.append(f"+{self.chars_inserted} chars")
        if not parts:
            return "Identical"
        parts.append(f"Similarity: {self.similarity:.0%}")
        return "  |  ".join(parts)


def _compute_diff(
    text_a: str,
    text_b: str,
    granularity: str,
    dark: bool,
) -> DiffResult:
    """Compute a diff between two strings and return rendered artefacts."""
    tokenise = _tokenise_char if granularity == "char" else _tokenise_word
    tokens_a = tokenise(text_a)
    tokens_b = tokenise(text_b)

    mode = "dark" if dark else "light"
    sm = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
    opcodes = sm.get_opcodes()
    stats = DiffStats(similarity=sm.ratio())

    # ── Build left-pane HTML ───────────────────────────────────────────────────
    css_base = (
        "white-space:pre-wrap;font-family:'DejaVu Sans Mono',monospace;"
        "font-size:9pt;line-height:1.5;"
    )
    body_bg = "#1e293b" if dark else "#ffffff"
    text_color = "#f1f5f9" if dark else "#1e293b"
    left_parts = [
        f'<html><body style="background:{body_bg};color:{text_color};margin:8px">'
        f'<pre style="{css_base}">'
    ]
    seg_idx = 0
    for tag, i1, i2, j1, j2 in opcodes:
        chunk_a = "".join(tokens_a[i1:i2])
        if tag == "equal":
            left_parts.append(html.escape(chunk_a))
        elif tag == "delete":
            bg = _COLORS["delete"][mode]
            left_parts.append(
                f'<a name="seg{seg_idx}"></a>'
                f'<span style="background:{bg};text-decoration:line-through">'
                f"{html.escape(chunk_a)}</span>"
            )
            stats.words_deleted += len(chunk_a.split())
            stats.chars_deleted += len(chunk_a)
            seg_idx += 1
        elif tag == "replace":
            bg = _COLORS["replace_del"][mode]
            left_parts.append(
                f'<a name="seg{seg_idx}"></a>'
                f'<span style="background:{bg};text-decoration:line-through">'
                f"{html.escape(chunk_a)}</span>"
            )
            stats.words_replaced += len(chunk_a.split())
            stats.chars_deleted += len(chunk_a)
            # inserts counted below in right-side pass
        # insert: nothing to show on left side
    left_parts.append("</pre></body></html>")
    left_html = "".join(left_parts)

    # ── Compute right-pane format spans and segment list ───────────────────────
    right_formats: List[Tuple[int, int, str]] = []
    segments: List[DiffSegment] = []
    right_pos = 0
    seg_idx = 0

    for tag, i1, i2, j1, j2 in opcodes:
        chunk_b = "".join(tokens_b[j1:j2])
        chunk_len = len(chunk_b)
        if tag == "insert":
            right_formats.append((right_pos, right_pos + chunk_len, "insert"))
            segments.append(DiffSegment(
                tag="insert",
                right_start=right_pos,
                right_end=right_pos + chunk_len,
                left_anchor=f"seg{seg_idx}",
            ))
            stats.words_inserted += len(chunk_b.split())
            stats.chars_inserted += chunk_len
            seg_idx += 1
        elif tag == "replace":
            right_formats.append((right_pos, right_pos + chunk_len, "replace_ins"))
            segments.append(DiffSegment(
                tag="replace",
                right_start=right_pos,
                right_end=right_pos + chunk_len,
                left_anchor=f"seg{seg_idx}",
            ))
            stats.chars_inserted += chunk_len
            seg_idx += 1
        right_pos += chunk_len

    return DiffResult(
        left_html=left_html,
        right_formats=right_formats,
        segments=segments,
        stats=stats,
    )


# ── HTML export ────────────────────────────────────────────────────────────────

_EXPORT_CSS = """
body { font-family: 'Segoe UI', sans-serif; background: #f8fafc; color: #1e293b; margin: 0; }
h1 { background: #1e293b; color: #f1f5f9; padding: 16px 24px; margin: 0; font-size: 18px; }
.meta { background: #e2e8f0; padding: 8px 24px; font-size: 12px; color: #64748b; }
.string-block { border: 1px solid #cbd5e1; margin: 16px 24px; border-radius: 6px; overflow: hidden; }
.string-header { background: #334155; color: #f1f5f9; padding: 6px 12px; font-size: 11px; font-family: monospace; }
.panels { display: flex; }
.panel { flex: 1; padding: 10px 14px; font-family: 'DejaVu Sans Mono', monospace; font-size: 12px;
         white-space: pre-wrap; word-break: break-word; line-height: 1.55; }
.panel-left { border-right: 1px solid #cbd5e1; background: #fff; }
.panel-right { background: #f8fafc; }
.panel-label { font-weight: 600; font-size: 10px; text-transform: uppercase;
               letter-spacing: 0.05em; color: #64748b; padding: 4px 14px;
               background: #f1f5f9; border-bottom: 1px solid #e2e8f0; }
.panel-label-row { display: flex; border-bottom: 1px solid #e2e8f0; }
.panel-label-row div { flex: 1; }
.panel-label-row div:first-child { border-right: 1px solid #e2e8f0; }
.stats { padding: 5px 14px; font-size: 10px; color: #64748b; background: #f8fafc;
         border-top: 1px solid #e2e8f0; }
del { background: #fee2e2; text-decoration: line-through; }
ins { background: #dcfce7; text-decoration: none; }
.repl-del { background: #fff3cd; text-decoration: line-through; }
.repl-ins { background: #fef9c3; }
.summary { margin: 12px 24px; padding: 12px 16px; background: #fff; border: 1px solid #cbd5e1;
           border-radius: 6px; font-size: 13px; }
.summary table { border-collapse: collapse; }
.summary td { padding: 3px 12px 3px 0; }
"""


def _escape(t: str) -> str:
    return html.escape(t).replace("\n", "<br>")


_EMPTY_SPAN = '<em style="color:#94a3b8">(empty)</em>'


def _empty_html(content: str) -> str:
    return content if content else _EMPTY_SPAN


def build_html_report(
    rows: List[dict],
    comparison_data: Optional[Dict[int, str]],
    granularity: str,
    changed_only: bool,
) -> str:
    """Build a self-contained HTML diff report string."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode_label = "Comparison vs Current" if comparison_data else "Source vs Translation"
    gran_label = "Character" if granularity == "char" else "Word"

    blocks: List[str] = []
    total = 0
    changed = 0

    for row in rows:
        original = row.get("original", "")
        current = row.get("translated", "")
        string_id = row.get("id", 0)

        if comparison_data is not None:
            left_text = comparison_data.get(string_id, "")
            right_text = current
            left_label = "Comparison File"
            right_label = "Current Translation"
        else:
            left_text = original
            right_text = current
            left_label = "Original Source"
            right_label = "Translation"

        tokenise = _tokenise_char if granularity == "char" else _tokenise_word
        tokens_a = tokenise(left_text)
        tokens_b = tokenise(right_text)
        sm = difflib.SequenceMatcher(None, tokens_a, tokens_b, autojunk=False)
        opcodes = sm.get_opcodes()
        is_identical = all(tag == "equal" for tag, *_ in opcodes)

        total += 1
        if not is_identical:
            changed += 1

        if changed_only and is_identical:
            continue

        # Build left side HTML
        left_parts: List[str] = []
        for tag, i1, i2, j1, j2 in opcodes:
            chunk_a = "".join(tokens_a[i1:i2])
            if tag == "equal":
                left_parts.append(_escape(chunk_a))
            elif tag == "delete":
                left_parts.append(f"<del>{_escape(chunk_a)}</del>")
            elif tag == "replace":
                left_parts.append(f'<span class="repl-del">{_escape(chunk_a)}</span>')

        # Build right side HTML
        right_parts: List[str] = []
        for tag, i1, i2, j1, j2 in opcodes:
            chunk_b = "".join(tokens_b[j1:j2])
            if tag == "equal":
                right_parts.append(_escape(chunk_b))
            elif tag == "insert":
                right_parts.append(f"<ins>{_escape(chunk_b)}</ins>")
            elif tag == "replace":
                right_parts.append(f'<span class="repl-ins">{_escape(chunk_b)}</span>')

        sim_pct = f"{sm.ratio():.0%}"
        id_str = f"0x{string_id:08X}"
        status = row.get("status", "pending")
        diff_badge = "identical" if is_identical else f"similarity {sim_pct}"

        left_html_content = _EMPTY_SPAN if not left_text else _empty_html("".join(left_parts))
        right_html = _EMPTY_SPAN if not right_text else _empty_html("".join(right_parts))
        block = (
            f'<div class="string-block">'
            f'<div class="string-header">ID: {id_str} &nbsp;·&nbsp; Status: {status}'
            f' &nbsp;·&nbsp; {diff_badge}</div>'
            f'<div class="panel-label-row">'
            f'  <div class="panel-label">{html.escape(left_label)}</div>'
            f'  <div class="panel-label">{html.escape(right_label)}</div>'
            f'</div>'
            f'<div class="panels">'
            f'  <div class="panel panel-left">{left_html_content}</div>'
            f'  <div class="panel panel-right">{right_html}</div>'
            f'</div>'
            f'</div>'
        )
        blocks.append(block)

    summary = (
        f'<div class="summary">'
        f"<strong>Report summary</strong><br>"
        f"<table>"
        f"<tr><td>Total strings</td><td><strong>{total}</strong></td></tr>"
        f"<tr><td>Changed strings</td><td><strong>{changed}</strong></td></tr>"
        f"<tr><td>Identical strings</td><td><strong>{total - changed}</strong></td></tr>"
        f"</table>"
        f"</div>"
    )

    return (
        "<!DOCTYPE html><html><head>"
        f"<meta charset='utf-8'>"
        f"<title>Translation Diff Report — {now}</title>"
        f"<style>{_EXPORT_CSS}</style>"
        "</head><body>"
        f"<h1>Translation Diff Report</h1>"
        f'<div class="meta">'
        f"Mode: {mode_label} &nbsp;·&nbsp; Granularity: {gran_label}"
        f" &nbsp;·&nbsp; Generated: {now}"
        f"</div>"
        + summary
        + "\n".join(blocks)
        + "</body></html>"
    )


# ── Dialog ─────────────────────────────────────────────────────────────────────

class DiffViewerDialog(QDialog):
    """
    Side-by-side diff dialog for translation review and inline editing.

    Emits translation_updated(row_index, new_text) whenever the user saves
    an edit (OK, or by navigating to another row with auto-save on).
    """

    translation_updated = Signal(int, str)

    def __init__(
        self,
        rows: List[dict],
        initial_row: int = 0,
        comparison_data: Optional[Dict[int, str]] = None,
        source_lang: str = "English",
        target_lang: str = "Ukrainian",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._rows = rows
        self._comparison_data = comparison_data  # None → source vs translation mode
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._current_row_idx: int = max(0, min(initial_row, len(rows) - 1))
        self._granularity: str = "char" if comparison_data is None else "word"
        self._segments: List[DiffSegment] = []
        self._current_segment: int = -1
        self._updating_format: bool = False
        self._pending_update = QTimer(self)
        self._pending_update.setSingleShot(True)
        self._pending_update.timeout.connect(self._update_diff)

        self.setWindowTitle(self.tr("String Diff Viewer"))
        self.resize(1280, 760)
        self.setMinimumSize(900, 560)
        self._build_ui()
        self._load_row(self._current_row_idx)

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(6)

        # ── Toolbar ────────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        # String navigation
        self._btn_prev_row = QPushButton("◀ Prev")
        self._btn_prev_row.setToolTip("Go to previous string (Ctrl+Left)")
        self._btn_prev_row.setShortcut("Ctrl+Left")
        self._btn_prev_row.clicked.connect(self._go_prev_row)

        self._lbl_position = QLabel()
        self._lbl_position.setAlignment(Qt.AlignCenter)
        self._lbl_position.setMinimumWidth(100)
        self._lbl_position.setStyleSheet("font-weight: bold;")

        self._btn_next_row = QPushButton("Next ▶")
        self._btn_next_row.setToolTip("Go to next string (Ctrl+Right)")
        self._btn_next_row.setShortcut("Ctrl+Right")
        self._btn_next_row.clicked.connect(self._go_next_row)

        # Changed-only filter
        self._chk_changed_only = QCheckBox("Changed only")
        self._chk_changed_only.setToolTip("Navigate only between strings with differences")
        self._chk_changed_only.toggled.connect(self._refresh_nav_state)

        sep1 = _make_vsep()

        # Granularity
        lbl_gran = QLabel("Granularity:")
        self._combo_gran = QComboBox()
        self._combo_gran.addItem("Word", "word")
        self._combo_gran.addItem("Character", "char")
        default_idx = 1 if self._comparison_data is None else 0
        self._combo_gran.setCurrentIndex(default_idx)
        self._combo_gran.currentIndexChanged.connect(self._on_granularity_changed)

        sep2 = _make_vsep()

        # Segment navigation
        self._btn_prev_seg = QPushButton("↑ Prev change")
        self._btn_prev_seg.setToolTip("Jump to previous changed segment (Alt+Up)")
        self._btn_prev_seg.setShortcut("Alt+Up")
        self._btn_prev_seg.clicked.connect(self._go_prev_segment)

        self._lbl_seg_nav = QLabel("—")
        self._lbl_seg_nav.setAlignment(Qt.AlignCenter)
        self._lbl_seg_nav.setMinimumWidth(80)

        self._btn_next_seg = QPushButton("↓ Next change")
        self._btn_next_seg.setToolTip("Jump to next changed segment (Alt+Down)")
        self._btn_next_seg.setShortcut("Alt+Down")
        self._btn_next_seg.clicked.connect(self._go_next_segment)

        toolbar.addWidget(self._btn_prev_row)
        toolbar.addWidget(self._lbl_position)
        toolbar.addWidget(self._btn_next_row)
        toolbar.addWidget(self._chk_changed_only)
        toolbar.addWidget(sep1)
        toolbar.addWidget(lbl_gran)
        toolbar.addWidget(self._combo_gran)
        toolbar.addWidget(sep2)
        toolbar.addWidget(self._btn_prev_seg)
        toolbar.addWidget(self._lbl_seg_nav)
        toolbar.addWidget(self._btn_next_seg)
        toolbar.addStretch()

        root.addLayout(toolbar)

        # ── Column headers ─────────────────────────────────────────────────────
        header_row = QHBoxLayout()
        header_row.setSpacing(0)
        self._lbl_left_header = QLabel()
        self._lbl_right_header = QLabel()
        for lbl in (self._lbl_left_header, self._lbl_right_header):
            lbl.setStyleSheet(
                "font-weight:600;font-size:11px;text-transform:uppercase;"
                "letter-spacing:0.05em;padding:4px 8px;"
                "border-bottom:2px solid #475569;"
            )
            lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header_row.addWidget(self._lbl_left_header, stretch=1)
        # Tiny fixed spacer matching the splitter handle width
        spacer_label = QLabel()
        spacer_label.setFixedWidth(6)
        header_row.addWidget(spacer_label)
        header_row.addWidget(self._lbl_right_header, stretch=1)
        root.addLayout(header_row)

        # ── Splitter with diff panes ───────────────────────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(6)

        self._left_pane = QTextBrowser()
        self._left_pane.setOpenLinks(False)
        self._left_pane.setFont(_mono_font())

        self._right_pane = QTextEdit()
        self._right_pane.setAcceptRichText(False)
        self._right_pane.setFont(_mono_font())
        self._right_pane.document().contentsChange.connect(self._on_contents_change)

        splitter.addWidget(self._left_pane)
        splitter.addWidget(self._right_pane)
        splitter.setSizes([600, 600])
        root.addWidget(splitter, stretch=1)

        # ── Stats bar ──────────────────────────────────────────────────────────
        stats_bar = QHBoxLayout()
        self._lbl_stats = QLabel()
        self._lbl_stats.setStyleSheet(
            "color:#94a3b8;font-size:11px;font-family:monospace;padding:2px 4px;"
        )
        stats_bar.addWidget(self._lbl_stats)
        stats_bar.addStretch()

        self._lbl_id = QLabel()
        self._lbl_id.setStyleSheet("color:#94a3b8;font-size:11px;font-family:monospace;")
        stats_bar.addWidget(self._lbl_id)
        root.addLayout(stats_bar)

        # ── Bottom buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._btn_export = QPushButton("Export HTML Report…")
        self._btn_export.clicked.connect(self._export_html)

        self._btn_ok = QPushButton("Save && Close")
        self._btn_ok.setProperty("primary", True)
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._save_and_accept)

        btn_cancel = QPushButton("Close")
        btn_cancel.clicked.connect(self.reject)

        btn_row.addWidget(self._btn_export)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_ok)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    # ── Row loading ────────────────────────────────────────────────────────────

    def _load_row(self, row_idx: int) -> None:
        """Populate both panes with the string pair at *row_idx*."""
        if not self._rows or not (0 <= row_idx < len(self._rows)):
            return
        self._current_row_idx = row_idx
        self._current_segment = -1

        row = self._rows[row_idx]
        string_id = row.get("id", 0)
        original = row.get("original", "")
        current = row.get("translated", "")

        if self._comparison_data is not None:
            left_text = self._comparison_data.get(string_id, "")
            self._lbl_left_header.setText("Comparison File")
            self._lbl_right_header.setText(f"Current Translation — {self._target_lang}  (editable)")
        else:
            left_text = original
            self._lbl_left_header.setText(f"Original Source — {self._source_lang}")
            self._lbl_right_header.setText(f"Translation — {self._target_lang}  (editable)")

        self._left_text = left_text
        self._lbl_id.setText(f"ID: 0x{string_id:08X}")

        # Load right pane text without triggering diff (block temporarily)
        self._updating_format = True
        try:
            self._right_pane.setPlainText(current)
        finally:
            self._updating_format = False

        self._update_diff()
        self._refresh_nav_state()

    # ── Diff computation and rendering ─────────────────────────────────────────

    def _update_diff(self) -> None:
        """Recompute diff and refresh both panes. Called on load and after debounce."""
        left_text = getattr(self, "_left_text", "")
        right_text = self._right_pane.toPlainText()
        dark = _is_dark()
        gran = self._combo_gran.currentData()

        result = _compute_diff(left_text, right_text, gran, dark)
        self._segments = result.segments
        self._current_segment = -1

        # ── Update left pane ───────────────────────────────────────────────────
        self._left_pane.setHtml(result.left_html)

        # ── Update right pane format overlay ──────────────────────────────────
        self._apply_right_formats(result.right_formats)

        # ── Stats ──────────────────────────────────────────────────────────────
        self._lbl_stats.setText(result.stats.summary())

        # ── Segment nav label ──────────────────────────────────────────────────
        n = len(self._segments)
        if n == 0:
            self._lbl_seg_nav.setText("No changes")
        else:
            self._lbl_seg_nav.setText(f"0 / {n}")

        self._btn_prev_seg.setEnabled(n > 0)
        self._btn_next_seg.setEnabled(n > 0)

    def _apply_right_formats(self, formats: List[Tuple[int, int, str]]) -> None:
        """Apply QTextCharFormat highlights to the right pane without changing text."""
        self._updating_format = True
        try:
            doc = self._right_pane.document()
            # Clear existing character formatting
            cursor = QTextCursor(doc)
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.setCharFormat(QTextCharFormat())
            cursor.clearSelection()

            dark = _is_dark()
            mode = "dark" if dark else "light"

            for start, end, color_key in formats:
                bg_hex = _COLORS[color_key][mode]
                if not bg_hex:
                    continue
                fmt = QTextCharFormat()
                fmt.setBackground(QColor(bg_hex))
                c = QTextCursor(doc)
                c.setPosition(start)
                c.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                c.mergeCharFormat(fmt)
        finally:
            self._updating_format = False

    # ── Segment navigation ─────────────────────────────────────────────────────

    @Slot()
    def _go_next_segment(self) -> None:
        if not self._segments:
            return
        self._current_segment = (self._current_segment + 1) % len(self._segments)
        self._scroll_to_segment(self._current_segment)

    @Slot()
    def _go_prev_segment(self) -> None:
        if not self._segments:
            return
        n = len(self._segments)
        self._current_segment = (self._current_segment - 1) % n
        self._scroll_to_segment(self._current_segment)

    def _scroll_to_segment(self, seg_idx: int) -> None:
        if not (0 <= seg_idx < len(self._segments)):
            return
        seg = self._segments[seg_idx]
        n = len(self._segments)
        self._lbl_seg_nav.setText(f"{seg_idx + 1} / {n}")

        # Scroll left pane to anchor
        self._left_pane.scrollToAnchor(seg.left_anchor)

        # Move cursor in right pane to segment start
        cursor = QTextCursor(self._right_pane.document())
        cursor.setPosition(min(seg.right_start, len(self._right_pane.toPlainText())))
        self._right_pane.setTextCursor(cursor)
        self._right_pane.ensureCursorVisible()

    # ── Row navigation ─────────────────────────────────────────────────────────

    def _effective_rows(self) -> List[int]:
        """Return indices into _rows to navigate, filtered by changed-only if set."""
        if not self._chk_changed_only.isChecked():
            return list(range(len(self._rows)))
        result = []
        for i, row in enumerate(self._rows):
            current = row.get("translated", "")
            string_id = row.get("id", 0)
            left = (
                self._comparison_data.get(string_id, "")
                if self._comparison_data is not None
                else row.get("original", "")
            )
            if left != current:
                result.append(i)
        return result

    def _refresh_nav_state(self) -> None:
        effective = self._effective_rows()
        n = len(effective)
        pos = effective.index(self._current_row_idx) + 1 if self._current_row_idx in effective else 0
        self._lbl_position.setText(f"{pos} / {n}" if n else "0 / 0")
        self._btn_prev_row.setEnabled(n > 1)
        self._btn_next_row.setEnabled(n > 1)

    @Slot()
    def _go_prev_row(self) -> None:
        self._auto_save_current()
        effective = self._effective_rows()
        if not effective:
            return
        try:
            pos = effective.index(self._current_row_idx)
        except ValueError:
            pos = 0
        new_pos = (pos - 1) % len(effective)
        self._load_row(effective[new_pos])

    @Slot()
    def _go_next_row(self) -> None:
        self._auto_save_current()
        effective = self._effective_rows()
        if not effective:
            return
        try:
            pos = effective.index(self._current_row_idx)
        except ValueError:
            pos = 0
        new_pos = (pos + 1) % len(effective)
        self._load_row(effective[new_pos])

    # ── Save / accept / cancel ─────────────────────────────────────────────────

    def _auto_save_current(self) -> None:
        """Emit translation_updated for the current row without closing."""
        new_text = self._right_pane.toPlainText()
        original = self._rows[self._current_row_idx].get("translated", "")
        if new_text != original:
            self._rows[self._current_row_idx]["translated"] = new_text
            self.translation_updated.emit(self._current_row_idx, new_text)

    @Slot()
    def _save_and_accept(self) -> None:
        self._auto_save_current()
        self.accept()

    # ── Granularity change ─────────────────────────────────────────────────────

    @Slot()
    def _on_granularity_changed(self) -> None:
        self._granularity = self._combo_gran.currentData()
        self._update_diff()

    # ── Debounced text change ──────────────────────────────────────────────────

    @Slot(int, int, int)
    def _on_contents_change(self, position: int, removed: int, added: int) -> None:
        del position, removed, added
        if self._updating_format:
            return
        self._pending_update.start(250)

    # ── Export ─────────────────────────────────────────────────────────────────

    @Slot()
    def _export_html(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self,
            self.tr("Export Diff as HTML"),
            "diff_report.html",
            "HTML Files (*.html *.htm);;All Files (*)",
        )
        if not path:
            return

        gran = self._combo_gran.currentData()
        report_html = build_html_report(
            self._rows,
            self._comparison_data,
            gran,
            changed_only=self._chk_changed_only.isChecked(),
        )
        try:
            Path(path).write_text(report_html, encoding="utf-8")
            logger.info("Diff HTML report exported to %s", path)
            # Open in browser if possible
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            except Exception:
                pass
        except OSError as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))


# ── Utilities ──────────────────────────────────────────────────────────────────

def _make_vsep() -> QWidget:
    sep = QWidget()
    sep.setFixedWidth(1)
    sep.setFixedHeight(20)
    sep.setStyleSheet("background:#475569;")
    return sep


def _mono_font() -> QFont:
    font = QFont()
    font.setFamily("DejaVu Sans Mono")
    font.setPointSize(9)
    return font


# ── Convenience constructor ────────────────────────────────────────────────────

def open_diff_viewer(
    parent: QWidget,
    rows: List[dict],
    initial_row: int,
    comparison_data: Optional[Dict[int, str]] = None,
    source_lang: str = "English",
    target_lang: str = "Ukrainian",
) -> Optional[DiffViewerDialog]:
    """
    Open a non-modal DiffViewerDialog.  Returns the dialog so the caller can
    connect translation_updated.  Returns None when there are no rows.
    """
    if not rows:
        return None
    dlg = DiffViewerDialog(
        rows=rows,
        initial_row=initial_row,
        comparison_data=comparison_data,
        source_lang=source_lang,
        target_lang=target_lang,
        parent=parent,
    )
    return dlg
