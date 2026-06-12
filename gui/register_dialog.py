"""
Register Consistency Dialog.

Shows NPC speakers whose translated dialogue mixes informal (ти) and formal (ви)
address.  Selecting a speaker populates two evidence panels; double-clicking
any string jumps the main table to that row.
"""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.register_checker import RegisterGroup, RegisterHit

logger = logging.getLogger(__name__)

_COL_TY_BG  = QColor("#fffbeb")
_COL_VY_BG  = QColor("#eff6ff")
_COL_TY_HDR = QColor("#d97706")
_COL_VY_HDR = QColor("#2563eb")
_ROLE_ROW   = Qt.ItemDataRole.UserRole


class RegisterDialog(QDialog):
    """Dialog displaying ти/ви register inconsistency results."""

    jump_to_row = Signal(int)

    def __init__(
        self,
        groups: List[RegisterGroup],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Register Consistency – ти/ви"))
        self.resize(880, 600)
        self._groups = groups
        self._last_hit_table: Optional[QTableWidget] = None
        self._setup_ui()
        self._populate_groups()
        if groups:
            self._speaker_table.selectRow(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        n = len(self._groups)
        if n:
            summary = self.tr(
                "{n} speaker(s) with mixed ти/ви register — select a row to inspect."
            ).format(n=n)
        else:
            summary = self.tr(
                "No register inconsistency found. All translated strings use a "
                "consistent address form."
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

        # ── Speaker table (top pane) ──────────────────────────────────────────
        self._speaker_table = QTableWidget(0, 3)
        self._speaker_table.setHorizontalHeaderLabels([
            self.tr("Speaker / EDID prefix"),
            self.tr("ти-form"),
            self.tr("ви-form"),
        ])
        hh = self._speaker_table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._speaker_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._speaker_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._speaker_table.setAlternatingRowColors(True)
        self._speaker_table.verticalHeader().setVisible(False)
        self._speaker_table.currentRowChanged.connect(self._on_group_changed)
        splitter.addWidget(self._speaker_table)

        # ── Evidence panels (bottom pane) ─────────────────────────────────────
        bottom = QWidget()
        blay = QHBoxLayout(bottom)
        blay.setContentsMargins(0, 4, 0, 0)
        blay.setSpacing(8)

        ty_panel, self._ty_tbl = self._make_panel(
            self.tr("ти-form strings (informal)"), _COL_TY_HDR, _COL_TY_BG)
        vy_panel, self._vy_tbl = self._make_panel(
            self.tr("ви-form strings (formal/plural)"), _COL_VY_HDR, _COL_VY_BG)

        blay.addWidget(ty_panel)
        blay.addWidget(vy_panel)
        splitter.addWidget(bottom)
        splitter.setSizes([180, 340])
        root.addWidget(splitter, 1)

        # ── Button bar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()
        self._btn_jump = QPushButton(self.tr("Jump to String in Table"))
        self._btn_jump.setEnabled(False)
        self._btn_jump.clicked.connect(self._jump_selected)
        bar.addWidget(self._btn_jump)
        bar.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        root.addLayout(bar)

        for tbl in (self._ty_tbl, self._vy_tbl):
            tbl.cellDoubleClicked.connect(self._on_double_click)
            tbl.currentCellChanged.connect(
                lambda *_, t=tbl: self._on_hit_focus(t))

    def _make_panel(
        self,
        title: str,
        header_color: QColor,
        row_bg: QColor,
    ) -> Tuple[QWidget, QTableWidget]:
        """Return (container_widget, inner_QTableWidget)."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        lbl = QLabel(
            f"<b style='color:{header_color.name()}'>{title}</b>")
        lay.addWidget(lbl)

        tbl = QTableWidget(0, 3)
        tbl.setHorizontalHeaderLabels([
            self.tr("Row"), self.tr("ID"), self.tr("Translation preview"),
        ])
        hh = tbl.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        tbl.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        tbl.setAlternatingRowColors(True)
        tbl.verticalHeader().setVisible(False)
        tbl._row_bg = row_bg  # type: ignore[attr-defined]
        lay.addWidget(tbl)
        return panel, tbl

    # ── Data helpers ──────────────────────────────────────────────────────────

    def _populate_groups(self) -> None:
        self._speaker_table.setRowCount(len(self._groups))
        for r, grp in enumerate(self._groups):
            key = grp.speaker_key
            if key in ("_unknown_", "_file_"):
                display = self.tr("(unknown speaker)")
            else:
                display = key

            name_item = QTableWidgetItem(display)
            name_item.setData(_ROLE_ROW, r)

            ty_item = QTableWidgetItem(str(len(grp.ty_hits)))
            ty_item.setForeground(_COL_TY_HDR)
            ty_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            vy_item = QTableWidgetItem(str(len(grp.vy_hits)))
            vy_item.setForeground(_COL_VY_HDR)
            vy_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            self._speaker_table.setItem(r, 0, name_item)
            self._speaker_table.setItem(r, 1, ty_item)
            self._speaker_table.setItem(r, 2, vy_item)

    def _fill_hit_table(
        self, tbl: QTableWidget, hits: List[RegisterHit]
    ) -> None:
        bg = tbl._row_bg  # type: ignore[attr-defined]
        tbl.setRowCount(len(hits))
        for r, hit in enumerate(hits):
            row_item = QTableWidgetItem(str(hit.row_index + 1))
            row_item.setData(_ROLE_ROW, hit.row_index)
            row_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            id_item = QTableWidgetItem(f"0x{hit.string_id:08X}")
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            txt_item = QTableWidgetItem(hit.text[:200])

            for col, item in enumerate((row_item, id_item, txt_item)):
                item.setBackground(bg)
                tbl.setItem(r, col, item)

        tbl.resizeRowsToContents()

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_group_changed(self, row: int) -> None:
        if 0 <= row < len(self._groups):
            grp = self._groups[row]
            self._fill_hit_table(self._ty_tbl, grp.ty_hits)
            self._fill_hit_table(self._vy_tbl, grp.vy_hits)
        self._last_hit_table = None
        self._btn_jump.setEnabled(False)

    def _on_hit_focus(self, tbl: QTableWidget) -> None:
        self._last_hit_table = tbl
        self._btn_jump.setEnabled(tbl.currentRow() >= 0)

    def _on_double_click(self, table_row: int, _col: int) -> None:  # noqa: ARG002
        tbl = self.sender()
        if not isinstance(tbl, QTableWidget):
            return
        item = tbl.item(table_row, 0)
        if item:
            self._emit_jump(item)

    def _jump_selected(self) -> None:
        tbl = self._last_hit_table
        if tbl is None:
            return
        item = tbl.item(tbl.currentRow(), 0)
        if item:
            self._emit_jump(item)

    def _emit_jump(self, row_col0_item: QTableWidgetItem) -> None:
        row_index = row_col0_item.data(_ROLE_ROW)
        if isinstance(row_index, int) and row_index >= 0:
            self.jump_to_row.emit(row_index)
