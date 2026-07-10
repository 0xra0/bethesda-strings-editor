"""Official-TM miner dialog.

Aligns the base game's English ``.strings`` against an official target-language
``.strings`` (both shipped side-by-side, keyed on identical string IDs, inside
``Starfield - Localization.ba2``) to auto-build an authoritative Translation
Memory + glossary of Bethesda's *canonical* terminology — weapon names, faction
names, UI verbs, quest objectives — with zero AI calls.

The heavy scan/align/mine runs on a :class:`QThread`; classification and
alignment live in the pure, tested :mod:`bethesda_strings.official_tm_miner`.
On import the dialog emits :data:`import_requested` with the :class:`MineResult`;
:mod:`gui.main_window` folds it into the app's :class:`TranslationMemory` and
:class:`Glossary`.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
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
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from bethesda_strings.official_tm_miner import (
    MineResult,
    available_languages,
    mine_official,
)

logger = logging.getLogger(__name__)

# Languages Bethesda actually ships an official localization for (Starfield).
# The community target (uk/cs/ko/ru) is not shipped, so it only appears as a
# *reference* if the user has dropped loose translated files into Data.
_LANG_LABELS = {
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "esmx": "Spanish (LatAm)",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "pl": "Polish",
    "ptbr": "Portuguese (BR)",
    "zhhans": "Chinese (Simpl.)",
    "zhhant": "Chinese (Trad.)",
    "ru": "Russian",
    "uk": "Ukrainian",
    "cs": "Czech",
    "ko": "Korean",
    "pt": "Portuguese",
    "tr": "Turkish",
    "hu": "Hungarian",
}


def _lang_label(code: str) -> str:
    return f"{_LANG_LABELS.get(code, code)} ({code})"


# ── Background scanner/miner ─────────────────────────────────────────────────


class _MineWorker(QObject):
    progress: Signal = Signal(str)
    finished: Signal = Signal(object)   # MineResult
    error:    Signal = Signal(str)

    def __init__(
        self,
        data_dir: str,
        source_lang: str,
        target_lang: str,
        reference_langs: list,
        build_tm: bool,
        build_glossary: bool,
    ) -> None:
        super().__init__()
        self._data_dir = data_dir
        self._source_lang = source_lang
        self._target_lang = target_lang
        self._reference_langs = reference_langs
        self._build_tm = build_tm
        self._build_glossary = build_glossary

    @Slot()
    def run(self) -> None:
        try:
            result = mine_official(
                self._data_dir,
                source_lang=self._source_lang,
                target_lang=self._target_lang,
                reference_langs=self._reference_langs,
                build_tm=self._build_tm,
                build_glossary=self._build_glossary,
                progress=lambda msg: self.progress.emit(msg),
            )
            self.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.error("Official-TM mining failed: %s", exc, exc_info=True)
            self.error.emit(str(exc))


# ── Dialog ───────────────────────────────────────────────────────────────────


class OfficialTMDialog(QDialog):
    """Pick a game Data folder + language pair, mine the official TM/glossary,
    preview it, and import it.  Emits :data:`import_requested` on Import."""

    import_requested: Signal = Signal(object)   # MineResult

    def __init__(
        self,
        parent=None,
        data_dir: str = "",
        source_lang: str = "en",
        target_lang: str = "de",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Mine Official Terminology (TM + Glossary)"))
        self.resize(860, 620)

        self._thread: QThread | None = None
        self._worker: _MineWorker | None = None
        self._result: MineResult | None = None
        self._default_source = source_lang
        self._default_target = target_lang

        layout = QVBoxLayout(self)

        info = QLabel(self.tr(
            "Aligns the base game's official languages (shipped side-by-side in "
            "the localization archive, keyed on identical string IDs) to build an "
            "authoritative Translation Memory + glossary of Bethesda's canonical "
            "terminology — no AI calls. Point this at your game's Data folder."
        ))
        info.setWordWrap(True)
        layout.addWidget(info)

        # Data folder row
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(self.tr("Game Data folder:")))
        self._dir_edit = QLineEdit(data_dir)
        self._dir_edit.editingFinished.connect(self._detect_languages)
        row1.addWidget(self._dir_edit, 1)
        browse = QPushButton(self.tr("Browse…"))
        browse.clicked.connect(self._pick_dir)
        row1.addWidget(browse)
        layout.addLayout(row1)

        # Language selection
        lang_box = QGroupBox(self.tr("Languages"))
        lang_layout = QHBoxLayout(lang_box)
        lang_layout.addWidget(QLabel(self.tr("Source:")))
        self._src_combo = QComboBox()
        self._src_combo.setMinimumWidth(170)
        lang_layout.addWidget(self._src_combo)
        lang_layout.addWidget(QLabel(self.tr("Official target:")))
        self._tgt_combo = QComboBox()
        self._tgt_combo.setMinimumWidth(170)
        lang_layout.addWidget(self._tgt_combo)
        lang_layout.addStretch(1)
        layout.addWidget(lang_box)

        ref_box = QGroupBox(self.tr("Reference languages (annotate glossary, optional)"))
        ref_layout = QHBoxLayout(ref_box)
        ref_layout.addWidget(QLabel(self.tr("Codes (comma-separated):")))
        self._ref_edit = QLineEdit()
        self._ref_edit.setPlaceholderText(self.tr("e.g. pl, ru — a Slavic cross-reference"))
        ref_layout.addWidget(self._ref_edit, 1)
        layout.addWidget(ref_box)

        # Build options
        opts = QHBoxLayout()
        self._chk_tm = QCheckBox(self.tr("Build Translation Memory"))
        self._chk_tm.setChecked(True)
        self._chk_glossary = QCheckBox(self.tr("Build glossary"))
        self._chk_glossary.setChecked(True)
        opts.addWidget(self._chk_tm)
        opts.addWidget(self._chk_glossary)
        opts.addStretch(1)
        self._mine_btn = QPushButton(self.tr("Mine"))
        self._mine_btn.clicked.connect(self._run)
        opts.addWidget(self._mine_btn)
        layout.addLayout(opts)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)     # indeterminate
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)

        # Glossary preview
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            self.tr("Source"), self.tr("Official target"),
            self.tr("Count"), self.tr("Consistency"), self.tr("References"),
        ])
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, 220)
        self._table.setColumnWidth(1, 220)
        self._table.setColumnWidth(2, 60)
        self._table.setColumnWidth(3, 90)
        layout.addWidget(self._table, 1)

        # Buttons
        btns = QHBoxLayout()
        btns.addStretch(1)
        self._import_btn = QPushButton(self.tr("Import into TM && Glossary"))
        self._import_btn.setEnabled(False)
        self._import_btn.clicked.connect(self._do_import)
        btns.addWidget(self._import_btn)
        close_btn = QPushButton(self.tr("Close"))
        close_btn.clicked.connect(self.reject)
        btns.addWidget(close_btn)
        layout.addLayout(btns)

        if data_dir:
            self._detect_languages()

    # ── language detection ────────────────────────────────────────────────────
    def _pick_dir(self) -> None:
        start = self._dir_edit.text() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, self.tr("Select Game Data Folder"), start)
        if d:
            self._dir_edit.setText(d)
            self._detect_languages()

    def _detect_languages(self) -> None:
        path = self._dir_edit.text().strip()
        if not path or not Path(path).is_dir():
            return
        try:
            langs = available_languages(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Language detection failed: %s", exc)
            langs = set()
        if not langs:
            self._summary.setText(self.tr(
                "No official .strings languages found under that folder."
            ))
            return

        ordered = sorted(langs)
        # Source combo — prefer English, then anything present.
        self._src_combo.clear()
        for code in ordered:
            self._src_combo.addItem(_lang_label(code), code)
        self._select_combo(self._src_combo, self._default_source, fallback="en")

        # Target combo — the official localization to align against (not English).
        self._tgt_combo.clear()
        tgt_codes = [c for c in ordered if c != self._src_combo.currentData()]
        for code in tgt_codes:
            self._tgt_combo.addItem(_lang_label(code), code)
        self._select_combo(self._tgt_combo, self._default_target, fallback=None)

        self._summary.setText(self.tr("Found {n} official language(s): {langs}").format(
            n=len(ordered), langs=", ".join(ordered),
        ))

    @staticmethod
    def _select_combo(combo: QComboBox, preferred: str, fallback: str | None) -> None:
        for target in (preferred, fallback):
            if not target:
                continue
            idx = combo.findData(target)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                return
        if combo.count():
            combo.setCurrentIndex(0)

    # ── mining ────────────────────────────────────────────────────────────────
    def _run(self) -> None:
        if self._thread and self._thread.isRunning():
            return
        data_dir = self._dir_edit.text().strip()
        if not data_dir or not Path(data_dir).is_dir():
            QMessageBox.warning(self, self.tr("Mine"),
                                self.tr("Please choose a valid game Data folder."))
            return
        src = self._src_combo.currentData()
        tgt = self._tgt_combo.currentData()
        if not src or not tgt:
            QMessageBox.warning(self, self.tr("Mine"),
                                self.tr("No language pair detected. Pick a Data folder first."))
            return
        if src == tgt:
            QMessageBox.warning(self, self.tr("Mine"),
                                self.tr("Source and target languages must differ."))
            return
        if not self._chk_tm.isChecked() and not self._chk_glossary.isChecked():
            QMessageBox.warning(self, self.tr("Mine"),
                                self.tr("Enable at least one of TM or glossary."))
            return

        refs = [c.strip().lower() for c in self._ref_edit.text().split(",") if c.strip()]

        self._progress.setVisible(True)
        self._mine_btn.setEnabled(False)
        self._import_btn.setEnabled(False)
        self._table.setRowCount(0)
        self._summary.setText(self.tr("Scanning…"))

        self._worker = _MineWorker(
            data_dir, src, tgt, refs,
            self._chk_tm.isChecked(), self._chk_glossary.isChecked(),
        )
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._thread.start()

    @Slot(str)
    def _on_progress(self, msg: str) -> None:
        self._summary.setText(msg)

    def _stop_thread(self) -> None:
        if self._thread:
            self._thread.quit()
            self._thread.wait()
            self._thread = None
        self._worker = None

    @Slot(object)
    def _on_finished(self, result: MineResult) -> None:
        self._progress.setVisible(False)
        self._mine_btn.setEnabled(True)
        self._stop_thread()
        self._result = result
        self._summary.setText(self.tr(
            "{plugins} plugin(s), {aligned} aligned strings → "
            "{tm} TM entries, {gloss} glossary terms "
            "({src} → {tgt})."
        ).format(
            plugins=result.plugin_count,
            aligned=result.aligned_pairs,
            tm=len(result.tm_pairs),
            gloss=len(result.glossary),
            src=result.source_lang,
            tgt=result.target_lang,
        ))
        self._fill_preview(result)
        self._import_btn.setEnabled(bool(result))

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._progress.setVisible(False)
        self._mine_btn.setEnabled(True)
        self._stop_thread()
        self._summary.setText(self.tr("Mining failed."))
        QMessageBox.critical(self, self.tr("Mine"), msg)

    def _fill_preview(self, result: MineResult) -> None:
        # Cap the preview; the full set still imports.
        preview = result.glossary[:500]
        self._table.setRowCount(len(preview))
        for r, cand in enumerate(preview):
            self._table.setItem(r, 0, QTableWidgetItem(cand.source))
            self._table.setItem(r, 1, QTableWidgetItem(cand.target))
            self._table.setItem(r, 2, QTableWidgetItem(str(cand.count)))
            cons = QTableWidgetItem(f"{cand.consistency:.0%}")
            cons.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(r, 3, cons)
            refs = "  ".join(f"{k}: {v}" for k, v in cand.ref.items())
            self._table.setItem(r, 4, QTableWidgetItem(refs))

    # ── import ────────────────────────────────────────────────────────────────
    def _do_import(self) -> None:
        if not self._result:
            return
        self.import_requested.emit(self._result)
        self.accept()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._stop_thread()
        super().closeEvent(event)
