"""Minimal GPU utilization monitor for the status bar.

Reads from AMD sysfs (/sys/class/drm + hwmon) or NVIDIA nvidia-smi.
No external dependencies required — pure sysfs/subprocess.

AMD stats rely on Linux sysfs, so they're Linux-only.  NVIDIA stats come from
`nvidia-smi`, which ships with the driver on Windows and macOS as well, so
NVIDIA GPUs are covered on every platform.  When nothing is found the widget
hides itself.

Shows: GPU% · VRAMused/VRAMtotal · Temperature°C
Color: green < 50/70%/70°C · yellow < 80/90%/85°C · red above that.
Polls every 2 seconds on a background thread (so a slow/hung nvidia-smi never
freezes the UI).  Hidden automatically if no GPU found.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QWidget

logger = logging.getLogger(__name__)

# Suppress the console window nvidia-smi would otherwise flash on Windows.
# The constant only exists on Windows, so default to 0 elsewhere.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── Data ──────────────────────────────────────────────────────────────────────

@dataclass
class GpuStats:
    utilization: int    # 0–100 %
    vram_used_mb: int   # MB
    vram_total_mb: int  # MB
    temperature: int    # °C; -1 = unavailable


# ── Backends ──────────────────────────────────────────────────────────────────

def _read_int(path: Path) -> Optional[int]:
    try:
        return int(path.read_text().strip())
    except BaseException:
        return None


def _find_amd_device() -> Optional[Path]:
    """Return the sysfs device path for the first AMDGPU card."""
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        busy = card / "device" / "gpu_busy_percent"
        if not busy.exists():
            continue
        # Confirm via hwmon name so we don't accidentally pick a display engine
        hwmon_root = card / "device" / "hwmon"
        if hwmon_root.exists():
            for hw in hwmon_root.iterdir():
                name_file = hw / "name"
                if name_file.exists() and name_file.read_text().strip() == "amdgpu":
                    return card / "device"
        # Fallback: gpu_busy_percent existing is AMD-specific
        return card / "device"
    return None


def _read_amd(dev: Path) -> Optional[GpuStats]:
    util       = _read_int(dev / "gpu_busy_percent")
    vram_used  = _read_int(dev / "mem_info_vram_used")
    vram_total = _read_int(dev / "mem_info_vram_total")
    if util is None or vram_used is None or vram_total is None:
        return None

    # Prefer junction temp (temp2) over edge (temp1) — closer to real die temp
    temp = -1
    hwmon_root = dev / "hwmon"
    if hwmon_root.exists():
        for hw in sorted(hwmon_root.iterdir()):
            for idx in (2, 1, 3):
                t = _read_int(hw / f"temp{idx}_input")
                if t is not None:
                    temp = t // 1000  # millidegrees → °C
                    break
            if temp != -1:
                break

    return GpuStats(
        utilization=util,
        vram_used_mb=vram_used  // (1024 * 1024),
        vram_total_mb=vram_total // (1024 * 1024),
        temperature=temp,
    )


def _read_nvidia() -> Optional[GpuStats]:
    try:
        r = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
            creationflags=_NO_WINDOW,
        )
        if r.returncode != 0:
            return None
        parts = [p.strip() for p in r.stdout.strip().split(",")]
        if len(parts) < 4:
            return None
        return GpuStats(
            utilization=int(parts[0]),
            vram_used_mb=int(parts[1]),
            vram_total_mb=int(parts[2]),
            temperature=int(parts[3]),
        )
    except BaseException:
        return None


def read_gpu_stats() -> Optional[GpuStats]:
    """Return current GPU stats, or None if no supported GPU found.

    AMD is read from Linux sysfs (Linux-only); NVIDIA via nvidia-smi (all
    platforms).  Returns None when neither is present so the widget can hide.
    """
    if sys.platform == "linux":
        dev = _find_amd_device()
        if dev:
            return _read_amd(dev)
    return _read_nvidia()


# ── Widget ────────────────────────────────────────────────────────────────────

def _color_gpu(pct: int) -> str:
    if pct < 50:
        return "#4ade80"
    if pct < 80:
        return "#fbbf24"
    return "#f87171"


def _color_vram(used_mb: int, total_mb: int) -> str:
    if total_mb == 0:
        return "#6b7280"
    ratio = used_mb / total_mb
    if ratio < 0.70:
        return "#4ade80"
    if ratio < 0.90:
        return "#fbbf24"
    return "#f87171"


def _color_temp(t: int) -> str:
    if t < 0:
        return "#6b7280"
    if t < 70:
        return "#4ade80"
    if t < 85:
        return "#fbbf24"
    return "#f87171"

def _fmt_mb(mb: int) -> str:
    return f"{mb / 1024:.1f}G" if mb >= 1024 else f"{mb}M"


class _GpuPollWorker(QThread):
    """Polls GPU stats off the UI thread and emits each reading.

    ``read_gpu_stats`` shells out to nvidia-smi (up to a 3 s timeout), so running
    it on the main thread would stutter the UI every poll.  This worker does the
    blocking work on its own thread and emits results back via a queued signal.
    """

    polled = Signal(object)  # GpuStats | None

    def __init__(self, interval_ms: int, parent=None) -> None:
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._stop_evt = threading.Event()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                stats = read_gpu_stats()
            except BaseException:  # never let a poll failure kill the thread
                stats = None
            self.polled.emit(stats)
            # Interruptible wait — stop() returns control immediately.
            self._stop_evt.wait(self._interval_ms / 1000.0)

    def stop(self) -> None:
        self._stop_evt.set()


class GpuMonitorWidget(QWidget):
    """Compact status-bar widget: GPU% · VRAM · Temp, polled every 2 s."""

    _POLL_MS = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 2, 0)
        lay.setSpacing(0)

        self._lbl = QLabel()
        self._lbl.setStyleSheet("font-size: 11px;")
        lay.addWidget(self._lbl)

        # Stay hidden until the worker confirms a GPU on its first reading — the
        # detection probe itself can block, so we never run it on the UI thread.
        self._first_reading = True
        self.setVisible(False)

        self._worker = _GpuPollWorker(self._POLL_MS)
        self._worker.polled.connect(self._on_polled)  # queued: marshals to UI thread
        self._worker.start()

        # Make sure the thread is stopped cleanly before the process exits,
        # otherwise Qt warns/aborts on a still-running QThread at teardown.
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_worker)

    def _on_polled(self, stats: Optional[GpuStats]) -> None:
        if stats is None:
            if self._first_reading:
                # No GPU detectable — stop polling and stay hidden for good.
                self._stop_worker()
            self._first_reading = False
            return
        self._first_reading = False
        if not self.isVisible():
            self.setVisible(True)
        self._apply(stats)

    def _stop_worker(self) -> None:
        worker = getattr(self, "_worker", None)
        if worker is None:
            return
        self._worker = None
        worker.stop()
        worker.wait(2000)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._stop_worker()
        super().closeEvent(event)

    def _apply(self, s: GpuStats) -> None:
        gc = _color_gpu(s.utilization)
        vc = _color_vram(s.vram_used_mb, s.vram_total_mb)
        tc = _color_temp(s.temperature)

        used_str  = _fmt_mb(s.vram_used_mb)
        total_str = _fmt_mb(s.vram_total_mb)

        html = (
            f"<span style='color:{gc}'>GPU {s.utilization}%</span>"
            f"<span style='color:#555'> · </span>"
            f"<span style='color:{vc}'>{used_str}/{total_str}</span>"
        )
        if s.temperature >= 0:
            html += (
                f"<span style='color:#555'> · </span>"
                f"<span style='color:{tc}'>{s.temperature}°C</span>"
            )

        self._lbl.setText(html)
        self._lbl.setToolTip(
            f"GPU utilization:  {s.utilization}%\n"
            f"VRAM:             {used_str} / {total_str}\n"
            + (f"Temperature:      {s.temperature}°C" if s.temperature >= 0 else "")
        )
