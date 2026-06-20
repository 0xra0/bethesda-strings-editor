"""Tests for gui.ollama_control — the Ollama force-stop / restart helper.

Pure-function coverage (no Qt, no real Ollama).  Commands are exercised with
harmless shell builtins (true / false / sleep / printf) so the tests run anywhere
a POSIX /bin/sh exists.
"""

import sys

import pytest

from gui import ollama_control


@pytest.fixture
def linux(monkeypatch):
    """Pin platform to Linux so detection/elevation tests are host-independent."""
    monkeypatch.setattr(ollama_control.sys, "platform", "linux")


# ── detect_restart_command ────────────────────────────────────────────────


def test_detect_prefers_sv(linux, monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "sv")
    assert ollama_control.detect_restart_command() == "sv restart ollama"


def test_detect_falls_back_to_systemctl(linux, monkeypatch):
    # No sv, but systemctl present.
    monkeypatch.setattr(
        ollama_control.shutil, "which", lambda b: b == "systemctl"
    )
    assert ollama_control.detect_restart_command() == "systemctl restart ollama"


def test_detect_falls_back_to_pkill(linux, monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "pkill")
    assert ollama_control.detect_restart_command() == "pkill -x ollama"


def test_detect_returns_empty_when_nothing_found(linux, monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: None)
    assert ollama_control.detect_restart_command() == ""


def test_detect_order_sv_beats_systemctl(linux, monkeypatch):
    # Both present → sv wins (matches the user's runit box).
    monkeypatch.setattr(
        ollama_control.shutil, "which", lambda b: b in ("sv", "systemctl")
    )
    assert ollama_control.detect_restart_command() == "sv restart ollama"


def test_detect_windows_uses_taskkill(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "platform", "win32")
    assert ollama_control.detect_restart_command() == "taskkill /F /T /IM ollama.exe"


def test_detect_macos_prefers_brew(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "platform", "darwin")
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "brew")
    assert ollama_control.detect_restart_command() == "brew services restart ollama"


def test_detect_kill_command_is_platform_specific(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "platform", "win32")
    assert ollama_control.detect_kill_command() == "taskkill /F /T /IM ollama.exe"
    monkeypatch.setattr(ollama_control.sys, "platform", "linux")
    assert ollama_control.detect_kill_command() == "pkill -x ollama"


def test_command_needs_root():
    assert ollama_control.command_needs_root("systemctl restart ollama")
    assert ollama_control.command_needs_root("sudo sv restart ollama")
    assert not ollama_control.command_needs_root("pkill -x ollama")
    assert not ollama_control.command_needs_root("taskkill /F /T /IM ollama.exe")


# ── is_already_stopped (benign "nothing was running") ──────────────────────


def test_taskkill_not_found_is_benign():
    # Windows taskkill exits 128 with "process not found" when Ollama is absent.
    out = 'ERROR: The process "ollama.exe" not found.'
    assert ollama_control.is_already_stopped(
        "taskkill /F /T /IM ollama.exe", 128, out
    )


def test_taskkill_not_found_by_text_any_code():
    assert ollama_control.is_already_stopped(
        "taskkill /F /IM ollama.exe", 1, "process not found"
    )


def test_taskkill_real_failure_is_not_benign():
    # Access denied (service-owned Ollama) is a genuine failure, not "not running".
    assert not ollama_control.is_already_stopped(
        "taskkill /F /T /IM ollama.exe", 1, "ERROR: Access is denied."
    )


def test_pkill_no_match_is_benign():
    # pkill exits 1 when no process matched — Ollama already stopped.
    assert ollama_control.is_already_stopped("pkill -x ollama", 1, "")


def test_pkill_syntax_error_is_not_benign():
    assert not ollama_control.is_already_stopped("pkill -x ollama", 2, "usage: pkill")


def test_service_restart_failure_is_not_benign():
    # systemctl/sv failures are real — never swallowed.
    assert not ollama_control.is_already_stopped(
        "systemctl restart ollama", 1, "Failed to restart ollama.service"
    )
    assert not ollama_control.is_already_stopped("sv restart ollama", 1, "fail: ollama")


# ── build_restart_argv ────────────────────────────────────────────────────


def test_build_argv_wraps_in_shell(linux):
    assert ollama_control.build_restart_argv("sudo sv restart ollama") == [
        "/bin/sh",
        "-c",
        "sudo sv restart ollama",
    ]


def test_build_argv_windows_uses_cmd(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "platform", "win32")
    assert ollama_control.build_restart_argv("taskkill /F /IM ollama.exe") == [
        "cmd",
        "/c",
        "taskkill /F /IM ollama.exe",
    ]


# ── elevation (graphical sudo) ─────────────────────────────────────────────


def test_strip_leading_priv():
    assert ollama_control._strip_leading_priv("sudo -A sv restart ollama") == (
        "sv restart ollama"
    )
    assert ollama_control._strip_leading_priv("pkexec systemctl restart ollama") == (
        "systemctl restart ollama"
    )
    assert ollama_control._strip_leading_priv("sv restart ollama") == (
        "sv restart ollama"
    )


def test_elevation_prefix_sudo_askpass(linux, monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "sudo")
    monkeypatch.setattr(
        ollama_control, "_find_askpass", lambda: "/usr/bin/ssh-askpass"
    )
    res = ollama_control.elevation_prefix()
    assert res is not None
    prefix, env = res
    assert prefix == "sudo -A"
    assert env == {"SUDO_ASKPASS": "/usr/bin/ssh-askpass"}


def test_elevation_prefix_pkexec_fallback(linux, monkeypatch):
    # No askpass → fall back to pkexec (polkit's own dialog).
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "pkexec")
    monkeypatch.setattr(ollama_control, "_find_askpass", lambda: None)
    res = ollama_control.elevation_prefix()
    assert res is not None
    prefix, env = res
    assert prefix == "pkexec"
    assert env == {}


def test_elevation_prefix_none_on_windows(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "platform", "win32")
    assert ollama_control.elevation_prefix() is None


def test_prepare_restart_no_elevate(linux, monkeypatch):
    monkeypatch.delattr(ollama_control.sys, "frozen", raising=False)
    argv, env = ollama_control.prepare_restart("pkill -x ollama", elevate=False)
    assert argv == ["/bin/sh", "-c", "pkill -x ollama"]
    assert env is None  # nothing to inject


def test_prepare_restart_elevate_wraps_and_sets_askpass(linux, monkeypatch):
    monkeypatch.delattr(ollama_control.sys, "frozen", raising=False)
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "sudo")
    monkeypatch.setattr(
        ollama_control, "_find_askpass", lambda: "/usr/bin/ssh-askpass"
    )
    argv, env = ollama_control.prepare_restart(
        "sudo sv restart ollama", elevate=True
    )
    # Leading sudo stripped, re-applied as sudo -A; askpass exported.
    assert argv == ["/bin/sh", "-c", "sudo -A sv restart ollama"]
    assert env is not None and env["SUDO_ASKPASS"] == "/usr/bin/ssh-askpass"


# ── sudo -S (app-themed password dialog path) ──────────────────────────────


def test_sudo_available_true_when_sudo_on_path(linux, monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "sudo")
    assert ollama_control.sudo_available() is True


def test_sudo_available_false_without_sudo(linux, monkeypatch):
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: None)
    assert ollama_control.sudo_available() is False


def test_sudo_available_false_on_windows(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "platform", "win32")
    monkeypatch.setattr(ollama_control.shutil, "which", lambda b: b == "sudo")
    assert ollama_control.sudo_available() is False


def test_build_sudo_stdin_argv_uses_dash_s(linux):
    assert ollama_control.build_sudo_stdin_argv("sv restart ollama") == [
        "/bin/sh",
        "-c",
        "sudo -S -p '' sv restart ollama",
    ]


def test_build_sudo_stdin_argv_strips_leading_sudo(linux):
    # User already typed 'sudo …' — must not double up.
    assert ollama_control.build_sudo_stdin_argv("sudo systemctl restart ollama") == [
        "/bin/sh",
        "-c",
        "sudo -S -p '' systemctl restart ollama",
    ]


# ── restart_env (PyInstaller LD_LIBRARY_PATH handling) ─────────────────────


def test_restart_env_none_when_not_frozen(monkeypatch):
    monkeypatch.delattr(ollama_control.sys, "frozen", raising=False)
    assert ollama_control.restart_env() is None


def test_restart_env_restores_orig_when_frozen(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")
    env = ollama_control.restart_env()
    assert env is not None
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"  # restored to the system value


def test_restart_env_extra_merges_even_when_not_frozen(monkeypatch):
    # SUDO_ASKPASS must be injected even from source (not frozen) → concrete env.
    monkeypatch.delattr(ollama_control.sys, "frozen", raising=False)
    env = ollama_control.restart_env({"SUDO_ASKPASS": "/usr/bin/ssh-askpass"})
    assert env is not None
    assert env["SUDO_ASKPASS"] == "/usr/bin/ssh-askpass"


def test_restart_env_drops_var_when_no_orig(monkeypatch):
    monkeypatch.setattr(ollama_control.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bundle/_internal")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    env = ollama_control.restart_env()
    assert env is not None
    assert "LD_LIBRARY_PATH" not in env  # bundle path removed, none to restore


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
    # Simulate sudo's no-tty message; the helper should point at the elevation
    # option / passwordless sudo.
    ok, msg = ollama_control.restart_ollama(
        "printf 'sudo: a terminal is required to read the password'; false"
    )
    assert ok is False
    assert "Requires root" in msg or "passwordless sudo" in msg
