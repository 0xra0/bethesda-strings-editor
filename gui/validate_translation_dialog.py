"""Validate a folder of translated string files against the English sources.

Finds files/IDs that will show ``<Error: Unknown lstring ID …>`` in-game:
missing files, empty (header-only) files, unparseable files, and files missing
some IDs the source has.  Sources are read from the game's ``.ba2`` archives
(where the base game keeps them) and/or loose ``*_<lang>.*`` files.

Classification is delegated to the pure, tested
:mod:`bethesda_strings.strings_validator`; this dialog only gathers the ID
indexes and renders the result.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
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
)

from bethesda_strings.ba2_handler import BA2File
from bethesda_strings.core import BethesdaStringFile, format_string_id
from bethesda_strings.strings_validator import (
    FileReport,
    summarize,
    validate_translation,
)

logger = logging.getLogger(__name__)

_STRING_EXTS = ("strings", "dlstrings", "ilstrings")

_STATUS_COLOR = {
    "missing": "#e06c75",
    "parse_error": "#e06c75",
    "empty": "#e5a03a",
    "incomplete": "#e5c07b",
    "orphan": "#5c6370",
    "ok": "#98c379",
}


def _split_key(filename: str, lang: str):
    """'starfield_uk.ILSTRINGS' , 'uk' -> ('starfield', 'ilstrings') or None."""
    name = Path(filename).name
    dot = name.rfind(".")
    if dot < 0:
        return None
    ext = name[dot + 1:].lower()
    if ext not in _STRING_EXTS:
        return None
    stem = name[:dot].lower()
    suffix = f"_{lang.lower()}"
    if not stem.endswith(suffix):
        return None
    return (stem[: -len(suffix)], ext)


def _ids_from_buffer(raw: bytes, ext: str):
    try:
        sf = BethesdaStringFile(buffer=raw, file_extension=ext)
        return frozenset(s.id for s in sf.strings)
    except Exception as e:  # noqa: BLE001
        logger.warning("Validator: parse failed (%s): %s", ext, e)
        return None


class ValidateTranslationDialog(QDialog):
    def __init__(self, parent=None, translated_dir: str = "",
                 source_lang: str = "en", target_lang: str = "uk") -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Validate Translation Folder"))
        self.resize(900, 600)
        self._reports: list[FileReport] = []

        layout = QVBoxLayout(self)

        info = QLabel(self.tr(
            "Compares your translated string files against the English sources and "
            "lists everything that will show 'Unknown lstring ID' in-game."
        ))
        info.setWordWrap(True)
        layout.addWidget(info)

        # Translated folder row
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(self.tr("Translated folder:")))
        self._trans_edit = QLineEdit(translated_dir)
        row1.addWidget(self._trans_edit, 1)
        b1 = QPushButton(self.tr("Browse…"))
        b1.clicked.connect(lambda: self._pick(self._trans_edit))
        row1.addWidget(b1)
        layout.addLayout(row1)

        # Source (game Data) folder row
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(self.tr("Game Data folder (English source):")))
        self._data_edit = QLineEdit()
        row2.addWidget(self._data_edit, 1)
        b2 = QPushButton(self.tr("Browse…"))
        b2.clicked.connect(lambda: self._pick(self._data_edit))
        row2.addWidget(b2)
        layout.addLayout(row2)

        # Languages row
        row3 = QHBoxLayout()
        row3.addWidget(QLabel(self.tr("Source lang:")))
        self._src_lang = QLineEdit(source_lang)
        self._src_lang.setMaximumWidth(70)
        row3.addWidget(self._src_lang)
        row3.addWidget(QLabel(self.tr("Target lang:")))
        self._tgt_lang = QLineEdit(target_lang)
        self._tgt_lang.setMaximumWidth(70)
        row3.addWidget(self._tgt_lang)
        row3.addStretch(1)
        self._validate_btn = QPushButton(self.tr("Validate"))
        self._validate_btn.clicked.connect(self._run)
        row3.addWidget(self._validate_btn)
        layout.addLayout(row3)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            self.tr("Status"), self.tr("File"), self.tr("Source"),
            self.tr("Translated"), self.tr("Detail"),
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 100)
        self._table.setColumnWidth(1, 240)
        self._table.setColumnWidth(2, 70)
        self._table.setColumnWidth(3, 80)
        layout.addWidget(self._table, 1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self._export_btn = QPushButton(self.tr("Export CSV…"))
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._export_csv)
        btns.addWidget(self._export_btn)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.accept)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

    # ── helpers ──────────────────────────────────────────────────────────────
    def _pick(self, edit: QLineEdit) -> None:
        start = edit.text() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, self.tr("Select Folder"), start)
        if d:
            edit.setText(d)

    def _scan_loose(self, folder: Path, lang: str) -> dict:
        index: dict = {}
        if not folder.is_dir():
            return index
        for p in folder.iterdir():
            if not p.is_file():
                continue
            key = _split_key(p.name, lang)
            if key is None:
                continue
            try:
                sf = BethesdaStringFile(str(p))
                index[key] = frozenset(s.id for s in sf.strings)
            except Exception as e:  # noqa: BLE001
                logger.warning("Validator: cannot parse %s: %s", p.name, e)
                index[key] = None
        return index

    def _scan_ba2s(self, data_dir: Path, lang: str) -> dict:
        index: dict = {}
        if not data_dir.is_dir():
            return index
        seen = set()
        for ba2 in sorted(data_dir.glob("*.ba2")) + sorted(data_dir.glob("*.BA2")):
            if ba2 in seen:
                continue
            seen.add(ba2)
            try:
                ba = BA2File(str(ba2))
            except Exception:  # noqa: BLE001
                continue
            try:
                names = ba.list_strings_files()
                for name in names:
                    key = _split_key(name, lang)
                    if key is None:
                        continue
                    try:
                        raw = ba.extract(name)
                    except Exception:  # noqa: BLE001
                        continue
                    ids = _ids_from_buffer(raw, key[1])
                    # First archive wins; don't let a later empty override a good set.
                    if key not in index or (index[key] is None and ids):
                        index[key] = ids
            finally:
                try:
                    ba.close()
                except Exception:  # noqa: BLE001
                    pass
        return index

    def _run(self) -> None:
        trans_dir = Path(self._trans_edit.text().strip())
        data_dir = Path(self._data_edit.text().strip()) if self._data_edit.text().strip() else None
        src_lang = self._src_lang.text().strip() or "en"
        tgt_lang = self._tgt_lang.text().strip() or "uk"

        if not trans_dir.is_dir():
            QMessageBox.warning(self, self.tr("Validate"),
                                self.tr("Please choose a valid translated folder."))
            return

        # Source index: BA2 archives + loose source files (loose overrides BA2).
        source: dict = {}
        if data_dir:
            source.update(self._scan_ba2s(data_dir, src_lang))
            loose_src_dir = data_dir / "Strings" if (data_dir / "Strings").is_dir() else data_dir
            for k, v in self._scan_loose(loose_src_dir, src_lang).items():
                source[k] = v
        # Also consider source files sitting in the translated folder.
        for k, v in self._scan_loose(trans_dir, src_lang).items():
            source.setdefault(k, v)

        translated = self._scan_loose(trans_dir, tgt_lang)

        if not source:
            QMessageBox.warning(self, self.tr("Validate"), self.tr(
                "No {lang} source string files found.\nPoint 'Game Data folder' at "
                "your Starfield Data directory (with the .ba2 archives)."
            ).format(lang=src_lang))
            return

        self._reports = validate_translation(source, translated)
        self._fill(self._reports)

    def _fill(self, reports: list[FileReport]) -> None:
        counts = summarize(reports)
        will_error = sum(1 for r in reports if r.will_error_in_game)
        self._summary.setText(self.tr(
            "{err} file(s) will error in-game — "
            "missing: {missing}, empty: {empty}, unparseable: {parse}, "
            "incomplete: {inc}.  OK: {ok}, orphan: {orphan}."
        ).format(
            err=will_error,
            missing=counts.get("missing", 0),
            empty=counts.get("empty", 0),
            parse=counts.get("parse_error", 0),
            inc=counts.get("incomplete", 0),
            ok=counts.get("ok", 0),
            orphan=counts.get("orphan", 0),
        ))

        self._table.setRowCount(len(reports))
        for r, rep in enumerate(reports):
            status = QTableWidgetItem(rep.status)
            color = _STATUS_COLOR.get(rep.status)
            if color:
                status.setForeground(Qt.GlobalColor.black)
                from PySide6.QtGui import QBrush, QColor
                status.setBackground(QBrush(QColor(color)))
            self._table.setItem(r, 0, status)
            self._table.setItem(r, 1, QTableWidgetItem(rep.filename))
            self._table.setItem(r, 2, QTableWidgetItem(str(rep.source_count)))
            self._table.setItem(r, 3, QTableWidgetItem(str(rep.translated_count)))
            detail = rep.detail
            if rep.missing_ids:
                preview = ", ".join(format_string_id(i) for i in rep.missing_ids[:8])
                more = "…" if len(rep.missing_ids) > 8 else ""
                detail += f"  [{preview}{more}]"
            self._table.setItem(r, 4, QTableWidgetItem(detail))
        self._export_btn.setEnabled(bool(reports))

    def _export_csv(self) -> None:
        if not self._reports:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, self.tr("Export CSV"), str(Path.home() / "translation_validation.csv"),
            self.tr("CSV Files (*.csv)"),
        )
        if not path:
            return
        import csv
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["status", "file", "source_count", "translated_count",
                            "missing_ids", "extra_ids", "detail"])
                for rep in self._reports:
                    w.writerow([
                        rep.status, rep.filename, rep.source_count, rep.translated_count,
                        " ".join(format_string_id(i) for i in rep.missing_ids),
                        " ".join(format_string_id(i) for i in rep.extra_ids),
                        rep.detail,
                    ])
            QMessageBox.information(self, self.tr("Export CSV"),
                                    self.tr("Saved report to {path}").format(path=path))
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, self.tr("Export CSV"),
                                 self.tr("Failed to write CSV:\n{err}").format(err=e))
