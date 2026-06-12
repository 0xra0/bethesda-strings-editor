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
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

import requests

logger = logging.getLogger(__name__)

_SEARCH_URL  = "https://search.nexusmods.com/mods"
_GRAPHQL_URL = "https://api.nexusmods.com/v2/graphql"
_API_BASE    = "https://api.nexusmods.com/v1"
_UA          = "bethesda-strings-editor"
_TIMEOUT     = 20

# GraphQL query: nameStemmed MATCHES is a full-text/stemmed search, works for
# multi-word queries.  gameDomainName is the slug from GAMES (e.g. "starfield").
_GQL_SEARCH = """
{
  mods(
    filter: {
      gameDomainName: [{ value: "%s", op: EQUALS }]
      nameStemmed: [{ value: "%s", op: MATCHES }]
    }
    sort: [{ endorsements: { direction: DESC } }]
    count: 30
  ) {
    nodes {
      modId gameId name summary author
      downloads endorsements updatedAt thumbnailUrl
    }
  }
}
"""

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
        game_domain: str = "",
    ) -> List[NexusSearchResult]:
        # Prefer the official GraphQL API (api.nexusmods.com) — it lives on the
        # same host as all other API calls so DNS issues with search.nexusmods.com
        # don't affect it.  Fall back to the legacy Elasticsearch endpoint only if
        # GraphQL itself fails.
        if game_domain:
            try:
                return self._graphql_search(query, game_domain, game_id)
            except NexusModsError:
                pass  # fall through to legacy endpoint

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

    def _graphql_search(
        self,
        query: str,
        game_domain: str,
        game_id: int,
    ) -> List[NexusSearchResult]:
        safe_query = query.replace("\\", "\\\\").replace('"', '\\"')
        gql = _GQL_SEARCH % (game_domain, safe_query)
        try:
            resp = self._session.post(
                _GRAPHQL_URL,
                json={"query": gql},
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.ConnectionError as exc:
            raise NexusModsError(f"GraphQL connection error: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise NexusModsError("NexusMods GraphQL search timed out.") from exc
        except requests.RequestException as exc:
            raise NexusModsError(f"GraphQL request failed: {exc}") from exc

        if "errors" in data:
            msgs = "; ".join(e.get("message", "") for e in data["errors"])
            raise NexusModsError(f"GraphQL error: {msgs}")

        results = []
        for item in data.get("data", {}).get("mods", {}).get("nodes", []):
            updated_str = item.get("updatedAt", "")
            try:
                updated_ts = int(
                    datetime.fromisoformat(updated_str.replace("Z", "+00:00")).timestamp()
                )
            except (ValueError, AttributeError):
                updated_ts = 0
            results.append(NexusSearchResult(
                mod_id=int(item.get("modId", 0)),
                name=item.get("name", ""),
                author=item.get("author") or "",
                summary=item.get("summary") or "",
                game_id=int(item.get("gameId") or game_id),
                category="",
                downloads=int(item.get("downloads", 0)),
                endorsements=int(item.get("endorsements", 0)),
                updated_ts=updated_ts,
                picture_url=item.get("thumbnailUrl") or "",
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

        # catId 4 = OLD_VERSION, catId 7 = ARCHIVED — both should be hidden.
        # Use both name and numeric id so the filter is robust across API versions.
        _HIDE_IDS  = {4, 7}
        _HIDE_CATS = {"OLD_VERSION", "ARCHIVED"}

        files = []
        for f in data.get("files", []):
            cat_name = (f.get("category_name", "") or "").upper().replace(" ", "_")
            cat_id   = int(f.get("category_id", 0) or 0)
            if cat_name in _HIDE_CATS or cat_id in _HIDE_IDS:
                continue
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
        # Sort: likely translation files first, then by upload date (newest first)
        files.sort(key=lambda f: (not f.likely_translation, -f.uploaded_ts))
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
