"""
Dialog for selecting which strings file to edit from a BA2 archive.
"""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)


class BA2PickerDialog(QDialog):
    """
    Shown when a BA2 archive contains more than one translatable strings file.
    The user picks exactly one entry to open.
    """

    def __init__(self, archive_name: str, entries: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Select Strings File"))
        self.setMinimumWidth(480)
        self._selected: str | None = None

        layout = QVBoxLayout(self)

        lbl = QLabel(
            self.tr(
                "<b>{name}</b> contains {n} translatable file(s).<br>"
                "Select the one you want to edit:"
            ).format(name=archive_name, n=len(entries))
        )
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        self._list = QListWidget()
        for entry in entries:
            item = QListWidgetItem(entry)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(Path(entry.replace("\\", "/")).name)
            self._list.addItem(item)
        self._list.setCurrentRow(0)
        self._list.itemDoubleClicked.connect(self._accept_selection)
        layout.addWidget(self._list)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_selection(self) -> None:
        item = self._list.currentItem()
        if item:
            self._selected = item.data(Qt.ItemDataRole.UserRole)
            self.accept()

    def selected_entry(self) -> str | None:
        """Return the chosen internal archive path, or None if cancelled."""
        return self._selected
