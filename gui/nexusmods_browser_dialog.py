"""
NexusMods API Bridge — browse, download, and import translation mods as TM.

Layout
------
┌─ Search ─────────────────────────────────────┬─ Mod detail ────────────────────────────┐
│  Game  [Starfield ▼]  [search query…] [🔍]   │  Mod name (large)                       │
│  N results                                    │  by <author>  ·  updated                │
│  ┌──────┐ ┌──────┐ ┌──────┐                  │  ─────────────────────────────────────  │
│  │thumb │ │thumb │ │thumb │                  │  Summary text (word-wrap)               │
│  │      │ │      │ │      │                  │                                         │
│  │Name  │ │Name  │ │Name  │                  │  Files                                  │
│  │auth  │ │auth  │ │auth  │                  │  ┌──────────────────────────────────┐  │
│  │★ ↓   │ │★ ↓   │ │★ ↓   │                  │  │ file.ba2 | 1.0 | 4 MB | Main   │  │
│  └──────┘ └──────┘ └──────┘                  │  └──────────────────────────────────┘  │
│  ...                                          │  [🌐 Open mod page]                    │
│                                               │  [⬇ Download & Import as TM]           │
└───────────────────────────────────────────────┴─────────────────────────────────────────┘
  ── Status bar ──────────────────────────────────────────────────────────────────────────

Keyboard: Enter in search box triggers search; click card selects mod.
"""

from __future__ import annotations

import hashlib
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests as _requests

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QProgressBar, QPushButton, QScrollArea,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

from gui.nexusmods_client import (
    CONTAINER_EXTS, GAMES, NexusClient, NexusModFile, NexusModsError,
    NexusSearchResult, PLUGIN_EXTS, STRINGS_EXTS,
)

_STRINGS_EXTS = STRINGS_EXTS
_PLUGIN_EXTS  = PLUGIN_EXTS

_CARD_W   = 215
_CARD_IMG = 121   # 16:9
_COLS     = 3


# ── Background workers ────────────────────────────────────────────────────────

class _WorkerSignals(QObject):
    finished = Signal(object)   # result
    error    = Signal(str)
    progress = Signal(int, int) # done, total


class _SearchWorker(QRunnable):
    def __init__(self, client: NexusClient, query: str, game_id: int, game_domain: str = "") -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._client = client
        self._query = query
        self._game_id = game_id
        self._game_domain = game_domain
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            results = self._client.search(self._query, self._game_id, game_domain=self._game_domain)
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
        self._client   = client
        self._domain   = domain
        self._mod_id   = mod_id
        self._mod_name = mod_name
        self._nf       = nf
        self._dest_dir = dest_dir
        self.signals   = _WorkerSignals()
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self) -> None:
        try:
            url = self._client.download_url(self._domain, self._mod_id, self._nf.file_id)

            self._dest_dir.mkdir(parents=True, exist_ok=True)
            dest = self._dest_dir / self._nf.file_name

            self._client.stream_download(
                url, dest,
                progress=lambda done, total: self.signals.progress.emit(done, total),
                cancelled=self._cancelled,
            )
            if self._cancelled.is_set():
                dest.unlink(missing_ok=True)
                return

            paths = _extract_strings(dest)
            self.signals.finished.emit(paths)
        except Exception as exc:
            self.signals.error.emit(str(exc))


class _ModInfoWorker(QRunnable):
    def __init__(self, client: NexusClient, domain: str, mod_id: int) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._client = client
        self._domain = domain
        self._mod_id = mod_id
        self.signals = _WorkerSignals()

    def run(self) -> None:
        try:
            data = self._client.get_mod(self._domain, self._mod_id)
            self.signals.finished.emit(data)
        except Exception as exc:
            self.signals.error.emit(str(exc))


# ── Thumbnail loading ─────────────────────────────────────────────────────────

class _ThumbnailSignals(QObject):
    loaded = Signal(object, bytes)   # (_ModCard, raw image bytes)


class _ThumbnailLoader(QRunnable):
    def __init__(self, url: str, card: "_ModCard", cache_dir: Path) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._url      = url
        self._card     = card
        self._cache_dir = cache_dir
        self.signals   = _ThumbnailSignals()

    def run(self) -> None:
        key        = hashlib.md5(self._url.encode()).hexdigest()
        cache_file = self._cache_dir / "thumb_cache" / key
        try:
            if cache_file.exists():
                data = cache_file.read_bytes()
            else:
                resp = _requests.get(self._url, timeout=10)
                if not resp.ok:
                    return
                data = resp.content
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(data)
            self.signals.loaded.emit(self._card, data)
        except Exception:
            pass


class _BannerSignals(QObject):
    loaded = Signal(int, bytes)   # (mod_id, raw image bytes)


class _BannerLoader(QRunnable):
    def __init__(self, url: str, mod_id: int, cache_dir: Path) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._url      = url
        self._mod_id   = mod_id
        self._cache_dir = cache_dir
        self.signals   = _BannerSignals()

    def run(self) -> None:
        key        = hashlib.md5(self._url.encode()).hexdigest()
        cache_file = self._cache_dir / "banner_cache" / key
        try:
            if cache_file.exists():
                data = cache_file.read_bytes()
            else:
                resp = _requests.get(self._url, timeout=15)
                if not resp.ok:
                    return
                data = resp.content
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_bytes(data)
            self.signals.loaded.emit(self._mod_id, data)
        except Exception:
            pass


# ── Mod card widget ───────────────────────────────────────────────────────────

class _ModCard(QFrame):
    clicked = Signal(object)   # NexusSearchResult

    _STYLE_NORMAL = (
        "QFrame#ModCard { border: 1px solid #2d2d3d; border-radius: 4px;"
        " background: #16161e; }"
    )
    _STYLE_SELECTED = (
        "QFrame#ModCard { border: 2px solid #DA8B15; border-radius: 4px;"
        " background: #1e1b0e; }"
    )

    def __init__(self, result: NexusSearchResult, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._result = result
        self.setFixedWidth(_CARD_W)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("ModCard")
        self.setStyleSheet(self._STYLE_NORMAL)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(0, 0, 0, 6)
        vl.setSpacing(3)

        # Thumbnail
        self._thumb_lbl = QLabel()
        self._thumb_lbl.setFixedSize(_CARD_W, _CARD_IMG)
        self._thumb_lbl.setAlignment(Qt.AlignCenter)
        self._thumb_lbl.setStyleSheet(
            "background: #0d0d1a; border-top-left-radius: 4px;"
            " border-top-right-radius: 4px;"
        )
        vl.addWidget(self._thumb_lbl)

        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(8, 2, 8, 0)
        bl.setSpacing(2)

        name_lbl = QLabel(result.name)
        name_lbl.setWordWrap(True)
        name_lbl.setMaximumHeight(40)
        f = QFont()
        f.setBold(True)
        f.setPointSize(9)
        name_lbl.setFont(f)
        bl.addWidget(name_lbl)

        if result.author:
            by_lbl = QLabel(f"by {result.author}")
            by_lbl.setStyleSheet("color: #8b949e; font-size: 9px;")
            by_lbl.setMaximumHeight(16)
            bl.addWidget(by_lbl)

        def _fmt(n: int) -> str:
            return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

        stats_parts = []
        if result.endorsements:
            stats_parts.append(f"👍 {_fmt(result.endorsements)}")
        if result.downloads:
            stats_parts.append(f"↓ {_fmt(result.downloads)}")
        if stats_parts:
            stats_lbl = QLabel("  ".join(stats_parts))
            stats_lbl.setStyleSheet("color: #8b949e; font-size: 9px;")
            stats_lbl.setMaximumHeight(16)
            bl.addWidget(stats_lbl)

        if result.updated_ts:
            ts = datetime.fromtimestamp(result.updated_ts, tz=timezone.utc)
            date_lbl = QLabel(ts.strftime("%d %b %Y"))
            date_lbl.setStyleSheet("color: #6e7681; font-size: 9px;")
            date_lbl.setMaximumHeight(14)
            bl.addWidget(date_lbl)

        vl.addWidget(body)

    def set_thumbnail(self, pixmap: QPixmap) -> None:
        scaled = pixmap.scaled(
            _CARD_W, _CARD_IMG,
            Qt.KeepAspectRatioByExpanding,
            Qt.SmoothTransformation,
        )
        x = (scaled.width()  - _CARD_W)   // 2
        y = (scaled.height() - _CARD_IMG) // 2
        self._thumb_lbl.setPixmap(scaled.copy(x, y, _CARD_W, _CARD_IMG))

    def set_selected(self, selected: bool) -> None:
        self.setStyleSheet(self._STYLE_SELECTED if selected else self._STYLE_NORMAL)

    @property
    def result(self) -> NexusSearchResult:
        return self._result

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._result)
        super().mousePressEvent(event)


# ── Misc helpers ──────────────────────────────────────────────────────────────

def _extract_from_archive(path: Path, out_dir: Path, wanted_exts: set) -> List[Path]:
    """Extract files whose suffix is in *wanted_exts* from any supported archive.

    Supports .zip (stdlib), .7z (py7zr library or 7z CLI), and .rar (rarfile
    library or unrar/7z CLI).  Returns a list of extracted file paths.
    """
    import subprocess
    suffix = path.suffix.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    found: List[Path] = []

    if suffix == ".zip":
        with zipfile.ZipFile(path, "r") as zf:
            for member in zf.namelist():
                if Path(member).suffix.lower() in wanted_exts:
                    extracted = zf.extract(member, out_dir)
                    found.append(Path(extracted))

    elif suffix == ".7z":
        try:
            import py7zr  # type: ignore[import-untyped]
            with py7zr.SevenZipFile(path, mode="r") as archive:
                targets = [n for n in archive.getnames()
                           if Path(n).suffix.lower() in wanted_exts]
                if targets:
                    archive.extract(path=out_dir, targets=targets)
                    for t in targets:
                        p = out_dir / t
                        if p.exists():
                            found.append(p)
        except ImportError:
            # Fall back to 7z CLI (p7zip package)
            try:
                result = subprocess.run(
                    ["7z", "e", str(path), "-y", f"-o{out_dir}",
                     *[f"*{ext}" for ext in wanted_exts]],
                    capture_output=True, timeout=120,
                )
                if result.returncode == 0:
                    found = [p for p in out_dir.iterdir()
                             if p.suffix.lower() in wanted_exts]
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    elif suffix == ".rar":
        try:
            import rarfile  # type: ignore[import-untyped]
            with rarfile.RarFile(path) as rf:
                for member in rf.namelist():
                    if Path(member).suffix.lower() in wanted_exts:
                        rf.extract(member, out_dir)
                        found.append(out_dir / member)
        except ImportError:
            try:
                result = subprocess.run(
                    ["7z", "e", str(path), "-y", f"-o{out_dir}",
                     *[f"*{ext}" for ext in wanted_exts]],
                    capture_output=True, timeout=120,
                )
                if result.returncode == 0:
                    found = [p for p in out_dir.iterdir()
                             if p.suffix.lower() in wanted_exts]
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    return found


def _extract_strings(path: Path) -> List[Path]:
    """Return .strings/.dlstrings/.ilstrings paths from path or archive.

    If path is already a strings file, return [path].
    If it's a zip/7z/rar, extract matching files and return them.
    Otherwise return [path] (BA2 — caller handles via BA2File picker).
    """
    suffix = path.suffix.lower()
    if suffix in _STRINGS_EXTS:
        return [path]
    if suffix in {".zip", ".7z", ".rar"}:
        out_dir = path.parent / path.stem
        return _extract_from_archive(path, out_dir, _STRINGS_EXTS) or [path]
    return [path]


def _extract_plugins(path: Path) -> List[Path]:
    """Return .esp/.esm/.esl files from path or archive."""
    suffix = path.suffix.lower()
    if suffix in _PLUGIN_EXTS:
        return [path]
    if suffix in {".zip", ".7z", ".rar"}:
        out_dir = path.parent / path.stem
        return _extract_from_archive(path, out_dir, _PLUGIN_EXTS)
    return []


# ── Main dialog ───────────────────────────────────────────────────────────────

class NexusModsBrowserDialog(QDialog):
    """Browse NexusMods for translation mods and import them as Translation Memory."""

    tm_ready          = Signal(object, str)  # (TranslationMemory, source_label)
    merge_requested   = Signal(object)      # TranslationMemory
    open_file_requested = Signal(object)    # Path — open downloaded plugin in editor

    def __init__(
        self,
        api_key: str,
        cache_dir: Path,
        cookies_file: str = "",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("NexusMods Translation Browser"))
        self.setMinimumSize(QSize(1100, 640))
        self.resize(1300, 750)

        self._api_key          = api_key
        self._cache_dir        = cache_dir
        self._client: Optional[NexusClient] = None
        self._current_mod: Optional[NexusSearchResult] = None
        self._current_files: List[NexusModFile] = []
        self._active_download: Optional[_DownloadWorker] = None
        self._cards: List[_ModCard] = []
        self._selected_card: Optional[_ModCard] = None
        self._banner_mod_id: int = -1

        if api_key:
            try:
                self._client = NexusClient(api_key, cookies_file=cookies_file)
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
        splitter.setSizes([700, 600])

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
        self._game_combo.setCurrentIndex(0)
        game_row.addWidget(self._game_combo, stretch=1)
        layout.addLayout(game_row)

        # Search bar
        search_row = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(self.tr('Search: e.g. "Ukrainian translation"'))
        self._search_edit.returnPressed.connect(self._do_search)
        search_row.addWidget(self._search_edit, stretch=1)
        self._search_btn = QPushButton(self.tr("🔍  Search"))
        self._search_btn.setEnabled(bool(self._client))
        self._search_btn.clicked.connect(self._do_search)
        search_row.addWidget(self._search_btn)
        layout.addLayout(search_row)

        # Result count label
        self._results_count_lbl = QLabel()
        self._results_count_lbl.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(self._results_count_lbl)

        # Card grid inside scroll area
        self._card_container = QWidget()
        self._card_grid = QGridLayout(self._card_container)
        self._card_grid.setSpacing(8)
        self._card_grid.setContentsMargins(4, 4, 4, 4)
        self._card_grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._card_container)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(scroll, stretch=1)

        return panel

    def _build_detail_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 0, 0, 0)
        layout.setSpacing(6)

        # Banner image — populated from the full mod info API when a card is clicked
        self._banner_label = QLabel()
        self._banner_label.setFixedHeight(180)
        self._banner_label.setAlignment(Qt.AlignCenter)
        self._banner_label.setStyleSheet("background: #0d0d1a; border-radius: 4px;")
        layout.addWidget(self._banner_label)

        self._mod_name_label = QLabel(self.tr("(click a mod card)"))
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

        self._open_page_btn = QPushButton(self.tr("🌐  Open mod page in browser"))
        self._open_page_btn.setEnabled(False)
        self._open_page_btn.clicked.connect(self._open_mod_page)
        layout.addWidget(self._open_page_btn)

        self._open_plugin_btn = QPushButton(self.tr("⬇  Download & Open in Editor"))
        self._open_plugin_btn.setEnabled(False)
        self._open_plugin_btn.setToolTip(self.tr(
            "Download the selected .esp/.esm/.esl file (or zip containing one)\n"
            "and open it automatically in the editor."
        ))
        self._open_plugin_btn.clicked.connect(self._download_open_plugin)
        layout.addWidget(self._open_plugin_btn)

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
        game_domain, game_id = self._game_info()

        # Clear existing cards
        while self._card_grid.count():
            item = self._card_grid.takeAt(0)
            w = item.widget() if item else None
            if w:
                w.deleteLater()
        self._cards.clear()
        self._selected_card = None
        self._results_count_lbl.setText("")

        self._search_btn.setEnabled(False)
        self._set_status(self.tr("Searching…"))

        worker = _SearchWorker(self._client, query, game_id, game_domain)
        worker.signals.finished.connect(self._on_search_done)
        worker.signals.error.connect(self._on_search_error)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _on_search_done(self, results) -> None:
        self._search_btn.setEnabled(True)
        if not results:
            self._set_status(self.tr("No results found."))
            self._results_count_lbl.setText(self.tr("0 results"))
            return
        self._populate_results(results)
        n = len(results)
        self._results_count_lbl.setText(self.tr(f"{n} result{'s' if n != 1 else ''}"))
        self._set_status("")

    @Slot(str)
    def _on_search_error(self, msg: str) -> None:
        self._search_btn.setEnabled(True)
        self._set_status(self.tr(f"Search failed: {msg}"), error=True)

    def _populate_results(self, results: list) -> None:
        for i, r in enumerate(results):
            card = _ModCard(r, self._card_container)
            card.clicked.connect(self._on_card_clicked)
            self._card_grid.addWidget(card, i // _COLS, i % _COLS)
            self._cards.append(card)

            if r.picture_url:
                loader = _ThumbnailLoader(r.picture_url, card, self._cache_dir)
                loader.signals.loaded.connect(self._on_thumbnail_loaded)
                QThreadPool.globalInstance().start(loader)

    @Slot(object, bytes)
    def _on_thumbnail_loaded(self, card: _ModCard, data: bytes) -> None:
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        if not pixmap.isNull():
            card.set_thumbnail(pixmap)

    # ── Mod selection ─────────────────────────────────────────────────────────

    @Slot(object)
    def _on_card_clicked(self, result: NexusSearchResult) -> None:
        # Update selection highlight
        if self._selected_card:
            self._selected_card.set_selected(False)
        for card in self._cards:
            if card.result is result:
                card.set_selected(True)
                self._selected_card = card
                break

        if result == self._current_mod:
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

        # Fetch full mod info for banner image + version field
        self._banner_mod_id = result.mod_id
        self._banner_label.clear()
        info_worker = _ModInfoWorker(self._client, domain, result.mod_id)
        info_worker.signals.finished.connect(self._on_mod_info_done)
        QThreadPool.globalInstance().start(info_worker)

    def _show_mod_detail(self, r: NexusSearchResult) -> None:
        self._mod_name_label.setText(r.name)
        ts = datetime.fromtimestamp(r.updated_ts, tz=timezone.utc) if r.updated_ts else None
        date_str = ts.strftime("%Y-%m-%d") if ts else "—"
        meta_parts = [f"by {r.author}"] if r.author else []
        if r.downloads:
            meta_parts.append(f"↓ {r.downloads:,}")
        if r.endorsements:
            meta_parts.append(f"★ {r.endorsements:,}")
        meta_parts.append(f"updated {date_str}")
        self._mod_meta_label.setText("  ·  ".join(meta_parts))
        self._mod_summary.setPlainText(r.summary)

    @Slot(object)
    def _on_mod_info_done(self, data: dict) -> None:
        if not self._current_mod:
            return
        # Discard results for a mod we've since moved away from
        if data.get("mod_id") != self._banner_mod_id:
            return
        # Update meta label with version from the real mod info
        version = data.get("version", "")
        r = self._current_mod
        ts = datetime.fromtimestamp(r.updated_ts, tz=timezone.utc) if r.updated_ts else None
        date_str = ts.strftime("%Y-%m-%d") if ts else "—"
        meta_parts = [f"by {r.author}"] if r.author else []
        if version:
            meta_parts.append(f"v{version}")
        if r.downloads:
            meta_parts.append(f"↓ {r.downloads:,}")
        if r.endorsements:
            meta_parts.append(f"★ {r.endorsements:,}")
        meta_parts.append(f"updated {date_str}")
        self._mod_meta_label.setText("  ·  ".join(meta_parts))
        # Load the full banner image (picture_url is larger than thumbnail_url)
        pic = data.get("picture_url", "")
        if pic:
            loader = _BannerLoader(pic, self._banner_mod_id, self._cache_dir)
            loader.signals.loaded.connect(self._on_banner_loaded)
            QThreadPool.globalInstance().start(loader)

    @Slot(int, bytes)
    def _on_banner_loaded(self, mod_id: int, data: bytes) -> None:
        if mod_id != self._banner_mod_id:
            return
        px = QPixmap()
        if not px.loadFromData(data):
            return
        w = self._banner_label.width() or 600
        h = self._banner_label.height()
        scaled = px.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = max(0, (scaled.width()  - w) // 2)
        y = max(0, (scaled.height() - h) // 2)
        self._banner_label.setPixmap(scaled.copy(x, y, w, h))

    @Slot(object)
    def _on_files_done(self, files) -> None:
        self._current_files = files
        self._files_table.setRowCount(len(files))
        for i, f in enumerate(files):
            name_item = QTableWidgetItem(f.file_name)
            name_item.setData(Qt.UserRole, f)
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
        has_client = self._client is not None
        self._import_tm_btn.setEnabled(nf is not None and has_client)
        self._merge_btn.setEnabled(nf is not None and has_client)
        # Enable "Open in Editor" for plugin files and archives that may contain them
        can_open = (
            nf is not None and has_client
            and (nf.is_plugin or nf.is_container)
        )
        self._open_plugin_btn.setEnabled(can_open)

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
        self._progress.setRange(0, 0)
        self._set_status(self.tr(f"Downloading {nf.file_name}…"))

        worker = _DownloadWorker(self._client, domain, self._current_mod.mod_id, mod_name, nf, dest_dir)
        worker.signals.progress.connect(self._on_download_progress)
        worker.signals.error.connect(self._on_download_error)
        worker.signals.finished.connect(lambda paths: self._on_download_done(paths, on_done_action))
        self._active_download = worker
        QThreadPool.globalInstance().start(worker)

    @Slot()
    def _download_open_plugin(self) -> None:
        self._start_download("open_plugin")

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
            mb_done  = done  / 1_048_576
            mb_total = total / 1_048_576
            self._set_status(self.tr(f"Downloading… {mb_done:.1f} / {mb_total:.1f} MB"))
        else:
            self._progress.setRange(0, 0)

    @Slot(str)
    def _on_download_error(self, msg: str) -> None:
        self._reset_download_ui()
        if msg == "FREE_NO_CURL_CFFI":
            self._set_status(
                self.tr(
                    "⚠  Free-user download requires <b>curl-cffi</b>.  "
                    "Run <code>pip install curl-cffi</code> then restart the app."
                ),
                error=True,
            )
        elif msg == "FREE_NO_COOKIES":
            self._set_status(
                self.tr(
                    "⚠  No NexusMods session found in Firefox or Chromium.  "
                    "Log in to NexusMods in your browser, then retry."
                ),
                error=True,
            )
        elif msg == "FREE_SESSION_EXPIRED":
            self._set_status(
                self.tr(
                    "⚠  NexusMods session expired.  "
                    "Log in again in your browser, then retry."
                ),
                error=True,
            )
        elif msg.startswith("FREE_POPUP_PARSE"):
            self._set_status(
                self.tr(
                    "⚠  Could not parse download tokens from NexusMods.  "
                    "The site may have changed — please report this issue."
                ),
                error=True,
            )
        else:
            self._set_status(self.tr(f"Download failed: {msg}"), error=True)

    @Slot(object)
    def _on_download_done(self, paths: list, action: str) -> None:
        self._reset_download_ui()
        if not paths:
            self._set_status(self.tr("No files found in the downloaded archive."), error=True)
            return

        # ── Open plugin in editor ─────────────────────────────────────────────
        if action == "open_plugin":
            plugins = _extract_plugins(paths[0])
            if not plugins:
                self._set_status(self.tr(
                    f"No .esp/.esm/.esl found in {paths[0].name}."
                ), error=True)
                return
            self.open_file_requested.emit(plugins[0])
            self._set_status(self.tr(f"✓  Opening {plugins[0].name} in editor…"))
            self.accept()
            return

        # ── BA2 / 7z / rar — suggest manual open ─────────────────────────────
        if paths[0].suffix.lower() in (CONTAINER_EXTS - {".zip"}):
            self._set_status(self.tr(
                f"Downloaded: {paths[0].name} — open it in the editor via File → Open "
                "to browse its contents."
            ))
            return

        # ── Strings / TM actions ──────────────────────────────────────────────
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
        else:
            self.merge_requested.emit(tm)
            self._set_status(self.tr(
                f"✓  Merge requested: {loaded} string(s) from {len(paths)} file(s)."
            ))

    def _reset_download_ui(self) -> None:
        self._progress.setVisible(False)
        self._cancel_btn.setVisible(False)
        self._active_download = None
        nf = self._selected_file()
        has_file = nf is not None
        self._import_tm_btn.setEnabled(has_file)
        self._merge_btn.setEnabled(has_file)
        self._open_plugin_btn.setEnabled(
            has_file and self._client is not None
            and (nf.is_plugin or nf.is_container)
        )
