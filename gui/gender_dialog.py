"""
Gendered Noun Agreement Dialog.

Shows adjective/noun gender mismatches found by gender_checker.py.
Selecting a row jumps the main string table to the offending string.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from gui.gender_checker import GENDER_LABEL, GenderMismatch

logger = logging.getLogger(__name__)

_ROLE_ROW = Qt.ItemDataRole.UserRole
_COL_BG   = {
    "M": QColor("#eff6ff"),   # blue tint  – masculine mismatch
    "F": QColor("#fdf4ff"),   # violet     – feminine mismatch
    "N": QColor("#f0fdf4"),   # green tint – neuter mismatch
}
_COL_HDR = {
    "M": QColor("#2563eb"),
    "F": QColor("#7c3aed"),
    "N": QColor("#16a34a"),
}


class GenderDialog(QDialog):
    """Dialog displaying gendered noun agreement mismatches."""

    jump_to_row = Signal(int)

    def __init__(
        self,
        mismatches: List[GenderMismatch],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Gender Agreement Check – Ukrainian"))
        self.resize(960, 560)
        self._mismatches = mismatches
        self._setup_ui()
        self._populate()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        n = len(self._mismatches)
        if n:
            summary = self.tr(
                "{n} adjective/noun gender mismatch(es) found — "
                "double-click a row to jump to the string."
            ).format(n=n)
        else:
            summary = self.tr(
                "No gender agreement issues found in the current translation."
            )
        lbl = QLabel(summary)
        lbl.setWordWrap(True)
        root.addWidget(lbl)

        if not n:
            btn = QPushButton(self.tr("Close"))
            btn.clicked.connect(self.accept)
            root.addWidget(btn, 0, Qt.AlignmentFlag.AlignRight)
            return

        splitter = QSplitter(Qt.Orientation.Vertical)

        # ── Mismatch table ────────────────────────────────────────────────────
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels([
            self.tr("Row"),
            self.tr("String ID"),
            self.tr("Adjective"),
            self.tr("Adj gender"),
            self.tr("Noun"),
            self.tr("Noun gender"),
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.currentRowChanged.connect(self._on_row_changed)
        self._table.cellDoubleClicked.connect(self._jump_current)
        splitter.addWidget(self._table)

        # ── Context preview ───────────────────────────────────────────────────
        preview_w = QWidget()
        pv_lay = QVBoxLayout(preview_w)
        pv_lay.setContentsMargins(0, 4, 0, 0)
        pv_lay.addWidget(QLabel(
            f"<b>{self.tr('Translation context')}</b>"))
        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(120)
        pv_lay.addWidget(self._preview)
        splitter.addWidget(preview_w)

        splitter.setSizes([380, 120])
        root.addWidget(splitter, 1)

        # ── Button bar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        self._btn_jump = QPushButton(self.tr("Jump to String in Table"))
        self._btn_jump.setEnabled(False)
        self._btn_jump.clicked.connect(self._jump_current)
        bar.addWidget(self._btn_jump)
        bar.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        root.addLayout(bar)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        self._table.setRowCount(len(self._mismatches))
        for r, mm in enumerate(self._mismatches):
            bg = _COL_BG.get(mm.noun_gender, QColor("#f9fafb"))
            adj_col = _COL_HDR.get(mm.adj_gender, QColor("#374151"))
            noun_col = _COL_HDR.get(mm.noun_gender, QColor("#374151"))

            row_item = QTableWidgetItem(str(mm.row_index + 1))
            row_item.setData(_ROLE_ROW, mm.row_index)
            row_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            id_item = QTableWidgetItem(f"0x{mm.string_id:08X}")
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            adj_item  = QTableWidgetItem(mm.adj_token)
            adj_item.setForeground(adj_col)
            bold = QFont()
            bold.setBold(True)
            adj_item.setFont(bold)

            adjg_item = QTableWidgetItem(GENDER_LABEL.get(mm.adj_gender, mm.adj_gender))
            adjg_item.setForeground(adj_col)
            adjg_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            noun_item = QTableWidgetItem(mm.noun_token)
            noun_item.setForeground(noun_col)
            noun_item.setFont(bold)

            noung_item = QTableWidgetItem(GENDER_LABEL.get(mm.noun_gender, mm.noun_gender))
            noung_item.setForeground(noun_col)
            noung_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            for col, item in enumerate((
                row_item, id_item, adj_item, adjg_item, noun_item, noung_item
            )):
                item.setBackground(bg)
                self._table.setItem(r, col, item)

        self._table.resizeColumnsToContents()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_row_changed(self, row: int) -> None:
        self._btn_jump.setEnabled(row >= 0)
        if 0 <= row < len(self._mismatches):
            mm = self._mismatches[row]
            # Render context with the mismatched words highlighted in HTML
            text = mm.text
            adj_l = mm.adj_token.lower()
            noun_l = mm.noun_token.lower()
            html = ""
            i = 0
            while i < len(text):
                lo = text[i:].lower()
                matched = False
                for token, color in (
                    (adj_l, "#dc2626"),
                    (noun_l, "#2563eb"),
                ):
                    if lo.startswith(token):
                        html += (
                            f"<span style='background:{color}20;"
                            f"color:{color};font-weight:bold'>"
                            f"{text[i:i + len(token)]}</span>"
                        )
                        i += len(token)
                        matched = True
                        break
                if not matched:
                    c = text[i]
                    html += c.replace("&", "&amp;").replace("<", "&lt;")
                    i += 1
            self._preview.setHtml(f"<p style='font-size:11pt'>{html}</p>")

    def _jump_current(self, *_args) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item:
            row_index = item.data(_ROLE_ROW)
            if isinstance(row_index, int) and row_index >= 0:
                self.jump_to_row.emit(row_index)
