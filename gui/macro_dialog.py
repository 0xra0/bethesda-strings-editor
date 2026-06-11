"""
Macro editor dialog for defining and running batch string operations.

Open via Ctrl+M or 'q' key in the string table (not while editing a cell).
"""
from __future__ import annotations

import re
from typing import List, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from gui.macro_recorder import MacroRecorder, MacroStep, MacroStepType


class _RegexReplaceDialog(QDialog):
    """Small dialog for defining or editing one regex-replace step."""

    def __init__(self, parent=None, step: Optional[MacroStep] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            self.tr("Edit Regex Replace") if step else self.tr("Add Regex Replace")
        )
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(self.tr("Pattern (Python regex):")))
        self._pattern = QLineEdit()
        self._pattern.setPlaceholderText(self.tr("e.g.  \\s+$  or  (?<=\\w)\\s{2,}(?=\\w)"))
        if step:
            self._pattern.setText(step.args.get("pattern", ""))
        layout.addWidget(self._pattern)

        layout.addWidget(QLabel(self.tr("Replacement (leave blank to delete matches):")))
        self._replacement = QLineEdit()
        self._replacement.setPlaceholderText(self.tr("e.g.  ' '  or  \\1  (empty → delete)"))
        if step:
            self._replacement.setText(step.args.get("replacement", ""))
        layout.addWidget(self._replacement)

        field_row = QHBoxLayout()
        field_row.addWidget(QLabel(self.tr("Field:")))
        self._field = QComboBox()
        self._field.addItems([self.tr("Translated"), self.tr("Original")])
        if step:
            self._field.setCurrentText(step.args.get("field", "translated").capitalize())
        field_row.addWidget(self._field)
        field_row.addSpacing(16)
        self._ignore_case = QCheckBox(self.tr("Ignore case"))
        if step:
            self._ignore_case.setChecked(step.args.get("ignore_case", False))
        field_row.addWidget(self._ignore_case)
        field_row.addStretch()
        layout.addLayout(field_row)

        self._status = QLabel()
        layout.addWidget(self._status)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        self._pattern.textChanged.connect(self._validate)
        self._validate(self._pattern.text())

    @Slot(str)
    def _validate(self, text: str) -> None:
        if not text:
            self._status.setText("")
            return
        try:
            re.compile(text)
            self._status.setText(self.tr("✓ Valid pattern"))
            self._status.setStyleSheet("color: green;")
        except re.error as e:
            self._status.setText(self.tr("✗ {e}").format(e=e))
            self._status.setStyleSheet("color: red;")

    @Slot()
    def _on_accept(self) -> None:
        if not self._pattern.text().strip():
            QMessageBox.warning(self, self.tr("No Pattern"), self.tr("Pattern cannot be empty."))
            return
        try:
            re.compile(self._pattern.text().strip())
        except re.error as e:
            QMessageBox.critical(self, self.tr("Invalid Pattern"), str(e))
            return
        self.accept()

    def get_step(self) -> MacroStep:
        return MacroStep(
            step_type=MacroStepType.REGEX_REPLACE,
            args={
                "pattern": self._pattern.text().strip(),
                "replacement": self._replacement.text(),
                "field": self._field.currentText().lower(),
                "ignore_case": self._ignore_case.isChecked(),
            },
        )


class MacroDialog(QDialog):
    """
    Editor for building a sequence of text-processing steps and running them
    against rows in the string table.

    Usage:
        dlg = MacroDialog(recorder, model, selected_rows, parent)
        dlg.exec()
    """

    def __init__(
        self,
        recorder: MacroRecorder,
        model,
        selected_rows: Optional[List[int]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._recorder = recorder
        self._model = model
        self._selected_rows = selected_rows or []
        self.setWindowTitle(self.tr("Macro Editor"))
        self.setMinimumSize(580, 440)
        self._build_ui()
        self._refresh_list()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ── Steps list + side buttons ─────────────────────────────────────────
        list_row = QHBoxLayout()

        left = QVBoxLayout()
        left.addWidget(QLabel(self.tr("Steps (applied top to bottom to each row):")))
        self._step_list = QListWidget()
        self._step_list.setAlternatingRowColors(True)
        self._step_list.currentRowChanged.connect(self._on_sel_changed)
        self._step_list.itemDoubleClicked.connect(lambda _: self._edit_step())
        left.addWidget(self._step_list)
        list_row.addLayout(left, stretch=1)

        btn_col = QVBoxLayout()
        btn_col.setAlignment(Qt.AlignTop)

        self._btn_add_replace = QPushButton(self.tr("Add Regex Replace…"))
        self._btn_add_replace.clicked.connect(self._add_replace)
        btn_col.addWidget(self._btn_add_replace)

        self._btn_add_approve = QPushButton(self.tr("Add Approve"))
        self._btn_add_approve.setToolTip(self.tr("Mark each matched row as translated"))
        self._btn_add_approve.clicked.connect(
            lambda: self._add_status_step("translated")
        )
        btn_col.addWidget(self._btn_add_approve)

        self._btn_add_reject = QPushButton(self.tr("Add Reject"))
        self._btn_add_reject.setToolTip(
            self.tr("Clear translation and mark each row as pending")
        )
        self._btn_add_reject.clicked.connect(
            lambda: self._add_status_step("pending")
        )
        btn_col.addWidget(self._btn_add_reject)

        btn_col.addSpacing(12)

        self._btn_edit = QPushButton(self.tr("Edit…"))
        self._btn_edit.clicked.connect(self._edit_step)
        btn_col.addWidget(self._btn_edit)

        self._btn_remove = QPushButton(self.tr("Remove"))
        self._btn_remove.clicked.connect(self._remove_step)
        btn_col.addWidget(self._btn_remove)

        self._btn_up = QPushButton(self.tr("▲ Up"))
        self._btn_up.clicked.connect(self._move_up)
        btn_col.addWidget(self._btn_up)

        self._btn_down = QPushButton(self.tr("▼ Down"))
        self._btn_down.clicked.connect(self._move_down)
        btn_col.addWidget(self._btn_down)

        btn_col.addSpacing(12)
        self._btn_clear = QPushButton(self.tr("Clear All"))
        self._btn_clear.clicked.connect(self._clear_all)
        btn_col.addWidget(self._btn_clear)

        list_row.addLayout(btn_col)
        root.addLayout(list_row)

        # ── Scope + preview ───────────────────────────────────────────────────
        scope_grp = QGroupBox(self.tr("Apply to"))
        scope_layout = QVBoxLayout(scope_grp)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel(self.tr("Scope:")))
        self._scope = QComboBox()
        self._scope.addItems([
            self.tr("All rows"),
            self.tr("Translated rows only"),
            self.tr("Pending rows only"),
            self.tr("Selected rows ({n})").format(n=len(self._selected_rows)),
        ])
        if not self._selected_rows:
            self._scope.model().item(3).setEnabled(False)
        self._scope.currentIndexChanged.connect(self._update_preview)
        scope_row.addWidget(self._scope)
        scope_row.addStretch()
        scope_layout.addLayout(scope_row)
        self._preview_lbl = QLabel()
        scope_layout.addWidget(self._preview_lbl)
        root.addWidget(scope_grp)

        # ── Bottom buttons ────────────────────────────────────────────────────
        bottom = QHBoxLayout()
        self._btn_play = QPushButton(self.tr("▶  Play"))
        self._btn_play.setDefault(True)
        self._btn_play.clicked.connect(self._play)
        bottom.addWidget(self._btn_play)
        bottom.addStretch()
        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.reject)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

    # ── List helpers ──────────────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        self._step_list.clear()
        for step in self._recorder.steps:
            self._step_list.addItem(QListWidgetItem(step.description()))
        self._update_preview()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        steps = self._recorder.steps
        sel = self._step_list.currentRow()
        n = len(steps)
        has = n > 0
        self._btn_play.setEnabled(has and self._model is not None)
        self._btn_clear.setEnabled(has)
        self._btn_remove.setEnabled(0 <= sel < n)
        self._btn_up.setEnabled(sel > 0)
        self._btn_down.setEnabled(0 <= sel < n - 1)
        self._btn_edit.setEnabled(
            0 <= sel < n
            and steps[sel].step_type == MacroStepType.REGEX_REPLACE
        )

    @Slot(int)
    def _on_sel_changed(self, _: int) -> None:
        self._sync_buttons()

    # ── Step add / edit / remove ──────────────────────────────────────────────

    @Slot()
    def _add_replace(self) -> None:
        dlg = _RegexReplaceDialog(self)
        if dlg.exec() == QDialog.Accepted:
            steps = self._recorder.steps
            steps.append(dlg.get_step())
            self._recorder.set_steps(steps)
            self._refresh_list()
            self._step_list.setCurrentRow(self._step_list.count() - 1)

    def _add_status_step(self, status: str) -> None:
        steps = self._recorder.steps
        steps.append(MacroStep(MacroStepType.SET_STATUS, {"status": status}))
        self._recorder.set_steps(steps)
        self._refresh_list()

    @Slot()
    def _edit_step(self) -> None:
        sel = self._step_list.currentRow()
        steps = self._recorder.steps
        if not (0 <= sel < len(steps)):
            return
        step = steps[sel]
        if step.step_type != MacroStepType.REGEX_REPLACE:
            return
        dlg = _RegexReplaceDialog(self, step=step)
        if dlg.exec() == QDialog.Accepted:
            steps[sel] = dlg.get_step()
            self._recorder.set_steps(steps)
            self._refresh_list()
            self._step_list.setCurrentRow(sel)

    @Slot()
    def _remove_step(self) -> None:
        sel = self._step_list.currentRow()
        steps = self._recorder.steps
        if 0 <= sel < len(steps):
            steps.pop(sel)
            self._recorder.set_steps(steps)
            self._refresh_list()
            self._step_list.setCurrentRow(min(sel, self._step_list.count() - 1))

    @Slot()
    def _move_up(self) -> None:
        sel = self._step_list.currentRow()
        steps = self._recorder.steps
        if sel > 0:
            steps[sel - 1], steps[sel] = steps[sel], steps[sel - 1]
            self._recorder.set_steps(steps)
            self._refresh_list()
            self._step_list.setCurrentRow(sel - 1)

    @Slot()
    def _move_down(self) -> None:
        sel = self._step_list.currentRow()
        steps = self._recorder.steps
        if 0 <= sel < len(steps) - 1:
            steps[sel], steps[sel + 1] = steps[sel + 1], steps[sel]
            self._recorder.set_steps(steps)
            self._refresh_list()
            self._step_list.setCurrentRow(sel + 1)

    @Slot()
    def _clear_all(self) -> None:
        self._recorder.clear()
        self._refresh_list()

    # ── Preview ───────────────────────────────────────────────────────────────

    @Slot()
    def _update_preview(self) -> None:
        if not self._recorder.steps or self._model is None:
            self._preview_lbl.setText(self.tr("Define at least one step, then click Play."))
            return
        rows = self._target_rows()
        matches = self._recorder.count_matches(self._model, rows)
        self._preview_lbl.setText(
            self.tr("{m} of {total} rows would be modified.").format(
                m=matches, total=len(rows)
            )
        )

    def _target_rows(self) -> List[int]:
        if self._model is None:
            return []
        total = self._model.rowCount()
        scope = self._scope.currentIndex()
        if scope == 0:
            return list(range(total))
        if scope == 1:
            return [
                i for i in range(total)
                if self._model._data[i].get("status") == "translated"
            ]
        if scope == 2:
            return [
                i for i in range(total)
                if self._model._data[i].get("status") != "translated"
            ]
        return list(self._selected_rows)

    # ── Play ─────────────────────────────────────────────────────────────────

    @Slot()
    def _play(self) -> None:
        if not self._recorder.steps or self._model is None:
            return

        for step in self._recorder.steps:
            if step.step_type == MacroStepType.REGEX_REPLACE:
                try:
                    re.compile(step.args.get("pattern", ""))
                except re.error as e:
                    QMessageBox.critical(
                        self,
                        self.tr("Invalid Pattern"),
                        self.tr("Step '{s}' has an invalid regex:\n{e}").format(
                            s=step.description(), e=e
                        ),
                    )
                    return

        rows = self._target_rows()
        if not rows:
            QMessageBox.information(
                self,
                self.tr("Nothing to Do"),
                self.tr("No rows match the selected scope."),
            )
            return

        progress = QProgressDialog(
            self.tr("Applying macro…"), self.tr("Cancel"), 0, len(rows), self
        )
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(300)

        def _progress(done: int, _: int) -> None:
            progress.setValue(done)
            QApplication.processEvents()

        def _should_stop() -> bool:
            return progress.wasCanceled()

        modified = self._recorder.replay_on_rows(
            self._model, rows, _progress, _should_stop
        )

        # Single batch repaint
        self._model.layoutChanged.emit()

        if progress.wasCanceled():
            QMessageBox.information(
                self,
                self.tr("Cancelled"),
                self.tr("Macro cancelled. {n} row(s) modified before stopping.").format(
                    n=modified
                ),
            )
        else:
            QMessageBox.information(
                self,
                self.tr("Done"),
                self.tr("Macro applied: {n} of {total} row(s) modified.").format(
                    n=modified, total=len(rows)
                ),
            )

        self._update_preview()
