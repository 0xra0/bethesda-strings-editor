"""
Real UI widget bounds, read out of the game's own Scaleform SWFs.

``width_fit`` can measure a translated label's rendered width exactly, but until
now it compared that width against a *guessed* budget — nothing in a ``.strings``
file says how wide a button is.  The game does say, in its `DefineEditText` tags:
every text field in every menu carries its authored bounds, its margins, its font
class and its wrap/clip behaviour.  This module reads them.

What is ground truth here, and what is not
──────────────────────────────────────────
  • **Bounds and margins** — exact.  Straight out of the tag, in twips.
  • **Clip behaviour** — exact, and it is what makes a field *length-critical*:
    a field with no Multiline, no WordWrap and no AutoSize physically cannot
    reflow, so text that exceeds its width is clipped.  No heuristic needed.
  • **Font face** — exact *given a fontconfig*.  Fields name their font by class
    (`$MAIN_Font_Bold`), and ``fontconfig.txt`` maps that class to a family
    (`RF_55_M`) — which is one of the faces bundled in ``data/fonts/``.
  • **Font size** — usually *not* stated.  Starfield sets it from ActionScript at
    runtime; across the shipped Interface SWFs only ~4 % of clipping fields
    declare a size (in their HTML initial text, `<font size="18">`).  Where it is
    declared we use it (``FontSizeSource.DECLARED``).  Where it is not, it is
    derived from the field's height (``DERIVED``) and must be treated as such —
    see ``_HEIGHT_TO_FONT_PX``.

A field is therefore only fully authoritative when its size is DECLARED.  The
distinction is carried on every record rather than being flattened away, because
width scales linearly with font size: a 20 % size error is a 20 % width error.

Which text field draws which string is *not* knowable from here — a ``.strings``
entry carries no link to a SWF field.  This module supplies the catalogue; the
caller (or the user) picks which widget a string is being tested against.

Pure Python: no Qt.
"""

from __future__ import annotations

import logging
import re
import struct
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .font_checker import parse_fontconfig
from .swf import (
    TAG_DEFINE_EDIT_TEXT,
    TAG_DEFINE_SPRITE,
    TAG_PLACE_OBJECT2,
    TAG_PLACE_OBJECT3,
    TAG_SYMBOL_CLASS,
    TWIPS,
    decompress_swf,
    iter_tags,
    read_cstring,
    read_rect,
    skip_matrix,
)

logger = logging.getLogger(__name__)


# Single-line field height ÷ font size, calibrated on the 24 shipped Starfield
# fields that declare both (median 1.22, range 1.15–1.44).  Used only to *derive*
# a size when the SWF does not state one; the spread is why such fields are
# flagged DERIVED rather than folded in with the exact ones.
_HEIGHT_TO_FONT_PX = 1.22

_HTML_SIZE_RE = re.compile(r'size="(\d+)"')
_HTML_FACE_RE = re.compile(r'face="([^"]+)"')

# Starfield ships a large-font accessibility variant of most menus alongside the
# standard one (missionmenu.swf / missionmenu_lrg.swf).  Where the font grows the
# box usually does *not* — median ×1.30 and up to ×2.64 — so that build is where
# a label that only just fits the standard menu will clip.
#
# It is not, however, universally tighter, and nothing here may assume it is.  Of
# the 589 widgets the shipped UI defines in both builds, 251 grow the font, 336
# use an identical one, and 2 actually *shrink* it (missionboard › text_tf goes
# 49px → 26px).  So the worst case is decided per widget, on capacity, and
# worst_case() reports which build it picked and why — see VariantOutcome.
_LARGE_FONT_SUFFIX = "_lrg"


class FontSizeSource(Enum):
    """Where a field's font size came from — see the module docstring."""
    DECLARED = "declared"   # stated in the SWF (HTML `<font size=…>` or FontHeight)
    DERIVED = "derived"     # inferred from the field's height; approximate


class VariantOutcome(Enum):
    """What the large-font check actually found for one widget.

    Four outcomes, and they are *not* interchangeable — three of them leave the
    chosen widget unchanged, and a caller that reports them all as silence tells
    the user nothing about whether the accessibility build was checked at all:

    ``TIGHTER``     the other build has less room, so it is the one measured.
                    Usually the large-font one — but not always, so callers must
                    name the build rather than assume it.
    ``NO_TIGHTER``  the twin exists and gives no less room; this already is the
                    worst case.
    ``AMBIGUOUS``   a twin exists but cannot be identified, so it was NOT checked
                    (see ``WidgetCatalogue.variants``).
    ``NO_VARIANT``  this menu ships no other build of the widget.
    """
    TIGHTER = "tighter"
    NO_TIGHTER = "no_tighter"
    AMBIGUOUS = "ambiguous"
    NO_VARIANT = "no_variant"


@dataclass(frozen=True)
class TextField:
    """One `DefineEditText` field: a real place the game draws a string."""

    swf: str                    # source file stem, e.g. "buttonclips"
    name: str                   # "BasicButton_Label.Label_tf", or "" if unnamed
    char_id: int

    width_px: float             # authored bounds
    height_px: float
    left_margin_px: float
    right_margin_px: float

    font_class: str             # "$MAIN_Font_Bold" — resolve via fontconfig
    font_px: float
    font_size_source: FontSizeSource

    multiline: bool
    word_wrap: bool
    auto_size: bool

    @property
    def usable_width_px(self) -> float:
        """Width actually available to text, once margins are taken out."""
        return max(0.0, self.width_px - self.left_margin_px - self.right_margin_px)

    @property
    def is_measurable(self) -> bool:
        """False for zero-width boxes, which the game sizes at runtime.

        A 0 px field is not a tiny widget — it is a widget whose bounds
        ActionScript supplies later.  Measuring against it would flag *every*
        string as overflowing, so it is excluded rather than reported.
        """
        return self.usable_width_px > 0 and self.font_px > 0

    @property
    def is_large_font(self) -> bool:
        """True for Starfield's ``*_lrg`` large-font accessibility menu variants."""
        return self.swf.endswith(_LARGE_FONT_SUFFIX)

    @property
    def base_swf(self) -> str:
        """The menu this field belongs to, with any ``_lrg`` suffix stripped."""
        if self.is_large_font:
            return self.swf[: -len(_LARGE_FONT_SUFFIX)]
        return self.swf

    @property
    def capacity_em(self) -> float:
        """How much text the box holds, in em — the font-size-independent measure.

        Comparing two variants of the same widget by raw pixel width is
        meaningless when their font sizes differ; capacity is what actually
        decides which one runs out of room first.  Lower = tighter.
        """
        if self.font_px <= 0:
            return 0.0
        return self.usable_width_px / self.font_px

    @property
    def clips(self) -> bool:
        """True when text cannot reflow, so overflow is *clipped* — length-critical.

        This is the property that makes a field worth width-checking at all, and
        it is read from the game rather than guessed.
        """
        return not (self.multiline or self.word_wrap or self.auto_size)

    @property
    def is_exact(self) -> bool:
        """True when both the bounds *and* the font size are the game's own."""
        return self.font_size_source is FontSizeSource.DECLARED

    @property
    def label(self) -> str:
        """Human-readable identity, e.g. ``buttonclips › BasicButton_Label.Label_tf``."""
        return f"{self.swf} › {self.name}" if self.name else f"{self.swf} › #{self.char_id}"


# ── DefineEditText ────────────────────────────────────────────────────────────
# Flag bits, MSB-first in the tag's 2-byte flags field (SWF spec order).

_F_HAS_TEXT = 0x8000
_F_WORD_WRAP = 0x4000
_F_MULTILINE = 0x2000
_F_HAS_TEXT_COLOR = 0x0400
_F_HAS_MAX_LENGTH = 0x0200
_F_HAS_FONT = 0x0100
_F_HAS_FONT_CLASS = 0x0080
_F_AUTO_SIZE = 0x0040
_F_HAS_LAYOUT = 0x0020


def parse_edit_text(body: bytes) -> Optional[dict]:
    """Decode a DefineEditText tag body, or None if it is malformed.

    The tag is a chain of optional fields whose presence is driven by the flags
    word, so every one has to be consumed in order — skipping a present field
    misaligns everything after it.
    """
    try:
        char_id = struct.unpack_from("<H", body, 0)[0]
        (xmin, xmax, ymin, ymax), pos = read_rect(body, 2)

        flags = struct.unpack_from(">H", body, pos)[0]
        pos += 2

        has_font = bool(flags & _F_HAS_FONT)
        font_class = ""
        font_height_twips: Optional[int] = None

        if has_font:
            pos += 2    # FontID — the glyph source, not needed for geometry
        if flags & _F_HAS_FONT_CLASS:
            font_class, pos = read_cstring(body, pos)
        if has_font:
            font_height_twips = struct.unpack_from("<H", body, pos)[0]
            pos += 2
        if flags & _F_HAS_TEXT_COLOR:
            pos += 4    # RGBA
        if flags & _F_HAS_MAX_LENGTH:
            pos += 2

        left_margin = right_margin = 0
        if flags & _F_HAS_LAYOUT:
            pos += 1    # Align
            left_margin = struct.unpack_from("<H", body, pos)[0]
            pos += 2
            right_margin = struct.unpack_from("<H", body, pos)[0]
            pos += 2
            pos += 4    # Indent SI16 + Leading SI16

        _variable_name, pos = read_cstring(body, pos)

        initial_text = ""
        if flags & _F_HAS_TEXT:
            initial_text, pos = read_cstring(body, pos)
    except (struct.error, IndexError, ValueError):
        return None

    # Starfield leaves HasFont clear and states the size in the HTML initial text
    # instead ("<p align=…><font size=\"18\" …>").  Prefer a real FontHeight when
    # one exists; fall back to the HTML attribute; otherwise leave it unknown.
    font_px: Optional[float] = None
    if font_height_twips:
        font_px = font_height_twips / TWIPS
    else:
        match = _HTML_SIZE_RE.search(initial_text)
        if match:
            font_px = float(match.group(1))

    html_face = _HTML_FACE_RE.search(initial_text)
    if not font_class and html_face:
        font_class = html_face.group(1)

    return {
        "char_id": char_id,
        "width_px": (xmax - xmin) / TWIPS,
        "height_px": (ymax - ymin) / TWIPS,
        "left_margin_px": left_margin / TWIPS,
        "right_margin_px": right_margin / TWIPS,
        "font_class": font_class,
        "font_px": font_px,
        "multiline": bool(flags & _F_MULTILINE),
        "word_wrap": bool(flags & _F_WORD_WRAP),
        "auto_size": bool(flags & _F_AUTO_SIZE),
    }


# ── Naming: SymbolClass + sprite placements ───────────────────────────────────
# A DefineEditText tag carries no name of its own.  Its identity comes from where
# it is *placed*: a PlaceObject2/3 inside a DefineSprite gives it an instance name
# ("Label_tf"), and SymbolClass names the sprite ("BasicButton_Label").  Joining
# the two is what turns character id 35 into something a human can recognise.


def _parse_symbol_class(body: bytes) -> Dict[int, str]:
    try:
        count = struct.unpack_from("<H", body, 0)[0]
    except struct.error:
        return {}
    out: Dict[int, str] = {}
    pos = 2
    for _ in range(count):
        try:
            char_id = struct.unpack_from("<H", body, pos)[0]
            pos += 2
            name, pos = read_cstring(body, pos)
        except (struct.error, IndexError):
            break
        out[char_id] = name
    return out


def _parse_place_object(body: bytes, tag_type: int) -> Optional[Tuple[int, str]]:
    """Return (placed_char_id, instance_name) for a named placement, else None."""
    try:
        flags = body[0]
        pos = 1
        if tag_type == TAG_PLACE_OBJECT3:
            pos += 1    # second flags byte
        pos += 2        # Depth

        has_char = bool(flags & 0x02)
        has_matrix = bool(flags & 0x04)
        has_cxform = bool(flags & 0x08)
        has_ratio = bool(flags & 0x10)
        has_name = bool(flags & 0x20)

        if not (has_char and has_name):
            return None
        char_id = struct.unpack_from("<H", body, pos)[0]
        pos += 2
        if has_matrix:
            pos = skip_matrix(body, pos)
        if has_cxform:
            # Variable-width colour transform; rather than risk a misaligned read
            # of the name that follows, decline to name this placement.
            return None
        if has_ratio:
            pos += 2
        name, _ = read_cstring(body, pos)
    except (struct.error, IndexError):
        return None
    return (char_id, name) if name else None


def _collect_names(data: bytes) -> Dict[int, str]:
    """Map each placed character id → "SpriteClass.instance_name"."""
    symbols: Dict[int, str] = {}
    placements: Dict[int, Tuple[int, str]] = {}

    for tag_type, body in iter_tags(data):
        if tag_type == TAG_SYMBOL_CLASS:
            symbols.update(_parse_symbol_class(body))
        elif tag_type == TAG_DEFINE_SPRITE:
            try:
                sprite_id = struct.unpack_from("<H", body, 0)[0]
            except struct.error:
                continue
            # A sprite body is itself a tag stream, starting after
            # SpriteID UI16 + FrameCount UI16.
            for sub_type, sub_body in iter_tags(body, start=4):
                if sub_type not in (TAG_PLACE_OBJECT2, TAG_PLACE_OBJECT3):
                    continue
                placed = _parse_place_object(sub_body, sub_type)
                if placed:
                    placements.setdefault(placed[0], (sprite_id, placed[1]))

    names: Dict[int, str] = {}
    for char_id, (sprite_id, instance) in placements.items():
        owner = symbols.get(sprite_id)
        names[char_id] = f"{owner}.{instance}" if owner else instance
    return names


# ── Scanning ──────────────────────────────────────────────────────────────────

def parse_swf_text_fields(data: bytes, swf_name: str) -> List[TextField]:
    """Extract every text field from one already-decompressed SWF."""
    names = _collect_names(data)
    fields: List[TextField] = []

    for tag_type, body in iter_tags(data):
        if tag_type != TAG_DEFINE_EDIT_TEXT:
            continue
        raw = parse_edit_text(body)
        if raw is None:
            continue

        font_px = raw["font_px"]
        if font_px:
            size_source = FontSizeSource.DECLARED
        else:
            # Not stated — infer from the box height.  Approximate by construction.
            font_px = round(raw["height_px"] / _HEIGHT_TO_FONT_PX, 1)
            size_source = FontSizeSource.DERIVED
        if font_px <= 0:
            continue

        fields.append(TextField(
            swf=swf_name,
            name=names.get(raw["char_id"], ""),
            char_id=raw["char_id"],
            width_px=raw["width_px"],
            height_px=raw["height_px"],
            left_margin_px=raw["left_margin_px"],
            right_margin_px=raw["right_margin_px"],
            font_class=raw["font_class"],
            font_px=font_px,
            font_size_source=size_source,
            multiline=raw["multiline"],
            word_wrap=raw["word_wrap"],
            auto_size=raw["auto_size"],
        ))
    return fields


def parse_swf_file(path: Path) -> List[TextField]:
    """Extract every text field from a SWF on disk."""
    try:
        data = decompress_swf(path.read_bytes())
    except OSError as exc:
        logger.warning("Cannot read %s: %s", path, exc)
        return []
    if data is None:
        return []
    return parse_swf_text_fields(data, path.stem)


def _variant_key(field: TextField) -> Tuple[str, str, float]:
    """Identity of a widget across the standard and large-font builds of a menu.

    Deliberately **not** keyed on character id: the two menus are compiled
    separately, so ids drift and pairing on them produces nonsense (a "pair"
    whose large-font font is *smaller*).  Keying on the box the widget occupies —
    same menu, same field name, same usable width — pairs only widgets we can
    actually show are the same one, and across the shipped UI it yields zero
    such anomalies.
    """
    return (field.base_swf, field.name, round(field.usable_width_px, 1))


@dataclass
class WidgetCatalogue:
    """Every text field found in a game's UI, plus its font-class → family map."""

    fields: List[TextField]
    font_map: Dict[str, str]     # "$MAIN_Font_Bold" → "RF_55_M"
    swf_count: int

    def __post_init__(self) -> None:
        # Lazy variant index — an internal cache, not a constructor argument.
        self._variants: Optional[Dict[Tuple[str, str, float], List[TextField]]] = None

    def clipping(self) -> List[TextField]:
        """The fields where width is a real failure mode: they clip, and they measure."""
        return [f for f in self.fields if f.clips and f.is_measurable]

    def resolve_family(self, field: TextField) -> str:
        """Font family this field renders in, per fontconfig ("" if unmapped)."""
        return self.font_map.get(field.font_class, "")

    def _variant_index(self) -> Dict[Tuple[str, str, float], List[TextField]]:
        if self._variants is None:
            index: Dict[Tuple[str, str, float], List[TextField]] = {}
            for candidate in self.fields:
                if candidate.is_measurable:
                    index.setdefault(_variant_key(candidate), []).append(candidate)
            self._variants = index
        return self._variants

    def _key_group(self, field: TextField) -> List[TextField]:
        return self._variant_index().get(_variant_key(field), [])

    def variants(self, field: TextField) -> List[TextField]:
        """*field* together with its counterpart in the other font-size build.

        Only an **unambiguous 1:1** pairing counts.  A single menu often reuses
        one field name for several boxes of the same width (``text_tf`` alone
        appears 146 times across the shipped UI), and when it does there is no
        way to tell which of them the other build's field corresponds to — so
        the pair is refused rather than guessed.

        Guessing here is not a cosmetic error.  Without this check the key also
        groups *unrelated fields within the same menu*, and the tightest of them
        wins: picking ``chargenmenu › text_tf`` (843px @ 29px) measured against a
        126 px title in the same menu — a budget of six characters, which flags
        every translation as clipping.  It is the same class of nonsense the
        discarded character-id key produced, arriving through a different door.
        """
        group = self._key_group(field)
        standard = [f for f in group if not f.is_large_font]
        large = [f for f in group if f.is_large_font]
        if len(standard) > 1 or len(large) > 1:
            return [field]              # ambiguous — refuse to pair
        return group or [field]

    def worst_case(self, field: TextField) -> TextField:
        """The tightest build of *field* — its large-font twin where that is tighter.

        Returns *field* itself when the menu has no large-font variant, or when
        the variant is no tighter.  Compared on ``capacity_em``, since the boxes
        match and only the font differs; comparing pixel widths would say they
        are identical and miss the whole point.
        """
        return self.worst_case_with_reason(field)[0]

    def worst_case_with_reason(
        self, field: TextField
    ) -> Tuple[TextField, VariantOutcome]:
        """``worst_case``, plus *why* — so a UI can say whether the check applied.

        The returned field alone cannot distinguish "its large-font build is no
        tighter, so this is already the worst case" from "the large-font build
        was never checked, because we could not identify it".  Both leave the
        field unchanged, and reporting them alike would let the user believe a
        widget was covered when it never was.
        """
        variants = self.variants(field)
        tightest = min(variants, key=lambda f: (f.capacity_em, f.swf))
        if tightest.capacity_em < field.capacity_em:
            return tightest, VariantOutcome.TIGHTER
        if len(variants) > 1:
            return field, VariantOutcome.NO_TIGHTER
        # Nothing to compare against — but say *why*: a twin the key cannot
        # resolve is a check that did not happen, not a menu without one.
        if any(f.is_large_font != field.is_large_font for f in self._key_group(field)):
            return field, VariantOutcome.AMBIGUOUS
        return field, VariantOutcome.NO_VARIANT

    def __bool__(self) -> bool:
        return bool(self.fields)


def _iter_swf_sources(data_dir: Path) -> Iterable[Tuple[str, bytes]]:
    """Yield (name, raw_bytes) for every UI SWF, loose files first.

    Loose ``Interface/*.swf`` shadow their archived counterparts (that is how the
    game itself resolves them, and how localisation mods override menus), so a
    loose file wins and the archived copy of the same name is skipped.
    """
    seen: set[str] = set()

    interface = data_dir / "Interface"
    if interface.is_dir():
        for path in sorted(interface.glob("*.swf")):
            seen.add(path.name.lower())
            try:
                yield path.stem, path.read_bytes()
            except OSError as exc:
                logger.warning("Cannot read %s: %s", path, exc)

    for archive in sorted(data_dir.glob("*Interface*.ba2")):
        try:
            from .ba2_handler import BA2File

            with BA2File(archive) as ba2:
                for entry in ba2.list_files():
                    if not entry.lower().endswith(".swf"):
                        continue
                    base = Path(entry.replace("\\", "/")).name
                    if base.lower() in seen:
                        continue
                    seen.add(base.lower())
                    try:
                        yield Path(base).stem, ba2.extract(entry)
                    except Exception as exc:   # noqa: BLE001 - one bad entry
                        logger.warning("Cannot extract %s from %s: %s", entry, archive, exc)
        except Exception as exc:               # noqa: BLE001 - one bad archive
            logger.warning("Cannot open %s: %s", archive, exc)


def _find_fontconfig(data_dir: Path) -> Dict[str, str]:
    """Load the font-class → family map from any fontconfig*.txt in Interface/.

    Starfield ships one per language (``fontconfig_uk.txt``), and they agree on
    the class names, so the first readable one is enough to resolve a field's face.
    """
    interface = data_dir / "Interface"
    if not interface.is_dir():
        return {}
    for path in sorted(interface.glob("fontconfig*.txt")):
        cfg = parse_fontconfig(path)
        mapping = {k: v for k, v in cfg.items() if k != "__libs__"}
        if mapping:
            return mapping
    return {}


def scan_game_ui(data_dir: Path, progress=None) -> WidgetCatalogue:
    """Build the widget catalogue for a game Data directory.

    Reads loose ``Interface/*.swf`` and any ``*Interface*.ba2``, so it works on a
    vanilla install (everything archived) and on a modded/unpacked one alike.
    """
    fields: List[TextField] = []
    count = 0
    for name, raw in _iter_swf_sources(data_dir):
        count += 1
        if progress is not None:
            progress(count, name)
        data = decompress_swf(raw)
        if data is None:
            continue
        try:
            fields.extend(parse_swf_text_fields(data, name))
        except Exception as exc:               # noqa: BLE001 - never fail a whole scan
            logger.warning("Failed parsing %s: %s", name, exc)

    return WidgetCatalogue(
        fields=fields,
        font_map=_find_fontconfig(data_dir),
        swf_count=count,
    )


def sort_key(field: TextField) -> Tuple:
    """Catalogue ordering: exact-size fields first, then widest, then by name."""
    return (not field.is_exact, -field.usable_width_px, field.label)


def dedupe(fields: Sequence[TextField]) -> List[TextField]:
    """Collapse fields that are identical in every dimension a translator cares about.

    The same component is compiled into many menu SWFs (a button clip appears in
    dozens), so the raw catalogue is mostly repeats of a handful of real widgets.
    """
    seen: Dict[Tuple, TextField] = {}
    for f in fields:
        key = (f.name, round(f.usable_width_px, 1), round(f.font_px, 1), f.font_class)
        seen.setdefault(key, f)
    return sorted(seen.values(), key=sort_key)
