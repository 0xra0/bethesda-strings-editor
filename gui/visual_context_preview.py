"""
Visual Context Preview dock panel.

Renders the currently selected string inside a faithful recreation of
Bethesda's Starfield UI boxes so translators can judge wrap behaviour,
overflow, and readability before the mod goes live.

Uses the actual TTF fonts extracted from the game's Scaleform SWF files
(``data/fonts/RF_35_M.ttf`` for body text, ``data/fonts/NB_Architekt_Light.ttf``
for English), registered with ``QFontDatabase`` on first use.  Falls back to
the system sans-serif when the game fonts are absent.

Layout (QDockWidget, docks at bottom or right):

  ┌─ Visual Context Preview ─────────────────────────────────────────────────┐
  │  Context: [Auto-detect ▼]  View: [Source] [Translation] [Both]           │
  ├──────────────────────────────────────────────────────────────────────────┤
  │  ┌────────────── Starfield UI box (source) ─────────────┐               │
  │  │  You should see New Atlantis before you die, at       │               │
  │  │  least once in your life.                             │               │
  │  └───────────────────────────────────────────────────────┘               │
  │  ┌────────────── Starfield UI box (translation) ────────┐               │
  │  │  Ти маєш побачити Нову Атлантиду, принаймні раз у    │               │
  │  │  своєму житті, перш ніж помреш.                      │               │
  │  └───────────────────────────────────────────────────────┘               │
  ├──────────────────────────────────────────────────────────────────────────┤
  │  Src: 68 chars  Trl: 79 chars (+16%)  Lines: 2/3  ✓ Fits               │
  └──────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QRect, Slot
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QFontMetrics, QLinearGradient,
    QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDockWidget, QHBoxLayout, QLabel,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

# ── Font registration ─────────────────────────────────────────────────────────

_FONTS_DIR = Path(__file__).parent.parent / "data" / "fonts"

_FONT_KEYS = {
    "body":      "RF_35_M.ttf",          # Starfield Cyrillic body (RF_35_M)
    "bold":      "RF_55_M.ttf",          # Starfield Cyrillic bold (RF_55_M)
    "latin":     "NB_Architekt_Light.ttf",
    "latin_bold":"NB_Architekt.ttf",
}

_registered: dict[str, str] = {}   # key → Qt family name


def _ensure_fonts() -> None:
    """Register game TTF files with Qt on first call."""
    if _registered:
        return
    for key, filename in _FONT_KEYS.items():
        path = _FONTS_DIR / filename
        if not path.exists():
            continue
        fid = QFontDatabase.addApplicationFont(str(path))
        if fid >= 0:
            families = QFontDatabase.applicationFontFamilies(fid)
            if families:
                _registered[key] = families[0]
                logger.debug("Registered font %s as %r", filename, families[0])


def _make_font(bold: bool, size: int, mono: bool = False) -> QFont:
    """Return the best available font for the given parameters."""
    _ensure_fonts()
    if mono:
        f = QFont("Consolas, Monospace")
        f.setStyleHint(QFont.Monospace)
        f.setBold(bold)
        f.setPointSize(size)
        return f
    # Prefer game font; fall back to system sans-serif
    if bold and "bold" in _registered:
        family = _registered["bold"]
    elif not bold and "body" in _registered:
        family = _registered["body"]
    else:
        family = ""
    f = QFont(family) if family else QFont()
    f.setStyleHint(QFont.SansSerif)
    f.setBold(bold and not family)
    f.setPointSize(size)
    return f


# ── Game-tag stripper ─────────────────────────────────────────────────────────

_TAG_RE = re.compile(
    r"<Alias=[^>]*>|<GlobalValue=[^>]*>|<font[^>]*>|</font>|<color[^>]*>|</color>"
    r"|<br\s*/?>|\[PLYR\]|%[1-9]?\$?[sd]|\{[0-9]+\}",
    re.IGNORECASE,
)


def _clean_for_preview(text: str) -> str:
    """Strip game markup so the renderer sees plain text."""
    return _TAG_RE.sub("", text).strip()


# ── UI context presets ────────────────────────────────────────────────────────
# Virtual pixel dimensions matching Starfield's 1280×720 native UI canvas.

_PRESETS: dict[str, dict] = {
    # Dialogue uses game-accurate rendering (see _render_dialogue_scene).
    # These values are only used by the stats bar (char/line counts); the
    # visual render ignores them entirely.
    "dialogue": {
        "label": "Dialogue / Subtitle",
        # Native SWF sprite: 597×147 px; stage 1920×1080.
        # Box spans ~91% of stage width, height ~14.8% of stage height.
        # Font: RF_35_M ($NB_Grotesk_Semibold) fontHeight=520 twips = 26 pt.
        "box_w": 597, "box_h": 147,
        "font_size": 26, "font_bold": False, "mono": False,
        "max_lines": 4,
        "bg": "#0d111a", "fg": "#ffffff",
        "frame_bg": "#00000080", "border": "#ffffff33",
        "corner_r": 0, "pad": 10,
        "hint": "~4 lines at 1920×1080",
    },
    "quest": {
        "label": "Quest Objective",
        "box_w": 400, "box_h": 58,
        "font_size": 15, "font_bold": False, "mono": False,
        "max_lines": 2,
        "bg": "#0d0d1a", "fg": "#c8c8d8",
        "frame_bg": "#16162a", "border": "#3a3a6a",
        "corner_r": 3, "pad": 10,
        "hint": "2 lines",
    },
    "book": {
        "label": "Book / Document",
        "box_w": 480, "box_h": 340,
        "font_size": 13, "font_bold": False, "mono": False,
        "max_lines": 22,
        "bg": "#1a1510", "fg": "#d8c898",
        "frame_bg": "#221e14", "border": "#6a5830",
        "corner_r": 2, "pad": 14,
        "hint": "~22 lines",
    },
    "note": {
        "label": "Note / Letter",
        "box_w": 440, "box_h": 260,
        "font_size": 13, "font_bold": False, "mono": False,
        "max_lines": 16,
        "bg": "#1a1510", "fg": "#d8c898",
        "frame_bg": "#221e14", "border": "#6a5830",
        "corner_r": 2, "pad": 14,
        "hint": "~16 lines",
    },
    "terminal": {
        "label": "Terminal Screen",
        "box_w": 560, "box_h": 300,
        "font_size": 12, "font_bold": False, "mono": True,
        "max_lines": 20,
        "bg": "#030e03", "fg": "#00cc00",
        "frame_bg": "#030e03", "border": "#009900",
        "corner_r": 0, "pad": 12,
        "hint": "~20 lines (monospace)",
    },
    "ui": {
        "label": "UI Label / Menu",
        "box_w": 340, "box_h": 42,
        "font_size": 15, "font_bold": True, "mono": False,
        "max_lines": 2,
        "bg": "#0a0a18", "fg": "#ffffff",
        "frame_bg": "#18183a", "border": "#4444aa",
        "corner_r": 2, "pad": 10,
        "hint": "1–2 lines",
    },
    "general": {
        "label": "General",
        "box_w": 520, "box_h": 130,
        "font_size": 15, "font_bold": False, "mono": False,
        "max_lines": 7,
        "bg": "#0d0d1a", "fg": "#d8d8e8",
        "frame_bg": "#16162a", "border": "#3a3a6a",
        "corner_r": 4, "pad": 12,
        "hint": "~7 lines",
    },
}

# Maps StringType enum names → preset key
_TYPE_TO_PRESET: dict[str, str] = {
    "DIALOGUE": "dialogue",
    "QUEST":    "quest",
    "BOOK":     "book",
    "NOTE":     "note",
    "TERMINAL": "terminal",
    "UI":       "ui",
    "SYSTEM":   "ui",
    "UNKNOWN":  "general",
}


# ── Line-wrap helper ──────────────────────────────────────────────────────────

def _wrap_text(text: str, font: QFont, box_w: int) -> list[str]:
    """Word-wrap *text* to fit *box_w* pixels using *font*.

    Honours explicit newlines in the source text.
    Returns a list of rendered lines.
    """
    fm = QFontMetrics(font)
    lines: list[str] = []
    for paragraph in text.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip() if current else word
            if fm.horizontalAdvance(candidate) <= box_w:
                current = candidate
            else:
                if current:
                    lines.append(current)
                # If single word is wider than box, still add it (it'll clip)
                current = word
        if current:
            lines.append(current)
    return lines


# ── Background tile cache ─────────────────────────────────────────────────────
# 50×50 RGBA noise texture extracted from the game's Scaleform SWF (shape 79 /
# DefineBitsLossless2 id 78 in dialoguemenu.swf).  White pixels at alpha 0-92
# are tiled over the subtitle panel fill to add the characteristic grain.

_DATA_DIR = Path(__file__).parent.parent / "data"
_bg_tile_px: Optional[QPixmap] = None


def _ensure_bg_tile() -> Optional[QPixmap]:
    global _bg_tile_px
    if _bg_tile_px is not None:
        return _bg_tile_px
    path = _DATA_DIR / "dialogue_bg_tile.png"
    if path.exists():
        _bg_tile_px = QPixmap(str(path))
    return _bg_tile_px


# ── Dialogue scene renderer ───────────────────────────────────────────────────
# Faithful recreation of the Starfield dialogue subtitle box.
#
# Measurements from dialoguemenu.swf (JPEXS export, verified pixel-by-pixel):
#   Panel sprite:   597 × 147 px at SWF native scale
#   Border top:     1 px, white alpha=51  (~20 %)
#   Border left/rt: 1 px, white alpha=38  (~15 %)
#   Border bottom:  1 px, white alpha=19  (~ 7 %)
#   Fill:           black alpha=127       (~50 %)
#   Noise tile:     50×50, white 0-92 alpha, tiled over fill
#   Font:           $NB_Grotesk_Semibold → RF_35_M (UK), fontHeight=520 = 26 pt
#   Stage:          1920 × 1080 (Scaleform full-screen)
#   Box position:   full-width with ~4.5 % margin; ~8 % from bottom

_STAGE_W = 1920.0
_STAGE_H = 1080.0
_BOX_H_RATIO  = 147 / _STAGE_H        # box height fraction
_MARGIN_RATIO = 0.045                  # horizontal margin fraction
_BOTTOM_RATIO = 0.074                  # box bottom margin fraction
_NATIVE_FONT_PT = 26                   # fontHeight=520/20


def _render_dialogue_scene(
    text: str,
    label: str,
    canvas_w: int,
    canvas_h: int,
) -> tuple[QPixmap, int, int, bool]:
    """Game-accurate Starfield subtitle panel render.

    Returns (pixmap, line_count, max_lines, overflows).
    """
    # ── Layout maths ──────────────────────────────────────────────────────────
    margin_x   = max(8, int(canvas_w * _MARGIN_RATIO))
    box_w      = canvas_w - margin_x * 2
    font_scale = canvas_w / _STAGE_W          # proportional to stage width
    font_size  = max(8, int(_NATIVE_FONT_PT * font_scale))

    font = _make_font(False, font_size)
    fm   = QFontMetrics(font)
    line_h = fm.height() + max(1, int(2 * font_scale))

    # The native panel height tracks the box-width scale
    box_h = max(int(canvas_h * _BOX_H_RATIO), fm.height() + 16)

    pad_x = max(6, int(10 * font_scale))
    pad_y = max(4, int(8  * font_scale))
    text_w = box_w - pad_x * 2

    bottom_margin = max(8, int(canvas_h * _BOTTOM_RATIO))
    box_y = canvas_h - bottom_margin - box_h
    box_x = margin_x

    # Speaker name font (smaller than subtitle)
    name_font_pt = max(7, int(font_size * 0.82))
    name_font = _make_font(False, name_font_pt)

    # ── Word-wrap ──────────────────────────────────────────────────────────────
    clean   = _clean_for_preview(text)
    lines   = _wrap_text(clean, font, text_w)
    n_lines = len(lines)
    max_lines = max(1, (box_h - pad_y * 2) // line_h)
    overflows = n_lines > max_lines

    # ── Render ─────────────────────────────────────────────────────────────────
    px = QPixmap(canvas_w, canvas_h)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing, False)  # pixel-accurate borders

    # Background: atmospheric dark gradient (deep-space sky)
    grad = QLinearGradient(0, 0, 0, canvas_h)
    grad.setColorAt(0.0, QColor("#0e1420"))
    grad.setColorAt(0.5, QColor("#0a1018"))
    grad.setColorAt(1.0, QColor("#06080e"))
    painter.fillRect(0, 0, canvas_w, canvas_h, QBrush(grad))

    # Subtle star-field dots (deterministic for stable rendering)
    painter.setPen(QColor(255, 255, 255, 60))
    for i in range(40):
        sx = (i * 137 + 47) % canvas_w
        sy = (i * 251 + 83) % (box_y - 10)
        painter.drawPoint(sx, sy)

    # Label ("Source" / "Translation") at top-left
    lbl_font = _make_font(True, max(7, name_font_pt - 1))
    painter.setFont(lbl_font)
    painter.setPen(QColor("#556688"))
    painter.drawText(box_x, max(14, int(14 * font_scale)), label)

    # Speaker name above box
    name_gap = max(4, int(6 * font_scale))
    name_y   = box_y - name_gap
    painter.setFont(name_font)
    painter.setPen(QColor("#b0b8cc"))
    painter.drawText(box_x, name_y, "NPC Name")

    # Panel fill: black at 50 % opacity (alpha=127)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(0, 0, 0, 127))
    painter.drawRect(box_x, box_y, box_w, box_h)

    # Noise tile overlay (grain from the original SWF texture)
    tile = _ensure_bg_tile()
    if tile and not tile.isNull():
        painter.setOpacity(0.28)
        painter.drawTiledPixmap(box_x, box_y, box_w, box_h, tile)
        painter.setOpacity(1.0)

    # Borders (pixel-exact from sprite measurements)
    # Top: white alpha=51
    painter.setPen(QPen(QColor(255, 255, 255, 51), 1, Qt.SolidLine))
    painter.drawLine(box_x, box_y, box_x + box_w - 1, box_y)
    # Left: white alpha=38
    painter.setPen(QPen(QColor(255, 255, 255, 38), 1, Qt.SolidLine))
    painter.drawLine(box_x, box_y, box_x, box_y + box_h - 1)
    # Right: white alpha=38
    painter.drawLine(box_x + box_w - 1, box_y, box_x + box_w - 1, box_y + box_h - 1)
    # Bottom: white alpha=19
    painter.setPen(QPen(QColor(255, 255, 255, 19), 1, Qt.SolidLine))
    painter.drawLine(box_x, box_y + box_h - 1, box_x + box_w - 1, box_y + box_h - 1)

    # Subtitle text
    painter.setFont(font)
    text_x = box_x + pad_x
    text_y = box_y + pad_y + fm.ascent()
    fg_col  = QColor("#ffffff")
    ov_col  = QColor("#ff4444")

    for i, line in enumerate(lines):
        if text_y > box_y + box_h - pad_y:
            break
        painter.setPen(ov_col if i >= max_lines else fg_col)
        painter.drawText(text_x, text_y, line)
        text_y += line_h

    # Overflow badge
    if overflows:
        bw, bh = 76, 18
        bx = box_x + box_w - bw - 4
        by = box_y + box_h - bh - 4
        painter.fillRect(bx, by, bw, bh, QColor("#cc2222"))
        bf = _make_font(True, max(6, font_size - 4))
        painter.setFont(bf)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(bx + 4, by + 13, "OVERFLOW")

    painter.end()
    return px, n_lines, max_lines, overflows


# ── Generic render helper ─────────────────────────────────────────────────────

def _render_preview(
    text: str,
    preset_key: str,
    label: str,       # "Source" / "Translation"
    canvas_w: int,
    canvas_h: int,
) -> tuple[QPixmap, int, int, bool]:
    """
    Render text into a *canvas_w* × *canvas_h* pixmap styled as a Bethesda UI box.

    Dialogue context uses the game-accurate ``_render_dialogue_scene`` renderer.
    Returns (pixmap, line_count, max_lines, overflows).
    """
    if preset_key == "dialogue":
        return _render_dialogue_scene(text, label, canvas_w, canvas_h)

    p = _PRESETS[preset_key]
    font = _make_font(p["font_bold"], p["font_size"], p["mono"])
    fm = QFontMetrics(font)
    line_h = fm.height() + 2  # small leading

    # Scale the virtual box dimensions to fit within canvas
    native_box_w = p["box_w"]
    native_box_h = p["box_h"]
    pad = p["pad"]

    # Fit the box (with outer margin) into canvas
    margin = 16
    scale = min(
        (canvas_w - margin * 2) / (native_box_w + pad * 2),
        (canvas_h - margin * 2 - 24) / (native_box_h + pad * 2 + 20),  # 20 for label
    )
    scale = max(0.4, min(scale, 2.5))

    box_w = int(native_box_w * scale)
    box_h = int(native_box_h * scale)
    scaled_pad = max(6, int(pad * scale))
    text_w = box_w - scaled_pad * 2
    scaled_font_size = max(7, int(p["font_size"] * scale))
    font.setPointSize(scaled_font_size)
    fm = QFontMetrics(font)
    line_h = fm.height() + max(1, int(2 * scale))

    # Word-wrap text
    clean = _clean_for_preview(text)
    lines = _wrap_text(clean, font, text_w)
    n_lines = len(lines)
    max_lines = max(1, box_h // line_h)
    overflows = n_lines > max_lines

    # --- Draw ---
    px = QPixmap(canvas_w, canvas_h)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing)

    # Outer canvas background
    painter.fillRect(0, 0, canvas_w, canvas_h, QColor("#1a1a2a"))

    # Label text (Source / Translation)
    lbl_font = QFont(font)
    lbl_font.setPointSize(max(7, scaled_font_size - 2))
    lbl_font.setBold(True)
    painter.setFont(lbl_font)
    painter.setPen(QColor("#888888"))
    box_x = (canvas_w - box_w) // 2
    box_y = margin + fm.height()
    painter.drawText(box_x, margin + fm.height() - 4, label)

    # Frame background
    frame_rect = QRect(box_x, box_y, box_w, box_h)
    r = p["corner_r"]
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(p["frame_bg"]))
    if r > 0:
        painter.drawRoundedRect(frame_rect, r * scale, r * scale)
    else:
        painter.fillRect(frame_rect, QColor(p["frame_bg"]))

    # Border
    border_col = QColor(p["border"])
    painter.setPen(QPen(border_col, max(1, int(scale))))
    painter.setBrush(Qt.NoBrush)
    if r > 0:
        painter.drawRoundedRect(frame_rect, r * scale, r * scale)
    else:
        painter.drawRect(frame_rect)

    # Text rendering
    painter.setFont(font)
    text_x = box_x + scaled_pad
    text_y = box_y + scaled_pad + fm.ascent()
    fg = QColor(p["fg"])
    overflow_fg = QColor("#ff4444")

    for i, line in enumerate(lines):
        if text_y > box_y + box_h - scaled_pad:
            break
        col = overflow_fg if i >= max_lines else fg
        painter.setPen(col)
        painter.drawText(text_x, text_y, line)
        text_y += line_h

    # Overflow badge
    if overflows:
        badge_x = box_x + box_w - 80
        badge_y = box_y + box_h - 22
        painter.fillRect(badge_x, badge_y, 76, 18, QColor("#cc2222"))
        badge_font = QFont(font)
        badge_font.setPointSize(max(6, scaled_font_size - 3))
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(badge_x + 4, badge_y + 13, "OVERFLOW")

    painter.end()
    return px, n_lines, max_lines, overflows


# ── Main dock widget ──────────────────────────────────────────────────────────

class VisualContextPreview(QDockWidget):
    """Dockable panel that renders strings as Bethesda UI boxes."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("VisualContextPreview")
        self.setWindowTitle(self.tr("Visual Context Preview"))
        self.setMinimumWidth(320)
        self.setMinimumHeight(200)

        self._source_text: str = ""
        self._translated_text: str = ""
        self._string_type: str = "UNKNOWN"
        self._preset_override: Optional[str] = None

        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("vcpRoot")
        vlay = QVBoxLayout(root)
        vlay.setContentsMargins(6, 4, 6, 4)
        vlay.setSpacing(4)

        # ── Toolbar ────────────────────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        toolbar.addWidget(QLabel(self.tr("Context:")))
        self._ctx_combo = QComboBox()
        self._ctx_combo.setToolTip(
            self.tr("Override the auto-detected UI context for this string")
        )
        self._ctx_combo.addItem(self.tr("Auto-detect"), None)
        for key, preset in _PRESETS.items():
            self._ctx_combo.addItem(preset["label"], key)
        self._ctx_combo.currentIndexChanged.connect(self._on_ctx_changed)
        toolbar.addWidget(self._ctx_combo)

        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel(self.tr("View:")))

        self._btn_src = QToolButton()
        self._btn_src.setText(self.tr("Source"))
        self._btn_src.setCheckable(True)
        self._btn_src.setChecked(False)

        self._btn_trl = QToolButton()
        self._btn_trl.setText(self.tr("Translation"))
        self._btn_trl.setCheckable(True)
        self._btn_trl.setChecked(True)

        self._btn_both = QToolButton()
        self._btn_both.setText(self.tr("Both"))
        self._btn_both.setCheckable(True)
        self._btn_both.setChecked(False)

        grp = QButtonGroup(self)
        grp.setExclusive(True)
        grp.addButton(self._btn_src)
        grp.addButton(self._btn_trl)
        grp.addButton(self._btn_both)
        grp.buttonToggled.connect(self._on_view_changed)

        for btn in (self._btn_src, self._btn_trl, self._btn_both):
            btn.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            toolbar.addWidget(btn)

        toolbar.addStretch()
        vlay.addLayout(toolbar)

        # ── Preview area ────────────────────────────────────────────────────
        self._preview_area = _PreviewArea(self)
        self._preview_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vlay.addWidget(self._preview_area, 1)

        # ── Stats bar ───────────────────────────────────────────────────────
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(12)
        self._chars_label = QLabel("")
        self._lines_label = QLabel("")
        self._overflow_label = QLabel("")
        self._overflow_label.setStyleSheet("color: #ff5555; font-weight: bold;")
        self._hint_label = QLabel("")
        self._hint_label.setStyleSheet("color: #888888;")
        for w in (self._chars_label, self._lines_label, self._overflow_label, self._hint_label):
            stats_layout.addWidget(w)
        stats_layout.addStretch()
        vlay.addLayout(stats_layout)

        self.setWidget(root)

        # initial blank render
        self._refresh()

    # ── Slots ─────────────────────────────────────────────────────────────────

    @Slot()
    def _on_ctx_changed(self) -> None:
        self._preset_override = self._ctx_combo.currentData()
        self._refresh()

    @Slot()
    def _on_view_changed(self) -> None:
        self._refresh()

    def update_string(self, row_data: Optional[dict]) -> None:
        """Called by MainWindow when selection changes."""
        if row_data is None:
            self._source_text = ""
            self._translated_text = ""
            self._string_type = "UNKNOWN"
        else:
            self._source_text = row_data.get("original", "") or ""
            self._translated_text = row_data.get("translated", "") or ""
            try:
                from gui.string_type_detector import classify
                stype = classify(self._source_text)
                self._string_type = stype.name
            except Exception:
                self._string_type = "UNKNOWN"
        self._refresh()

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _active_preset_key(self) -> str:
        if self._preset_override:
            return self._preset_override
        return _TYPE_TO_PRESET.get(self._string_type, "general")

    def _refresh(self) -> None:
        preset_key = self._active_preset_key()
        p = _PRESETS[preset_key]

        view_src  = self._btn_src.isChecked()
        view_both = self._btn_both.isChecked()

        area_w = max(200, self._preview_area.width())
        area_h = max(120, self._preview_area.height())

        if view_both:
            half_w = (area_w - 6) // 2
            src_px, _, max_l, _ = _render_preview(
                self._source_text or "(no text)", preset_key,
                self.tr("Source"), half_w, area_h,
            )
            trl_px, trl_lines, _, trl_ov = _render_preview(
                self._translated_text or "(no translation)", preset_key,
                self.tr("Translation"), half_w, area_h,
            )
            combined = QPixmap(area_w, area_h)
            combined.fill(QColor("#111120"))
            p2 = QPainter(combined)
            p2.drawPixmap(0, 0, src_px)
            p2.drawPixmap(half_w + 6, 0, trl_px)
            p2.end()
            self._preview_area.set_pixmap(combined)
            n_lines = trl_lines
            overflows = trl_ov
        elif view_src:
            px, n_lines, max_l, overflows = _render_preview(
                self._source_text or "(no text)", preset_key,
                self.tr("Source"), area_w, area_h,
            )
            self._preview_area.set_pixmap(px)
        else:
            px, n_lines, max_l, overflows = _render_preview(
                self._translated_text or "(no translation)", preset_key,
                self.tr("Translation"), area_w, area_h,
            )
            self._preview_area.set_pixmap(px)

        # Stats
        src_len = len(_clean_for_preview(self._source_text))
        trl_len = len(_clean_for_preview(self._translated_text))
        if src_len > 0:
            ratio = int((trl_len - src_len) / src_len * 100)
            sign = "+" if ratio >= 0 else ""
            self._chars_label.setText(
                self.tr("Src: {s}  Trl: {t} ({r}%)").format(
                    s=src_len, t=trl_len, r=f"{sign}{ratio}"
                )
            )
        else:
            self._chars_label.setText(
                self.tr("Chars: {n}").format(n=trl_len)
            )
        if view_both or not view_src:
            self._lines_label.setText(
                self.tr("Lines: {n}/{m}").format(n=n_lines, m=max_l)
            )
            if overflows:
                self._overflow_label.setText(self.tr("OVERFLOW"))
            else:
                self._overflow_label.setText(self.tr("✓ Fits"))
                self._overflow_label.setStyleSheet("color: #44cc44; font-weight: bold;")
        else:
            self._lines_label.setText("")
            self._overflow_label.setText("")
        if overflows:
            self._overflow_label.setStyleSheet("color: #ff5555; font-weight: bold;")

        self._hint_label.setText(p["hint"])

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()


class _PreviewArea(QWidget):
    """Displays the rendered QPixmap, refreshes on resize."""

    def __init__(self, dock: VisualContextPreview) -> None:
        super().__init__()
        self._dock = dock
        self._pixmap: Optional[QPixmap] = None
        self.setMinimumHeight(100)

    def set_pixmap(self, px: QPixmap) -> None:
        self._pixmap = px
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._dock._refresh()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#111120"))
        if self._pixmap and not self._pixmap.isNull():
            painter.drawPixmap(0, 0, self._pixmap)
        painter.end()
