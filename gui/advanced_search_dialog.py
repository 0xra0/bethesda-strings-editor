"""
Advanced Search / Find & Replace Dialog for Bethesda Strings AI Translator.
Supports searching by ID, original text, translated text, status, and regex.
Replace operates on the Translated column only.
"""

import concurrent.futures
import logging
import re
from typing import List

from PySide6.QtCore import QItemSelectionModel, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QMessageBox,
)

logger = logging.getLogger(__name__)


class AdvancedSearchDialog(QDialog):
    """Dialog for advanced string searching with multiple criteria."""

    # Signal emitted when search is performed with results
    search_results = Signal(list)  # list of row indices that match

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Advanced Search / Replace"))
        self.setMinimumSize(580, 520)
        self._last_results: list = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── Search criteria ────────────────────────────────────────────────────
        criteria_group = QGroupBox(self.tr("Search Criteria"))
        criteria_layout = QFormLayout()

        self.combo_column = QComboBox()
        self.combo_column.addItem(self.tr("All columns"), "all")
        self.combo_column.addItem(self.tr("Original text"), "original")
        self.combo_column.addItem(self.tr("Translated text"), "translated")
        self.combo_column.addItem(self.tr("Both texts"), "both")
        criteria_layout.addRow(self.tr("Search in:"), self.combo_column)

        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText(self.tr("Enter search text…"))
        self.txt_search.returnPressed.connect(self._do_search)
        criteria_layout.addRow(self.tr("Find:"), self.txt_search)

        self.txt_id = QLineEdit()
        self.txt_id.setPlaceholderText(self.tr("e.g., 0x00001234 or 4660"))
        criteria_layout.addRow(self.tr("String ID:"), self.txt_id)

        self.combo_status = QComboBox()
        self.combo_status.addItem(self.tr("Any status"), "any")
        self.combo_status.addItem(self.tr("Translated"), "translated")
        self.combo_status.addItem(self.tr("Not translated"), "not_translated")
        criteria_layout.addRow(self.tr("Status:"), self.combo_status)

        self.chk_regex = QCheckBox(self.tr("Use regular expressions"))
        self.chk_case = QCheckBox(self.tr("Case sensitive"))
        self.chk_whole_word = QCheckBox(self.tr("Whole word only"))

        options_layout = QVBoxLayout()
        options_layout.addWidget(self.chk_regex)
        options_layout.addWidget(self.chk_case)
        options_layout.addWidget(self.chk_whole_word)
        criteria_layout.addRow(self.tr("Options:"), options_layout)

        criteria_group.setLayout(criteria_layout)
        layout.addWidget(criteria_group)

        # ── Replace ────────────────────────────────────────────────────────────
        replace_group = QGroupBox(self.tr("Replace (Translated text only)"))
        replace_layout = QFormLayout()

        self.txt_replace = QLineEdit()
        self.txt_replace.setPlaceholderText(
            self.tr("Replacement text (leave blank to delete matches)")
        )
        replace_layout.addRow(self.tr("Replace with:"), self.txt_replace)

        replace_btn_row = QHBoxLayout()
        self.btn_replace_all = QPushButton(self.tr("Replace All"))
        self.btn_replace_all.setToolTip(
            self.tr("Replace all occurrences in the Translated column\n"
                    "for rows matching the current search criteria")
        )
        self.btn_replace_all.clicked.connect(self._do_replace_all)
        replace_btn_row.addWidget(self.btn_replace_all)
        replace_btn_row.addStretch()
        self.lbl_replace_result = QLabel("")
        replace_btn_row.addWidget(self.lbl_replace_result)
        replace_layout.addRow(replace_btn_row)

        replace_group.setLayout(replace_layout)
        layout.addWidget(replace_group)

        # ── Status label ───────────────────────────────────────────────────────
        self.lbl_results = QLabel(self.tr("Enter search criteria and click Search"))
        layout.addWidget(self.lbl_results)

        # ── Buttons ────────────────────────────────────────────────────────────
        button_layout = QHBoxLayout()

        self.btn_search = QPushButton(self.tr("🔍 Search"))
        self.btn_search.clicked.connect(self._do_search)
        self.btn_search.setDefault(True)
        button_layout.addWidget(self.btn_search)

        self.btn_clear = QPushButton(self.tr("Clear"))
        self.btn_clear.clicked.connect(self._clear)
        button_layout.addWidget(self.btn_clear)

        button_layout.addStretch()

        self.btn_select_all = QPushButton(self.tr("Select All Results"))
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_select_all.setEnabled(False)
        button_layout.addWidget(self.btn_select_all)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        button_layout.addWidget(buttons)

        layout.addLayout(button_layout)

    # ── Pattern helpers ────────────────────────────────────────────────────────

    def _build_pattern(self, text: str) -> str:
        if not text:
            return ""
        pattern = text if self.chk_regex.isChecked() else re.escape(text)
        if self.chk_whole_word.isChecked():
            pattern = r"\b" + pattern + r"\b"
        return pattern

    def _re_flags(self) -> int:
        return 0 if self.chk_case.isChecked() else re.IGNORECASE

    # ── Search ─────────────────────────────────────────────────────────────────

    def _matches_status(self, row_data: dict, status_filter: str) -> bool:
        if status_filter == "any":
            return True
        if status_filter == "translated":
            return bool(row_data.get("translated") and row_data.get("status") == "translated")
        if status_filter == "not_translated":
            return not (row_data.get("translated") and row_data.get("status") == "translated")
        return True

    def _matches_column(self, text: str, pattern: str) -> bool:
        if not pattern:
            return True
        try:
            return bool(re.search(pattern, text, self._re_flags()))
        except re.error as exc:
            logger.warning("Invalid regex pattern: %s", exc)
            return False

    def perform_search(self, data: List[dict]) -> List[int]:
        column_filter = self.combo_column.currentData()
        status_filter = self.combo_status.currentData()
        search_text = self.txt_search.text().strip()
        id_text = self.txt_id.text().strip()

        if not search_text and not id_text and status_filter == "any":
            return []

        pattern = self._build_pattern(search_text) if search_text else ""

        id_filter = None
        if id_text:
            try:
                id_filter = int(id_text, 16) if id_text.lower().startswith("0x") else int(id_text)
            except ValueError:
                logger.warning("Invalid ID filter: %s", id_text)

        results = []
        for row_idx, row_data in enumerate(data):
            if not self._matches_status(row_data, status_filter):
                continue
            if id_filter is not None and row_data.get("id") != id_filter:
                continue
            if pattern:
                orig = row_data.get("original", "")
                trans = row_data.get("translated", "")
                if column_filter == "original":
                    if not self._matches_column(orig, pattern):
                        continue
                elif column_filter == "translated":
                    if not self._matches_column(trans, pattern):
                        continue
                elif column_filter == "both":
                    if not (self._matches_column(orig, pattern) or self._matches_column(trans, pattern)):
                        continue
                else:  # all
                    id_str = f"0x{row_data.get('id', 0):08X}"
                    if not any([
                        self._matches_column(orig, pattern),
                        self._matches_column(trans, pattern),
                        self._matches_column(id_str, pattern),
                    ]):
                        continue
            results.append(row_idx)
        return results

    def _do_search(self):
        _parent = self.parent()
        if not _parent:
            return

        model = _parent.table_model  # type: ignore[attr-defined]
        data = model._data

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(self.perform_search, data)
                results = future.result(timeout=5.0)
        except concurrent.futures.TimeoutError:
            logger.warning("Search timed out — pattern may be too complex")
            self.lbl_results.setText(self.tr("Search timed out — simplify the pattern"))
            self.btn_select_all.setEnabled(False)
            return

        self._last_results = results
        self.lbl_results.setText(self.tr("Found {count} result(s)").format(count=len(results)))
        self.btn_select_all.setEnabled(len(results) > 0)
        self.search_results.emit(results)

        if results:
            _parent.table_view.selectRow(results[0])  # type: ignore[attr-defined]

    # ── Replace ────────────────────────────────────────────────────────────────

    def _do_replace_all(self):
        _parent = self.parent()
        if not _parent:
            return

        search_text = self.txt_search.text().strip()
        if not search_text:
            QMessageBox.information(
                self, self.tr("Replace"), self.tr("Enter search text first.")
            )
            return

        try:
            pattern = self._build_pattern(search_text)
            # Validate pattern before touching any data
            re.compile(pattern, self._re_flags())
        except re.error as exc:
            QMessageBox.warning(
                self, self.tr("Invalid Pattern"),
                self.tr("Regular expression error:\n{error}").format(error=exc),
            )
            return

        replace_text = self.txt_replace.text()

        model = _parent.table_model  # type: ignore[attr-defined]
        data = model._data

        # Search restricted to translated column for replace
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(self.perform_search, data)
                matching_rows = future.result(timeout=5.0)
        except concurrent.futures.TimeoutError:
            self.lbl_results.setText(self.tr("Search timed out — simplify the pattern"))
            return

        if not matching_rows:
            self.lbl_results.setText(self.tr("No matches found"))
            self.lbl_replace_result.setText("")
            return

        # Confirm if many rows affected
        if len(matching_rows) > 20:
            reply = QMessageBox.question(
                self,
                self.tr("Replace All"),
                self.tr("Replace in {n} row(s)?").format(n=len(matching_rows)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        flags = self._re_flags()
        replaced_count = 0
        total_subs = 0

        for row_idx in matching_rows:
            row_data = data[row_idx]
            old_trans = row_data.get("translated", "")
            if not old_trans:
                continue
            try:
                new_trans, n = re.subn(pattern, replace_text, old_trans, flags=flags)
            except re.error:
                continue
            if n > 0 and new_trans != old_trans:
                model.set_translated_text(row_idx, new_trans)
                replaced_count += 1
                total_subs += n

        if replaced_count:
            msg = self.tr(
                "Replaced {subs} occurrence(s) in {rows} row(s)"
            ).format(subs=total_subs, rows=replaced_count)
            self.lbl_replace_result.setText(msg)
            self.lbl_results.setText(msg)
            logger.info("Find & Replace: %d substitutions in %d rows", total_subs, replaced_count)
        else:
            self.lbl_replace_result.setText(self.tr("No replacements made"))

        # Refresh search results highlight
        self._do_search()

    # ── Misc ───────────────────────────────────────────────────────────────────

    def _clear(self):
        self.txt_search.clear()
        self.txt_replace.clear()
        self.txt_id.clear()
        self.combo_column.setCurrentIndex(0)
        self.combo_status.setCurrentIndex(0)
        self.chk_regex.setChecked(False)
        self.chk_case.setChecked(False)
        self.chk_whole_word.setChecked(False)
        self.lbl_results.setText(self.tr("Enter search criteria and click Search"))
        self.lbl_replace_result.setText("")
        self.btn_select_all.setEnabled(False)
        self._last_results = []
        self.txt_search.setFocus()

    def _select_all(self):
        _parent = self.parent()
        if not _parent:
            return

        model = _parent.table_model  # type: ignore[attr-defined]
        data = model._data
        results = self.perform_search(data)

        selection_model = _parent.table_view.selectionModel()  # type: ignore[attr-defined]
        selection_model.clearSelection()
        for row_idx in results:
            index = model.index(row_idx, 0)
            selection_model.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)

        self.lbl_results.setText(
            self.tr("Selected {count} result(s)").format(count=len(results))
        )
