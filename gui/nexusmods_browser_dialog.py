"""
NexusMods API Bridge — browse, download, and import translation mods as TM.

Layout
------
┌─ Search ──────────────────────────────────┬─ Mod detail ──────────────────────────────┐
│  Game  [Starfield ▼]  [translation query] │  Mod name (large)                         │
│  [🔍 Search]                              │  by <author>  ·  category  ·  updated     │
│                                           │  ─────────────────────────────────────── │
│  ┌──────────────────────────────────────┐ │  Summary text (word-wrap)                 │
│  │ Mod name | Author | ↓ | ★ | Updated │ │                                           │
│  │ ...                                  │ │  Files                                    │
│  └──────────────────────────────────────┘ │  ┌──────────────────────────────────────┐│
│                                           │  │ file.ba2 | 1.0 | 4 MB | Main       ││
│                                           │  │ ...                                  ││
│                                           │  └──────────────────────────────────────┘│
│                                           │                                           │
│                                           │  [🌐 Open mod page]                      │
│                                           │  [⬇ Download & Import as TM]             │
│                                           │  [⬇ Download & Merge into Current]       │
└───────────────────────────────────────────┴───────────────────────────────────────────┘
  ── Status bar ──────────────────────────────────────────────────────────────────────────

Keyboard: Enter in search box triggers search; double-click result selects mod.
"""

from __future__ import annotations

import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QProgressBar, QPushButton,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from gui.nexusmods_client import GAMES, NexusClient, NexusModFile, NexusModsError, NexusSearchResult

_STRINGS_EXTS = {".strings", ".dlstrings", ".ilstrings"}


# ── Background workers ────────────────────────────────────────────────────────

class _WorkerSignals(QObject):
    finished = Signal(object)   # result
    error    = Signal(str)
    progress = Signal(int, int) # done, total


class _SearchWorker(QRunnable):
    def __init__(self, client: NexusClient, query: str, game_id: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._client = client
        self._query = query
        self._game_id = game_id
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            results = self._client.search(self._query, self._game_id)
            self.signals.finished.emit(results)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _FilesWorker(QRunnable):
    def __init__(self, client: NexusClient, domain: str, mod_id: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._client = client
        self._domain = domain
        self._mod_id = mod_id
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            files = self._client.mod_files(self._domain, self._mod_id)
            self.signals.finished.emit(files)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _DownloadWorker(QRunnable):
    def __init__(
        self,
        client: NexusClient,
        domain: str,
        mod_id: int,
        mod_name: str,
        nf: NexusModFile,
        dest_dir: Path,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._client = client
        self._domain = domain
        self._mod_id = mod_id
        self._mod_name = mod_name
        self._nf = nf
        self._dest_dir = dest_dir
        self._stop = threading.Event()
        self.signals = _WorkerSignals()

    def cancel(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            url = self._client.download_url(self._domain, self._mod_id, self._nf.file_id)
            if not url:
                # Free account or page-visit restriction — tell the dialog
                self.signals.error.emit(
                    "NO_DIRECT_LINK:" + self._client.mod_page_url(self._domain, self._mod_id)
                )
                return

            dest = self._dest_dir / self._nf.file_name
            self._client.download_file(
                url, dest,
                progress=lambda d, t: self.signals.progress.emit(d, t),
                stop_event=self._stop,
            )
            # Resolve to actual strings files
            strings_paths = _extract_strings(dest)
            self.signals.finished.emit(strings_paths)
        except NexusModsError as exc:
            self.signals.error.emit(str(exc))
        except Exception as exc:
            self.signals.error.emit(f"Unexpected error: {exc}")


def _extract_strings(path: Path) -> List[Path]:
    """Return a list of .strings/.dlstrings/.ilstrings paths from path.

    If path is already a strings file, return [path].
    If it's a zip, extract all strings files to path.parent/{stem}/ and return them.
    Otherwise return [] (BA2 — caller will handle via BA2File picker).
    """
    suffix = path.suffix.lower()
    if suffix in _STRINGS_EXTS:
        return [path]
    if suffix == ".zip":
        out_dir = path.parent / path.stem
        out_dir.mkdir(exist_ok=True)
        found = []
        with zipfile.ZipFile(path, "r") as zf:
            for member in zf.namelist():
                if Path(member).suffix.lower() in _STRINGS_EXTS:
                    extracted = zf.extract(member, out_dir)
                    found.append(Path(extracted))
        return found
    # .ba2, .7z, .rar — return the archive path with a special marker so caller
    # can open the BA2 picker or prompt the user
    return [path]


# ── Main dialog ───────────────────────────────────────────────────────────────

class NexusModsBrowserDialog(QDialog):
    """Browse NexusMods for translation mods and import them as Translation Memory."""

    # Emitted when the user imports a TM; parent connects this to _load_tm_from_memory
    tm_ready = Signal(object, str)   # (TranslationMemory, source_label)
    # Emitted when the user wants to merge into the current table
    merge_requested = Signal(object)  # TranslationMemory

    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("NexusMods Translation Browser"))
        self.setMinimumSize(QSize(1000, 640))
        self.resize(1100, 680)

        self._api_key   = api_key
        self._cache_dir = cache_dir
        self._client: Optional[NexusClient] = None
        self._current_mod: Optional[NexusSearchResult] = None
        self._current_files: List[NexusModFile] = []
        self._active_download: Optional[_DownloadWorker] = None

        if api_key:
            try:
                self._client = NexusClient(api_key)
            except NexusModsError:
                pass

        self._build_ui()
        if not api_key:
            self._set_status(self.tr(
                "⚠  No NexusMods API key configured — go to Settings → NexusMods to add one."
            ), error=True)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 4)
        outer.setSpacing(6)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, stretch=1)

        splitter.addWidget(self._build_search_panel())
        splitter.addWidget(self._build_detail_panel())
        splitter.setSizes([420, 580])

        # ── Progress + status ──────────────────────────────────────────
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setTextVisible(True)
        self._progress.setMaximumHeight(16)
        outer.addWidget(self._progress)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("font-size: 11px;")
        outer.addWidget(self._status_label)

        close_btn = QDialogButtonBox(QDialogButtonBox.Close)
        close_btn.rejected.connect(self.reject)
        outer.addWidget(close_btn)

    def _build_search_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(6)

        # Game selector
        game_row = QHBoxLayout()
        game_row.addWidget(QLabel(self.tr("Game:")))
        self._game_combo = QComboBox()
        for name in GAMES:
            self._game_combo.addItem(name, GAMES[name])
        self._game_combo.setCurrentIndex(0)  # Starfield
        game_row.addWidget(self._game_combo, stretch=1)
        layout.addLayout(game_row)

        # Search bar
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(self.tr("Search: e.g. \"Ukrainian translation\""))
        self._search_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_edit, stretch=1)
        self._search_btn = QPushButton(self.tr("🔍  Search"))
        self._search_btn.setEnabled(bool(self._client))
        self._search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self._search_btn)
        layout.addLayout(search_row)

        # Results table
        self._results_table = QTableWidget(0, 5)
        self._results_table.setHorizontalHeaderLabels(
            [self.tr("Mod Name"), self.tr("Author"), self.tr("↓"), self.tr("★"), self.tr("Updated")]
        )
        hdr = self._results_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._results_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._results_table.setAlternatingRowColors(True)
        self._results_table.verticalHeader().setVisible(False)
        self._results_table.itemSelectionChanged.connect(self._on_result_selected)
        self._results_table.doubleClicked.connect(self._on_result_selected)
        layout.addWidget(self._results_table, stretch=1)

        return panel

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(6)

        # Mod header
        self._mod_name_label = QLabel(self.tr("(select a mod from the list)"))
        font = QFont()
        font.setBold(True)
        font.setPointSize(13)
        self._mod_name_label.setFont(font)
        self._mod_name_label.setWordWrap(True)
        layout.addWidget(self._mod_name_label)

        self._mod_meta_label = QLabel()
        self._mod_meta_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(self._mod_meta_label)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #444;")
        layout.addWidget(sep)

        self._mod_summary = QTextEdit()
        self._mod_summary.setReadOnly(True)
        self._mod_summary.setMaximumHeight(90)
        self._mod_summary.setStyleSheet("font-size: 12px; background: transparent; border: none;")
        layout.addWidget(self._mod_summary)

        layout.addWidget(QLabel(self.tr("Files:")))

        self._files_table = QTableWidget(0, 4)
        self._files_table.setHorizontalHeaderLabels(
            [self.tr("File name"), self.tr("Version"), self.tr("Size"), self.tr("Category")]
        )
        fhdr = self._files_table.horizontalHeader()
        fhdr.setSectionResizeMode(0, QHeaderView.Stretch)
        fhdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        fhdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        fhdr.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._files_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._files_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._files_table.setAlternatingRowColors(True)
        self._files_table.verticalHeader().setVisible(False)
        self._files_table.itemSelectionChanged.connect(self._on_file_selected)
        layout.addWidget(self._files_table, stretch=1)

        # Action buttons
        self._open_page_btn = QPushButton(self.tr("🌐  Open mod page in browser"))
        self._open_page_btn.setEnabled(False)
        self._open_page_btn.clicked.connect(self._open_mod_page)
        layout.addWidget(self._open_page_btn)

        btn_row = QHBoxLayout()
        self._import_tm_btn = QPushButton(self.tr("⬇  Download & Import as TM"))
        self._import_tm_btn.setEnabled(False)
        self._import_tm_btn.setToolTip(self.tr(
            "Download the selected file and load it as a Translation Memory.\n"
            "Known strings will be pre-filled and not retranslated by AI."
        ))
        self._import_tm_btn.clicked.connect(self._download_import_tm)
        btn_row.addWidget(self._import_tm_btn)

        self._merge_btn = QPushButton(self.tr("⬇  Download & Merge into Current"))
        self._merge_btn.setEnabled(False)
        self._merge_btn.setToolTip(self.tr(
            "Download the selected file and apply any matching translations\n"
            "to the currently open file.  Existing translations are preserved."
        ))
        self._merge_btn.clicked.connect(self._download_merge)
        btn_row.addWidget(self._merge_btn)
        layout.addLayout(btn_row)

        self._cancel_btn = QPushButton(self.tr("✕  Cancel download"))
        self._cancel_btn.setVisible(False)
        self._cancel_btn.clicked.connect(self._cancel_download)
        layout.addWidget(self._cancel_btn)

        return panel

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(self, msg: str, error: bool = False) -> None:
        color = "#da3633" if error else "#8b949e"
        self._status_label.setText(f"<span style='color:{color}'>{msg}</span>")

    def _selected_result(self) -> Optional[NexusSearchResult]:
        rows = self._results_table.selectedItems()
        if not rows:
            return None
        row = self._results_table.currentRow()
        item = self._results_table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _selected_file(self) -> Optional[NexusModFile]:
        rows = self._files_table.selectedItems()
        if not rows:
            return None
        row = self._files_table.currentRow()
        item = self._files_table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _game_info(self) -> tuple[str, int]:
        return self._game_combo.currentData()

    # ── Search ────────────────────────────────────────────────────────────────

    @Slot()
    def _do_search(self) -> None:
        if not self._client:
            return
        query = self._search_edit.text().strip()
        if not query:
            return
        _, game_id = self._game_info()
        self._search_btn.setEnabled(False)
        self._results_table.setRowCount(0)
        self._set_status(self.tr("Searching…"))

        worker = _SearchWorker(self._client, query, game_id)
        worker.signals.finished.connect(self._on_search_done)
        worker.signals.error.connect(self._on_search_error)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _on_search_done(self, results) -> None:
        self._search_btn.setEnabled(True)
        if not results:
            self._set_status(self.tr("No results found."))
            return
        self._populate_results(results)
        self._set_status(self.tr(f"{len(results)} result(s)"))

    @Slot(str)
    def _on_search_error(self, msg: str) -> None:
        self._search_btn.setEnabled(True)
        self._set_status(self.tr(f"Search failed: {msg}"), error=True)

    def _populate_results(self, results: list) -> None:
        self._results_table.setRowCount(len(results))
        for i, r in enumerate(results):
            name_item = QTableWidgetItem(r.name)
            name_item.setData(Qt.UserRole, r)
            self._results_table.setItem(i, 0, name_item)
            self._results_table.setItem(i, 1, QTableWidgetItem(r.author))
            self._results_table.setItem(i, 2, _num_item(r.downloads))
            self._results_table.setItem(i, 3, _num_item(r.endorsements))
            ts = datetime.fromtimestamp(r.updated_ts, tz=timezone.utc) if r.updated_ts else None
            date_str = ts.strftime("%Y-%m-%d") if ts else "—"
            self._results_table.setItem(i, 4, QTableWidgetItem(date_str))
        self._results_table.resizeRowsToContents()

    # ── Mod selection ─────────────────────────────────────────────────────────

    @Slot()
    def _on_result_selected(self) -> None:
        result = self._selected_result()
        if result is None or result == self._current_mod:
            return
        self._current_mod = result
        self._show_mod_detail(result)
        self._files_table.setRowCount(0)
        self._import_tm_btn.setEnabled(False)
        self._merge_btn.setEnabled(False)
        self._open_page_btn.setEnabled(True)

        if not self._client:
            return
        domain, _ = self._game_info()
        worker = _FilesWorker(self._client, domain, result.mod_id)
        worker.signals.finished.connect(self._on_files_done)
        worker.signals.error.connect(lambda e: self._set_status(f"Files: {e}", error=True))
        QThreadPool.globalInstance().start(worker)

    def _show_mod_detail(self, r: NexusSearchResult) -> None:
        self._mod_name_label.setText(r.name)
        ts = datetime.fromtimestamp(r.updated_ts, tz=timezone.utc) if r.updated_ts else None
        date_str = ts.strftime("%Y-%m-%d") if ts else "—"
        self._mod_meta_label.setText(
            f"by {r.author}  ·  {r.category}  ·  "
            f"↓ {r.downloads:,}  ·  ★ {r.endorsements:,}  ·  updated {date_str}"
        )
        self._mod_summary.setPlainText(r.summary)

    @Slot(object)
    def _on_files_done(self, files) -> None:
        self._current_files = files
        self._files_table.setRowCount(len(files))
        for i, f in enumerate(files):
            name_item = QTableWidgetItem(f.file_name)
            name_item.setData(Qt.UserRole, f)
            # Highlight likely translation files
            if f.likely_translation:
                name_item.setForeground(QColor("#3fb950"))
            self._files_table.setItem(i, 0, name_item)
            self._files_table.setItem(i, 1, QTableWidgetItem(f.version))
            size_str = f"{f.size_kb / 1024:.1f} MB" if f.size_kb > 1024 else f"{f.size_kb} KB"
            self._files_table.setItem(i, 2, QTableWidgetItem(size_str))
            self._files_table.setItem(i, 3, QTableWidgetItem(f.category))
        self._files_table.resizeRowsToContents()
        self._set_status(self.tr(f"{len(files)} file(s) — translation-related files shown in green"))

    @Slot()
    def _on_file_selected(self) -> None:
        nf = self._selected_file()
        enabled = nf is not None and self._client is not None
        self._import_tm_btn.setEnabled(enabled)
        self._merge_btn.setEnabled(enabled)

    # ── Mod page ──────────────────────────────────────────────────────────────

    @Slot()
    def _open_mod_page(self) -> None:
        if self._current_mod and self._client:
            domain, _ = self._game_info()
            url = self._client.mod_page_url(domain, self._current_mod.mod_id)
            QDesktopServices.openUrl(url)

    # ── Download ──────────────────────────────────────────────────────────────

    def _start_download(self, on_done_action: str) -> None:
        if not self._client or not self._current_mod:
            return
        nf = self._selected_file()
        if not nf:
            return

        domain, _ = self._game_info()
        mod_name = self._current_mod.name
        dest_dir = self._cache_dir / "nexus_cache" / str(self._current_mod.mod_id)

        self._import_tm_btn.setEnabled(False)
        self._merge_btn.setEnabled(False)
        self._cancel_btn.setVisible(True)
        self._progress.setVisible(True)
        self._progress.setRange(0, 0)  # indeterminate until first progress signal
        self._set_status(self.tr(f"Downloading {nf.file_name}…"))

        worker = _DownloadWorker(self._client, domain, self._current_mod.mod_id, mod_name, nf, dest_dir)
        worker.signals.progress.connect(self._on_download_progress)
        worker.signals.error.connect(self._on_download_error)
        worker.signals.finished.connect(lambda paths: self._on_download_done(paths, on_done_action))
        self._active_download = worker
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _download_import_tm(self) -> None:
        self._start_download("import_tm")

    @Slot()
    def _download_merge(self) -> None:
        self._start_download("merge")

    @Slot()
    def _cancel_download(self) -> None:
        if self._active_download:
            self._active_download.cancel()
        self._reset_download_ui()
        self._set_status(self.tr("Download cancelled."))

    @Slot(int, int)
    def _on_download_progress(self, done: int, total: int) -> None:
        if total > 0:
            self._progress.setRange(0, total)
            self._progress.setValue(done)
            mb_done = done / 1_048_576
            mb_total = total / 1_048_576
            self._set_status(self.tr(f"Downloading… {mb_done:.1f} / {mb_total:.1f} MB"))
        else:
            self._progress.setRange(0, 0)

    @Slot(str)
    def _on_download_error(self, msg: str) -> None:
        self._reset_download_ui()
        if msg.startswith("NO_DIRECT_LINK:"):
            page_url = msg[len("NO_DIRECT_LINK:"):]
            self._set_status(
                self.tr(
                    "⚠  Direct download requires NexusMods Premium.  "
                    "The mod page has been opened in your browser — "
                    "download manually and use <b>Translation → Load Translation Memory…</b>."
                ),
                error=True,
            )
            QDesktopServices.openUrl(page_url)
        else:
            self._set_status(self.tr(f"Download failed: {msg}"), error=True)

    @Slot(object)
    def _on_download_done(self, paths: list, action: str) -> None:
        self._reset_download_ui()
        if not paths:
            self._set_status(self.tr("No .strings files found in the downloaded archive."), error=True)
            return

        # If it's a BA2 or unsupported archive, we can only offer open-in-editor
        from gui.nexusmods_client import CONTAINER_EXTS
        if paths[0].suffix.lower() in (CONTAINER_EXTS - {".zip"}):
            self._set_status(self.tr(
                f"Downloaded: {paths[0].name} — open it in the editor via File → Open "
                "to browse its contents."
            ))
            return

        # Load all strings files into one TM
        from gui.translation_memory import TranslationMemory
        tm = TranslationMemory()
        loaded = 0
        for p in paths:
            try:
                loaded += tm.load_strings_file(p)
            except Exception as exc:
                self._set_status(self.tr(f"Could not read {p.name}: {exc}"), error=True)
                return

        label = f"NexusMods: {self._current_mod.name}" if self._current_mod else "NexusMods"
        if action == "import_tm":
            self.tm_ready.emit(tm, label)
            self._set_status(self.tr(
                f"✓  TM imported: {loaded} string(s) from {len(paths)} file(s).  "
                "Known strings will be skipped during AI translation."
            ))
        else:  # merge
            self.merge_requested.emit(tm)
            self._set_status(self.tr(
                f"✓  Merge requested: {loaded} string(s) from {len(paths)} file(s)."
            ))

    def _reset_download_ui(self) -> None:
        self._progress.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._active_download = None
        has_file = self._selected_file() is not None
        self._import_tm_btn.setEnabled(has_file)
        self._merge_btn.setEnabled(has_file)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _num_item(n: int) -> QTableWidgetItem:
    """Right-aligned number table cell."""
    item = QTableWidgetItem(f"{n:,}")
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return item
