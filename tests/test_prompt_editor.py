"""
Tests for the translation prompt customization hooks (Translation Prompt Editor).

Pure-function tests against ``ollama_worker`` — no Qt, no QThread, no network.
They exercise the module-level customization state that
``TranslationRequest.to_system_prompt()`` reads, which every backend (Ollama,
Claude API, Claude Code CLI) shares.
"""

import pytest

from gui.ollama_worker import (
    TranslationRequest,
    default_style_rule,
    effective_style_rule,
    get_prompt_customizations,
    set_prompt_customizations,
)


@pytest.fixture(autouse=True)
def _reset_customizations():
    """Ensure a clean module state before and after every test."""
    set_prompt_customizations(None, "")
    yield
    set_prompt_customizations(None, "")


def _req(source="en", target="uk", **kw):
    return TranslationRequest(
        index=0, original_text="", string_id=0,
        source_lang=source, target_lang=target, **kw,
    )


# ── default / effective style rule ──────────────────────────────────────────
def test_default_style_rule_returns_builtin_for_known_lang():
    assert "authentic, distinctly Ukrainian" in default_style_rule("uk")
    assert "formal Standard German" in default_style_rule("de")


def test_default_style_rule_falls_back_for_unknown_lang():
    rule = default_style_rule("xx")
    assert "NASApunk" in rule and "xx" in rule


def test_effective_style_rule_prefers_override():
    set_prompt_customizations({"uk": "CUSTOM."}, "")
    assert effective_style_rule("uk") == "CUSTOM."
    # Untouched language still resolves to its default.
    assert effective_style_rule("de") == default_style_rule("de")


def test_effective_style_rule_ignores_blank_override():
    set_prompt_customizations({"uk": "   "}, "")
    assert effective_style_rule("uk") == default_style_rule("uk")


# ── set / get customizations ────────────────────────────────────────────────
def test_set_and_get_roundtrip_strips_whitespace():
    set_prompt_customizations({"uk": "  RULE  "}, "  NOTE  ")
    rules, addendum = get_prompt_customizations()
    assert rules == {"uk": "RULE"}
    assert addendum == "NOTE"


def test_get_returns_copy_not_live_reference():
    set_prompt_customizations({"uk": "RULE"}, "")
    rules, _ = get_prompt_customizations()
    rules["uk"] = "MUTATED"
    rules["de"] = "NEW"
    # Mutating the snapshot must not affect installed state.
    assert effective_style_rule("uk") == "RULE"
    assert effective_style_rule("de") == default_style_rule("de")


def test_set_none_clears_previous_overrides():
    set_prompt_customizations({"uk": "RULE"}, "NOTE")
    set_prompt_customizations(None, "")
    assert get_prompt_customizations() == ({}, "")


def test_set_skips_non_string_and_empty_keys():
    set_prompt_customizations({"uk": 123, "": "x", "de": "OK"}, "")
    rules, _ = get_prompt_customizations()
    assert rules == {"de": "OK"}


# ── to_system_prompt integration ────────────────────────────────────────────
def test_prompt_uses_default_rule_when_uncustomized():
    p = _req().to_system_prompt()
    assert "authentic, distinctly Ukrainian" in p


def test_prompt_applies_style_override():
    set_prompt_customizations({"uk": "CUSTOM UK RULE."}, "")
    p = _req().to_system_prompt()
    assert "CUSTOM UK RULE." in p
    assert "authentic, distinctly Ukrainian" not in p


def test_prompt_appends_addendum_at_end():
    set_prompt_customizations(None, "PROJECT NOTE.")
    p = _req().to_system_prompt()
    assert p.rstrip().endswith("PROJECT NOTE.")


def test_addendum_does_not_appear_without_customization():
    p = _req().to_system_prompt()
    assert "PROJECT NOTE." not in p


def test_token_preservation_rules_survive_customization():
    """Overriding Rule 1 must never drop the formatting-token rules (2-7)."""
    set_prompt_customizations({"uk": "SHORT."}, "EXTRA.")
    p = _req().to_system_prompt()
    for token in ("[[STRUCT_BREAK_SGL_N]]", "<Alias=", "%1$s", "Square brackets"):
        assert token in p, token


def test_override_only_affects_matching_target_language():
    set_prompt_customizations({"uk": "UK ONLY."}, "")
    de = _req(target="de").to_system_prompt()
    assert "UK ONLY." not in de
    assert "formal Standard German" in de


# ── fix-mode (proofreader) path ─────────────────────────────────────────────
def test_fix_mode_honours_override_and_addendum():
    set_prompt_customizations({"uk": "FIX RULE."}, "FIX NOTE.")
    p = _req(fix_translation="погано").to_system_prompt()
    assert "proofreader" in p
    assert "FIX RULE." in p
    assert p.rstrip().endswith("FIX NOTE.")
