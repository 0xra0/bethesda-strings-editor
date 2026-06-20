"""Tests for gui.desktop_notify — platform routing of push notifications.

Pure routing logic only: every backend is stubbed, so no real notification
daemon, D-Bus, or Qt tray is touched.  Verifies that Windows/macOS go straight
to the Qt tray balloon while Linux prefers the freedesktop backends.
"""

from gui import desktop_notify


class _Recorder:
    """Stub backends that record whether they were called (and report success)."""

    def __init__(self, monkeypatch, notify=False, dbus=False, qt=False):
        self.calls = []
        monkeypatch.setattr(
            desktop_notify,
            "_try_notify_send",
            lambda *a, **k: (self.calls.append("notify"), notify)[1],
        )
        monkeypatch.setattr(
            desktop_notify,
            "_try_dbus_send",
            lambda *a, **k: (self.calls.append("dbus"), dbus)[1],
        )
        monkeypatch.setattr(
            desktop_notify,
            "_try_qt_tray",
            lambda *a, **k: (self.calls.append("qt"), qt)[1],
        )


def test_windows_goes_straight_to_qt_tray(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "win32")
    rec = _Recorder(monkeypatch, qt=True)
    desktop_notify.send_notification("t", "b", tray_icon=object())
    # No libnotify/D-Bus probing on Windows — only the Qt balloon.
    assert rec.calls == ["qt"]


def test_macos_goes_straight_to_qt_tray(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "darwin")
    rec = _Recorder(monkeypatch, qt=True)
    desktop_notify.send_notification("t", "b", tray_icon=object())
    assert rec.calls == ["qt"]


def test_linux_prefers_notify_send(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "linux")
    rec = _Recorder(monkeypatch, notify=True)
    desktop_notify.send_notification("t", "b")
    assert rec.calls == ["notify"]  # stops at the first success


def test_linux_falls_back_to_dbus_then_qt(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "linux")
    rec = _Recorder(monkeypatch, notify=False, dbus=False, qt=True)
    desktop_notify.send_notification("t", "b", tray_icon=object())
    assert rec.calls == ["notify", "dbus", "qt"]


def test_never_raises_when_all_backends_fail(monkeypatch):
    monkeypatch.setattr(desktop_notify.sys, "platform", "linux")
    _Recorder(monkeypatch, notify=False, dbus=False, qt=False)
    # Must not raise even when nothing can deliver the notification.
    desktop_notify.send_notification("t", "b", tray_icon=None)
