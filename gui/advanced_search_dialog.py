"""
Advanced Search Dialog for Bethesda Strings AI Translator
Supports searching by ID, original text, translated text, status, and regex.
"""

import concurrent.futures
import logging
import re
from typing import List

from PySide6.QtCore import QItemSelectionModel, Qt, Signal
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
    QSpinBox,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class AdvancedSearchDialog(QDialog):
    """Dialog for advanced string searching with multiple criteria."""

    # Signal emitted when search is performed with results
    search_results = Signal(list)  # list of row indices that match

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Advanced Search"))
        self.setMinimumSize(550, 450)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Search criteria group
        criteria_group = QGroupBox(self.tr("Search Criteria"))
        criteria_layout = QFormLayout()

        # Search in column selector
        self.combo_column = QComboBox()
        self.combo_column.addItem(self.tr("All columns"), "all")
        self.combo_column.addItem(self.tr("Original text"), "original")
        self.combo_column.addItem(self.tr("Translated text"), "translated")
        self.combo_column.addItem(self.tr("Both texts"), "both")
        criteria_layout.addRow(self.tr("Search in:"), self.combo_column)

        # Search text
        self.txt_search = QLineEdit()
        self.txt_search.setPlaceholderText(self.tr("Enter search text..."))
        self.txt_search.returnPressed.connect(self._do_search)
        criteria_layout.addRow(self.tr("Text:"), self.txt_search)

        # String ID filter
        self.txt_id = QLineEdit()
        self.txt_id.setPlaceholderText(self.tr("e.g., 0x00001234 or 4660"))
        criteria_layout.addRow(self.tr("String ID:"), self.txt_id)

        # Status filter
        self.combo_status = QComboBox()
        self.combo_status.addItem(self.tr("Any status"), "any")
        self.combo_status.addItem(self.tr("Translated"), "translated")
        self.combo_status.addItem(self.tr("Not translated"), "not_translated")
        criteria_layout.addRow(self.tr("Status:"), self.combo_status)

        # Options
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

        # Results info
        self.lbl_results = QLabel(self.tr("Enter search criteria and click Search"))
        layout.addWidget(self.lbl_results)

        # Buttons
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

    def _build_pattern(self, text: str) -> str:
        """Build regex pattern from search options."""
        if not text:
            return ""

        if self.chk_regex.isChecked():
            # Use text as-is for regex
            pattern = text
        else:
            pattern = re.escape(text)

        if self.chk_whole_word.isChecked():
            pattern = r"\b" + pattern + r"\b"

        return pattern

    def _matches_status(self, row_data: dict, status_filter: str) -> bool:
        """Check if a row matches the status filter."""
        if status_filter == "any":
            return True
        if status_filter == "translated":
            return bool(row_data.get("translated") and row_data.get("status") == "translated")
        if status_filter == "not_translated":
            return bool(
                not row_data.get("translated") or row_data.get("status") != "translated"
            )
        return True

    def _matches_column(self, text: str, pattern: str, column_filter: str) -> bool:
        """Check if text matches search pattern for given column filter."""
        if not pattern:
            return True

        flags = 0 if self.chk_case.isChecked() else re.IGNORECASE
        try:
            return bool(re.search(pattern, text, flags))
        except re.error as e:
            logger.warning(f"Invalid regex pattern: {e}")
            return False

    def perform_search(self, data: List[dict]) -> List[int]:
        """Search through data and return matching row indices."""
        column_filter = self.combo_column.currentData()
        status_filter = self.combo_status.currentData()
        search_text = self.txt_search.text().strip()
        id_text = self.txt_id.text().strip()

        if not search_text and not id_text and status_filter == "any":
            return []

        # Build search pattern
        pattern = self._build_pattern(search_text) if search_text else ""

        # Parse ID filter if provided
        id_filter = None
        if id_text:
            try:
                if id_text.lower().startswith("0x"):
                    id_filter = int(id_text, 16)
                else:
                    id_filter = int(id_text)
            except ValueError:
                logger.warning(f"Invalid ID filter: {id_text}")

        results = []
        for row_idx, row_data in enumerate(data):
            # Check status filter
            if not self._matches_status(row_data, status_filter):
                continue

            # Check ID filter
            if id_filter is not None and row_data.get("id") != id_filter:
                continue

            # Check text search
            if pattern:
                if column_filter == "original":
                    if not self._matches_column(
                        row_data.get("original", ""), pattern, column_filter
                    ):
                        continue
                elif column_filter == "translated":
                    if not self._matches_column(
                        row_data.get("translated", ""), pattern, column_filter
                    ):
                        continue
                elif column_filter == "both":
                    orig_match = self._matches_column(
                        row_data.get("original", ""), pattern, column_filter
                    )
                    trans_match = self._matches_column(
                        row_data.get("translated", ""), pattern, column_filter
                    )
                    if not (orig_match or trans_match):
                        continue
                else:  # All columns
                    id_str = f"0x{row_data.get('id', 0):08X}"
                    orig_match = self._matches_column(
                        row_data.get("original", ""), pattern, column_filter
                    )
                    trans_match = self._matches_column(
                        row_data.get("translated", ""), pattern, column_filter
                    )
                    id_match = self._matches_column(id_str, pattern, column_filter)
                    if not (orig_match or trans_match or id_match):
                        continue

            results.append(row_idx)

        return results

    def _do_search(self):
        """Perform search and emit results."""
        _parent = self.parent()
        if not _parent:
            return

        model = _parent.table_model  # type: ignore[attr-defined]
        data = model._data

        # Run search in a worker thread so a pathological regex can be cancelled
        # after a timeout rather than hanging the UI thread indefinitely (ReDoS).
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(self.perform_search, data)
                results = future.result(timeout=5.0)
        except concurrent.futures.TimeoutError:
            logger.warning("Search timed out — pattern may be too complex")
            self.lbl_results.setText(self.tr("Search timed out — simplify the pattern"))
            self.btn_select_all.setEnabled(False)
            return

        self.lbl_results.setText(
            self.tr("Found {count} result(s)").format(count=len(results))
        )
        self.btn_select_all.setEnabled(len(results) > 0)

        self.search_results.emit(results)

        if results:
            _parent.table_view.selectRow(results[0])  # type: ignore[attr-defined]

    def _clear(self):
        """Clear search criteria."""
        self.txt_search.clear()
        self.txt_id.clear()
        self.combo_column.setCurrentIndex(0)
        self.combo_status.setCurrentIndex(0)
        self.chk_regex.setChecked(False)
        self.chk_case.setChecked(False)
        self.chk_whole_word.setChecked(False)
        self.lbl_results.setText(self.tr("Enter search criteria and click Search"))
        self.btn_select_all.setEnabled(False)
        self.txt_search.setFocus()

    def _select_all(self):
        """Select all search results in the table view."""
        _parent = self.parent()
        if not _parent:
            return

        # Get the latest results by re-running search
        model = _parent.table_model  # type: ignore[attr-defined]
        data = model._data
        results = self.perform_search(data)

        # Select all results in the table
        selection_model = _parent.table_view.selectionModel()  # type: ignore[attr-defined]
        selection_model.clearSelection()

        for row_idx in results:
            index = model.index(row_idx, 0)
            selection_model.select(
                index, QItemSelectionModel.Select | QItemSelectionModel.Rows
            )

        self.lbl_results.setText(
            self.tr("Selected {count} result(s)").format(count=len(results))
        )
