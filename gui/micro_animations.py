"""
Subtle micro-animations for visual feedback.

All functions are fire-and-forget: call them and they clean up after themselves.
Safe to call from the main GUI thread only.
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSequentialAnimationGroup, Qt
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QProgressBar, QWidget

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
    """
    Two-pulse green flash on *bar* to signal a successful batch completion.

    Timeline (~1.1 s):  bar turns green → opacity pulses 1→0.5→1→0.5→1→0
                        → stylesheet cleared, bar hidden.
    """
    # Cancel any in-progress animation from a previous batch
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


def show_success_badge(parent: QWidget, message: str) -> None:
    """
    Show a floating "✓ <message>" toast that fades in, holds, then fades out.

    Timeline:  0–220 ms fade in  →  220–1700 ms hold  →  1700–2150 ms fade out
               → widget auto-deleted.

    The badge is centred horizontally and placed at ~65 % of the parent's height
    (toast / snackbar position — above the status bar, below the table).
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
    badge._anim_seq = seq  # type: ignore[attr-defined]  — keep alive until done
