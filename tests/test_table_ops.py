"""
Tests for StringTableModel.clear_translations and apply_to_identical
(the Delete-to-clear and "Apply to All Identical Originals" features).
"""

import pytest

from PySide6.QtWidgets import QApplication

from gui.string_table import StringTableModel


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def _model(rows, _app):
    m = StringTableModel()
    m._data = [
        {"id": i, "original": o, "translated": t, "status": s,
         "length": 0, "offset": 0}
        for i, (o, t, s) in enumerate(rows)
    ]
    return m


# ── clear_translations (Delete key) ──────────────────────────────────────────
def test_clear_reverts_text_and_status(_app):
    m = _model([("Hello", "Привіт", "translated"),
                ("World", "Світ", "translated")], _app)
    n = m.clear_translations([0, 1])
    assert n == 2
    for r in (0, 1):
        assert m._data[r]["translated"] == ""
        assert m._data[r]["status"] == "pending"


def test_clear_skips_already_empty_pending_rows(_app):
    m = _model([("Hello", "", "pending"),
                ("World", "Світ", "translated")], _app)
    n = m.clear_translations([0, 1])
    assert n == 1  # only the translated row counted
    assert m._data[1]["status"] == "pending"


def test_clear_ignores_out_of_range(_app):
    m = _model([("Hello", "Привіт", "translated")], _app)
    assert m.clear_translations([5, -1]) == 0


# ── apply_to_identical (Ctrl+Alt+D) ──────────────────────────────────────────
def test_apply_to_identical_propagates(_app):
    m = _model([
        ("Open", "Відкрити", "translated"),   # source row
        ("Open", "", "pending"),              # same source, empty
        ("Open", "Відчинити", "translated"),  # same source, different translation
        ("Close", "Закрити", "translated"),   # different source
    ], _app)
    n = m.apply_to_identical(0)
    assert n == 2  # rows 1 and 2 updated, row 3 untouched
    assert m._data[1]["translated"] == "Відкрити"
    assert m._data[1]["status"] == "translated"
    assert m._data[2]["translated"] == "Відкрити"
    assert m._data[3]["translated"] == "Закрити"  # unchanged


def test_apply_to_identical_no_translation_is_noop(_app):
    m = _model([("Open", "", "pending"), ("Open", "", "pending")], _app)
    assert m.apply_to_identical(0) == 0


def test_apply_to_identical_no_siblings(_app):
    m = _model([("Unique", "Унікальний", "translated"),
                ("Other", "Інший", "translated")], _app)
    assert m.apply_to_identical(0) == 0


def test_apply_to_identical_skips_rows_already_matching(_app):
    m = _model([("Open", "Відкрити", "translated"),
                ("Open", "Відкрити", "translated")], _app)
    # Row 1 already has the same translation → nothing to change.
    assert m.apply_to_identical(0) == 0
