"""Font & Glyph Checker dialog.

Scans translated strings for characters not supported by the game's font
atlases (Scaleform SWF) and flags them so the translator can fix them before
they render as tofu (□) in-game.

Flow:
  1. User picks a font source (SWF file / TTF file / game directory /
     built-in conservative safe set).
  2. Click "Scan" — FontChecker scans all translated strings.
  3. Results show: which characters are missing (with U+ code and fix hint),
     and which strings are affected (with a Jump button).
  4. "Auto-fix All" applies every suggested safe replacement at once via
     the ``fix_applied`` signal.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from bethesda_strings.font_checker import FontCheckResult, FontChecker, GlyphIssue, MissingGlyph

logger = logging.getLogger(__name__)


class FontCheckerDialog(QDialog):
    """Modal dialog for font glyph coverage scanning.

    Emits:
      jump_to_row(row_index)  — navigate main table to this row
      fix_applied(list of (row_index, new_text)) — apply auto-fixes to model
    """

    jump_to_row = Signal(int)
    fix_applied = Signal(list)   # list[tuple[int, str]]

    # ──────────────────────────────────────────────────────────────────────────

    def __init__(self, rows: List[dict], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._rows = rows
        self._checker = FontChecker()
        self._result: Optional[FontCheckResult] = None
        self._selected_missing: Optional[MissingGlyph] = None

        self.setWindowTitle(self.tr("Font & Glyph Checker"))
        self.setWindowIcon(QIcon.fromTheme("font-x-generic"))
        self.resize(820, 620)
        self.setModal(True)

        self._build_ui()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(8)

        # ── Font source group ──────────────────────────────────────────────
        src_box = QGroupBox(self.tr("Font Source"))
        src_layout = QVBoxLayout(src_box)
        src_layout.setSpacing(4)

        # Row 1: SWF file
        swf_row = QHBoxLayout()
        swf_label = QLabel(self.tr("SWF font atlas:"))
        swf_label.setFixedWidth(130)
        self._swf_edit = QLineEdit()
        self._swf_edit.setPlaceholderText(self.tr("Optional — e.g. Data/Interface/Fonts.swf"))
        self._swf_edit.setReadOnly(True)
        self._swf_btn = QToolButton()
        self._swf_btn.setText("…")
        self._swf_btn.setToolTip(self.tr("Browse for a Scaleform SWF font atlas"))
        self._swf_btn.clicked.connect(self._browse_swf)
        swf_row.addWidget(swf_label)
        swf_row.addWidget(self._swf_edit, 1)
        swf_row.addWidget(self._swf_btn)
        src_layout.addLayout(swf_row)

        # Row 2: TTF/OTF file
        ttf_row = QHBoxLayout()
        ttf_label = QLabel(self.tr("TTF / OTF font:"))
        ttf_label.setFixedWidth(130)
        self._ttf_edit = QLineEdit()
        self._ttf_edit.setPlaceholderText(self.tr("Optional — e.g. Data/Fonts/SomeFontUA.ttf"))
        self._ttf_edit.setReadOnly(True)
        self._ttf_btn = QToolButton()
        self._ttf_btn.setText("…")
        self._ttf_btn.setToolTip(self.tr("Browse for a TTF / OTF font file"))
        self._ttf_btn.clicked.connect(self._browse_ttf)
        ttf_row.addWidget(ttf_label)
        ttf_row.addWidget(self._ttf_edit, 1)
        ttf_row.addWidget(self._ttf_btn)
        src_layout.addLayout(ttf_row)

        # Row 3: game directory
        gd_row = QHBoxLayout()
        gd_label = QLabel(self.tr("Game Data dir:"))
        gd_label.setFixedWidth(130)
        self._gd_edit = QLineEdit()
        self._gd_edit.setPlaceholderText(self.tr("Optional — auto-locates fontconfig.txt + SWF files"))
        self._gd_edit.setReadOnly(True)
        self._gd_btn = QToolButton()
        self._gd_btn.setText("…")
        self._gd_btn.setToolTip(self.tr("Browse for the game's Data directory"))
        self._gd_btn.clicked.connect(self._browse_game_dir)
        gd_row.addWidget(gd_label)
        gd_row.addWidget(self._gd_edit, 1)
        gd_row.addWidget(self._gd_btn)
        src_layout.addLayout(gd_row)

        # Loaded font labels
        self._src_info_label = QLabel(self.tr(
            "No external font loaded — using built-in Starfield safe character set."
        ))
        self._src_info_label.setWordWrap(True)
        pal = self._src_info_label.palette()
        pal.setColor(self._src_info_label.foregroundRole(), QColor("#888"))
        self._src_info_label.setPalette(pal)
        src_layout.addWidget(self._src_info_label)

        root.addWidget(src_box)

        # ── Scan button + summary ──────────────────────────────────────────
        scan_row = QHBoxLayout()
        self._scan_btn = QPushButton(self.tr("Scan Translations"))
        self._scan_btn.setIcon(QIcon.fromTheme("system-search"))
        self._scan_btn.clicked.connect(self._run_scan)
        scan_row.addWidget(self._scan_btn)
        self._summary_label = QLabel("")
        self._summary_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scan_row.addWidget(self._summary_label)
        root.addLayout(scan_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)

        # ── Results splitter ───────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: missing character table
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 4, 0)
        ll.addWidget(QLabel(self.tr("Missing characters:")))
        self._char_table = QTableWidget(0, 4)
        self._char_table.setHorizontalHeaderLabels([
            self.tr("Char"), self.tr("U+"), self.tr("Strings"), self.tr("Suggested fix"),
        ])
        self._char_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._char_table.horizontalHeader().setStretchLastSection(True)
        self._char_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._char_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._char_table.setAlternatingRowColors(True)
        self._char_table.itemSelectionChanged.connect(self._on_char_selected)
        ll.addWidget(self._char_table, 1)
        splitter.addWidget(left)

        # Right: affected strings table
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 0, 0, 0)
        rl.addWidget(QLabel(self.tr("Affected strings:")))
        self._string_table = QTableWidget(0, 4)
        self._string_table.setHorizontalHeaderLabels([
            self.tr("ID"), self.tr("Translation (excerpt)"), self.tr("Missing"), self.tr(""),
        ])
        self._string_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._string_table.horizontalHeader().setStretchLastSection(False)
        hdr = self._string_table.horizontalHeader()
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._string_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._string_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._string_table.setAlternatingRowColors(True)
        rl.addWidget(self._string_table, 1)
        splitter.addWidget(right)

        splitter.setSizes([300, 520])
        root.addWidget(splitter, 1)

        # ── Bottom buttons ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._fix_btn = QPushButton(self.tr("Auto-fix All"))
        self._fix_btn.setIcon(QIcon.fromTheme("edit-find-replace"))
        self._fix_btn.setEnabled(False)
        self._fix_btn.setToolTip(self.tr(
            "Replace all unsupported characters that have a known safe substitute.\n"
            "Characters with no safe replacement are left unchanged."
        ))
        self._fix_btn.clicked.connect(self._apply_all_fixes)
        btn_row.addWidget(self._fix_btn)

        self._export_btn = QPushButton(self.tr("Export Report…"))
        self._export_btn.setIcon(QIcon.fromTheme("document-save"))
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_report)
        btn_row.addWidget(self._export_btn)

        btn_row.addStretch(1)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    # ── Font source pickers ───────────────────────────────────────────────────

    def _browse_swf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Open SWF Font Atlas"),
            str(Path.home()),
            self.tr("Scaleform SWF (*.swf);;All files (*)"),
        )
        if path:
            self._swf_edit.setText(path)
            self._reload_fonts()

    def _browse_ttf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Open TrueType / OpenType Font"),
            str(Path.home()),
            self.tr("Font files (*.ttf *.otf);;All files (*)"),
        )
        if path:
            self._ttf_edit.setText(path)
            self._reload_fonts()

    def _browse_game_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            self.tr("Select Game Data Directory"),
            str(Path.home()),
        )
        if path:
            self._gd_edit.setText(path)
            self._reload_fonts()

    def _reload_fonts(self) -> None:
        """Re-build the FontChecker from the current UI source fields."""
        self._checker.clear()
        loaded_names: List[str] = []

        gd = self._gd_edit.text().strip()
        if gd:
            n = self._checker.load_game_directory(Path(gd))
            if n:
                loaded_names.append(self.tr("{n} font(s) from game directory").format(n=n))
            else:
                loaded_names.append(self.tr("⚠ Game directory: no fonts found"))

        swf = self._swf_edit.text().strip()
        if swf:
            n = self._checker.load_swf(Path(swf))
            if n:
                loaded_names.append(self.tr("{n} font(s) from SWF").format(n=n))
            else:
                loaded_names.append(self.tr("⚠ SWF: no font records found"))

        ttf = self._ttf_edit.text().strip()
        if ttf:
            n = self._checker.load_ttf(Path(ttf))
            if n:
                loaded_names.append(
                    self.tr("{name} ({cp} glyphs)").format(
                        name=self._checker.sources[-1].name,
                        cp=self._checker.sources[-1].glyph_count,
                    )
                )
            else:
                loaded_names.append(self.tr("⚠ TTF/OTF: could not parse font"))

        if loaded_names:
            self._src_info_label.setText(
                self.tr("Loaded: {info}").format(info="; ".join(loaded_names))
            )
        else:
            self._src_info_label.setText(self.tr(
                "No external font loaded — using built-in Starfield safe character set."
            ))

        # Clear stale results when source changes
        self._result = None
        self._char_table.setRowCount(0)
        self._string_table.setRowCount(0)
        self._summary_label.setText("")
        self._fix_btn.setEnabled(False)
        self._export_btn.setEnabled(False)

    # ── Scanning ──────────────────────────────────────────────────────────────

    def _run_scan(self) -> None:
        self._scan_btn.setEnabled(False)
        self._char_table.setRowCount(0)
        self._string_table.setRowCount(0)
        self._summary_label.setText(self.tr("Scanning…"))

        try:
            self._result = self._checker.check_rows(self._rows)
            self._populate_results(self._result)
        except Exception as exc:
            logger.error("Font scan error: %s", exc)
            self._summary_label.setText(self.tr("Error during scan: {err}").format(err=exc))
        finally:
            self._scan_btn.setEnabled(True)

    def _populate_results(self, result: FontCheckResult) -> None:
        if not result.missing_glyphs:
            self._summary_label.setText(
                self.tr("✓ All {n} translated strings use supported characters.").format(
                    n=result.total_strings_scanned
                )
            )
            self._fix_btn.setEnabled(False)
            self._export_btn.setEnabled(False)
            return

        self._summary_label.setText(
            self.tr(
                "{issues} string(s) contain {chars} unsupported character(s)"
            ).format(
                issues=result.strings_with_issues,
                chars=len(result.missing_glyphs),
            )
        )

        # Populate missing-char table
        self._char_table.setRowCount(len(result.missing_glyphs))
        for row_i, mg in enumerate(result.missing_glyphs):
            char_item = QTableWidgetItem(mg.char)
            char_item.setFont(QFont("monospace", 14))
            char_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._char_table.setItem(row_i, 0, char_item)

            uplus = QTableWidgetItem(f"U+{mg.codepoint:04X}")
            uplus.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._char_table.setItem(row_i, 1, uplus)

            count_item = QTableWidgetItem(str(mg.string_count))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._char_table.setItem(row_i, 2, count_item)

            if mg.suggested_fix is not None:
                fix_text = repr(mg.suggested_fix) if mg.suggested_fix else "(delete)"
                fix_item = QTableWidgetItem(
                    f"→ {fix_text}" + ("" if mg.fix_is_safe else " ⚠ (fix char not in font)")
                )
                if not mg.fix_is_safe:
                    fix_item.setForeground(QColor("#cc7700"))
            else:
                fix_item = QTableWidgetItem(self.tr("No safe replacement"))
                fix_item.setForeground(QColor("#cc0000"))
            self._char_table.setItem(row_i, 3, fix_item)

        # Select first row to populate the strings panel
        self._char_table.selectRow(0)

        any_fixable = any(mg.fix_is_safe for mg in result.missing_glyphs)
        self._fix_btn.setEnabled(any_fixable)
        self._export_btn.setEnabled(True)

    def _on_char_selected(self) -> None:
        if self._result is None:
            return
        rows = self._char_table.selectedItems()
        if not rows:
            return
        row_i = self._char_table.currentRow()
        if row_i < 0 or row_i >= len(self._result.missing_glyphs):
            return
        mg = self._result.missing_glyphs[row_i]
        self._selected_missing = mg
        self._populate_string_table(mg)

    def _populate_string_table(self, mg: MissingGlyph) -> None:
        """Show all strings affected by the currently selected missing char."""
        if self._result is None:
            return

        # Build a quick index: row_index → GlyphIssue
        issue_map: Dict[int, GlyphIssue] = {i.row_index: i for i in self._result.issues}

        self._string_table.setRowCount(len(mg.row_indices))
        for t_row, row_idx in enumerate(mg.row_indices):
            issue = issue_map.get(row_idx)
            if issue is None:
                continue

            id_hex = f"0x{issue.string_id:08X}" if issue.string_id else str(row_idx)
            id_item = QTableWidgetItem(id_hex)
            id_item.setFont(QFont("monospace"))
            id_item.setData(Qt.ItemDataRole.UserRole, row_idx)
            self._string_table.setItem(t_row, 0, id_item)

            # Excerpt of translated text with the bad character highlighted
            excerpt = issue.translated[:120].replace("\n", "↵")
            self._string_table.setItem(t_row, 1, QTableWidgetItem(excerpt))

            chars_str = " ".join(f"U+{ord(c):04X}" for c in issue.missing_chars)
            self._string_table.setItem(t_row, 2, QTableWidgetItem(chars_str))

            # Jump button
            jump_btn = QPushButton(self.tr("Jump"))
            jump_btn.setFixedWidth(54)
            jump_btn.clicked.connect(self._make_jump_handler(row_idx))
            self._string_table.setCellWidget(t_row, 3, jump_btn)

        self._string_table.resizeColumnToContents(0)
        self._string_table.resizeColumnToContents(2)
        self._string_table.resizeColumnToContents(3)

    def _make_jump_handler(self, row_idx: int):
        return lambda: self.jump_to_row.emit(row_idx)

    # ── Auto-fix ──────────────────────────────────────────────────────────────

    def _apply_all_fixes(self) -> None:
        if self._result is None:
            return
        patches: List[tuple] = []
        for issue in self._result.issues:
            if issue.fixed_text is not None and issue.fixed_text != issue.translated:
                patches.append((issue.row_index, issue.fixed_text))

        if not patches:
            return

        self.fix_applied.emit(patches)

        # Update our local row cache so re-scan sees the changes
        for row_idx, new_text in patches:
            if row_idx < len(self._rows):
                self._rows[row_idx]["translated"] = new_text

        # Re-scan with the patched data
        self._run_scan()

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_report(self) -> None:
        if self._result is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            self.tr("Export Glyph Report"),
            str(Path.home() / "glyph_report.html"),
            self.tr("HTML report (*.html);;Text report (*.txt);;All files (*)"),
        )
        if not path:
            return
        if path.endswith(".txt"):
            self._export_txt(path)
        else:
            self._export_html(path)

    def _export_txt(self, path: str) -> None:
        r = self._result
        if r is None:
            return
        lines = [
            "Font & Glyph Checker Report",
            "=" * 40,
            f"Strings scanned:  {r.total_strings_scanned}",
            f"Strings affected: {r.strings_with_issues}",
            f"Missing chars:    {len(r.missing_glyphs)}",
            "",
            "Missing characters:",
        ]
        for mg in r.missing_glyphs:
            fix = f" → {mg.suggested_fix!r}" if mg.suggested_fix is not None else " (no fix)"
            lines.append(f"  U+{mg.codepoint:04X}  {mg.char!r:6}  {mg.string_count} string(s){fix}")
        lines.append("")
        lines.append("Affected strings:")
        for issue in r.issues:
            lines.append(f"  [0x{issue.string_id:08X}] {issue.translated[:80]!r}")
            lines.append(f"    Missing: {', '.join(f'U+{ord(c):04X}' for c in issue.missing_chars)}")
            if issue.fixed_text:
                lines.append(f"    Fixed:   {issue.fixed_text[:80]!r}")
        try:
            Path(path).write_text("\n".join(lines), encoding="utf-8")
        except OSError as exc:
            logger.error("Export failed: %s", exc)

    def _export_html(self, path: str) -> None:
        r = self._result
        if r is None:
            return
        rows_html = ""
        for issue in r.issues:
            chars_str = ", ".join(
                f'<code>U+{ord(c):04X}</code>' for c in issue.missing_chars
            )
            fixed_cell = f"<td>{issue.fixed_text[:100]}</td>" if issue.fixed_text else "<td>—</td>"
            rows_html += (
                f"<tr>"
                f"<td><code>0x{issue.string_id:08X}</code></td>"
                f"<td>{issue.translated[:120]}</td>"
                f"<td>{chars_str}</td>"
                f"{fixed_cell}"
                f"</tr>\n"
            )
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Glyph Report</title>
<style>body{{font-family:sans-serif;font-size:13px}}
table{{border-collapse:collapse;width:100%}}
th,td{{border:1px solid #ccc;padding:4px 8px;text-align:left}}
th{{background:#eee}}tr:nth-child(even){{background:#f9f9f9}}</style>
</head><body>
<h2>Font &amp; Glyph Checker Report</h2>
<p>Strings scanned: <b>{r.total_strings_scanned}</b> &nbsp;
   Strings affected: <b>{r.strings_with_issues}</b> &nbsp;
   Missing characters: <b>{len(r.missing_glyphs)}</b></p>
<table>
<tr><th>String ID</th><th>Translation</th><th>Missing chars</th><th>Auto-fix preview</th></tr>
{rows_html}
</table>
</body></html>"""
        try:
            Path(path).write_text(html, encoding="utf-8")
        except OSError as exc:
            logger.error("Export failed: %s", exc)
