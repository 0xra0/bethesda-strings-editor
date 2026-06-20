"""
Desktop push notifications — cross-platform.

Backend priority:
  Linux:
    1. notify-send (libnotify) — GNOME, KDE Plasma, XFCE, MATE, Cinnamon,
       and any tiling WM with a freedesktop notification daemon
       (dunst, mako, swaync, fnott, deadd-notification-center, etc.).
    2. dbus-send  — same freedesktop D-Bus spec, no libnotify wrapper needed.
    3. QSystemTrayIcon.showMessage — Qt-level popup as last resort.
  Windows / macOS:
    QSystemTrayIcon.showMessage first — it maps to the native balloon/toast
    (Shell_NotifyIcon on Windows, NSUserNotification on macOS); the libnotify /
    D-Bus probes are skipped since those tools don't exist there.
"""

import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_ICON_PATH: str = str(
    (Path(__file__).parent.parent / "resources" / "app_icon_64.png").resolve()
)
_APP_NAME = "Bethesda Strings Translator"


def _try_notify_send(title: str, body: str, timeout_ms: int) -> bool:
    if not shutil.which("notify-send"):
        return False
    try:
        subprocess.Popen(
            [
                "notify-send",
                f"--app-name={_APP_NAME}",
                f"--icon={_ICON_PATH}",
                f"--expire-time={timeout_ms}",
                "--category=transfer.complete",
                title,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def _try_dbus_send(title: str, body: str, timeout_ms: int) -> bool:
    if not shutil.which("dbus-send"):
        return False
    try:
        # org.freedesktop.Notifications.Notify signature:
        #   app_name, replaces_id, app_icon, summary, body,
        #   actions, hints, expire_timeout
        subprocess.Popen(
            [
                "dbus-send",
                "--session",
                "--dest=org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications.Notify",
                f"string:{_APP_NAME}",
                "uint32:0",
                "string:dialog-information",
                f"string:{title}",
                f"string:{body}",
                "array:string:",
                "dict:string:string:",
                f"int32:{timeout_ms}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError:
        return False


def _try_qt_tray(title: str, body: str, timeout_ms: int, tray_icon) -> bool:
    """Show a balloon via QSystemTrayIcon — the native path on Windows/macOS.

    ``showMessage`` only works on a *visible* tray icon whose platform supports
    balloon messages, so verify both first (otherwise it silently no-ops, which
    is the usual reason "nothing pops up" on Windows).
    """
    if tray_icon is None:
        return False
    try:
        from PySide6.QtWidgets import QSystemTrayIcon

        if not QSystemTrayIcon.supportsMessages():
            return False
        # If the tray wasn't shown at creation (system tray reported unavailable
        # at startup, common on a slow Windows logon), show it now.
        if not tray_icon.isVisible():
            tray_icon.show()
        if not tray_icon.isVisible():
            return False
        tray_icon.showMessage(
            title,
            body,
            QSystemTrayIcon.MessageIcon.Information,
            timeout_ms,
        )
        return True
    except Exception as exc:
        logger.debug(f"QSystemTrayIcon.showMessage failed: {exc}")
        return False


def send_notification(
    title: str,
    body: str,
    timeout_ms: int = 6000,
    tray_icon=None,
) -> None:
    """Send a desktop push notification (cross-platform).

    tray_icon: optional QSystemTrayIcon, used for the native Windows/macOS
    balloon and as the Linux last-resort fallback.  Falls through each backend
    silently; never raises.
    """
    # Windows / macOS: notify-send & dbus-send don't exist there, so go straight
    # to the Qt tray balloon (the OS-native notification path).
    if sys.platform != "linux":
        _try_qt_tray(title, body, timeout_ms, tray_icon)
        return
    # Linux: prefer the freedesktop notification daemon, fall back to Qt.
    if _try_notify_send(title, body, timeout_ms):
        return
    if _try_dbus_send(title, body, timeout_ms):
        return
    _try_qt_tray(title, body, timeout_ms, tray_icon)
