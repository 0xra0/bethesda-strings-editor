"""
Mod-update migration dialog for ESP/ESM plugins.

EspMigrateSetupDialog — collects old / new / prior-translation plugin paths.
EspMigrateDialog      — shows the diff, migrates unchanged translations, exports.

This is the plugin-file counterpart of ``gui.version_compare_dialog`` (which
handles flat ``.strings`` files).  Diffing/migration logic lives in
``bethesda_strings.esp_diff``; this module is presentation only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from bethesda_strings.esp_diff import (
    EspDiffEntry,
    build_migration_items,
    esp_diff_summary,
    esp_to_csv,
    esp_to_html,
)
from bethesda_strings.version_diff import DiffStatus

logger = logging.getLogger(__name__)

_PLUGIN_FILTER = (
    "Plugin Files (*.esp *.esm *.esl *.ESP *.ESM *.ESL);;All Files (*)"
)

_STATUS_COLORS: Dict[DiffStatus, Tuple[str, str]] = {
    DiffStatus.ADDED:     ("#dcfce7", "#0f2b14"),
    DiffStatus.REMOVED:   ("#fee2e2", "#3b1212"),
    DiffStatus.MODIFIED:  ("#fef9c3", "#251e00"),
    DiffStatus.UNCHANGED: ("",         ""),
}

_STATUS_LABELS: Dict[DiffStatus, str] = {
    DiffStatus.ADDED:     "Added",
    DiffStatus.REMOVED:   "Removed",
    DiffStatus.MODIFIED:  "Modified",
    DiffStatus.UNCHANGED: "Unchanged",
}


def _is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    return app.palette().base().color().lightness() < 128  # type: ignore[union-attr]


# ══════════════════════════════════════════════════════════════════════════════
# Setup dialog — file selection
# ══════════════════════════════════════════════════════════════════════════════

class EspMigrateSetupDialog(QDialog):
    """Collects the plugin paths needed for a mod-update migration."""

    def __init__(
        self,
        initial_new_path: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Mod Update Migration (ESP/ESM)"))
        self.setMinimumWidth(620)
        self._setup_ui(initial_new_path)

    def _setup_ui(self, initial_new_path: str) -> None:
        layout = QVBoxLayout(self)

        intro = QLabel(self.tr(
            "Carry your existing translations forward to an updated mod. Provide "
            "the previous English plugin and the new English plugin to see what "
            "was added, changed, or removed. Add your prior <b>translated</b> "
            "plugin to migrate the unchanged strings automatically.\n\n"
            "Migration fills the currently-open plugin, so open the new version "
            "in the editor first."
        ))
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(intro)
        layout.addSpacing(8)

        def _file_row(label: str, placeholder: str, initial: str = "") -> Tuple[QLineEdit, QPushButton]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(190)
            edit = QLineEdit(initial)
            edit.setPlaceholderText(placeholder)
            btn = QPushButton(self.tr("Browse…"))
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            row.addWidget(btn)
            layout.addLayout(row)
            return edit, btn

        self._edit_old, btn_old = _file_row(
            self.tr("Old plugin (English):"),
            "MyMod_v1.0.esp (previous version)",
        )
        self._edit_new, btn_new = _file_row(
            self.tr("New plugin (English):"),
            "MyMod_v1.2.esp (updated version)",
            initial=initial_new_path,
        )
        self._edit_trans, btn_trans = _file_row(
            self.tr("Prior translation (optional):"),
            "MyMod_v1.0_UK.esp (your previous translation)",
        )

        def _browse(edit: QLineEdit, title: str) -> None:
            path, _ = QFileDialog.getOpenFileName(self, title, "", _PLUGIN_FILTER)
            if path:
                edit.setText(path)

        btn_old.clicked.connect(
            lambda: _browse(self._edit_old, self.tr("Select Old Plugin")))
        btn_new.clicked.connect(
            lambda: _browse(self._edit_new, self.tr("Select New Plugin")))
        btn_trans.clicked.connect(
            lambda: _browse(self._edit_trans, self.tr("Select Prior Translated Plugin")))

        layout.addSpacing(8)
        enc_row = QHBoxLayout()
        enc_row.addWidget(QLabel(self.tr("Text encoding:")))
        self._combo_enc = QComboBox()
        for enc in ("utf-8", "cp1252", "cp1251"):
            self._combo_enc.addItem(enc)
        enc_row.addWidget(self._combo_enc)
        enc_row.addStretch()
        layout.addLayout(enc_row)

        layout.addSpacing(8)
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self) -> None:
        if not self._edit_old.text().strip():
            QMessageBox.warning(self, self.tr("Missing File"),
                                self.tr("Please select the old plugin."))
            return
        if not self._edit_new.text().strip():
            QMessageBox.warning(self, self.tr("Missing File"),
                                self.tr("Please select the new plugin."))
            return
        self.accept()

    @property
    def old_path(self) -> str:
        return self._edit_old.text().strip()

    @property
    def new_path(self) -> str:
        return self._edit_new.text().strip()

    @property
    def translation_path(self) -> str:
        return self._edit_trans.text().strip()

    @property
    def encoding(self) -> str:
        return self._combo_enc.currentText()


# ══════════════════════════════════════════════════════════════════════════════
# Results dialog — diff table, migration, export
# ══════════════════════════════════════════════════════════════════════════════

class EspMigrateDialog(QDialog):
    """Shows an ESP/ESM version diff and migrates unchanged translations.

    Emits ``migrate_requested(list)`` where each item is
    ``(form_id, record_sig, field_sig, occurrence, translation)`` so the caller
    can match it to a loaded table row by the same composite key.
    """

    migrate_requested = Signal(object)  # List[Tuple[int,str,str,int,str]]

    def __init__(
        self,
        entries: List[EspDiffEntry],
        old_label: str = "Old Version",
        new_label: str = "New Version",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Mod Update Migration"))
        self.resize(1280, 760)
        self.setMinimumSize(960, 560)

        self._entries = entries
        self._old_label = old_label
        self._new_label = new_label
        self._row_entries: List[EspDiffEntry] = []

        self._setup_ui()
        self._populate_table(entries)

    # ── UI construction ──────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        counts = esp_diff_summary(self._entries)
        badge_styles = {
            "added":     "color:#16a34a;font-weight:700;",
            "removed":   "color:#dc2626;font-weight:700;",
            "modified":  "color:#d97706;font-weight:700;",
            "unchanged": "color:#64748b;font-weight:700;",
        }
        for status, count in counts.items():
            lbl = QLabel(f"{count} {status}")
            lbl.setStyleSheet(
                f"font-size:13px;padding:4px 10px;border-radius:4px;"
                f"background:#f1f5f9;{badge_styles.get(status, '')}"
            )
            summary_row.addWidget(lbl)
        summary_row.addStretch()
        root.addLayout(summary_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        filter_row.addWidget(QLabel(self.tr("Show:")))
        self._filters: Dict[DiffStatus, QCheckBox] = {}
        for status in DiffStatus:
            chk = QCheckBox(_STATUS_LABELS[status])
            chk.setChecked(True)
            chk.toggled.connect(self._apply_filter)
            filter_row.addWidget(chk)
            self._filters[status] = chk
        self._chk_changed_only = QCheckBox(self.tr("Changed only"))
        self._chk_changed_only.toggled.connect(self._on_changed_only)
        filter_row.addSpacing(16)
        filter_row.addWidget(self._chk_changed_only)
        filter_row.addStretch()
        root.addLayout(filter_row)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            self.tr("FormID"), self.tr("EditorID"), self.tr("Record·Field"),
            self.tr("Status"), self._old_label, self._new_label,
            self.tr("Existing Translation"),
        ])
        hdr = self._table.horizontalHeader()
        for col in (0, 1, 2, 3):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col in (4, 5, 6):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setDefaultSectionSize(48)
        self._table.setWordWrap(True)
        root.addWidget(self._table, 1)

        btn_row = QHBoxLayout()
        self._migrate_btn = QPushButton()
        self._migrate_btn.clicked.connect(self._migrate_unchanged)
        btn_row.addWidget(self._migrate_btn)
        btn_row.addSpacing(16)

        export_csv_btn = QPushButton(self.tr("Export CSV…"))
        export_csv_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(export_csv_btn)
        export_html_btn = QPushButton(self.tr("Export HTML Report…"))
        export_html_btn.clicked.connect(self._export_html)
        btn_row.addWidget(export_html_btn)
        btn_row.addStretch()

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        btn_row.addWidget(btn_box)
        root.addLayout(btn_row)

        self._update_migrate_btn()

    # ── Table ────────────────────────────────────────────────────────────────
    def _populate_table(self, entries: List[EspDiffEntry]) -> None:
        self._table.setRowCount(0)
        self._row_entries = []
        dark = _is_dark()
        mono = QFont("DejaVu Sans Mono", 9)

        self._table.setUpdatesEnabled(False)
        try:
            for entry in entries:
                row = self._table.rowCount()
                self._table.insertRow(row)
                self._row_entries.append(entry)

                bg_hex = _STATUS_COLORS[entry.status][1 if dark else 0]
                bg = QColor(bg_hex) if bg_hex else None

                cells = [
                    f"0x{entry.form_id:08X}",
                    entry.edid,
                    f"{entry.record_sig} {entry.field_sig}",
                    _STATUS_LABELS[entry.status].upper(),
                    entry.old_text,
                    entry.new_text,
                    entry.existing_translation,
                ]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    if col >= 4:
                        item.setFont(mono)
                    if bg:
                        item.setBackground(bg)
                    if col == 3:
                        item.setData(Qt.ItemDataRole.UserRole, entry.status.value)
                    self._table.setItem(row, col, item)
        finally:
            self._table.setUpdatesEnabled(True)

        self._table.resizeRowsToContents()

    # ── Filtering ────────────────────────────────────────────────────────────
    @Slot()
    def _apply_filter(self) -> None:
        visible = {s for s, chk in self._filters.items() if chk.isChecked()}
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 3)
            if item is None:
                continue
            try:
                status = DiffStatus(item.data(Qt.ItemDataRole.UserRole))
            except ValueError:
                continue
            self._table.setRowHidden(row, status not in visible)

    @Slot(bool)
    def _on_changed_only(self, checked: bool) -> None:
        for status, chk in self._filters.items():
            if status == DiffStatus.UNCHANGED:
                chk.setChecked(not checked)
                chk.setEnabled(not checked)

    # ── Migration ────────────────────────────────────────────────────────────
    def _update_migrate_btn(self) -> None:
        migratable = sum(1 for e in self._entries if e.can_migrate())
        self._migrate_btn.setText(
            self.tr("Migrate {n} Unchanged Translation(s)").format(n=migratable))
        self._migrate_btn.setEnabled(migratable > 0)

    @Slot()
    def _migrate_unchanged(self) -> None:
        items = build_migration_items(self._entries)
        if not items:
            return
        reply = QMessageBox.question(
            self,
            self.tr("Migrate Translations"),
            self.tr(
                "Copy {n} existing translation(s) for unchanged strings into the "
                "currently-open plugin?\n\nOnly strings that are still untranslated "
                "or pending will be filled — your in-progress work is left alone."
            ).format(n=len(items)),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.migrate_requested.emit(items)
        logger.info("ESP migration: %d translations emitted", len(items))
        QMessageBox.information(
            self,
            self.tr("Migration Complete"),
            self.tr("{n} translation(s) applied to the open plugin. Save the file "
                    "to keep the changes.").format(n=len(items)),
        )

    # ── Export ───────────────────────────────────────────────────────────────
    @Slot()
    def _export_csv(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self, self.tr("Export as CSV"), "mod_migration.csv",
            "CSV Files (*.csv);;All Files (*)")
        if not path:
            return
        try:
            Path(path).write_text(esp_to_csv(self._entries), encoding="utf-8-sig")
            logger.info("ESP migration CSV exported to %s", path)
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))

    @Slot()
    def _export_html(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self, self.tr("Export as HTML Report"), "mod_migration.html",
            "HTML Files (*.html);;All Files (*)")
        if not path:
            return
        try:
            html_text = esp_to_html(
                self._entries, old_label=self._old_label, new_label=self._new_label,
                changed_only=self._chk_changed_only.isChecked())
            Path(path).write_text(html_text, encoding="utf-8")
            logger.info("ESP migration HTML report exported to %s", path)
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
            except Exception:
                pass
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))
