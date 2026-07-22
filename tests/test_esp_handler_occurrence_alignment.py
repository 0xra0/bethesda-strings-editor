"""
Tests that ESP extraction and write-back agree on occurrence indices.

A record can repeat the same field signature many times — a WEAP's FULL appears
once for the weapon's own name and again for every OBTE/OBTF modification-grade
label — and `_parse_record` / `_patch_fields` line entries up purely by "the Nth
occurrence of this signature in this record". Two independent ways for those two
counters to drift apart, both of which silently wrote translations into the
wrong fields:

* `_patch_fields` skipped only exactly ``b"\\x00"``, while extraction skipped any
  field that was empty after rstrip **or** looked like a resource path. Both now
  route through `_field_translatable_text`.
* `EspFile.save()` advanced its own counter only for entries that actually
  changed, so an entry deliberately left as-is (a proper noun whose translation
  equals its original) desynced the map from there on.

Hand-built record bytes — no game files.
"""

import struct
from pathlib import Path

from bethesda_strings.esp_handler import (
    EspFile,
    _field_translatable_text,
    _patch_fields,
)


def _field(sig: str, text: str) -> bytes:
    """A single null-terminated text field: sig(4) + size(2 LE) + data."""
    payload = text.encode("utf-8") + b"\x00"
    return sig.encode("ascii") + struct.pack("<H", len(payload)) + payload


def _record(rec_sig: str, form_id: int, body: bytes) -> bytes:
    """A whole top-level record: 24-byte header + field buffer."""
    return (
        rec_sig.encode("ascii")
        + struct.pack("<I", len(body))
        + struct.pack("<I", 0)        # flags
        + struct.pack("<I", form_id)
        + struct.pack("<I", 0)        # timestamp/VC info
        + struct.pack("<H", 0)        # form version
        + struct.pack("<H", 0)        # unknown
        + body
    )


_TES4 = _record("TES4", 0, b"")


def _weap_body() -> bytes:
    """EDID + 4 FULL occurrences, mimicking a real WEAP layout: the weapon's own
    name, a field whose text is really a resource path (never translatable, and
    must survive untouched), then two modification-grade names."""
    return (
        _field("EDID", "TEST_WEAP")
        + _field("FULL", "ARX-15")
        + _field("FULL", "config\\low")
        + _field("FULL", "Standard (Low)")
        + _field("FULL", "Standard (Mid)")
    )


# ── the shared predicate itself ──────────────────────────────────────────────
def test_translatable_text_rejects_empty_variants():
    assert _field_translatable_text(b"", "utf-8") is None
    assert _field_translatable_text(b"\x00", "utf-8") is None
    # Multi-byte null padding passed the old `fdata != b"\x00"` write-back check
    # (it is not a single null byte) though extraction always treated it as empty.
    assert _field_translatable_text(b"\x00\x00\x00", "utf-8") is None


def test_translatable_text_rejects_resource_paths():
    assert _field_translatable_text(b"config\\low\x00", "utf-8") is None
    assert _field_translatable_text(b"arx15.nif\x00", "utf-8") is None


def test_translatable_text_keeps_display_text():
    assert _field_translatable_text(b"ARX-15\x00", "utf-8") == "ARX-15"
    assert _field_translatable_text(b"Standard (Low)\x00", "utf-8") == "Standard (Low)"


# ── extraction vs write-back ─────────────────────────────────────────────────
def test_writeback_skips_the_same_occurrences_extraction_did():
    body = _weap_body()

    esp = EspFile()
    esp._parse_record(b"WEAP", 0x00123456, 0, body, "utf-8")
    assert [e.original for e in esp.strings] == [
        "ARX-15", "Standard (Low)", "Standard (Mid)",
    ]

    trans_map = {
        (0x00123456, "FULL", 0): "[name]",
        (0x00123456, "FULL", 1): "[grade 1]",
        (0x00123456, "FULL", 2): "[grade 2]",
    }
    new_body = bytes(_patch_fields(body, 0x00123456, "WEAP", "utf-8", trans_map, {}))

    esp2 = EspFile()
    esp2._parse_record(b"WEAP", 0x00123456, 0, new_body, "utf-8")
    # Not shifted by the skipped "config\low" occurrence.
    assert [e.original for e in esp2.strings] == ["[name]", "[grade 1]", "[grade 2]"]
    # And the path field is untouched rather than overwritten with a translation.
    assert b"config\\low\x00" in new_body


def test_roundtrip_keeps_each_translation_on_its_own_field(tmp_path: Path):
    src = tmp_path / "weap.esm"
    src.write_bytes(_TES4 + _record("WEAP", 0x00123456, _weap_body()))

    esp = EspFile()
    esp.load(src, encoding="utf-8")
    assert esp.is_localized is False

    by_text = {e.original: e for e in esp.strings}
    assert set(by_text) == {"ARX-15", "Standard (Low)", "Standard (Mid)"}
    by_text["ARX-15"].translation = "[name]"
    by_text["Standard (Low)"].translation = "[grade 1]"
    by_text["Standard (Mid)"].translation = "[grade 2]"

    out = tmp_path / "weap_translated.esm"
    esp.save(out, encoding="utf-8")

    esp2 = EspFile()
    esp2.load(out, encoding="utf-8")
    # Without the fix the weapon's own name slot holds a grade label instead.
    assert [e.original for e in esp2.strings] == ["[name]", "[grade 1]", "[grade 2]"]


# ── save()'s counter must advance for untranslated entries too ───────────────
def test_save_counter_advances_for_entries_left_untranslated(tmp_path: Path):
    body = (
        _field("EDID", "TEST_WEAP")
        + _field("FULL", "ARX-15")        # proper noun, kept as-is
        + _field("FULL", "Fallback")
        + _field("FULL", "Standard Low")
        + _field("FULL", "Standard Med")
    )
    src = tmp_path / "weap.esm"
    src.write_bytes(_TES4 + _record("WEAP", 0x01000806, body))

    esp = EspFile()
    esp.load(src, encoding="utf-8")

    by_text = {e.original: e for e in esp.strings}
    by_text["ARX-15"].translation = "ARX-15"        # translation == original
    by_text["Fallback"].translation = "[fallback]"
    by_text["Standard Low"].translation = "[low]"
    by_text["Standard Med"].translation = "[med]"

    out = tmp_path / "weap_translated.esm"
    esp.save(out, encoding="utf-8")

    esp2 = EspFile()
    esp2.load(out, encoding="utf-8")
    # Without the fix this comes back as ["[fallback]", "[low]", "[med]"] —
    # "ARX-15" overwritten and "Standard Med" shifted off the end untranslated.
    assert [e.original for e in esp2.strings] == [
        "ARX-15", "[fallback]", "[low]", "[med]",
    ]
