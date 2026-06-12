"""
Translation session persistence.

A WorkSession captures a named work context: which file is open, the last
search filter, cursor position, scroll offset, and the set of string IDs
that were translated during this session.  Sessions are stored as individual
JSON files in <config_dir>/sessions/ and are completely separate from the
crash-recovery snapshot.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class SearchState:
    """Serialisable snapshot of the last AdvancedSearchDialog run."""
    query:          str  = ""
    id_filter:      str  = ""
    column:         str  = "all"    # "all" | "original" | "translated" | "both"
    status:         str  = "any"    # "any" | "translated" | "not_translated"
    use_regex:      bool = False
    case_sensitive: bool = False
    whole_word:     bool = False

    def is_empty(self) -> bool:
        return not self.query and not self.id_filter and self.status == "any"

    def summary(self) -> str:
        if self.is_empty():
            return ""
        parts = []
        if self.query:
            parts.append(f'"{self.query}"')
        if self.id_filter:
            parts.append(f"ID={self.id_filter}")
        if self.status != "any":
            parts.append(self.status.replace("_", " "))
        return " · ".join(parts)


@dataclass
class WorkSession:
    name:                 str
    created:              str               # ISO datetime string
    modified:             str               # ISO datetime string
    file_path:            str               # absolute path to source file
    file_type:            str               # "esp" | "strings" | "ba2"
    current_row:          int    = 0
    scroll_value:         int    = 0
    search:               SearchState       = field(default_factory=SearchState)
    translated_in_session: List[int]        = field(default_factory=list)
    note:                 str    = ""

    @property
    def translated_count(self) -> int:
        return len(self.translated_in_session)

    @property
    def modified_dt(self) -> datetime:
        try:
            return datetime.fromisoformat(self.modified)
        except ValueError:
            return datetime.min

    def touch(self) -> None:
        self.modified = datetime.now().isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        d = asdict(self)
        d["version"] = _SCHEMA_VERSION
        d["search"] = asdict(self.search)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "WorkSession":
        search_raw = d.get("search", {})
        search = SearchState(
            query=search_raw.get("query", ""),
            id_filter=search_raw.get("id_filter", ""),
            column=search_raw.get("column", "all"),
            status=search_raw.get("status", "any"),
            use_regex=search_raw.get("use_regex", False),
            case_sensitive=search_raw.get("case_sensitive", False),
            whole_word=search_raw.get("whole_word", False),
        )
        return cls(
            name=d["name"],
            created=d.get("created", ""),
            modified=d.get("modified", ""),
            file_path=d.get("file_path", ""),
            file_type=d.get("file_type", ""),
            current_row=d.get("current_row", 0),
            scroll_value=d.get("scroll_value", 0),
            search=search,
            translated_in_session=d.get("translated_in_session", []),
            note=d.get("note", ""),
        )


# ── Storage ────────────────────────────────────────────────────────────────────

def _slug(name: str) -> str:
    """Convert a session name to a safe filename component."""
    name = unicodedata.normalize("NFC", name)
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_-]+", "_", name)
    return name[:64] or "session"


class SessionStore:
    """
    Manages WorkSession files in *sessions_dir*.

    Each session is one JSON file: <slug>.json.  Multiple sessions can share
    the same slug if their names normalise the same way — in that case a
    numeric suffix is appended to prevent collisions.
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────────

    def list_sessions(self) -> List[WorkSession]:
        """Return all sessions sorted by modified date (newest first)."""
        sessions = []
        for path in sorted(self._dir.glob("*.json")):
            s = self._load_file(path)
            if s:
                sessions.append(s)
        sessions.sort(key=lambda s: s.modified_dt, reverse=True)
        return sessions

    def save(self, session: WorkSession) -> bool:
        """Save *session* to disk.  Returns True on success."""
        session.touch()
        path = self._path_for(session.name)
        try:
            tmp = path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(session.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(path)
            logger.debug("Session saved: %s → %s", session.name, path.name)
            return True
        except Exception as exc:
            logger.warning("Failed to save session %r: %s", session.name, exc)
            return False

    def load(self, name: str) -> Optional[WorkSession]:
        """Load a session by exact name.  Returns None if not found."""
        path = self._path_for(name)
        if not path.exists():
            # Scan all files in case slug collision shifted the filename
            for p in self._dir.glob("*.json"):
                s = self._load_file(p)
                if s and s.name == name:
                    return s
        return self._load_file(path)

    def delete(self, name: str) -> bool:
        path = self._path_for(name)
        try:
            if path.exists():
                path.unlink()
                return True
            # Brute-force scan for mismatched slug
            for p in self._dir.glob("*.json"):
                s = self._load_file(p)
                if s and s.name == name:
                    p.unlink()
                    return True
        except Exception as exc:
            logger.warning("Failed to delete session %r: %s", name, exc)
        return False

    def rename(self, old_name: str, new_name: str) -> bool:
        """Load, rename, save under new slug, delete old file."""
        session = self.load(old_name)
        if session is None:
            return False
        old_path = self._path_for(old_name)
        session.name = new_name
        if self.save(session):
            if old_path.exists() and old_path != self._path_for(new_name):
                old_path.unlink(missing_ok=True)
            return True
        return False

    def name_exists(self, name: str) -> bool:
        return any(s.name == name for s in self.list_sessions())

    # ── Internals ───────────────────────────────────────────────────────────────

    def _path_for(self, name: str) -> Path:
        slug = _slug(name)
        # If the exact slug file exists, use it (even if it stores a different name)
        candidate = self._dir / f"{slug}.json"
        if candidate.exists():
            existing = self._load_file(candidate)
            if existing and existing.name == name:
                return candidate
        # Find an unused slug
        path = self._dir / f"{slug}.json"
        if not path.exists():
            return path
        for i in range(2, 100):
            path = self._dir / f"{slug}_{i}.json"
            if not path.exists():
                existing = self._load_file(self._dir / f"{slug}.json")
                if existing and existing.name == name:
                    return self._dir / f"{slug}.json"
                return path
        return self._dir / f"{slug}.json"

    def _load_file(self, path: Path) -> Optional[WorkSession]:
        try:
            data = json.loads(path.read_text("utf-8"))
            if data.get("version") != _SCHEMA_VERSION:
                return None
            return WorkSession.from_dict(data)
        except Exception as exc:
            logger.debug("Cannot read session file %s: %s", path, exc)
            return None
