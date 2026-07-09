"""
Guard against the group-box title being struck through by its own frame.

Every built-in theme positions the title with ``subcontrol-origin: margin``,
which draws it in the band *above* the border, anchored at the widget's top
edge. The band is exactly ``margin-top`` tall, so if ``margin-top`` is smaller
than the title's text height the border is painted straight through the words.

Qt resolves the QSS ``em`` unit to ``QFontMetrics::height()`` — the very height
the title occupies — so ``margin-top: 1.0em`` is the exact break-even point and
anything expressed in ``px``/``pt`` breaks as soon as the user's font grows.
Hence the rule: em units, with headroom.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from gui.theme_manager import THEMES

# `QGroupBox {` — the ``::title`` rule has a `::` before the brace, so it can't match here.
_GROUPBOX_RE = re.compile(r"QGroupBox\s*\{([^}]*)\}")
_TITLE_RE = re.compile(r"QGroupBox::title\s*\{([^}]*)\}")
_MARGIN_TOP_RE = re.compile(r"margin-top:\s*([0-9.]+)\s*(px|pt|em|ex)")

# 1.0em == the title height exactly; keep a margin of safety for descenders/DPI rounding.
_MIN_EM = 1.2


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_theme_styles_groupbox_and_title(theme):
    qss = THEMES[theme]
    assert _GROUPBOX_RE.search(qss), f"{theme}: no QGroupBox rule"
    assert _TITLE_RE.search(qss), f"{theme}: no QGroupBox::title rule"


@pytest.mark.parametrize("theme", sorted(THEMES))
def test_title_in_margin_band_has_room_for_its_text(theme):
    qss = THEMES[theme]
    title = _TITLE_RE.search(qss)
    assert title, f"{theme}: no QGroupBox::title rule"
    if "subcontrol-origin: margin" not in title.group(1):
        pytest.skip(f"{theme}: title is not drawn in the margin band")

    box = _GROUPBOX_RE.search(qss)
    assert box, f"{theme}: no QGroupBox rule"
    m = _MARGIN_TOP_RE.search(box.group(1))
    assert m, f"{theme}: QGroupBox has no margin-top — the border will cross the title"

    value, unit = float(m.group(1)), m.group(2)
    assert unit == "em", (
        f"{theme}: margin-top is {value}{unit}; use em so the band tracks the font size "
        f"(a fixed {unit} value is crossed by the border once the font grows)"
    )
    assert value >= _MIN_EM, (
        f"{theme}: margin-top {value}em leaves no headroom above the title "
        f"(need >= {_MIN_EM}em; 1.0em is exactly the text height)"
    )
