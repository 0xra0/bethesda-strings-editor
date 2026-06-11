"""
Local TTS synthesis engine abstraction for the Audio Preview panel.

Supported engines (in priority order):
  1. Piper  — high-quality neural TTS; requires the ``piper`` binary +
              a .onnx model file.  Optional: falls back to eSpeak if absent.
  2. eSpeak-NG — low-footprint formant synthesizer; ``espeak-ng`` binary is
                 typically available in any Linux distro.  Produces lower-quality
                 audio but needs zero extra downloads.

Both engines write a WAV file to a caller-supplied path and return a
``TTSResult`` with the measured audio duration.  A cache keyed on
``sha256(text|voice|engine)`` avoids redundant synthesis runs.

Duration estimation (``estimate_duration``) provides a syllable-based
fallback so the timing bar can display something meaningful even when no TTS
is available.
"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import wave
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Average syllables/second in voiced game dialogue (conservative — dialogue is
# slightly slower than natural speech to aid localisation timing).
_SYLLABLES_PER_SEC: dict[str, float] = {
    "uk": 4.2,
    "ru": 4.2,
    "en": 4.8,
    "de": 4.0,
    "fr": 5.0,
    "es": 5.0,
    "pl": 4.5,
    "cs": 4.3,
}
_DEFAULT_SYLLABLES_PER_SEC = 4.5

# Unicode vowel sets for syllable counting
_VOWELS_CY = frozenset("аеєиіїоуюяёАЕЄИІЇОУЮЯЁ")
_VOWELS_LATIN = frozenset("aeiouAEIOU")


# ── Helpers ───────────────────────────────────────────────────────────────────

def estimate_duration(text: str, lang: str = "uk") -> float:
    """Return a rough duration estimate in seconds based on syllable count.

    This is used as a fallback when no TTS audio is available, and to give
    an instant "reading time" display while synthesis runs in the background.
    """
    # Strip markup tokens that won't be spoken
    text = re.sub(r"\[\[.*?\]\]|<[^>]+>|#[0-9a-fA-F]+|\{[^}]+\}", "", text)
    rate = _SYLLABLES_PER_SEC.get(lang[:2], _DEFAULT_SYLLABLES_PER_SEC)
    syllables = sum(1 for ch in text if ch in _VOWELS_CY or ch in _VOWELS_LATIN)
    # Minimum: short text still takes some time; add 0.2s padding per pause marker
    pauses = text.count(",") + text.count(".") + text.count("—") + text.count("…")
    return max(0.5, syllables / rate + pauses * 0.15)


def wav_duration(path: Path) -> float:
    """Return exact duration of a WAV file in seconds.  Returns 0.0 on error."""
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def cache_key(text: str, voice: str, engine: str) -> str:
    """Deterministic cache filename component from synthesis parameters."""
    payload = f"{text}|{voice}|{engine}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


# ── Result type ───────────────────────────────────────────────────────────────

class TTSResult:
    __slots__ = ("audio_path", "duration", "engine")

    def __init__(self, audio_path: Path, duration: float, engine: str) -> None:
        self.audio_path = audio_path
        self.duration = duration
        self.engine = engine


# ── espeak-NG engine ──────────────────────────────────────────────────────────

# espeak-ng language codes for the target languages used in the app
_ESPEAK_VOICES: dict[str, str] = {
    "uk": "uk",
    "ru": "ru",
    "de": "de",
    "fr": "fr",
    "es": "es",
    "pl": "pl",
    "cs": "cs",
    "en": "en",
    "en-US": "en-us",
    "en-GB": "en-gb",
}

# Words-per-minute for espeak-ng (-s flag).  Game dialogue is read at a slower
# cadence than the espeak default of 175 WPM.
_ESPEAK_SPEED = 130


def espeak_available(binary: str = "espeak-ng") -> bool:
    return shutil.which(binary) is not None


def synthesize_espeak(
    text: str,
    voice: str,
    output_path: Path,
    binary: str = "espeak-ng",
    speed: int = _ESPEAK_SPEED,
) -> bool:
    """Synthesize *text* using espeak-ng and write WAV to *output_path*.

    Returns True on success.
    """
    espeak_voice = _ESPEAK_VOICES.get(voice, voice)
    cmd = [binary, "-v", espeak_voice, "-s", str(speed), "-w", str(output_path), text]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=30, text=True
        )
        if result.returncode != 0:
            logger.warning("espeak-ng error: %s", result.stderr.strip())
            return False
        return output_path.is_file() and output_path.stat().st_size > 44
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("espeak-ng failed: %s", exc)
        return False


# ── Piper engine ──────────────────────────────────────────────────────────────

def piper_available(binary: str = "piper") -> bool:
    return shutil.which(binary) is not None or (len(binary) > 0 and Path(binary).is_file())


def synthesize_piper(
    text: str,
    model_path: str,
    output_path: Path,
    binary: str = "piper",
) -> bool:
    """Synthesize *text* using Piper and write WAV to *output_path*.

    Piper reads text from stdin and writes WAV to --output-file.
    Returns True on success.
    """
    if not model_path or not Path(model_path).is_file():
        logger.warning("Piper model not found: %s", model_path)
        return False
    piper_bin = shutil.which(binary) or (binary if Path(binary).is_file() else None)
    if piper_bin is None:
        logger.warning("Piper binary not found: %s", binary)
        return False
    cmd = [piper_bin, "--model", model_path, "--output-file", str(output_path)]
    try:
        result = subprocess.run(
            cmd, input=text, capture_output=True, timeout=60, text=True
        )
        if result.returncode != 0:
            logger.warning("Piper error: %s", result.stderr.strip())
            return False
        return output_path.is_file() and output_path.stat().st_size > 44
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("Piper failed: %s", exc)
        return False


# ── Unified synthesize entry point ────────────────────────────────────────────

def synthesize(
    text: str,
    *,
    engine_type: str,          # "piper" | "espeak" | "none"
    voice: str = "uk",         # espeak voice code OR piper model path
    piper_binary: str = "piper",
    piper_model: str = "",
    espeak_binary: str = "espeak-ng",
    espeak_speed: int = _ESPEAK_SPEED,
    cache_dir: Optional[Path] = None,
) -> Optional[TTSResult]:
    """Synthesize *text* and return a ``TTSResult``, or ``None`` on failure.

    Results are cached in *cache_dir* if supplied so the same text is only
    synthesized once per session.
    """
    if engine_type == "none" or not text.strip():
        return None

    engine_key = f"{engine_type}:{voice}:{piper_model}"
    key = cache_key(text, voice, engine_key)

    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"{key}.wav"
        if cached.is_file():
            dur = wav_duration(cached)
            if dur > 0.05:
                return TTSResult(cached, dur, engine_type)

    output = (cache_dir / f"{key}.wav") if cache_dir else (
        Path(f"/tmp/bse_tts_{key}.wav")
    )

    success = False
    used_engine = engine_type

    if engine_type == "piper":
        success = synthesize_piper(text, piper_model, output, binary=piper_binary)
        if not success:
            logger.info("Piper failed, falling back to espeak-ng")
            success = synthesize_espeak(text, voice, output,
                                        binary=espeak_binary, speed=espeak_speed)
            used_engine = "espeak"
    elif engine_type == "espeak":
        success = synthesize_espeak(text, voice, output,
                                    binary=espeak_binary, speed=espeak_speed)
        used_engine = "espeak"

    if not success:
        return None

    dur = wav_duration(output)
    return TTSResult(output, dur, used_engine) if dur > 0.05 else None


# ── Audio directory scanner ───────────────────────────────────────────────────

class AudioFileIndex:
    """Lazy index of audio files in a directory, keyed by form ID patterns.

    Bethesda voice files after extraction follow naming conventions like:
      00012345.wav
      *_00012345_*.wav   (with NPC EDID and topic prefix/suffix)

    The index is built once on first access and reused.
    """

    EXTENSIONS = (".wav", ".mp3", ".ogg", ".flac", ".xwm")

    def __init__(self) -> None:
        self._dir: Optional[Path] = None
        self._hex_map: dict[int, list[Path]] = {}   # form_id → [file, ...]
        self._built = False

    def set_directory(self, path: str) -> None:
        new_dir = Path(path) if path else None
        if new_dir != self._dir:
            self._dir = new_dir
            self._hex_map.clear()
            self._built = False

    def find(self, form_id: int) -> Optional[Path]:
        """Return the best matching audio file for *form_id*, or None."""
        if not self._built:
            self._build()
        candidates = self._hex_map.get(form_id)
        if candidates:
            # Prefer .wav, then .mp3, then others
            for ext in (".wav", ".mp3", ".ogg", ".flac"):
                for p in candidates:
                    if p.suffix.lower() == ext:
                        return p
            return candidates[0]
        return None

    def _build(self) -> None:
        self._built = True
        self._hex_map.clear()
        if self._dir is None or not self._dir.is_dir():
            return
        # Walk directory tree (up to depth 5 to cover voiced subdirs)
        try:
            for p in self._dir.rglob("*"):
                if p.suffix.lower() not in self.EXTENSIONS:
                    continue
                stem = p.stem.upper()
                # Match 8-char hex segment anywhere in filename
                for match in re.finditer(r"[0-9A-F]{8}", stem):
                    try:
                        fid = int(match.group(), 16)
                        self._hex_map.setdefault(fid, []).append(p)
                    except ValueError:
                        pass
        except OSError as exc:
            logger.warning("Audio index build failed: %s", exc)
