"""
UI width-fit simulator.

``font_checker`` answers "can the game *draw* this character?".  This module
answers the next question: "does the drawn string *fit the box*?".  A glyph that
exists in the atlas is still a bug if the translated label runs past the edge of
its button — Cyrillic and German expand 15–30 % over English, and Bethesda's
Scaleform widgets clip rather than shrink.

How the width is computed
─────────────────────────
Exactly, from the font's own horizontal metrics.  ``FontSource.advances`` gives
each codepoint's advance as a fraction of the EM square (parsed from a SWF
FontAdvanceTable or a TTF ``hmtx`` table), so::

    pixel_width = sum(advance_em(ch) for ch in text) * font_pixel_size

This is the same summation the renderer performs, minus kerning pairs (Scaleform
applies kerning only when the font tag ships a kerning table; ignoring it is a
sub-1 % effect and errs toward *under*-reporting overflow, never inventing it).

How the budget is decided — and what is actually known
─────────────────────────────────────────────────────
The measurement above is exact.  The *budget* it is compared against is a model,
and the two must not be confused:

  • ``Confidence.MEASURED``  — the box width was read out of the game's own SWF
    (the dialogue subtitle panel: 597×147 px on a 1920×1080 stage, verified
    pixel-by-pixel — see ``gui/visual_context_preview``).
  • ``Confidence.ESTIMATED`` — a plausible default for a widget class we have no
    SWF bounds for.  These are starting points, not ground truth, and every one
    of them is user-editable via ``WidgetSpec.with_budget()``.

So a flagged row means "wider than the budget you are testing against", not
"guaranteed broken in-game".  To keep that distinction usable, every ``FitResult``
also carries ``source_ratio`` — the translated width as a multiple of the English
source width.  The English *did* fit by construction (Bethesda laid the widget
out around it), which makes a large ratio strong evidence of overflow regardless
of how good the absolute budget is.

Pure Python — no Qt, no game files required (the bundled ``data/fonts/*.ttf`` are
the real Starfield faces, so the default metrics are genuine).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .font_checker import FontSource, parse_swf_glyphs, parse_ttf_glyphs

# ── Font roles ────────────────────────────────────────────────────────────────
# Starfield renders Latin locales in NB Architekt and Cyrillic locales in the
# RF_* faces (NB Architekt has no Cyrillic coverage at all).  Weight matters for
# width: RF_55_SB is ~19 % wider per glyph than RF_35_M, so a bold button label
# measured with the regular face reads as fitting when it does not.

_FONTS_DIR = Path(__file__).parent.parent / "data" / "fonts"

ROLE_BODY = "body"          # regular body text
ROLE_BOLD = "bold"          # bold / semibold — buttons, headers
ROLE_LATIN = "latin"        # English-only display face

_ROLE_FILES: Dict[str, Tuple[str, ...]] = {
    # Ordered by preference; the first file that parses wins.
    ROLE_BODY:  ("RF_35_M.ttf", "NB_Architekt_Light.ttf"),
    ROLE_BOLD:  ("RF_55_SB.ttf", "RF_55_M.ttf", "NB_Architekt.ttf"),
    ROLE_LATIN: ("NB_Architekt_Light.ttf", "RF_35_M.ttf"),
}

# Fallback advance for a codepoint the font has no metric for.  Deliberately
# mid-range rather than 0 — a missing metric must never make a string look
# *narrower* than it is, which would hide an overflow.
_FALLBACK_ADVANCE = 0.55


# ── Placeholder handling ──────────────────────────────────────────────────────
# Two very different kinds of markup live in these strings:
#
#   * formatting tags  (<font>, </font>, <br>)      — render as nothing; 0 px.
#   * value placeholders (<Alias=X>, %s, <mag>, …)  — render as *runtime text* of
#     unknown length.  Measuring the literal "<Alias=Player>" is wrong (it is not
#     what the player sees) and deleting it is also wrong (it under-measures and
#     hides overflow).  Each is therefore replaced by a representative sample of
#     what the engine actually substitutes.

_FORMAT_TAG_RE = re.compile(
    r"</?font[^>]*>|</?color[^>]*>|<br\s*/?>|</?[bi]>",
    re.IGNORECASE,
)

# Representative substitutions, chosen to be typical rather than worst-case.
_ALIAS_SAMPLE = "Vasco"      # a mid-length NPC/alias name
_NUMBER_SAMPLE = "25"        # <mag>, <dur>, numeric format specifiers
_TEXT_SAMPLE = "Item"        # generic %s-style string substitution

_VALUE_PATTERNS: Sequence[Tuple[re.Pattern, str]] = (
    (re.compile(r"<Alias=[^>]*>", re.IGNORECASE), _ALIAS_SAMPLE),
    (re.compile(r"\[PLYR\]", re.IGNORECASE), _ALIAS_SAMPLE),
    (re.compile(r"<GlobalValue=[^>]*>", re.IGNORECASE), _NUMBER_SAMPLE),
    (re.compile(r"<(?:mag|dur|area|repetitions)>", re.IGNORECASE), _NUMBER_SAMPLE),
    (re.compile(r"<(?:basename|relat)>", re.IGNORECASE), _TEXT_SAMPLE),
    (re.compile(r"%[1-9]?\$?d"), _NUMBER_SAMPLE),
    (re.compile(r"%[1-9]?\$?s"), _TEXT_SAMPLE),
    (re.compile(r"\{[0-9]+\}"), _TEXT_SAMPLE),
)

_ANY_VALUE_RE = re.compile(
    r"<Alias=[^>]*>|\[PLYR\]|<GlobalValue=[^>]*>"
    r"|<(?:mag|dur|area|repetitions|basename|relat)>"
    r"|%[1-9]?\$?[sd]|\{[0-9]+\}",
    re.IGNORECASE,
)


def render_text(text: str) -> Tuple[str, bool]:
    """Return (what the player actually sees, contains_runtime_value).

    Formatting tags are dropped; value placeholders become a representative
    sample.  The bool marks the result as an *estimate* — the true width depends
    on runtime data (a player-chosen name has no upper bound).
    """
    has_value = bool(_ANY_VALUE_RE.search(text))
    out = _FORMAT_TAG_RE.sub("", text)
    for pattern, sample in _VALUE_PATTERNS:
        out = pattern.sub(sample, out)
    return out, has_value


# ── Font metrics ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FontMetrics:
    """Horizontal metrics for one font face, keyed by codepoint."""

    name: str
    advances: Mapping[int, float]          # codepoint → advance in EM fractions
    role: str = ROLE_BODY

    def advance(self, ch: str) -> float:
        return self.advances.get(ord(ch), _FALLBACK_ADVANCE)

    def measure(self, text: str, font_px: float) -> float:
        """Rendered width of *text* in pixels at *font_px* pixel size."""
        if not text:
            return 0.0
        em = sum(self.advances.get(ord(ch), _FALLBACK_ADVANCE) for ch in text)
        return em * font_px

    def coverage(self, text: str) -> float:
        """Fraction of *text*'s characters that have a real (non-fallback) metric.

        Below 1.0 the measurement leans on ``_FALLBACK_ADVANCE`` and should be
        surfaced as approximate — typically a Cyrillic string measured against a
        Latin-only face.
        """
        if not text:
            return 1.0
        known = sum(1 for ch in text if ord(ch) in self.advances)
        return known / len(text)


def load_bundled_metrics() -> Dict[str, FontMetrics]:
    """Load metrics for every font role from the bundled Starfield TTFs.

    Returns ``{role: FontMetrics}``, omitting roles whose files are missing or
    unparseable.  Empty when ``data/fonts/`` is absent entirely.
    """
    out: Dict[str, FontMetrics] = {}
    for role, filenames in _ROLE_FILES.items():
        for filename in filenames:
            path = _FONTS_DIR / filename
            if not path.is_file():
                continue
            try:
                sources = parse_ttf_glyphs(path)
            except (OSError, struct.error):  # pragma: no cover - defensive
                continue
            src = next((s for s in sources if s.advances), None)
            if src is None:
                continue
            out[role] = FontMetrics(name=src.name, advances=src.advances, role=role)
            break
    return out


# ── fontconfig family → bundled face ──────────────────────────────────────────
# A text field names its font by *class* ($MAIN_Font_Bold); fontconfig.txt maps
# that class to a *family* (RF_55_M); and the family is the stem of a TTF we
# already ship.  That chain resolves a field's face exactly, so nothing has to be
# guessed from "is this widget probably bold?".

_FAMILY_FILES: Dict[str, str] = {
    "RF_35_M": "RF_35_M.ttf",
    "RF_55_M": "RF_55_M.ttf",
    "RF_55_SB": "RF_55_SB.ttf",
    "NB Architekt": "NB_Architekt.ttf",
    "NB Architekt Light": "NB_Architekt_Light.ttf",
}

# Which of our three roles a family stands in for, when a role is all the caller
# has (e.g. the built-in widget presets).
_FAMILY_ROLES: Dict[str, str] = {
    "RF_35_M": ROLE_BODY,
    "RF_55_M": ROLE_BOLD,
    "RF_55_SB": ROLE_BOLD,
    "NB Architekt": ROLE_BOLD,
    "NB Architekt Light": ROLE_LATIN,
}


def role_for_family(family: str) -> str:
    return _FAMILY_ROLES.get(family, ROLE_BODY)


def load_metrics_for_family(family: str) -> Optional[FontMetrics]:
    """Metrics for a fontconfig font family, or None if we do not ship that face.

    Starfield also maps icon fonts (`Genesis Controller Buttons`) and faces we do
    not bundle (`Starfield_Grotesk_R`); those return None so the caller can say
    "cannot measure this widget" rather than silently measuring with the wrong
    font — a controller-glyph field is not text and must never be width-checked.
    """
    filename = _FAMILY_FILES.get(family)
    if not filename:
        return None
    path = _FONTS_DIR / filename
    if not path.is_file():
        return None
    try:
        sources = parse_ttf_glyphs(path)
    except (OSError, struct.error):  # pragma: no cover - defensive
        return None
    src = next((s for s in sources if s.advances), None)
    if src is None:
        return None
    return FontMetrics(
        name=src.name, advances=src.advances, role=role_for_family(family)
    )


def metrics_from_sources(
    sources: Iterable[FontSource],
    role: str = ROLE_BODY,
    name: str = "",
) -> Optional[FontMetrics]:
    """Merge user-supplied ``FontSource``s (a game SWF/TTF) into one FontMetrics.

    Earlier sources win on conflict.  Returns None when none of them carried
    advance widths — a SWF font tag without the ``HasLayout`` flag has glyphs but
    no metrics, and guessing widths for it would defeat the point of the tool.
    """
    merged: Dict[int, float] = {}
    picked: List[str] = []
    for src in sources:
        if not src.advances:
            continue
        picked.append(src.name)
        for cp, adv in src.advances.items():
            merged.setdefault(cp, adv)
    if not merged:
        return None
    return FontMetrics(name=name or ", ".join(picked), advances=merged, role=role)


def load_font_file(path: Path) -> List[FontSource]:
    """Parse a .swf/.ttf/.otf into FontSources (dispatching on extension)."""
    if path.suffix.lower() == ".swf":
        return parse_swf_glyphs(path)
    return parse_ttf_glyphs(path)


# ── Widget specs ──────────────────────────────────────────────────────────────

class Confidence(Enum):
    """How much of a widget's geometry is the game's own.

    Ordered from most to least trustworthy.  The distinction is kept on the spec
    rather than flattened away because width scales *linearly* with font size —
    a 20 % error in a derived size is a 20 % error in the verdict.
    """
    MEASURED = "measured"     # bounds AND font size read from the game's SWF
    DERIVED = "derived"       # bounds from the SWF; font size inferred from height
    ESTIMATED = "estimated"   # neither — a plausible default, user should verify


@dataclass(frozen=True)
class WidgetSpec:
    """A length-critical UI widget and the space its text has to fit in.

    Budgets are in pixels on Starfield's 1920×1080 reference stage, which is the
    coordinate space Scaleform lays out in; they scale with resolution, so the
    fit verdict is resolution-independent.
    """

    key: str
    label: str
    budget_px: float           # usable text width, i.e. box width minus padding
    font_px: float             # rendered font size in stage pixels
    role: str = ROLE_BODY
    uppercase: bool = False    # widget applies a CAPS transform before drawing
    confidence: Confidence = Confidence.ESTIMATED
    note: str = ""
    family: str = ""           # fontconfig-resolved face, when known ("RF_55_M")

    def with_budget(self, budget_px: float) -> "WidgetSpec":
        """Return a copy with a corrected budget (the dialog's budget editor).

        Overriding a budget makes it *the user's* measurement, so the confidence
        is no longer ours to claim — it stays whatever the caller sets.
        """
        return replace(self, budget_px=float(budget_px))

    @classmethod
    def from_text_field(cls, field, family: str = "") -> "WidgetSpec":
        """Build a spec from a real ``swf_widgets.TextField`` read out of the game.

        This is the whole point of the SWF scan: the budget stops being a guess.
        ``budget_px`` becomes the field's authored width minus its margins, and
        ``font_px`` the size the field actually renders at — exactly when the SWF
        declares one (`MEASURED`), and inferred from the box height when it does
        not (`DERIVED`, see ``swf_widgets._HEIGHT_TO_FONT_PX``).

        *family* is the fontconfig-resolved font family (e.g. ``RF_55_M``); it
        picks the metric face, so no weight guessing is needed either.
        """
        from .swf_widgets import FontSizeSource

        exact = field.font_size_source is FontSizeSource.DECLARED
        note = field.font_class or ""
        if family:
            note = f"{note} → {family}" if note else family
        if not exact:
            note += "  (font size inferred from box height)"

        return cls(
            key=f"swf:{field.swf}:{field.char_id}",
            label=field.label,
            budget_px=field.usable_width_px,
            font_px=field.font_px,
            role=role_for_family(family),
            uppercase=False,   # the CAPS transform lives in ActionScript, not the SWF
            confidence=Confidence.MEASURED if exact else Confidence.DERIVED,
            note=note.strip(),
            family=family,
        )


# Length-critical widgets: single-line, fixed-width, clip on overflow.  Wrapping
# widgets (dialogue subtitles, books, notes) are deliberately absent — they grow
# downward instead of clipping, so *width* is not their failure mode and flagging
# them would be noise.
_WIDGET_LIST: Sequence[WidgetSpec] = (
    WidgetSpec(
        key="button", label="Button", budget_px=300, font_px=24, role=ROLE_BOLD,
        uppercase=True, note="Menu/dialog action buttons — drawn in caps.",
    ),
    WidgetSpec(
        key="menu_item", label="Menu label", budget_px=380, font_px=26, role=ROLE_BODY,
        note="Main/pause menu entries.",
    ),
    WidgetSpec(
        key="tab", label="Tab label", budget_px=220, font_px=22, role=ROLE_BOLD,
        uppercase=True, note="Inventory/ship tab headers — the tightest widget.",
    ),
    WidgetSpec(
        key="item_name", label="Item name (list cell)", budget_px=420, font_px=22,
        role=ROLE_BODY, note="Inventory rows; long names truncate with an ellipsis.",
    ),
    # These two are the real thing: bounds *and* font size read straight out of
    # the shipped SWFs (see swf_widgets), which is why they are MEASURED while
    # their neighbours are not.  Both were originally guessed far too generously
    # here — the HUD objective is 335 px, not the 480 px that seemed reasonable.
    WidgetSpec(
        key="hud_objective", label="HUD objective", budget_px=335, font_px=18,
        role=ROLE_BOLD, confidence=Confidence.MEASURED,
        note="hudmessagesmenu › QuestObjectiveText_tf ($MAIN_Font_Bold → RF_55_M).",
    ),
    WidgetSpec(
        key="notification", label="HUD notification", budget_px=317, font_px=19,
        role=ROLE_BOLD, confidence=Confidence.MEASURED,
        note="hudmenu › PromptMessageWidget.textField ($MAIN_Font_Bold → RF_55_M).",
    ),
    WidgetSpec(
        key="tooltip_title", label="Tooltip title", budget_px=340, font_px=24,
        role=ROLE_BOLD, note="Header line of an item tooltip.",
    ),
)

WIDGETS: Dict[str, WidgetSpec] = {w.key: w for w in _WIDGET_LIST}

DEFAULT_WIDGET = "menu_item"


def widget_keys() -> List[str]:
    return [w.key for w in _WIDGET_LIST]


# ── Length-critical classification ────────────────────────────────────────────
# Which strings are worth width-checking at all.  Prose wraps; labels clip.  This
# mirrors the "UI" arm of gui.string_type_detector.classify() but lives here so
# the pure library never has to import the GUI layer.

_SENTENCE_RE = re.compile(r"[.?!]\s|[.?!]$")
_MAX_LABEL_CHARS = 48

# Widths are compared against the *rendered* text, so tags are already resolved.


def is_length_critical(text: str) -> bool:
    """True when *text* looks like a fixed-width label rather than prose.

    A label is short, single-line and has no sentence punctuation.  Everything
    else is prose destined for a wrapping widget, where width is not the failure
    mode.
    """
    if not text:
        return False
    rendered, _ = render_text(text)
    stripped = rendered.strip()
    if not stripped or "\n" in stripped:
        return False
    if len(stripped) > _MAX_LABEL_CHARS:
        return False
    return not _SENTENCE_RE.search(stripped)


def _is_all_caps(text: str) -> bool:
    """True when *text* has letters and none of them are lowercase.

    ``islower()`` is used rather than a character class so this stays correct for
    every script the game ships (Cyrillic ё/ъ, Greek, accented Latin).
    """
    return any(c.isalpha() for c in text) and not any(c.islower() for c in text)


def infer_widget_key(text: str) -> Optional[str]:
    """Best-guess widget class for *text*, or None when it is not length-critical.

    A guess, and only a guess: a ``.strings`` entry carries no link to the SWF
    text field that will draw it, so nothing in the file says "this is a button".
    The dialog lets the user re-assign any row, and this only decides the default.
    """
    if not is_length_critical(text):
        return None
    rendered, _ = render_text(text)
    stripped = rendered.strip()

    # ALL-CAPS short labels are almost always buttons/tabs in Bethesda UIs.
    if len(stripped) <= 20 and _is_all_caps(stripped):
        return "button"
    if len(stripped) <= 14:
        return "tab"
    return DEFAULT_WIDGET


# ── Fit checking ──────────────────────────────────────────────────────────────

@dataclass
class FitResult:
    """The width verdict for one string in one widget."""

    row_index: int
    string_id: int
    widget_key: str
    source: str
    translated: str
    rendered: str              # what is actually measured (tags resolved)
    width_px: float
    budget_px: float
    source_width_px: float     # English source width in the same widget
    fits: bool
    coverage: float            # 1.0 = every glyph had a real metric
    has_runtime_value: bool    # contains <Alias=…>/%s — width is an estimate

    @property
    def overflow_px(self) -> float:
        return max(0.0, self.width_px - self.budget_px)

    @property
    def fill_ratio(self) -> float:
        """Width as a fraction of the budget.  >1.0 overflows."""
        if self.budget_px <= 0:
            return 0.0
        return self.width_px / self.budget_px

    @property
    def source_ratio(self) -> float:
        """Translated width ÷ English source width.  The layout-independent signal.

        The English fit by construction, so >1.0 means the translation is asking
        for more room than the widget was ever designed to give.
        """
        if self.source_width_px <= 0:
            return 0.0
        return self.width_px / self.source_width_px

    @property
    def is_approximate(self) -> bool:
        return self.has_runtime_value or self.coverage < 1.0


def check_fit(
    translated: str,
    widget: WidgetSpec,
    metrics: FontMetrics,
    *,
    source: str = "",
    row_index: int = -1,
    string_id: int = 0,
) -> FitResult:
    """Measure *translated* against *widget*'s budget using real font metrics."""
    rendered, has_value = render_text(translated)
    text = rendered.upper() if widget.uppercase else rendered

    width = metrics.measure(text, widget.font_px)

    src_rendered, _ = render_text(source)
    src_text = src_rendered.upper() if widget.uppercase else src_rendered
    src_width = metrics.measure(src_text, widget.font_px)

    return FitResult(
        row_index=row_index,
        string_id=string_id,
        widget_key=widget.key,
        source=source,
        translated=translated,
        rendered=rendered,
        width_px=width,
        budget_px=widget.budget_px,
        source_width_px=src_width,
        fits=width <= widget.budget_px,
        coverage=metrics.coverage(text),
        has_runtime_value=has_value,
    )


@dataclass
class WidthCheckResult:
    results: List[FitResult]           # overflowing rows only, worst first
    measured: List[FitResult]          # every length-critical row, including fits
    checked: int                       # length-critical rows measured
    skipped_prose: int                 # rows that wrap, so width is not their bug
    untranslated: int
    font_name: str

    @property
    def overflow_count(self) -> int:
        return len(self.results)

    def tight_fits(self, threshold: float = 0.9) -> List[FitResult]:
        """Rows that fit but sit within ``1 - threshold`` of the edge, worst first.

        These are the ones that break first if a budget estimate is slightly off,
        so they are worth showing even though they technically pass.
        """
        tight = [r for r in self.measured if r.fits and r.fill_ratio >= threshold]
        tight.sort(key=lambda r: r.fill_ratio, reverse=True)
        return tight


def scan_rows(
    rows: Sequence[Mapping],
    metrics_by_role: Mapping[str, FontMetrics],
    *,
    budgets: Optional[Mapping[str, WidgetSpec]] = None,
    widget_override: Optional[str] = None,
) -> WidthCheckResult:
    """Width-check every length-critical translated string in *rows*.

    Rows are the main table's dicts (``original``/``translated``/``id``).  Only
    overflowing rows come back — sorted worst-overflow first, so the most broken
    label is the first thing the translator sees.

    *widget_override* forces every row into one widget class (the "test all my
    labels as buttons" mode); otherwise each row's class is inferred.
    """
    specs = dict(WIDGETS)
    if budgets:
        specs.update(budgets)

    fallback_metrics = (
        metrics_by_role.get(ROLE_BODY)
        or next(iter(metrics_by_role.values()), None)
    )
    if fallback_metrics is None:
        return WidthCheckResult([], [], 0, 0, 0, "")

    measured: List[FitResult] = []
    checked = skipped = untranslated = 0

    for row_index, row in enumerate(rows):
        translated = (row.get("translated") or "").strip()
        source = row.get("original") or ""
        if not translated:
            untranslated += 1
            continue

        # The override picks the widget *class*; it never drags prose into the
        # width check.  A wrapping paragraph cannot "overflow a button" — it was
        # never going in one — and reporting that would be pure noise.
        probe = source or translated
        if not is_length_critical(probe):
            skipped += 1
            continue

        key = widget_override or infer_widget_key(probe)
        spec = specs.get(key) if key else None
        if spec is None:
            skipped += 1
            continue

        metrics = metrics_by_role.get(spec.role) or fallback_metrics
        checked += 1

        measured.append(check_fit(
            translated,
            spec,
            metrics,
            source=source,
            row_index=row_index,
            string_id=int(row.get("id", 0) or 0),
        ))

    overflowing = sorted(
        (r for r in measured if not r.fits),
        key=lambda r: r.overflow_px,
        reverse=True,
    )
    return WidthCheckResult(
        results=overflowing,
        measured=measured,
        checked=checked,
        skipped_prose=skipped,
        untranslated=untranslated,
        font_name=fallback_metrics.name,
    )
