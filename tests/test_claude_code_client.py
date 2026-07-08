"""Tests for the Claude Code CLI backend (:mod:`gui.claude_code_client`).

All pure / hermetic — the real ``claude`` binary is never spawned:

* routing / alias mapping / argv shaping / JSON-result parsing / message
  flattening are pure functions;
* ``_run`` / ``translate`` / ``chat`` / ``review_translation`` are exercised by
  monkeypatching ``find_claude_cli`` and the client's ``_invoke`` with a fake
  ``CompletedProcess`` — no subprocess, no network, no Claude Code login.

The key subscription guarantee (``ANTHROPIC_API_KEY`` stripped from the child
environment so the CLI can't silently fall back to API billing) is verified by
capturing the env passed to ``subprocess.run``.
"""

import subprocess
import types

import pytest

from gui.claude_code_client import (
    ClaudeCodeClient,
    ClaudeCodeError,
    CLAUDE_CODE_MODELS,
    DEFAULT_MODEL,
    cli_alias_for,
    find_claude_cli,
    is_claude_code_model,
)


def _proc(returncode=0, stdout="", stderr=""):
    """A stand-in for subprocess.CompletedProcess."""
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _ok_json(text, usage=None, cost=None):
    import json
    obj = {"type": "result", "subtype": "success", "is_error": False, "result": text}
    if usage is not None:
        obj["usage"] = usage
    if cost is not None:
        obj["total_cost_usd"] = cost
    return json.dumps(obj)


# A realistic Anthropic-style usage block as the CLI emits it.
_USAGE = {
    "input_tokens": 9,
    "cache_creation_input_tokens": 2629,
    "cache_read_input_tokens": 9139,
    "output_tokens": 420,
}


# ── Routing / registry ─────────────────────────────────────────────────────────

def test_is_claude_code_model():
    assert is_claude_code_model("claude-code")
    assert is_claude_code_model("claude-code:haiku")
    assert is_claude_code_model("claude-code:sonnet")
    assert not is_claude_code_model("claude-haiku-4-5")   # metered API model
    assert not is_claude_code_model("claude-opus-4-8")
    assert not is_claude_code_model("translategemma3-st")
    assert not is_claude_code_model("")


def test_default_model_is_registered():
    assert DEFAULT_MODEL in CLAUDE_CODE_MODELS


def test_cli_alias_for():
    assert cli_alias_for("claude-code:haiku") == "haiku"
    assert cli_alias_for("claude-code:sonnet") == "sonnet"
    assert cli_alias_for("claude-code:opus") == "opus"
    assert cli_alias_for("claude-code") == "sonnet"      # bare id → default tier
    assert cli_alias_for("nonsense") == "sonnet"          # safe fallback


# ── argv shaping ────────────────────────────────────────────────────────────────

def test_build_argv_shape():
    c = ClaudeCodeClient(model="claude-code:opus")
    argv = c._build_argv("/usr/bin/claude", "SYSTEM")
    assert argv[0] == "/usr/bin/claude"
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--system-prompt") + 1] == "SYSTEM"
    # No MCP servers loaded from global config.
    assert "--strict-mcp-config" in argv


def test_build_argv_omits_empty_system():
    c = ClaudeCodeClient(model="claude-code:haiku")
    assert "--system-prompt" not in c._build_argv("/usr/bin/claude", "")


# ── Result parsing ──────────────────────────────────────────────────────────────

def test_parse_result_success_strips():
    assert ClaudeCodeClient._parse_result(_ok_json("  hi there \n")) == "hi there"


def test_parse_result_error_flag_raises():
    with pytest.raises(ClaudeCodeError) as ei:
        ClaudeCodeClient._parse_result('{"is_error": true, "result": "boom"}')
    assert "boom" in str(ei.value)


def test_parse_result_non_json_raises_with_text():
    with pytest.raises(ClaudeCodeError) as ei:
        ClaudeCodeClient._parse_result("fatal: not logged in")
    assert "not logged in" in str(ei.value)


def test_parse_result_empty_raises():
    with pytest.raises(ClaudeCodeError):
        ClaudeCodeClient._parse_result("   ")


def test_parse_result_unexpected_shape_raises():
    with pytest.raises(ClaudeCodeError):
        ClaudeCodeClient._parse_result('{"is_error": false, "result": 42}')


# ── Message flattening ──────────────────────────────────────────────────────────

def test_flatten_single_user_turn_is_verbatim():
    assert ClaudeCodeClient._flatten_messages(
        [{"role": "user", "content": "just this"}]
    ) == "just this"


def test_flatten_multi_turn_labels_roles():
    out = ClaudeCodeClient._flatten_messages([
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ])
    assert out == "User: a\n\nAssistant: b\n\nUser: c"


def test_flatten_content_blocks():
    out = ClaudeCodeClient._flatten_messages([
        {"role": "user", "content": [
            {"type": "text", "text": "x"},
            {"type": "text", "text": "y"},
        ]},
    ])
    assert out == "x y"


# ── CLI discovery ───────────────────────────────────────────────────────────────

def test_find_cli_honours_override(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("CLAUDE_CLI_PATH", str(fake))
    assert find_claude_cli(refresh=True) == str(fake)


# ── _run round-trip (mocked subprocess) ─────────────────────────────────────────

def _client_with_invoke(monkeypatch, proc=None, exc=None, capture=None):
    """Build a client whose _invoke returns *proc* / raises *exc* and records args."""
    monkeypatch.setattr("gui.claude_code_client.find_claude_cli",
                        lambda *a, **k: "/usr/bin/claude")
    c = ClaudeCodeClient(model="claude-code:sonnet")

    def fake_invoke(argv, prompt):
        if capture is not None:
            capture["argv"] = argv
            capture["prompt"] = prompt
        if exc is not None:
            raise exc
        return proc
    monkeypatch.setattr(c, "_invoke", fake_invoke)
    return c


def test_run_success(monkeypatch):
    cap = {}
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("Привіт")), capture=cap)
    assert c._run("SYS", "Hello") == "Привіт"
    assert cap["prompt"] == "Hello"           # user prompt goes on stdin
    assert "SYS" in cap["argv"]               # system prompt in argv


def test_run_missing_cli_raises(monkeypatch):
    monkeypatch.setattr("gui.claude_code_client.find_claude_cli", lambda *a, **k: None)
    c = ClaudeCodeClient(model="claude-code:sonnet")
    with pytest.raises(ClaudeCodeError) as ei:
        c._run("SYS", "Hello")
    assert "claude" in str(ei.value).lower()


def test_run_timeout_raises(monkeypatch):
    c = _client_with_invoke(
        monkeypatch, exc=subprocess.TimeoutExpired(cmd="claude", timeout=1)
    )
    with pytest.raises(ClaudeCodeError) as ei:
        c._run("SYS", "Hello")
    assert "timed out" in str(ei.value).lower()


def test_run_nonzero_prefers_stderr(monkeypatch):
    c = _client_with_invoke(
        monkeypatch, proc=_proc(1, "some noise", "Error: not authenticated")
    )
    with pytest.raises(ClaudeCodeError) as ei:
        c._run("SYS", "Hello")
    assert "not authenticated" in str(ei.value)


def test_run_nonzero_but_valid_result_returned(monkeypatch):
    # Defensive: if the CLI exits nonzero yet still printed a success result,
    # prefer the parsed text over the exit code.
    c = _client_with_invoke(monkeypatch, proc=_proc(1, _ok_json("ok text"), ""))
    assert c._run("SYS", "Hello") == "ok text"


# ── Public surface (translate / chat / review) ──────────────────────────────────

def test_chat_flattens_and_returns(monkeypatch):
    cap = {}
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("reply")), capture=cap)
    out = c.chat([{"role": "user", "content": "question"}], system="SYS")
    assert out == "reply"
    assert cap["prompt"] == "question"


def test_chat_stream_yields_single_chunk(monkeypatch):
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("streamed")))
    chunks = list(c.chat_stream([{"role": "user", "content": "q"}], system="SYS"))
    assert chunks == ["streamed"]


def test_chat_mcp_falls_back_to_chat(monkeypatch):
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("no-mcp reply")))
    assert c.chat_mcp([{"role": "user", "content": "q"}], system="SYS") == "no-mcp reply"


def test_translate_uses_translation_prompts(monkeypatch):
    cap = {}
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("Привіт")), capture=cap)
    out = c.translate("Hello", source_lang="en", target_lang="uk")
    assert out == "Привіт"
    # The source text reaches the CLI on stdin (from TranslationRequest.to_prompt()).
    assert "Hello" in cap["prompt"]
    # A non-empty system prompt (the translator instructions) is passed.
    assert "--system-prompt" in cap["argv"]


def test_review_translation_returns_text(monkeypatch):
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("Rating: Good")))
    out = c.review_translation("Hello", "Привіт", source_lang="en", target_lang="uk")
    assert "Good" in out


# ── Subscription guarantee: never leak API-key env into the child ───────────────

# ── Usage accounting ────────────────────────────────────────────────────────────

def test_usage_starts_zero():
    c = ClaudeCodeClient(model="claude-code:sonnet")
    assert c.get_usage() == {"input_tokens": 0, "output_tokens": 0,
                             "cost_usd": 0.0, "calls": 0}


def test_usage_accumulates_across_calls(monkeypatch):
    c = _client_with_invoke(
        monkeypatch, proc=_proc(0, _ok_json("hi", usage=_USAGE, cost=0.0083))
    )
    c._run("SYS", "one")
    c._run("SYS", "two")
    u = c.get_usage()
    # input = input_tokens + cache_read + cache_creation, summed over 2 calls
    assert u["input_tokens"] == (9 + 9139 + 2629) * 2
    assert u["output_tokens"] == 420 * 2
    assert u["calls"] == 2
    assert abs(u["cost_usd"] - 0.0166) < 1e-6


def test_usage_reset(monkeypatch):
    c = _client_with_invoke(
        monkeypatch, proc=_proc(0, _ok_json("hi", usage=_USAGE, cost=0.01))
    )
    c._run("SYS", "one")
    assert c.get_usage()["calls"] == 1
    c.reset_usage()
    assert c.get_usage() == {"input_tokens": 0, "output_tokens": 0,
                             "cost_usd": 0.0, "calls": 0}


def test_usage_counts_call_even_without_usage_block(monkeypatch):
    # A valid result with no usage/cost still counts as one call (0 tokens).
    c = _client_with_invoke(monkeypatch, proc=_proc(0, _ok_json("hi")))
    c._run("SYS", "one")
    u = c.get_usage()
    assert u["calls"] == 1
    assert u["input_tokens"] == 0 and u["output_tokens"] == 0
    assert u["cost_usd"] == 0.0


def test_get_usage_returns_a_copy():
    c = ClaudeCodeClient(model="claude-code:sonnet")
    snap = c.get_usage()
    snap["input_tokens"] = 999
    assert c.get_usage()["input_tokens"] == 0   # internal state untouched


def test_failed_call_records_no_usage(monkeypatch):
    # A hard error (nonzero + non-JSON) must not touch usage totals.
    c = _client_with_invoke(monkeypatch, proc=_proc(1, "boom", "stderr boom"))
    with pytest.raises(ClaudeCodeError):
        c._run("SYS", "one")
    assert c.get_usage()["calls"] == 0


def test_invoke_strips_api_key_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-should-not-leak")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["input"] = kwargs.get("input")
        return _proc(0, _ok_json("ok"))

    monkeypatch.setattr("gui.claude_code_client.subprocess.run", fake_run)
    c = ClaudeCodeClient(model="claude-code:sonnet")
    c._invoke(["/usr/bin/claude", "-p"], "the prompt")

    assert "ANTHROPIC_API_KEY" not in captured["env"]
    assert "ANTHROPIC_AUTH_TOKEN" not in captured["env"]
    assert captured["input"] == "the prompt"
