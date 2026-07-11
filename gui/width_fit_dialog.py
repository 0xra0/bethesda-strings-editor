"""UI Width-Fit Simulator dialog.

The Font & Glyph Checker asks "can the game *draw* this character?".  This asks
the next question: "does the drawn label *fit its box*?".  Ukrainian, German and
Polish routinely run 15–30 % longer than English, and Bethesda's Scaleform
widgets clip rather than shrink — so a perfectly-spelled translation still ships
as a truncated button.

Flow:
  1. Pick a font source (defaults to the real Starfield faces bundled in
     ``data/fonts/``; a game SWF or TTF can be loaded to override).
  2. Optionally correct the per-widget pixel budgets — these are *estimates*, and
     the dialog says so.  The measured width is exact; the box it is compared
     against is the modelled part.
  3. Scan → every length-critical string is measured and overflow is flagged,
     worst first, with a Jump button back to the table.

See ``bethesda_strings.width_fit`` for the metric extraction and the fit engine.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bethesda_strings import format_string_id
from bethesda_strings.width_fit import (
    ROLE_BODY,
    ROLE_BOLD,
    ROLE_LATIN,
    WIDGETS,
    Confidence,
    FitResult,
    FontMetrics,
    WidgetSpec,
    WidthCheckResult,
    load_bundled_metrics,
    load_font_file,
    metrics_from_sources,
    scan_rows,
)

logger = logging.getLogger(__name__)

# Fill-ratio thresholds for the colour scale.
_WARN_FILL = 0.90     # within 10 % of the edge — tight, but not yet broken


class WidthFitDialog(QDialog):
    """Modal dialog for the UI width-fit simulation.

    Emits:
      jump_to_row(row_index) — navigate the main table to this row
    """

    jump_to_row = Signal(int)

    def __init__(self, rows: List[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._result: Optional[WidthCheckResult] = None

        # Role → metrics.  Defaults to the bundled Starfield faces, so the tool
        # works with zero configuration and still uses genuine game metrics.
        self._metrics: Dict[str, FontMetrics] = load_bundled_metrics()
        self._custom_font: Optional[str] = None

        self.setWindowTitle(self.tr("UI Width-Fit Simulator"))
        self.setWindowIcon(QIcon.fromTheme("format-justify-fill"))
        self.resize(1000, 660)
        self.setModal(True)

        self._build_ui()
        self._refresh_font_label()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        root.addWidget(self._build_font_group())
        root.addWidget(self._build_budget_group())

        # ── Scan row ───────────────────────────────────────────────────────
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton(self.tr("Simulate Fit"))
        self._scan_btn.setIcon(QIcon.fromTheme("system-search"))
        self._scan_btn.clicked.connect(self._run_scan)
        scan_row.addWidget(self._scan_btn)

        scan_row.addWidget(QLabel(self.tr("Widget:")))
        self._widget_combo = QComboBox()
        self._widget_combo.setToolTip(self.tr(
            "Auto-detect guesses each string's widget from its shape.\n"
            "Pick a specific widget to test every label against that one box."
        ))
        self._widget_combo.addItem(self.tr("Auto-detect"), None)
        for spec in WIDGETS.values():
            self._widget_combo.addItem(spec.label, spec.key)
        scan_row.addWidget(self._widget_combo)

        self._tight_chk = QCheckBox(self.tr("Also list tight fits (>90%)"))
        self._tight_chk.setToolTip(self.tr(
            "Include strings that fit but come within 10% of the edge — these\n"
            "break first when the budget estimate is slightly off."
        ))
        self._tight_chk.toggled.connect(self._on_tight_toggled)
        scan_row.addWidget(self._tight_chk)

        self._summary_label = QLabel("")
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scan_row.addWidget(self._summary_label, 1)
        root.addLayout(scan_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # ── Results ────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 8)
        self._table.setHorizontalHeaderLabels([
            self.tr("ID"), self.tr("Widget"), self.tr("Source"),
            self.tr("Translation"), self.tr("Width"), self.tr("Budget"),
            self.tr("Fill"), self.tr("vs Source"),
        ])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(self._on_row_activated)
        root.addWidget(self._table, 1)

        # ── Bottom buttons ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._jump_btn = QPushButton(self.tr("Jump to String"))
        self._jump_btn.setIcon(QIcon.fromTheme("go-jump"))
        self._jump_btn.setEnabled(False)
        self._jump_btn.clicked.connect(self._on_row_activated)
        btn_row.addWidget(self._jump_btn)

        self._export_btn = QPushButton(self.tr("Export CSV…"))
        self._export_btn.setIcon(QIcon.fromTheme("document-save"))
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_csv)
        btn_row.addWidget(self._export_btn)

        btn_row.addStretch(1)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _build_font_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Font Metrics"))
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        row = QHBoxLayout()
        row.addWidget(QLabel(self.tr("Font file:")))
        self._font_edit = QLineEdit()
        self._font_edit.setReadOnly(True)
        self._font_edit.setPlaceholderText(
            self.tr("Optional — override with a game SWF atlas or TTF/OTF")
        )
        row.addWidget(self._font_edit, 1)

        browse = QToolButton()
        browse.setText("…")
        browse.setToolTip(self.tr("Load advance widths from a SWF or TTF font"))
        browse.clicked.connect(self._browse_font)
        row.addWidget(browse)

        reset = QToolButton()
        reset.setText(self.tr("Reset"))
        reset.setToolTip(self.tr("Go back to the bundled Starfield fonts"))
        reset.clicked.connect(self._reset_font)
        row.addWidget(reset)
        lay.addLayout(row)

        self._font_label = QLabel("")
        self._font_label.setWordWrap(True)
        self._font_label.setStyleSheet("color: #888;")
        lay.addWidget(self._font_label)
        return box

    def _build_budget_group(self) -> QGroupBox:
        box = QGroupBox(self.tr("Widget Budgets (editable)"))
        lay = QVBoxLayout(box)
        lay.setSpacing(4)

        caveat = QLabel(self.tr(
            "Measured text width is exact — it comes from the font's own advance "
            "widths. The budgets below are estimates for a 1920×1080 stage; correct "
            "them if you know a widget's real width. The “vs Source” column does not "
            "depend on them at all."
        ))
        caveat.setWordWrap(True)
        caveat.setStyleSheet("color: #888;")
        lay.addWidget(caveat)

        self._budget_table = QTableWidget(len(WIDGETS), 4)
        self._budget_table.setHorizontalHeaderLabels([
            self.tr("Widget"), self.tr("Text width (px)"),
            self.tr("Font size (px)"), self.tr("Notes"),
        ])
        bh = self._budget_table.horizontalHeader()
        bh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        bh.setStretchLastSection(True)
        self._budget_table.verticalHeader().setVisible(False)
        self._budget_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._budget_table.setMaximumHeight(190)

        self._budget_spins: Dict[str, QDoubleSpinBox] = {}
        self._font_spins: Dict[str, QDoubleSpinBox] = {}

        for i, spec in enumerate(WIDGETS.values()):
            name = QTableWidgetItem(spec.label)
            if spec.uppercase:
                name.setToolTip(self.tr("This widget draws its text in CAPS."))
                name.setText(f"{spec.label}  (CAPS)")
            self._budget_table.setItem(i, 0, name)

            budget = QDoubleSpinBox()
            budget.setRange(10, 4000)
            budget.setDecimals(0)
            budget.setSuffix(" px")
            budget.setValue(spec.budget_px)
            self._budget_spins[spec.key] = budget
            self._budget_table.setCellWidget(i, 1, budget)

            font_px = QDoubleSpinBox()
            font_px.setRange(4, 200)
            font_px.setDecimals(0)
            font_px.setSuffix(" px")
            font_px.setValue(spec.font_px)
            self._font_spins[spec.key] = font_px
            self._budget_table.setCellWidget(i, 2, font_px)

            note = QTableWidgetItem(spec.note)
            if spec.confidence is Confidence.ESTIMATED:
                note.setToolTip(self.tr(
                    "Estimated default — not read from the game's SWF. Verify it "
                    "against your own UI if you can."
                ))
            self._budget_table.setItem(i, 3, note)

        lay.addWidget(self._budget_table)
        return box

    # ── Font source ───────────────────────────────────────────────────────────

    def _browse_font(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Load Font Metrics"),
            str(Path.home()),
            self.tr("Fonts (*.swf *.ttf *.otf);;All files (*)"),
        )
        if not path:
            return
        try:
            sources = load_font_file(Path(path))
        except Exception as exc:
            logger.error("Font metric load failed: %s", exc)
            QMessageBox.warning(
                self, self.tr("Width-Fit Simulator"),
                self.tr("Could not parse that font: {err}").format(err=exc),
            )
            return

        metrics = metrics_from_sources(sources, role=ROLE_BODY)
        if metrics is None:
            # Glyphs without layout data — a real and common SWF case.  Say so
            # rather than silently falling back and reporting invented widths.
            QMessageBox.warning(
                self, self.tr("Width-Fit Simulator"),
                self.tr(
                    "That font contains glyphs but no advance-width table, so it "
                    "cannot be used to measure text.\n\n"
                    "SWF font tags only carry widths when their HasLayout flag is "
                    "set. Keeping the current metrics."
                ),
            )
            return

        # One user-supplied face stands in for every role — we cannot know which
        # of its weights the game pairs with which widget.
        self._metrics = {role: metrics for role in (ROLE_BODY, ROLE_BOLD, ROLE_LATIN)}
        self._custom_font = metrics.name
        self._font_edit.setText(path)
        self._refresh_font_label()
        self._clear_results()

    def _reset_font(self) -> None:
        self._metrics = load_bundled_metrics()
        self._custom_font = None
        self._font_edit.clear()
        self._refresh_font_label()
        self._clear_results()

    def _refresh_font_label(self) -> None:
        if not self._metrics:
            self._font_label.setText(self.tr(
                "⚠ No font metrics available — data/fonts/ is missing. "
                "Load a SWF or TTF to measure text."
            ))
            return
        if self._custom_font:
            self._font_label.setText(
                self.tr("Measuring with: {name} (loaded file, used for every widget)")
                .format(name=self._custom_font)
            )
            return
        names = ", ".join(
            f"{role}={m.name}" for role, m in sorted(self._metrics.items())
        )
        self._font_label.setText(
            self.tr("Measuring with the bundled Starfield fonts — {names}").format(names=names)
        )

    # ── Scanning ──────────────────────────────────────────────────────────────

    def _current_budgets(self) -> Dict[str, WidgetSpec]:
        """Read the budget editor back into WidgetSpec overrides."""
        out: Dict[str, WidgetSpec] = {}
        for key, spec in WIDGETS.items():
            budget = self._budget_spins[key].value()
            font_px = self._font_spins[key].value()
            if budget != spec.budget_px or font_px != spec.font_px:
                out[key] = WidgetSpec(
                    key=spec.key, label=spec.label, budget_px=budget, font_px=font_px,
                    role=spec.role, uppercase=spec.uppercase,
                    confidence=spec.confidence, note=spec.note,
                )
        return out

    def _clear_results(self) -> None:
        self._result = None
        self._table.setRowCount(0)
        self._summary_label.setText("")
        self._export_btn.setEnabled(False)
        self._jump_btn.setEnabled(False)

    def _run_scan(self) -> None:
        if not self._metrics:
            QMessageBox.warning(
                self, self.tr("Width-Fit Simulator"),
                self.tr("No font metrics loaded — nothing can be measured."),
            )
            return

        self._scan_btn.setEnabled(False)
        try:
            result = scan_rows(
                self._rows,
                self._metrics,
                budgets=self._current_budgets(),
                widget_override=self._widget_combo.currentData(),
            )
            self._result = result
            self._populate(result)
        except Exception as exc:
            logger.exception("Width-fit scan failed")
            self._summary_label.setText(self.tr("Error during scan: {err}").format(err=exc))
        finally:
            self._scan_btn.setEnabled(True)

    def _on_tight_toggled(self) -> None:
        """Re-render from the existing scan — no need to re-measure."""
        if self._result is not None:
            self._populate(self._result)

    def _visible_results(self) -> List[FitResult]:
        """Overflowing rows, plus tight-but-fitting ones when asked for."""
        if self._result is None:
            return []
        rows = list(self._result.results)
        if self._tight_chk.isChecked():
            rows += self._result.tight_fits(_WARN_FILL)
        return rows

    def _populate(self, result: WidthCheckResult) -> None:
        rows = self._visible_results()

        if result.checked == 0:
            self._summary_label.setText(self.tr(
                "No length-critical strings found — this file is all prose, which "
                "wraps rather than clipping."
            ))
        elif not result.results:
            self._summary_label.setText(self.tr(
                "✓ All {n} length-critical string(s) fit their widget."
            ).format(n=result.checked))
        else:
            self._summary_label.setText(self.tr(
                "{over} of {n} length-critical string(s) overflow  ·  {prose} prose "
                "string(s) skipped"
            ).format(
                over=result.overflow_count, n=result.checked, prose=result.skipped_prose,
            ))

        self._table.setRowCount(len(rows))
        for i, res in enumerate(rows):
            self._table.setItem(i, 0, self._id_item(res))
            self._table.setItem(i, 1, QTableWidgetItem(WIDGETS[res.widget_key].label))
            self._table.setItem(i, 2, QTableWidgetItem(res.source))
            self._table.setItem(i, 3, self._translation_item(res))
            self._table.setItem(i, 4, self._num_item(f"{res.width_px:.0f} px"))
            self._table.setItem(i, 5, self._num_item(f"{res.budget_px:.0f} px"))
            self._table.setItem(i, 6, self._fill_item(res))
            self._table.setItem(i, 7, self._ratio_item(res))

        self._export_btn.setEnabled(bool(rows))
        self._jump_btn.setEnabled(bool(rows))

    # ── Cell factories ────────────────────────────────────────────────────────

    def _id_item(self, res: FitResult) -> QTableWidgetItem:
        item = QTableWidgetItem(
            format_string_id(res.string_id) if res.string_id else str(res.row_index)
        )
        item.setFont(QFont("monospace"))
        item.setData(Qt.ItemDataRole.UserRole, res.row_index)
        return item

    def _translation_item(self, res: FitResult) -> QTableWidgetItem:
        item = QTableWidgetItem(res.translated)
        if res.is_approximate:
            item.setText(f"{res.translated}  ≈")
            item.setToolTip(self.tr(
                "Approximate: this string contains runtime text (a name, a number) "
                "or characters with no metric in the loaded font, so its final width "
                "is not fully knowable here."
            ))
        return item

    def _num_item(self, text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return item

    def _fill_item(self, res: FitResult) -> QTableWidgetItem:
        pct = res.fill_ratio * 100
        item = self._num_item(f"{pct:.0f}%")
        if not res.fits:
            item.setForeground(QColor("#cc2222"))
            item.setToolTip(self.tr("Overflows by {n:.0f} px").format(n=res.overflow_px))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        elif res.fill_ratio >= _WARN_FILL:
            item.setForeground(QColor("#cc7700"))
            item.setToolTip(self.tr("Fits, but with almost no margin."))
        else:
            item.setForeground(QColor("#2e9e4f"))
        return item

    def _ratio_item(self, res: FitResult) -> QTableWidgetItem:
        if res.source_width_px <= 0:
            return self._num_item("—")
        item = self._num_item(f"×{res.source_ratio:.2f}")
        item.setToolTip(self.tr(
            "Translated width as a multiple of the English source width. The English "
            "fit by construction, so a high value means overflow regardless of how "
            "accurate the budget estimate is."
        ))
        if res.source_ratio >= 1.5:
            item.setForeground(QColor("#cc2222"))
        elif res.source_ratio >= 1.25:
            item.setForeground(QColor("#cc7700"))
        return item

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_row_activated(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        item = self._table.item(row, 0)
        if item is None:
            return
        row_idx = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(row_idx, int):
            self.jump_to_row.emit(row_idx)

    def _export_csv(self) -> None:
        rows = self._visible_results()
        if not rows:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export Width-Fit Report"),
            str(Path.home() / "width_fit_report.csv"),
            self.tr("CSV (*.csv);;All files (*)"),
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh)
                writer.writerow([
                    "String ID", "Widget", "Source", "Translation",
                    "Width (px)", "Budget (px)", "Fill %", "vs Source",
                    "Fits", "Approximate",
                ])
                for r in rows:
                    writer.writerow([
                        format_string_id(r.string_id), WIDGETS[r.widget_key].label,
                        r.source, r.translated,
                        f"{r.width_px:.0f}", f"{r.budget_px:.0f}",
                        f"{r.fill_ratio * 100:.0f}",
                        f"{r.source_ratio:.2f}" if r.source_width_px else "",
                        "yes" if r.fits else "no",
                        "yes" if r.is_approximate else "no",
                    ])
        except OSError as exc:
            logger.error("Width-fit export failed: %s", exc)
            QMessageBox.warning(
                self, self.tr("Width-Fit Simulator"),
                self.tr("Could not write the report: {err}").format(err=exc),
            )
