"""
Tests for OllamaWorker._stream_ollama's inter-token stall watchdog.

The request-level read timeout has to be large enough to absorb Ollama's GPU
queue wait before the first token, which makes it useless for catching a
generation that freezes mid-stream.  A separate watchdog enforces a tighter
gap *between* streamed tokens once generation has started:

  * steady-but-slow generation (gaps < stall_timeout) completes normally;
  * a genuine freeze (gap > stall_timeout) is aborted quickly and surfaced as
    a ReadTimeout, instead of burning the whole first-token budget.

Run with:
    python -m pytest tests/test_ollama_stall_watchdog.py -v
"""

import sys
import threading
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QMutex  # noqa: E402
from gui.ollama_worker import OllamaWorker  # noqa: E402


# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeResp:
    """Streaming response that reproduces a given inter-token cadence.

    ``gaps[i]`` seconds elapse before ``lines[i]`` is yielded.  close() behaves
    like a real socket closed from another thread: it unblocks the iterator and
    makes the next read raise OSError.
    """

    def __init__(self, gaps, lines):
        self.status_code = 200
        self._gaps = gaps
        self._lines = lines
        self._closed = threading.Event()

    def raise_for_status(self):
        pass

    def iter_lines(self):
        for gap, line in zip(self._gaps, self._lines):
            if self._closed.wait(gap):
                raise OSError("socket closed by watchdog")
            yield line

    def close(self):
        self._closed.set()


class _FakeSession:
    def __init__(self, resp):
        self._resp = resp
        self.timeout = 300

    def post(self, *_args, **_kwargs):
        return self._resp


def _bare_worker():
    """An OllamaWorker with only the attributes _stream_ollama touches.

    Bypasses __init__ so the test needs neither a running Ollama nor the full
    Qt model/signal wiring.
    """
    w = OllamaWorker.__new__(OllamaWorker)
    w._stop_flag = False
    w._mutex = QMutex()
    w._active_responses = {}
    w._responses_lock = threading.Lock()
    w.base_url = "http://localhost:11434"
    return w


def _run(worker, gaps, lines, stall_timeout, request_timeout=600):
    worker._session = _FakeSession(_FakeResp(gaps, lines))
    return worker._stream_ollama(
        {"model": "x", "prompt": "y"}, request_timeout, stall_timeout=stall_timeout
    )


# ── tests ───────────────────────────────────────────────────────────────────


def test_steady_slow_stream_completes():
    """Gaps below stall_timeout must not trip the watchdog."""
    out = _run(
        _bare_worker(),
        gaps=[1, 1, 1, 1],
        lines=[
            b'{"response":"a"}',
            b'{"response":"b"}',
            b'{"response":"c"}',
            b'{"response":"","done":true}',
        ],
        stall_timeout=3,
    )
    assert out == "abc"


def test_midstream_freeze_trips_watchdog():
    """A gap longer than stall_timeout is aborted as a ReadTimeout, fast."""
    worker = _bare_worker()
    t0 = time.monotonic()
    with pytest.raises(requests.exceptions.ReadTimeout):
        _run(
            worker,
            gaps=[1, 30],  # 30s freeze after the first token; stall_timeout=3
            lines=[b'{"response":"a"}', b'{"response":"b"}'],
            stall_timeout=3,
            request_timeout=600,
        )
    elapsed = time.monotonic() - t0
    # Aborted on the stall (~3s + poll), not on the 600s request budget.
    assert elapsed < 20, f"watchdog took too long: {elapsed:.1f}s"


def test_stall_timeout_capped_to_request_timeout():
    """stall_timeout never exceeds the overall request budget."""
    # request_timeout (2s) < stall_timeout (60s): the effective stall bound is 2s,
    # so a 30s freeze still aborts promptly rather than waiting 60s.
    worker = _bare_worker()
    t0 = time.monotonic()
    with pytest.raises(requests.exceptions.ReadTimeout):
        _run(
            worker,
            gaps=[0.5, 30],
            lines=[b'{"response":"a"}', b'{"response":"b"}'],
            stall_timeout=60,
            request_timeout=2,
        )
    assert time.monotonic() - t0 < 20


def test_stop_flag_returns_none_not_timeout():
    """When stop() set the flag, a closed socket yields None, not a timeout."""
    worker = _bare_worker()

    resp = _FakeResp(gaps=[0.2, 30], lines=[b'{"response":"a"}', b'{"response":"b"}'])
    worker._session = _FakeSession(resp)

    def _stopper():
        time.sleep(0.6)
        # Mimic stop(): set the flag first, then close the live socket.
        worker._stop_flag = True
        resp.close()

    t = threading.Thread(target=_stopper)
    t.start()
    out = worker._stream_ollama(
        {"model": "x", "prompt": "y"}, 600, stall_timeout=120
    )
    t.join()
    assert out is None
