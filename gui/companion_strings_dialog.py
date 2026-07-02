"""Read-only viewer for the companion-triplet reference.

Shows the strings from the sibling .strings/.dlstrings/.ilstrings files that were
loaded alongside the current file.  Purely informational — nothing here can be
edited or saved, which is the whole point: the three files have independent ID
spaces and must never be merged into the file being translated.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from bethesda_strings.core import format_string_id
from bethesda_strings.triplet import TripletReference


class CompanionStringsDialog(QDialog):
    def __init__(self, reference: TripletReference, parent=None) -> None:
        super().__init__(parent)
        self._ref = reference
        self.setWindowTitle(self.tr("Companion Strings (read-only reference)"))
        self.resize(760, 520)

        layout = QVBoxLayout(self)

        info = QLabel(self.tr(
            "These strings come from the sibling .strings/.dlstrings/.ilstrings "
            "files.\nThey each keep their own independent ID space and are never "
            "written into the file you are translating."
        ))
        info.setWordWrap(True)
        layout.addWidget(info)

        # Filter row: by extension + free-text search.
        filt = QHBoxLayout()
        filt.addWidget(QLabel(self.tr("File type:")))
        self._ext_combo = QComboBox()
        self._ext_combo.addItem(self.tr("All"), "")
        for ext in reference.extensions():
            self._ext_combo.addItem(f".{ext}", ext)
        self._ext_combo.currentIndexChanged.connect(self._refill)
        filt.addWidget(self._ext_combo)

        filt.addSpacing(12)
        filt.addWidget(QLabel(self.tr("Search:")))
        self._search = QLineEdit()
        self._search.setPlaceholderText(self.tr("filter by ID (hex) or text…"))
        self._search.textChanged.connect(self._refill)
        filt.addWidget(self._search, 1)
        layout.addLayout(filt)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            self.tr("File"), self.tr("ID"), self.tr("Text"),
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setColumnWidth(0, 220)
        self._table.setColumnWidth(1, 90)
        layout.addWidget(self._table, 1)

        self._count_lbl = QLabel()
        layout.addWidget(self._count_lbl)

        self._refill()

    def _refill(self) -> None:
        ext_filter = self._ext_combo.currentData()
        query = self._search.text().strip().lower()

        rows = []
        for e in self._ref.iter_entries():
            if ext_filter and e.ext != ext_filter:
                continue
            if query:
                id_hex = format_string_id(e.string_id, prefix="").lower()
                if query not in e.text.lower() and query not in id_hex:
                    continue
            rows.append(e)

        self._table.setRowCount(len(rows))
        for r, e in enumerate(rows):
            src_item = QTableWidgetItem(e.source)
            id_item = QTableWidgetItem(format_string_id(e.string_id))
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            text_item = QTableWidgetItem(e.text)
            self._table.setItem(r, 0, src_item)
            self._table.setItem(r, 1, id_item)
            self._table.setItem(r, 2, text_item)

        self._count_lbl.setText(
            self.tr("{shown} of {total} companion strings").format(
                shown=len(rows), total=len(self._ref)
            )
        )
