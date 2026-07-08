"""
Translation Memory browser — a searchable, read-only view of the loaded TM.

The TM is otherwise invisible; this dialog makes it inspectable so the user can
confirm what will be reused before/while translating. Filters live across ID,
source, and translation as the user types.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class TMBrowserDialog(QDialog):
    """Read-only, filterable table of Translation Memory entries."""

    def __init__(self, memory, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Translation Memory Browser"))
        self.setMinimumSize(820, 560)
        self._rows = list(memory.entries()) if memory is not None else []

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(self.tr("Filter:")))
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.tr("Type to filter across ID, source and translation…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        top.addWidget(self.search, 1)
        layout.addLayout(top)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr("ID"), self.tr("Source"), self.tr("Translation")])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.count_lbl = QLabel()
        bottom.addWidget(self.count_lbl)
        bottom.addStretch(1)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        self._populate(self._rows)

    def _populate(self, rows) -> None:
        self.table.setRowCount(len(rows))
        for r, (sid, src, tr) in enumerate(rows):
            self.table.setItem(r, 0, QTableWidgetItem(sid))
            self.table.setItem(r, 1, QTableWidgetItem(src))
            self.table.setItem(r, 2, QTableWidgetItem(tr))
        self.count_lbl.setText(
            self.tr("{shown} of {total} entries").format(shown=len(rows), total=len(self._rows))
        )

    def _apply_filter(self, text: str) -> None:
        q = text.strip().lower()
        if not q:
            self._populate(self._rows)
            return
        filtered = [
            row for row in self._rows
            if q in row[0].lower() or q in row[1].lower() or q in row[2].lower()
        ]
        self._populate(filtered)
