"""
Translation Session dialogs.

SessionManagerDialog  — lists all saved sessions; lets the user resume, create,
                        rename, or delete sessions.
NewSessionDialog      — prompts for name + optional note when creating a session.
RenameSessionDialog   — single-field rename dialog.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui.session_manager import WorkSession

logger = logging.getLogger(__name__)

_ROLE_NAME = Qt.ItemDataRole.UserRole
_ACTIVE_BG = QColor("#1a3d2b")    # green tint for the current session row


# ── Session Manager ────────────────────────────────────────────────────────────

class SessionManagerDialog(QDialog):
    """
    Shows all saved sessions with progress and metadata.

    Emits ``resume_requested(name)`` when the user wants to resume a session.
    """

    resume_requested = Signal(str)   # session name

    def __init__(
        self,
        sessions: List[WorkSession],
        current_session_name: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Translation Sessions"))
        self.resize(760, 480)
        self._sessions = sessions
        self._current_name = current_session_name
        self._setup_ui()
        self._populate()
        if sessions:
            self._table.selectRow(0)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        header = QLabel(self.tr(
            "Sessions let you save and resume named work contexts — search filter, "
            "cursor position, and per-session translation count."
        ))
        header.setWordWrap(True)
        root.addWidget(header)

        # ── Session list table ────────────────────────────────────────────────
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([
            self.tr("Name"),
            self.tr("File"),
            self.tr("Translated in session"),
            self.tr("Last modified"),
        ])
        hh = self._table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.selectionModel().currentRowChanged.connect(
            lambda cur, _prev: self._on_row_changed(cur.row())
        )
        self._table.cellDoubleClicked.connect(self._resume)
        root.addWidget(self._table, 1)

        # ── Detail panel ──────────────────────────────────────────────────────
        detail_box = QGroupBox(self.tr("Details"))
        d_lay = QFormLayout(detail_box)
        self._lbl_file   = QLabel()
        self._lbl_file.setWordWrap(True)
        self._lbl_search = QLabel()
        self._lbl_note   = QLabel()
        self._lbl_note.setWordWrap(True)
        d_lay.addRow(self.tr("File:"),   self._lbl_file)
        d_lay.addRow(self.tr("Filter:"), self._lbl_search)
        d_lay.addRow(self.tr("Note:"),   self._lbl_note)
        root.addWidget(detail_box)

        # ── Button bar ────────────────────────────────────────────────────────
        bar = QHBoxLayout()

        self._btn_resume = QPushButton(self.tr("Resume Session"))
        self._btn_resume.setDefault(True)
        self._btn_resume.setEnabled(False)
        self._btn_resume.clicked.connect(self._resume)

        self._btn_rename = QPushButton(self.tr("Rename…"))
        self._btn_rename.setEnabled(False)
        self._btn_rename.clicked.connect(self._rename)

        self._btn_delete = QPushButton(self.tr("Delete"))
        self._btn_delete.setEnabled(False)
        self._btn_delete.clicked.connect(self._delete)

        bar.addWidget(self._btn_resume)
        bar.addWidget(self._btn_rename)
        bar.addWidget(self._btn_delete)
        bar.addStretch()

        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        bar.addWidget(close_btn)
        root.addLayout(bar)

    # ── Data ──────────────────────────────────────────────────────────────────

    def _populate(self) -> None:
        self._table.setRowCount(len(self._sessions))
        bold = QFont()
        bold.setBold(True)

        for r, s in enumerate(self._sessions):
            is_active = (s.name == self._current_name)
            bg = _ACTIVE_BG if is_active else QColor()

            name_item = QTableWidgetItem(
                ("▶ " if is_active else "") + s.name)
            name_item.setData(_ROLE_NAME, s.name)
            if is_active:
                name_item.setFont(bold)
                name_item.setForeground(QColor("#4ade80"))

            file_item = QTableWidgetItem(Path(s.file_path).name if s.file_path else "—")
            file_item.setToolTip(s.file_path)

            count_item = QTableWidgetItem(
                str(s.translated_count) if s.translated_count else "—")
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            try:
                dt = datetime.fromisoformat(s.modified)
                mod_str = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                mod_str = s.modified[:16]
            mod_item = QTableWidgetItem(mod_str)
            mod_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            for col, item in enumerate((name_item, file_item, count_item, mod_item)):
                if is_active and bg.isValid():
                    item.setBackground(bg)
                self._table.setItem(r, col, item)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_row_changed(self, row: int) -> None:
        has_sel = 0 <= row < len(self._sessions)
        self._btn_resume.setEnabled(has_sel)
        self._btn_rename.setEnabled(has_sel)
        self._btn_delete.setEnabled(has_sel)
        if has_sel:
            s = self._sessions[row]
            self._lbl_file.setText(s.file_path or "—")
            self._lbl_search.setText(s.search.summary() or self.tr("(none)"))
            self._lbl_note.setText(s.note or self.tr("(none)"))

    def _current_session(self) -> Optional[WorkSession]:
        r = self._table.currentRow()
        if 0 <= r < len(self._sessions):
            return self._sessions[r]
        return None

    def _resume(self, *_) -> None:
        s = self._current_session()
        if s:
            self.resume_requested.emit(s.name)
            self.accept()

    def _rename(self) -> None:
        s = self._current_session()
        if not s:
            return
        dlg = RenameSessionDialog(s.name, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.new_name:
            s.name = dlg.new_name
            self._populate()

    def _delete(self) -> None:
        s = self._current_session()
        if not s:
            return
        ans = QMessageBox.question(
            self,
            self.tr("Delete Session"),
            self.tr('Delete session "{name}"? This cannot be undone.').format(
                name=s.name),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans == QMessageBox.StandardButton.Yes:
            self._sessions.remove(s)
            self._populate()
            if not self._sessions:
                for lbl in (self._lbl_file, self._lbl_search, self._lbl_note):
                    lbl.clear()
                self._btn_resume.setEnabled(False)
                self._btn_rename.setEnabled(False)
                self._btn_delete.setEnabled(False)


# ── New Session ────────────────────────────────────────────────────────────────

class NewSessionDialog(QDialog):
    """Prompts for a session name and an optional note."""

    def __init__(
        self,
        existing_names: List[str],
        suggested_name: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("New Translation Session"))
        self.setMinimumWidth(400)
        self._existing = existing_names
        self.session_name: str = ""
        self.session_note: str = ""
        self._setup_ui(suggested_name)

    def _setup_ui(self, suggested_name: str) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()

        self._name_edit = QLineEdit(suggested_name)
        self._name_edit.setPlaceholderText(self.tr("e.g., Barrett Dialogue"))
        self._name_edit.textChanged.connect(self._validate)
        form.addRow(self.tr("Session name:"), self._name_edit)

        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText(self.tr("Optional description…"))
        form.addRow(self.tr("Note:"), self._note_edit)

        self._warn = QLabel()
        self._warn.setStyleSheet("color: #dc2626;")
        form.addRow("", self._warn)

        root.addLayout(form)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        self._ok_btn = btns.button(QDialogButtonBox.StandardButton.Ok)
        root.addWidget(btns)

        self._validate()

    def _validate(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            self._warn.setText(self.tr("Name cannot be empty."))
            self._ok_btn.setEnabled(False)
        elif name in self._existing:
            self._warn.setText(self.tr("A session with this name already exists."))
            self._ok_btn.setEnabled(False)
        else:
            self._warn.clear()
            self._ok_btn.setEnabled(True)

    def _accept(self) -> None:
        self.session_name = self._name_edit.text().strip()
        self.session_note = self._note_edit.text().strip()
        self.accept()


# ── Rename Session ─────────────────────────────────────────────────────────────

class RenameSessionDialog(QDialog):
    def __init__(self, old_name: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Rename Session"))
        self.setMinimumWidth(340)
        self.new_name: str = ""
        self._setup_ui(old_name)

    def _setup_ui(self, old_name: str) -> None:
        root = QVBoxLayout(self)
        self._edit = QLineEdit(old_name)
        self._edit.selectAll()
        root.addWidget(QLabel(self.tr("New name:")))
        root.addWidget(self._edit)
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._accept)
        btns.rejected.connect(self.reject)
        root.addWidget(btns)

    def _accept(self) -> None:
        n = self._edit.text().strip()
        if n:
            self.new_name = n
            self.accept()
