"""Lightweight dialog to assign (or clear) a character profile on selected strings."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from bethesda_strings.character_profiles import ProfileManager


class ProfileAssignDialog(QDialog):
    """Show the profile list; user picks one to assign to the selected rows.

    ``accepted_profile_id`` is ``None`` when the user chooses "Clear".
    ``was_accepted`` is ``True`` if the dialog was confirmed (vs cancelled).
    """

    def __init__(
        self,
        manager: ProfileManager,
        row_count: int,
        current_profile_id: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self.accepted_profile_id: Optional[str] = None
        self.was_accepted = False

        self.setWindowTitle(self.tr("Assign Character Profile"))
        self.setMinimumWidth(380)
        self.setModal(True)

        layout = QVBoxLayout(self)

        desc = QLabel(
            self.tr("Apply to {n} selected string(s):").format(n=row_count)
            if row_count != 1
            else self.tr("Apply to 1 selected string:")
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.itemDoubleClicked.connect(self._on_double_click)

        # "No profile" sentinel
        no_item = QListWidgetItem(self.tr("— No profile (clear assignment) —"))
        no_item.setData(Qt.ItemDataRole.UserRole, None)
        no_item.setForeground(QColor("#888"))
        self._list.addItem(no_item)

        for p in manager.all():
            item = QListWidgetItem()
            item.setText(f"  {p.name}")
            item.setToolTip(p.description)
            item.setData(Qt.ItemDataRole.UserRole, p.profile_id)

            # Colored bullet
            font = QFont()
            font.setBold(p.profile_id == current_profile_id)
            item.setFont(font)
            item.setForeground(QColor(p.color))
            self._list.addItem(item)

            if p.profile_id == current_profile_id:
                self._list.setCurrentItem(item)

        layout.addWidget(self._list, 1)

        btns = QDialogButtonBox()
        assign_btn = QPushButton(self.tr("Assign"))
        assign_btn.setDefault(True)
        assign_btn.clicked.connect(self._assign)
        btns.addButton(assign_btn, QDialogButtonBox.ButtonRole.AcceptRole)

        clear_btn = QPushButton(self.tr("Clear"))
        clear_btn.setToolTip(self.tr("Remove profile assignment from selected strings"))
        clear_btn.clicked.connect(self._clear)
        btns.addButton(clear_btn, QDialogButtonBox.ButtonRole.ResetRole)

        cancel_btn = QPushButton(self.tr("Cancel"))
        cancel_btn.clicked.connect(self.reject)
        btns.addButton(cancel_btn, QDialogButtonBox.ButtonRole.RejectRole)

        layout.addWidget(btns)

    def _assign(self) -> None:
        item = self._list.currentItem()
        if item is None:
            return
        self.accepted_profile_id = item.data(Qt.ItemDataRole.UserRole)
        self.was_accepted = True
        self.accept()

    def _clear(self) -> None:
        self.accepted_profile_id = None
        self.was_accepted = True
        self.accept()

    def _on_double_click(self, item: QListWidgetItem) -> None:
        self.accepted_profile_id = item.data(Qt.ItemDataRole.UserRole)
        self.was_accepted = True
        self.accept()
