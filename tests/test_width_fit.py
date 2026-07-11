"""Tests for the UI width-fit simulator and the font metrics it rests on.

Two halves:

  • Metric extraction — SWF FontAdvanceTable + TTF ``hmtx``.  The SWF cases use
    hand-built tag bytes (no game files); the TTF cases use the real Starfield
    faces committed under ``data/fonts/``, so the advances asserted here are the
    genuine ones the game renders with.
  • The fit engine — placeholder rendering, measurement, length-critical
    classification and the row scan.

Pure Python throughout: no Qt, no QApplication, no game install.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from bethesda_strings.font_checker import (
    _SWF_UPEM_DEFINEFONT2,
    _SWF_UPEM_DEFINEFONT3,
    FontChecker,
    _parse_definefont2,
    parse_swf_glyphs,
    parse_ttf_glyphs,
)
from bethesda_strings.swf_widgets import FontSizeSource, TextField
from bethesda_strings.width_fit import (
    DEFAULT_WIDGET,
    ROLE_BODY,
    ROLE_BOLD,
    WIDGETS,
    Confidence,
    FontMetrics,
    WidgetSpec,
    check_fit,
    infer_widget_key,
    is_length_critical,
    load_bundled_metrics,
    load_metrics_for_family,
    metrics_from_sources,
    render_text,
    scan_rows,
)

FONTS_DIR = Path(__file__).parent.parent / "data" / "fonts"


def _text_field(
    *, width=300.0, left=0.0, right=0.0, exact=True, font=18.0
) -> TextField:
    """A synthetic TextField standing in for one read out of a game SWF."""
    return TextField(
        swf="hudmessagesmenu", name="QuestObjectiveText_tf", char_id=42,
        width_px=width, height_px=22.0,
        left_margin_px=left, right_margin_px=right,
        font_class="$MAIN_Font_Bold", font_px=font,
        font_size_source=(
            FontSizeSource.DECLARED if exact else FontSizeSource.DERIVED
        ),
        multiline=False, word_wrap=False, auto_size=False,
    )


# ── Synthetic SWF builders ────────────────────────────────────────────────────

def _definefont_body(
    *,
    codes: list[int],
    advances: list[int] | None,
    has_layout: bool,
    name: bytes = b"TestFont",
) -> bytes:
    """Build a DefineFont2/3 tag body with narrow offsets and narrow codes.

    Body layout (all little-endian), mirroring the SWF spec:
        FontID UI16 | Flags UI8 | LanguageCode UI8 | NameLen UI8 | Name
        NumGlyphs UI16 | OffsetTable UI16[n] | CodeTableOffset UI16
        GlyphShapeTable (dummy) | CodeTable UI8[n]
        [layout] Ascent SI16 | Descent SI16 | Leading SI16 | Advances SI16[n]
    """
    n = len(codes)
    flags = 0x80 if has_layout else 0x00   # FontFlagsHasLayout

    shapes = b"\x00" * 4                    # dummy glyph shapes, never parsed
    # Offsets are measured from the start of the OffsetTable field.
    code_table_offset = (n * 2) + 2 + len(shapes)

    out = bytearray()
    out += struct.pack("<H", 1)             # FontID
    out += bytes([flags, 0, len(name)])     # Flags, LanguageCode, NameLen
    out += name
    out += struct.pack("<H", n)             # NumGlyphs
    for i in range(n):                      # OffsetTable (values unused here)
        out += struct.pack("<H", code_table_offset + 100 + i)
    out += struct.pack("<H", code_table_offset)
    out += shapes
    out += bytes(codes)                     # CodeTable (UI8 — narrow codes)

    if has_layout:
        out += struct.pack("<hhh", 800, -200, 0)   # ascent/descent/leading
        for adv in (advances or []):
            out += struct.pack("<h", adv)
    return bytes(out)


def _wrap_swf(tag_type: int, body: bytes) -> bytes:
    """Wrap a tag body in a minimal uncompressed (FWS) SWF container."""
    header = bytearray(b"FWS\x06")
    header += struct.pack("<I", 0)   # FileLength (unchecked by the parser)
    header += b"\x00"                # RECT with nbits=0 → 1 byte
    header += struct.pack("<HH", 0, 1)  # FrameRate, FrameCount

    tag = bytearray()
    if len(body) < 0x3F:
        tag += struct.pack("<H", (tag_type << 6) | len(body))
    else:
        tag += struct.pack("<H", (tag_type << 6) | 0x3F)
        tag += struct.pack("<I", len(body))
    tag += body
    tag += struct.pack("<H", 0)      # End tag
    return bytes(header) + bytes(tag)


# ── SWF advance table ─────────────────────────────────────────────────────────

def test_definefont2_advances_normalised_by_1024_em():
    body = _definefont_body(
        codes=[ord("A"), ord("B")], advances=[512, 1024], has_layout=True
    )
    name, codes, advances = _parse_definefont2(body, _SWF_UPEM_DEFINEFONT2)

    assert name == "TestFont"
    assert codes == {ord("A"), ord("B")}
    assert advances[ord("A")] == pytest.approx(0.5)   # 512 / 1024
    assert advances[ord("B")] == pytest.approx(1.0)


def test_definefont3_uses_the_20480_unit_em_square():
    # DefineFont3 stores coordinates 20x larger; the same 0.5em advance is 10240.
    body = _definefont_body(codes=[ord("A")], advances=[10240], has_layout=True)
    _, _, advances = _parse_definefont2(body, _SWF_UPEM_DEFINEFONT3)
    assert advances[ord("A")] == pytest.approx(0.5)

    # Reading it with DefineFont2's EM square would be 20x too wide — the bug
    # this constant exists to prevent.
    _, _, wrong = _parse_definefont2(body, _SWF_UPEM_DEFINEFONT2)
    assert wrong[ord("A")] == pytest.approx(10.0)


def test_glyph_coverage_without_layout_flag_yields_no_advances():
    """A font tag may ship glyphs but no metrics — we must not invent widths."""
    body = _definefont_body(codes=[ord("A"), ord("B")], advances=None, has_layout=False)
    _, codes, advances = _parse_definefont2(body, _SWF_UPEM_DEFINEFONT2)
    assert codes == {ord("A"), ord("B")}
    assert advances == {}


def test_truncated_advance_table_is_survived():
    body = _definefont_body(codes=[ord("A"), ord("B")], advances=[512, 1024], has_layout=True)
    truncated = body[:-3]   # lop off part of the advance table
    _, codes, advances = _parse_definefont2(truncated, _SWF_UPEM_DEFINEFONT2)
    assert codes == {ord("A"), ord("B")}   # coverage still parsed
    assert advances == {}                  # but metrics are refused, not guessed


def test_negative_advance_is_ignored():
    body = _definefont_body(codes=[ord("A"), ord("B")], advances=[-5, 512], has_layout=True)
    _, _, advances = _parse_definefont2(body, _SWF_UPEM_DEFINEFONT2)
    assert ord("A") not in advances
    assert advances[ord("B")] == pytest.approx(0.5)


def test_swf_container_roundtrip(tmp_path: Path):
    body = _definefont_body(codes=[ord("A"), ord("B")], advances=[512, 256], has_layout=True)
    swf = tmp_path / "fonts.swf"
    swf.write_bytes(_wrap_swf(48, body))   # tag 48 = DefineFont2

    sources = parse_swf_glyphs(swf)
    assert len(sources) == 1
    src = sources[0]
    assert src.has_metrics
    assert src.advances[ord("A")] == pytest.approx(0.5)
    assert src.advances[ord("B")] == pytest.approx(0.25)


def test_swf_definefont3_tag_uses_the_larger_em_square(tmp_path: Path):
    body = _definefont_body(codes=[ord("A")], advances=[10240], has_layout=True)
    swf = tmp_path / "fonts3.swf"
    swf.write_bytes(_wrap_swf(75, body))   # tag 75 = DefineFont3

    src = parse_swf_glyphs(swf)[0]
    assert src.advances[ord("A")] == pytest.approx(0.5)


def test_font_checker_merges_advances_and_reports_absence(tmp_path: Path):
    body = _definefont_body(codes=[ord("A")], advances=[512], has_layout=True)
    swf = tmp_path / "f.swf"
    swf.write_bytes(_wrap_swf(48, body))

    fc = FontChecker()
    assert fc.has_metrics is False          # nothing loaded yet
    fc.load_swf(swf)
    assert fc.has_metrics is True
    assert fc.combined_advances()[ord("A")] == pytest.approx(0.5)


# ── TTF hmtx (real Starfield faces) ───────────────────────────────────────────

def test_ttf_hmtx_advances_are_real_and_proportional():
    src = parse_ttf_glyphs(FONTS_DIR / "RF_35_M.ttf")[0]
    adv = src.advances

    assert src.has_metrics
    # Every mapped glyph gets a metric.
    assert len(adv) == src.glyph_count
    # Proportional font: 'i' is far narrower than 'M'/'W'.
    assert adv[ord("i")] < adv[ord("M")]
    assert adv[ord("i")] < adv[ord("W")]
    # Advances are sane EM fractions, not raw font units.
    assert all(0.0 <= a <= 2.0 for a in adv.values())


def test_body_face_covers_cyrillic_and_latin_display_face_does_not():
    """Why role matters: measuring Ukrainian with the Latin face would be fiction."""
    body = parse_ttf_glyphs(FONTS_DIR / "RF_35_M.ttf")[0]
    latin = parse_ttf_glyphs(FONTS_DIR / "NB_Architekt_Light.ttf")[0]

    assert ord("щ") in body.advances
    assert ord("щ") not in latin.advances
    assert ord("A") in latin.advances


def test_bold_face_is_wider_than_regular():
    """A bold button label measured with the regular face under-reports overflow."""
    regular = parse_ttf_glyphs(FONTS_DIR / "RF_35_M.ttf")[0]
    semibold = parse_ttf_glyphs(FONTS_DIR / "RF_55_SB.ttf")[0]
    assert semibold.advances[ord("M")] > regular.advances[ord("M")]


def test_load_bundled_metrics_provides_every_role():
    m = load_bundled_metrics()
    assert ROLE_BODY in m and ROLE_BOLD in m
    assert m[ROLE_BODY].advances
    assert m[ROLE_BOLD].advances


def test_metrics_from_sources_refuses_metricless_fonts(tmp_path: Path):
    body = _definefont_body(codes=[ord("A")], advances=None, has_layout=False)
    swf = tmp_path / "nolayout.swf"
    swf.write_bytes(_wrap_swf(48, body))
    sources = parse_swf_glyphs(swf)

    assert sources[0].codepoints            # glyphs are there
    assert metrics_from_sources(sources) is None   # widths are not — say so


# ── Measurement ───────────────────────────────────────────────────────────────

def _fake_metrics(**kw) -> FontMetrics:
    # 'a' is half an em, 'w' is a full em — easy arithmetic.
    return FontMetrics(name="fake", advances={ord("a"): 0.5, ord("w"): 1.0}, **kw)


def test_measure_sums_advances_times_pixel_size():
    m = _fake_metrics()
    assert m.measure("aa", 100) == pytest.approx(100.0)   # 0.5 + 0.5 em
    assert m.measure("aw", 100) == pytest.approx(150.0)   # 0.5 + 1.0 em
    assert m.measure("", 100) == 0.0


def test_measure_scales_linearly_with_font_size():
    m = _fake_metrics()
    assert m.measure("aw", 50) == pytest.approx(m.measure("aw", 100) / 2)


def test_unknown_codepoint_falls_back_without_understating_width():
    m = _fake_metrics()
    # An unmetered glyph must contribute *something* — a 0 would hide overflow.
    assert m.measure("z", 100) > 0
    assert m.coverage("aw") == pytest.approx(1.0)
    assert m.coverage("az") == pytest.approx(0.5)


# ── Placeholder rendering ─────────────────────────────────────────────────────

def test_formatting_tags_render_as_nothing():
    out, has_value = render_text("<font color='#fff'>Open</font>")
    assert out == "Open"
    assert has_value is False


def test_value_placeholders_become_representative_text():
    """Measuring the literal '<Alias=Player>' would be nonsense; deleting it hides overflow."""
    out, has_value = render_text("Talk to <Alias=Captain>")
    assert "<Alias" not in out
    assert out.startswith("Talk to ")
    assert len(out) > len("Talk to ")
    assert has_value is True


@pytest.mark.parametrize("text", ["%s items", "%d credits", "{0} left", "[PLYR]", "<mag> dmg"])
def test_every_value_placeholder_form_is_substituted_and_flagged(text):
    out, has_value = render_text(text)
    assert has_value is True
    assert "%" not in out and "{" not in out and "<" not in out and "[" not in out


def test_runtime_value_marks_the_result_approximate():
    m = load_bundled_metrics()
    res = check_fit("Вітаю, <Alias=Player>", WIDGETS["menu_item"], m[ROLE_BODY])
    assert res.has_runtime_value is True
    assert res.is_approximate is True


# ── Length-critical classification ────────────────────────────────────────────

@pytest.mark.parametrize("text", ["Inventory", "ACCEPT", "Take All", "Ship Builder"])
def test_short_labels_are_length_critical(text):
    assert is_length_critical(text) is True


@pytest.mark.parametrize("text", [
    "You should see New Atlantis before you die, at least once in your life.",
    "Line one\nLine two",
    "",
    "Are you sure you want to leave?",
])
def test_prose_and_multiline_are_not_length_critical(text):
    assert is_length_critical(text) is False


def test_all_caps_label_infers_a_button():
    assert infer_widget_key("ACCEPT") == "button"
    # Unicode-correct: Cyrillic caps count too.
    assert infer_widget_key("ПРИЙНЯТИ") == "button"


def test_widget_inference_falls_back_by_length():
    assert infer_widget_key("Inventory") == "tab"          # <= 14 chars
    assert infer_widget_key("Ship Systems Panel") == DEFAULT_WIDGET
    assert infer_widget_key("This is a full sentence, and it wraps.") is None


# ── Fit checking ──────────────────────────────────────────────────────────────

def test_overflow_is_flagged_with_pixel_arithmetic():
    m = _fake_metrics()
    spec = WidgetSpec(key="t", label="T", budget_px=100, font_px=100, role=ROLE_BODY)

    fits = check_fit("aa", spec, m)      # 100 px vs 100 px budget
    assert fits.fits is True
    assert fits.overflow_px == 0

    over = check_fit("aaw", spec, m)     # 200 px vs 100 px budget
    assert over.fits is False
    assert over.overflow_px == pytest.approx(100.0)
    assert over.fill_ratio == pytest.approx(2.0)


def test_uppercase_widget_measures_the_transformed_text():
    """A caps-transforming button draws wider than its raw string suggests."""
    m = load_bundled_metrics()[ROLE_BOLD]
    plain = WidgetSpec(key="a", label="a", budget_px=999, font_px=24, role=ROLE_BOLD)
    caps = WidgetSpec(
        key="b", label="b", budget_px=999, font_px=24, role=ROLE_BOLD, uppercase=True
    )
    assert check_fit("Accept", caps, m).width_px > check_fit("Accept", plain, m).width_px


def test_source_ratio_exposes_expansion_independent_of_the_budget():
    m = load_bundled_metrics()[ROLE_BODY]
    spec = WIDGETS["menu_item"]
    res = check_fit("Скасувати останню дію", spec, m, source="Undo")
    # The English fit by construction; a 3x-wider translation will not.
    assert res.source_ratio > 3.0


def test_with_budget_overrides_only_the_budget():
    spec = WIDGETS["button"]
    tweaked = spec.with_budget(123)
    assert tweaked.budget_px == 123
    assert tweaked.font_px == spec.font_px
    assert tweaked.uppercase == spec.uppercase
    assert spec.budget_px != 123          # frozen — original untouched


def test_budgets_declare_their_provenance_honestly():
    """Guards the honesty of the tool: nothing claims SWF provenance it lacks.

    The two HUD widgets were read out of the shipped SWFs, so they may say
    MEASURED — and must cite the field they came from.  Everything else is still
    a guess and has to admit it.
    """
    measured = {k for k, w in WIDGETS.items() if w.confidence is Confidence.MEASURED}
    assert measured == {"hud_objective", "notification"}
    for key in measured:
        assert "›" in WIDGETS[key].note      # names the source field

    rest = [w for k, w in WIDGETS.items() if k not in measured]
    assert all(w.confidence is Confidence.ESTIMATED for w in rest)


# ── Real widgets read out of the game ─────────────────────────────────────────

def test_widget_spec_from_a_real_text_field_is_measured_when_the_swf_states_the_size():
    field = _text_field(width=300.0, left=10.0, right=15.0, exact=True, font=18.0)
    spec = WidgetSpec.from_text_field(field, "RF_55_M")

    # The budget is the *usable* width — margins are not available to text.
    assert spec.budget_px == pytest.approx(275.0)
    assert spec.font_px == pytest.approx(18.0)
    assert spec.confidence is Confidence.MEASURED
    assert spec.family == "RF_55_M"
    assert spec.role == ROLE_BOLD           # resolved from the family, not guessed


def test_widget_spec_is_derived_when_the_swf_omits_the_font_size():
    spec = WidgetSpec.from_text_field(_text_field(exact=False), "RF_35_M")
    assert spec.confidence is Confidence.DERIVED
    assert "inferred" in spec.note          # the caveat travels with the spec
    assert spec.role == ROLE_BODY


def test_family_resolves_to_the_bundled_face():
    bold = load_metrics_for_family("RF_55_M")
    body = load_metrics_for_family("RF_35_M")
    assert bold is not None and bold.name == "RF_55_M"
    assert body is not None and body.name == "RF_35_M"


def test_unbundled_and_icon_fonts_are_refused_not_guessed():
    """A controller-glyph field is icons, not text — measuring it would be nonsense."""
    assert load_metrics_for_family("Genesis Controller  Buttons") is None
    assert load_metrics_for_family("Starfield_Grotesk_R") is None
    assert load_metrics_for_family("") is None


def test_a_real_widget_can_be_scanned_against():
    field = _text_field(width=335.0, exact=True, font=18.0)
    spec = WidgetSpec.from_text_field(field, "RF_55_M")
    face = load_metrics_for_family("RF_55_M")
    assert face is not None
    metrics = {spec.role: face}

    rows = [{"id": 1, "original": "Board the ship",
             "translated": "Піднятися на борт корабля негайно та відлетіти"}]
    res = scan_rows(rows, metrics, budgets={spec.key: spec}, widget_override=spec.key)
    assert res.checked == 1
    assert res.overflow_count == 1
    assert res.results[0].widget_key == spec.key


# ── Row scan ──────────────────────────────────────────────────────────────────

def _rows():
    return [
        {"id": 1, "original": "Undo", "translated": "Скасувати останню дію"},   # overflows
        {"id": 2, "original": "Exit", "translated": "Вихід"},                   # fits
        {"id": 3, "original": "Long prose line that certainly wraps in game, yes.",
         "translated": "Довгий текст, який точно переноситься у грі."},         # prose
        {"id": 4, "original": "Settings", "translated": ""},                    # untranslated
    ]


def test_scan_reports_only_overflowing_length_critical_rows():
    res = scan_rows(_rows(), load_bundled_metrics())

    assert res.untranslated == 1
    assert res.skipped_prose == 1
    assert res.checked == 2
    assert res.overflow_count == 1
    assert res.results[0].row_index == 0
    assert res.results[0].string_id == 1


def test_scan_sorts_worst_overflow_first():
    rows = [
        {"id": 1, "original": "Undo", "translated": "Скасувати"},
        {"id": 2, "original": "Undo", "translated": "Скасувати останню виконану дію негайно"},
    ]
    res = scan_rows(rows, load_bundled_metrics(), widget_override="tab")
    assert res.overflow_count >= 1
    overflows = [r.overflow_px for r in res.results]
    assert overflows == sorted(overflows, reverse=True)


def test_widget_override_forces_every_label_into_one_class():
    res = scan_rows(_rows(), load_bundled_metrics(), widget_override="button")
    assert res.checked == 2
    assert all(r.widget_key == "button" for r in res.results)


def test_widget_override_still_never_width_checks_prose():
    """A paragraph cannot 'overflow a button' — it was never going in one."""
    res = scan_rows(_rows(), load_bundled_metrics(), widget_override="button")
    assert res.skipped_prose == 1
    assert all(r.row_index != 2 for r in res.results)   # row 2 is the prose line


def test_custom_budget_changes_the_verdict():
    rows = [{"id": 1, "original": "Exit", "translated": "Вихід"}]
    metrics = load_bundled_metrics()

    assert scan_rows(rows, metrics, widget_override="tab").overflow_count == 0

    tight = {"tab": WIDGETS["tab"].with_budget(5)}
    res = scan_rows(rows, metrics, budgets=tight, widget_override="tab")
    assert res.overflow_count == 1


def test_scan_without_any_metrics_reports_nothing_rather_than_guessing():
    res = scan_rows(_rows(), {})
    assert res.overflow_count == 0
    assert res.checked == 0
