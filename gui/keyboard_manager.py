"""
Centralized action registry for keyboard shortcut management and command palette.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from PySide6.QtGui import QAction, QKeySequence

logger = logging.getLogger(__name__)


@dataclass
class ActionEntry:
    """One registered application action."""

    id: str                                    # e.g. "translate_selected"
    name: str                                  # Display name in palette
    description: str                           # Tooltip / help text
    default_shortcut: str                      # QKeySequence string, or ""
    callback: Callable                         # Invoked when triggered
    category: str                              # Grouping: "File", "Translation", …
    enabled_check: Optional[Callable[[], bool]] = None
    keywords: tuple = ()                       # Extra search terms


class KeyboardManager:
    """
    Central registry for application actions.

    Wraps both QAction-backed commands and pure-Python callbacks into a
    uniform table that powers the command palette and the shortcut editor.
    Custom overrides are persisted via AppSettings.custom_shortcuts.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, ActionEntry] = {}
        self._custom: Dict[str, str] = {}          # id → override shortcut
        self._qactions: Dict[str, QAction] = {}    # id → backing QAction (optional)

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        entry: ActionEntry,
        qaction: Optional[QAction] = None,
    ) -> None:
        """Register an action entry, optionally linking it to a QAction."""
        self._entries[entry.id] = entry
        if qaction is not None:
            self._qactions[entry.id] = qaction

    def register_qaction(
        self,
        action_id: str,
        qaction: QAction,
        category: str,
        description: str = "",
        keywords: tuple = (),
        enabled_check: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Convenience: register directly from an existing QAction."""
        name = qaction.text().replace("&", "").rstrip("…").strip()
        entry = ActionEntry(
            id=action_id,
            name=name,
            description=description or qaction.toolTip() or name,
            default_shortcut=qaction.shortcut().toString(),
            callback=qaction.trigger,
            category=category,
            enabled_check=enabled_check,
            keywords=keywords,
        )
        self.register(entry, qaction)

    def unregister(self, action_id: str) -> None:
        self._entries.pop(action_id, None)
        self._qactions.pop(action_id, None)

    # ── Query ─────────────────────────────────────────────────────────────────

    def all_actions(self) -> List[ActionEntry]:
        return list(self._entries.values())

    def search(self, query: str) -> List[ActionEntry]:
        """Return actions matching *query* against name/description/category/keywords."""
        if not query.strip():
            return self.all_actions()
        q = query.lower()
        return [
            e for e in self._entries.values()
            if q in e.name.lower()
            or q in e.description.lower()
            or q in e.category.lower()
            or any(q in kw.lower() for kw in e.keywords)
        ]

    def is_enabled(self, action_id: str) -> bool:
        e = self._entries.get(action_id)
        if e is None:
            return False
        if e.enabled_check is not None:
            try:
                return bool(e.enabled_check())
            except Exception:
                return False
        return True

    # ── Shortcut management ───────────────────────────────────────────────────

    def effective_shortcut(self, action_id: str) -> str:
        """Return the currently active shortcut (custom override or default)."""
        if action_id in self._custom:
            return self._custom[action_id]
        e = self._entries.get(action_id)
        return e.default_shortcut if e else ""

    def set_custom_shortcut(self, action_id: str, shortcut: str) -> None:
        """Override a shortcut. Empty string resets to the default."""
        if shortcut:
            self._custom[action_id] = shortcut
        else:
            self._custom.pop(action_id, None)
        self._apply_to_qaction(action_id)

    def apply_all_custom_shortcuts(self) -> None:
        """Push all stored custom overrides to their backing QActions."""
        for action_id in self._custom:
            self._apply_to_qaction(action_id)

    def _apply_to_qaction(self, action_id: str) -> None:
        qa = self._qactions.get(action_id)
        if qa is None:
            return
        sc = self.effective_shortcut(action_id)
        qa.setShortcut(QKeySequence(sc))

    def load_custom_shortcuts(self, overrides: dict) -> None:
        self._custom = dict(overrides or {})

    def get_custom_shortcuts(self) -> dict:
        return dict(self._custom)

    # ── Display helpers ───────────────────────────────────────────────────────

    @staticmethod
    def shortcut_display(shortcut: str) -> str:
        """Convert a shortcut string to a platform-native display form."""
        if not shortcut:
            return ""
        return QKeySequence(shortcut).toString(QKeySequence.NativeText)
