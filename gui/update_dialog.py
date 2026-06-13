"""
Update-available dialog.

Shows the new version, full changelog from the GitHub release body,
a per-platform download button with a live progress bar, and a fallback
"Open in Browser" button that goes to the releases page.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout,
    QLabel, QProgressBar, QPushButton,
    QTextEdit, QVBoxLayout,
)

from gui.updater import DownloadWorker, RELEASES_URL


def _pick_asset(assets: List[Dict]) -> Optional[Dict]:
    """Return the best asset for the current platform, or None."""
    plat = sys.platform
    for a in assets:
        name = a["name"].lower()
        if plat == "linux" and "linux" in name and name.endswith(".zip"):
            return a
        if plat == "win32" and ("windows" in name or "win" in name) and name.endswith(".zip"):
            return a
        if plat == "darwin" and ("mac" in name or "darwin" in name):
            return a
    return None


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n //= 1024
    return f"{n:.1f} GB"


def _open_folder(path: str) -> None:
    folder = str(Path(path).parent)
    if sys.platform == "linux":
        subprocess.Popen(["xdg-open", folder])
    elif sys.platform == "win32":
        os.startfile(folder)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", folder])


class UpdateDialog(QDialog):
    """Shown when a newer GitHub release is available."""

    def __init__(self, current: str, new_ver: str,
                 changelog: str, assets: List[Dict], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Update Available"))
        self.setMinimumWidth(580)
        self.setMinimumHeight(420)

        self._current = current
        self._new     = new_ver
        self._assets  = assets
        self._worker: Optional[DownloadWorker] = None

        self._build_ui(changelog)

    def _build_ui(self, changelog: str) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(10)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel(
            f'<span style="font-size:15px; font-weight:bold;">'
            f'Version {self._new} is available</span><br>'
            f'<span style="color: gray;">You are running version {self._current}</span>'
        )
        header.setTextFormat(Qt.RichText)
        root.addWidget(header)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        root.addWidget(sep)

        # ── Changelog ─────────────────────────────────────────────────────────
        root.addWidget(QLabel(self.tr("What's new:")))
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setPlainText(changelog or self.tr("(no release notes provided)"))
        self._log.setMinimumHeight(180)
        root.addWidget(self._log)

        # ── Progress (hidden until download starts) ────────────────────────────
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        self._status = QLabel()
        self._status.setVisible(False)
        root.addWidget(self._status)

        # ── Buttons ───────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        asset = _pick_asset(self._assets)
        if asset:
            size_str = _human_size(asset["size"])
            self._btn_dl = QPushButton(
                self.tr(f"Download  {self._new}  ({size_str})")
            )
            self._btn_dl.setDefault(True)
            self._btn_dl.clicked.connect(lambda: self._start_download(asset))
        else:
            self._btn_dl = QPushButton(self.tr("Open Releases Page"))
            self._btn_dl.setDefault(True)
            self._btn_dl.clicked.connect(self._open_browser)

        btn_row.addWidget(self._btn_dl)

        btn_browser = QPushButton(self.tr("Open in Browser"))
        btn_browser.clicked.connect(self._open_browser)
        btn_row.addWidget(btn_browser)

        btn_row.addStretch()

        btn_later = QPushButton(self.tr("Later"))
        btn_later.clicked.connect(self.reject)
        btn_row.addWidget(btn_later)

        root.addLayout(btn_row)

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _open_browser(self) -> None:
        QDesktopServices.openUrl(QUrl(RELEASES_URL))

    def _start_download(self, asset: Dict) -> None:
        dest = str(Path.home() / "Downloads" / asset["name"])

        self._btn_dl.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status.setVisible(True)
        self._status.setText(self.tr(f"Downloading {asset['name']}…"))

        self._worker = DownloadWorker(asset["url"], dest, self)
        self._worker.progress.connect(self._progress.setValue)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    @Slot(str)
    def _on_done(self, path: str) -> None:
        self._status.setText(
            self.tr(f"Download complete — saved to {path}")
        )
        self._btn_dl.setText(self.tr("Open Downloads Folder"))
        self._btn_dl.setEnabled(True)
        try:
            self._btn_dl.clicked.disconnect()
        except RuntimeError:
            pass
        self._btn_dl.clicked.connect(lambda: _open_folder(path))

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._status.setText(self.tr(f"Download failed: {msg}"))
        self._btn_dl.setEnabled(True)
