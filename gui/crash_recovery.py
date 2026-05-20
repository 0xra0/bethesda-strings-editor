"""
Crash recovery: periodic auto-save of translation progress + startup restore.

The recovery snapshot is a JSON file in the app config directory.  It is
written every N minutes while a file is open and deleted on clean exit.
If the file is present at the next startup it means the previous session
ended unexpectedly, so the user is offered a restore dialog.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QLabel,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)

_RECOVERY_FILENAME = "crash_recovery.json"
_SCHEMA_VERSION = 1


class CrashRecoveryManager:
    """Read/write/delete the recovery snapshot file in the config directory."""

    def __init__(self, config_dir: Path) -> None:
        self._path = config_dir / _RECOVERY_FILENAME

    # ── Public API ──────────────────────────────────────────────────────────────

    def save_snapshot(
        self,
        source_path: str,
        file_type: str,
        encoding: str,
        source_lang: str,
        target_lang: str,
        translations: list,
    ) -> bool:
        """Atomically write a recovery snapshot.  Returns True on success."""
        data = {
            "version": _SCHEMA_VERSION,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source_path": source_path,
            "file_type": file_type,
            "encoding": encoding,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "translations": translations,
        }
        try:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            tmp.replace(self._path)
            logger.debug(
                "Recovery snapshot saved: %d translation(s)", len(translations)
            )
            return True
        except Exception as exc:
            logger.warning("Failed to write recovery snapshot: %s", exc)
            return False

    def load_snapshot(self) -> Optional[dict]:
        """Return the snapshot dict, or None if absent/invalid."""
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Cannot read recovery snapshot: %s", exc)
            self.clear()
            return None
        if data.get("version") != _SCHEMA_VERSION:
            logger.warning("Recovery snapshot version mismatch — discarding")
            self.clear()
            return None
        return data

    def clear(self) -> None:
        """Delete the snapshot (called on clean exit or after restore/discard)."""
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception as exc:
            logger.warning("Failed to delete recovery snapshot: %s", exc)

    def has_snapshot(self) -> bool:
        return self._path.exists()


class CrashRecoveryDialog(QDialog):
    """Modal dialog shown on startup when a crash recovery snapshot is found."""

    def __init__(self, snapshot: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Restore Previous Session?"))
        self.setMinimumWidth(480)
        self.setWindowModality(Qt.ApplicationModal)
        self._source_exists = Path(snapshot.get("source_path", "")).exists()
        self._build_ui(snapshot)

    def _build_ui(self, snap: dict) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(24, 20, 24, 20)

        heading = QLabel(self.tr("Unsaved work detected"))
        heading.setStyleSheet("font-size: 16px; font-weight: 700;")
        layout.addWidget(heading)

        source_name = Path(snap.get("source_path", "")).name or "unknown"
        n = len(snap.get("translations", []))
        ts = snap.get("timestamp", "")
        try:
            ts_nice = datetime.fromisoformat(ts).strftime("%Y-%m-%d  %H:%M:%S")
        except ValueError:
            ts_nice = ts

        info_text = self.tr(
            "The application closed unexpectedly while editing:<br>"
            "<b>{name}</b><br><br>"
            "<b>{n}</b> translated string(s) were auto-saved at {ts}."
        ).format(name=source_name, n=n, ts=ts_nice)

        info = QLabel(info_text)
        info.setWordWrap(True)
        info.setTextFormat(Qt.RichText)
        layout.addWidget(info)

        if not self._source_exists:
            warn = QLabel(
                self.tr(
                    "The source file no longer exists at its saved location.\n"
                    "Restore is unavailable — you can only discard the snapshot."
                )
            )
            warn.setStyleSheet("color: #ef4444; font-size: 12px;")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        layout.addWidget(sep)

        hint = QLabel(
            self.tr(
                "Restore — reopen the file and apply the auto-saved translations.\n"
                "Discard — delete the snapshot and start fresh."
            )
        )
        hint.setStyleSheet("font-size: 11px; opacity: 0.65;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        btns = QDialogButtonBox()
        restore_btn = btns.addButton(self.tr("Restore"), QDialogButtonBox.AcceptRole)
        restore_btn.setEnabled(self._source_exists)
        restore_btn.setProperty("primary", True)
        btns.addButton(self.tr("Discard"), QDialogButtonBox.RejectRole)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)
