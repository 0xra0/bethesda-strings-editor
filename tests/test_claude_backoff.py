"""
Tests for ClaudeClient's 429/5xx exponential-backoff retry.

Hermetic: the client is built via __new__ (so the real anthropic SDK is never
imported) and `_client` is swapped for a fake whose create() plays a scripted
sequence of errors/results. time.sleep is monkeypatched to a no-op.
"""

import pytest

import gui.claude_client as cc
from gui.claude_client import ClaudeClient


class _StatusError(Exception):
    """Duck-types an anthropic APIStatusError (has .status_code)."""

    def __init__(self, status_code, retry_after=None):
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        if retry_after is not None:
            self.response = type("R", (), {"headers": {"retry-after": retry_after}})()


class RateLimitError(Exception):
    """Name-matched retriable error (no status_code attribute)."""


class _FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class _FakeClient:
    def __init__(self, script):
        self.messages = _FakeMessages(script)
        self.beta = self  # so beta.messages.create resolves to the same script


def _client(script):
    c = ClaudeClient.__new__(ClaudeClient)
    c._client = _FakeClient(script)
    c.model = "test-model"
    return c


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(cc.time, "sleep", lambda s: slept.append(s))
    return slept


# ── retriable classification ─────────────────────────────────────────────────
def test_is_retriable_by_status():
    assert cc._is_retriable_error(_StatusError(429))
    assert cc._is_retriable_error(_StatusError(503))
    assert not cc._is_retriable_error(_StatusError(400))
    assert not cc._is_retriable_error(_StatusError(401))


def test_is_retriable_by_type_name():
    assert cc._is_retriable_error(RateLimitError())
    assert not cc._is_retriable_error(ValueError("nope"))


def test_retry_after_parsing():
    assert cc._retry_after_seconds(_StatusError(429, retry_after="2.5")) == 2.5
    assert cc._retry_after_seconds(_StatusError(429)) is None
    assert cc._retry_after_seconds(ValueError()) is None


# ── _create_message retry loop ───────────────────────────────────────────────
def test_retries_then_succeeds():
    c = _client([_StatusError(429), _StatusError(503), "OK"])
    assert c._create_message(model="m", messages=[]) == "OK"
    assert c._client.messages.calls == 3


def test_non_retriable_raises_immediately():
    c = _client([ValueError("bad request")])
    with pytest.raises(ValueError):
        c._create_message(model="m", messages=[])
    assert c._client.messages.calls == 1


def test_gives_up_after_max_retries():
    c = _client([_StatusError(429)] * (cc._MAX_RETRIES + 3))
    with pytest.raises(_StatusError):
        c._create_message(model="m", messages=[])
    assert c._client.messages.calls == cc._MAX_RETRIES


def test_honours_retry_after_header(_no_sleep):
    c = _client([_StatusError(429, retry_after="7"), "OK"])
    assert c._create_message(model="m", messages=[]) == "OK"
    # The single sleep used the server-provided Retry-After value.
    assert _no_sleep == [7.0]


def test_beta_path_also_retries():
    c = _client([RateLimitError(), "OK"])
    assert c._create_message(beta=True, model="m", messages=[]) == "OK"
    assert c._client.messages.calls == 2
