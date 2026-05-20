"""
Security audit log — append-only JSON-lines file.

Records security-relevant application events (file operations, translation
batches, settings changes, encryption changes) without logging any actual
translated text or original game content.

Usage
-----
    from gui.audit_log import get_audit_log
    log = get_audit_log()
    log.file_opened("/path/to/Starfield_ru.strings", fmt=".strings", count=420)
    log.translation_complete(total=420, translated=281, errors=8)
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_MAX_BYTES = 5 * 1024 * 1024   # rotate at 5 MB


class AuditLog:
    """
    Thread-safe, append-only JSON-lines security log.

    Each line is a JSON object with at minimum:
        ``{"ts": "<ISO-8601>", "event": "<EVENT_CODE>", ...}``

    Sensitive content (translated text, source text) is never logged —
    only metadata (file path, count, model name, event type).
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._enabled = True

    def configure(self, path: Optional[Path], enabled: bool) -> None:
        with self._lock:
            self._path = path
            self._enabled = enabled

    # ── Low-level write ───────────────────────────────────────────────

    def _write(self, event: str, **kwargs: Any) -> None:
        if not self._enabled or not self._path:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "event": event,
            **kwargs,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # Rotate if over size limit
                if self._path.exists() and self._path.stat().st_size >= _MAX_BYTES:
                    rotated = self._path.with_suffix(".1.jsonl")
                    self._path.replace(rotated)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line)
            except Exception as e:
                logger.debug("Audit log write failed: %s", e)

    # ── Public event helpers ─────────────────────────────────────────

    def app_start(self, version: str) -> None:
        self._write("APP_START", version=version)

    def app_close(self) -> None:
        self._write("APP_CLOSE")

    def file_opened(self, path: str, fmt: str, string_count: int) -> None:
        """Log that a game file was opened (path, format, entry count)."""
        self._write("FILE_OPEN", path=path, format=fmt, strings=string_count)

    def file_saved(self, path: str, fmt: str, string_count: int) -> None:
        self._write("FILE_SAVE", path=path, format=fmt, strings=string_count)

    def translation_start(self, model: str, count: int, source_lang: str, target_lang: str) -> None:
        self._write(
            "TRANSLATION_START",
            model=model,
            count=count,
            source=source_lang,
            target=target_lang,
        )

    def translation_complete(self, total: int, translated: int, errors: int) -> None:
        self._write(
            "TRANSLATION_COMPLETE",
            total=total,
            translated=translated,
            errors=errors,
        )

    def settings_changed(self, changed_keys: list[str]) -> None:
        """Log which settings keys were modified (no values, just key names)."""
        self._write("SETTINGS_CHANGE", keys=changed_keys)

    def cache_cleared(self, entries_removed: int) -> None:
        self._write("CACHE_CLEAR", removed=entries_removed)

    def cache_encryption_changed(self, enabled: bool, backend: str) -> None:
        self._write("CACHE_ENCRYPT_CHANGE", enabled=enabled, backend=backend)

    def export_performed(self, fmt: str, path: str) -> None:
        self._write("EXPORT", format=fmt, path=path)

    def security_event(self, description: str, **kwargs: Any) -> None:
        """Escape hatch for ad-hoc security events."""
        self._write("SECURITY", description=description, **kwargs)


# Module-level singleton
_audit_log: Optional[AuditLog] = None


def get_audit_log() -> AuditLog:
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLog()
    return _audit_log
