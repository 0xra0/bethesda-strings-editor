"""
Tests for OllamaWorker's wedged-backend circuit breaker.

A read timeout means Ollama returned *no bytes* for the entire first-token
budget — the backend has wedged, not merely slowed.  When several strings in a
row time out with no successful translation between them, the breaker aborts the
whole batch (set _backend_wedged + _stop_flag, close in-flight sockets) instead
of letting every remaining string burn its full timeout.  This is what actually
helps the reported failure: mamaylm batches that froze with zero output for ~50
minutes at the tail of a run.

Run with:
    python -m pytest tests/test_ollama_wedge_breaker.py -v
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtCore import QMutex  # noqa: E402
from gui.ollama_worker import OllamaWorker  # noqa: E402


class _FakeResp:
    """Minimal streaming response that records whether it was closed."""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _bare_worker(threshold):
    """An OllamaWorker with only the attributes the breaker touches."""
    w = OllamaWorker.__new__(OllamaWorker)
    w._mutex = QMutex()
    w._stop_flag = False
    w._consecutive_timeouts = 0
    w._wedge_threshold = threshold
    w._backend_wedged = False
    w._executor = None
    w._active_responses = {}
    w._responses_lock = threading.Lock()
    return w


def test_breaker_trips_at_threshold():
    """The threshold-th consecutive timeout flips both wedged and stop flags."""
    w = _bare_worker(threshold=3)

    w._note_timeout()
    assert not w._backend_wedged and not w._stop_flag  # 1
    w._note_timeout()
    assert not w._backend_wedged and not w._stop_flag  # 2
    w._note_timeout()
    assert w._backend_wedged and w._stop_flag           # 3 → trip


def test_breaker_does_not_trip_below_threshold():
    """Fewer timeouts than the threshold must not abort the batch."""
    w = _bare_worker(threshold=5)
    for _ in range(4):
        w._note_timeout()
    assert not w._backend_wedged
    assert not w._stop_flag


def test_reset_counter_requires_full_run_again():
    """Clearing the counter (as a success does) means the breaker needs a fresh
    full run of timeouts before it trips."""
    w = _bare_worker(threshold=3)
    w._note_timeout()
    w._note_timeout()           # 2 — almost there
    w._consecutive_timeouts = 0  # a successful translation resets the run
    w._note_timeout()
    w._note_timeout()
    assert not w._backend_wedged  # only 2 since the reset
    w._note_timeout()
    assert w._backend_wedged      # now 3 in a row → trip


def test_trip_closes_inflight_responses():
    """Tripping the breaker closes every registered in-flight response so the
    batch loop unblocks promptly (same mechanism stop() uses)."""
    w = _bare_worker(threshold=1)
    r1, r2 = _FakeResp(), _FakeResp()
    w._active_responses = {1: r1, 2: r2}

    w._note_timeout()  # threshold=1 → trips immediately

    assert w._backend_wedged
    assert r1.closed and r2.closed
    assert w._active_responses == {}


def test_zero_threshold_never_trips():
    """Before a batch sets the threshold (0), timeouts must never trip."""
    w = _bare_worker(threshold=0)
    for _ in range(20):
        w._note_timeout()
    assert not w._backend_wedged
    assert not w._stop_flag
