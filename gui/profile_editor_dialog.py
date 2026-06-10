"""Character Profile editor dialog.

Left panel: list of all profiles (built-in + user-created) with Add /
Duplicate / Delete buttons.

Right panel: edit form — name, color, formality, temperature, contractions,
free-text addendum with a "Regenerate" button that re-builds it from the
structured fields.
"""

from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bethesda_strings.character_profiles import CharacterProfile, ProfileManager

logger = logging.getLogger(__name__)

_FORMALITY_OPTIONS = ["casual", "neutral", "formal"]


class ProfileEditorDialog(QDialog):
    """Manage character profiles — create, edit, duplicate, delete."""

    def __init__(self, manager: ProfileManager, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._current: Optional[CharacterProfile] = None
        self._dirty = False            # unsaved edits to the form

        self.setWindowTitle(self.tr("Character Profiles"))
        self.setWindowIcon(QIcon.fromTheme("user-identity"))
        self.resize(880, 600)
        self.setModal(True)

        self._build_ui()
        self._populate_list()
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ── Left: profile list + buttons ──────────────────────────────────────
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel(self.tr("Profiles:")))

        self._list = QListWidget()
        self._list.setMinimumWidth(200)
        self._list.currentRowChanged.connect(self._on_row_changed)
        ll.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton(self.tr("＋ Add"))
        self._add_btn.setToolTip(self.tr("Create a new blank profile"))
        self._add_btn.clicked.connect(self._add_profile)
        btn_row.addWidget(self._add_btn)

        self._dup_btn = QPushButton(self.tr("⊞ Duplicate"))
        self._dup_btn.setToolTip(self.tr("Duplicate the selected profile"))
        self._dup_btn.clicked.connect(self._duplicate_profile)
        btn_row.addWidget(self._dup_btn)

        self._del_btn = QPushButton(self.tr("✕ Delete"))
        self._del_btn.setToolTip(self.tr("Delete the selected profile (built-in profiles cannot be deleted)"))
        self._del_btn.clicked.connect(self._delete_profile)
        btn_row.addWidget(self._del_btn)
        ll.addLayout(btn_row)
        splitter.addWidget(left)

        # ── Right: edit form ──────────────────────────────────────────────────
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)

        self._builtin_notice = QLabel(self.tr(
            "ℹ This is a built-in profile.  Name and color are read-only; "
            "you can freely edit the AI addendum."
        ))
        self._builtin_notice.setWordWrap(True)
        self._builtin_notice.setVisible(False)
        rl.addWidget(self._builtin_notice)

        # Basic info
        info_box = QGroupBox(self.tr("Profile"))
        info_form = QFormLayout(info_box)
        info_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(self.tr("e.g. Freestar Ranger"))
        self._name_edit.textChanged.connect(self._mark_dirty)
        info_form.addRow(self.tr("Name:"), self._name_edit)

        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText(self.tr("One-line description shown in the assign picker"))
        self._desc_edit.textChanged.connect(self._mark_dirty)
        info_form.addRow(self.tr("Description:"), self._desc_edit)

        color_row = QWidget()
        color_hl = QHBoxLayout(color_row)
        color_hl.setContentsMargins(0, 0, 0, 0)
        self._color_swatch = QLabel("  ")
        self._color_swatch.setFixedSize(28, 22)
        self._color_swatch.setAutoFillBackground(True)
        color_hl.addWidget(self._color_swatch)
        self._color_hex = QLineEdit()
        self._color_hex.setMaximumWidth(90)
        self._color_hex.setPlaceholderText("#RRGGBB")
        self._color_hex.textChanged.connect(self._on_color_hex_changed)
        color_hl.addWidget(self._color_hex)
        pick_btn = QToolButton()
        pick_btn.setText(self.tr("Pick…"))
        pick_btn.clicked.connect(self._pick_color)
        color_hl.addWidget(pick_btn)
        color_hl.addStretch(1)
        info_form.addRow(self.tr("Color:"), color_row)

        rl.addWidget(info_box)

        # AI settings
        ai_box = QGroupBox(self.tr("AI Settings"))
        ai_form = QFormLayout(ai_box)
        ai_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._formality_combo = QComboBox()
        for opt in _FORMALITY_OPTIONS:
            self._formality_combo.addItem(opt.capitalize(), opt)
        self._formality_combo.currentIndexChanged.connect(self._mark_dirty)
        ai_form.addRow(self.tr("Formality:"), self._formality_combo)

        temp_row = QWidget()
        temp_hl = QHBoxLayout(temp_row)
        temp_hl.setContentsMargins(0, 0, 0, 0)
        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.05)
        self._temp_spin.setDecimals(2)
        self._temp_spin.setSpecialValueText(self.tr("(worker default)"))
        self._temp_spin.setToolTip(self.tr(
            "0.00 = use the worker's default temperature.\n"
            "Higher values increase creativity/variation;\n"
            "lower values produce more deterministic output."
        ))
        self._temp_spin.valueChanged.connect(self._mark_dirty)
        temp_hl.addWidget(self._temp_spin)
        temp_hl.addWidget(QLabel(self.tr("(0.00 = worker default)")))
        temp_hl.addStretch(1)
        ai_form.addRow(self.tr("Temperature:"), temp_row)

        self._contractions_chk = QCheckBox(self.tr("Allow contractions"))
        self._contractions_chk.stateChanged.connect(self._mark_dirty)
        ai_form.addRow("", self._contractions_chk)

        rl.addWidget(ai_box)

        # System addendum
        add_box = QGroupBox(self.tr("System Prompt Addendum"))
        add_layout = QVBoxLayout(add_box)
        add_layout.addWidget(QLabel(self.tr(
            "This text is appended to the AI system prompt when translating strings "
            "assigned to this profile.  Edit freely or regenerate from the fields above."
        )))
        self._addendum_edit = QTextEdit()
        self._addendum_edit.setPlaceholderText(self.tr(
            "e.g. Character: Freestar Ranger (casual register)\n"
            "Use informal language. Contractions are natural…"
        ))
        self._addendum_edit.setMinimumHeight(100)
        self._addendum_edit.textChanged.connect(self._mark_dirty)
        add_layout.addWidget(self._addendum_edit, 1)

        regen_btn = QPushButton(self.tr("⟲ Regenerate from structured fields"))
        regen_btn.setToolTip(self.tr("Overwrite the addendum with text generated from Name + Formality + Contractions fields"))
        regen_btn.clicked.connect(self._regenerate_addendum)
        add_layout.addWidget(regen_btn)
        rl.addWidget(add_box, 1)

        # Save / Discard row
        save_row = QHBoxLayout()
        self._save_btn = QPushButton(self.tr("Save Changes"))
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save_current)
        save_row.addWidget(self._save_btn)
        self._discard_btn = QPushButton(self.tr("Discard"))
        self._discard_btn.setEnabled(False)
        self._discard_btn.clicked.connect(self._discard_changes)
        save_row.addWidget(self._discard_btn)
        save_row.addStretch(1)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self._on_close)
        save_row.addWidget(close_btn)
        rl.addLayout(save_row)

        splitter.addWidget(right)
        splitter.setSizes([220, 660])
        root.addWidget(splitter, 1)

    # ── List management ───────────────────────────────────────────────────────

    def _populate_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        for p in self._manager.all():
            item = QListWidgetItem()
            badge = "⚫" if p.is_builtin else "●"
            item.setText(f"{badge} {p.name}")
            item.setData(Qt.ItemDataRole.UserRole, p.profile_id)
            item.setForeground(QColor(p.color))
            if p.is_builtin:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            self._list.addItem(item)
        self._list.blockSignals(False)

    def _refresh_current_item(self) -> None:
        row = self._list.currentRow()
        if row < 0 or self._current is None:
            return
        item = self._list.item(row)
        badge = "⚫" if self._current.is_builtin else "●"
        item.setText(f"{badge} {self._current.name}")
        item.setForeground(QColor(self._current.color))

    # ── Profile CRUD ──────────────────────────────────────────────────────────

    def _add_profile(self) -> None:
        if not self._confirm_save():
            return
        p = self._manager.new_profile()
        self._manager.upsert(p)
        self._populate_list()
        # Select the new item
        for i in range(self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == p.profile_id:
                self._list.setCurrentRow(i)
                break

    def _duplicate_profile(self) -> None:
        if self._current is None:
            return
        if not self._confirm_save():
            return
        copy = self._manager.duplicate(self._current.profile_id)
        if copy:
            self._populate_list()
            for i in range(self._list.count()):
                if self._list.item(i).data(Qt.ItemDataRole.UserRole) == copy.profile_id:
                    self._list.setCurrentRow(i)
                    break

    def _delete_profile(self) -> None:
        if self._current is None:
            return
        if self._current.is_builtin:
            QMessageBox.information(
                self, self.tr("Cannot Delete"),
                self.tr("Built-in profiles cannot be deleted.")
            )
            return
        answer = QMessageBox.question(
            self,
            self.tr("Delete Profile"),
            self.tr("Delete profile '{name}'? This cannot be undone.").format(
                name=self._current.name
            ),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._manager.delete(self._current.profile_id)
        self._current = None
        self._dirty = False
        self._populate_list()
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._clear_form()

    # ── Form ──────────────────────────────────────────────────────────────────

    def _on_row_changed(self, row: int) -> None:
        if not self._confirm_save():
            # Revert the selection change
            self._list.blockSignals(True)
            for i in range(self._list.count()):
                if (self._current
                        and self._list.item(i).data(Qt.ItemDataRole.UserRole) == self._current.profile_id):
                    self._list.setCurrentRow(i)
                    break
            self._list.blockSignals(False)
            return
        if row < 0:
            self._clear_form()
            return
        item = self._list.item(row)
        pid = item.data(Qt.ItemDataRole.UserRole)
        self._current = self._manager.get(pid)
        self._load_form(self._current)

    def _load_form(self, p: Optional[CharacterProfile]) -> None:
        if p is None:
            self._clear_form()
            return
        self._dirty = False
        builtin = p.is_builtin
        self._builtin_notice.setVisible(builtin)
        self._name_edit.setReadOnly(builtin)
        self._color_hex.setReadOnly(builtin)

        self._name_edit.blockSignals(True)
        self._name_edit.setText(p.name)
        self._name_edit.blockSignals(False)

        self._desc_edit.blockSignals(True)
        self._desc_edit.setText(p.description)
        self._desc_edit.blockSignals(False)

        self._color_hex.blockSignals(True)
        self._color_hex.setText(p.color)
        self._color_hex.blockSignals(False)
        self._update_swatch(p.color)

        idx = _FORMALITY_OPTIONS.index(p.formality) if p.formality in _FORMALITY_OPTIONS else 1
        self._formality_combo.blockSignals(True)
        self._formality_combo.setCurrentIndex(idx)
        self._formality_combo.blockSignals(False)

        self._temp_spin.blockSignals(True)
        self._temp_spin.setValue(p.temperature if p.temperature is not None else 0.0)
        self._temp_spin.blockSignals(False)

        self._contractions_chk.blockSignals(True)
        self._contractions_chk.setChecked(p.allow_contractions)
        self._contractions_chk.blockSignals(False)

        self._addendum_edit.blockSignals(True)
        self._addendum_edit.setPlainText(p.system_addendum)
        self._addendum_edit.blockSignals(False)

        self._save_btn.setEnabled(False)
        self._discard_btn.setEnabled(False)

    def _clear_form(self) -> None:
        self._current = None
        self._dirty = False
        for w in (self._name_edit, self._desc_edit, self._color_hex):
            w.clear()
        self._addendum_edit.clear()
        self._save_btn.setEnabled(False)
        self._discard_btn.setEnabled(False)

    def _mark_dirty(self, *_) -> None:
        if not self._dirty:
            self._dirty = True
            self._save_btn.setEnabled(True)
            self._discard_btn.setEnabled(True)

    def _save_current(self) -> None:
        if self._current is None:
            return
        self._apply_form_to_profile(self._current)
        self._manager.upsert(self._current)
        self._dirty = False
        self._save_btn.setEnabled(False)
        self._discard_btn.setEnabled(False)
        self._refresh_current_item()

    def _discard_changes(self) -> None:
        if self._current:
            self._load_form(self._current)
        self._dirty = False

    def _apply_form_to_profile(self, p: CharacterProfile) -> None:
        if not p.is_builtin:
            p.name = self._name_edit.text().strip() or p.name
            color = self._color_hex.text().strip()
            if QColor(color).isValid():
                p.color = color
        p.description = self._desc_edit.text().strip()
        p.formality = _FORMALITY_OPTIONS[self._formality_combo.currentIndex()]
        t = self._temp_spin.value()
        p.temperature = t if t > 0.0 else None
        p.allow_contractions = self._contractions_chk.isChecked()
        p.system_addendum = self._addendum_edit.toPlainText().strip()

    def _regenerate_addendum(self) -> None:
        if self._current is None:
            return
        # Build a temporary profile from the current form state to preview addendum
        tmp = CharacterProfile(
            profile_id=self._current.profile_id,
            name=self._name_edit.text().strip() or self._current.name,
            description="",
            color="#808080",
            temperature=None,
            system_addendum="",
            formality=_FORMALITY_OPTIONS[self._formality_combo.currentIndex()],
            allow_contractions=self._contractions_chk.isChecked(),
            custom_instructions=self._current.custom_instructions,
            is_builtin=False,
        )
        self._addendum_edit.setPlainText(tmp.generate_addendum())

    # ── Color picker ──────────────────────────────────────────────────────────

    def _pick_color(self) -> None:
        initial = QColor(self._color_hex.text())
        if not initial.isValid():
            initial = QColor("#808080")
        color = QColorDialog.getColor(initial, self, self.tr("Pick Profile Color"))
        if color.isValid():
            self._color_hex.setText(color.name())

    def _on_color_hex_changed(self, text: str) -> None:
        self._update_swatch(text)
        self._mark_dirty()

    def _update_swatch(self, hex_color: str) -> None:
        c = QColor(hex_color)
        if c.isValid():
            pal = self._color_swatch.palette()
            pal.setColor(QPalette.ColorRole.Window, c)
            self._color_swatch.setPalette(pal)

    # ── Close guard ───────────────────────────────────────────────────────────

    def _confirm_save(self) -> bool:
        """Return True if it is safe to navigate away (saves or discards dirty state)."""
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            self.tr("Unsaved Changes"),
            self.tr("Save changes to '{name}'?").format(
                name=self._current.name if self._current else "?"
            ),
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            self._save_current()
            return True
        if answer == QMessageBox.StandardButton.Discard:
            self._dirty = False
            return True
        return False  # Cancel

    def _on_close(self) -> None:
        if self._confirm_save():
            self.accept()
