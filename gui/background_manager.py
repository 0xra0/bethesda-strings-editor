"""
Background / wallpaper manager for the main window.

Supports:
  Static images  — PNG, JPG, JPEG, BMP, TIFF, WEBP, ICO, SVG
  Animated GIFs  — via QMovie (loops automatically)
  Video files    — MP4, AVI, MKV, WEBM, MOV, WMV, FLV, M4V, MPEG, OGV
                   requires PySide6-Multimedia and system GStreamer plugins

Fit modes: cover, contain, stretch, tile, center
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".ico", ".svg",
}
ANIMATED_EXTS = {".gif"}
VIDEO_EXTS = {
    ".mp4", ".avi", ".mkv", ".webm", ".mov", ".wmv", ".flv",
    ".m4v", ".mpg", ".mpeg", ".ts", ".ogv",
}
ALL_SUPPORTED_EXTS = IMAGE_EXTS | ANIMATED_EXTS | VIDEO_EXTS


class _BackgroundWidget(QWidget):
    """
    Invisible overlay that sits below all other main-window children and
    paints the wallpaper / video frame.  Mouse events pass through.
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)

        self._pixmap: Optional[QPixmap] = None
        self._opacity: float = 0.30
        self._fit_mode: str = "cover"

        self._movie = None          # QMovie — animated GIF
        self._player = None         # QMediaPlayer — video
        self._vsink = None          # QVideoSink  — video

    # ── public API ─────────────────────────────────────────────────────────

    def set_opacity(self, v: float) -> None:
        self._opacity = max(0.0, min(1.0, v))
        self.update()

    def set_fit_mode(self, mode: str) -> None:
        self._fit_mode = mode
        self.update()

    def load(self, path: str) -> bool:
        """Load *path*.  Returns True on success."""
        self._clear()
        if not path:
            self.update()
            return True
        ext = Path(path).suffix.lower()
        if ext in VIDEO_EXTS:
            return self._load_video(path)
        if ext in ANIMATED_EXTS:
            return self._load_gif(path)
        return self._load_static(path)

    # ── internals ──────────────────────────────────────────────────────────

    def _clear(self) -> None:
        if self._movie:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        if self._player:
            self._player.stop()
            self._player.deleteLater()
            self._player = None
        if self._vsink:
            self._vsink.deleteLater()
            self._vsink = None
        self._pixmap = None

    def _load_static(self, path: str) -> bool:
        px = QPixmap(path)
        if px.isNull():
            logger.warning("BackgroundManager: cannot load image %s", path)
            return False
        self._pixmap = px
        self.update()
        return True

    def _load_gif(self, path: str) -> bool:
        from PySide6.QtGui import QMovie
        self._movie = QMovie(path)
        if not self._movie.isValid():
            logger.warning("BackgroundManager: invalid GIF %s", path)
            self._movie = None
            return False
        self._movie.frameChanged.connect(self._on_gif_frame)
        self._movie.start()
        return True

    def _on_gif_frame(self, _: int) -> None:
        if self._movie:
            self._pixmap = self._movie.currentPixmap()
            self.update()

    def _load_video(self, path: str) -> bool:
        try:
            from PySide6.QtMultimedia import QMediaPlayer, QVideoSink
        except ImportError:
            logger.warning(
                "BackgroundManager: PySide6-Multimedia not available — "
                "falling back to static load for %s", path
            )
            return self._load_static(path)

        self._vsink = QVideoSink(self)
        self._vsink.videoFrameChanged.connect(self._on_video_frame)

        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._vsink)
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.play()
        return True

    def _on_video_frame(self, frame) -> None:
        if not frame.isValid():
            return
        img = frame.toImage()
        if not img.isNull():
            self._pixmap = QPixmap.fromImage(img)
            self.update()

    def _on_media_status(self, status) -> None:
        try:
            from PySide6.QtMultimedia import QMediaPlayer
            if status == QMediaPlayer.MediaStatus.EndOfMedia and self._player:
                self._player.setPosition(0)
                self._player.play()
        except Exception:
            pass

    # ── painting ───────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        if not self._pixmap or self._pixmap.isNull() or self._opacity <= 0.0:
            return

        p = QPainter(self)
        p.setOpacity(self._opacity)

        W, H = self.width(), self.height()
        pw, ph = self._pixmap.width(), self._pixmap.height()
        if pw <= 0 or ph <= 0:
            return

        mode = self._fit_mode

        if mode == "stretch":
            p.drawPixmap(0, 0, W, H, self._pixmap)

        elif mode == "tile":
            for y in range(0, H, ph):
                for x in range(0, W, pw):
                    p.drawPixmap(x, y, self._pixmap)

        elif mode == "center":
            x = (W - pw) // 2
            y = (H - ph) // 2
            p.drawPixmap(x, y, self._pixmap)

        elif mode == "contain":
            scale = min(W / pw, H / ph)
            nw, nh = int(pw * scale), int(ph * scale)
            p.drawPixmap((W - nw) // 2, (H - nh) // 2, nw, nh, self._pixmap)

        else:  # cover (default)
            scale = max(W / pw, H / ph)
            nw, nh = int(pw * scale), int(ph * scale)
            p.drawPixmap((W - nw) // 2, (H - nh) // 2, nw, nh, self._pixmap)

        p.end()


class BackgroundManager:
    """
    Owns the _BackgroundWidget and applies settings changes to it.

    Usage::

        # in MainWindow.__init__
        self.bg_manager = BackgroundManager(self)

        # in MainWindow.resizeEvent
        self.bg_manager.resize()

        # after settings dialog accepted
        self.bg_manager.apply(
            self.settings.background_enabled,
            self.settings.background_path,
            self.settings.background_opacity,
            self.settings.background_fit_mode,
        )
    """

    def __init__(self, main_window: QWidget) -> None:
        self._win = main_window
        self._widget: Optional[_BackgroundWidget] = None

    def apply(self, enabled: bool, path: str, opacity: float, fit_mode: str) -> None:
        if not enabled or not path:
            if self._widget:
                self._widget.hide()
            return

        if self._widget is None:
            self._widget = _BackgroundWidget(self._win)

        self._widget.set_opacity(opacity)
        self._widget.set_fit_mode(fit_mode)
        self._widget.load(path)
        self._resize()
        self._widget.lower()
        self._widget.show()

    def resize(self) -> None:
        self._resize()

    def _resize(self) -> None:
        if self._widget and self._widget.isVisible():
            self._widget.setGeometry(self._win.rect())
