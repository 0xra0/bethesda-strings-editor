"""Tests for gui.ollama_control — the Ollama force-stop / restart helper.

Pure-function coverage (no Qt, no real Ollama).  Commands are exercised with
harmless shell builtins (true / false / sleep / printf) so the tests run anywhere
a POSIX /bin/sh exists.
"""

import sys

import pytest

from gui import ollama_control


# ── detect_restart_command ────────────────────────────────────────────────


def test_detect_prefers_sv(monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "sv")
    assert ollama_control.detect_restart_command() == "sv restart ollama"


def test_detect_falls_back_to_systemctl(monkeypatch):
    # No sv, but systemctl present.
    monkeypatch.setattr(
        ollama_control.shutil, "which", lambda b: b == "systemctl"
    )
    assert ollama_control.detect_restart_command() == "systemctl restart ollama"


def test_detect_falls_back_to_pkill(monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "pkill")
    assert ollama_control.detect_restart_command() == "pkill -x ollama"


def test_detect_returns_empty_when_nothing_found(monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: None)
    assert ollama_control.detect_restart_command() == ""


def test_detect_order_sv_beats_systemctl(monkeypatch):
    # Both present → sv wins (matches the user's runit box).
    monkeypatch.setattr(
        ollama_control.shutil, "which", lambda b: b in ("sv", "systemctl")
    )
    assert ollama_control.detect_restart_command() == "sv restart ollama"


# ── build_restart_argv ────────────────────────────────────────────────────


def test_build_argv_wraps_in_shell():
    assert ollama_control.build_restart_argv("sudo sv restart ollama") == [
        "/bin/sh",
        "-c",
        "sudo sv restart ollama",
    ]


# ── restart_ollama ────────────────────────────────────────────────────────


def test_restart_empty_command_is_rejected():
    ok, msg = ollama_control.restart_ollama("")
    assert ok is False
    assert "No restart command" in msg


def test_restart_empty_after_strip_is_rejected():
    ok, _msg = ollama_control.restart_ollama("   ")
    assert ok is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell required")
def test_restart_success_zero_exit():
    ok, msg = ollama_control.restart_ollama("printf done; true")
    assert ok is True
    assert "done" in msg


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell required")
def test_restart_nonzero_exit_reports_failure():
    ok, msg = ollama_control.restart_ollama("printf nope; false")
    assert ok is False
    assert "nope" in msg


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell required")
def test_restart_timeout_is_killed():
    ok, msg = ollama_control.restart_ollama("sleep 5", timeout=0.5)
    assert ok is False
    assert "timed out" in msg.lower()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell required")
def test_restart_sudo_password_hint():
    # Simulate sudo's no-tty message; the helper should add a NOPASSWD hint.
    ok, msg = ollama_control.restart_ollama(
        "printf 'sudo: a terminal is required to read the password'; false"
    )
    assert ok is False
    assert "NOPASSWD" in msg
