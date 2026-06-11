"""
Detached String List window — pop-out table for multi-monitor workflows.

Creates an independent top-level window containing a second view of the
exact same StringTableModel and QItemSelectionModel used by the main
window.  Because both views share the same selection model, clicking a
row in either window immediately highlights it in the other.

Geometry is saved/restored via QSettings so the window opens on the
same monitor and at the same size across sessions.  When no saved
geometry exists, the window is placed on the second screen (if present).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHeaderView,
    QTableView, QVBoxLayout, QWidget,
)


class DetachedTableWindow(QWidget):
    """A standalone window containing a second view of the string list."""

    _SETTINGS_KEY = "DetachedTable/geometry"

    def __init__(
        self,
        table_model,
        selection_model: QItemSelectionModel,
        title: str = "String List",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, Qt.Window)
        self.setWindowTitle(title)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(900, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Second view — shares model and selection model with the main table.
        # EditTriggers is NoEditTriggers so all editing stays in the main window.
        self._view = QTableView()
        self._view.setObjectName("DetachedStringTableView")
        self._view.setAlternatingRowColors(True)
        self._view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._view.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._view.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self._view.setWordWrap(True)

        header = self._view.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)

        self._view.setModel(table_model)
        # Share the exact selection model so both views stay in sync
        self._view.setSelectionModel(selection_model)

        # Restore column widths to sensible defaults (user can resize)
        self._view.setColumnWidth(0, 100)   # ID
        self._view.setColumnWidth(1, 28)    # Kind
        self._view.setColumnWidth(2, 420)   # Original
        self._view.setColumnWidth(3, 420)   # Translated
        self._view.setColumnWidth(4, 60)    # Length
        self._view.setColumnWidth(5, 80)    # Offset
        self._view.setColumnWidth(6, 55)    # Status

        layout.addWidget(self._view)

    # ── Geometry helpers ──────────────────────────────────────────────────────

    def place_and_show(self, q_settings=None) -> None:
        """Restore saved geometry or place on the second screen, then show."""
        restored = False
        if q_settings is not None:
            geom = q_settings.value(self._SETTINGS_KEY)
            if geom is not None:
                self.restoreGeometry(geom)
                restored = True

        if not restored:
            screens = QApplication.screens()
            if len(screens) > 1:
                # Place on the second screen
                screen_geom = screens[1].availableGeometry()
                self.setGeometry(screen_geom)
            # else: leave at the default resize(900, 600) position

        self.show()
        self.raise_()
        self.activateWindow()

    def save_geometry_to(self, q_settings) -> None:
        """Save window geometry for next session."""
        q_settings.setValue(self._SETTINGS_KEY, self.saveGeometry())

    # ── Close ─────────────────────────────────────────────────────────────────

    def closeEvent(self, event: QCloseEvent) -> None:
        # Replace the shared selection model with a fresh one before the view
        # is destroyed so Qt doesn't delete the main window's selection model.
        from PySide6.QtCore import QItemSelectionModel
        self._view.setSelectionModel(QItemSelectionModel(self._view.model(), self._view))
        super().closeEvent(event)

    def scroll_to_current(self) -> None:
        """Scroll to make the currently selected row visible."""
        indexes = self._view.selectionModel().selectedRows() if self._view.selectionModel() else []
        if indexes:
            self._view.scrollTo(indexes[0], QAbstractItemView.EnsureVisible)
