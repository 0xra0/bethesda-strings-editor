"""
Wwise voice index for Starfield — map dialogue FormIDs to the original game
voice clips packed inside ``*Voices*.ba2`` archives, and decode them to WAV.

Background
----------
Unlike Skyrim / Fallout 4 (which used ``.fuz`` = FaceFX + xWMA), Starfield ships
voice acting as **Wwise Encoded Media** (``.wem``, RIFF/WAVE with the Wwise
Vorbis ``0xFFFF`` format tag) bundled inside GNRL v2 BA2 archives.  The internal
path convention is::

    sound/voice/<plugin>.esm/<voicetype>/<8-hex-lowercase-formid>.wem

The 8-hex FormID is the dialogue **INFO** record's FormID, which is exactly the
``id`` shown for each row when an ESP/ESM plugin is loaded in the editor — so a
direct FormID lookup resolves the clip with no extra index needed.  The same
FormID can appear under several voice-type folders (shared/common lines), so the
index stores a *list* of clips per FormID and the UI may let the user pick which
voice type to audition.

``ffmpeg`` cannot decode Wwise Vorbis; ``vgmstream-cli`` can.  This module shells
out to ``vgmstream-cli`` to produce a cached ``.wav`` the GUI's subprocess audio
player can play directly.

This module is part of the pure-Python ``bethesda_strings`` layer and has no Qt
dependency.
"""

from __future__ import annotations

import logging
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .ba2_handler import BA2File

logger = logging.getLogger(__name__)

# A voice archive filename looks like one of:
#   Starfield - Voices01.ba2        (no lang suffix  -> English)
#   Starfield - Voices02.ba2        (English)
#   Starfield - VoicesPatch.ba2     (English)
#   SFBGS003 - Voices_en.ba2        (suffix -> en)
#   ShatteredSpace - Voices_de.ba2  (suffix -> de)
# The optional digit/"patch" run sits between "voices" and an optional _lang.
_ARCHIVE_LANG_RE = re.compile(
    r"voices(?:\d+|patch)?(?:_([a-z]{2,3}))?\.ba2$", re.IGNORECASE
)

# Internal name -> 8-hex FormID just before the .wem extension.
_NAME_FORMID_RE = re.compile(r"([0-9a-fA-F]{8})\.wem$")

# Internal name -> the voice-type folder (the path component before the file).
_NAME_VOICETYPE_RE = re.compile(r"/([^/]+)/[0-9a-fA-F]{8}\.wem$", re.IGNORECASE)

# Filenames with no _lang suffix are the English base-game voices.
_DEFAULT_LANGUAGE = "en"


def classify_archive_language(filename: str) -> Optional[str]:
    """Return the language code of a voice BA2 by filename, or ``None``.

    ``None`` means the file is not a recognizable voice archive (so it should be
    skipped without opening it — important for huge texture archives).  A voice
    archive with no ``_xx`` suffix (``Voices01``/``Voices02``/``VoicesPatch``)
    is the English base game and reports ``"en"``.
    """
    m = _ARCHIVE_LANG_RE.search(Path(filename).name)
    if m is None:
        return None
    return (m.group(1) or _DEFAULT_LANGUAGE).lower()


def form_id_from_name(internal_name: str) -> Optional[int]:
    """Parse the FormID from a ``…/<8hex>.wem`` internal archive path."""
    m = _NAME_FORMID_RE.search(internal_name)
    if m is None:
        return None
    return int(m.group(1), 16)


def voice_type_from_name(internal_name: str) -> str:
    """Return the voice-type folder name from an internal archive path."""
    m = _NAME_VOICETYPE_RE.search(internal_name.replace("\\", "/"))
    return m.group(1) if m else ""


@dataclass(frozen=True)
class VoiceClip:
    """One playable voice line located inside a voice archive."""

    form_id: int
    voice_type: str
    archive_name: str
    internal_name: str
    archive_index: int


class VoiceIndex:
    """Index of FormID -> voice clips across a game ``Data`` directory.

    Scanning opens every matching voice archive and reads its name table, which
    for the base game is ~160k entries and takes a few seconds — call
    :meth:`build` from a background thread.  Decoding is cached as ``.wav`` files
    under *cache_dir* so re-auditioning a line is instant.
    """

    def __init__(
        self,
        data_dir: "str | Path",
        vgmstream_binary: str = "vgmstream-cli",
        cache_dir: "Optional[str | Path]" = None,
        language: str = _DEFAULT_LANGUAGE,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._vgmstream = vgmstream_binary or "vgmstream-cli"
        self._language = (language or _DEFAULT_LANGUAGE).lower()
        if cache_dir is None:
            cache_dir = Path(tempfile.gettempdir()) / "bse_voice_cache"
        self._cache_dir = Path(cache_dir)

        self._archives: list[BA2File] = []
        self._archive_names: list[str] = []
        self._map: dict[int, list[VoiceClip]] = {}
        self._built = False
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def language(self) -> str:
        return self._language

    @property
    def count(self) -> int:
        """Number of distinct FormIDs that have at least one voice clip."""
        return len(self._map)

    @property
    def archive_count(self) -> int:
        return len(self._archives)

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def build(self) -> int:
        """Scan voice archives for the target language and build the index.

        Idempotent — returns immediately if already built.  Returns the number
        of distinct FormIDs indexed.
        """
        with self._lock:
            if self._built:
                return len(self._map)
            if not self._data_dir.is_dir():
                logger.warning(
                    "Voice data dir does not exist: %s", self._data_dir
                )
                self._built = True
                return 0

            for ba2_path in sorted(self._data_dir.glob("*.ba2")):
                lang = classify_archive_language(ba2_path.name)
                if lang != self._language:
                    continue
                try:
                    archive = BA2File(ba2_path)
                except Exception as exc:  # noqa: BLE001 — skip unreadable archives
                    logger.warning("Skipping voice archive %s: %s", ba2_path.name, exc)
                    continue

                aidx = len(self._archives)
                self._archives.append(archive)
                self._archive_names.append(ba2_path.name)

                added = 0
                for name in archive.list_files():
                    form_id = form_id_from_name(name)
                    if form_id is None:
                        continue
                    clip = VoiceClip(
                        form_id=form_id,
                        voice_type=voice_type_from_name(name),
                        archive_name=ba2_path.name,
                        internal_name=name,
                        archive_index=aidx,
                    )
                    self._map.setdefault(form_id, []).append(clip)
                    added += 1
                logger.info(
                    "Voice archive %s: indexed %d clips", ba2_path.name, added
                )

            self._built = True
            logger.info(
                "Voice index built: %d archives, %d distinct FormIDs (lang=%s)",
                len(self._archives), len(self._map), self._language,
            )
            return len(self._map)

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def find(self, form_id: int) -> list[VoiceClip]:
        """Return all voice clips for *form_id* (empty list if none)."""
        return list(self._map.get(int(form_id), ()))

    def voice_types(self, form_id: int) -> list[str]:
        """Return the distinct voice-type folders that voice *form_id*."""
        seen: list[str] = []
        for clip in self._map.get(int(form_id), ()):
            if clip.voice_type not in seen:
                seen.append(clip.voice_type)
        return seen

    # ------------------------------------------------------------------
    # Decoding
    # ------------------------------------------------------------------

    def get_wav(
        self, form_id: int, voice_type: Optional[str] = None
    ) -> Optional[Path]:
        """Extract and decode a voice clip to a cached WAV; return its path.

        If *voice_type* is given, the matching clip is used; otherwise the first
        clip for the FormID is used.  Returns ``None`` if there is no clip or if
        decoding fails.
        """
        clips = self.find(form_id)
        if not clips:
            return None

        clip = None
        if voice_type:
            for c in clips:
                if c.voice_type == voice_type:
                    clip = c
                    break
        if clip is None:
            clip = clips[0]

        cache_name = f"{clip.form_id:08x}__{clip.voice_type or 'default'}.wav"
        out_path = self._cache_dir / cache_name
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path

        with self._lock:
            # Re-check after acquiring the lock (another thread may have decoded).
            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
            try:
                data = self._archives[clip.archive_index].extract(clip.internal_name)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to extract %s: %s", clip.internal_name, exc)
                return None

            self._cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_wem: Optional[Path] = None
            try:
                fd, tmp_name = tempfile.mkstemp(suffix=".wem", dir=self._cache_dir)
                tmp_wem = Path(tmp_name)
                with open(fd, "wb") as fh:
                    fh.write(data)
                if not self._decode(tmp_wem, out_path):
                    return None
            finally:
                if tmp_wem is not None:
                    tmp_wem.unlink(missing_ok=True)

            if out_path.exists() and out_path.stat().st_size > 0:
                return out_path
            return None

    def _decode(self, wem_path: Path, wav_path: Path) -> bool:
        """Run vgmstream-cli to decode *wem_path* into *wav_path*."""
        try:
            proc = subprocess.run(
                [self._vgmstream, "-o", str(wav_path), str(wem_path)],
                capture_output=True,
                timeout=60,
            )
        except FileNotFoundError:
            logger.error("vgmstream binary not found: %s", self._vgmstream)
            return False
        except subprocess.TimeoutExpired:
            logger.error("vgmstream timed out decoding %s", wem_path.name)
            return False
        if proc.returncode != 0:
            logger.error(
                "vgmstream failed (%d): %s",
                proc.returncode,
                proc.stderr.decode("utf-8", "replace")[:200],
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            for archive in self._archives:
                try:
                    archive.close()
                except Exception:  # noqa: BLE001
                    pass
            self._archives.clear()

    def __enter__(self) -> "VoiceIndex":
        return self

    def __exit__(self, *_) -> None:
        self.close()
