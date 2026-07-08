"""
Tests for ESP extraction-correctness additions:

* GPOF/GPOG (GameplayOption Form/Group) NNAM fields are recognised as translatable.
* A resource-path safety filter skips asset paths that masquerade as text
  (e.g. a DOOR/CNAM value that is really an animation/marker path).
* A synthetic anti-hallucination context note is attached to QUST/FULL quest
  titles when the record has no real NLDT developer note.

Pure-function + direct `_parse_record` tests with hand-built record bytes — no
game files.
"""

import struct

from bethesda_strings.esp_handler import (
    EspFile,
    _field_list_index,
    _looks_like_resource_path,
)


def _field(sig: bytes, text: str) -> bytes:
    """A single null-terminated text field: sig(4) + size(2 LE) + data."""
    payload = text.encode("utf-8") + b"\x00"
    return sig + struct.pack("<H", len(payload)) + payload


def _parse(rec_sig: bytes, body: bytes):
    esp = EspFile()
    esp._parse_record(rec_sig, 0x00001234, 0, body, "utf-8")
    return esp.strings


# ── resource-path filter ─────────────────────────────────────────────────────
def test_looks_like_resource_path_positive():
    for p in [
        r"meshes\\door\\anim.nif",
        r"Effects\\Blood.dds",
        "animation.hkx",
        "voice.wem",
        r"weapons\\rifle",  # backslash alone
    ]:
        assert _looks_like_resource_path(p), p


def test_looks_like_resource_path_negative():
    for t in [
        "Open the door",
        "New Atlantis",
        "Mining Equipment",
        "Close",
        "",
        "U.S.S. Nova",         # spaces, no asset ext
        "It costs 5 credits.",
        "readme",              # no extension
    ]:
        assert not _looks_like_resource_path(t), t


# ── GPOF/GPOG field recognition ──────────────────────────────────────────────
def test_gpof_gpog_nnam_are_translatable():
    assert _field_list_index("NNAM", "GPOF") is not None
    assert _field_list_index("NNAM", "GPOG") is not None


def test_gpof_nnam_is_extracted():
    strings = _parse(b"GPOF", _field(b"NNAM", "Difficulty"))
    assert len(strings) == 1
    assert strings[0].original == "Difficulty"
    assert strings[0].record_sig == "GPOF"
    assert strings[0].field_sig == "NNAM"


# ── QUST/FULL synthetic context ──────────────────────────────────────────────
def test_qust_full_gets_synthetic_context_note():
    strings = _parse(b"QUST", _field(b"FULL", "The Old Neighborhood"))
    assert len(strings) == 1
    assert "quest title" in strings[0].context_note.lower()


def test_non_qust_full_has_no_synthetic_note():
    strings = _parse(b"WEAP", _field(b"FULL", "Laser Rifle"))
    assert len(strings) == 1
    assert strings[0].context_note == ""


def test_real_nldt_note_overrides_synthetic():
    body = _field(b"NLDT", "Developer note: greeting used at the docks") + _field(b"FULL", "Docks")
    strings = _parse(b"QUST", body)
    assert len(strings) == 1
    assert "Developer note" in strings[0].context_note
    assert "quest title" not in strings[0].context_note.lower()


# ── DOOR/CNAM path filtering ─────────────────────────────────────────────────
def test_door_cnam_resource_path_is_skipped():
    strings = _parse(b"DOOR", _field(b"CNAM", r"meshes\\interior\\door01.nif"))
    assert strings == []


def test_door_cnam_real_text_is_kept():
    strings = _parse(b"DOOR", _field(b"CNAM", "Close the airlock"))
    assert len(strings) == 1
    assert strings[0].original == "Close the airlock"
