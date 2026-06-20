"""
Background update checker and file downloader.

UpdateChecker  — QThread that hits the GitHub releases API and emits
                 update_available(version, changelog, assets) if a newer
                 release exists, or no_update() / check_failed(msg) otherwise.

DownloadWorker — QThread that streams a file to disk and emits progress(0-100),
                 finished(path) or error(msg).
"""
from __future__ import annotations

import html
import logging
import re
from typing import List, Dict, Optional

import requests
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

GITHUB_API   = "https://api.github.com/repos/0xra0/bethesda-strings-editor/releases/latest"
RELEASES_API = "https://api.github.com/repos/0xra0/bethesda-strings-editor/releases"
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


# ── Changelog (GitHub releases → HTML for the welcome screen) ───────────────


def parse_releases(data: Optional[list], limit: int = 6) -> List[Dict]:
    """Turn the GitHub releases-list JSON into tidy dicts (newest first).

    Drafts are skipped (no public changelog yet).  Each item is
    ``{tag, name, date, body, url, prerelease}``.  Pure — unit-testable with a
    captured JSON payload, no network.
    """
    out: List[Dict] = []
    for rel in data or []:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        tag = (rel.get("tag_name") or "").strip()
        out.append(
            {
                "tag": tag,
                "name": (rel.get("name") or tag or "").strip(),
                "date": (rel.get("published_at") or "")[:10],  # YYYY-MM-DD
                "body": (rel.get("body") or "").strip(),
                "url": rel.get("html_url") or RELEASES_URL,
                "prerelease": bool(rel.get("prerelease")),
            }
        )
    return out[: max(0, limit)]


_MD_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_MD_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_MD_CODE = re.compile(r"`([^`]+)`")


def _md_inline(text: str) -> str:
    """Escape HTML, then apply inline markdown (links, bold, inline code)."""
    text = html.escape(text)
    # Links first so their text can still pick up bold/code afterwards.
    text = _MD_LINK.sub(r'<a href="\2">\1</a>', text)
    text = _MD_BOLD.sub(r"<b>\1</b>", text)
    text = _MD_CODE.sub(r"<code>\1</code>", text)
    return text


def markdown_to_html(body: Optional[str]) -> str:
    """Tiny markdown → HTML for release notes (headings, bullets, inline).

    Handles the subset GitHub release bodies actually use here: ``#``-headings,
    ``-``/``*`` bullet lists, blank-line paragraphs, and inline links/bold/code.
    Not a full CommonMark implementation — just enough to read cleanly in a
    QTextBrowser.  Pure and testable.
    """
    lines = (body or "").replace("\r\n", "\n").split("\n")
    parts: List[str] = []
    in_list = False

    def _close_list() -> None:
        nonlocal in_list
        if in_list:
            parts.append("</ul>")
            in_list = False

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            _close_list()
            continue
        if stripped.startswith("#"):
            _close_list()
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            tag = "h3" if level <= 2 else "h4"
            parts.append(f"<{tag}>{_md_inline(heading)}</{tag}>")
        elif stripped[:2] in ("- ", "* "):
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{_md_inline(stripped[2:].strip())}</li>")
        else:
            _close_list()
            parts.append(f"<p>{_md_inline(stripped)}</p>")

    _close_list()
    return "\n".join(parts)


def _truncate_md(body: str, max_lines: int = 24) -> tuple[str, bool]:
    """Keep at most *max_lines* non-empty lines of a release body.

    Release notes here are auto-generated and can run to thousands of lines, so
    the welcome panel shows only a summary with a "read more" link.  Returns
    ``(text, truncated)``.
    """
    lines = (body or "").replace("\r\n", "\n").split("\n")
    kept: List[str] = []
    seen = 0
    for ln in lines:
        kept.append(ln)
        if ln.strip():
            seen += 1
            if seen >= max_lines:
                break
    truncated = seen >= max_lines and any(
        l.strip() for l in lines[len(kept):]
    )
    return "\n".join(kept), truncated


def changelog_to_html(releases: List[Dict], current_version: str = "") -> str:
    """Full HTML document for the welcome "What's New" panel."""
    cur = parse_version(current_version) if current_version else ()
    blocks: List[str] = [
        "<style>"
        "h2{margin:2px 0 2px 0;font-size:15px;}"
        "h3{margin:8px 0 2px 0;font-size:13px;}"
        "h4{margin:6px 0 2px 0;font-size:12px;}"
        "ul{margin:2px 0 6px 0;-qt-list-indent:1;}"
        "li{margin:1px 0;}"
        "p{margin:3px 0;}"
        ".date{color:gray;font-size:11px;}"
        ".tag{color:#6366f1;}"
        ".cur{color:#10b981;}"
        "code{background:rgba(127,127,127,0.18);}"
        "</style>"
    ]
    for rel in releases:
        title = html.escape(rel.get("name") or rel.get("tag") or "")
        date = html.escape(rel.get("date") or "")
        is_cur = bool(cur) and parse_version(rel.get("tag", "")) == cur
        badge = " <span class='cur'>(installed)</span>" if is_cur else ""
        pre = " <span class='tag'>pre-release</span>" if rel.get("prerelease") else ""
        meta = f" <span class='date'>· {date}</span>" if date else ""
        blocks.append(f"<h2><span class='tag'>{title}</span>{badge}{pre}{meta}</h2>")
        text, truncated = _truncate_md(rel.get("body", ""))
        body = markdown_to_html(text)
        blocks.append(body or "<p><i>No release notes.</i></p>")
        if truncated:
            url = html.escape(rel.get("url") or RELEASES_URL)
            blocks.append(f"<p><a href='{url}'>… read the full notes →</a></p>")
        blocks.append("<hr/>")
    return "\n".join(blocks)


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


class ChangelogFetcher(QThread):
    """Fetches the recent releases list from GitHub in a background thread.

    Emits ``loaded(list)`` with parsed release dicts (see :func:`parse_releases`)
    or ``failed(str)`` — never blocks the UI and never raises.
    """

    loaded = Signal(list)
    failed = Signal(str)

    def __init__(self, limit: int = 6, parent=None) -> None:
        super().__init__(parent)
        self.limit = limit

    def run(self) -> None:
        try:
            resp = requests.get(
                RELEASES_API,
                timeout=10,
                headers=_API_HEADERS,
                params={"per_page": max(1, self.limit)},
            )
            resp.raise_for_status()
            releases = parse_releases(resp.json(), limit=self.limit)
            self.loaded.emit(releases)
        except Exception as exc:
            logger.debug("Changelog fetch failed: %s", exc)
            self.failed.emit(str(exc))


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
