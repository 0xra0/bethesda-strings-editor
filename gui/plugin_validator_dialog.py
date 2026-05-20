"""
Plugin Validator Dialog.

Scans an ESP/ESM file for issues that cause NPC dialogue camera bugs:
missing Localized flag, stray DIAL/SCEN/INFO records, ONAM overrides,
and missing master dependencies.

Checks are derived from xEdit (TES5Edit) wbDefinitionsSF1 + wbImplementation.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from bethesda_strings.esp_handler import (
    SEVERITY_ERROR,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    PluginValidationResult,
    ValidationIssue,
    _DANGEROUS_RECORD_SIGS,
    patch_localized_flag,
    validate_plugin,
)

_SEV_LABEL = {
    SEVERITY_ERROR:   "✗ Error",
    SEVERITY_WARNING: "⚠ Warning",
    SEVERITY_INFO:    "ℹ Info",
}
_SEV_COLOR = {
    SEVERITY_ERROR:   QColor("#dc2626"),
    SEVERITY_WARNING: QColor("#d97706"),
    SEVERITY_INFO:    QColor("#2563eb"),
}
_SEV_ROW_BG = {
    SEVERITY_ERROR:   QColor("#fff1f2"),
    SEVERITY_WARNING: QColor("#fffbeb"),
    SEVERITY_INFO:    QColor("#eff6ff"),
}


class PluginValidatorDialog(QDialog):
    def __init__(self, parent=None, initial_path: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Plugin Validator (xEdit checks)"))
        self.resize(820, 560)
        self._result: PluginValidationResult | None = None
        self._build_ui(initial_path)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self, initial_path: str) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # File picker row
        file_row = QHBoxLayout()
        self._path_edit = QLineEdit(initial_path)
        self._path_edit.setPlaceholderText(self.tr("Path to .esp / .esm plugin file…"))
        self._path_edit.returnPressed.connect(self._run)
        file_row.addWidget(self._path_edit, 1)

        browse_btn = QPushButton(self.tr("Browse…"))
        browse_btn.clicked.connect(self._browse)
        file_row.addWidget(browse_btn)

        self._run_btn = QPushButton(self.tr("Validate"))
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

        # Splitter: table on top, detail below
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Issues table
        issues_box = QGroupBox(self.tr("Issues"))
        issues_layout = QVBoxLayout(issues_box)
        issues_layout.setContentsMargins(4, 4, 4, 4)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels([
            self.tr("Severity"), self.tr("Code"), self.tr("Message"),
        ])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(False)
        self._table.currentItemChanged.connect(self._on_row_changed)
        issues_layout.addWidget(self._table)
        splitter.addWidget(issues_box)

        # Detail panel
        detail_box = QGroupBox(self.tr("Detail"))
        detail_layout = QVBoxLayout(detail_box)
        detail_layout.setContentsMargins(4, 4, 4, 4)
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        detail_layout.addWidget(self._detail)
        splitter.addWidget(detail_box)

        splitter.setSizes([340, 160])
        root.addWidget(splitter, 1)

        # Record counts group (hidden until validated)
        self._counts_box = QGroupBox(self.tr("Record type counts"))
        counts_layout = QVBoxLayout(self._counts_box)
        counts_layout.setContentsMargins(4, 4, 4, 4)
        self._counts_label = QLabel("")
        self._counts_label.setWordWrap(True)
        self._counts_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        counts_layout.addWidget(self._counts_label)
        self._counts_box.setVisible(False)
        root.addWidget(self._counts_box)

        # Fix / Close buttons
        btn_row = QHBoxLayout()
        self._fix_btn = QPushButton(self.tr("Fix Localized Flag"))
        self._fix_btn.setToolTip(self.tr(
            "Set the Localized flag (bit 7 / 0x080) in the TES4 header.\n"
            "A backup is saved as <filename>.bak before patching."
        ))
        self._fix_btn.setVisible(False)
        self._fix_btn.clicked.connect(self._fix_localized_flag)
        btn_row.addWidget(self._fix_btn)
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
            self._summary_label.setText(self.tr("File not found: ") + path_str)
            self._summary_label.setStyleSheet("color: #dc2626;")
            self._summary_label.setVisible(True)
            return

        try:
            self._result = validate_plugin(path)
        except Exception as exc:
            self._summary_label.setText(self.tr("Error reading file: ") + str(exc))
            self._summary_label.setStyleSheet("color: #dc2626;")
            self._summary_label.setVisible(True)
            return

        self._populate(self._result)

    def _populate(self, result: PluginValidationResult) -> None:
        # Summary
        errors   = sum(1 for i in result.issues if i.severity == SEVERITY_ERROR)
        warnings = sum(1 for i in result.issues if i.severity == SEVERITY_WARNING)
        infos    = sum(1 for i in result.issues if i.severity == SEVERITY_INFO)

        parts = []
        if errors:
            parts.append(f"{errors} error(s)")
        if warnings:
            parts.append(f"{warnings} warning(s)")
        if infos:
            parts.append(f"{infos} info")

        localized_str = (
            "Localized ✓" if result.is_localized else "Localized ✗"
        )
        summary = f"{result.plugin_name}  —  {localized_str}  —  " + (
            ", ".join(parts) if parts else "No issues found"
        )
        self._summary_label.setText(summary)
        color = "#dc2626" if errors else ("#d97706" if warnings else "#16a34a")
        self._summary_label.setStyleSheet(f"color: {color};")
        self._summary_label.setVisible(True)

        # Show Fix button only when the Localized flag is missing
        has_flag_error = any(i.code == "NO_LOCALIZED_FLAG" for i in result.issues)
        self._fix_btn.setVisible(has_flag_error)

        # Issues table
        self._table.setRowCount(0)
        for issue in result.issues:
            row = self._table.rowCount()
            self._table.insertRow(row)

            sev_item = QTableWidgetItem(_SEV_LABEL[issue.severity])
            sev_item.setForeground(_SEV_COLOR[issue.severity])
            sev_item.setData(Qt.ItemDataRole.UserRole, issue)
            bg = _SEV_ROW_BG[issue.severity]
            sev_item.setBackground(bg)

            code_item = QTableWidgetItem(issue.code)
            code_item.setBackground(bg)

            msg_item = QTableWidgetItem(issue.message)
            msg_item.setBackground(bg)

            self._table.setItem(row, 0, sev_item)
            self._table.setItem(row, 1, code_item)
            self._table.setItem(row, 2, msg_item)

        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(1)

        if self._table.rowCount() > 0:
            self._table.selectRow(0)

        # Record counts
        if result.record_counts:
            dangerous = {s.decode() for s in _DANGEROUS_RECORD_SIGS}
            parts_c = []
            for sig, count in sorted(result.record_counts.items(),
                                     key=lambda x: -x[1]):
                mark = " ⚠" if sig in dangerous else ""
                parts_c.append(f"{sig}: {count}{mark}")
            self._counts_label.setText("  |  ".join(parts_c))
            self._counts_box.setVisible(True)
        else:
            self._counts_box.setVisible(False)

    def _on_row_changed(self, current, previous) -> None:  # noqa: ARG002
        if current is None:
            self._detail.clear()
            return
        row = current.row()
        sev_item = self._table.item(row, 0)
        if sev_item is None:
            return
        issue: ValidationIssue = sev_item.data(Qt.ItemDataRole.UserRole)
        if issue and issue.detail:
            self._detail.setPlainText(issue.detail)
        else:
            self._detail.clear()

    def _fix_localized_flag(self) -> None:
        """Patch the Localized flag into the TES4 header of the current plugin."""
        path_str = self._path_edit.text().strip()
        if not path_str:
            return
        path = Path(path_str)

        answer = QMessageBox.question(
            self,
            self.tr("Fix Localized Flag"),
            self.tr(
                "This will modify the plugin file in-place:\n\n"
                "{path}\n\n"
                "A backup will be saved as:\n"
                "{bak}\n\n"
                "Set the Localized flag and continue?"
            ).format(path=str(path), bak=str(path) + ".bak"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            backup = patch_localized_flag(path)
        except Exception as exc:
            QMessageBox.critical(
                self,
                self.tr("Patch Failed"),
                self.tr("Could not patch file:\n{error}").format(error=str(exc)),
            )
            return

        QMessageBox.information(
            self,
            self.tr("Flag Set"),
            self.tr(
                "Localized flag set successfully.\n\n"
                "Backup saved to:\n{bak}"
            ).format(bak=str(backup)),
        )

        # Re-validate to show the updated result
        self._run()
