"""Tests for player-gender-aware translation (directive + source detector).

Pure functions and direct ``TranslationRequest`` construction — no Qt, no network.
"""

import pytest

from gui.ollama_worker import (
    TranslationRequest,
    _player_gender_directive,
    get_player_gender,
    get_prompt_customizations,
    is_gendered_target,
    set_player_gender,
    set_prompt_customizations,
)
from gui.player_gender import (
    count_player_referring_texts,
    find_player_referring_rows,
    is_player_referring,
)


@pytest.fixture(autouse=True)
def _clean_prompt_state():
    """Reset module-level prompt state before and after every test."""
    set_prompt_customizations({}, "", {})
    set_player_gender("")
    yield
    set_prompt_customizations({}, "", {})
    set_player_gender("")


def _req(target_lang="uk", source_lang="en", **kw):
    return TranslationRequest(
        index=0, original_text="", string_id=0,
        source_lang=source_lang, target_lang=target_lang, **kw,
    )


# ── set/get_player_gender ────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("male", "male"),
    ("female", "female"),
    ("neutral", "neutral"),
    ("MALE", "male"),
    ("  Female  ", "female"),
    ("", ""),
    (None, ""),
    ("bogus", ""),
    ("m", ""),
])
def test_set_get_normalization(raw, expected):
    set_player_gender(raw)
    assert get_player_gender() == expected


def test_clearing_reverts_to_unset():
    set_player_gender("male")
    assert get_player_gender() == "male"
    set_player_gender("")
    assert get_player_gender() == ""


# ── directive wording ────────────────────────────────────────────────────────

def test_directive_empty_when_unset():
    assert _player_gender_directive("uk") == ""


def test_directive_empty_for_non_gendered_target():
    set_player_gender("male")
    assert _player_gender_directive("ko") == ""
    assert _player_gender_directive("en") == ""
    assert _player_gender_directive("ja") == ""
    assert _player_gender_directive("zhhans") == ""


def test_directive_male_uk():
    set_player_gender("male")
    d = _player_gender_directive("uk")
    assert "Player gender:" in d
    assert "masculine" in d
    assert "Ukrainian" in d


def test_directive_female_de():
    set_player_gender("female")
    d = _player_gender_directive("de")
    assert "feminine" in d
    assert "German" in d


def test_directive_neutral_pl():
    set_player_gender("neutral")
    d = _player_gender_directive("pl")
    assert "gender-neutral phrasing" in d
    assert "Polish" in d


# ── to_system_prompt integration ─────────────────────────────────────────────

def test_prompt_includes_directive_for_gendered_target():
    set_player_gender("female")
    prompt = _req(target_lang="uk").to_system_prompt()
    assert "Player gender:" in prompt
    assert "feminine" in prompt


def test_prompt_excludes_directive_for_non_gendered_target():
    set_player_gender("male")
    prompt = _req(target_lang="ko").to_system_prompt()
    assert "Player gender:" not in prompt


def test_prompt_excludes_directive_when_unset():
    prompt = _req(target_lang="uk").to_system_prompt()
    assert "Player gender:" not in prompt


def test_fix_mode_includes_directive():
    set_player_gender("male")
    prompt = _req(target_lang="uk", fix_translation="погана спроба").to_system_prompt()
    assert "Player gender:" in prompt
    assert "masculine" in prompt


def test_player_gender_does_not_touch_customization_tuple():
    """Setting only the player gender must not disturb the 3-tuple contract."""
    set_player_gender("male")
    assert get_prompt_customizations() == ({}, "", {})


# ── is_player_referring ──────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "You are ready.",
    "Your ship is docked.",
    "Is this yours?",
    "Know yourself.",
    "You're late.",
    "You've arrived.",
    "<Alias=Player> has arrived",
    "<Alias.ShortName=Player> waits",
    "[PLYR], report in.",
    "Welcome, [Player].",
])
def test_is_player_referring_positive(text):
    assert is_player_referring(text) is True


@pytest.mark.parametrize("text", [
    "The reactor is online.",
    "A young explorer sets out.",   # "young" must not match "you"
    "Rich flavour profile.",         # "our" must not match "your"
    "Contour mapping complete.",
    "Sarah Morgan nods.",
    "",
])
def test_is_player_referring_negative(text):
    assert is_player_referring(text) is False


# ── find_player_referring_rows ───────────────────────────────────────────────

def test_find_rows_returns_matching_indices():
    rows = [
        {"original": "The door is locked."},
        {"original": "You need a keycard."},
        {"original": "System nominal."},
        {"original": "<Alias=Player> approaches."},
    ]
    assert find_player_referring_rows(rows) == [1, 3]


def test_find_rows_handles_missing_key_and_non_dicts():
    rows = [{"translated": "х"}, None, {"original": None}, {"original": "your turn"}]
    assert find_player_referring_rows(rows) == [3]


# ── is_gendered_target (pre-batch nudge gate) ────────────────────────────────

@pytest.mark.parametrize("lang", ["uk", "pl", "de", "es", "fr", "it", "ru", "ptbr"])
def test_gendered_targets(lang):
    assert is_gendered_target(lang) is True


@pytest.mark.parametrize("lang", ["en", "ko", "ja", "zhhans", "tr", ""])
def test_non_gendered_targets(lang):
    assert is_gendered_target(lang) is False


# ── count_player_referring_texts ─────────────────────────────────────────────

def test_count_player_referring_texts():
    texts = ["You win.", "System nominal.", "Your ship.", "<Alias=Player> waits", None, ""]
    assert count_player_referring_texts(texts) == 3


def test_count_player_referring_texts_empty():
    assert count_player_referring_texts([]) == 0
    assert count_player_referring_texts(["a door", "a reactor"]) == 0
