"""Tests for the Claude Messages-API MCP connector wiring.

Two surfaces are covered, both pure (no network, no `anthropic` package):

* ``ClaudeClient._mcp_request_kwargs`` / ``chat_mcp`` — the request-shaping and
  the server-side pause_turn loop.  The client is built via ``__new__`` with a
  fake ``_client`` so the real Anthropic SDK is never imported.
* ``AppSettings`` MCP-server persistence — per-entry auth-token obfuscation and
  the migration that seeds the new fields.
"""

import pytest

from gui.app_settings import AppSettings
from gui.claude_client import ClaudeClient, _MCP_BETA


# ── Fake Anthropic client ─────────────────────────────────────────────────────

class _Block:
    def __init__(self, type: str, text: str = "", name: str = "") -> None:
        self.type = type
        self.text = text
        self.name = name


class _Resp:
    def __init__(self, content, stop_reason: str = "end_turn") -> None:
        self.content = content
        self.stop_reason = stop_reason


class _Messages:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _Beta:
    def __init__(self, messages: _Messages) -> None:
        self.messages = messages


class _FakeClient:
    def __init__(self, plain=None, beta=None) -> None:
        self.messages = _Messages(plain or [])
        self.beta = _Beta(_Messages(beta or []))


def _client(mcp_servers=None, plain=None, beta=None, model="claude-haiku-4-5"):
    c = ClaudeClient.__new__(ClaudeClient)  # skip __init__ → no anthropic import
    c.model = model
    c.mcp_servers = list(mcp_servers or [])
    c._client = _FakeClient(plain, beta)
    return c


# ── _mcp_request_kwargs ───────────────────────────────────────────────────────

def test_no_servers_returns_empty_kwargs():
    assert _client()._mcp_request_kwargs() == {}


def test_request_kwargs_shape_pairs_server_with_toolset():
    kw = _client(
        [{"name": "gloss", "url": "https://x/mcp", "authorization_token": "tok"}]
    )._mcp_request_kwargs()
    assert kw["betas"] == [_MCP_BETA]
    assert kw["mcp_servers"] == [
        {"type": "url", "url": "https://x/mcp", "name": "gloss",
         "authorization_token": "tok"},
    ]
    assert kw["tools"] == [{"type": "mcp_toolset", "mcp_server_name": "gloss"}]


def test_token_is_optional():
    kw = _client([{"name": "g", "url": "https://x/mcp"}])._mcp_request_kwargs()
    assert "authorization_token" not in kw["mcp_servers"][0]


def test_incomplete_rows_are_skipped():
    kw = _client([
        {"name": "", "url": "https://x/mcp"},   # no name
        {"name": "g", "url": ""},               # no url
        {"name": "ok", "url": "https://x/mcp"},
    ])._mcp_request_kwargs()
    assert [s["name"] for s in kw["mcp_servers"]] == ["ok"]
    assert kw["tools"] == [{"type": "mcp_toolset", "mcp_server_name": "ok"}]


def test_all_incomplete_returns_empty():
    kw = _client([{"name": "", "url": ""}, {"name": "x"}])._mcp_request_kwargs()
    assert kw == {}


# ── chat_mcp ──────────────────────────────────────────────────────────────────

def test_chat_mcp_falls_back_to_plain_chat_without_servers():
    c = _client([], plain=[_Resp([_Block("text", "hi")])])
    assert c.chat_mcp([{"role": "user", "content": "q"}]) == "hi"
    # The beta (MCP) endpoint must not be touched when MCP is unconfigured.
    assert c._client.beta.messages.calls == []


def test_chat_mcp_collects_text_and_reports_tools():
    c = _client(
        [{"name": "g", "url": "https://x/mcp"}],
        beta=[_Resp([_Block("mcp_tool_use", name="lookup"),
                     _Block("text", "answer")])],
    )
    tools = []
    out = c.chat_mcp([{"role": "user", "content": "q"}], on_tool=tools.append)
    assert out == "answer"
    assert tools == ["lookup"]
    call = c._client.beta.messages.calls[0]
    assert call["betas"] == [_MCP_BETA]
    assert call["tools"] == [{"type": "mcp_toolset", "mcp_server_name": "g"}]
    assert not c._client.messages.calls  # plain endpoint unused


def test_chat_mcp_resumes_on_pause_turn():
    c = _client(
        [{"name": "g", "url": "https://x/mcp"}],
        beta=[
            _Resp([_Block("text", "part1")], stop_reason="pause_turn"),
            _Resp([_Block("text", "part2")], stop_reason="end_turn"),
        ],
    )
    out = c.chat_mcp([{"role": "user", "content": "q"}])
    assert out == "part1part2"
    calls = c._client.beta.messages.calls
    assert len(calls) == 2
    # The paused assistant turn is echoed back to continue the tool loop.
    assert calls[1]["messages"][-1]["role"] == "assistant"


def test_chat_mcp_passes_system_prompt_as_cached_block():
    c = _client(
        [{"name": "g", "url": "https://x/mcp"}],
        beta=[_Resp([_Block("text", "ok")])],
    )
    c.chat_mcp([{"role": "user", "content": "q"}], system="be terse")
    sys_block = c._client.beta.messages.calls[0]["system"][0]
    assert sys_block["text"] == "be terse"
    assert sys_block["cache_control"] == {"type": "ephemeral"}


# ── AppSettings persistence ───────────────────────────────────────────────────

def test_mcp_token_obfuscated_on_disk_and_restored():
    s = AppSettings.defaults()
    s.enable_mcp = True
    s.mcp_servers = [
        {"name": "g", "url": "https://x/mcp", "authorization_token": "secret-tok"},
    ]
    d = s.to_dict()
    entry = d["mcp_servers"][0]
    assert entry["name"] == "g" and entry["url"] == "https://x/mcp"
    assert entry["authorization_token"].startswith("enc:")
    assert "secret-tok" not in entry["authorization_token"]
    # In-memory value must stay plaintext (only the serialized copy is wrapped).
    assert s.mcp_servers[0]["authorization_token"] == "secret-tok"

    restored = AppSettings.from_dict(d)
    assert restored.enable_mcp is True
    assert restored.mcp_servers[0]["authorization_token"] == "secret-tok"


def test_mcp_legacy_plaintext_token_still_read():
    d = AppSettings.defaults().to_dict()
    d["config_version"] = AppSettings.defaults().config_version
    d["mcp_servers"] = [{"name": "g", "url": "u", "authorization_token": "plainkey"}]
    restored = AppSettings.from_dict(d)
    assert restored.mcp_servers[0]["authorization_token"] == "plainkey"


def test_mcp_entry_without_token_roundtrips_unchanged():
    s = AppSettings.defaults()
    s.mcp_servers = [{"name": "g", "url": "u"}]
    restored = AppSettings.from_dict(s.to_dict())
    assert restored.mcp_servers == [{"name": "g", "url": "u"}]


def test_migration_seeds_mcp_defaults():
    restored = AppSettings.from_dict({"config_version": 37})
    assert restored.enable_mcp is False
    assert restored.mcp_servers == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
