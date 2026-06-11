"""
Translation Editor Pane — detachable dock widget.

Provides a large, comfortable editing area for the currently selected
string.  The user can dock it alongside the main table, or drag it to
a second monitor for a true side-by-side workflow.

Signals
-------
translation_approved(source_row: int, text: str)
    Emitted when the user approves the translation.  The main window
    connects this to its model-update path.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDockWidget, QHBoxLayout, QLabel, QPushButton,
    QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)


class TranslationEditorPane(QDockWidget):
    """Dockable / floating editor for source + translation text."""

    translation_approved = Signal(int, str)   # (source_row, translated_text)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("TranslationEditorPane")
        self.setWindowTitle(self.tr("Translation Editor"))
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        self._current_row: int = -1
        self._original_translation: str = ""

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # ── Meta row ──────────────────────────────────────────────
        meta_row = QHBoxLayout()
        self._id_label = QLabel()
        self._id_label.setStyleSheet("font-family: monospace; font-size: 11px;")
        meta_row.addWidget(self._id_label)
        meta_row.addStretch()
        self._status_label = QLabel()
        self._status_label.setStyleSheet("font-size: 11px;")
        meta_row.addWidget(self._status_label)
        layout.addLayout(meta_row)

        # ── Source panel ──────────────────────────────────────────
        src_hdr = QLabel(self.tr("Source"))
        src_hdr.setStyleSheet("font-size: 10px; font-weight: 600; color: #888; letter-spacing: 1px;")
        layout.addWidget(src_hdr)

        self._source_edit = QTextEdit()
        self._source_edit.setReadOnly(True)
        self._source_edit.setPlaceholderText(self.tr("(select a string in the table)"))
        self._source_edit.setMaximumHeight(140)
        self._source_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self._source_edit)

        # ── Translation panel ─────────────────────────────────────
        trans_hdr = QLabel(self.tr("Translation"))
        trans_hdr.setStyleSheet("font-size: 10px; font-weight: 600; color: #888; letter-spacing: 1px;")
        layout.addWidget(trans_hdr)

        self._trans_edit = QTextEdit()
        self._trans_edit.setPlaceholderText(self.tr("Enter translation here…"))
        self._trans_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._trans_edit, stretch=1)

        # ── Button row ────────────────────────────────────────────
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._revert_btn = QPushButton(self.tr("Revert"))
        self._revert_btn.setToolTip(self.tr("Discard edits and restore the stored translation"))
        self._revert_btn.setEnabled(False)
        self._revert_btn.clicked.connect(self._revert)
        btn_row.addWidget(self._revert_btn)

        btn_row.addStretch()

        self._approve_btn = QPushButton(self.tr("✓  Approve  Ctrl+Enter"))
        self._approve_btn.setStyleSheet(
            "QPushButton { background: #238636; color: white; border: none;"
            " border-radius: 4px; padding: 5px 18px; font-weight: bold; }"
            "QPushButton:hover { background: #2ea043; }"
            "QPushButton:disabled { background: #21262d; color: #444; }"
        )
        self._approve_btn.setEnabled(False)
        self._approve_btn.clicked.connect(self._approve)
        btn_row.addWidget(self._approve_btn)

        layout.addLayout(btn_row)
        self.setWidget(root)

        # Shortcut scoped to this dock (works even when the dock is floating)
        QShortcut(QKeySequence("Ctrl+Return"), root, activated=self._approve)
        QShortcut(QKeySequence("Ctrl+Enter"), root, activated=self._approve)

        # Track changes to enable/disable Revert
        self._trans_edit.textChanged.connect(self._on_text_changed)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_string(self, row_data: Optional[dict], source_row: int) -> None:
        """Update the pane with a newly selected string."""
        self._current_row = source_row

        if row_data is None:
            self._id_label.setText("")
            self._status_label.setText("")
            self._source_edit.clear()
            self._trans_edit.clear()
            self._approve_btn.setEnabled(False)
            self._revert_btn.setEnabled(False)
            return

        string_id = row_data.get("string_id", -1)
        self._id_label.setText(f"ID 0x{string_id:08X}" if string_id >= 0 else "")

        status = row_data.get("status", "pending")
        color = {"translated": "#238636", "pending": "#e3b341", "error": "#da3633"}.get(status, "#888")
        self._status_label.setText(f"<span style='color:{color}'>{status}</span>")

        self._source_edit.blockSignals(True)
        self._source_edit.setPlainText(row_data.get("original", ""))
        self._source_edit.blockSignals(False)

        translation = row_data.get("translated", "")
        self._original_translation = translation

        self._trans_edit.blockSignals(True)
        self._trans_edit.setPlainText(translation)
        self._trans_edit.blockSignals(False)

        self._approve_btn.setEnabled(True)
        self._revert_btn.setEnabled(False)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _approve(self) -> None:
        if self._current_row < 0:
            return
        text = self._trans_edit.toPlainText()
        if text.strip():
            self.translation_approved.emit(self._current_row, text)
            self._original_translation = text
            self._revert_btn.setEnabled(False)

    def _revert(self) -> None:
        self._trans_edit.blockSignals(True)
        self._trans_edit.setPlainText(self._original_translation)
        self._trans_edit.blockSignals(False)
        self._revert_btn.setEnabled(False)

    def _on_text_changed(self) -> None:
        current = self._trans_edit.toPlainText()
        self._revert_btn.setEnabled(
            self._current_row >= 0 and current != self._original_translation
        )
