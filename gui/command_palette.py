"""
Ctrl+K command palette — searchable quick-action launcher.
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.keyboard_manager import ActionEntry, KeyboardManager

logger = logging.getLogger(__name__)


class CommandPaletteDialog(QDialog):
    """
    VS Code-style command palette (Ctrl+K).

    Displays all registered actions with their shortcuts; type to filter.
    Arrow keys / Enter to navigate and execute. Escape to dismiss.
    """

    def __init__(self, keyboard_manager: KeyboardManager, parent=None) -> None:
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint)
        self._km = keyboard_manager
        self._setup_ui()
        self._populate("")
        self._center_under_title_bar()

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setFixedWidth(660)
        self.setMaximumHeight(480)
        self.setObjectName("CommandPalette")
        self.setStyleSheet(
            "QDialog#CommandPalette {"
            "  border: 1px solid rgba(99,102,241,0.65);"
            "  border-radius: 8px;"
            "}"
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Search input ──────────────────────────────────────────────────────
        search_wrap = QWidget()
        search_wrap.setObjectName("CPSearchWrap")
        sw_layout = QHBoxLayout(search_wrap)
        sw_layout.setContentsMargins(12, 8, 12, 8)

        self._search = QLineEdit()
        self._search.setObjectName("CPSearch")
        self._search.setPlaceholderText(self.tr("Type to search actions…"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_query_changed)
        self._search.returnPressed.connect(self._execute_selected)
        sw_layout.addWidget(self._search)
        outer.addWidget(search_wrap)

        # Subtle separator
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(99,102,241,0.25);")
        outer.addWidget(sep)

        # ── Results list ──────────────────────────────────────────────────────
        self._list = QListWidget()
        self._list.setObjectName("CPList")
        self._list.setFrameShape(QListWidget.NoFrame)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.itemActivated.connect(self._on_item_activated)
        outer.addWidget(self._list)

        # ── Hint bar ──────────────────────────────────────────────────────────
        hint_wrap = QWidget()
        hint_wrap.setObjectName("CPHintBar")
        hint_layout = QHBoxLayout(hint_wrap)
        hint_layout.setContentsMargins(12, 3, 12, 3)
        lbl = QLabel(self.tr("↵ Execute   ↑↓ Navigate   Esc Dismiss"))
        lbl.setStyleSheet("font-size: 10px; color: palette(mid);")
        hint_layout.addWidget(lbl)
        hint_layout.addStretch()
        outer.addWidget(hint_wrap)

    def _center_under_title_bar(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        pw = parent.frameGeometry()
        self.adjustSize()
        x = pw.left() + (pw.width() - self.width()) // 2
        y = pw.top() + 52
        self.move(x, y)

    # ── Population ────────────────────────────────────────────────────────────

    def _populate(self, query: str) -> None:
        self._list.clear()
        entries = self._km.search(query)

        for e in entries:
            enabled = self._km.is_enabled(e.id)

            item = QListWidgetItem(self._list)
            item.setData(Qt.UserRole, e)
            if not enabled:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)

            row_widget = self._build_row(e, enabled)
            item.setSizeHint(row_widget.sizeHint())
            self._list.setItemWidget(item, row_widget)

        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        self.adjustSize()

    def _build_row(self, e: ActionEntry, enabled: bool) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(12, 5, 12, 5)
        layout.setSpacing(8)

        # Category chip
        cat = QLabel(e.category)
        cat.setStyleSheet(
            "padding: 1px 6px; border-radius: 3px;"
            " background: rgba(99,102,241,0.18); font-size: 10px;"
        )
        layout.addWidget(cat)

        # Action name
        name = QLabel(e.name)
        if not enabled:
            name.setStyleSheet("color: palette(mid);")
        layout.addWidget(name, 1)

        # Shortcut badge
        sc = self._km.effective_shortcut(e.id)
        if sc:
            sc_lbl = QLabel(KeyboardManager.shortcut_display(sc))
            sc_lbl.setStyleSheet(
                "padding: 1px 6px; border: 1px solid palette(mid);"
                " border-radius: 3px; font-size: 10px;"
            )
            layout.addWidget(sc_lbl)

        return row

    # ── Interaction ───────────────────────────────────────────────────────────

    def _on_query_changed(self, text: str) -> None:
        self._populate(text)

    def _on_item_activated(self, item: QListWidgetItem) -> None:
        self._trigger_item(item)

    def _execute_selected(self) -> None:
        self._trigger_item(self._list.currentItem())

    def _trigger_item(self, item: Optional[QListWidgetItem]) -> None:
        if item is None:
            return
        entry: ActionEntry = item.data(Qt.UserRole)
        if entry and self._km.is_enabled(entry.id):
            self.accept()
            try:
                entry.callback()
            except Exception as exc:
                logger.warning("Command palette action %r failed: %s", entry.id, exc)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            self.reject()
        elif key == Qt.Key_Down:
            row = self._list.currentRow()
            self._list.setCurrentRow(min(row + 1, self._list.count() - 1))
        elif key == Qt.Key_Up:
            row = self._list.currentRow()
            self._list.setCurrentRow(max(row - 1, 0))
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self._execute_selected()
        else:
            super().keyPressEvent(event)
