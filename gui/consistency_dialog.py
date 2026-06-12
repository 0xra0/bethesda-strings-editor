"""
Dialog for reviewing and resolving translation inconsistencies.
"""

from __future__ import annotations

import logging
from typing import List

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.consistency_checker import InconsistencyGroup
from gui.micro_animations import FadeInMixin

logger = logging.getLogger(__name__)


class ConsistencyDialog(FadeInMixin, QDialog):
    """Shows inconsistently-translated strings and lets user pick the canonical form.

    Emits replacements_requested with a list of (row_indices, canonical_text) tuples
    that the caller should apply to the table model.
    """

    replacements_requested = Signal(list)

    def __init__(self, groups: List[InconsistencyGroup], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Consistency Check"))
        self.setMinimumSize(920, 580)
        self._groups = groups
        self._canonical: dict[int, str] = {}   # group index → chosen canonical text
        self._resolved: set[int] = set()        # group indices already replaced
        self._setup_ui()
        if groups:
            self._list.setCurrentRow(0)

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        count = len(self._groups)
        info = QLabel(self.tr(
            "{n} inconsistency group(s) found — same source text translated differently. "
            "Select a group, click the preferred translation to mark it as canonical, "
            "then click Replace."
        ).format(n=count))
        info.setWordWrap(True)
        layout.addWidget(info)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, 1)

        # ── Left panel: list of groups ────────────────────────────────────────
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(self.tr("Source strings:")))

        self._list = QListWidget()
        self._list.setWordWrap(True)
        self._list.setAlternatingRowColors(True)
        for g in self._groups:
            src = g.source[:80] + ("…" if len(g.source) > 80 else "")
            item = QListWidgetItem(
                f"{src}\n{g.variant_count} variants · {g.total_rows} rows"
            )
            item.setToolTip(g.source)
            self._list.addItem(item)

        self._list.currentRowChanged.connect(self._on_group_selected)
        left_layout.addWidget(self._list)
        splitter.addWidget(left)

        # ── Right panel: variant table ────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self._variant_label = QLabel(self.tr("Variants (click a row to select as canonical):"))
        right_layout.addWidget(self._variant_label)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels([self.tr("Translation"), self.tr("Rows")])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._on_variant_selected)
        right_layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        self._replace_btn = QPushButton(self.tr("Replace non-canonical in this group"))
        self._replace_btn.setEnabled(False)
        self._replace_btn.clicked.connect(self._replace_current_group)
        btn_row.addWidget(self._replace_btn)
        btn_row.addStretch()
        right_layout.addLayout(btn_row)

        splitter.addWidget(right)
        splitter.setSizes([320, 600])

        # ── Bottom buttons ────────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self._apply_all_btn = btn_box.addButton(
            self.tr("Replace All Groups"), QDialogButtonBox.ButtonRole.ActionRole
        )
        self._apply_all_btn.setToolTip(self.tr(
            "Apply chosen canonical translations for all groups that have a variant selected."
        ))
        self._apply_all_btn.clicked.connect(self._replace_all_groups)
        btn_box.addButton(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    # ── Group selection ───────────────────────────────────────────────────────

    def _on_group_selected(self, row: int):
        self._table.clearContents()
        self._table.setRowCount(0)
        self._replace_btn.setEnabled(False)

        if row < 0 or row >= len(self._groups):
            return

        group = self._groups[row]
        canonical = self._canonical.get(row)

        for trans, indices in sorted(group.variants.items(), key=lambda x: -len(x[1])):
            r = self._table.rowCount()
            self._table.insertRow(r)

            item_text = QTableWidgetItem(trans)
            item_text.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item_text.setData(Qt.ItemDataRole.UserRole, indices)

            item_count = QTableWidgetItem(str(len(indices)))
            item_count.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item_count.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            self._table.setItem(r, 0, item_text)
            self._table.setItem(r, 1, item_count)

            if trans == canonical:
                self._bold_row(r, True)

        # Re-select the previously chosen canonical row
        if canonical is not None:
            for r in range(self._table.rowCount()):
                item = self._table.item(r, 0)
                if item and item.text() == canonical:
                    self._table.selectRow(r)
                    self._replace_btn.setEnabled(row not in self._resolved)
                    break

    # ── Variant selection ─────────────────────────────────────────────────────

    def _on_variant_selected(self):
        group_row = self._list.currentRow()
        if group_row < 0:
            return

        selected = self._table.selectedItems()
        if not selected:
            self._replace_btn.setEnabled(False)
            return

        table_row = self._table.row(selected[0])
        item = self._table.item(table_row, 0)
        if item is None:
            return

        canonical = item.text()
        self._canonical[group_row] = canonical

        for r in range(self._table.rowCount()):
            self._bold_row(r, r == table_row)

        self._replace_btn.setEnabled(group_row not in self._resolved)

    # ── Replace actions ───────────────────────────────────────────────────────

    def _replace_current_group(self):
        group_row = self._list.currentRow()
        if group_row < 0 or group_row in self._resolved:
            return

        canonical = self._canonical.get(group_row)
        if not canonical:
            return

        group = self._groups[group_row]
        replacements = [
            (indices, canonical)
            for trans, indices in group.variants.items()
            if trans != canonical
        ]
        if not replacements:
            return

        affected = sum(len(r[0]) for r in replacements)
        canonical_display = canonical[:120] + ("…" if len(canonical) > 120 else "")
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Replace"),
            self.tr(
                "Replace {n} row(s) with:\n\n\"{text}\"\n\nProceed?"
            ).format(n=affected, text=canonical_display),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.replacements_requested.emit(replacements)
        self._resolved.add(group_row)
        self._replace_btn.setEnabled(False)
        self._mark_group_resolved(group_row)
        logger.info(
            "Consistency: replaced %d rows in group '%s...' with canonical form",
            affected, group.source[:40],
        )

    def _replace_all_groups(self):
        pending = {
            gi: canonical
            for gi, canonical in self._canonical.items()
            if gi not in self._resolved and gi < len(self._groups)
        }
        if not pending:
            QMessageBox.information(
                self,
                self.tr("Nothing to Replace"),
                self.tr(
                    "No canonical forms have been selected. "
                    "Click a variant row to mark it as canonical first."
                ),
            )
            return

        all_replacements = []
        for gi, canonical in pending.items():
            group = self._groups[gi]
            for trans, indices in group.variants.items():
                if trans != canonical:
                    all_replacements.append((indices, canonical))

        if not all_replacements:
            return

        affected = sum(len(r[0]) for r in all_replacements)
        reply = QMessageBox.question(
            self,
            self.tr("Confirm Replace All"),
            self.tr(
                "Replace {n} row(s) across {g} group(s)?"
            ).format(n=affected, g=len(pending)),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.replacements_requested.emit(all_replacements)
        for gi in pending:
            self._resolved.add(gi)
            self._mark_group_resolved(gi)
        logger.info(
            "Consistency: replaced %d rows across %d groups", affected, len(pending)
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _bold_row(self, table_row: int, bold: bool):
        for col in range(self._table.columnCount()):
            item = self._table.item(table_row, col)
            if item:
                font = item.font()
                font.setBold(bold)
                item.setFont(font)

    def _mark_group_resolved(self, group_row: int):
        item = self._list.item(group_row)
        if item is None:
            return
        group = self._groups[group_row]
        src = group.source[:80] + ("…" if len(group.source) > 80 else "")
        item.setText(f"✓ {src}\nResolved")
