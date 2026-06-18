"""
VMAD Script Property Analysis dialog.

Starfield records carry a ``VMAD`` subrecord (compiled Papyrus script
attachments).  String-typed *properties* inside it occasionally hold display
text worth translating, but most are script identifiers, event names, keyword
EditorIDs or resource paths that **break the mod if edited**.

This dialog mirrors xTranslator's behaviour: it parses VMAD, classifies every
embedded string, *locks* the risky ones, and only lets the translator edit the
values that look like real display text.  Editing is byte-safe — see
:func:`bethesda_strings.esp_handler.apply_vmad_translations`, which rewrites
only the edited string spans and recomputes record/GRUP sizes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from bethesda_strings.esp_handler import (
    VmadStringEntry,
    apply_vmad_translations,
    scan_vmad,
)
from bethesda_strings.vmad_handler import (
    RISK_LOCKED,
    RISK_REVIEW,
    RISK_TRANSLATABLE,
)

_RISK_LABEL = {
    RISK_TRANSLATABLE: "✓ Translatable",
    RISK_REVIEW:       "? Review",
    RISK_LOCKED:       "🔒 Locked",
}
_RISK_COLOR = {
    RISK_TRANSLATABLE: QColor("#16a34a"),
    RISK_REVIEW:       QColor("#d97706"),
    RISK_LOCKED:       QColor("#dc2626"),
}
_RISK_ROW_BG = {
    RISK_TRANSLATABLE: QColor("#f0fdf4"),
    RISK_REVIEW:       QColor("#fffbeb"),
    RISK_LOCKED:       QColor("#fef2f2"),
}
_RISK_ORDER = {RISK_TRANSLATABLE: 0, RISK_REVIEW: 1, RISK_LOCKED: 2}

_COL_RISK, _COL_FORM, _COL_TYPE, _COL_SCRIPT, _COL_PROP, _COL_ORIG, _COL_TRANS = range(7)


class VmadDialog(QDialog):
    """Analyse and safely edit VMAD (Papyrus) script-property strings."""

    def __init__(self, parent=None, initial_path: str = "", encoding: str = "utf-8") -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Script Property Analysis (VMAD)"))
        self.resize(960, 620)
        self._entries: list[VmadStringEntry] = []
        self._build_ui(initial_path, encoding)
        if initial_path:
            self._run()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self, initial_path: str, encoding: str) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # File picker + encoding row
        file_row = QHBoxLayout()
        self._path_edit = QLineEdit(initial_path)
        self._path_edit.setPlaceholderText(self.tr("Path to .esp / .esm / .esl plugin file…"))
        self._path_edit.returnPressed.connect(self._run)
        file_row.addWidget(self._path_edit, 1)

        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(browse_btn)

        file_row.addWidget(QLabel(self.tr("Encoding:")))
        self._enc_combo = QComboBox()
        self._enc_combo.addItems(["utf-8", "cp1252", "cp1251"])
        if self._enc_combo.findText(encoding) < 0:
            self._enc_combo.insertItem(0, encoding)
        self._enc_combo.setCurrentText(encoding)
        self._enc_combo.currentTextChanged.connect(lambda _t: self._run())
        file_row.addWidget(self._enc_combo)

        self._run_btn = QPushButton(self.tr("Scan"))
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._run)
        file_row.addWidget(self._run_btn)
        root.addLayout(file_row)

        # Summary bar
        self._summary_label = QLabel("")
        font = QFont()
        font.setBold(True)
        self._summary_label.setFont(font)
        self._summary_label.setVisible(False)
        root.addWidget(self._summary_label)

        # Filter / safety toggles
        toggle_row = QHBoxLayout()
        self._hide_locked = QCheckBox(self.tr("Hide locked properties"))
        self._hide_locked.setChecked(False)
        self._hide_locked.toggled.connect(self._repopulate)
        toggle_row.addWidget(self._hide_locked)

        self._allow_locked = QCheckBox(self.tr("Allow editing locked properties (advanced)"))
        self._allow_locked.setToolTip(self.tr(
            "Locked properties are script identifiers, event names or resource\n"
            "paths. Editing them almost always breaks the mod. Only enable this\n"
            "if you are certain a locked value is shown to the player."
        ))
        self._allow_locked.toggled.connect(self._repopulate)
        toggle_row.addWidget(self._allow_locked)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        # Splitter: table on top, detail below
        splitter = QSplitter(Qt.Orientation.Vertical)

        table_box = QGroupBox(self.tr("Script properties"))
        table_layout = QVBoxLayout(table_box)
        table_layout.setContentsMargins(4, 4, 4, 4)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            self.tr("Risk"), self.tr("FormID"), self.tr("Type"),
            self.tr("Script"), self.tr("Property"),
            self.tr("Original"), self.tr("Translation"),
        ])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(False)
        self._table.currentItemChanged.connect(self._on_row_changed)
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_ORIG, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(_COL_TRANS, QHeaderView.ResizeMode.Stretch)
        table_layout.addWidget(self._table)
        splitter.addWidget(table_box)

        detail_box = QGroupBox(self.tr("Why this classification"))
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        detail_layout.addWidget(self._detail)
        splitter.addWidget(detail_box)

        splitter.setSizes([460, 120])
        root.addWidget(splitter, 1)

        # Action buttons
        btn_row = QHBoxLayout()
        self._apply_btn = QPushButton(self.tr("Apply && Save"))
        self._apply_btn.setToolTip(self.tr(
            "Write edited values back into the plugin. A backup (.bak) is saved\n"
            "first; only edited string spans are rewritten — everything else is\n"
            "preserved byte-for-byte."
        ))
        self._apply_btn.clicked.connect(self._apply)
        self._apply_btn.setEnabled(False)
        btn_row.addWidget(self._apply_btn)
        btn_row.addStretch()
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select plugin file"),
            self._path_edit.text() or "",
            self.tr("Plugin files (*.esp *.esm *.esl);;All files (*)"),
        )
        if path:
            self._path_edit.setText(path)
            self._run()

    def _run(self) -> None:
        path_str = self._path_edit.text().strip()
        if not path_str:
            return
        path = Path(path_str)
        if not path.exists():
            self._show_error(self.tr("File not found: ") + path_str)
            return
        try:
            result = scan_vmad(path, self._enc_combo.currentText())
        except Exception as exc:
            self._show_error(self.tr("Error reading file: ") + str(exc))
            return

        self._entries = sorted(
            result.entries,
            key=lambda e: (_RISK_ORDER.get(e.risk, 9), e.form_id, e.vmad_index),
        )

        parts = [
            f"{result.translatable_count} translatable",
            f"{result.review_count} to review",
            f"{result.locked_count} locked",
        ]
        warn = ""
        if result.parse_warnings:
            warn = self.tr("  —  ⚠ {n} record(s) had VMAD data this tool could not "
                           "fully parse (their later strings are hidden for safety)").format(
                n=result.parse_warnings)
        summary = (
            f"{result.plugin_name}  —  {len(result.entries)} script string(s) in "
            f"{result.records_with_vmad} record(s)  —  " + ", ".join(parts) + warn
        )
        self._summary_label.setText(summary)
        self._summary_label.setStyleSheet(
            "color: #16a34a;" if result.translatable_count else "color: #6b7280;"
        )
        self._summary_label.setVisible(True)
        self._apply_btn.setEnabled(bool(result.entries))
        self._repopulate()

    def _repopulate(self) -> None:
        hide_locked = self._hide_locked.isChecked()
        allow_locked = self._allow_locked.isChecked()

        self._table.blockSignals(True)
        self._table.setRowCount(0)
        for entry in self._entries:
            if hide_locked and entry.risk == RISK_LOCKED:
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            bg = _RISK_ROW_BG[entry.risk]

            risk_item = QTableWidgetItem(_RISK_LABEL[entry.risk])
            risk_item.setForeground(_RISK_COLOR[entry.risk])
            risk_item.setData(Qt.ItemDataRole.UserRole, entry)

            ptype = ("String[%d]" % entry.array_index) if entry.array_index >= 0 else "String"
            cells = {
                _COL_RISK:  risk_item,
                _COL_FORM:  QTableWidgetItem(f"{entry.form_id:08X}"),
                _COL_TYPE:  QTableWidgetItem(ptype),
                _COL_SCRIPT: QTableWidgetItem(entry.script_name),
                _COL_PROP:  QTableWidgetItem(entry.prop_name),
                _COL_ORIG:  QTableWidgetItem(entry.original),
                _COL_TRANS: QTableWidgetItem(entry.translation or ""),
            }
            editable = entry.risk != RISK_LOCKED or allow_locked
            for col, item in cells.items():
                item.setBackground(bg)
                if col == _COL_TRANS and editable:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                else:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, col, item)

        self._table.blockSignals(False)
        self._table.resizeColumnToContents(_COL_RISK)
        self._table.resizeColumnToContents(_COL_FORM)
        self._table.resizeColumnToContents(_COL_TYPE)

    def _on_row_changed(self, current, previous) -> None:  # noqa: ARG002
        if current is None:
            self._detail.clear()
            return
        risk_item = self._table.item(current.row(), _COL_RISK)
        if risk_item is None:
            return
        entry: VmadStringEntry = risk_item.data(Qt.ItemDataRole.UserRole)
        if not entry:
            self._detail.clear()
            return
        lines = [
            f"{_RISK_LABEL.get(entry.risk, entry.risk)}",
            "",
            entry.reason,
            "",
            f"Script:   {entry.script_name}",
            f"Property: {entry.prop_name}",
            f"Record:   {entry.record_sig}  ({entry.form_id:08X})"
            + (f"  EDID: {entry.edid}" if entry.edid else ""),
        ]
        if entry.risk == RISK_LOCKED:
            lines += [
                "",
                "This value is locked because editing it would most likely break "
                "the script binding (it is an identifier, event name or resource "
                "path — not text the player reads). Enable “Allow editing locked "
                "properties” only if you are sure it is display text.",
            ]
        self._detail.setPlainText("\n".join(lines))

    def _collect_edits(self) -> dict[tuple[int, int], str]:
        edits: dict[tuple[int, int], str] = {}
        for row in range(self._table.rowCount()):
            risk_item = self._table.item(row, _COL_RISK)
            trans_item = self._table.item(row, _COL_TRANS)
            if risk_item is None or trans_item is None:
                continue
            entry: VmadStringEntry = risk_item.data(Qt.ItemDataRole.UserRole)
            if not entry:
                continue
            new_text = trans_item.text()
            if new_text and new_text != entry.original:
                edits[(entry.form_id, entry.vmad_index)] = new_text
        return edits

    def _apply(self) -> None:
        path_str = self._path_edit.text().strip()
        if not path_str:
            return
        path = Path(path_str)
        edits = self._collect_edits()
        if not edits:
            QMessageBox.information(
                self, self.tr("Nothing to Save"),
                self.tr("No edited values to write. Edit a Translation cell first."),
            )
            return

        answer = QMessageBox.question(
            self, self.tr("Apply VMAD Edits"),
            self.tr(
                "Write {n} edited script-property string(s) back into:\n\n{path}\n\n"
                "A backup will be saved as {bak}.\n"
                "Only the edited values are rewritten — all other bytes are preserved.\n\n"
                "Continue?"
            ).format(n=len(edits), path=str(path), bak=path.name + ".bak"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            backup = apply_vmad_translations(
                path, edits, encoding=self._enc_combo.currentText()
            )
        except Exception as exc:
            QMessageBox.critical(
                self, self.tr("Save Failed"),
                self.tr("Could not write changes:\n{error}").format(error=str(exc)),
            )
            return

        QMessageBox.information(
            self, self.tr("Saved"),
            self.tr("Wrote {n} edited string(s).\n\nBackup: {bak}").format(
                n=len(edits), bak=str(backup) if backup else "—"),
        )
        self._run()  # re-scan to reflect the saved state

    def _show_error(self, msg: str) -> None:
        self._summary_label.setText(msg)
        self._summary_label.setStyleSheet("color: #dc2626;")
        self._summary_label.setVisible(True)
        self._table.setRowCount(0)
        self._apply_btn.setEnabled(False)
