"""
QTableView model for displaying and editing Bethesda string entries
FIXED: Infinite recursion in headerData/flags, QPalette.Base error
"""

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QTextEdit,
    QVBoxLayout,
)

from bethesda_strings import BethesdaStringFile, EncodingConverter
from bethesda_strings.esp_handler import EspFile

logger = logging.getLogger(__name__)


class StringTableModel(QAbstractTableModel):
    """Model for Bethesda string data in QTableView."""

    COLUMNS = ["ID", "Original", "Translated", "Length", "Offset", "Status"]

    # Column header overrides for ESP/ESM mode
    _ESP_HEADERS = {0: "FormID", 3: "EDID", 4: "Type"}

    # Emitted when the user manually edits a row that was already AI-translated.
    # Carries (row_index, original_source_text) so the estimator can learn.
    string_manually_corrected = Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: List[dict] = []
        self._encoding = "utf-8"
        self._locale = None
        self._mode = "strings"  # "strings" or "esp"
        self._file_ref: Optional[BethesdaStringFile] = None
        self._diff_data: Dict[int, str] = {}
        self._quality_data: Dict[int, str] = {}   # row_index → severity string
        self._pre_est_data: Dict[int, Any] = {}   # row_index → ComplexityReport
        self._color_blind_mode: bool = False

    def set_color_blind_mode(self, enabled: bool) -> None:
        """Switch between default red/green and color-blind-friendly blue/orange palette."""
        if self._color_blind_mode != enabled:
            self._color_blind_mode = enabled
            self.layoutChanged.emit()

    def load_from_esp_file(self, esp: EspFile, encoding: str = "utf-8", locale: Optional[str] = None):
        """Populate model from an EspFile (ESP/ESM plugin)."""
        self.beginResetModel()
        self._mode = "esp"
        self._file_ref = None
        self._encoding = encoding
        self._locale = locale.lower() if locale else None
        self._data.clear()
        self._diff_data.clear()
        self._quality_data.clear()
        self._pre_est_data.clear()

        for entry in esp.strings:
            self._data.append({
                "id":     entry.form_id,
                "original": entry.original,
                "translated": entry.translation or "",
                "length": entry.edid,                          # repurposed: EDID
                "offset": f"{entry.record_sig} {entry.field_sig}",  # repurposed: type
                "status": "translated" if entry.translation else "pending",
                "context_note": entry.context_note,
                "_esp_entry": entry,
            })

        self.endResetModel()
        logger.info(f"Loaded {len(self._data)} strings from ESP/ESM (mode=esp)")

    def apply_changes_to_esp_file(self, esp: EspFile, encoding: str = "utf-8") -> None:
        """Write translated text back into EspStringEntry objects."""
        for row in self._data:
            entry = row.get("_esp_entry")
            if entry is None:
                continue
            trans = row.get("translated", "")
            if trans and row["status"] == "translated":
                entry.translation = trans

    def load_from_bethesda_file(
        self,
        file: BethesdaStringFile,
        encoding: Optional[str] = None,
        locale: Optional[str] = None,
    ):
        """Populate model from BethesdaStringFile with auto-detected or explicit encoding.

        If *encoding* is None, the file's auto-detected encoding is used.
        A locale-based fallback is tried only when the primary decode fails.
        """
        self.beginResetModel()
        self._mode = "strings"
        self._file_ref = file
        self._locale = locale.lower() if locale else None

        # Prefer the file's own detected/overridden encoding over locale heuristic.
        primary_enc = encoding if encoding is not None else file.encoding
        self._encoding = primary_enc

        # Keep a locale-based fallback in case primary still fails per-string.
        _, fallback_enc = EncodingConverter.get_encodings_for_locale(locale or "english")
        if fallback_enc == primary_enc:
            fallback_enc = None  # No point trying same encoding twice

        self._data.clear()
        self._diff_data.clear()
        self._quality_data.clear()
        self._pre_est_data.clear()

        for s in file.strings:
            try:
                original = s.get_string(primary_enc)
                used_enc = primary_enc
            except UnicodeDecodeError:
                if fallback_enc:
                    try:
                        original = s.get_string(fallback_enc)
                        used_enc = fallback_enc
                    except Exception:
                        original = s.get_string("utf-8", errors="replace")
                        used_enc = "utf-8"
                else:
                    original = s.get_string("utf-8", errors="replace")
                    used_enc = "utf-8"

            self._data.append(
                {
                    "id": s.id,
                    "original": original,
                    "translated": "",
                    "length": s.length,
                    "offset": s.relative_offset,
                    "status": "pending",
                    "_string_obj": s,
                    "_encoding_used": used_enc,
                }
            )

        self.endResetModel()
        logger.info(
            "Loaded %d strings with encoding %s (source: %s, locale: %s)",
            len(self._data), primary_enc, file._encoding_source, locale,
        )

    def apply_changes_to_file(self, file: BethesdaStringFile):
        """Write translated text back to StringDataObjects using the file's encoding.

        Every string is re-encoded to target_enc, not just translated ones.
        This prevents mixed-encoding output when the source file used a different
        encoding (e.g. CP1251 Russian source saved as UTF-8 Ukrainian).
        Untranslated strings fall back to their decoded original text.
        """
        target_enc = file.encoding  # Always use the file's detected/overridden encoding
        for row in self._data:
            if row["translated"] and row["status"] == "translated":
                text = row["translated"]
            else:
                # Re-encode the original text in the target encoding so the output
                # file is consistently encoded even for untranslated strings.
                src_enc = row.get("_encoding_used", target_enc)
                if src_enc == target_enc:
                    continue  # bytes are already in the right encoding — skip
                text = row.get("original", "")
                if not text:
                    continue
            try:
                row["_string_obj"].set_string(text, target_enc)
            except Exception as e:
                logger.warning(f"Failed to update string {row['id']}: {e}")
                if row["status"] == "translated":
                    row["status"] = "error"

    def set_comparison_data(self, data_map: Dict[int, str]):
        """Set data for comparison (diff)."""
        self._diff_data = data_map
        self.layoutChanged.emit()

    def set_quality_data(self, quality_map: Dict[int, str]) -> None:
        """Update per-row quality severity. quality_map: {row_index → severity}."""
        self._quality_data = quality_map
        self.layoutChanged.emit()

    def clear_quality_data(self) -> None:
        """Remove all quality highlights."""
        if self._quality_data:
            self._quality_data.clear()
            self.layoutChanged.emit()

    def set_pre_est_data(self, est_map: Dict[int, Any]) -> None:
        """Store pre-translation complexity reports. est_map: {row_index → ComplexityReport}."""
        self._pre_est_data = est_map
        self.layoutChanged.emit()

    def clear_pre_est_data(self) -> None:
        if self._pre_est_data:
            self._pre_est_data.clear()
            self.layoutChanged.emit()

    def compute_complexity_estimates(self, estimator: Any, source_lang: str = "English") -> None:
        """Compute and store pre-translation estimates for all pending rows."""
        result: Dict[int, Any] = {}
        for i, row in enumerate(self._data):
            if row.get("status") == "pending":
                result[i] = estimator.estimate(row.get("original", ""), source_lang)
        self.set_pre_est_data(result)

    def set_translated_text(self, row_index: int, text: str):
        """Set translated text for a single row."""
        if 0 <= row_index < len(self._data):
            self._data[row_index]["translated"] = text
            self._data[row_index]["status"] = "translated"
            trans_idx = self.index(row_index, self.COLUMNS.index("Translated"))
            status_idx = self.index(row_index, self.COLUMNS.index("Status"))
            self.dataChanged.emit(trans_idx, trans_idx, [Qt.DisplayRole, Qt.ForegroundRole])
            self.dataChanged.emit(status_idx, status_idx, [Qt.DisplayRole, Qt.ForegroundRole])

    def set_translated_text_batch(self, updates: list) -> None:
        """Apply multiple (row_index, text) pairs and emit a single dataChanged range.

        Coalesces N individual dataChanged signals into one covering the full
        dirty range — the view repaints once per 16ms flush instead of once per
        translation signal, keeping the UI at ~60 fps during batch translation.
        """
        if not updates:
            return
        rows_changed = []
        for row_index, text in updates:
            if 0 <= row_index < len(self._data):
                self._data[row_index]["translated"] = text
                self._data[row_index]["status"] = "translated"
                rows_changed.append(row_index)
        if not rows_changed:
            return
        min_row = min(rows_changed)
        max_row = max(rows_changed)
        first_col = min(self.COLUMNS.index("Translated"), self.COLUMNS.index("Status"))
        last_col = max(self.COLUMNS.index("Translated"), self.COLUMNS.index("Status"))
        self.dataChanged.emit(
            self.index(min_row, first_col),
            self.index(max_row, last_col),
            [Qt.DisplayRole, Qt.ForegroundRole],
        )

    def import_translations(
        self,
        translation_map: dict[int, str],
        source_map: dict[str, str] | None = None,
    ) -> int:
        """Import translations from ID→text and/or source→text maps.

        Matching order (mirrors xTranslator's XMLImportbase):
          1. By string ID (fast, exact)
          2. By source text (fallback for rows whose ID wasn't in the map)

        Returns the number of rows actually updated.
        """
        if not translation_map and not source_map:
            return 0

        applied_count = 0
        id_to_row = {row["id"]: i for i, row in enumerate(self._data)}

        self.beginResetModel()

        # Pass 1: match by ID
        matched_rows: set[int] = set()
        for string_id, text in (translation_map or {}).items():
            if string_id in id_to_row:
                row_idx = id_to_row[string_id]
                self._data[row_idx]["translated"] = text
                self._data[row_idx]["status"] = "translated"
                matched_rows.add(row_idx)
                applied_count += 1

        # Pass 2: match by source text for unmatched rows
        if source_map:
            for i, row in enumerate(self._data):
                if i in matched_rows:
                    continue
                src = row.get("original", "")
                if src and src in source_map:
                    self._data[i]["translated"] = source_map[src]
                    self._data[i]["status"] = "translated"
                    applied_count += 1

        self.endResetModel()
        return applied_count

    def get_row_data(self, row_index: int) -> dict:
        """Get complete row data dict."""
        if 0 <= row_index < len(self._data):
            return self._data[row_index].copy()
        return {}

    def __len__(self) -> int:
        """Return number of rows."""
        return len(self._data)

    def rowCount(self, parent=QModelIndex()):
        """Return number of rows."""
        if parent.isValid():
            return 0
        return len(self._data)

    def columnCount(self, parent=QModelIndex()):
        """Return number of columns."""
        if parent.isValid():
            return 0
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        """Return header data."""
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self.COLUMNS):
                if self._mode == "esp" and section in self._ESP_HEADERS:
                    return self._ESP_HEADERS[section]
                col_name = self.COLUMNS[section]
                return QApplication.translate("StringTableModel", col_name)
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        """Return cell data."""
        if not index.isValid():
            return None

        row = index.row()
        col = index.column()

        if row >= len(self._data) or col >= len(self.COLUMNS):
            return None

        row_data = self._data[row]
        col_name = self.COLUMNS[col]

        if role in [Qt.DisplayRole, Qt.EditRole]:
            if col_name == "ID":
                if self._mode == "esp":
                    return f"{row_data['id']:08X}"
                return f"0x{row_data['id']:08X}"
            elif col_name == "Original":
                text = row_data["original"]
                if role == Qt.EditRole:
                    return text
                return text[:200] + ("…" if len(text) > 200 else "")
            elif col_name == "Translated":
                text = row_data.get("translated", "")
                if role == Qt.EditRole:
                    return text
                return text[:200] + ("…" if len(text) > 200 else "") if text else ""
            elif col_name == "Length":
                if self._mode == "esp":
                    return str(row_data["length"])  # EDID string
                return str(row_data["length"])
            elif col_name == "Offset":
                if self._mode == "esp":
                    return str(row_data["offset"])  # "ACTI FULL"
                return f"0x{row_data['offset']:X}"
            elif col_name == "Status":
                status_map = {"pending": "⏳", "translated": "✓", "error": "✗"}
                base = status_map.get(row_data["status"], "?")
                if row_data["status"] == "translated":
                    q = self._quality_data.get(row)
                    if q == "error":
                        return "⚠✗"
                    if q == "warning":
                        return "⚠"
                elif row_data["status"] == "pending":
                    est = self._pre_est_data.get(row)
                    if est is not None:
                        return est.status_icon  # ○ / ◑ / ●
                return base

        elif role == Qt.ForegroundRole:
            # Color-blind mode: replace green→blue, red→orange for deuteranopia safety.
            # Symbols (✓/⚠/✗) already distinguish states without color.
            ok_col  = QColor("#2563eb") if self._color_blind_mode else QColor("#22c55e")
            err_col = QColor("#ea580c") if self._color_blind_mode else QColor("#dc2626")
            if col_name == "Status":
                if row_data["status"] == "translated":
                    q = self._quality_data.get(row)
                    if q == "error":
                        return err_col
                    if q == "warning":
                        return QColor("#d97706")
                    return ok_col
                elif row_data["status"] == "error":
                    return err_col
                elif row_data["status"] == "pending":
                    est = self._pre_est_data.get(row)
                    if est is not None:
                        if est.level == "hard":
                            return err_col
                        if est.level == "medium":
                            return QColor("#f59e0b")   # amber — same for both modes
            elif col_name == "Translated" and not row_data.get("translated"):
                return QColor("#9ca3af")

        elif role == Qt.FontRole:
            if col_name in ["Original", "Translated"]:
                font = QFont()
                if self._locale in ["uk", "ru", "be", "bg", "sr"]:
                    font.setFamily("DejaVu Sans Mono")
                else:
                    font.setFamily("Segoe UI")
                # Inherit app font size so the user-configured size is respected
                app_pt = QApplication.font().pointSize()
                font.setPointSize(app_pt if app_pt > 0 else 9)
                return font

        elif role == Qt.AccessibleTextRole:
            # Screen readers (AT-SPI2 on Linux, MSAA/UIA on Windows) read this
            # instead of the raw display text, giving meaningful descriptions.
            if col_name == "Status":
                status = row_data["status"]
                q = self._quality_data.get(row)
                if status == "translated":
                    if q == "error":
                        return self.tr("Translated — quality error")
                    if q == "warning":
                        return self.tr("Translated — quality warning")
                    return self.tr("Translated — OK")
                if status == "pending":
                    est = self._pre_est_data.get(row)
                    if est is not None:
                        return self.tr("Pending — difficulty: {level}").format(
                            level=est.level
                        )
                    return self.tr("Pending")
                if status == "error":
                    return self.tr("Translation error")
            elif col_name == "ID":
                return self.tr("String ID: {id}").format(id=row_data["id"])
            elif col_name == "Original":
                return self.tr("Original: {text}").format(text=row_data["original"])
            elif col_name == "Translated":
                t = row_data.get("translated", "")
                return self.tr("Translation: {text}").format(text=t) if t else self.tr("Not translated")

        elif role == Qt.TextAlignmentRole:
            if col_name in ["ID", "Length", "Offset"]:
                return Qt.AlignRight | Qt.AlignVCenter
            elif col_name == "Status":
                return Qt.AlignCenter

        elif role == Qt.BackgroundRole:
            row_id = row_data["id"]
            _app = QApplication.instance()
            dark = _app is not None and _app.palette().base().color().lightness() < 128
            # Priority: Post-QA Error > Post-QA Warning > Pre-est Hard > Pre-est Medium >
            #           Diff > Translated
            q_sev = self._quality_data.get(row)
            if q_sev == "error":
                return QColor("#4d1010") if dark else QColor("#fee2e2")
            if q_sev == "warning":
                return QColor("#3d2600") if dark else QColor("#fef3c7")

            if row_data["status"] == "pending":
                est = self._pre_est_data.get(row)
                if est is not None:
                    if est.level == "hard":
                        return QColor("#2d1010") if dark else QColor("#fff1f0")
                    if est.level == "medium":
                        return QColor("#281e00") if dark else QColor("#fffbeb")

            if row_id in self._diff_data:
                comparison_text = self._diff_data[row_id]
                if comparison_text != row_data["translated"]:
                    return QColor("#3d3400") if dark else QColor("#fef9c3")

            if (
                col == 2
                and row_data["translated"]
                and row_data["status"] == "translated"
            ):
                return QColor("#0a2535") if dark else QColor("#f0f9ff")

        elif role == Qt.ToolTipRole:
            row_id = row_data["id"]
            base_tooltip = ""
            if col_name == "Original":
                base_tooltip = row_data["original"]
                note = row_data.get("context_note", "")
                if note:
                    base_tooltip = f"📝 Translator note: {note}\n\n{base_tooltip}"
            elif col_name == "Translated":
                base_tooltip = row_data.get("translated", "")
            elif col_name == "ID":
                if self._mode == "esp":
                    base_tooltip = f"FormID: {row_id:08X}  EDID: {row_data.get('length', '')}"
                else:
                    base_tooltip = f"String ID: {row_id} (0x{row_id:08X})"

            extra_info = []
            if row_id in self._diff_data:
                comp_text = self._diff_data[row_id]
                if comp_text != row_data["translated"]:
                    extra_info.append("COMPARISON FILE:")
                    extra_info.append(
                        comp_text[:1000] + ("..." if len(comp_text) > 1000 else "")
                    )

            q_sev = self._quality_data.get(row)
            if col_name == "Status":
                if q_sev:
                    base_tooltip = f"Quality: {q_sev.upper()}"
                elif row_data["status"] == "pending":
                    est = self._pre_est_data.get(row)
                    if est is not None:
                        lines = [f"Complexity: {est.score}/100 ({est.level.upper()})"]
                        if est.suggest_review:
                            lines.append("Suggested for manual review")
                        for issue in est.issues:
                            prefix = {"error": "[ERR]", "warning": "[WARN]", "info": "[INFO]"}.get(
                                issue.severity, "[?]"
                            )
                            lines.append(f"{prefix} {issue.message}")
                            if issue.detail:
                                lines.append(f"      {issue.detail}")
                        base_tooltip = "\n".join(lines)

            if extra_info:
                header = f"{base_tooltip}\n{'-' * 40}\n" if base_tooltip else ""
                return header + "\n".join(extra_info)
            return base_tooltip

        return None

    def setData(self, index, value, role=Qt.EditRole):
        """Update model data."""
        if not index.isValid() or role != Qt.EditRole:
            return False

        row = index.row()
        col = index.column()
        col_name = self.COLUMNS[col]

        if col_name == "Translated":
            was_translated = (
                0 <= row < len(self._data)
                and self._data[row].get("status") == "translated"
            )
            self.set_translated_text(row, value)
            if was_translated and 0 <= row < len(self._data):
                original = self._data[row].get("original", "")
                self.string_manually_corrected.emit(row, original)
            return True

        return False

    def flags(self, index: QModelIndex):
        """Return item flags."""
        try:
            if not index.isValid():
                return Qt.NoItemFlags

            col_name = (
                self.COLUMNS[index.column()] if index.column() < len(self.COLUMNS) else ""
            )

            # Only Translated column is editable
            if col_name == "Translated":
                return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable

            return Qt.ItemIsEnabled | Qt.ItemIsSelectable
        except BaseException:
            return Qt.ItemIsEnabled | Qt.ItemIsSelectable


class StringEditDialog(QDialog):
    """Dialog for editing a single string with multi-line support."""

    def __init__(self, row_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(
            self.tr("Edit String - ID: 0x{row_id:08X}").format(row_id=row_data["id"])
        )
        self.resize(800, 600)

        layout = QVBoxLayout(self)

        # ID Label
        layout.addWidget(
            QLabel(
                self.tr("<b>String ID:</b> 0x{row_id:08X} ({row_id})").format(
                    row_id=row_data["id"]
                )
            )
        )

        # Original Text
        layout.addWidget(QLabel(self.tr("<b>Original Text:</b>")))
        self.txt_original = QTextEdit()
        self.txt_original.setPlainText(row_data["original"])
        self.txt_original.setReadOnly(True)
        # Set a slightly different background to indicate read-only
        palette = self.txt_original.palette()
        palette.setColor(QPalette.Base, palette.color(QPalette.Window))
        self.txt_original.setPalette(palette)
        layout.addWidget(self.txt_original)

        # Translated Text
        layout.addWidget(QLabel(self.tr("<b>Translated Text:</b>")))
        self.txt_translated = QTextEdit()
        self.txt_translated.setPlainText(row_data.get("translated", ""))
        self.txt_translated.setAcceptRichText(False)
        layout.addWidget(self.txt_translated)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Focus translated text
        self.txt_translated.setFocus()

    def get_translated_text(self) -> str:
        """Return the text from the translation field."""
        return self.txt_translated.toPlainText()


class StringTableView(QTableView):
    """Customized QTableView for string data."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableView.SelectRows)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.setWordWrap(True)
        self.setVerticalScrollMode(QTableView.ScrollPerPixel)
        self.setHorizontalScrollMode(QTableView.ScrollPerPixel)

        # Context Menu
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        header = self.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)

        self.setColumnWidth(0, 100)  # ID
        self.setColumnWidth(1, 400)  # Original
        self.setColumnWidth(2, 400)  # Translated
        self.setColumnWidth(3, 60)  # Length
        self.setColumnWidth(4, 80)  # Offset
        self.setColumnWidth(5, 50)  # Status

        self.setItemDelegate(StringItemDelegate(self))

        # Vim-style navigation state
        self._vim_g_pending = False
        self._vim_g_timer = QTimer(self)
        self._vim_g_timer.setSingleShot(True)
        self._vim_g_timer.timeout.connect(self._vim_g_timeout)

    def keyPressEvent(self, event) -> None:
        """Handle keyboard shortcuts and vim-style navigation."""
        # While inline-editing a cell, let the editor handle everything
        if self.state() == QAbstractItemView.EditingState:
            super().keyPressEvent(event)
            return

        key = event.key()
        mods = event.modifiers()
        ctrl  = Qt.KeyboardModifier.ControlModifier
        shift = Qt.KeyboardModifier.ShiftModifier

        # ── Copy / Paste shortcuts ──────────────────────────────────────────
        if mods == ctrl:
            if key == Qt.Key.Key_C:
                self._copy_column("Translated")
                return
            if key == Qt.Key.Key_V:
                self._paste_to_translated()
                return

        if mods == (ctrl | shift):
            if key == Qt.Key.Key_C:
                self._copy_column("Original")
                return
            if key == Qt.Key.Key_V:
                self._fill_translated_from_source()
                return

        # ── Vim-style navigation ────────────────────────────────────────────
        if mods == Qt.KeyboardModifier.NoModifier:
            if key == Qt.Key.Key_J:
                self._vim_move(+1)
                return
            if key == Qt.Key.Key_K:
                self._vim_move(-1)
                return
            if key == Qt.Key.Key_G:
                if self._vim_g_pending:
                    self._vim_g_pending = False
                    self._vim_g_timer.stop()
                    self._vim_go_to_row(0)
                else:
                    self._vim_g_pending = True
                    self._vim_g_timer.start(500)
                return

        if mods == shift and key == Qt.Key.Key_G:
            self._vim_g_pending = False
            self._vim_g_timer.stop()
            self._vim_go_to_row(self.model().rowCount() - 1)
            return

        self._vim_g_pending = False
        self._vim_g_timer.stop()
        super().keyPressEvent(event)

    # ── Clipboard helpers ───────────────────────────────────────────────────

    def _source_model(self):
        m = self.model()
        return m.sourceModel() if hasattr(m, "sourceModel") else m

    def _selected_source_rows(self) -> List[int]:
        """Sorted list of source-model row indices for the current selection."""
        m = self.model()
        has_proxy = hasattr(m, "mapToSource")
        rows: List[int] = []
        for idx in self.selectionModel().selectedRows():
            rows.append(m.mapToSource(idx).row() if has_proxy else idx.row())
        if not rows:
            cur = self.currentIndex()
            if cur.isValid():
                rows.append(m.mapToSource(cur).row() if has_proxy else cur.row())
        return sorted(rows)

    def _copy_column(self, col_name: str) -> None:
        """Copy text from col_name for all selected rows, joined by newlines."""
        sm = self._source_model()
        if sm is None or not hasattr(sm, "COLUMNS"):
            return
        col_idx = sm.COLUMNS.index(col_name)
        texts = [
            sm.data(sm.index(r, col_idx), Qt.ItemDataRole.EditRole) or ""
            for r in self._selected_source_rows()
        ]
        if texts:
            QApplication.clipboard().setText("\n".join(texts))

    def _paste_to_translated(self) -> None:
        """Paste clipboard text into the Translated cell of selected rows.

        If clipboard has exactly as many lines as there are selected rows the
        text is distributed 1-to-1; otherwise every selected row gets the full
        clipboard text.
        """
        text = QApplication.clipboard().text()
        if not text:
            return
        sm = self._source_model()
        if sm is None or not hasattr(sm, "COLUMNS"):
            return
        rows = self._selected_source_rows()
        if not rows:
            return
        col = sm.COLUMNS.index("Translated")
        lines = text.splitlines()
        for i, row in enumerate(rows):
            cell_text = lines[i] if len(lines) == len(rows) else text
            sm.setData(sm.index(row, col), cell_text)

    def _fill_translated_from_source(self) -> None:
        """Copy each selected row's Original text into its Translated cell."""
        sm = self._source_model()
        if sm is None or not hasattr(sm, "COLUMNS"):
            return
        orig_col  = sm.COLUMNS.index("Original")
        trans_col = sm.COLUMNS.index("Translated")
        for row in self._selected_source_rows():
            orig = sm.data(sm.index(row, orig_col), Qt.ItemDataRole.EditRole) or ""
            if orig:
                sm.setData(sm.index(row, trans_col), orig)

    def _vim_g_timeout(self) -> None:
        self._vim_g_pending = False

    def _vim_move(self, delta: int) -> None:
        m = self.model()
        if m is None:
            return
        row = self.currentIndex().row()
        new_row = max(0, min(row + delta, m.rowCount() - 1))
        self._vim_go_to_row(new_row)

    def _vim_go_to_row(self, row: int) -> None:
        m = self.model()
        if m is None or m.rowCount() == 0:
            return
        idx = m.index(row, 0)
        self.setCurrentIndex(idx)
        self.scrollTo(idx)

    def resize_columns_to_content(self):
        """Auto-size columns based on content."""
        self.resizeColumnToContents(0)
        self.setColumnWidth(1, 400)
        self.setColumnWidth(2, 400)

    @Slot(QModelIndex)
    def _show_context_menu(self, position):
        """Show context menu for string table."""
        index = self.indexAt(position)
        if not index.isValid():
            return

        menu = QMenu(self)

        edit_action = menu.addAction(self.tr("Edit String..."))
        edit_action.triggered.connect(lambda: self._open_edit_dialog(index.row()))

        diff_action = menu.addAction(self.tr("View Diff..."))
        diff_action.triggered.connect(lambda: self._open_diff_dialog(index.row()))

        menu.addSeparator()

        act = menu.addAction(self.tr("Copy Translation\tCtrl+C"))
        act.triggered.connect(lambda: self._copy_column("Translated"))

        act = menu.addAction(self.tr("Copy Source\tCtrl+Shift+C"))
        act.triggered.connect(lambda: self._copy_column("Original"))

        menu.addSeparator()

        act = menu.addAction(self.tr("Paste to Translation\tCtrl+V"))
        act.triggered.connect(self._paste_to_translated)

        act = menu.addAction(self.tr("Fill Translation from Source\tCtrl+Shift+V"))
        act.triggered.connect(self._fill_translated_from_source)

        menu.exec(self.viewport().mapToGlobal(position))

    def _copy_to_clipboard(self, index, col_name):
        """Copy text from specific column to clipboard."""
        model = self.model()
        # Handle proxy model if exists
        if hasattr(model, "mapToSource"):
            source_index = model.mapToSource(index)
            source_model = model.sourceModel()
        else:
            source_index = index
            source_model = model

        col_idx = source_model.COLUMNS.index(col_name)
        text = source_model.data(
            source_model.index(source_index.row(), col_idx), Qt.EditRole
        )
        if text:
            QApplication.clipboard().setText(text)

    def _open_edit_dialog(self, row):
        """Open multi-line edit dialog for a row."""
        model = self.model()

        # Handle proxy model
        source_model = model
        source_row = row
        if hasattr(model, "mapToSource"):
            source_index = model.mapToSource(model.index(row, 0))
            source_row = source_index.row()
            source_model = model.sourceModel()

        row_data = source_model.get_row_data(source_row)
        dialog = StringEditDialog(row_data, self)

        if dialog.exec() == QDialog.Accepted:
            new_text = dialog.get_translated_text()
            source_model.set_translated_text(source_row, new_text)

    def _open_diff_dialog(self, row):
        """Open the diff viewer for the given table row."""
        from gui.diff_viewer import DiffViewerDialog

        model = self.model()
        source_model = model
        source_row = row
        if hasattr(model, "mapToSource"):
            source_index = model.mapToSource(model.index(row, 0))
            source_row = source_index.row()
            source_model = model.sourceModel()

        if not isinstance(source_model, StringTableModel):
            return

        rows = list(source_model._data)
        comparison_data = dict(source_model._diff_data) if source_model._diff_data else None

        dlg = DiffViewerDialog(
            rows=rows,
            initial_row=source_row,
            comparison_data=comparison_data,
            parent=self,
        )
        dlg.translation_updated.connect(
            lambda idx, text: source_model.set_translated_text(idx, text)
        )
        dlg.exec()

    def edit(self, index, trigger, event):
        """Override edit to open dialog on double-click."""
        if trigger == QAbstractItemView.DoubleClicked:
            self._open_edit_dialog(index.row())
            return False  # Don't open default inline editor
        return super().edit(index, trigger, event)


class StringItemDelegate(QStyledItemDelegate):
    """Custom delegate for rendering string cells.

    Pass a *completion_source* callable to enable auto-complete in the
    Translated column.  The callable takes no arguments and returns a list of
    strings used to populate a QCompleter on each editor open.
    """

    def __init__(self, parent=None, completion_source=None):
        super().__init__(parent)
        self._completion_source = completion_source

    def createEditor(self, parent, option, index):
        model = index.model()
        col_name = ""
        if hasattr(model, "COLUMNS") and index.column() < len(model.COLUMNS):
            col_name = model.COLUMNS[index.column()]

        editor = QLineEdit(parent)
        editor.setFrame(False)

        if col_name == "Translated" and self._completion_source is not None:
            words = self._completion_source()
            if words:
                from PySide6.QtCore import Qt as _Qt
                completer = QCompleter(words, editor)
                completer.setCaseSensitivity(_Qt.CaseInsensitive)
                completer.setFilterMode(_Qt.MatchContains)
                completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
                completer.setMaxVisibleItems(10)
                editor.setCompleter(completer)

        return editor

    def setEditorData(self, editor, index):
        if isinstance(editor, QLineEdit):
            value = index.data(Qt.EditRole) or ""
            editor.setText(value)
            editor.selectAll()
        else:
            super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QLineEdit):
            model.setData(index, editor.text(), Qt.EditRole)
        else:
            super().setModelData(editor, model, index)

    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex):
        """Paint cell with custom styling."""
        # Use background color from model if provided
        bg_color = index.data(Qt.BackgroundRole)
        if bg_color:
            painter.save()
            painter.fillRect(option.rect, bg_color)
            painter.restore()

        # Call parent paint
        super().paint(painter, option, index)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex):
        """Provide row height hint for better text wrapping."""
        hint = super().sizeHint(option, index)
        hint.setHeight(max(hint.height(), 40))
        return hint
