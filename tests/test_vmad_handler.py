"""
Tests for VMAD (Papyrus script property) parsing, risk classification and the
safe byte-splice editor (bethesda_strings/vmad_handler.py), plus the ESP-level
scan / apply round-trip (bethesda_strings/esp_handler.py).

All buffers are built synthetically — no game files required.

Run with:
    python -m pytest tests/test_vmad_handler.py -v
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from bethesda_strings import vmad_handler as V  # noqa: E402
from bethesda_strings.esp_handler import (  # noqa: E402
    apply_vmad_translations,
    scan_vmad,
)


# ── Synthetic VMAD / record builders ─────────────────────────────────────────────

def _wstr(s: str) -> bytes:
    b = s.encode("utf-8")
    return struct.pack("<H", len(b)) + b


def _value(ptype: int, value) -> bytes:
    if ptype == V._T_OBJECT:
        return b"\x00" * 8
    if ptype == V._T_STRING:
        return _wstr(value)
    if ptype == V._T_INT32:
        return struct.pack("<i", value)
    if ptype == V._T_FLOAT:
        return struct.pack("<f", value)
    if ptype == V._T_BOOL:
        return struct.pack("<B", 1 if value else 0)
    if ptype == V._T_ARR_OBJECT:
        return struct.pack("<I", len(value)) + b"".join(b"\x00" * 8 for _ in value)
    if ptype == V._T_ARR_STRING:
        return struct.pack("<I", len(value)) + b"".join(_wstr(v) for v in value)
    if ptype == V._T_ARR_INT32:
        return struct.pack("<I", len(value)) + b"".join(struct.pack("<i", v) for v in value)
    raise ValueError(f"unsupported test type {ptype}")


def build_vmad(scripts, version: int = 6, object_format: int = 2) -> bytes:
    """scripts: list of (script_name, [(prop_name, type, value), ...])."""
    out = struct.pack("<hhH", version, object_format, len(scripts))
    for sname, props in scripts:
        out += _wstr(sname)
        if version >= 4:
            out += b"\x00"  # script status
        out += struct.pack("<H", len(props))
        for pname, ptype, value in props:
            out += _wstr(pname)
            out += struct.pack("<B", ptype)
            if version >= 4:
                out += b"\x00"  # property status
            out += _value(ptype, value)
    return out


def _field(sig: bytes, payload: bytes) -> bytes:
    assert len(payload) <= 0xFFFF
    return sig + struct.pack("<H", len(payload)) + payload


def _record(sig: bytes, form_id: int, fields: bytes, flags: int = 0) -> bytes:
    header = (
        sig + struct.pack("<I", len(fields)) + struct.pack("<I", flags)
        + struct.pack("<I", form_id) + b"\x00" * 8  # timestamp/vc/version/unknown
    )
    return header + fields


def _tes4() -> bytes:
    hedr = _field(b"HEDR", struct.pack("<fiI", 0.96, 0, 0x800))
    return _record(b"TES4", 0, hedr)


# ── parse: extraction of string properties ───────────────────────────────────────

def test_parse_extracts_scalar_string():
    buf = build_vmad([
        ("MyScript", [
            ("ButtonText", V._T_STRING, "Press to open"),
            ("SomeInt", V._T_INT32, 42),
        ]),
    ])
    info = V.parse_vmad(buf)
    assert info.fully_parsed is True
    assert info.version == 6
    assert info.script_names == ["MyScript"]
    assert len(info.strings) == 1
    assert info.strings[0].value == "Press to open"
    assert info.strings[0].prop_name == "ButtonText"
    assert info.strings[0].array_index == -1


def test_parse_skips_non_string_types():
    buf = build_vmad([
        ("S", [
            ("Obj", V._T_OBJECT, None),
            ("Flag", V._T_BOOL, True),
            ("Amount", V._T_FLOAT, 1.5),
            ("Refs", V._T_ARR_OBJECT, [None, None, None]),
            ("Nums", V._T_ARR_INT32, [1, 2]),
        ]),
    ])
    info = V.parse_vmad(buf)
    assert info.fully_parsed is True
    assert info.strings == []


def test_parse_array_of_strings():
    buf = build_vmad([
        ("S", [
            ("Options", V._T_ARR_STRING, ["Yes please", "No thanks", "Maybe later"]),
        ]),
    ])
    info = V.parse_vmad(buf)
    assert info.fully_parsed is True
    assert [s.value for s in info.strings] == ["Yes please", "No thanks", "Maybe later"]
    assert [s.array_index for s in info.strings] == [0, 1, 2]


def test_parse_version2_has_no_status_bytes():
    buf = build_vmad(
        [("S", [("Msg", V._T_STRING, "Hello there")])],
        version=2,
    )
    info = V.parse_vmad(buf)
    assert info.fully_parsed is True
    assert info.version == 2
    assert info.strings[0].value == "Hello there"


def test_parse_unknown_type_stops_gracefully():
    # Build a valid string property, then a property with a bogus type.
    head = struct.pack("<hhH", 6, 2, 1)
    body = _wstr("S") + b"\x00" + struct.pack("<H", 2)
    body += _wstr("Good") + struct.pack("<B", V._T_STRING) + b"\x00" + _wstr("Visible text")
    body += _wstr("Bad") + struct.pack("<B", 99) + b"\x00" + b"\xde\xad"
    info = V.parse_vmad(head + body)
    assert info.fully_parsed is False
    # The string decoded before the bad type is still captured.
    assert any(s.value == "Visible text" for s in info.strings)


def test_parse_truncated_buffer_no_crash():
    buf = build_vmad([("S", [("T", V._T_STRING, "abc")])])
    info = V.parse_vmad(buf[:-2])  # chop the value short
    assert info.fully_parsed is False


def test_parse_empty():
    info = V.parse_vmad(b"")
    assert info.strings == []
    assert info.fully_parsed is False


# ── risk classification ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,value", [
    ("TexturePath", "textures/ui/foo.dds"),
    ("AnyName", "data\\meshes\\thing.nif"),
    ("Resource", "scripts/MyScript.pex"),
    ("Whatever", "ButtonClicked.swf"),
])
def test_classify_paths_locked(name, value):
    risk, _ = V.classify_string_property(name, value)
    assert risk == V.RISK_LOCKED


@pytest.mark.parametrize("name,value", [
    ("EventName", "OnActivate"),
    ("KeywordEdid", "WeaponTypeLaser"),
    ("ScriptVar", "MQ101_StartStage"),
])
def test_classify_identifier_names_locked(name, value):
    risk, _ = V.classify_string_property(name, value)
    assert risk == V.RISK_LOCKED


def test_classify_empty_locked():
    risk, _ = V.classify_string_property("Anything", "   ")
    assert risk == V.RISK_LOCKED


def test_classify_text_property_translatable():
    risk, _ = V.classify_string_property("ButtonText", "Continue")
    assert risk == V.RISK_TRANSLATABLE


def test_classify_phrase_translatable():
    risk, _ = V.classify_string_property("Custom01", "Welcome to the New Atlantis spaceport")
    assert risk == V.RISK_TRANSLATABLE


def test_classify_single_token_identifier_locked():
    risk, _ = V.classify_string_property("Custom01", "MyEventName")
    assert risk == V.RISK_LOCKED


def test_classify_single_plain_word_review():
    risk, _ = V.classify_string_property("Custom01", "metal")
    assert risk == V.RISK_REVIEW


# ── safe splice (replace_strings) ────────────────────────────────────────────────

def test_replace_grows_and_round_trips():
    buf = build_vmad([
        ("S", [
            ("A", V._T_STRING, "old"),
            ("Keep", V._T_INT32, 7),
            ("B", V._T_STRING, "second value here"),
        ]),
    ])
    new = V.replace_strings(buf, {0: "a much longer replacement string"})
    info = V.parse_vmad(new)
    assert info.fully_parsed is True
    assert info.strings[0].value == "a much longer replacement string"
    assert info.strings[1].value == "second value here"  # untouched


def test_replace_shrinks():
    buf = build_vmad([("S", [("A", V._T_STRING, "a long original value")])])
    new = V.replace_strings(buf, {0: "hi"})
    info = V.parse_vmad(new)
    assert info.strings[0].value == "hi"
    assert info.fully_parsed is True


def test_replace_multiple_at_once():
    buf = build_vmad([
        ("S", [
            ("A", V._T_STRING, "one"),
            ("B", V._T_STRING, "two"),
            ("C", V._T_STRING, "three"),
        ]),
    ])
    new = V.replace_strings(buf, {0: "FIRST", 2: "THIRD"})
    info = V.parse_vmad(new)
    assert [s.value for s in info.strings] == ["FIRST", "two", "THIRD"]


def test_replace_preserves_trailing_bytes():
    # Append a fake "fragment" tail the parser never models; it must survive.
    buf = build_vmad([("S", [("A", V._T_STRING, "abc")])])
    tail = b"\xca\xfe\xba\xbe fragment-like trailing data"
    edited = V.replace_strings(buf + tail, {0: "xyz"})
    assert edited.endswith(tail)


def test_replace_no_edits_returns_same():
    buf = build_vmad([("S", [("A", V._T_STRING, "abc")])])
    assert V.replace_strings(buf, {}) == buf
    assert V.replace_strings(buf, {99: "ignored"}) == buf


def test_replace_array_element():
    buf = build_vmad([("S", [("Opts", V._T_ARR_STRING, ["x", "y", "z"])])])
    new = V.replace_strings(buf, {1: "MIDDLE"})
    info = V.parse_vmad(new)
    assert [s.value for s in info.strings] == ["x", "MIDDLE", "z"]


def test_replace_cp1251_encoding():
    buf = build_vmad([("S", [("Msg", V._T_STRING, "Hello")])])
    new = V.replace_strings(buf, {0: "Привіт"}, encoding="cp1251")
    info = V.parse_vmad(new, encoding="cp1251")
    assert info.strings[0].value == "Привіт"


# ── find_vmad_field ──────────────────────────────────────────────────────────────

def test_find_vmad_field():
    vmad = build_vmad([("S", [("T", V._T_STRING, "text")])])
    body = _field(b"EDID", b"MyForm\x00") + _field(b"VMAD", vmad) + _field(b"FULL", b"Name\x00")
    found = V.find_vmad_field(body)
    assert found == vmad


def test_find_vmad_field_absent():
    body = _field(b"EDID", b"X\x00") + _field(b"FULL", b"Y\x00")
    assert V.find_vmad_field(body) is None


# ── ESP-level scan / apply round-trip ────────────────────────────────────────────

def _build_esp(record_bytes: bytes, wrap_grup: bool = False) -> bytes:
    data = _tes4()
    if wrap_grup:
        grup_body = record_bytes
        grup = (
            b"GRUP" + struct.pack("<I", 24 + len(grup_body))
            + b"MESG" + struct.pack("<I", 0) + b"\x00" * 8
        )
        data += grup + grup_body
    else:
        data += record_bytes
    return data


def _make_record(form_id=0x00001234, flags=0):
    vmad = build_vmad([
        ("TerminalScript", [
            ("ButtonText", V._T_STRING, "Open the door"),
            ("KeywordEdid", V._T_STRING, "MyKeyword01"),
            ("AssetPath", V._T_STRING, "textures/x.dds"),
        ]),
    ])
    fields = _field(b"EDID", b"MyTerminal\x00") + _field(b"VMAD", vmad)
    return _record(b"MESG", form_id, fields, flags=flags)


def test_scan_vmad_top_level_record(tmp_path):
    p = tmp_path / "plugin.esp"
    p.write_bytes(_build_esp(_make_record()))
    result = scan_vmad(p)
    assert result.records_with_vmad == 1
    assert len(result.entries) == 3
    by_prop = {e.prop_name: e for e in result.entries}
    assert by_prop["ButtonText"].risk == V.RISK_TRANSLATABLE
    assert by_prop["KeywordEdid"].risk == V.RISK_LOCKED
    assert by_prop["AssetPath"].risk == V.RISK_LOCKED
    assert by_prop["ButtonText"].edid == "MyTerminal"
    assert by_prop["ButtonText"].form_id == 0x00001234


def test_scan_vmad_inside_grup(tmp_path):
    p = tmp_path / "plugin.esp"
    p.write_bytes(_build_esp(_make_record(), wrap_grup=True))
    result = scan_vmad(p)
    assert result.records_with_vmad == 1
    assert len(result.entries) == 3


def test_apply_vmad_round_trip_top_level(tmp_path):
    p = tmp_path / "plugin.esp"
    p.write_bytes(_build_esp(_make_record()))
    result = scan_vmad(p)
    target = next(e for e in result.entries if e.prop_name == "ButtonText")

    backup = apply_vmad_translations(
        p, {(target.form_id, target.vmad_index): "Відчинити двері"},
        encoding="cp1251",
    )
    assert backup is not None and backup.exists()

    again = scan_vmad(p, encoding="cp1251")
    by_prop = {e.prop_name: e for e in again.entries}
    assert by_prop["ButtonText"].original == "Відчинити двері"
    # Other properties untouched.
    assert by_prop["KeywordEdid"].original == "MyKeyword01"
    assert by_prop["AssetPath"].original == "textures/x.dds"


def test_apply_vmad_round_trip_in_grup(tmp_path):
    p = tmp_path / "plugin.esp"
    p.write_bytes(_build_esp(_make_record(), wrap_grup=True))
    result = scan_vmad(p)
    target = next(e for e in result.entries if e.prop_name == "ButtonText")

    apply_vmad_translations(p, {(target.form_id, target.vmad_index): "Much longer translated caption"})

    again = scan_vmad(p)
    by_prop = {e.prop_name: e for e in again.entries}
    assert by_prop["ButtonText"].original == "Much longer translated caption"
    # The GRUP must still parse correctly after the size change → 3 entries again.
    assert len(again.entries) == 3


def test_apply_vmad_compressed_record(tmp_path):
    import zlib
    vmad = build_vmad([("S", [("Caption", V._T_STRING, "Original caption text")])])
    fields = _field(b"EDID", b"C\x00") + _field(b"VMAD", vmad)
    decompressed = fields
    body = struct.pack("<I", len(decompressed)) + zlib.compress(decompressed)
    rec = _record(b"MESG", 0x00005678, body, flags=0x00040000)  # compressed flag
    p = tmp_path / "plugin.esp"
    p.write_bytes(_build_esp(rec))

    result = scan_vmad(p)
    assert len(result.entries) == 1
    target = result.entries[0]
    assert target.original == "Original caption text"

    apply_vmad_translations(p, {(target.form_id, target.vmad_index): "New caption"})
    again = scan_vmad(p)
    assert again.entries[0].original == "New caption"


def test_apply_vmad_no_matching_edits_is_noop(tmp_path):
    p = tmp_path / "plugin.esp"
    original = _build_esp(_make_record())
    p.write_bytes(original)
    # Edit targets a form_id that doesn't exist → file unchanged.
    apply_vmad_translations(p, {(0xDEADBEEF, 0): "ignored"}, make_backup=False)
    assert p.read_bytes() == original
