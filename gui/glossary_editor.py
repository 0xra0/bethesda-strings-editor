"""
Glossary editor dialog — create, search, filter, and bulk-manage
GlossaryEntry records with import / export to CSV, TBX, and JSON.
"""
from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.glossary import Glossary, GlossaryEntry, GlossaryManager

logger = logging.getLogger(__name__)

# Table columns
_COL_SOURCE = 0
_COL_TARGET = 1
_COL_CATEGORY = 2
_COL_DEFINITION = 3
_COLS = 4


class GlossaryEditorDialog(QDialog):
    """
    Full-featured glossary editor.

    Emits ``glossary_changed`` when entries are saved so the caller can
    refresh any dependent UI (suggest dock, quality checks, etc.).
    """

    glossary_changed = Signal()

    def __init__(
        self,
        manager: GlossaryManager,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager

        # Work on deep copies so Cancel truly discards all edits.
        self._global_copy = self._clone_glossary(manager.global_glossary)
        self._project_copy = (
            self._clone_glossary(manager.project_glossary)
            if manager.project_glossary
            else None
        )

        self.setWindowTitle(self.tr("Glossary Editor"))
        self.resize(1000, 680)
        self.setMinimumSize(760, 500)

        self._build_ui()
        self._refresh_table()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Scope + filter bar ─────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        top_bar.setSpacing(6)

        top_bar.addWidget(QLabel(self.tr("Glossary:")))
        self._scope_combo = QComboBox()
        self._scope_combo.addItem(self.tr("Global"), "global")
        if self._project_copy is not None:
            label = self.tr("Project — {name}").format(name=self._project_copy.label)
            self._scope_combo.addItem(label, "project")
        self._scope_combo.currentIndexChanged.connect(self._on_scope_changed)
        top_bar.addWidget(self._scope_combo)

        top_bar.addSpacing(12)
        top_bar.addWidget(QLabel(self.tr("Search:")))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(self.tr("Filter by source, target, or category…"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._refresh_table)
        top_bar.addWidget(self._search_edit, stretch=1)

        top_bar.addWidget(QLabel(self.tr("Category:")))
        self._cat_combo = QComboBox()
        self._cat_combo.setMinimumWidth(120)
        self._cat_combo.currentIndexChanged.connect(self._refresh_table)
        top_bar.addWidget(self._cat_combo)

        root.addLayout(top_bar)

        # ── Table + detail splitter ────────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)

        # Table
        self._table = QTableWidget(0, _COLS)
        self._table.setHorizontalHeaderLabels(
            [self.tr("Source Term"), self.tr("Target Term"),
             self.tr("Category"), self.tr("Definition")]
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_SOURCE, QHeaderView.Interactive
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_TARGET, QHeaderView.Interactive
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_CATEGORY, QHeaderView.Interactive
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_DEFINITION, QHeaderView.Stretch
        )
        self._table.setColumnWidth(_COL_SOURCE, 200)
        self._table.setColumnWidth(_COL_TARGET, 200)
        self._table.setColumnWidth(_COL_CATEGORY, 130)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
        )
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        self._table.itemChanged.connect(self._on_item_changed)
        splitter.addWidget(self._table)

        # Action buttons row (above detail panel, below table)
        btn_bar = QWidget()
        btn_layout = QHBoxLayout(btn_bar)
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(6)

        self._btn_new = QPushButton(self.tr("+ New Entry"))
        self._btn_new.clicked.connect(self._add_entry)
        btn_layout.addWidget(self._btn_new)

        self._btn_delete = QPushButton(self.tr("Delete Selected"))
        self._btn_delete.clicked.connect(self._delete_selected)
        self._btn_delete.setEnabled(False)
        btn_layout.addWidget(self._btn_delete)

        btn_layout.addStretch()

        # Bulk: change category for selected
        btn_layout.addWidget(QLabel(self.tr("Set category for selected:")))
        self._bulk_cat_edit = QLineEdit()
        self._bulk_cat_edit.setPlaceholderText(self.tr("Category name…"))
        self._bulk_cat_edit.setMaximumWidth(150)
        btn_layout.addWidget(self._bulk_cat_edit)
        btn_apply_cat = QPushButton(self.tr("Apply"))
        btn_apply_cat.clicked.connect(self._bulk_set_category)
        btn_layout.addWidget(btn_apply_cat)

        # Detail panel
        detail_group = QGroupBox(self.tr("Entry Details"))
        detail_layout = QVBoxLayout(detail_group)
        detail_layout.setSpacing(4)

        def _row(label: str, widget: QWidget) -> QHBoxLayout:
            hl = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(90)
            hl.addWidget(lbl)
            hl.addWidget(widget)
            return hl

        self._def_edit = QTextEdit()
        self._def_edit.setPlaceholderText(self.tr("Definition (optional)"))
        self._def_edit.setMaximumHeight(60)
        detail_layout.addLayout(_row(self.tr("Definition:"), self._def_edit))

        self._ex_edit = QLineEdit()
        self._ex_edit.setPlaceholderText(
            self.tr("Examples — separate with  |  (optional)")
        )
        detail_layout.addLayout(_row(self.tr("Examples:"), self._ex_edit))

        self._notes_edit = QLineEdit()
        self._notes_edit.setPlaceholderText(self.tr("Notes (optional)"))
        detail_layout.addLayout(_row(self.tr("Notes:"), self._notes_edit))

        self._btn_apply_detail = QPushButton(self.tr("Apply Details"))
        self._btn_apply_detail.clicked.connect(self._apply_detail)
        self._btn_apply_detail.setEnabled(False)
        detail_layout.addWidget(self._btn_apply_detail, alignment=Qt.AlignRight)

        # Wrap btn_bar + detail_group in a widget for the splitter
        bottom_widget = QWidget()
        bw_layout = QVBoxLayout(bottom_widget)
        bw_layout.setContentsMargins(0, 0, 0, 0)
        bw_layout.addWidget(btn_bar)
        bw_layout.addWidget(detail_group)

        splitter.addWidget(bottom_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, stretch=1)

        # ── Import / Export ────────────────────────────────────────────────────
        io_bar = QHBoxLayout()
        io_bar.setSpacing(4)

        for label, slot in [
            (self.tr("Import CSV"), self._import_csv),
            (self.tr("Import TBX"), self._import_tbx),
            (self.tr("Import JSON"), self._import_json),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            io_bar.addWidget(btn)

        io_bar.addStretch()

        for label, slot in [
            (self.tr("Export CSV"), self._export_csv),
            (self.tr("Export TBX"), self._export_tbx),
            (self.tr("Export JSON"), self._export_json),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            io_bar.addWidget(btn)

        root.addLayout(io_bar)

        # ── OK / Cancel ────────────────────────────────────────────────────────
        bottom_btns = QHBoxLayout()
        bottom_btns.addStretch()
        btn_cancel = QPushButton(self.tr("Cancel"))
        btn_cancel.clicked.connect(self.reject)
        bottom_btns.addWidget(btn_cancel)
        btn_save = QPushButton(self.tr("Save"))
        btn_save.setDefault(True)
        btn_save.clicked.connect(self._save_and_accept)
        bottom_btns.addWidget(btn_save)
        root.addLayout(bottom_btns)

        self._ignore_item_changes = False

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _clone_glossary(gloss: Glossary) -> Glossary:
        clone = Glossary(gloss.path, label=gloss.label)
        for e in gloss.entries:
            clone.add_entry(copy.deepcopy(e), _rebuild=False)
        clone._rebuild_search_index()
        return clone

    def _current_glossary(self) -> Glossary:
        if self._scope_combo.currentData() == "project" and self._project_copy:
            return self._project_copy
        return self._global_copy

    def _entry_id_at(self, row: int) -> Optional[str]:
        item = self._table.item(row, _COL_SOURCE)
        if item is None:
            return None
        return item.data(Qt.UserRole)

    # ── table refresh ──────────────────────────────────────────────────────────

    def _refresh_table(self) -> None:
        gloss = self._current_glossary()
        query = self._search_edit.text().strip()
        cat_filter = self._cat_combo.currentData() or ""

        entries = gloss.search(query) if query else gloss.entries
        if cat_filter:
            entries = [e for e in entries if e.category == cat_filter]

        # Rebuild category filter combo (preserve current selection)
        prev_cat = self._cat_combo.currentData()
        self._cat_combo.blockSignals(True)
        self._cat_combo.clear()
        self._cat_combo.addItem(self.tr("All categories"), "")
        for cat in gloss.categories():
            self._cat_combo.addItem(cat, cat)
        idx = self._cat_combo.findData(prev_cat)
        self._cat_combo.setCurrentIndex(max(0, idx))
        self._cat_combo.blockSignals(False)

        # Populate table
        self._ignore_item_changes = True
        self._table.setRowCount(len(entries))
        for row_idx, e in enumerate(entries):
            self._set_table_row(row_idx, e)
        self._ignore_item_changes = False

        self._table.resizeRowsToContents()

    def _set_table_row(self, row: int, entry: GlossaryEntry) -> None:
        def _item(text: str) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            return it

        src_item = _item(entry.source_term)
        src_item.setData(Qt.UserRole, entry.id)  # store ID in source column
        self._table.setItem(row, _COL_SOURCE, src_item)
        self._table.setItem(row, _COL_TARGET, _item(entry.target_term))
        self._table.setItem(row, _COL_CATEGORY, _item(entry.category))
        def_item = _item(entry.definition)
        def_item.setFlags(def_item.flags() & ~Qt.ItemIsEditable)  # read-only in table
        self._table.setItem(row, _COL_DEFINITION, def_item)

    # ── event handlers ─────────────────────────────────────────────────────────

    def _on_scope_changed(self) -> None:
        self._search_edit.clear()
        self._refresh_table()
        self._clear_detail_form()

    def _on_row_selected(self) -> None:
        rows = self._table.selectedItems()
        has_sel = bool(rows)
        self._btn_delete.setEnabled(has_sel)
        self._btn_apply_detail.setEnabled(has_sel)

        selected_rows = list({item.row() for item in rows})
        if len(selected_rows) == 1:
            entry_id = self._entry_id_at(selected_rows[0])
            if entry_id:
                entry = self._current_glossary().get_entry(entry_id)
                if entry:
                    self._populate_detail_form(entry)
                    return
        self._clear_detail_form()

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._ignore_item_changes:
            return
        row = item.row()
        entry_id = self._entry_id_at(row)
        if not entry_id:
            return
        gloss = self._current_glossary()
        entry = gloss.get_entry(entry_id)
        if entry is None:
            return

        col = item.column()
        if col == _COL_SOURCE:
            entry.source_term = item.text().strip()
        elif col == _COL_TARGET:
            entry.target_term = item.text().strip()
        elif col == _COL_CATEGORY:
            entry.category = item.text().strip()
        gloss.update_entry(entry)

    # ── detail form ────────────────────────────────────────────────────────────

    def _populate_detail_form(self, entry: GlossaryEntry) -> None:
        self._def_edit.setPlainText(entry.definition)
        self._ex_edit.setText(" | ".join(entry.examples))
        self._notes_edit.setText(entry.notes)

    def _clear_detail_form(self) -> None:
        self._def_edit.clear()
        self._ex_edit.clear()
        self._notes_edit.clear()

    def _apply_detail(self) -> None:
        rows = list({item.row() for item in self._table.selectedItems()})
        if len(rows) != 1:
            return
        entry_id = self._entry_id_at(rows[0])
        if not entry_id:
            return
        gloss = self._current_glossary()
        entry = gloss.get_entry(entry_id)
        if entry is None:
            return

        entry.definition = self._def_edit.toPlainText().strip()
        raw_ex = self._ex_edit.text()
        entry.examples = [x.strip() for x in raw_ex.split("|") if x.strip()]
        entry.notes = self._notes_edit.text().strip()
        gloss.update_entry(entry)

        # Refresh definition column
        self._ignore_item_changes = True
        def_item = self._table.item(rows[0], _COL_DEFINITION)
        if def_item:
            def_item.setText(entry.definition)
        self._ignore_item_changes = False

    # ── CRUD ───────────────────────────────────────────────────────────────────

    def _add_entry(self) -> None:
        entry = GlossaryEntry(source_term="", target_term="", category="")
        gloss = self._current_glossary()
        gloss.add_entry(entry)

        self._ignore_item_changes = True
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._set_table_row(row, entry)
        self._ignore_item_changes = False

        self._table.scrollToBottom()
        self._table.selectRow(row)
        _item = self._table.item(row, _COL_SOURCE)
        if _item:
            self._table.editItem(_item)

    def _delete_selected(self) -> None:
        selected_rows = sorted(
            {item.row() for item in self._table.selectedItems()}, reverse=True
        )
        if not selected_rows:
            return

        gloss = self._current_glossary()
        ids = [eid for r in selected_rows if (eid := self._entry_id_at(r))]
        gloss.remove_entries(ids)

        self._ignore_item_changes = True
        for r in selected_rows:
            self._table.removeRow(r)
        self._ignore_item_changes = False
        self._clear_detail_form()

    def _bulk_set_category(self) -> None:
        new_cat = self._bulk_cat_edit.text().strip()
        selected_rows = {item.row() for item in self._table.selectedItems()}
        if not selected_rows or not new_cat:
            return

        gloss = self._current_glossary()
        self._ignore_item_changes = True
        for r in selected_rows:
            entry_id = self._entry_id_at(r)
            if entry_id:
                entry = gloss.get_entry(entry_id)
                if entry:
                    entry.category = new_cat
                    gloss.update_entry(entry)
                    cat_item = self._table.item(r, _COL_CATEGORY)
                    if cat_item:
                        cat_item.setText(new_cat)
        self._ignore_item_changes = False

    # ── import / export ────────────────────────────────────────────────────────

    def _import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Import CSV Glossary"), "", "CSV files (*.csv)"
        )
        if not path:
            return
        gloss = self._current_glossary()
        try:
            count = gloss.import_csv(Path(path))
            self._refresh_table()
            QMessageBox.information(
                self,
                self.tr("Import Complete"),
                self.tr("Imported {n} entries from CSV.").format(n=count),
            )
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Import Failed"), str(exc))

    def _import_tbx(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Import TBX Glossary"), "", "TBX files (*.tbx *.xml)"
        )
        if not path:
            return
        gloss = self._current_glossary()
        try:
            count = gloss.import_tbx(Path(path))
            self._refresh_table()
            QMessageBox.information(
                self,
                self.tr("Import Complete"),
                self.tr("Imported {n} entries from TBX.").format(n=count),
            )
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Import Failed"), str(exc))

    def _import_json(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, self.tr("Import JSON Glossary"), "", "JSON files (*.json)"
        )
        if not path:
            return
        gloss = self._current_glossary()
        try:
            prev = len(gloss)
            gloss.load_json(Path(path))
            count = len(gloss) - prev
            self._refresh_table()
            QMessageBox.information(
                self,
                self.tr("Import Complete"),
                self.tr("Imported {n} entries from JSON.").format(n=count),
            )
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Import Failed"), str(exc))

    def _export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export Glossary as CSV"), "", "CSV files (*.csv)"
        )
        if not path:
            return
        try:
            self._current_glossary().export_csv(Path(path))
            QMessageBox.information(
                self, self.tr("Export Complete"), self.tr("Glossary exported to CSV.")
            )
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Export Failed"), str(exc))

    def _export_tbx(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export Glossary as TBX"), "", "TBX files (*.tbx)"
        )
        if not path:
            return
        try:
            self._current_glossary().export_tbx(Path(path))
            QMessageBox.information(
                self, self.tr("Export Complete"), self.tr("Glossary exported to TBX.")
            )
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Export Failed"), str(exc))

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export Glossary as JSON"), "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            self._current_glossary().save_json(Path(path))
            QMessageBox.information(
                self, self.tr("Export Complete"), self.tr("Glossary exported to JSON.")
            )
        except Exception as exc:
            QMessageBox.critical(self, self.tr("Export Failed"), str(exc))

    # ── save ───────────────────────────────────────────────────────────────────

    def _save_and_accept(self) -> None:
        # Copy edits back to the real glossaries and persist to disk
        _copy_entries(self._global_copy, self._manager.global_glossary)
        self._manager.global_glossary.save_json()

        if self._project_copy and self._manager.project_glossary:
            _copy_entries(self._project_copy, self._manager.project_glossary)
            self._manager.project_glossary.save_json()

        self.glossary_changed.emit()
        self.accept()


def _copy_entries(src: Glossary, dst: Glossary) -> None:
    """Replace all entries in *dst* with deep copies from *src*."""
    dst.clear()
    for e in src.entries:
        dst.add_entry(copy.deepcopy(e))
