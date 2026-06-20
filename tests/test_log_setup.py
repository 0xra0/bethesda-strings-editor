"""Tests for gui.log_setup file-handler resolution.

The key guarantee: a frozen app launched with a read-only working directory must
still start — setup_logging falls back to a writable location instead of letting
FileHandler raise before the GUI appears.
"""

import logging

from gui import log_setup


def test_make_file_handler_uses_requested_path(tmp_path):
    p = tmp_path / "translator.log"
    h = log_setup._make_file_handler(str(p))
    try:
        assert isinstance(h, logging.FileHandler)
        assert h.baseFilename == str(p)
    finally:
        if h is not None:
            h.close()


def test_make_file_handler_falls_back_when_primary_unwritable():
    # Parent dir does not exist → FileHandler raises OSError on the first
    # candidate; the helper must fall back to a writable dir, not crash.
    bad = "/nonexistent_readonly_dir_xyz/translator.log"
    h = log_setup._make_file_handler(bad)
    try:
        assert h is not None
        assert h.baseFilename != bad
    finally:
        if h is not None:
            h.close()


def test_make_file_handler_returns_none_when_all_fail(monkeypatch):
    def always_fail(*_a, **_k):
        raise OSError("read-only everywhere")

    monkeypatch.setattr(log_setup.logging, "FileHandler", always_fail)
    assert log_setup._make_file_handler("translator.log") is None


def test_setup_logging_console_only_when_no_log_file():
    root = log_setup.setup_logging(log_file="")
    try:
        assert not any(
            isinstance(h, logging.FileHandler) for h in root.handlers
        )
        assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    finally:
        # Restore a sane logging config for the rest of the suite.
        log_setup.setup_logging(log_file="")
