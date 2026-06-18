"""
Tests for the Wwise voice index (bethesda_strings/wwise_voice.py).

The pure functions — archive language classification, FormID parsing, and
voice-type extraction — are exercised with no game files.  A single end-to-end
test that actually opens a BA2 and decodes a clip is gated behind a path-exists
skip so the suite stays green on machines without the game installed (e.g. CI).

Run with:
    python -m pytest tests/test_wwise_voice.py -v
"""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bethesda_strings.wwise_voice import (  # noqa: E402
    VoiceIndex,
    classify_archive_language,
    form_id_from_name,
    voice_type_from_name,
)

# Optional real game data for the end-to-end test.
_GAME_DATA = Path(
    "/mnt/ssd/Starfield.Digital.Premium.Edition-InsaneRamZes/Data"
)
_SMALL_VOICE_BA2 = _GAME_DATA / "Starfield - VoicesPatch.ba2"
_VGMSTREAM = "/usr/bin/vgmstream-cli"


# ── classify_archive_language ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    "filename,expected",
    [
        # Base-game English voices carry no language suffix.
        ("Starfield - Voices01.ba2", "en"),
        ("Starfield - Voices02.ba2", "en"),
        ("Starfield - VoicesPatch.ba2", "en"),
        # DLC / patch archives use an explicit _xx suffix.
        ("SFBGS003 - Voices_en.ba2", "en"),
        ("SFBGS003 - Voices_de.ba2", "de"),
        ("SFBGS003 - Voices_es.ba2", "es"),
        ("SFBGS003 - Voices_fr.ba2", "fr"),
        ("SFBGS00D - Voices_ja.ba2", "ja"),
        ("ShatteredSpace - Voices_en.ba2", "en"),
        # Case-insensitive.
        ("STARFIELD - VOICES01.BA2", "en"),
    ],
)
def test_classify_voice_archives(filename, expected):
    assert classify_archive_language(filename) == expected


@pytest.mark.parametrize(
    "filename",
    [
        "Starfield - Textures01.ba2",
        "Starfield - Misc.ba2",
        "Starfield - Meshes01.ba2",
        "Starfield.esm",
        "readme.txt",
    ],
)
def test_classify_non_voice_archives_returns_none(filename):
    assert classify_archive_language(filename) is None


def test_classify_handles_full_paths():
    # A full path, not just a bare filename, still classifies on the basename.
    assert classify_archive_language("/games/Data/Starfield - Voices02.ba2") == "en"
    assert classify_archive_language("C:\\Data\\SFBGS003 - Voices_de.ba2") == "de"


# ── form_id_from_name ───────────────────────────────────────────────────────────

def test_form_id_parsed_from_internal_name():
    name = "sound/voice/starfield.esm/crimsonfleetfemale03/00458e38.wem"
    assert form_id_from_name(name) == 0x00458E38


def test_form_id_uppercase_hex():
    assert form_id_from_name("a/b/000ABCDE.wem") == 0x000ABCDE


def test_form_id_none_for_non_formid_names():
    # Expression clips are named by emotion, not FormID — must not match.
    assert form_id_from_name(
        "sound/voice/starfield.esm/humanfemaleexpressions/afraid.wem"
    ) is None


def test_form_id_none_for_non_wem():
    assert form_id_from_name("sound/voice/starfield.esm/x/00458e38.fuz") is None


# ── voice_type_from_name ────────────────────────────────────────────────────────

def test_voice_type_extracted():
    name = "sound/voice/starfield.esm/crimsonfleetfemale03/00458e38.wem"
    assert voice_type_from_name(name) == "crimsonfleetfemale03"


def test_voice_type_backslash_paths():
    name = "sound\\voice\\starfield.esm\\spacertype01\\00ea450b.wem"
    assert voice_type_from_name(name) == "spacertype01"


def test_voice_type_empty_when_no_folder():
    assert voice_type_from_name("00458e38.wem") == ""


# ── VoiceIndex on a non-existent directory ──────────────────────────────────────

def test_index_missing_dir_builds_empty(tmp_path):
    idx = VoiceIndex(tmp_path / "does_not_exist", language="en")
    assert idx.build() == 0
    assert idx.is_built
    assert idx.count == 0
    assert idx.find(0x12345) == []
    assert idx.voice_types(0x12345) == []
    assert idx.get_wav(0x12345) is None


def test_index_empty_dir_builds_empty(tmp_path):
    idx = VoiceIndex(tmp_path, language="en")
    assert idx.build() == 0
    assert idx.count == 0


# ── End-to-end against real game data (skipped without the game/vgmstream) ───────

_have_game = _SMALL_VOICE_BA2.is_file() and bool(shutil.which(_VGMSTREAM) or Path(_VGMSTREAM).is_file())


@pytest.mark.skipif(not _have_game, reason="Starfield voice BA2 / vgmstream not present")
def test_end_to_end_index_and_decode(tmp_path):
    import wave

    # Point at a dir containing only the small VoicesPatch archive via symlink,
    # so the scan stays fast and deterministic.
    data_dir = tmp_path / "Data"
    data_dir.mkdir()
    (data_dir / _SMALL_VOICE_BA2.name).symlink_to(_SMALL_VOICE_BA2)

    idx = VoiceIndex(
        data_dir,
        vgmstream_binary=_VGMSTREAM,
        cache_dir=tmp_path / "cache",
        language="en",
    )
    n = idx.build()
    assert n > 0
    assert idx.archive_count == 1

    # A FormID known to live in VoicesPatch.
    fid = 0x00458E38
    clips = idx.find(fid)
    assert clips, "expected at least one clip for the known FormID"
    assert "crimsonfleetfemale03" in idx.voice_types(fid)

    wav = idx.get_wav(fid)
    assert wav is not None and wav.exists() and wav.stat().st_size > 0

    # Decoded output is a real, non-empty WAV.
    with wave.open(str(wav)) as w:
        assert w.getnframes() > 0

    # Second call hits the cache and returns the same path.
    assert idx.get_wav(fid) == wav
    idx.close()
