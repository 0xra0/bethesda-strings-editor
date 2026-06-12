"""
NexusMods read-only API client for browsing and downloading translation mods.

Endpoints used
--------------
Search  : https://search.nexusmods.com/mods          (unofficial, used by the site)
Mod info: GET /v1/games/{domain}/mods/{mod_id}.json
Files   : GET /v1/games/{domain}/mods/{mod_id}/files.json
DL link : GET /v1/games/{domain}/mods/{mod_id}/files/{file_id}/download_link.json
          (returns CDN URLs; requires premium or a recent page visit for free accounts)

All calls need the ``apikey`` header (Settings → NexusMods API key).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://search.nexusmods.com/mods"
_API_BASE   = "https://api.nexusmods.com/v1"
_UA         = "bethesda-strings-editor"
_TIMEOUT    = 20

# Games where Bethesda strings files are relevant
GAMES: dict[str, tuple[str, int]] = {
    "Starfield":                  ("starfield",             4853),
    "Skyrim Special Edition":     ("skyrimspecialedition",  1704),
    "Skyrim (Legendary Edition)": ("skyrim",                 110),
    "Fallout 4":                  ("fallout4",              1151),
    "Fallout: New Vegas":         ("falloutnv",              130),
    "Oblivion":                   ("oblivion",               101),
}

# Extensions we can import directly
STRINGS_EXTS = {".strings", ".dlstrings", ".ilstrings"}
# Archive / BA2 that may contain strings files
CONTAINER_EXTS = {".ba2", ".zip", ".7z", ".rar"}


@dataclass
class NexusSearchResult:
    mod_id:       int
    name:         str
    author:       str
    summary:      str
    game_id:      int
    category:     str
    downloads:    int
    endorsements: int
    updated_ts:   int   # unix timestamp
    picture_url:  str = ""


@dataclass
class NexusModFile:
    file_id:      int
    name:         str
    file_name:    str
    version:      str
    size_kb:      int
    category:     str
    description:  str
    uploaded_ts:  int

    @property
    def is_strings(self) -> bool:
        return Path(self.file_name).suffix.lower() in STRINGS_EXTS

    @property
    def is_container(self) -> bool:
        return Path(self.file_name).suffix.lower() in CONTAINER_EXTS

    @property
    def likely_translation(self) -> bool:
        low = (self.file_name + self.name + self.description).lower()
        return any(k in low for k in (
            "translat", "strings", "locali", ".strings", ".dlstrings", ".ilstrings",
        ))


class NexusModsError(Exception):
    pass


class NexusClient:
    """Thin wrapper around the NexusMods REST v1 and search APIs."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise NexusModsError("NexusMods API key is not configured.")
        self._session = requests.Session()
        self._session.headers.update({
            "apikey": api_key,
            "User-Agent": _UA,
            "Accept": "application/json",
        })

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        game_id: int,
        *,
        include_adult: bool = False,
    ) -> List[NexusSearchResult]:
        params = {
            "terms": query,
            "game_id": game_id,
            "blocked_tags": "",
            "blocked_authors": "",
            "include_adult": "1" if include_adult else "0",
        }
        try:
            resp = self._session.get(
                _SEARCH_URL, params=params, timeout=_TIMEOUT
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            msg = str(exc)
            if "NameResolutionError" in msg or "Name or service not known" in msg or "Errno -2" in msg:
                raise NexusModsError(
                    "Cannot reach NexusMods search server (DNS resolution failed).\n"
                    "Check your internet connection or try again later."
                ) from exc
            raise NexusModsError(f"Connection error: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise NexusModsError("NexusMods search timed out. Try again later.") from exc
        except requests.RequestException as exc:
            raise NexusModsError(f"Search request failed: {exc}") from exc

        results = []
        for item in data.get("results", []):
            results.append(NexusSearchResult(
                mod_id=int(item.get("mod_id", 0)),
                name=item.get("name", ""),
                author=item.get("username", ""),
                summary=item.get("description", ""),
                game_id=int(item.get("game_id", game_id)),
                category=item.get("category_name", ""),
                downloads=int(item.get("downloads", 0)),
                endorsements=int(item.get("endorsements", 0)),
                updated_ts=int(item.get("updated_at", 0)),
                picture_url=item.get("thumbnail_url", ""),
            ))
        return results

    # ── Mod files ─────────────────────────────────────────────────────────────

    def mod_files(self, domain: str, mod_id: int) -> List[NexusModFile]:
        url = f"{_API_BASE}/games/{domain}/mods/{mod_id}/files.json"
        try:
            resp = self._session.get(url, timeout=_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise NexusModsError(f"Files request failed: {exc}") from exc

        files = []
        for f in data.get("files", []):
            files.append(NexusModFile(
                file_id=int(f.get("file_id", 0)),
                name=f.get("name", ""),
                file_name=f.get("file_name", ""),
                version=f.get("version", ""),
                size_kb=int(f.get("size_kb", 0)),
                category=f.get("category_name", ""),
                description=f.get("description", ""),
                uploaded_ts=int(f.get("uploaded_timestamp", 0)),
            ))
        # Sort: likely translation files first, then by size (smaller first)
        files.sort(key=lambda f: (not f.likely_translation, f.size_kb))
        return files

    # ── Download link ─────────────────────────────────────────────────────────

    def download_url(self, domain: str, mod_id: int, file_id: int) -> Optional[str]:
        """Return the best CDN download URL, or None for free-account 403s."""
        url = f"{_API_BASE}/games/{domain}/mods/{mod_id}/files/{file_id}/download_link.json"
        try:
            resp = self._session.get(url, timeout=_TIMEOUT)
            if resp.status_code == 403:
                logger.info(
                    "Download link 403 for mod %d file %d "
                    "(premium required or page not recently visited)", mod_id, file_id
                )
                return None
            resp.raise_for_status()
            links = resp.json()
            if links:
                return links[0].get("URI", "")
            return None
        except requests.RequestException as exc:
            raise NexusModsError(f"Download link request failed: {exc}") from exc

    def mod_page_url(self, domain: str, mod_id: int) -> str:
        return f"https://www.nexusmods.com/{domain}/mods/{mod_id}"

    # ── File download ─────────────────────────────────────────────────────────

    def download_file(
        self,
        url: str,
        dest: Path,
        progress: Optional[Callable[[int, int], None]] = None,
        stop_event: Optional[threading.Event] = None,
    ) -> None:
        """Stream *url* to *dest*, calling progress(bytes_done, total) if given."""
        resp = self._session.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if stop_event and stop_event.is_set():
                    raise NexusModsError("Download cancelled")
                if chunk:
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if dest.stat().st_size == 0:
            dest.unlink(missing_ok=True)
            raise NexusModsError("Downloaded file is empty")
