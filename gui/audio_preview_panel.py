"""
Audio / TTS Preview dock panel.

Shows the currently selected string's original and translated text, lets the
user browse for (or auto-locate) the original game audio, plays it back, and
synthesizes a TTS read-out of the translation so timing can be compared.

Layout (QDockWidget, docked at bottom or right):

  ┌─ String info ──────────────────────────────────────────────────┐
  │  ID 0x00012345  "You should see New Atlantis before you die…"   │
  └────────────────────────────────────────────────────────────────┘
  ┌─ Original audio ──────────────────┐ ┌─ TTS preview ────────────┐
  │ [Browse…] path/to/file.wav  [▶]  │ │ Voice: [uk ▼] [▶ Synth] │
  │ Duration: 2.34 s                  │ │ Duration: 2.11 s  [Stop] │
  └───────────────────────────────────┘ └──────────────────────────┘
  ┌─ Timing comparison ────────────────────────────────────────────┐
  │  ████████████████████░░░░  orig 2.34s  tts 2.11s  90%         │
  └────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import (
    QObject, QRunnable, QThreadPool, QTimer, Signal, Slot,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QDockWidget, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

logger = logging.getLogger(__name__)

# ── Timing bar widget ─────────────────────────────────────────────────────────

_BAR_HEIGHT = 18


class _TimingBar(QWidget):
    """Custom widget showing original vs. TTS duration as proportional bars."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.orig_dur: float = 0.0
        self.tts_dur: float = 0.0
        self.setMinimumHeight(_BAR_HEIGHT + 4)
        self.setMaximumHeight(_BAR_HEIGHT + 4)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_durations(self, orig: float, tts: float) -> None:
        self.orig_dur = orig
        self.tts_dur = tts
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.orig_dur <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = _BAR_HEIGHT
        y = 2

        max_dur = max(self.orig_dur, self.tts_dur, 0.01)

        # Original bar (neutral grey)
        orig_w = int(w * self.orig_dur / max_dur)
        painter.fillRect(0, y, orig_w, h, QColor("#555"))

        # TTS bar (color-coded by ratio)
        if self.tts_dur > 0:
            ratio = self.tts_dur / self.orig_dur
            if ratio <= 1.10:
                color = QColor("#4caf50")   # green  ≤ 110%
            elif ratio <= 1.30:
                color = QColor("#ff9800")   # orange ≤ 130%
            else:
                color = QColor("#f44336")   # red    > 130%

            tts_w = int(w * self.tts_dur / max_dur)
            bar_h = max(4, h // 2)
            bar_y = y + (h - bar_h) // 2
            painter.fillRect(0, bar_y, tts_w, bar_h, color)

        # Border
        painter.setPen(QPen(QColor("#888"), 1))
        painter.drawRect(0, y, w - 1, h - 1)

        painter.end()


# ── Background synthesis worker ───────────────────────────────────────────────

class _SynthSignals(QObject):
    done = Signal(object)   # TTSResult | None


class _SynthWorker(QRunnable):
    """Synthesize TTS audio in a thread-pool thread."""

    def __init__(
        self,
        text: str,
        engine_type: str,
        voice: str,
        piper_binary: str,
        piper_model: str,
        espeak_binary: str,
        espeak_speed: int,
        cache_dir: Path,
    ) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self.text = text
        self.engine_type = engine_type
        self.voice = voice
        self.piper_binary = piper_binary
        self.piper_model = piper_model
        self.espeak_binary = espeak_binary
        self.espeak_speed = espeak_speed
        self.cache_dir = cache_dir
        self.signals = _SynthSignals()

    def run(self) -> None:
        from gui.tts_engine import synthesize
        try:
            result = synthesize(
                self.text,
                engine_type=self.engine_type,
                voice=self.voice,
                piper_binary=self.piper_binary,
                piper_model=self.piper_model,
                espeak_binary=self.espeak_binary,
                espeak_speed=self.espeak_speed,
                cache_dir=self.cache_dir,
            )
        except Exception as exc:
            logger.error("TTS synthesis failed: %s", exc)
            result = None
        self.signals.done.emit(result)


# ── Panel ─────────────────────────────────────────────────────────────────────

class AudioPreviewPanel(QDockWidget):
    """Dockable panel for original audio playback and TTS preview."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("AudioPreviewPanel")
        self.setWindowTitle(self.tr("Audio Preview"))
        self.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea
            | Qt.DockWidgetArea.RightDockWidgetArea
            | Qt.DockWidgetArea.TopDockWidgetArea
            | Qt.DockWidgetArea.BottomDockWidgetArea
        )

        # Settings snapshot (refreshed from main_window when needed)
        self._engine_type = "espeak"
        self._voice = "uk"
        self._piper_binary = "piper"
        self._piper_model = ""
        self._espeak_binary = "espeak-ng"
        self._espeak_speed = 130
        self._auto_preview = False
        self._cache_dir: Optional[Path] = None

        # Current string state
        self._current_translated: str = ""
        self._current_string_id: int = -1

        # Media players
        self._orig_player = QMediaPlayer(self)
        self._orig_audio_out = QAudioOutput(self)
        self._orig_player.setAudioOutput(self._orig_audio_out)
        self._orig_player.playbackStateChanged.connect(self._on_orig_state_changed)

        self._tts_player = QMediaPlayer(self)
        self._tts_audio_out = QAudioOutput(self)
        self._tts_player.setAudioOutput(self._tts_audio_out)
        self._tts_player.playbackStateChanged.connect(self._on_tts_state_changed)

        # TTS synthesis result
        self._tts_result = None

        # Audio file index for auto-locate
        from gui.tts_engine import AudioFileIndex
        self._audio_index = AudioFileIndex()

        self._build_ui()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(6, 4, 6, 4)
        root_layout.setSpacing(4)

        # ── String info ──────────────────────────────────────────────
        self._info_label = QLabel(self.tr("(no string selected)"))
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("font-size: 11px; color: #aaa;")
        root_layout.addWidget(self._info_label)

        # ── Two columns: original audio | TTS ────────────────────────
        cols = QHBoxLayout()
        cols.setSpacing(8)

        # Original audio column
        orig_col = QVBoxLayout()
        orig_col.setSpacing(2)
        orig_col.addWidget(QLabel(self.tr("Original audio:")))

        orig_row = QHBoxLayout()
        self._orig_path_edit = QLineEdit()
        self._orig_path_edit.setPlaceholderText(self.tr("Path to .wav / .mp3 …"))
        self._orig_path_edit.setReadOnly(True)
        orig_row.addWidget(self._orig_path_edit, stretch=1)
        self._browse_btn = QToolButton()
        self._browse_btn.setText("…")
        self._browse_btn.setToolTip(self.tr("Browse for audio file"))
        self._browse_btn.clicked.connect(self._browse_orig)
        orig_row.addWidget(self._browse_btn)
        orig_col.addLayout(orig_row)

        orig_ctrl = QHBoxLayout()
        self._orig_play_btn = QPushButton(self.tr("▶ Play"))
        self._orig_play_btn.setEnabled(False)
        self._orig_play_btn.clicked.connect(self._toggle_orig)
        orig_ctrl.addWidget(self._orig_play_btn)
        self._orig_dur_label = QLabel("—")
        orig_ctrl.addWidget(self._orig_dur_label)
        orig_ctrl.addStretch()
        orig_col.addLayout(orig_ctrl)
        orig_col.addStretch()

        # TTS column
        tts_col = QVBoxLayout()
        tts_col.setSpacing(2)
        tts_col.addWidget(QLabel(self.tr("TTS preview:")))

        tts_ctrl = QHBoxLayout()
        self._synth_btn = QPushButton(self.tr("⟳ Synthesize"))
        self._synth_btn.setEnabled(False)
        self._synth_btn.clicked.connect(self._synthesize)
        tts_ctrl.addWidget(self._synth_btn)
        self._tts_play_btn = QPushButton(self.tr("▶ Play"))
        self._tts_play_btn.setEnabled(False)
        self._tts_play_btn.clicked.connect(self._toggle_tts)
        tts_ctrl.addWidget(self._tts_play_btn)
        tts_col.addLayout(tts_ctrl)

        tts_info = QHBoxLayout()
        self._tts_dur_label = QLabel("—")
        tts_info.addWidget(self._tts_dur_label)
        self._tts_status_label = QLabel("")
        self._tts_status_label.setStyleSheet("font-size: 10px; color: #888;")
        tts_info.addWidget(self._tts_status_label)
        tts_info.addStretch()
        tts_col.addLayout(tts_info)
        tts_col.addStretch()

        cols.addLayout(orig_col, stretch=1)
        cols.addLayout(tts_col, stretch=1)
        root_layout.addLayout(cols)

        # ── Timing bar ───────────────────────────────────────────────
        self._timing_bar = _TimingBar()
        self._timing_label = QLabel("")
        self._timing_label.setStyleSheet("font-size: 10px;")
        root_layout.addWidget(self._timing_bar)
        root_layout.addWidget(self._timing_label)

        root_layout.addStretch()
        self.setWidget(root)

    # ── Public API ────────────────────────────────────────────────────────────

    def apply_settings(
        self,
        engine_type: str,
        voice: str,
        piper_binary: str,
        piper_model: str,
        espeak_binary: str,
        espeak_speed: int,
        audio_dir: str,
        auto_preview: bool,
        cache_dir: Path,
    ) -> None:
        self._engine_type = engine_type
        self._voice = voice
        self._piper_binary = piper_binary
        self._piper_model = piper_model
        self._espeak_binary = espeak_binary
        self._espeak_speed = espeak_speed
        self._auto_preview = auto_preview
        self._cache_dir = cache_dir
        self._audio_index.set_directory(audio_dir)

    def update_string(self, row_data: Optional[dict]) -> None:
        """Called from main_window when selection changes."""
        if row_data is None:
            self._info_label.setText(self.tr("(no string selected)"))
            self._synth_btn.setEnabled(False)
            self._current_translated = ""
            self._current_string_id = -1
            return

        string_id = row_data.get("string_id", -1)
        original = row_data.get("original", "")
        translated = row_data.get("translated", "")
        self._current_string_id = string_id
        self._current_translated = translated

        # String info header
        id_str = f"0x{string_id:08X}" if string_id >= 0 else "—"
        preview = original[:80] + ("…" if len(original) > 80 else "")
        self._info_label.setText("ID " + id_str + "  “" + preview + "”")

        self._synth_btn.setEnabled(bool(translated))

        # Try to auto-locate original audio by form ID
        if string_id > 0:
            found = self._audio_index.find(string_id)
            if found:
                self._set_orig_path(str(found))

        # Update estimated duration in timing bar even before synthesis
        from gui.tts_engine import estimate_duration
        lang = self._voice[:2] if self._voice else "uk"
        est = estimate_duration(translated, lang)
        if self._tts_result is None:
            self._tts_dur_label.setText(f"~{est:.2f} s (est.)")
            self._update_timing(est)

        if self._auto_preview and translated:
            QTimer.singleShot(150, self._synthesize)

    # ── Orig audio ────────────────────────────────────────────────────────────

    def _browse_orig(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self.tr("Select original audio file"),
            str(Path.home()),
            self.tr("Audio files (*.wav *.mp3 *.ogg *.flac *.xwm);;All files (*)"),
        )
        if path:
            self._set_orig_path(path)

    def _set_orig_path(self, path: str) -> None:
        self._orig_path_edit.setText(path)
        from gui.tts_engine import wav_duration
        p = Path(path)
        if p.suffix.lower() == ".wav":
            dur = wav_duration(p)
            if dur > 0:
                self._orig_dur_label.setText(f"{dur:.2f} s")
                self._update_timing(orig_dur=dur)
        self._orig_play_btn.setEnabled(p.is_file())

    def _toggle_orig(self) -> None:
        if self._orig_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._orig_player.pause()
        else:
            path = self._orig_path_edit.text().strip()
            if path and Path(path).is_file():
                self._orig_player.setSource(f"file://{path}")
                self._orig_player.play()

    @Slot(object)
    def _on_orig_state_changed(self, state) -> None:
        playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self._orig_play_btn.setText(self.tr("⏸ Pause") if playing else self.tr("▶ Play"))

    # ── TTS synthesis ─────────────────────────────────────────────────────────

    def _synthesize(self) -> None:
        text = self._current_translated.strip()
        if not text:
            return
        cache_dir = self._cache_dir or Path(os.path.expanduser("~/.config/bse/tts_cache"))
        self._synth_btn.setEnabled(False)
        self._tts_status_label.setText(self.tr("Synthesizing…"))
        worker = _SynthWorker(
            text=text,
            engine_type=self._engine_type,
            voice=self._voice,
            piper_binary=self._piper_binary,
            piper_model=self._piper_model,
            espeak_binary=self._espeak_binary,
            espeak_speed=self._espeak_speed,
            cache_dir=cache_dir,
        )
        worker.signals.done.connect(self._on_synth_done)
        QThreadPool.globalInstance().start(worker)

    @Slot(object)
    def _on_synth_done(self, result) -> None:
        self._synth_btn.setEnabled(bool(self._current_translated))
        if result is None:
            self._tts_status_label.setText(self.tr("Synthesis failed"))
            return
        self._tts_result = result
        self._tts_dur_label.setText(f"{result.duration:.2f} s ({result.engine})")
        self._tts_status_label.setText("")
        self._tts_play_btn.setEnabled(True)
        self._update_timing(tts_dur=result.duration)
        self._tts_player.setSource(f"file://{result.audio_path}")

    def _toggle_tts(self) -> None:
        if self._tts_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._tts_player.pause()
        else:
            self._tts_player.play()

    @Slot(object)
    def _on_tts_state_changed(self, state) -> None:
        playing = (state == QMediaPlayer.PlaybackState.PlayingState)
        self._tts_play_btn.setText(self.tr("⏸ Pause") if playing else self.tr("▶ Play"))

    # ── Timing bar ────────────────────────────────────────────────────────────

    def _update_timing(
        self,
        orig_dur: Optional[float] = None,
        tts_dur: Optional[float] = None,
    ) -> None:
        if orig_dur is not None:
            self._timing_bar.orig_dur = orig_dur
        if tts_dur is not None:
            self._timing_bar.tts_dur = tts_dur
        o = self._timing_bar.orig_dur
        t = self._timing_bar.tts_dur
        self._timing_bar.set_durations(o, t)
        if o > 0 and t > 0:
            pct = int(t / o * 100)
            color = "#4caf50" if pct <= 110 else "#ff9800" if pct <= 130 else "#f44336"
            self._timing_label.setText(
                f"<span style='color:{color}'>{pct}%</span> — "
                f"orig {o:.2f}s · tts {t:.2f}s"
            )
        elif o > 0:
            self._timing_label.setText(f"orig {o:.2f}s")
        else:
            self._timing_label.setText("")
