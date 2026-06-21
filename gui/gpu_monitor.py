"""Minimal GPU utilization monitor for the status bar.

Reads from AMD sysfs (/sys/class/drm + hwmon) or NVIDIA nvidia-smi.
No external dependencies required — pure sysfs/subprocess.

AMD stats rely on Linux sysfs, so they're Linux-only.  NVIDIA stats come from
`nvidia-smi`, which ships with the driver on Windows and macOS as well, so
NVIDIA GPUs are covered on every platform.  When nothing is found the widget
hides itself.

Shows: GPU% · VRAMused/VRAMtotal · Temperature°C
Color: green < 50/70%/70°C · yellow < 80/90%/85°C · red above that.
Updates every 2 seconds via QTimer.  Hidden automatically if no GPU found.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

logger = logging.getLogger(__name__)


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


class GpuMonitorWidget(QWidget):
    """Compact status-bar widget: GPU% · VRAM · Temp, updated every 2 s."""

    _POLL_MS = 2000

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 2, 0)
        lay.setSpacing(0)

        self._lbl = QLabel()
        self._lbl.setStyleSheet("font-size: 11px;")
        lay.addWidget(self._lbl)

        # Hide immediately if no GPU is detectable
        stats = read_gpu_stats()
        if stats is None:
            self.setVisible(False)
            return

        self._apply(stats)

        self._timer = QTimer(self)
        self._timer.setInterval(self._POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def _poll(self) -> None:
        stats = read_gpu_stats()
        if stats:
            self._apply(stats)

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
