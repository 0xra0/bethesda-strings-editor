"""
Desktop push notifications for Linux.

Backend priority:
  1. notify-send (libnotify) — GNOME, KDE Plasma, XFCE, MATE, Cinnamon,
     and any tiling WM with a freedesktop notification daemon
     (dunst, mako, swaync, fnott, deadd-notification-center, etc.).
  2. dbus-send  — same freedesktop D-Bus spec, no libnotify wrapper needed.
  3. QSystemTrayIcon.showMessage — Qt-level popup as last resort.
"""

import logging
import shutil
import subprocess
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


def send_notification(
    title: str,
    body: str,
    timeout_ms: int = 6000,
    tray_icon=None,
) -> None:
    """Send a desktop push notification on Linux.

    tray_icon: optional QSystemTrayIcon passed for the Qt fallback path.
    Falls through each backend silently; never raises.
    """
    if _try_notify_send(title, body, timeout_ms):
        return
    if _try_dbus_send(title, body, timeout_ms):
        return
    if tray_icon is not None:
        try:
            from PySide6.QtWidgets import QSystemTrayIcon
            tray_icon.showMessage(
                title,
                body,
                QSystemTrayIcon.MessageIcon.Information,
                timeout_ms,
            )
        except Exception as exc:
            logger.debug(f"QSystemTrayIcon.showMessage failed: {exc}")
