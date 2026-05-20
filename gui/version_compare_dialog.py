"""
Version-to-version comparison dialog for Bethesda string files.

VersionCompareDialog  — single-file pair comparison, migration, and export.
VersionBatchDialog    — folder-pair batch comparison with aggregate report.
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
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from bethesda_strings.version_diff import (
    DiffStatus,
    VersionDiffEntry,
    compute_version_diff,
    diff_summary,
    load_strings_file,
    to_csv,
    to_html,
)

logger = logging.getLogger(__name__)

# ── Status colours ─────────────────────────────────────────────────────────────
_STATUS_COLORS: Dict[DiffStatus, Tuple[str, str]] = {
    # (light-bg, dark-bg)
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
# VersionCompareDialog
# ══════════════════════════════════════════════════════════════════════════════

class VersionCompareDialog(QDialog):
    """Shows a version diff and lets the user migrate unchanged translations.

    Emits migrate_requested({string_id: translation}) so the caller can apply
    the migrated translations to the current table model.
    """

    migrate_requested = Signal(dict)   # {int string_id: str translation}

    def __init__(
        self,
        entries: List[VersionDiffEntry],
        old_label: str = "Old Version",
        new_label: str = "New Version",
        translation_label: str = "Existing Translation",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Game Version Comparison"))
        self.resize(1200, 720)
        self.setMinimumSize(900, 540)

        self._entries = entries
        self._old_label = old_label
        self._new_label = new_label
        self._translation_label = translation_label
        self._row_entries: List[VersionDiffEntry] = []  # parallel to table rows

        self._setup_ui()
        self._populate_table(entries)

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(6)

        # ── Summary badges ─────────────────────────────────────────────────────
        summary_row = QHBoxLayout()
        summary_row.setSpacing(12)
        counts = diff_summary(self._entries)
        badge_styles = {
            "added":     "color:#16a34a;font-weight:700;",
            "removed":   "color:#dc2626;font-weight:700;",
            "modified":  "color:#d97706;font-weight:700;",
            "unchanged": "color:#64748b;font-weight:700;",
        }
        self._stat_labels: Dict[str, QLabel] = {}
        for status, count in counts.items():
            lbl = QLabel(f"{count} {status}")
            lbl.setStyleSheet(
                f"font-size:13px;padding:4px 10px;border-radius:4px;"
                f"background:#f1f5f9;{badge_styles.get(status, '')}"
            )
            summary_row.addWidget(lbl)
            self._stat_labels[status] = lbl
        summary_row.addStretch()
        root.addLayout(summary_row)

        # ── Filter row ─────────────────────────────────────────────────────────
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

        # ── Main table ─────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            self.tr("ID"),
            self.tr("Status"),
            self._old_label,
            self._new_label,
            self._translation_label,
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setDefaultSectionSize(48)
        self._table.setWordWrap(True)
        root.addWidget(self._table, 1)

        # ── Action buttons ─────────────────────────────────────────────────────
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

    # ── Table population ───────────────────────────────────────────────────────

    def _populate_table(self, entries: List[VersionDiffEntry]) -> None:
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
                    f"0x{entry.string_id:08X}",
                    _STATUS_LABELS[entry.status].upper(),
                    entry.old_text,
                    entry.new_text,
                    entry.existing_translation,
                ]
                for col, text in enumerate(cells):
                    item = QTableWidgetItem(text)
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    if col >= 2:
                        item.setFont(mono)
                    if bg:
                        item.setBackground(bg)
                    self._table.setItem(row, col, item)

        finally:
            self._table.setUpdatesEnabled(True)

        # Store status in row UserRole on column 1 for filtering
        for row in range(self._table.rowCount()):
            entry = self._row_entries[row]
            status_item = self._table.item(row, 1)
            if status_item is not None:
                status_item.setData(Qt.ItemDataRole.UserRole, entry.status.value)

        self._table.resizeRowsToContents()

    # ── Filtering ──────────────────────────────────────────────────────────────

    @Slot()
    def _apply_filter(self) -> None:
        visible_statuses = {
            status
            for status, chk in self._filters.items()
            if chk.isChecked()
        }
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item is None:
                continue
            status_val = item.data(Qt.ItemDataRole.UserRole)
            try:
                status = DiffStatus(status_val)
            except ValueError:
                continue
            self._table.setRowHidden(row, status not in visible_statuses)

    @Slot(bool)
    def _on_changed_only(self, checked: bool) -> None:
        for status, chk in self._filters.items():
            if status == DiffStatus.UNCHANGED:
                chk.setChecked(not checked)
                chk.setEnabled(not checked)

    # ── Migration ──────────────────────────────────────────────────────────────

    def _update_migrate_btn(self) -> None:
        migratable = sum(1 for e in self._entries if e.can_migrate())
        self._migrate_btn.setText(
            self.tr("Migrate {n} Unchanged Translation(s)").format(n=migratable)
        )
        self._migrate_btn.setEnabled(migratable > 0)

    @Slot()
    def _migrate_unchanged(self) -> None:
        candidates = [e for e in self._entries if e.can_migrate()]
        if not candidates:
            return

        reply = QMessageBox.question(
            self,
            self.tr("Migrate Translations"),
            self.tr(
                "Copy {n} existing translation(s) for unchanged strings to the "
                "current file?\n\nThis will only update strings that are currently "
                "untranslated or marked as pending."
            ).format(n=len(candidates)),
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        payload = {e.string_id: e.existing_translation for e in candidates}
        self.migrate_requested.emit(payload)
        logger.info("Version migration: %d translations emitted", len(payload))
        QMessageBox.information(
            self,
            self.tr("Migration Complete"),
            self.tr("{n} translation(s) applied. Save the file to keep the changes.").format(
                n=len(payload)
            ),
        )

    # ── Export ─────────────────────────────────────────────────────────────────

    @Slot()
    def _export_csv(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self,
            self.tr("Export as CSV"),
            "version_diff.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            csv_text = to_csv(
                self._entries,
                old_label=self._old_label,
                new_label=self._new_label,
                translation_label=self._translation_label,
            )
            Path(path).write_text(csv_text, encoding="utf-8-sig")
            logger.info("Version diff CSV exported to %s", path)
            self.statusBar().showMessage if hasattr(self, "statusBar") else None
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))

    @Slot()
    def _export_html(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self,
            self.tr("Export as HTML Report"),
            "version_diff.html",
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        try:
            html_text = to_html(
                self._entries,
                old_label=self._old_label,
                new_label=self._new_label,
                translation_label=self._translation_label,
                changed_only=self._chk_changed_only.isChecked(),
            )
            Path(path).write_text(html_text, encoding="utf-8")
            logger.info("Version diff HTML report exported to %s", path)
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
            except Exception:
                pass
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))


# ══════════════════════════════════════════════════════════════════════════════
# File-selection setup dialog (shown before VersionCompareDialog)
# ══════════════════════════════════════════════════════════════════════════════

class VersionCompareSetupDialog(QDialog):
    """Collects the three file paths needed for a version comparison."""

    def __init__(
        self,
        initial_new_path: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Compare Game Versions"))
        self.setMinimumWidth(580)
        self._setup_ui(initial_new_path)

    def _setup_ui(self, initial_new_path: str) -> None:
        layout = QVBoxLayout(self)

        intro = QLabel(self.tr(
            "Compare two versions of the same source string file to identify what "
            "changed between game updates. Optionally provide a prior translation "
            "file to migrate unchanged strings automatically."
        ))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(8)

        def _file_row(label: str, placeholder: str, initial: str = "") -> Tuple[QLabel, QLineEdit, QPushButton]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(160)
            edit = QLineEdit(initial)
            edit.setPlaceholderText(placeholder)
            btn = QPushButton(self.tr("Browse…"))
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            row.addWidget(btn)
            layout.addLayout(row)
            return lbl, edit, btn

        _, self._edit_old, btn_old = _file_row(
            self.tr("Old source file:"),
            "Starfield_en.strings (previous game version)",
        )
        _, self._edit_new, btn_new = _file_row(
            self.tr("New source file:"),
            "Starfield_en.strings (current game version)",
            initial=initial_new_path,
        )
        _, self._edit_trans, btn_trans = _file_row(
            self.tr("Prior translation (optional):"),
            "Starfield_uk.strings (existing translation)",
        )

        _FILTER = (
            "String Files (*.strings *.dlstrings *.ilstrings "
            "*.STRINGS *.DLSTRINGS *.ILSTRINGS);;All Files (*)"
        )

        def _browse(edit: QLineEdit, title: str) -> None:
            path, _ = QFileDialog.getOpenFileName(self, title, "", _FILTER)
            if path:
                edit.setText(path)

        btn_old.clicked.connect(
            lambda: _browse(self._edit_old, self.tr("Select Old Source File"))
        )
        btn_new.clicked.connect(
            lambda: _browse(self._edit_new, self.tr("Select New Source File"))
        )
        btn_trans.clicked.connect(
            lambda: _browse(self._edit_trans, self.tr("Select Prior Translation File"))
        )

        layout.addSpacing(8)

        encoding_row = QHBoxLayout()
        encoding_row.addWidget(QLabel(self.tr("Source encoding:")))
        from PySide6.QtWidgets import QComboBox
        self._combo_enc = QComboBox()
        for enc in ("utf-8", "cp1251", "cp1252"):
            self._combo_enc.addItem(enc)
        encoding_row.addWidget(self._combo_enc)
        encoding_row.addStretch()
        layout.addLayout(encoding_row)

        layout.addSpacing(8)
        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self._on_accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def _on_accept(self) -> None:
        if not self._edit_old.text().strip():
            QMessageBox.warning(
                self, self.tr("Missing File"), self.tr("Please select the old source file.")
            )
            return
        if not self._edit_new.text().strip():
            QMessageBox.warning(
                self, self.tr("Missing File"), self.tr("Please select the new source file.")
            )
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
# VersionBatchDialog — folder-level batch comparison
# ══════════════════════════════════════════════════════════════════════════════

_GLOB_PATTERNS = ("*.strings", "*.dlstrings", "*.ilstrings",
                  "*.STRINGS", "*.DLSTRINGS", "*.ILSTRINGS")


class VersionBatchDialog(QDialog):
    """Compare all matching .strings files between two game-version folders."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Batch Compare Game Folders"))
        self.resize(900, 640)
        self.setMinimumSize(700, 480)
        self._results: List[Tuple[str, Dict[str, int], List[VersionDiffEntry]]] = []
        self._setup_ui()

    # ── UI construction ────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Folder selectors ───────────────────────────────────────────────────
        grp = QGroupBox(self.tr("Folders"))
        grp_layout = QVBoxLayout(grp)

        def _folder_row(label: str) -> Tuple[QLineEdit, QPushButton]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setFixedWidth(180)
            edit = QLineEdit()
            edit.setPlaceholderText(self.tr("Click Browse to select…"))
            btn = QPushButton(self.tr("Browse…"))
            row.addWidget(lbl)
            row.addWidget(edit, 1)
            row.addWidget(btn)
            grp_layout.addLayout(row)
            return edit, btn

        self._edit_old_dir, btn_old = _folder_row(self.tr("Old game folder:"))
        self._edit_new_dir, btn_new = _folder_row(self.tr("New game folder:"))
        self._edit_trans_dir, btn_trans = _folder_row(
            self.tr("Translations folder (optional):")
        )

        def _browse_dir(edit: QLineEdit, title: str) -> None:
            d = QFileDialog.getExistingDirectory(self, title, "")
            if d:
                edit.setText(d)

        btn_old.clicked.connect(
            lambda: _browse_dir(self._edit_old_dir, self.tr("Select Old Game Folder"))
        )
        btn_new.clicked.connect(
            lambda: _browse_dir(self._edit_new_dir, self.tr("Select New Game Folder"))
        )
        btn_trans.clicked.connect(
            lambda: _browse_dir(self._edit_trans_dir, self.tr("Select Translations Folder"))
        )

        enc_row = QHBoxLayout()
        enc_row.addWidget(QLabel(self.tr("Encoding:")))
        from PySide6.QtWidgets import QComboBox
        self._combo_enc = QComboBox()
        for enc in ("utf-8", "cp1251", "cp1252"):
            self._combo_enc.addItem(enc)
        enc_row.addWidget(self._combo_enc)
        self._btn_scan = QPushButton(self.tr("Scan && Compare"))
        self._btn_scan.setProperty("primary", True)
        self._btn_scan.clicked.connect(self._run_batch)
        enc_row.addSpacing(16)
        enc_row.addWidget(self._btn_scan)
        enc_row.addStretch()
        grp_layout.addLayout(enc_row)

        root.addWidget(grp)

        # ── Results table ──────────────────────────────────────────────────────
        self._results_table = QTableWidget(0, 6)
        self._results_table.setHorizontalHeaderLabels([
            self.tr("File"),
            self.tr("Added"),
            self.tr("Removed"),
            self.tr("Modified"),
            self.tr("Unchanged"),
            self.tr("Total"),
        ])
        hdr = self._results_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, 6):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._results_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._results_table.itemDoubleClicked.connect(self._open_file_diff)
        root.addWidget(self._results_table, 1)

        self._status_lbl = QLabel("")
        root.addWidget(self._status_lbl)

        # ── Bottom buttons ─────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._export_csv_btn = QPushButton(self.tr("Export Combined CSV…"))
        self._export_csv_btn.setEnabled(False)
        self._export_csv_btn.clicked.connect(self._export_combined_csv)
        btn_row.addWidget(self._export_csv_btn)

        self._export_html_btn = QPushButton(self.tr("Export Combined HTML Report…"))
        self._export_html_btn.setEnabled(False)
        self._export_html_btn.clicked.connect(self._export_combined_html)
        btn_row.addWidget(self._export_html_btn)

        btn_row.addStretch()
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        btn_row.addWidget(btn_box)
        root.addLayout(btn_row)

    # ── Batch scan ─────────────────────────────────────────────────────────────

    @Slot()
    def _run_batch(self) -> None:
        old_dir = Path(self._edit_old_dir.text().strip())
        new_dir = Path(self._edit_new_dir.text().strip())
        trans_dir_text = self._edit_trans_dir.text().strip()
        trans_dir = Path(trans_dir_text) if trans_dir_text else None
        enc = self._combo_enc.currentText()

        if not old_dir.is_dir():
            QMessageBox.warning(
                self, self.tr("Invalid Path"),
                self.tr("Old game folder does not exist.")
            )
            return
        if not new_dir.is_dir():
            QMessageBox.warning(
                self, self.tr("Invalid Path"),
                self.tr("New game folder does not exist.")
            )
            return

        # Find matching files
        old_files: Dict[str, Path] = {}
        for pat in _GLOB_PATTERNS:
            for p in old_dir.rglob(pat):
                old_files[p.name.lower()] = p

        new_files: Dict[str, Path] = {}
        for pat in _GLOB_PATTERNS:
            for p in new_dir.rglob(pat):
                new_files[p.name.lower()] = p

        matched = sorted(set(old_files) & set(new_files))
        if not matched:
            QMessageBox.information(
                self, self.tr("No Matching Files"),
                self.tr(
                    "No matching .strings/.dlstrings/.ilstrings files found in both folders."
                )
            )
            return

        progress = QProgressDialog(
            self.tr("Comparing files…"), self.tr("Cancel"), 0, len(matched), self
        )
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        self._results.clear()
        self._results_table.setRowCount(0)
        errors = 0

        for i, name in enumerate(matched):
            progress.setValue(i)
            if progress.wasCanceled():
                break
            try:
                old_str = load_strings_file(str(old_files[name]), enc)
                new_str = load_strings_file(str(new_files[name]), enc)
                trans_str: Optional[Dict[int, str]] = None
                if trans_dir:
                    trans_path = trans_dir / old_files[name].name
                    if not trans_path.exists():
                        # Try case-insensitive search
                        for f in trans_dir.iterdir():
                            if f.name.lower() == name:
                                trans_path = f
                                break
                    if trans_path.exists():
                        try:
                            trans_str = load_strings_file(str(trans_path), enc)
                        except Exception:
                            pass
                entries = compute_version_diff(old_str, new_str, trans_str)
                summary = diff_summary(entries)
                self._results.append((name, summary, entries))
                self._add_results_row(name, summary)
            except Exception as exc:
                logger.warning("Batch diff failed for %s: %s", name, exc)
                errors += 1

        progress.setValue(len(matched))

        total_files = len(self._results)
        status = self.tr(
            "{n} file(s) compared" + (f", {errors} error(s)" if errors else "")
        ).format(n=total_files)
        self._status_lbl.setText(status)
        self._export_csv_btn.setEnabled(total_files > 0)
        self._export_html_btn.setEnabled(total_files > 0)

    def _add_results_row(self, name: str, summary: Dict[str, int]) -> None:
        row = self._results_table.rowCount()
        self._results_table.insertRow(row)

        item_name = QTableWidgetItem(name)
        item_name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._results_table.setItem(row, 0, item_name)

        col_colors = {
            "added": "#16a34a", "removed": "#dc2626",
            "modified": "#d97706", "unchanged": "#64748b",
        }
        for col, key in enumerate(["added", "removed", "modified", "unchanged"], 1):
            val = summary.get(key, 0)
            item = QTableWidgetItem(str(val))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            if val > 0 and key != "unchanged":
                from PySide6.QtGui import QBrush
                item.setForeground(QBrush(QColor(col_colors[key])))
            self._results_table.setItem(row, col, item)

        total = sum(summary.values())
        item_total = QTableWidgetItem(str(total))
        item_total.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item_total.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self._results_table.setItem(row, 5, item_total)

    # ── Double-click → per-file diff dialog ────────────────────────────────────

    @Slot()
    def _open_file_diff(self) -> None:
        row = self._results_table.currentRow()
        if row < 0 or row >= len(self._results):
            return
        name, _, entries = self._results[row]
        old_dir = self._edit_old_dir.text().strip()
        new_dir = self._edit_new_dir.text().strip()
        dlg = VersionCompareDialog(
            entries=entries,
            old_label=f"Old: {old_dir}",
            new_label=f"New: {new_dir}",
            translation_label="Existing Translation",
            parent=self,
        )
        dlg.setWindowTitle(self.tr("Version Diff — {name}").format(name=name))
        dlg.exec()

    # ── Combined exports ───────────────────────────────────────────────────────

    @Slot()
    def _export_combined_csv(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self,
            self.tr("Export Combined CSV"),
            "batch_version_diff.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not path:
            return
        try:
            import csv
            import io
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["File", "ID", "Status", "Old Source", "New Source", "Translation"])
            for name, _, entries in self._results:
                for e in entries:
                    w.writerow([
                        name,
                        f"0x{e.string_id:08X}",
                        e.status.value,
                        e.old_text,
                        e.new_text,
                        e.existing_translation,
                    ])
            Path(path).write_text(buf.getvalue(), encoding="utf-8-sig")
            logger.info("Batch CSV exported to %s", path)
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))

    @Slot()
    def _export_combined_html(self) -> None:
        from gui.file_dialog_helper import get_save_filename
        path, _ = get_save_filename(
            self,
            self.tr("Export Combined HTML Report"),
            "batch_version_diff.html",
            "HTML Files (*.html);;All Files (*)",
        )
        if not path:
            return
        try:
            from datetime import datetime
            import html as _html_mod
            now = datetime.now().strftime("%Y-%m-%d %H:%M")
            sections: List[str] = [
                f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                f"<title>Batch Version Diff — {now}</title>"
                f"<style>{_BATCH_CSS}</style>"
                f"</head><body>"
                f"<h1>Batch Game Version Diff Report</h1>"
                f'<div class="meta">Generated: {now} · {len(self._results)} file(s)</div>'
            ]
            for name, summary, entries in self._results:
                changed = sum(
                    v for k, v in summary.items() if k != "unchanged"
                )
                sections.append(
                    f'<div class="file-section">'
                    f'<h2>{_html_mod.escape(name)} '
                    f'<span class="badge-changed">{changed} changed</span></h2>'
                    + _summary_row_html(summary)
                    + to_html(entries, changed_only=True, title=name)
                      .split("<body>", 1)[-1]
                      .split("</body>")[0]
                      .replace("<h1>", '<h3 style="display:none">')  # strip inner h1
                    + "</div>"
                )
            sections.append("</body></html>")
            Path(path).write_text("".join(sections), encoding="utf-8")
            logger.info("Batch HTML report exported to %s", path)
            try:
                from PySide6.QtCore import QUrl
                from PySide6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(path).resolve())))
            except Exception:
                pass
        except OSError as exc:
            QMessageBox.critical(self, self.tr("Export Error"), str(exc))


def _summary_row_html(summary: Dict[str, int]) -> str:
    parts = " · ".join(
        f'<span style="color:{c};font-weight:700">{summary.get(k,0)} {k}</span>'
        for k, c in [
            ("added", "#16a34a"), ("removed", "#dc2626"),
            ("modified", "#d97706"), ("unchanged", "#94a3b8"),
        ]
    )
    return f'<p style="margin:4px 0 12px;font-size:13px">{parts}</p>'


_BATCH_CSS = """
body{font-family:'Segoe UI',sans-serif;background:#f8fafc;color:#1e293b;margin:0}
h1{background:#1e293b;color:#f1f5f9;padding:16px 24px;margin:0;font-size:18px}
.meta{background:#e2e8f0;padding:8px 24px;font-size:12px;color:#64748b;
      border-bottom:1px solid #cbd5e1}
.file-section{border:1px solid #cbd5e1;border-radius:8px;margin:16px 24px;
              overflow:hidden}
.file-section h2{background:#334155;color:#f1f5f9;padding:10px 16px;margin:0;
                 font-size:14px}
.badge-changed{background:#fef9c3;color:#d97706;padding:2px 8px;border-radius:4px;
               font-size:12px;font-weight:600}
.file-section p{padding:8px 16px}
.file-section table{width:calc(100% - 32px);margin:0 16px 12px;border-collapse:collapse;
                    font-size:11px}
.file-section th{background:#475569;color:#f1f5f9;padding:6px 10px;text-align:left}
.file-section td{padding:4px 8px;border-bottom:1px solid #e2e8f0;
                 font-family:'DejaVu Sans Mono',monospace;font-size:11px;
                 white-space:pre-wrap;word-break:break-word;max-width:280px}
"""
