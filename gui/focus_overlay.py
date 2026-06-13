"""
Zen / Focus Mode overlay.

A full-screen, distraction-free editor that shows one string at a time:
  - Source text (read-only)
  - Translation text (editable)
  - Minimal navigation: Prev / Next Untranslated / Approve & Next

Keyboard shortcuts (work even when focus is in the text edit):
  Ctrl+Enter       — approve translation and jump to next untranslated
  F7               — jump to next untranslated without saving
  Shift+F7         — jump to previous string
  Ctrl+Z / Ctrl+Y  — undo / redo (handled by QTextEdit natively)
  Esc              — exit focus mode

The overlay communicates with the main model via signals rather than
direct method calls to keep the coupling uni-directional.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QTextEdit, QVBoxLayout, QWidget,
)

# ── Palette / style ───────────────────────────────────────────────────────────

_BG          = "#0d1117"
_PANEL_BG    = "#161b22"
_PANEL_TRANS = "#0e1f3a"
_BORDER      = "#30363d"
_BORDER_ACT  = "#3b82f6"
_TEXT        = "#e6edf3"
_TEXT_DIM    = "#8b949e"
_GREEN       = "#238636"
_GREEN_HO    = "#2ea043"
_STATUS_DONE = "#238636"
_STATUS_PEND = "#e3b341"
_STATUS_ERR  = "#da3633"

_STYLE = f"""
QDialog {{
    background: {_BG};
}}
QWidget#focus_root {{
    background: {_BG};
}}
/* header / footer -------------------------------------------------------- */
QWidget#header_bar, QWidget#footer_bar {{
    background: {_BG};
}}
QLabel#counter_label {{
    color: {_TEXT_DIM};
    font-size: 12px;
}}
QLabel#id_label {{
    color: {_TEXT_DIM};
    font-size: 12px;
    font-family: monospace;
}}
QPushButton#exit_btn {{
    background: transparent;
    color: {_TEXT_DIM};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 4px 14px;
    font-size: 12px;
}}
QPushButton#exit_btn:hover {{
    color: {_TEXT};
    border-color: #6e7681;
}}
/* source panel ----------------------------------------------------------- */
QWidget#source_panel {{
    background: {_PANEL_BG};
    border: 1px solid {_BORDER};
    border-radius: 6px;
}}
QLabel#source_hdr {{
    color: {_TEXT_DIM};
    font-size: 10px;
    letter-spacing: 1px;
    padding: 8px 12px 0 12px;
}}
QTextEdit#source_edit {{
    background: transparent;
    color: {_TEXT};
    border: none;
    font-size: 15px;
    padding: 4px 12px 12px 12px;
    selection-background-color: #264f78;
}}
/* translation panel ------------------------------------------------------ */
QWidget#trans_panel {{
    background: {_PANEL_TRANS};
    border: 2px solid {_BORDER};
    border-radius: 6px;
}}
QWidget#trans_panel:focus-within {{
    border: 2px solid {_BORDER_ACT};
}}
QLabel#trans_hdr {{
    color: {_TEXT_DIM};
    font-size: 10px;
    letter-spacing: 1px;
    padding: 8px 12px 0 12px;
}}
QTextEdit#trans_edit {{
    background: transparent;
    color: {_TEXT};
    border: none;
    font-size: 15px;
    padding: 4px 12px 12px 12px;
    selection-background-color: #264f78;
}}
/* footer buttons --------------------------------------------------------- */
QPushButton#nav_btn {{
    background: transparent;
    color: {_TEXT_DIM};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 6px 18px;
    font-size: 13px;
    min-width: 90px;
}}
QPushButton#nav_btn:hover {{
    color: {_TEXT};
    border-color: #6e7681;
    background: #21262d;
}}
QPushButton#nav_btn:disabled {{
    color: #444c56;
    border-color: #30363d;
}}
QPushButton#approve_btn {{
    background: {_GREEN};
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 28px;
    font-size: 13px;
    font-weight: bold;
    min-width: 200px;
}}
QPushButton#approve_btn:hover {{
    background: {_GREEN_HO};
}}
QPushButton#approve_btn:disabled {{
    background: #21262d;
    color: #444c56;
}}
"""


class FocusModeOverlay(QDialog):
    """Full-screen, distraction-free single-string translation editor."""

    # Emitted when the user saves a translation (source_row, new_text)
    translation_committed = Signal(int, str)
    # Emitted when user navigates so main window can sync selection
    row_navigated = Signal(int)

    def __init__(self, table_model, initial_row: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.Dialog)
        self.setWindowTitle(self.tr("Focus Mode"))
        self.setWindowFlags(
            Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setStyleSheet(_STYLE)

        self._model = table_model
        self._current_row: int = max(0, initial_row)

        self._build_ui()
        self._install_shortcuts()
        self._load_row(self._current_row)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        # Root widget so we can name it for the stylesheet
        root = QWidget(self)
        root.setObjectName("focus_root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(40, 24, 40, 24)
        layout.setSpacing(16)

        # ── Header bar ────────────────────────────────────────────
        header = QWidget()
        header.setObjectName("header_bar")
        hdr_row = QHBoxLayout(header)
        hdr_row.setContentsMargins(0, 0, 0, 0)

        self._id_label = QLabel()
        self._id_label.setObjectName("id_label")
        hdr_row.addWidget(self._id_label)

        hdr_row.addStretch()

        self._counter_label = QLabel()
        self._counter_label.setObjectName("counter_label")
        hdr_row.addWidget(self._counter_label)

        hdr_row.addSpacing(24)

        self._status_label = QLabel()
        self._status_label.setObjectName("counter_label")
        hdr_row.addWidget(self._status_label)

        hdr_row.addSpacing(24)

        exit_btn = QPushButton(self.tr("Exit Focus Mode  Esc"))
        exit_btn.setObjectName("exit_btn")
        exit_btn.clicked.connect(self.close)
        hdr_row.addWidget(exit_btn)

        layout.addWidget(header)

        # ── Source panel ──────────────────────────────────────────
        src_panel = QWidget()
        src_panel.setObjectName("source_panel")
        src_layout = QVBoxLayout(src_panel)
        src_layout.setContentsMargins(0, 0, 0, 0)
        src_layout.setSpacing(0)

        src_hdr = QLabel(self.tr("SOURCE"))
        src_hdr.setObjectName("source_hdr")
        src_layout.addWidget(src_hdr)

        self._source_edit = QTextEdit()
        self._source_edit.setObjectName("source_edit")
        self._source_edit.setReadOnly(True)
        self._source_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._source_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        src_layout.addWidget(self._source_edit)

        layout.addWidget(src_panel, stretch=2)

        # ── Translation panel ─────────────────────────────────────
        trans_panel = QWidget()
        trans_panel.setObjectName("trans_panel")
        trans_layout = QVBoxLayout(trans_panel)
        trans_layout.setContentsMargins(0, 0, 0, 0)
        trans_layout.setSpacing(0)

        trans_hdr = QLabel(self.tr("TRANSLATION"))
        trans_hdr.setObjectName("trans_hdr")
        trans_layout.addWidget(trans_hdr)

        self._trans_edit = QTextEdit()
        self._trans_edit.setObjectName("trans_edit")
        self._trans_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        trans_layout.addWidget(self._trans_edit)

        layout.addWidget(trans_panel, stretch=3)

        # ── Footer bar ────────────────────────────────────────────
        footer = QWidget()
        footer.setObjectName("footer_bar")
        foot_row = QHBoxLayout(footer)
        foot_row.setContentsMargins(0, 0, 0, 0)
        foot_row.setSpacing(8)

        self._prev_btn = QPushButton(self.tr("← Prev  Shift+F7"))
        self._prev_btn.setObjectName("nav_btn")
        self._prev_btn.clicked.connect(self._go_prev)
        foot_row.addWidget(self._prev_btn)

        self._skip_btn = QPushButton(self.tr("Next Untranslated  F7"))
        self._skip_btn.setObjectName("nav_btn")
        self._skip_btn.clicked.connect(self._go_next_untranslated)
        foot_row.addWidget(self._skip_btn)

        foot_row.addStretch()

        self._approve_btn = QPushButton(self.tr("✓  Approve & Next  Ctrl+Enter"))
        self._approve_btn.setObjectName("approve_btn")
        self._approve_btn.clicked.connect(self._approve_and_next)
        foot_row.addWidget(self._approve_btn)

        layout.addWidget(footer)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Escape"), self, activated=self.close)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._approve_and_next)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._approve_and_next)
        QShortcut(QKeySequence("F7"), self, activated=self._go_next_untranslated)
        QShortcut(QKeySequence("Shift+F7"), self, activated=self._go_prev)

    # ── Row loading ───────────────────────────────────────────────────────────

    def _load_row(self, row: int) -> None:
        data = self._model._data
        if not data or not (0 <= row < len(data)):
            return
        self._current_row = row
        entry = data[row]

        string_id = entry.get("string_id", -1)
        self._id_label.setText(
            f"ID 0x{string_id:08X}" if string_id >= 0 else ""
        )

        total = len(data)
        pending = sum(1 for d in data if d.get("status") == "pending")
        self._counter_label.setText(
            self.tr(f"String {row + 1} of {total}  ·  {pending} pending")
        )

        status = entry.get("status", "pending")
        status_text = {"translated": "✓ translated", "pending": "○ pending", "error": "✕ error"}.get(status, status)
        status_color = {"translated": _STATUS_DONE, "pending": _STATUS_PEND, "error": _STATUS_ERR}.get(status, _TEXT_DIM)
        self._status_label.setText(f"<span style='color:{status_color}'>{status_text}</span>")

        self._source_edit.setPlainText(entry.get("original", ""))

        # Block signals so we don't accidentally mark the string as modified
        # before the user has typed anything
        self._trans_edit.blockSignals(True)
        self._trans_edit.setPlainText(entry.get("translated", ""))
        self._trans_edit.blockSignals(False)

        # Place cursor at end for immediate typing
        cursor = self._trans_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._trans_edit.setTextCursor(cursor)
        self._trans_edit.setFocus()

        self._prev_btn.setEnabled(row > 0)
        self.row_navigated.emit(row)

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go_prev(self) -> None:
        if self._current_row > 0:
            self._load_row(self._current_row - 1)

    def _go_next_untranslated(self) -> None:
        """Advance to the next pending string without saving."""
        data = self._model._data
        n = len(data)
        if n == 0:
            self._skip_btn.setEnabled(False)
            return
        start = min(self._current_row + 1, n)
        for i in range(start, n):
            if data[i].get("status") == "pending":
                self._load_row(i)
                return
        # Wrap around
        for i in range(0, start):
            if data[i].get("status") == "pending":
                self._load_row(i)
                return
        # None found — stay put
        self._skip_btn.setEnabled(False)

    def _approve_and_next(self) -> None:
        """Save the current translation and move to the next untranslated."""
        text = self._trans_edit.toPlainText()
        if text.strip():
            self.translation_committed.emit(self._current_row, text)
            # Optimistically update the model's in-memory data so the counter
            # and status badge refresh correctly on the next _load_row call.
            if 0 <= self._current_row < len(self._model._data):
                self._model._data[self._current_row]["status"] = "translated"
        self._go_next_untranslated()

    # ── Show / close ──────────────────────────────────────────────────────────

    def showEvent(self, event) -> None:
        self.showFullScreen()
        super().showEvent(event)
        self._trans_edit.setFocus()

    def closeEvent(self, event) -> None:
        # Commit any unsaved text before closing
        text = self._trans_edit.toPlainText()
        if text.strip() and 0 <= self._current_row < len(self._model._data):
            stored = self._model._data[self._current_row].get("translated", "")
            if text != stored:
                self.translation_committed.emit(self._current_row, text)
        super().closeEvent(event)

    # ── Public API ────────────────────────────────────────────────────────────

    def jump_to_row(self, row: int) -> None:
        """Called from main_window to sync navigation if user clicks the table."""
        if row != self._current_row:
            self._load_row(row)
