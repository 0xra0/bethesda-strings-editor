"""Forcible Ollama server control — a fast hard-stop for a wedged backend.

The normal Stop path (``OllamaWorker.stop``) only closes the *client* sockets.
On a slow or wedged ROCm GPU the Ollama *server* keeps generating the in-flight
requests, so the model stays resident and the whole machine feels frozen for many
seconds after the user hits Stop.  The only reliable way to free the GPU
immediately is to restart (or kill) the Ollama server process itself — exactly
what ``sv restart ollama`` / ``systemctl restart ollama`` / ``pkill ollama`` do.

This module runs a *user-configured* shell command to do that.  Nothing here ever
runs automatically: it fires only when the user has put a command in Settings, so
we never invoke ``sudo`` or restart a service behind the user's back.  Commands
run with stdin closed and under a hard timeout, so a ``sudo`` password prompt (no
NOPASSWD rule) fails fast instead of hanging the app.

Pure helpers (``detect_restart_command`` / ``restart_ollama``) live here with no
Qt dependency so they can be unit-tested; the GUI runs the command asynchronously
via ``QProcess`` (see ``MainWindow._force_restart_ollama``) and reuses
:func:`build_restart_argv` so both paths execute the command identically.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from typing import List, Tuple

logger = logging.getLogger(__name__)

# Candidate hard-stop commands in preference order.  Each tuple is
# (binary-to-probe, full-command).  runit (`sv`) first because that is what the
# user's box uses; then systemd; then a blunt process kill as a last resort.
_RESTART_CANDIDATES: List[Tuple[str, str]] = [
    ("sv", "sv restart ollama"),
    ("systemctl", "systemctl restart ollama"),
    ("rc-service", "rc-service ollama restart"),  # OpenRC
    ("brew", "brew services restart ollama"),     # macOS
    ("pkill", "pkill -x ollama"),                 # no service manager — just kill it
]


def detect_restart_command() -> str:
    """Best-guess command to restart/kill the local Ollama server.

    Probes ``PATH`` for a known service manager and returns the matching command,
    or ``""`` when none is found.  The result is only a *suggestion* shown in the
    Settings field — the user can edit it (e.g. prepend ``sudo``) before it ever
    runs.
    """
    for binary, command in _RESTART_CANDIDATES:
        if shutil.which(binary):
            return command
    return ""


def build_restart_argv(command: str) -> List[str]:
    """Wrap a command string so any shell form (sudo, pipes, ``&&``) works.

    Returns an argv suitable for both :func:`subprocess.Popen` and
    ``QProcess.start`` so the blocking and async paths execute identically.
    """
    return ["/bin/sh", "-c", command]


def restart_ollama(command: str, timeout: float = 20.0) -> Tuple[bool, str]:
    """Run *command* to restart/kill Ollama, blocking up to *timeout* seconds.

    Returns ``(ok, message)``.  stdin is closed so an interactive ``sudo``
    password prompt fails immediately rather than hanging; the whole process
    group is killed on timeout so a stuck command can never wedge the caller.

    The GUI does not call this (it uses ``QProcess`` to stay non-blocking) — this
    is the testable/CLI entry point and a fallback.
    """
    command = (command or "").strip()
    if not command:
        return False, "No restart command configured."

    try:
        proc = subprocess.Popen(
            build_restart_argv(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group so killpg reaches children
            text=True,
        )
    except (OSError, ValueError) as exc:
        logger.error("Failed to launch Ollama restart command %r: %s", command, exc)
        return False, f"Could not run command: {exc}"

    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        try:
            out, _ = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            out = ""
        logger.error("Ollama restart command timed out after %ss: %r", timeout, command)
        return False, f"Command timed out after {timeout:g}s (killed)."

    out = (out or "").strip()
    if proc.returncode == 0:
        logger.info("Ollama restart command succeeded: %r", command)
        return True, out or "Ollama restarted."

    logger.error(
        "Ollama restart command failed (exit %s): %r — %s",
        proc.returncode, command, out,
    )
    # A sudo password prompt with no tty surfaces here; give an actionable hint.
    hint = out or f"exit code {proc.returncode}"
    if "password" in out.lower() or "terminal is required" in out.lower():
        hint += " (configure passwordless sudo for this command, e.g. a NOPASSWD rule)"
    return False, hint


def _kill_group(proc: "subprocess.Popen") -> None:
    """Terminate the command's whole process group; ignore if already gone."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
