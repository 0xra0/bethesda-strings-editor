"""
Speaker (NPC) map dock panel.

Shows *who is speaking* the currently selected dialogue line, so a translator
keeps a character's voice consistent across Starfield's branching dialogue
trees.  The "who" is derived from the voice-type folder of the line's voice
clip (see :mod:`bethesda_strings.wwise_voice`) and turned into a friendly name /
faction / gender by :mod:`gui.speaker_map`.

This panel is a thin renderer: it never builds the voice index itself.  It calls
a *resolver* callback (wired to :class:`AudioPreviewPanel.resolve_speaker`, which
owns the shared :class:`VoiceIndex`) and renders whatever comes back through the
:meth:`update_speaker` slot.

Layout (QDockWidget):

  ┌─ Speaker ──────────────────────────────────────────────────────┐
  │  Sarah Morgan                                                   │
  │  female · Constellation · Named character                      │
  │  voice type: npcfsarahmorgan                                    │
  │                                                                │
  │  Also voiced by (shared line):                                 │
  │   • Generic citizen — Female 03                                │
  │                                                                │
  │  FormID: [0001A2B3] [Look up]                                  │
  │  (status line)                                                 │
  └────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDockWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
    QWidget,
)

from gui.speaker_map import SpeakerInfo, describe_voice_types

# Small colour hints per category for the badge line (kept subtle / theme-safe).
_CATEGORY_COLORS = {
    "Named character": "#7ec8ff",
    "Generic NPC": "#9ad29a",
    "Crowd (background)": "#b0a07e",
    "Announcer / system": "#c89af0",
    "Creature": "#e0a07e",
    "Robot": "#9ab0c8",
    "Player": "#ffd27e",
    "Non-verbal (expressions)": "#aaaaaa",
    "Test / non-dialogue": "#888888",
    "Unknown": "#888888",
}


class SpeakerPanel(QDockWidget):
    """Dockable panel showing the speaker behind the selected dialogue line."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SpeakerPanel")
        self.setWindowTitle(self.tr("Speaker"))
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # Resolver: form_id -> (eventually) update_speaker(form_id, voice_types).
        self._resolver: Optional[Callable[[int], None]] = None
        # The FormID we last asked about — lets us ignore stale async answers.
        self._expected_form_id: int = -1

        self._build_ui()
        self._show_placeholder(self.tr("(no dialogue line selected)"))

    # ── UI ──────────────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Primary speaker name.
        self._name_label = QLabel("")
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(self._name_label)

        # gender · faction · category badge line.
        self._badge_label = QLabel("")
        self._badge_label.setWordWrap(True)
        self._badge_label.setTextFormat(Qt.TextFormat.RichText)
        self._badge_label.setStyleSheet("font-size: 11px;")
        layout.addWidget(self._badge_label)

        # Raw voice type (mono, dim) — translators may want the exact folder name.
        self._raw_label = QLabel("")
        self._raw_label.setWordWrap(True)
        self._raw_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._raw_label.setStyleSheet(
            "font-family: monospace; font-size: 10px; color: #888;"
        )
        layout.addWidget(self._raw_label)

        # Shared-line alternatives.
        self._alt_label = QLabel("")
        self._alt_label.setWordWrap(True)
        self._alt_label.setTextFormat(Qt.TextFormat.RichText)
        self._alt_label.setStyleSheet("font-size: 11px; color: #bbb;")
        layout.addWidget(self._alt_label)

        layout.addStretch()

        # Manual FormID lookup (needed in .strings mode where the row id is a
        # string id, not the dialogue FormID).
        lookup_row = QHBoxLayout()
        lookup_row.addWidget(QLabel(self.tr("FormID:")))
        self._formid_edit = QLineEdit()
        self._formid_edit.setPlaceholderText(self.tr("hex, e.g. 0001A2B3"))
        self._formid_edit.returnPressed.connect(self._on_lookup_clicked)
        lookup_row.addWidget(self._formid_edit, stretch=1)
        self._lookup_btn = QPushButton(self.tr("Look up"))
        self._lookup_btn.clicked.connect(self._on_lookup_clicked)
        lookup_row.addWidget(self._lookup_btn)
        layout.addLayout(lookup_row)

        # Status line.
        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet("font-size: 10px; color: #888;")
        layout.addWidget(self._status_label)

        self.setWidget(root)

    # ── Public API ────────────────────────────────────────────────────────────────

    def set_resolver(self, resolver: Optional[Callable[[int], None]]) -> None:
        """Install the FormID -> speaker resolver (AudioPreviewPanel.resolve_speaker)."""
        self._resolver = resolver

    def update_for_row(self, row_data: Optional[dict]) -> None:
        """Called from main_window when the table selection changes."""
        if not row_data:
            self._expected_form_id = -1
            self._show_placeholder(self.tr("(no dialogue line selected)"))
            return

        # The table model keys the row by "id" (form_id in ESP mode, string id
        # in .strings mode); txt mode uses a non-int key, so guard the cast.
        raw_id = row_data.get("id", row_data.get("string_id", -1))
        form_id = raw_id if isinstance(raw_id, int) else -1

        # Only an ESP/ESM dialogue row carries a real dialogue FormID we can map
        # straight to a voice clip; in .strings mode the user looks one up by hand.
        if "_esp_entry" in row_data and form_id > 0:
            self._formid_edit.setText(f"{form_id:08X}")
            self._resolve(form_id)
        else:
            self._expected_form_id = -1
            self._show_placeholder(
                self.tr(
                    "Select a dialogue line in an ESP/ESM, or enter a FormID below "
                    "to look up the speaker."
                )
            )

    @Slot(int, object)
    def update_speaker(self, form_id: int, voice_types) -> None:
        """Slot for ``AudioPreviewPanel.speakerResolved``: render the result."""
        # Drop stale answers from a previous selection.
        if self._expected_form_id != -1 and form_id != self._expected_form_id:
            return

        if voice_types is None:
            self._show_placeholder(
                self.tr(
                    "Voice data not configured. Set the Voice Data directory in "
                    "Settings → Audio to map speakers."
                )
            )
            return

        infos = describe_voice_types(voice_types)
        if not infos:
            self._show_placeholder(
                self.tr("No voice clip found for FormID 0x%08X.") % max(form_id, 0)
            )
            return

        self._render(infos)

    # ── Rendering ─────────────────────────────────────────────────────────────────

    def _render(self, infos: list[SpeakerInfo]) -> None:
        primary = infos[0]

        name = primary.display_name
        if primary.is_cut:
            name += "  " + self.tr("(cut content)")
        self._name_label.setText(name)

        parts: list[str] = []
        if primary.gender:
            parts.append(primary.gender)
        if primary.faction and primary.faction not in primary.display_name:
            parts.append(primary.faction)
        color = _CATEGORY_COLORS.get(primary.category, "#888888")
        parts.append(f'<span style="color:{color};">{primary.category}</span>')
        if primary.source:
            parts.append(
                f'<span style="color:#c0a060;">{primary.source}</span>'
            )
        self._badge_label.setText(" · ".join(parts))

        self._raw_label.setText(
            self.tr("voice type: %s") % (primary.raw or "—")
        )

        # Shared lines: more than one distinct speaker recorded the same FormID.
        if len(infos) > 1:
            rows = "".join(
                f"&nbsp;&nbsp;• {i.display_name}<br>" for i in infos[1:]
            )
            self._alt_label.setText(
                self.tr("<b>Also voiced by (shared line):</b><br>") + rows
            )
            self._alt_label.show()
        else:
            self._alt_label.clear()
            self._alt_label.hide()

        self._status_label.clear()

    def _show_placeholder(self, msg: str) -> None:
        self._name_label.setText("")
        self._badge_label.setText("")
        self._raw_label.setText("")
        self._alt_label.clear()
        self._alt_label.hide()
        self._status_label.setText(msg)

    # ── Internals ─────────────────────────────────────────────────────────────────

    def _resolve(self, form_id: int) -> None:
        self._expected_form_id = form_id
        if self._resolver is None:
            self._show_placeholder(self.tr("Speaker lookup unavailable."))
            return
        self._status_label.setText(self.tr("Resolving speaker…"))
        self._resolver(form_id)

    @staticmethod
    def _parse_formid(text: str) -> Optional[int]:
        s = text.strip().lower()
        if s.startswith("0x"):
            s = s[2:]
        if not s:
            return None
        try:
            return int(s, 16)
        except ValueError:
            return None

    @Slot()
    def _on_lookup_clicked(self) -> None:
        fid = self._parse_formid(self._formid_edit.text())
        if fid is None:
            self._status_label.setText(self.tr("Enter a valid hex FormID."))
            return
        self._resolve(fid)
