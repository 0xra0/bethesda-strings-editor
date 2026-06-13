"""
Background update checker and file downloader.

UpdateChecker  — QThread that hits the GitHub releases API and emits
                 update_available(version, changelog, assets) if a newer
                 release exists, or no_update() / check_failed(msg) otherwise.

DownloadWorker — QThread that streams a file to disk and emits progress(0-100),
                 finished(path) or error(msg).
"""
from __future__ import annotations

import logging
from typing import List, Dict

import requests
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

GITHUB_API   = "https://api.github.com/repos/0xra0/bethesda-strings-editor/releases/latest"
RELEASES_URL = "https://github.com/0xra0/bethesda-strings-editor/releases"

_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def parse_version(s: str) -> tuple[int, ...]:
    """'v0.2.3' → (0, 2, 3).  Non-numeric parts become 0."""
    parts = []
    for p in s.strip().lstrip("v").split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts)


class UpdateChecker(QThread):
    """Checks GitHub releases API in a background thread."""

    # new_version (str), changelog (str), assets (list of {name, url, size})
    update_available = Signal(str, str, list)
    no_update        = Signal()
    check_failed     = Signal(str)

    def __init__(self, current_version: str, parent=None) -> None:
        super().__init__(parent)
        self.current_version = current_version

    def run(self) -> None:
        try:
            resp = requests.get(GITHUB_API, timeout=10, headers=_API_HEADERS)
            if resp.status_code == 404:
                self.no_update.emit()
                return
            resp.raise_for_status()
            data = resp.json()

            tag       = data.get("tag_name", "")
            changelog = data.get("body", "").strip()
            assets: List[Dict] = [
                {"name": a["name"], "url": a["browser_download_url"], "size": a["size"]}
                for a in data.get("assets", [])
            ]

            if parse_version(tag) > parse_version(self.current_version):
                self.update_available.emit(tag.lstrip("v"), changelog, assets)
            else:
                self.no_update.emit()

        except Exception as exc:
            logger.debug("Update check failed: %s", exc)
            self.check_failed.emit(str(exc))


class DownloadWorker(QThread):
    """Downloads a URL to a local path, reporting progress."""

    progress = Signal(int)   # 0–100
    finished = Signal(str)   # local file path
    error    = Signal(str)

    def __init__(self, url: str, dest: str, parent=None) -> None:
        super().__init__(parent)
        self.url  = url
        self.dest = dest

    def run(self) -> None:
        try:
            with requests.get(self.url, stream=True, timeout=120) as r:
                r.raise_for_status()
                total      = int(r.headers.get("content-length", 0))
                downloaded = 0
                with open(self.dest, "wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total > 0:
                                self.progress.emit(int(downloaded * 100 / total))
            self.progress.emit(100)
            self.finished.emit(self.dest)
        except Exception as exc:
            self.error.emit(str(exc))
