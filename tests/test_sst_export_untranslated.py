"""
SST XML export must never emit an empty ``<Dest>``.

Background — ``starfield_ru.STRINGS.xml``, exported from a real ru→uk run, carried
343 entries shaped like this:

    <String List="0" sID="00A22C">
      <Source>Брэдбери I-b</Source>
      <Dest></Dest>
    </String>

An SST file is a patch dictionary: an entry *claims* a finished translation for
that sID.  An empty ``<Dest>`` therefore does not mean "not translated yet", it
means "translate this to the empty string" — and the game draws nothing where the
planet name should be.  That is what made EMPTY_TRANSLATION an error-severity code
(``quality_checker.SEVERITY_ERROR`` — "Will break in game") and 343 of the 344
errors in ``quality_report_20260810_093056``.

Two things in the same repository already had the right policy and were not
consulted:

  * ``XMLHandler.parse_sst_xml`` *skips* entries with no ``<Dest>`` — the reader
    has always treated them as absent.
  * ``StringTableModel.apply_changes_to_file`` (the binary ``.strings`` save path)
    falls back to the original text for untranslated rows, so the same project
    saved as ``.strings`` was fine and saved as ``.xml`` was not.

Omitting the entry restores symmetry with the reader and cannot render blank: with
no entry for that sID, the game keeps what it already had.

Synthetic rows written to ``tmp_path``; no Qt, no game files.

Run with:
    python -m pytest tests/test_sst_export_untranslated.py -v
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import xml.etree.ElementTree as ET  # noqa: E402

import pytest  # noqa: E402

from bethesda_strings import XMLHandler  # noqa: E402


ROWS = [
    {"id": 0x000016, "original": 'Диалог сотрудников "Чанкса"', "translated": "Діалоги працівників «Чанкса»"},
    {"id": 0x00A22C, "original": "Брэдбери I-b",                "translated": ""},       # never translated
    {"id": 0x00A417, "original": "Брэдбери I-a",                "translated": "   "},     # whitespace only
    {"id": 0x0085AC, "original": "Станция Хаб",                 "translated": None},      # missing key
    {"id": 0x000030, "original": "[LC024 - Убежище Первого]",   "translated": "[LC024 - Прихисток Першого]"},
]


def _write(tmp_path, rows=ROWS):
    out = tmp_path / "export.xml"
    written = XMLHandler.write_sst_xml(str(out), rows, "ru", "uk")
    return out, written


def _dests(path):
    root = ET.parse(path).getroot()
    return {
        node.get("sID"): (node.findtext("Dest") or "")
        for node in root.iter("String")
    }


def test_no_entry_carries_an_empty_dest(tmp_path):
    out, _ = _write(tmp_path)
    for sid, dest in _dests(out).items():
        assert dest.strip(), f"sID {sid} exported with a blank <Dest>"


def test_untranslated_rows_are_omitted(tmp_path):
    out, _ = _write(tmp_path)
    assert set(_dests(out)) == {"000016", "000030"}


def test_translated_rows_survive_intact(tmp_path):
    out, _ = _write(tmp_path)
    dests = _dests(out)
    assert dests["000016"] == "Діалоги працівників «Чанкса»"
    assert dests["000030"] == "[LC024 - Прихисток Першого]"


def test_write_reports_what_it_skipped(tmp_path):
    """The caller has to be able to tell the user; silently dropping rows would
    trade one invisible failure for another."""
    _, written = _write(tmp_path)
    assert written == 2


def test_round_trip_is_exact(tmp_path):
    """The reader skips blank <Dest> entries, so omitting them on write makes
    write→parse lossless: what comes back is what went out."""
    out, _ = _write(tmp_path)
    parsed = XMLHandler.parse_sst_xml(str(out))
    assert parsed.by_id == {
        0x000016: "Діалоги працівників «Чанкса»",
        0x000030: "[LC024 - Прихисток Першого]",
    }


def test_all_untranslated_writes_a_valid_empty_file(tmp_path):
    out, written = _write(tmp_path, [{"id": 1, "original": "Брэдбери I", "translated": ""}])
    assert written == 0
    assert list(ET.parse(out).getroot().iter("String")) == []


def test_rows_without_an_id_are_still_skipped(tmp_path):
    out, written = _write(tmp_path, [{"id": None, "original": "x", "translated": "у"}])
    assert written == 0
