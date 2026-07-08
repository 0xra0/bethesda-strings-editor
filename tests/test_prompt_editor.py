"""
Tests for the translation prompt customization hooks (Translation Prompt Editor).

Pure-function tests against ``ollama_worker`` — no Qt, no QThread, no network.
They exercise the module-level customization state that
``TranslationRequest.to_system_prompt()`` reads, which every backend (Ollama,
Claude API, Claude Code CLI) shares.
"""

import pytest

from gui.ollama_worker import (
    PROMPT_DIALS,
    TranslationRequest,
    build_dials_prompt,
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
    rules, addendum, _dials = get_prompt_customizations()
    assert rules == {"uk": "RULE"}
    assert addendum == "NOTE"


def test_get_returns_copy_not_live_reference():
    set_prompt_customizations({"uk": "RULE"}, "")
    rules, _, _ = get_prompt_customizations()
    rules["uk"] = "MUTATED"
    rules["de"] = "NEW"
    # Mutating the snapshot must not affect installed state.
    assert effective_style_rule("uk") == "RULE"
    assert effective_style_rule("de") == default_style_rule("de")


def test_set_none_clears_previous_overrides():
    set_prompt_customizations({"uk": "RULE"}, "NOTE", {"formality": "formal"})
    set_prompt_customizations(None, "")
    assert get_prompt_customizations() == ({}, "", {})


def test_set_skips_non_string_and_empty_keys():
    set_prompt_customizations({"uk": 123, "": "x", "de": "OK"}, "")
    rules, _, _ = get_prompt_customizations()
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


# ── tuning dials ─────────────────────────────────────────────────────────────
def test_spec_defaults_are_valid_option_keys():
    """Every single-select dial's default must be one of its option keys."""
    for spec in PROMPT_DIALS:
        keys = {opt[0] for opt in spec["options"]}
        if spec["multi"]:
            assert spec["default"] == ""
        else:
            assert spec["default"] in keys, spec["key"]


def test_default_option_of_each_dial_has_empty_instruction():
    """The neutral choice must contribute no prompt text."""
    for spec in PROMPT_DIALS:
        if spec["multi"]:
            continue
        instr = {opt[0]: opt[2] for opt in spec["options"]}[spec["default"]]
        assert instr == "", spec["key"]


def test_build_dials_prompt_empty_when_all_default():
    assert build_dials_prompt(None) == ""
    assert build_dials_prompt({}) == ""
    # Explicit default values are treated as no-ops too.
    assert build_dials_prompt({"style": "natural", "formality": "standard"}) == ""


def test_build_dials_prompt_single_select():
    block = build_dials_prompt({"formality": "formal", "rigor": "fidelity"})
    assert block.startswith("Translation preferences:")
    assert "Lean formal" in block
    assert "Prioritize fidelity" in block
    # Two selected dials → two bullet lines.
    assert block.count("\n- ") == 2


def test_build_dials_prompt_multi_select_expression():
    block = build_dials_prompt(
        {"expression": ["translate_idioms", "adapt_jokes"]}
    )
    assert "equivalent target-language idiom" in block
    assert "the joke lands" in block
    assert block.count("\n- ") == 2


def test_build_dials_prompt_ignores_unknown_keys_and_options():
    assert build_dials_prompt({"bogus": "x", "formality": "nonexistent"}) == ""


def test_prompt_includes_dials_block():
    set_prompt_customizations(None, "", {"vocabulary": "technical"})
    p = _req().to_system_prompt()
    assert "Translation preferences:" in p
    assert "precise technical terminology" in p


def test_dials_block_precedes_addendum():
    set_prompt_customizations(None, "ZZZ ADDENDUM.", {"formality": "formal"})
    p = _req().to_system_prompt()
    assert "Translation preferences:" in p
    assert p.index("Translation preferences:") < p.index("ZZZ ADDENDUM.")
    assert p.rstrip().endswith("ZZZ ADDENDUM.")


def test_fix_mode_includes_dials_block():
    set_prompt_customizations(None, "", {"rigor": "critical"})
    p = _req(fix_translation="погано").to_system_prompt()
    assert "Translate critically" in p


def test_dials_snapshot_is_copied_not_shared():
    set_prompt_customizations(None, "", {"expression": ["adapt_jokes"]})
    _, _, dials = get_prompt_customizations()
    dials["expression"].append("localize_refs")
    dials["formality"] = "formal"
    # Mutating the snapshot must not change the installed prompt.
    p = _req().to_system_prompt()
    assert "the joke lands" in p
    assert "Localize references" not in p
    assert "Lean formal" not in p
