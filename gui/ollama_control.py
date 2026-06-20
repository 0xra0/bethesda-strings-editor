"""Forcible Ollama server control — a fast hard-stop for a wedged backend.

The normal Stop path (``OllamaWorker.stop``) only closes the *client* sockets.
On a slow or wedged ROCm GPU the Ollama *server* keeps generating the in-flight
requests, so the model stays resident and the whole machine feels frozen for many
seconds after the user hits Stop.  The only reliable way to free the GPU
immediately is to restart (or kill) the Ollama server process itself.

This module runs a *user-configured* command to do that.  Nothing here ever runs
automatically: it fires only when the user has put a command in Settings.  Two
ways to get the rights to do it:

* **Privileged (Linux/BSD):** when the command needs root (e.g. restarting a
  system service), set ``elevate`` and it is wrapped to ask for the password via
  a **graphical dialog** — ``sudo -A`` with a detected GUI askpass helper (works
  with no terminal and no polkit agent), falling back to ``pkexec`` (polkit's own
  dialog).  No NOPASSWD rule required.
* **Non-root (any OS, incl. Windows):** if Ollama runs as the current user, just
  kill it — ``pkill -x ollama`` on Unix, ``taskkill /F /T /IM ollama.exe`` on
  Windows.  No elevation needed.

Pure helpers live here with no Qt dependency so they can be unit-tested; the GUI
runs the command asynchronously via ``QProcess`` (see
``MainWindow._force_restart_ollama``) and reuses :func:`prepare_restart` so the
blocking and async paths execute the command identically.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Candidate hard-stop commands in preference order (Linux/BSD).  Each tuple is
# (binary-to-probe, full-command).  runit (`sv`) first; then systemd; OpenRC; a
# blunt user-owned process kill as a last resort.
_RESTART_CANDIDATES: List[Tuple[str, str]] = [
    ("sv", "sv restart ollama"),
    ("systemctl", "systemctl restart ollama"),
    ("rc-service", "rc-service ollama restart"),  # OpenRC
    ("pkill", "pkill -x ollama"),                 # no service manager — just kill it
]

# Non-root kill of a *user-owned* Ollama (frees the GPU without any elevation).
_WIN_KILL = "taskkill /F /T /IM ollama.exe"
_UNIX_KILL = "pkill -x ollama"

# GUI password helpers for `sudo -A` (SUDO_ASKPASS).  Probed in order; absolute
# paths are checked on disk, bare names via PATH.
_ASKPASS_CANDIDATES: List[str] = [
    "ssh-askpass",
    "ksshaskpass",
    "lxqt-openssh-askpass",
    "x11-ssh-askpass",
    "/usr/libexec/openssh/ssh-askpass",
    "/usr/lib/ssh/ssh-askpass",
    "/usr/lib/openssh/gnome-ssh-askpass",
    "/usr/bin/ssh-askpass",
]

_PRIV_WORDS = ("sudo", "pkexec", "doas")


def detect_restart_command() -> str:
    """Best-guess force-stop command for this platform.

    Windows → ``taskkill`` (non-root, kills a user-owned Ollama).  macOS → brew
    service restart if present, else ``pkill``.  Linux/BSD → the first available
    service manager, else ``pkill``.  Only a *suggestion* shown in Settings; the
    user can edit it.
    """
    if sys.platform == "win32":
        return _WIN_KILL
    if sys.platform == "darwin":
        if shutil.which("brew"):
            return "brew services restart ollama"
        return _UNIX_KILL
    for binary, command in _RESTART_CANDIDATES:
        if shutil.which(binary):
            return command
    return ""


def detect_kill_command() -> str:
    """Non-root command to kill a user-owned Ollama (works on every OS)."""
    return _WIN_KILL if sys.platform == "win32" else _UNIX_KILL


def command_needs_root(command: str) -> bool:
    """Heuristic: does *command* manage a system service (and so need root)?

    Used to pre-tick the elevation checkbox after auto-detect.  A bare process
    kill (pkill/taskkill) of a user-owned Ollama does not need root.
    """
    c = (command or "").lower()
    return any(tok in c for tok in ("systemctl", "sv ", "rc-service", "service "))


# ── Environment (PyInstaller + askpass) ────────────────────────────────────


def restart_env(extra: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """Environment for launching a *system* binary from a PyInstaller bundle.

    Returns ``None`` (inherit unchanged) when not frozen *and* no ``extra`` env is
    needed.  When frozen, PyInstaller's bootloader prepended the bundle's lib dir
    to ``LD_LIBRARY_PATH`` (``DYLD_LIBRARY_PATH`` on macOS); a system binary
    inheriting that may load the bundle's libstdc++/libssl and crash, so restore
    the pre-launch value from PyInstaller's ``<VAR>_ORIG`` snapshot (or drop it).
    *extra* (e.g. ``SUDO_ASKPASS``) is merged in last and forces a concrete env.
    """
    frozen = bool(getattr(sys, "frozen", False))
    if not frozen and not extra:
        return None
    env = dict(os.environ)
    if frozen:
        for key in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            orig = env.get(key + "_ORIG")
            if orig is not None:
                env[key] = orig
            else:
                env.pop(key, None)
    if extra:
        env.update(extra)
    return env


# ── Privilege escalation (graphical) ───────────────────────────────────────


def _find_askpass() -> Optional[str]:
    """Path to a GUI password helper for ``sudo -A``, or None."""
    for cand in _ASKPASS_CANDIDATES:
        if os.path.isabs(cand):
            if os.path.exists(cand):
                return cand
        else:
            found = shutil.which(cand)
            if found:
                return found
    return None


def elevation_prefix() -> Optional[Tuple[str, Dict[str, str]]]:
    """Graphical privilege-escalation prefix for a root command, or None.

    Returns ``(prefix, extra_env)``.  Linux/BSD only.  Prefers ``sudo -A`` with a
    detected GUI askpass helper — it needs neither a controlling terminal nor a
    running polkit agent — then ``pkexec`` (polkit's own dialog), then plain
    ``sudo`` as a last resort.
    """
    if sys.platform in ("win32", "darwin"):
        return None
    askpass = _find_askpass()
    if shutil.which("sudo") and askpass:
        return "sudo -A", {"SUDO_ASKPASS": askpass}
    if shutil.which("pkexec"):
        return "pkexec", {}
    if shutil.which("sudo"):
        return "sudo", {}
    return None


def sudo_available() -> bool:
    """True if ``sudo`` can be used for elevation on this platform.

    Unix only (Windows uses non-root ``taskkill``).  Gate for the app's own
    themed password dialog → :func:`build_sudo_stdin_argv`.
    """
    return sys.platform != "win32" and shutil.which("sudo") is not None


def build_sudo_stdin_argv(command: str) -> List[str]:
    """argv to run *command* as root via ``sudo -S`` (password from stdin).

    Pairs with the app's own themed password dialog (:class:`gui.sudo_dialog.
    SudoPasswordDialog`): the GUI collects the password, then writes it plus a
    newline to this process's stdin.  ``-S`` reads the password from stdin and
    ``-p ''`` suppresses sudo's own prompt text (the dialog already asked).  Any
    leading sudo/pkexec/doas the user typed is stripped first so we don't double
    up.  Returns an argv for :func:`subprocess.Popen` / ``QProcess.start``.
    """
    stripped = _strip_leading_priv((command or "").strip())
    return build_restart_argv(f"sudo -S -p '' {stripped}")


def _strip_leading_priv(command: str) -> str:
    """Drop a leading sudo/pkexec/doas (and its option flags) from *command*.

    Lets us re-apply the chosen escalation cleanly even if the user already typed
    ``sudo …`` in the Settings field.
    """
    toks = command.strip().split()
    while toks and toks[0].lower() in _PRIV_WORDS:
        toks.pop(0)
        while toks and toks[0].startswith("-"):
            toks.pop(0)
    return " ".join(toks)


def build_restart_argv(command: str) -> List[str]:
    """Wrap a command string in the platform shell so any form works.

    Windows → ``cmd /c``; everywhere else → ``/bin/sh -c``.  Returns an argv for
    both :func:`subprocess.Popen` and ``QProcess.start``.
    """
    if sys.platform == "win32":
        return ["cmd", "/c", command]
    return ["/bin/sh", "-c", command]


def prepare_restart(
    command: str, elevate: bool = False
) -> Tuple[List[str], Optional[Dict[str, str]]]:
    """Build ``(argv, env)`` for the force-stop command.

    When *elevate* is set (Linux/BSD), the command is wrapped to ask for the root
    password via a graphical dialog (see :func:`elevation_prefix`).  On Windows
    *elevate* is ignored — use the non-root ``taskkill`` method instead.  ``env``
    is ``None`` to mean "inherit", unless a clean PyInstaller env or
    ``SUDO_ASKPASS`` must be injected.
    """
    command = (command or "").strip()
    extra_env: Dict[str, str] = {}
    if elevate and command:
        info = elevation_prefix()
        if info is not None:
            prefix, extra_env = info
            command = f"{prefix} {_strip_leading_priv(command)}"
        else:
            logger.warning(
                "Elevation requested but no sudo/pkexec/askpass found — running "
                "the command unprivileged."
            )
    return build_restart_argv(command), restart_env(extra_env or None)


def restart_ollama(
    command: str, elevate: bool = False, timeout: float = 20.0
) -> Tuple[bool, str]:
    """Run *command* to restart/kill Ollama, blocking up to *timeout* seconds.

    Returns ``(ok, message)``.  stdin is closed so a plain ``sudo`` prompt fails
    fast instead of hanging (``sudo -A`` / ``pkexec`` use a GUI dialog instead);
    the whole process group is killed on timeout.  The GUI uses ``QProcess`` to
    stay non-blocking — this is the testable/CLI entry point and a fallback.
    """
    command = (command or "").strip()
    if not command:
        return False, "No restart command configured."

    argv, env = prepare_restart(command, elevate=elevate)

    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group so killpg reaches children
            text=True,
            env=env,
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
    hint = out or f"exit code {proc.returncode}"
    if "password" in out.lower() or "terminal is required" in out.lower():
        hint += (
            " (tick 'Requires root' in Settings to get a graphical password "
            "dialog, or set up passwordless sudo)"
        )
    return False, hint


def _kill_group(proc: "subprocess.Popen") -> None:
    """Terminate the command's whole process group; ignore if already gone."""
    if sys.platform == "win32":
        try:
            proc.kill()
        except OSError:
            pass
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except OSError:
            pass
