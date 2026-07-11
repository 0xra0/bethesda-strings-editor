"""Tests for real UI widget extraction from Scaleform SWFs.

`DefineEditText` is a chain of *optional* fields gated by a flags word, so the
parser's whole job is consuming exactly the fields that are present — skip one
and every byte after it is misread. These tests therefore build tag bytes by
hand and toggle the flags, rather than asserting against a game install.

The one thing they cannot fabricate is Starfield's own convention (HasFont clear,
size in the HTML initial text), so that shape is pinned explicitly.

Pure Python: no Qt, no game files.
"""

from __future__ import annotations

import struct

import pytest

from bethesda_strings.swf import (
    TAG_DEFINE_EDIT_TEXT,
    TWIPS,
    decompress_swf,
    iter_tags,
    read_cstring,
    read_rect,
)
from bethesda_strings.swf_widgets import (
    FontSizeSource,
    TextField,
    WidgetCatalogue,
    dedupe,
    parse_edit_text,
    parse_swf_text_fields,
)

# Flag bits (SWF spec order, MSB-first in the 2-byte flags word).
HAS_TEXT = 0x8000
WORD_WRAP = 0x4000
MULTILINE = 0x2000
HAS_TEXT_COLOR = 0x0400
HAS_MAX_LENGTH = 0x0200
HAS_FONT = 0x0100
HAS_FONT_CLASS = 0x0080
AUTO_SIZE = 0x0040
HAS_LAYOUT = 0x0020


def _rect(xmin, xmax, ymin, ymax, nbits=16) -> bytes:
    """Encode a bit-packed RECT from pixel values."""
    def bits(v):
        v = int(v * TWIPS)
        return format(v & ((1 << nbits) - 1), f"0{nbits}b")

    stream = format(nbits, "05b") + "".join(
        bits(v) for v in (xmin, xmax, ymin, ymax)
    )
    stream += "0" * (-len(stream) % 8)
    return bytes(int(stream[i:i + 8], 2) for i in range(0, len(stream), 8))


def _parsed(body: bytes) -> dict:
    """parse_edit_text, asserting it decoded — so a None fails loudly, not cryptically."""
    field = parse_edit_text(body)
    assert field is not None, "expected the tag to decode"
    return field


def _edit_text(
    *,
    char_id=7,
    width=200.0,
    height=22.0,
    flags=HAS_TEXT | HAS_FONT_CLASS | HAS_LAYOUT,
    font_class="$MAIN_Font_Bold",
    font_height_twips=None,
    margins=(0, 0),
    initial_text='<p><font size="18">Hi</font></p>',
) -> bytes:
    """Build a DefineEditText tag body."""
    out = bytearray()
    out += struct.pack("<H", char_id)
    out += _rect(0, width, 0, height)
    out += struct.pack(">H", flags)

    if flags & HAS_FONT:
        out += struct.pack("<H", 1)                      # FontID
    if flags & HAS_FONT_CLASS:
        out += font_class.encode() + b"\x00"
    if flags & HAS_FONT:
        out += struct.pack("<H", font_height_twips or 0)
    if flags & HAS_TEXT_COLOR:
        out += b"\xff\xff\xff\xff"
    if flags & HAS_MAX_LENGTH:
        out += struct.pack("<H", 64)
    if flags & HAS_LAYOUT:
        out += bytes([0])                                # Align
        out += struct.pack("<H", int(margins[0] * TWIPS))
        out += struct.pack("<H", int(margins[1] * TWIPS))
        out += struct.pack("<hh", 0, 0)                  # Indent, Leading
    out += b"\x00"                                       # VariableName (empty)
    if flags & HAS_TEXT:
        out += initial_text.encode() + b"\x00"
    return bytes(out)


# ── Geometry ──────────────────────────────────────────────────────────────────

def test_bounds_and_margins_are_decoded_in_pixels():
    f = _parsed(_edit_text(width=300.0, height=24.0, margins=(4.0, 6.0)))
    assert f["width_px"] == pytest.approx(300.0)
    assert f["height_px"] == pytest.approx(24.0)
    assert f["left_margin_px"] == pytest.approx(4.0)
    assert f["right_margin_px"] == pytest.approx(6.0)


def test_usable_width_excludes_the_margins():
    """The budget is the space text actually gets, not the box's outer width."""
    fields = parse_swf_text_fields(
        _swf(_edit_text(width=300.0, margins=(10.0, 15.0))), "menu"
    )
    assert fields[0].width_px == pytest.approx(300.0)
    assert fields[0].usable_width_px == pytest.approx(275.0)


def test_rect_roundtrips_through_the_bit_packed_encoder():
    (xmin, xmax, ymin, ymax), _ = read_rect(_rect(0, 100.0, 0, 20.0), 0)
    assert (xmax - xmin) / TWIPS == pytest.approx(100.0)
    assert (ymax - ymin) / TWIPS == pytest.approx(20.0)


# ── Optional-field chain ──────────────────────────────────────────────────────
# Each of these adds a present-but-skippable field *before* the ones we read. If
# the parser mis-handles any, the margins/text land on garbage.

@pytest.mark.parametrize("extra", [
    0,
    HAS_TEXT_COLOR,
    HAS_MAX_LENGTH,
    HAS_TEXT_COLOR | HAS_MAX_LENGTH,
])
def test_optional_fields_do_not_misalign_the_ones_after_them(extra):
    body = _edit_text(
        flags=HAS_TEXT | HAS_FONT_CLASS | HAS_LAYOUT | extra,
        width=250.0, margins=(5.0, 5.0),
    )
    f = _parsed(body)
    assert f["width_px"] == pytest.approx(250.0)
    assert f["left_margin_px"] == pytest.approx(5.0)
    assert f["font_class"] == "$MAIN_Font_Bold"
    assert f["font_px"] == 18       # read from the HTML, after everything above


def test_embedded_font_height_is_preferred_when_present():
    body = _edit_text(
        flags=HAS_TEXT | HAS_FONT | HAS_FONT_CLASS | HAS_LAYOUT,
        font_height_twips=26 * TWIPS,
        initial_text='<font size="99">x</font>',   # HTML must lose to the real field
    )
    assert _parsed(body)["font_px"] == pytest.approx(26.0)


def test_malformed_body_returns_none_rather_than_raising():
    assert parse_edit_text(b"\x01") is None
    assert parse_edit_text(b"") is None


# ── Starfield's actual convention ─────────────────────────────────────────────

def test_font_size_comes_from_the_html_when_the_tag_omits_it():
    """Starfield leaves HasFont clear and puts the size in the HTML initial text.

    Every one of the 1350 shipped Interface fields does this, so a parser that
    only looks at FontHeight finds no sizes at all.
    """
    body = _edit_text(
        flags=HAS_TEXT | HAS_FONT_CLASS | HAS_LAYOUT,
        initial_text='<p align="center"><font size="22" color="#fff">Go</font></p>',
    )
    f = _parsed(body)
    assert f["font_px"] == 22
    assert f["font_class"] == "$MAIN_Font_Bold"   # by class, not by embedded id


def test_declared_size_is_marked_exact_and_missing_size_is_derived():
    declared = parse_swf_text_fields(_swf(_edit_text(height=22.0)), "m")[0]
    assert declared.font_px == 18
    assert declared.font_size_source is FontSizeSource.DECLARED
    assert declared.is_exact is True

    # No size anywhere -> inferred from the box height, and flagged as such.
    silent = parse_swf_text_fields(
        _swf(_edit_text(height=22.0, initial_text="plain text")), "m"
    )[0]
    assert silent.font_size_source is FontSizeSource.DERIVED
    assert silent.is_exact is False
    assert 14 <= silent.font_px <= 20        # 22 / 1.22 ≈ 18


# ── Clip behaviour: what makes a field length-critical ─────────────────────────

def test_a_field_that_cannot_reflow_clips():
    field = parse_swf_text_fields(_swf(_edit_text()), "m")[0]
    assert field.clips is True


@pytest.mark.parametrize("flag", [MULTILINE, WORD_WRAP, AUTO_SIZE])
def test_any_reflow_capability_means_it_does_not_clip(flag):
    """Wrapping/growing fields fail downward, not sideways — width is not their bug."""
    body = _edit_text(flags=HAS_TEXT | HAS_FONT_CLASS | HAS_LAYOUT | flag)
    field = parse_swf_text_fields(_swf(body), "m")[0]
    assert field.clips is False


# ── SWF container ─────────────────────────────────────────────────────────────

def _swf(*bodies: bytes) -> bytes:
    """Wrap tag bodies in a minimal uncompressed SWF."""
    out = bytearray(b"FWS\x06")
    out += struct.pack("<I", 0)
    out += b"\x00"                      # stage RECT, nbits=0
    out += struct.pack("<HH", 0, 1)     # frame rate + count
    for body in bodies:
        if len(body) < 0x3F:
            out += struct.pack("<H", (TAG_DEFINE_EDIT_TEXT << 6) | len(body))
        else:
            out += struct.pack("<H", (TAG_DEFINE_EDIT_TEXT << 6) | 0x3F)
            out += struct.pack("<I", len(body))
        out += body
    out += struct.pack("<H", 0)         # End
    return bytes(out)


def test_scanning_a_swf_finds_every_text_field():
    data = _swf(_edit_text(char_id=1), _edit_text(char_id=2), _edit_text(char_id=3))
    fields = parse_swf_text_fields(data, "menu")
    assert [f.char_id for f in fields] == [1, 2, 3]
    assert all(f.swf == "menu" for f in fields)


def test_iter_tags_stops_cleanly_on_truncation():
    """A tag whose payload runs past EOF is dropped, not half-read."""
    intact = _swf(_edit_text())
    assert len(parse_swf_text_fields(intact, "m")) == 1

    truncated = intact[:-4]             # lop off the End tag and some payload
    assert parse_swf_text_fields(truncated, "m") == []
    assert list(iter_tags(truncated)) == []


def test_decompress_rejects_lzma_rather_than_guessing():
    assert decompress_swf(b"ZWS\x0d" + b"\x00" * 16) is None
    assert decompress_swf(b"junk") is None


def test_read_cstring_survives_a_missing_terminator():
    text, pos = read_cstring(b"abc", 0)
    assert text == "abc" and pos == 3


# ── Catalogue ─────────────────────────────────────────────────────────────────

def _tf(name="a", w=100.0, font=18.0, exact=True, clips=True, swf="m") -> TextField:
    return TextField(
        swf=swf, name=name, char_id=1,
        width_px=w, height_px=22.0, left_margin_px=0.0, right_margin_px=0.0,
        font_class="$MAIN_Font", font_px=font,
        font_size_source=FontSizeSource.DECLARED if exact else FontSizeSource.DERIVED,
        multiline=not clips, word_wrap=False, auto_size=False,
    )


def test_catalogue_lists_only_clipping_fields():
    cat = WidgetCatalogue(
        fields=[_tf("btn", clips=True), _tf("body", clips=False)],
        font_map={}, swf_count=1,
    )
    assert [f.name for f in cat.clipping()] == ["btn"]


def test_catalogue_resolves_the_font_family_via_fontconfig():
    cat = WidgetCatalogue(
        fields=[_tf()], font_map={"$MAIN_Font": "RF_35_M"}, swf_count=1,
    )
    assert cat.resolve_family(cat.fields[0]) == "RF_35_M"
    # An unmapped class resolves to nothing rather than to a wrong guess.
    assert WidgetCatalogue([], {}, 0).resolve_family(_tf()) == ""


def test_dedupe_collapses_the_same_component_repeated_across_menus():
    """One button clip is compiled into dozens of SWFs; the user wants it once."""
    fields = [_tf(swf="menu_a"), _tf(swf="menu_b"), _tf(w=300.0, swf="menu_c")]
    assert len(dedupe(fields)) == 2


def test_dedupe_sorts_exact_sized_widgets_first():
    fields = [_tf("derived", w=999.0, exact=False), _tf("exact", w=10.0, exact=True)]
    assert [f.name for f in dedupe(fields)] == ["exact", "derived"]


def test_zero_width_runtime_sized_fields_are_not_measurable():
    """A 0px box is sized by ActionScript later — measuring it flags everything."""
    runtime_sized = _tf(w=0.0)
    assert runtime_sized.is_measurable is False
    cat = WidgetCatalogue([runtime_sized, _tf("real", w=100.0)], {}, 1)
    assert [f.name for f in cat.clipping()] == ["real"]


# ── Large-font (_lrg) worst case ──────────────────────────────────────────────

def test_large_font_variant_is_recognised_from_the_swf_name():
    assert _tf(swf="missionmenu_lrg").is_large_font is True
    assert _tf(swf="missionmenu_lrg").base_swf == "missionmenu"
    assert _tf(swf="missionmenu").is_large_font is False
    assert _tf(swf="missionmenu").base_swf == "missionmenu"


def test_capacity_is_what_decides_which_build_is_tighter():
    """Same box, bigger font = less room. Pixel width alone would call them equal."""
    std = _tf(w=100.0, font=20.0)
    lrg = _tf(w=100.0, font=40.0, swf="m_lrg")
    assert std.usable_width_px == lrg.usable_width_px    # identical box…
    assert std.capacity_em == pytest.approx(5.0)         # …but half the room
    assert lrg.capacity_em == pytest.approx(2.5)


def test_worst_case_picks_the_large_font_build():
    std = _tf(name="Label_tf", w=100.0, font=18.0, swf="missionmenu")
    lrg = _tf(name="Label_tf", w=100.0, font=48.0, swf="missionmenu_lrg")
    cat = WidgetCatalogue([std, lrg], {}, 2)

    # From either entry, the tighter build is the one measured against.
    assert cat.worst_case(std).swf == "missionmenu_lrg"
    assert cat.worst_case(lrg).swf == "missionmenu_lrg"


def test_worst_case_keeps_the_field_when_the_variant_is_no_tighter():
    """192 of the shipped pairs have an identical font; swapping them is pointless."""
    std = _tf(name="Label_tf", w=100.0, font=18.0, swf="bartermenu")
    lrg = _tf(name="Label_tf", w=100.0, font=18.0, swf="bartermenu_lrg")
    cat = WidgetCatalogue([std, lrg], {}, 2)
    assert cat.worst_case(std).swf == "bartermenu"       # stable: no needless swap


def test_worst_case_is_a_no_op_without_a_large_font_twin():
    lone = _tf(name="Label_tf", swf="buttonclips")
    cat = WidgetCatalogue([lone], {}, 1)
    assert cat.worst_case(lone) is lone


def test_variants_do_not_pair_widgets_in_different_boxes():
    """The pairing key is the box, not the character id.

    Ids drift between the two separately-compiled menus, and pairing on them
    produced nonsense — "pairs" whose large-font build was *smaller*. Two fields
    of different widths are not the same widget, whatever their ids say.
    """
    std = _tf(name="Text_tf", w=354.0, font=36.0, swf="armorcraftingmenu")
    lrg = _tf(name="Text_tf", w=241.0, font=25.0, swf="armorcraftingmenu_lrg")
    cat = WidgetCatalogue([std, lrg], {}, 2)
    assert cat.worst_case(std) is std       # not paired, so not swapped
    assert cat.variants(std) == [std]
