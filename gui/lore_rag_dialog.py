"""
Lore RAG management dialog.

Three panels in a QTabWidget:
  • Index — stats (article count / source breakdown), search preview
  • Download — fetch lore articles from UESP MediaWiki API by category
  • Import — bulk-import from a local JSON or plain-text file
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests
from PySide6.QtCore import QObject, QThread, Signal, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from bethesda_strings.lore_db import LoreArticle, LoreDB

logger = logging.getLogger(__name__)

# ── UESP fetch worker ─────────────────────────────────────────────────────────

_UESP_API = "https://en.uesp.net/w/api.php"
_BATCH_SIZE = 50  # titles per extract request

_UESP_CATEGORIES: Dict[str, str] = {
    "Starfield-Factions":      "Factions & Organizations",
    "Starfield-Places":        "Places & Locations",
    "Starfield-Characters":    "Characters & NPCs",
    "Starfield-Items":         "Items & Equipment",
    "Starfield-Lore":          "Lore & History",
    "Starfield-Skills":        "Skills & Perks",
    "Starfield-Religions":     "Religions & Beliefs",
    "Starfield-Ships":         "Ships & Vehicles",
}


class _FetchWorker(QObject):
    """Background thread that downloads UESP articles into LoreDB."""

    progress = Signal(int, int, str)   # done, total, current_title
    finished = Signal(int, str)        # inserted_count, error_or_""
    cancelled = Signal()

    def __init__(self, db: LoreDB, categories: List[str]) -> None:
        super().__init__()
        self._db = db
        self._categories = categories
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:  # called by QThread.started
        try:
            titles = self._collect_titles()
            if self._cancel:
                self.cancelled.emit()
                return
            total = len(titles)
            inserted = 0
            for batch_start in range(0, total, _BATCH_SIZE):
                if self._cancel:
                    self.cancelled.emit()
                    return
                batch = titles[batch_start: batch_start + _BATCH_SIZE]
                articles = self._fetch_extracts(batch)
                for art in articles:
                    self._db.upsert(art)
                    inserted += 1
                    self.progress.emit(
                        batch_start + len(articles), total, art.title
                    )
                time.sleep(0.05)  # polite rate-limit
            self.finished.emit(inserted, "")
        except Exception as exc:
            logger.exception("UESP fetch failed")
            self.finished.emit(0, str(exc))

    def _collect_titles(self) -> List[str]:
        titles: list[str] = []
        for cat in self._categories:
            cmcontinue = None
            while True:
                params: dict = {
                    "action": "query",
                    "format": "json",
                    "list": "categorymembers",
                    "cmtitle": f"Category:{cat}",
                    "cmlimit": "500",
                    "cmtype": "page",
                }
                if cmcontinue:
                    params["cmcontinue"] = cmcontinue
                resp = requests.get(_UESP_API, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                for m in data.get("query", {}).get("categorymembers", []):
                    titles.append(m["title"])
                cont = data.get("continue", {})
                cmcontinue = cont.get("cmcontinue")
                if not cmcontinue:
                    break
                if self._cancel:
                    return titles
        return list(dict.fromkeys(titles))  # dedup, preserve order

    def _fetch_extracts(self, titles: List[str]) -> List[LoreArticle]:
        params = {
            "action": "query",
            "format": "json",
            "titles": "|".join(titles),
            "prop": "extracts|categories",
            "exintro": "true",
            "exlimit": str(len(titles)),
            "formatversion": "2",
        }
        resp = requests.get(_UESP_API, params=params, timeout=30)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", [])
        articles: list[LoreArticle] = []
        for page in pages:
            if "missing" in page or not page.get("extract"):
                continue
            cats = [
                c["title"].replace("Category:Starfield-", "").lower()
                for c in page.get("categories", [])
                if c.get("title", "").startswith("Category:Starfield-")
            ]
            articles.append(
                LoreArticle(
                    title=page["title"],
                    content=page["extract"],
                    source="uesp",
                    tags=",".join(cats[:5]),
                )
            )
        return articles


# ── Import worker ─────────────────────────────────────────────────────────────

class _ImportWorker(QObject):
    """Background thread that imports articles from a local JSON file."""

    progress = Signal(int, int, str)
    finished = Signal(int, str)

    def __init__(self, db: LoreDB, path: str) -> None:
        super().__init__()
        self._db = db
        self._path = Path(path)

    def run(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                entries = data
            elif isinstance(data, dict):
                # Support {"articles": [...]} wrapper
                entries = data.get("articles", list(data.values()))
            else:
                self.finished.emit(0, "Unexpected JSON structure")
                return
            total = len(entries)
            inserted = 0
            for i, entry in enumerate(entries):
                if isinstance(entry, dict):
                    art = LoreArticle(
                        title=str(entry.get("title", f"Article {i}")),
                        content=str(entry.get("content", entry.get("text", ""))),
                        source=str(entry.get("source", "import")),
                        tags=str(entry.get("tags", "")),
                    )
                elif isinstance(entry, str):
                    # Plain string: treat first line as title, rest as content
                    lines = entry.strip().splitlines()
                    art = LoreArticle(
                        title=lines[0][:120] if lines else f"Article {i}",
                        content=entry,
                        source="import",
                        tags="",
                    )
                else:
                    continue
                self._db.upsert(art)
                inserted += 1
                self.progress.emit(i + 1, total, art.title)
            self.finished.emit(inserted, "")
        except Exception as exc:
            logger.exception("Import failed")
            self.finished.emit(0, str(exc))


# ── Dialog ────────────────────────────────────────────────────────────────────

class LoreRAGDialog(QDialog):
    """Management UI for the lore RAG database."""

    def __init__(self, db: LoreDB, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._db = db
        self._thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None

        self.setWindowTitle(self.tr("Lore RAG — Context Database"))
        self.setMinimumSize(700, 520)
        self.resize(800, 580)
        self._build_ui()
        self._refresh_stats()

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)

        tabs = QTabWidget()
        tabs.addTab(self._build_index_tab(), self.tr("Index"))
        tabs.addTab(self._build_download_tab(), self.tr("Download (UESP)"))
        tabs.addTab(self._build_import_tab(), self.tr("Import (Local)"))
        root.addWidget(tabs)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(True)
        root.addWidget(self._progress_bar)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        root.addWidget(self._status_label)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        btn_box.rejected.connect(self.reject)
        root.addWidget(btn_box)

    def _build_index_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)

        # ── Stats ──────────────────────────────────────────────────────────
        stats_box = QGroupBox(self.tr("Index Statistics"))
        stats_form = QFormLayout(stats_box)
        self._lbl_count = QLabel("—")
        self._lbl_sources = QLabel("—")
        stats_form.addRow(self.tr("Articles indexed:"), self._lbl_count)
        stats_form.addRow(self.tr("Sources:"), self._lbl_sources)
        v.addWidget(stats_box)

        # ── Search preview ─────────────────────────────────────────────────
        search_box = QGroupBox(self.tr("Search Preview"))
        sv = QVBoxLayout(search_box)
        sh = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(
            self.tr("Type a term (e.g. House Va'ruun, Akila City)…")
        )
        self._search_edit.returnPressed.connect(self._run_search)
        sh.addWidget(self._search_edit)
        btn_search = QPushButton(self.tr("Search"))
        btn_search.clicked.connect(self._run_search)
        sh.addWidget(btn_search)
        sv.addLayout(sh)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._results_list = QListWidget()
        self._results_list.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding
        )
        self._results_list.currentRowChanged.connect(self._on_result_selected)
        splitter.addWidget(self._results_list)

        self._result_detail = QTextBrowser()
        self._result_detail.setOpenExternalLinks(False)
        splitter.addWidget(self._result_detail)
        splitter.setSizes([240, 400])
        sv.addWidget(splitter)
        v.addWidget(search_box)

        # ── Danger zone ────────────────────────────────────────────────────
        danger_box = QGroupBox(self.tr("Manage Index"))
        dh = QHBoxLayout(danger_box)
        btn_clear_all = QPushButton(self.tr("Clear All Articles…"))
        btn_clear_all.clicked.connect(self._clear_all)
        dh.addWidget(btn_clear_all)
        dh.addStretch()
        v.addWidget(danger_box)

        return w

    def _build_download_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            self.tr(
                "Downloads lore articles from the UESP wiki (en.uesp.net) via its MediaWiki API.\n"
                "Select the categories you want to index, then click Download."
            )
        )
        info.setWordWrap(True)
        v.addWidget(info)

        cat_box = QGroupBox(self.tr("Categories"))
        cv = QVBoxLayout(cat_box)
        self._cat_checks: dict[str, QCheckBox] = {}
        for cat_id, label in _UESP_CATEGORIES.items():
            chk = QCheckBox(f"{label}  ({cat_id})")
            chk.setChecked(cat_id in ("Starfield-Factions", "Starfield-Places", "Starfield-Characters"))
            cv.addWidget(chk)
            self._cat_checks[cat_id] = chk
        v.addWidget(cat_box)

        note = QLabel(
            self.tr(
                "Note: downloading all categories fetches several hundred articles.\n"
                "Internet access is required.  Rate-limited to be polite to UESP servers."
            )
        )
        note.setWordWrap(True)
        note_font = QFont(note.font())
        note_font.setItalic(True)
        note.setFont(note_font)
        v.addWidget(note)

        bh = QHBoxLayout()
        self._btn_download = QPushButton(self.tr("Download Selected Categories"))
        self._btn_download.clicked.connect(self._start_download)
        bh.addWidget(self._btn_download)
        self._btn_cancel_dl = QPushButton(self.tr("Cancel"))
        self._btn_cancel_dl.setEnabled(False)
        self._btn_cancel_dl.clicked.connect(self._cancel_worker)
        bh.addWidget(self._btn_cancel_dl)
        bh.addStretch()
        v.addLayout(bh)

        v.addStretch()
        return w

    def _build_import_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)

        info = QLabel(
            self.tr(
                "Import lore articles from a local JSON file.\n\n"
                "Expected format — a JSON array of objects:\n"
                '  [{"title": "House Va\'ruun", "content": "…", '
                '"source": "manual", "tags": "faction,lore"}, …]\n\n'
                "Alternatively, each entry may be a plain text string "
                "(first line = title)."
            )
        )
        info.setWordWrap(True)
        v.addWidget(info)

        form_box = QGroupBox(self.tr("Import File"))
        form = QFormLayout(form_box)
        fh = QHBoxLayout()
        self._import_path_edit = QLineEdit()
        self._import_path_edit.setPlaceholderText(self.tr("Path to JSON file…"))
        fh.addWidget(self._import_path_edit)
        btn_browse = QPushButton(self.tr("Browse…"))
        btn_browse.clicked.connect(self._browse_import)
        fh.addWidget(btn_browse)
        form.addRow(self.tr("File:"), fh)
        v.addWidget(form_box)

        bh = QHBoxLayout()
        self._btn_import = QPushButton(self.tr("Import"))
        self._btn_import.clicked.connect(self._start_import)
        bh.addWidget(self._btn_import)
        bh.addStretch()
        v.addLayout(bh)

        v.addStretch()
        return w

    # ── Stats refresh ─────────────────────────────────────────────────────────

    def _refresh_stats(self) -> None:
        count = self._db.article_count()
        self._lbl_count.setText(str(count))
        sources = self._db.sources()
        if sources:
            parts = [f"{s['source']} ({s['count']})" for s in sources]
            self._lbl_sources.setText(", ".join(parts))
        else:
            self._lbl_sources.setText(self.tr("(none)"))

    # ── Search ────────────────────────────────────────────────────────────────

    def _run_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query:
            return
        self._results_list.clear()
        self._result_detail.clear()
        self._search_results: list[dict] = self._db.search(query, max_results=8)
        for hit in self._search_results:
            excerpt = re.sub(r"</?b>", "", hit.get("excerpt", ""))
            item = QListWidgetItem(hit["title"])
            item.setToolTip(excerpt[:200])
            self._results_list.addItem(item)
        if not self._search_results:
            self._result_detail.setPlainText(self.tr("No results found."))

    def _on_result_selected(self, row: int) -> None:
        if row < 0 or row >= len(getattr(self, "_search_results", [])):
            return
        hit = self._search_results[row]
        content = self._db.get_article_content(hit["title"]) or "(no content)"
        self._result_detail.setPlainText(
            f"=== {hit['title']} ===\n\n{content[:2000]}"
        )

    # ── Clear all ─────────────────────────────────────────────────────────────

    def _clear_all(self) -> None:
        n = self._db.article_count()
        if n == 0:
            QMessageBox.information(self, self.tr("Index Empty"), self.tr("The index is already empty."))
            return
        if (
            QMessageBox.question(
                self,
                self.tr("Clear All Articles"),
                self.tr(f"Delete all {n} articles from the index?\nThis cannot be undone."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            == QMessageBox.StandardButton.Yes
        ):
            self._db.delete_all()
            self._refresh_stats()
            self._status_label.setText(self.tr(f"Deleted {n} articles."))

    # ── Download ──────────────────────────────────────────────────────────────

    def _start_download(self) -> None:
        selected = [k for k, v in self._cat_checks.items() if v.isChecked()]
        if not selected:
            QMessageBox.warning(
                self,
                self.tr("No Categories"),
                self.tr("Select at least one category to download."),
            )
            return
        self._start_worker(_FetchWorker(self._db, selected))
        self._btn_download.setEnabled(False)
        self._btn_cancel_dl.setEnabled(True)

    # ── Import ────────────────────────────────────────────────────────────────

    def _browse_import(self) -> None:
        from gui.file_dialog_helper import get_open_filename
        path, _ = get_open_filename(
            self,
            self.tr("Select JSON File"),
            filter_str="JSON files (*.json);;All files (*)",
        )
        if path:
            self._import_path_edit.setText(path)

    def _start_import(self) -> None:
        path = self._import_path_edit.text().strip()
        if not path or not Path(path).is_file():
            QMessageBox.warning(
                self,
                self.tr("File Not Found"),
                self.tr("Please select a valid JSON file to import."),
            )
            return
        self._start_worker(_ImportWorker(self._db, path))
        self._btn_import.setEnabled(False)

    # ── Worker lifecycle ──────────────────────────────────────────────────────

    def _start_worker(self, worker: QObject) -> None:
        self._worker = worker
        self._thread = QThread(self)
        worker.moveToThread(self._thread)
        worker.progress.connect(self._on_progress)  # type: ignore[attr-defined]
        worker.finished.connect(self._on_worker_finished)  # type: ignore[attr-defined]
        self._thread.started.connect(worker.run)  # type: ignore[attr-defined]
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._status_label.setText(self.tr("Working…"))
        self._thread.start()

    def _on_progress(self, done: int, total: int, title: str) -> None:
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(done)
        self._status_label.setText(self.tr(f"Fetching: {title} ({done}/{total})"))

    def _on_worker_finished(self, count: int, error: str) -> None:
        self._progress_bar.setVisible(False)
        self._btn_download.setEnabled(True)
        self._btn_cancel_dl.setEnabled(False)
        self._btn_import.setEnabled(True)
        if self._thread:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
        self._worker = None
        self._refresh_stats()
        if error:
            QMessageBox.warning(
                self, self.tr("Download Error"),
                self.tr(f"Error: {error}")
            )
            self._status_label.setText(self.tr(f"Error: {error}"))
        else:
            self._status_label.setText(
                self.tr(f"Done. {count} articles added/updated.")
            )

    def _cancel_worker(self) -> None:
        if self._worker and hasattr(self._worker, "cancel"):
            self._worker.cancel()  # type: ignore[attr-defined]
        self._btn_cancel_dl.setEnabled(False)
        self._status_label.setText(self.tr("Cancelling…"))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cancel_worker()
        super().closeEvent(event)
