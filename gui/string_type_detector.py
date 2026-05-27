"""
String content-type classifier and themed icon factory.

Icons are Phosphor Icons (MIT) rendered on-demand via QSvgRenderer and cached
per (StringType, dark_mode) pair so we never hit disk or the network.
"""

import re
from enum import Enum, auto
from typing import Optional

# ── Type enum ─────────────────────────────────────────────────────────────────

class StringType(Enum):
    DIALOGUE = auto()   # speech / conversation
    QUEST    = auto()   # objective / mission text
    BOOK     = auto()   # long document / lore book
    NOTE     = auto()   # note / letter / journal entry
    TERMINAL = auto()   # in-game computer terminal text
    UI       = auto()   # interface label / button / menu item
    SYSTEM   = auto()   # technical string with format markers
    UNKNOWN  = auto()   # unclassified


# ── Phosphor Icons SVG paths (MIT licence, phosphoricons.com) ─────────────────
# viewBox="0 0 256 256", fill="currentColor"

_PATHS: dict[StringType, str] = {
    StringType.DIALOGUE: (
        "M128,24A104,104,0,0,0,36.18,176.88L24.83,210.93a16,16,0,0,0,20.24,20.24"
        "l34.05-11.35A104,104,0,1,0,128,24Zm0,192a87.87,87.87,0,0,1-44.06-11.81,"
        "8,8,0,0,0-6.54-.67L40,216,52.47,178.6a8,8,0,0,0-.66-6.54A88,88,0,1,1,128,216Z"
    ),
    StringType.QUEST: (
        "M42.76,50A8,8,0,0,0,40,56V224a8,8,0,0,0,16,0V179.77c26.79-21.16,49.87-9.75,"
        "76.45,3.41,16.4,8.11,34.06,16.85,53,16.85,13.93,0,28.54-4.75,43.82-18a8,8,0,"
        "0,0,2.76-6V56A8,8,0,0,0,218.76,50c-28,24.23-51.72,12.49-79.21-1.12C111.07,34.76,"
        "78.78,18.79,42.76,50ZM216,172.25c-26.79,21.16-49.87,9.74-76.45-3.41-25-12.35,"
        "-52.81-26.13-83.55-8.4V59.79c26.79-21.16,49.87-9.75,76.45,3.4,25,12.35,"
        "52.82,26.13,83.55,8.4Z"
    ),
    StringType.BOOK: (
        "M232,48H160a40,40,0,0,0-32,16A40,40,0,0,0,96,48H24a8,8,0,0,0-8,8V200a8,8,0,"
        "0,0,8,8H96a24,24,0,0,1,24,24,8,8,0,0,0,16,0,24,24,0,0,1,24-24h72a8,8,0,0,0,"
        "8-8V56A8,8,0,0,0,232,48ZM96,192H32V64H96a24,24,0,0,1,24,24V200A39.81,39.81,"
        "0,0,0,96,192Zm128,0H160a39.81,39.81,0,0,0-24,8V88a24,24,0,0,1,24-24h64Z"
    ),
    StringType.NOTE: (
        "M88,96a8,8,0,0,1,8-8h64a8,8,0,0,1,0,16H96A8,8,0,0,1,88,96Zm8,40h64a8,8,0,"
        "0,0,0-16H96a8,8,0,0,0,0,16Zm32,16H96a8,8,0,0,0,0,16h32a8,8,0,0,0,0-16ZM224,"
        "48V156.69A15.86,15.86,0,0,1,219.31,168L168,219.31A15.86,15.86,0,0,1,156.69,"
        "224H48a16,16,0,0,1-16-16V48A16,16,0,0,1,48,32H208A16,16,0,0,1,224,48ZM48,"
        "208H152V160a8,8,0,0,1,8-8h48V48H48Zm120-40v28.7L196.69,168Z"
    ),
    StringType.TERMINAL: (
        "M128,128a8,8,0,0,1-3,6.25l-40,32a8,8,0,1,1-10-12.5L107.19,128,75,102.25a8,8,"
        "0,1,1,10-12.5l40,32A8,8,0,0,1,128,128Zm48,24H136a8,8,0,0,0,0,16h40a8,8,0,0,"
        "0,0-16Zm56-96V200a16,16,0,0,1-16,16H40a16,16,0,0,1-16-16V56A16,16,0,0,1,40,"
        "40H216A16,16,0,0,1,232,56ZM216,200V56H40V200H216Z"
    ),
    StringType.UI: (
        "M216,40H40A16,16,0,0,0,24,56V200a16,16,0,0,0,16,16H216a16,16,0,0,0,16-16V56"
        "A16,16,0,0,0,216,40Zm0,160H40V56H216V200ZM80,84A12,12,0,1,1,68,72,12,12,0,0,"
        "1,80,84Zm40,0a12,12,0,1,1-12-12A12,12,0,0,1,120,84Z"
    ),
    StringType.SYSTEM: (
        "M128,80a48,48,0,1,0,48,48A48.05,48.05,0,0,0,128,80Zm0,80a32,32,0,1,1,32-32"
        "A32,32,0,0,1,128,160Zm88-29.84q.06-2.16,0-4.32l14.92-18.64a8,8,0,0,0,1.48-7.06"
        ",107.21,107.21,0,0,0-10.88-26.25,8,8,0,0,0-6-3.93l-23.72-2.64q-1.48-1.56-3-3"
        "L186,40.54a8,8,0,0,0-3.94-6,107.71,107.71,0,0,0-26.25-10.87,8,8,0,0,0-7.06,"
        "1.49L130.16,40Q128,40,125.84,40L107.2,25.11a8,8,0,0,0-7.06-1.48A107.6,107.6,"
        "0,0,0,73.89,34.51a8,8,0,0,0-3.93,6L67.32,64.27q-1.56,1.49-3,3L40.54,70a8,8,"
        "0,0,0-6,3.94,107.71,107.71,0,0,0-10.87,26.25,8,8,0,0,0,1.49,7.06L40,125.84"
        "Q40,128,40,130.16L25.11,148.8a8,8,0,0,0-1.48,7.06,107.21,107.21,0,0,0,10.88,"
        "26.25,8,8,0,0,0,6,3.93l23.72,2.64q1.49,1.56,3,3L70,215.46a8,8,0,0,0,3.94,6,"
        "107.71,107.71,0,0,0,26.25,10.87,8,8,0,0,0,7.06-1.49L125.84,216q2.16.06,4.32,"
        "0l18.64,14.92a8,8,0,0,0,7.06,1.48,107.21,107.21,0,0,0,26.25-10.88,8,8,0,0,0,"
        "3.93-6l2.64-23.72q1.56-1.48,3-3L215.46,186a8,8,0,0,0,6-3.94,107.71,107.71,"
        "0,0,0,10.87-26.25,8,8,0,0,0-1.49-7.06Zm-16.1-6.5a73.93,73.93,0,0,1,0,8.68,"
        "8,8,0,0,0,1.74,5.48l14.19,17.73a91.57,91.57,0,0,1-6.23,15L187,173.11a8,8,0,"
        "0,0-5.1,2.64,74.11,74.11,0,0,1-6.14,6.14,8,8,0,0,0-2.64,5.1l-2.51,22.58a91.32,"
        "91.32,0,0,1-15,6.23l-17.74-14.19a8,8,0,0,0-5-1.75h-.48a73.93,73.93,0,0,1-8.68,"
        "0,8,8,0,0,0-5.48,1.74L100.45,215.8a91.57,91.57,0,0,1-15-6.23L82.89,187a8,8,0,"
        "0,0-2.64-5.1,74.11,74.11,0,0,1-6.14-6.14,8,8,0,0,0-5.1-2.64L46.43,170.6a91.32,"
        "91.32,0,0,1-6.23-15l14.19-17.74a8,8,0,0,0,1.74-5.48,73.93,73.93,0,0,1,0-8.68,"
        "8,8,0,0,0-1.74-5.48L40.2,100.45a91.57,91.57,0,0,1,6.23-15L69,82.89a8,8,0,0,0,"
        "5.1-2.64,74.11,74.11,0,0,1,6.14-6.14A8,8,0,0,0,82.89,69L85.4,46.43a91.32,91.32,"
        "0,0,1,15-6.23l17.74,14.19a8,8,0,0,0,5.48,1.74,73.93,73.93,0,0,1,8.68,0,8,8,0,"
        "0,0,5.48-1.74L155.55,40.2a91.57,91.57,0,0,1,15,6.23L173.11,69a8,8,0,0,0,2.64,"
        "5.1,74.11,74.11,0,0,1,6.14,6.14,8,8,0,0,0,5.1,2.64l22.58,2.51a91.32,91.32,"
        "0,0,1,6.23,15l-14.19,17.74A8,8,0,0,0,199.87,123.66Z"
    ),
}

# Human-readable labels shown in tooltips
_LABELS: dict[StringType, str] = {
    StringType.DIALOGUE: "Dialogue",
    StringType.QUEST:    "Quest",
    StringType.BOOK:     "Book",
    StringType.NOTE:     "Note",
    StringType.TERMINAL: "Terminal",
    StringType.UI:       "Interface",
    StringType.SYSTEM:   "System",
    StringType.UNKNOWN:  "",
}

# Icon tint colours — (dark_theme, light_theme)
_COLORS: dict[StringType, tuple[str, str]] = {
    StringType.DIALOGUE: ("#60a5fa", "#2563eb"),   # blue
    StringType.QUEST:    ("#fbbf24", "#d97706"),   # amber
    StringType.BOOK:     ("#c084fc", "#7c3aed"),   # violet
    StringType.NOTE:     ("#94a3b8", "#64748b"),   # slate
    StringType.TERMINAL: ("#4ade80", "#16a34a"),   # green
    StringType.UI:       ("#fb923c", "#ea580c"),   # orange
    StringType.SYSTEM:   ("#94a3b8", "#64748b"),   # slate
    StringType.UNKNOWN:  ("#64748b", "#94a3b8"),
}


# ── Classifier ────────────────────────────────────────────────────────────────

# Imperative verbs that usually open quest objectives
_QUEST_RE = re.compile(
    r"^(Find|Retrieve|Investigate|Explore|Defeat|Survive|Collect|Reach|Locate|"
    r"Search|Kill|Destroy|Protect|Escort|Steal|Hack|Repair|Build|Craft|Complete|"
    r"Talk to|Speak with|Report|Return|Deliver|Activate|Disable|Access|Follow|"
    r"Clear|Scan|Use|Dock|Board|Land|Eliminate|Rescue|Sabotage|Acquire)\b",
    re.IGNORECASE,
)
_CONTRACTIONS_RE = re.compile(
    r"\b(I've|I'm|I'll|I'd|you've|you're|you'll|don't|can't|won't|isn't|wasn't|"
    r"weren't|haven't|hadn't|wouldn't|couldn't|shouldn't|that's|it's|what's|"
    r"there's|here's|let's)\b",
    re.IGNORECASE,
)
_FORMAT_RE = re.compile(
    r"<Alias=|<GlobalValue=|\[PLYR\]|%[1-9]?\$?[sd]|\{[0-9]+\}|<font\b|<color\b",
    re.IGNORECASE,
)
_MARKUP_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def classify(text: str, file_ext: str = "") -> StringType:
    """Classify a localization string into a content type.

    Args:
        text:     The source (original) string.
        file_ext: Lowercase file extension without dot
                  (``"strings"``, ``"dlstrings"``, ``"ilstrings"``).
    """
    if not text:
        return StringType.UI

    stripped = text.strip()
    length = len(stripped)

    # 1. System — game markup / format specifiers
    if _FORMAT_RE.search(text):
        return StringType.SYSTEM

    # 2. Terminal — HTML line-breaks / in-game computer screens
    if _MARKUP_RE.search(text):
        return StringType.TERMINAL

    # 3. Book — long, multi-paragraph narrative
    if length > 350 and ("\n\n" in text or text.count("\n") >= 3):
        return StringType.BOOK

    # 4. Note — medium multi-line text (letter, journal)
    if 100 < length <= 900 and "\n" in text:
        return StringType.NOTE

    # 5. UI — very short label with no sentence-ending punctuation
    if length <= 40 and not re.search(r"[.?!]", stripped):
        return StringType.UI
    if length <= 20:
        return StringType.UI

    # 6. Quest — starts with an imperative action verb
    if _QUEST_RE.match(stripped):
        return StringType.QUEST

    # 7. Book — plain long text without newlines
    if length > 600:
        return StringType.BOOK

    # 8. File-extension hint for borderline cases
    if file_ext == "dlstrings":
        return StringType.DIALOGUE
    if file_ext == "ilstrings":
        return StringType.UI

    # 9. Dialogue — contractions / speech / interrogative
    if _CONTRACTIONS_RE.search(stripped):
        return StringType.DIALOGUE
    if stripped.endswith(("?", "!", '?"', '!"')) and length < 300:
        return StringType.DIALOGUE

    return StringType.UNKNOWN


# ── Icon factory ──────────────────────────────────────────────────────────────

_icon_cache: dict[tuple[StringType, bool], object] = {}  # QIcon values, typed as object to avoid import cycle


def get_type_icon(string_type: StringType, is_dark: bool, size: int = 16) -> Optional[object]:
    """Return a QIcon for *string_type* tinted for the current palette.

    Returns ``None`` for UNKNOWN.  Icons are cached after the first render.
    """
    if string_type == StringType.UNKNOWN:
        return None

    key = (string_type, is_dark)
    if key in _icon_cache:
        return _icon_cache[key]

    path_d = _PATHS.get(string_type)
    if not path_d:
        return None

    color = _COLORS[string_type][0 if is_dark else 1]

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon, QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer

        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">'
            f'<path fill="{color}" d="{path_d}"/>'
            "</svg>"
        ).encode()

        renderer = QSvgRenderer(svg)
        px = QPixmap(size, size)
        px.fill(Qt.transparent)
        painter = QPainter(px)
        renderer.render(painter)
        painter.end()
        icon = QIcon(px)
    except Exception:
        return None

    _icon_cache[key] = icon
    return icon


def clear_icon_cache() -> None:
    """Invalidate all cached icons (call after a theme change)."""
    _icon_cache.clear()


def label_for_type(string_type: StringType) -> str:
    """Human-readable tooltip string for a type."""
    return _LABELS.get(string_type, "")
