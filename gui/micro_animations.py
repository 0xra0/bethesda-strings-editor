"""
Subtle micro-animations for visual feedback.

All functions are fire-and-forget: call them and they clean up after themselves.
Safe to call from the main GUI thread only.
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPoint,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    QTimer,
    Qt,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QLabel,
    QProgressBar,
    QWidget,
)


# ── Progress bar: success flash ───────────────────────────────────────────────

_SUCCESS_CHUNK = """
QProgressBar {
    border: 1px solid #16a34a;
    border-radius: 3px;
    background: transparent;
    text-align: center;
    color: #dcfce7;
}
QProgressBar::chunk {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #15803d, stop:0.5 #22c55e, stop:1 #15803d);
    border-radius: 3px;
}
"""


def flash_progress_bar_success(bar: QProgressBar) -> None:
    """Two-pulse green flash on *bar* to signal a successful batch completion."""
    prev = getattr(bar, "_pulse_anim", None)
    if prev is not None:
        prev.stop()
        bar.setGraphicsEffect(None)  # type: ignore[arg-type]

    bar.setStyleSheet(_SUCCESS_CHUNK)

    effect = QGraphicsOpacityEffect(bar)
    bar.setGraphicsEffect(effect)

    anim = QPropertyAnimation(effect, b"opacity", bar)
    anim.setDuration(1100)
    anim.setKeyValueAt(0.00, 1.0)
    anim.setKeyValueAt(0.20, 0.45)
    anim.setKeyValueAt(0.40, 1.0)
    anim.setKeyValueAt(0.60, 0.45)
    anim.setKeyValueAt(0.80, 1.0)
    anim.setKeyValueAt(1.00, 0.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)

    def _done() -> None:
        bar.setGraphicsEffect(None)  # type: ignore[arg-type]
        bar.setStyleSheet("")
        bar.setVisible(False)
        bar._pulse_anim = None  # type: ignore[attr-defined]

    anim.finished.connect(_done)
    anim.start()
    bar._pulse_anim = anim  # type: ignore[attr-defined]


# ── Success badge (centred floating toast) ────────────────────────────────────

def show_success_badge(parent: QWidget, message: str) -> None:
    """
    Show a floating "✓ <message>" badge that fades in, holds, then fades out.

    Timeline:  0–220 ms fade in  →  220–1700 ms hold  →  1700–2150 ms fade out.
    """
    badge = QLabel(f"✓   {message}", parent)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet("""
        background-color: rgba(20, 83, 45, 215);
        color: #bbf7d0;
        border: 1px solid #22c55e;
        border-radius: 9px;
        font-size: 15px;
        font-weight: 700;
        padding: 10px 28px;
    """)
    badge.adjustSize()

    pw, ph = parent.width(), parent.height()
    bw, bh = badge.width(), badge.height()
    badge.move((pw - bw) // 2, int(ph * 0.65) - bh // 2)
    badge.show()
    badge.raise_()

    effect = QGraphicsOpacityEffect(badge)
    badge.setGraphicsEffect(effect)
    effect.setOpacity(0.0)

    seq = QSequentialAnimationGroup(badge)

    fade_in = QPropertyAnimation(effect, b"opacity")
    fade_in.setDuration(220)
    fade_in.setStartValue(0.0)
    fade_in.setEndValue(1.0)
    fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
    seq.addAnimation(fade_in)

    seq.addPause(1480)

    fade_out = QPropertyAnimation(effect, b"opacity")
    fade_out.setDuration(450)
    fade_out.setStartValue(1.0)
    fade_out.setEndValue(0.0)
    fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
    seq.addAnimation(fade_out)

    seq.finished.connect(badge.deleteLater)
    seq.start()
    badge._anim_seq = seq  # type: ignore[attr-defined]


# ── Smooth progress bar ───────────────────────────────────────────────────────

class SmoothProgressBar(QProgressBar):
    """QProgressBar that animates value changes with an eased tween."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._anim = QPropertyAnimation(self, b"value", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def set_value_animated(self, value: int) -> None:
        """Animate to *value*.  Falls back to direct setValue during cleanup."""
        if not self.isVisible():
            self.setValue(value)
            return
        self._anim.stop()
        self._anim.setStartValue(self.value())
        self._anim.setEndValue(value)
        self._anim.start()


# ── Dialog fade-in mixin ──────────────────────────────────────────────────────

class FadeInMixin:
    """
    Mixin for QDialog subclasses — fades the dialog from transparent to opaque
    the first time it is shown.

    Usage::

        class MyDialog(FadeInMixin, QDialog):
            ...
    """

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)  # type: ignore[misc]
        if not getattr(self, "_fade_in_done", False):
            self._fade_in_done = True  # type: ignore[attr-defined]
            _animate_fade_in(self)  # type: ignore[arg-type]


def _animate_fade_in(widget: QWidget, duration: int = 180) -> None:
    """Animate *widget* from opacity 0 → 1 then remove the effect."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def _cleanup() -> None:
        widget.setGraphicsEffect(None)  # type: ignore[arg-type]

    anim.finished.connect(_cleanup)
    anim.start()
    widget._fade_anim = anim  # type: ignore[attr-defined]


# ── Drop overlay fade-in ──────────────────────────────────────────────────────

def fade_in_overlay(widget: QWidget, duration: int = 140) -> None:
    """Fade *widget* from 0 → 1 opacity on show.  Used for the _DropOverlay."""
    prev = getattr(widget, "_overlay_fade", None)
    if prev is not None:
        prev.stop()
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    effect.setOpacity(0.0)

    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutQuad)

    def _done() -> None:
        widget.setGraphicsEffect(None)  # type: ignore[arg-type]
        widget._overlay_fade = None  # type: ignore[attr-defined]

    anim.finished.connect(_done)
    anim.start()
    widget._overlay_fade = anim  # type: ignore[attr-defined]


# ── Welcome card idle pulse ───────────────────────────────────────────────────

def start_card_pulse(card: QWidget) -> None:
    """
    Start a slow, infinite opacity pulse on *card* (0.80 ↔ 1.0, ~2.4 s cycle).
    Call stop_card_pulse() to cancel before hiding/removing the card.
    """
    prev = getattr(card, "_card_pulse", None)
    if prev is not None:
        return  # already running

    effect = QGraphicsOpacityEffect(card)
    card.setGraphicsEffect(effect)
    effect.setOpacity(1.0)

    anim = QPropertyAnimation(effect, b"opacity", card)
    anim.setDuration(2400)
    anim.setLoopCount(-1)  # infinite
    anim.setKeyValueAt(0.00, 1.0)
    anim.setKeyValueAt(0.50, 0.80)
    anim.setKeyValueAt(1.00, 1.0)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.start()

    card._card_pulse = anim  # type: ignore[attr-defined]
    card._card_pulse_effect = effect  # type: ignore[attr-defined]


def stop_card_pulse(card: QWidget) -> None:
    """Stop and remove the idle pulse started by start_card_pulse()."""
    anim = getattr(card, "_card_pulse", None)
    if anim is not None:
        anim.stop()
        card._card_pulse = None  # type: ignore[attr-defined]
    card.setGraphicsEffect(None)  # type: ignore[arg-type]


# ── In-app toast notifications ────────────────────────────────────────────────

_TOAST_COLORS = {
    "success": ("#14532d", "#bbf7d0", "#22c55e"),   # bg, text, border
    "error":   ("#450a0a", "#fecaca", "#ef4444"),
    "warning": ("#431407", "#fed7aa", "#f97316"),
    "info":    ("#0c1a2e", "#bfdbfe", "#3b82f6"),
}

_active_toasts: list["_ToastWidget"] = []


class _ToastWidget(QWidget):
    """A single slide-up / fade-out toast notification."""

    _MARGIN_RIGHT = 18
    _MARGIN_BOTTOM = 48
    _SPACING = 8

    def __init__(
        self,
        parent: QWidget,
        message: str,
        kind: str = "success",
        timeout_ms: int = 3500,
    ) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        bg, fg, border = _TOAST_COLORS.get(kind, _TOAST_COLORS["info"])
        self.setStyleSheet(f"""
            background: {bg};
            color: {fg};
            border: 1px solid {border};
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
            padding: 10px 20px;
        """)

        from PySide6.QtWidgets import QHBoxLayout
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        icon = {"success": "✓", "error": "✕", "warning": "⚠", "info": "ℹ"}.get(kind, "•")
        lbl = QLabel(f"{icon}  {message}")
        lbl.setStyleSheet("background: transparent; border: none; padding: 0;")
        lay.addWidget(lbl)
        self.adjustSize()

        self._timeout_ms = timeout_ms
        self._animate_in()

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _target_pos(self) -> QPoint:
        """Bottom-right corner position, stacked above other active toasts."""
        p = self.parent()
        if not isinstance(p, QWidget):
            return QPoint(0, 0)
        pw, ph = p.width(), p.height()
        bw, bh = self.width(), self.height()

        # Stack above any toasts that are already on screen
        stack_offset = 0
        for t in _active_toasts:
            if t is not self and t.isVisible():
                stack_offset += t.height() + self._SPACING

        x = pw - bw - self._MARGIN_RIGHT
        y = ph - bh - self._MARGIN_BOTTOM - stack_offset
        return QPoint(x, y)

    # ── Animation ─────────────────────────────────────────────────────────────

    def _animate_in(self) -> None:
        target = self._target_pos()
        start = QPoint(target.x(), target.y() + 24)  # slide up from below

        self.move(start)
        self.show()
        self.raise_()
        _active_toasts.append(self)

        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        effect.setOpacity(0.0)

        par = QParallelAnimationGroup(self)

        fade = QPropertyAnimation(effect, b"opacity")
        fade.setDuration(220)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        par.addAnimation(fade)

        slide = QPropertyAnimation(self, b"pos")
        slide.setDuration(220)
        slide.setStartValue(start)
        slide.setEndValue(target)
        slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        par.addAnimation(slide)

        def _in_done() -> None:
            self.setGraphicsEffect(None)  # type: ignore[arg-type]
            QTimer.singleShot(self._timeout_ms, self._animate_out)

        par.finished.connect(_in_done)
        par.start()
        self._in_anim = par

    def _animate_out(self) -> None:
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        effect.setOpacity(1.0)

        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(300)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)

        def _out_done() -> None:
            if self in _active_toasts:
                _active_toasts.remove(self)
            self.close()

        anim.finished.connect(_out_done)
        anim.start()
        self._out_anim = anim


def show_toast(
    parent: QWidget,
    message: str,
    kind: str = "success",
    timeout_ms: int = 3500,
) -> None:
    """
    Show a slide-up toast notification anchored to *parent*'s bottom-right corner.

    *kind* can be ``"success"``, ``"error"``, ``"warning"``, or ``"info"``.
    Multiple toasts stack vertically.
    """
    _ToastWidget(parent, message, kind=kind, timeout_ms=timeout_ms)
