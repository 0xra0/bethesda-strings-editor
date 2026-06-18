"""
Tests for the speaker (NPC) map (gui/speaker_map.py).

All pure functions — no Qt, no game files.  Covers the layered voice-type parser:
named characters (curated + fallback), gender f/m/x, generic faction+gender+
variant descriptors, the non-dialogue categories (creature/announcer/robot/
player/test), cut-content and content-pack markers, and de-duplication.

Run with:
    python -m pytest tests/test_speaker_map.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from gui.speaker_map import (  # noqa: E402
    CAT_ANNOUNCER,
    CAT_CREATURE,
    CAT_CROWD,
    CAT_EXPRESSIONS,
    CAT_GENERIC,
    CAT_NAMED,
    CAT_PLAYER,
    CAT_ROBOT,
    CAT_TEST,
    CAT_UNKNOWN,
    describe_voice_type,
    describe_voice_types,
)


# ── Named characters (curated dictionary) ────────────────────────────────────────

@pytest.mark.parametrize(
    "vt,name,gender",
    [
        ("npcfsarahmorgan", "Sarah Morgan", "female"),
        ("npcmsamcoe", "Sam Coe", "male"),
        ("npcmbarrett", "Barrett", "male"),
        ("npcfandreja", "Andreja", "female"),
        ("npcmwalterstroud", "Walter Stroud", "male"),
        ("npcmmarcoschen", "Marcos Chen", "male"),
        ("npcfimogenesalzo", "Imogene Salzo", "female"),
    ],
)
def test_named_curated(vt, name, gender):
    info = describe_voice_type(vt)
    assert info.is_named is True
    assert info.category == CAT_NAMED
    assert info.display_name == name
    assert info.gender == gender


def test_named_gender_neutral_x():
    info = describe_voice_type("npcxsivan")
    assert info.gender == "neutral"
    assert info.category == CAT_NAMED
    assert info.is_named is True


def test_named_no_gender_letter():
    # Some named entries omit the gender letter entirely.
    info = describe_voice_type("npccoopercarr")
    assert info.is_named is True
    assert info.display_name == "Cooper Carr"
    assert info.gender == ""


def test_named_starting_with_gender_letter_is_not_mis_split():
    # "npcfrankrenick" must resolve as Frank Renick, not gender=f + "rankrenick".
    info = describe_voice_type("npcfrankrenick")
    assert info.display_name == "Frank Renick"
    assert info.is_named is True


def test_named_fallback_title_cases_unknown():
    # An uncurated named NPC still reads as a (title-cased) named character.
    info = describe_voice_type("npcfzzzunknownperson")
    assert info.is_named is True
    assert info.category == CAT_NAMED
    assert info.gender == "female"
    assert info.display_name.startswith("Z")  # title-cased first letter


# ── Generic faction + gender + variant ───────────────────────────────────────────

def test_generic_faction_gender_number():
    info = describe_voice_type("crimsonfleetfemale03")
    assert info.is_named is False
    assert info.category == CAT_GENERIC
    assert info.faction == "Crimson Fleet"
    assert info.gender == "female"
    assert "Female 03" in info.display_name


def test_generic_plain_faction():
    info = describe_voice_type("genericmale05")
    assert info.faction == "Generic citizen"
    assert info.gender == "male"
    assert info.category == CAT_GENERIC


def test_generic_ucsecurity():
    info = describe_voice_type("ucsecuritymale03")
    assert info.faction == "UC Security"
    assert info.gender == "male"


def test_generic_varuun_apostrophe():
    info = describe_voice_type("varuunzealotmale02")
    assert info.faction == "Va'ruun Zealot"
    assert info.gender == "male"


def test_generic_accent_descriptor():
    info = describe_voice_type("genericfemaleaccent_french")
    assert info.gender == "female"
    assert "French" in info.display_name
    assert "accent" in info.display_name.lower()


def test_generic_child_descriptor():
    info = describe_voice_type("genericmalechild")
    assert info.gender == "male"
    assert "child" in info.display_name.lower()


def test_generic_old_descriptor():
    info = describe_voice_type("genericmaleold")
    assert "Elderly" in info.display_name


def test_generic_youngadult_descriptor():
    info = describe_voice_type("genericfemaleyoungadult")
    assert "Young adult" in info.display_name


def test_generic_nonbinary_is_neutral():
    info = describe_voice_type("genericnonbinary01")
    assert info.gender == "neutral"
    assert info.category == CAT_GENERIC


def test_generic_strips_leading_scene_code():
    # Stray scene prefixes like "ms01_" must not leak into the faction label.
    info = describe_voice_type("ms01_genericfemale02")
    assert info.faction == "Generic citizen"
    assert info.gender == "female"


# ── Crowd ────────────────────────────────────────────────────────────────────────

def test_crowd_category():
    info = describe_voice_type("genericcrowdfemale01")
    assert info.category == CAT_CROWD
    assert info.faction.startswith("Crowd")
    assert info.gender == "female"
    assert "Female 01" in info.display_name


def test_crowd_with_accent():
    info = describe_voice_type("genericcrowdfrenchfemale01")
    assert info.category == CAT_CROWD
    assert "French" in info.faction


# ── Non-dialogue categories ──────────────────────────────────────────────────────

def test_announcer_with_gender():
    info = describe_voice_type("announcerfthelock")
    assert info.category == CAT_ANNOUNCER
    assert info.gender == "female"


def test_announcer_male():
    info = describe_voice_type("announcermstation")
    assert info.category == CAT_ANNOUNCER
    assert info.gender == "male"


@pytest.mark.parametrize(
    "vt",
    [
        "cr_bipeda_default",
        "crocopedea_default",
        "crterrormorph",
        "crhexapoda",
    ],
)
def test_creatures(vt):
    info = describe_voice_type(vt)
    assert info.category == CAT_CREATURE
    assert info.is_named is False


def test_robot():
    info = describe_voice_type("robotmodela")
    assert info.category == CAT_ROBOT


def test_robot_with_name_tail():
    info = describe_voice_type("robotmodelavasco")
    assert info.category == CAT_ROBOT
    assert "Vasco" in info.display_name


def test_player_voice():
    info = describe_voice_type("playervoicemale01")
    assert info.category == CAT_PLAYER
    assert info.gender == "male"


def test_player_otherplayer():
    info = describe_voice_type("npcxotherplayer")
    assert info.category == CAT_PLAYER


@pytest.mark.parametrize(
    "vt",
    [
        "testvoicetype",
        "soundeffects_donotrecord",
        "_npc_nolines",
        "humanmalenovoice",
    ],
)
def test_test_and_nonrecord(vt):
    info = describe_voice_type(vt)
    assert info.category == CAT_TEST


def test_expressions_category():
    info = describe_voice_type("humanfemaleexpressions")
    assert info.category == CAT_EXPRESSIONS
    assert info.gender == "female"


# ── Cut content + content-pack markers ───────────────────────────────────────────

def test_cut_marker():
    info = describe_voice_type("_cut_npcmoldguy")
    assert info.is_cut is True
    assert info.category == CAT_NAMED


def test_content_pack_prefix_shattered_space():
    info = describe_voice_type("sfbgs001_npcfgracekim")
    assert info.source == "Shattered Space"
    assert info.is_named is True
    assert info.gender == "female"


def test_content_pack_prefix_keeps_inner_parse():
    info = describe_voice_type("sffl_crimsonfleetmale02")
    assert info.source == "SFFL"
    assert info.faction == "Crimson Fleet"
    assert info.gender == "male"


def test_base_game_has_no_source():
    info = describe_voice_type("genericmale01")
    assert info.source == ""
    assert info.is_cut is False


# ── raw is always preserved ──────────────────────────────────────────────────────

def test_raw_preserved():
    raw = "npcfSarahMorgan"
    info = describe_voice_type(raw)
    assert info.raw == raw  # original casing kept for the "voice type:" line


def test_empty_input():
    info = describe_voice_type("")
    assert info.category == CAT_UNKNOWN
    assert info.raw == ""


# ── describe_voice_types (multi / dedup) ──────────────────────────────────────────

def test_describe_many_dedup():
    out = describe_voice_types(
        ["npcfsarahmorgan", "npcfsarahmorgan", "crimsonfleetmale01"]
    )
    assert [i.display_name for i in out] == ["Sarah Morgan", "Crimson Fleet — Male 01"]


def test_describe_many_preserves_order():
    out = describe_voice_types(["genericmale01", "genericfemale01"])
    assert out[0].gender == "male"
    assert out[1].gender == "female"


def test_describe_many_empty():
    assert describe_voice_types([]) == []
