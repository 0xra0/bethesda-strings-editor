"""Tests for the translation-language setting: one table, and locale codes.

`default_source_lang` / `default_target_lang` hold a **Starfield locale code**
("uk"), never a display name ("Ukrainian").  Config v20 made that the contract;
the Settings dialog never adopted it, and kept a private four-name list whose
combo items carried the display name as their data.  Three things went wrong at
once, and each has a test here:

1. The dialog offered 4 of the 12 languages the main window offers.
2. Saving wrote "Ukrainian" over "uk", silently undoing v20 — every consumer
   (`_TARGET_STYLE`, `_GENDERED_TARGETS`, `preload_language_dictionaries`) keys
   off the code and ignores an unknown value rather than rejecting it.
3. Because the dialog looked the *code* up with findData() against *name* data,
   the combo matched nothing and sat at index -1 — so a user who opened Settings
   and pressed Save without touching the language rows wrote **None**, which
   later raised `TypeError: 'NoneType' object is not subscriptable` in the TMX
   loader on `default_source_lang[:2]`.

The Qt tests need a QApplication but no display (CI runs them offscreen).
"""

import pytest

from gui.app_settings import (
    LANGUAGE_CODES,
    LANGUAGE_NAME_TO_CODE,
    SUPPORTED_LANGUAGES,
    AppSettings,
    _migrate_config,
)


# ── The table itself ─────────────────────────────────────────────────────────

def test_every_language_has_a_display_name_and_a_code():
    assert SUPPORTED_LANGUAGES, "the language table must not be empty"
    for entry in SUPPORTED_LANGUAGES:
        name, code = entry  # also asserts the 2-tuple shape
        assert name and name.strip() == name
        assert code and code.islower() and code.isalpha()


def test_codes_and_names_are_unique():
    names = [name for name, _ in SUPPORTED_LANGUAGES]
    codes = [code for _, code in SUPPORTED_LANGUAGES]
    assert len(set(names)) == len(names)
    assert len(set(codes)) == len(codes)


def test_the_defaults_are_offered():
    """A default the picker cannot show is how the combo lands at index -1."""
    defaults = AppSettings()
    assert defaults.default_source_lang in LANGUAGE_CODES
    assert defaults.default_target_lang in LANGUAGE_CODES


def test_name_to_code_map_covers_the_table():
    assert LANGUAGE_NAME_TO_CODE == dict(SUPPORTED_LANGUAGES)
    assert LANGUAGE_CODES == {code for _, code in SUPPORTED_LANGUAGES}


# ── The two tables are one table ─────────────────────────────────────────────

def test_main_window_and_settings_dialog_offer_the_same_languages():
    """The regression guard: these were two private lists, 12 vs 4 entries."""
    pytest.importorskip("PySide6")
    from gui.main_window import MainWindow
    from gui.settings_dialog import SettingsDialog

    assert MainWindow.SUPPORTED_LANGUAGES == SUPPORTED_LANGUAGES
    assert SettingsDialog.SUPPORTED_LANGUAGES == SUPPORTED_LANGUAGES
    assert MainWindow.SUPPORTED_LANGUAGES == SettingsDialog.SUPPORTED_LANGUAGES


# ── The dialog stores codes, not display names ───────────────────────────────

@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def _dialog(qapp, settings):
    from gui.settings_dialog import SettingsDialog

    return SettingsDialog(settings)


def test_dialog_offers_every_language(qapp):
    dlg = _dialog(qapp, AppSettings())
    assert dlg.combo_source.count() == len(SUPPORTED_LANGUAGES)
    assert dlg.combo_target.count() == len(SUPPORTED_LANGUAGES)


def test_dialog_item_data_is_the_locale_code(qapp):
    """Item data used to be the display name, so findData(code) found nothing."""
    dlg = _dialog(qapp, AppSettings())
    for combo in (dlg.combo_source, dlg.combo_target):
        data = {combo.itemData(i) for i in range(combo.count())}
        assert data == LANGUAGE_CODES


def test_dialog_preselects_the_saved_language(qapp):
    settings = AppSettings()
    settings.default_source_lang = "en"
    settings.default_target_lang = "pl"
    dlg = _dialog(qapp, settings)

    # The bug showed as a blank combo: findData() returned -1.
    assert dlg.combo_source.currentIndex() >= 0
    assert dlg.combo_target.currentIndex() >= 0
    assert dlg.combo_source.currentData() == "en"
    assert dlg.combo_target.currentData() == "pl"


@pytest.mark.parametrize("source,target", [("ru", "uk"), ("en", "de"), ("en", "zhhans")])
def test_round_trip_preserves_the_code(qapp, source, target):
    """Open Settings, save without touching anything: the codes must survive."""
    settings = AppSettings()
    settings.default_source_lang = source
    settings.default_target_lang = target

    dlg = _dialog(qapp, settings)
    out = AppSettings()
    dlg.apply_to_settings(out)

    assert out.default_source_lang == source
    assert out.default_target_lang == target


def test_save_never_writes_a_display_name(qapp):
    """The v20 regression: "Ukrainian" where "uk" belongs."""
    dlg = _dialog(qapp, AppSettings())
    out = AppSettings()
    dlg.apply_to_settings(out)

    assert out.default_source_lang in LANGUAGE_CODES
    assert out.default_target_lang in LANGUAGE_CODES
    assert out.default_source_lang not in LANGUAGE_NAME_TO_CODE
    assert out.default_target_lang not in LANGUAGE_NAME_TO_CODE


def test_save_never_writes_none_even_with_an_unknown_setting(qapp):
    """An unmatched combo sits at -1, and currentData() is None there.

    None is the value that reached `default_source_lang[:2]` in the TMX loader
    and raised TypeError, so this asserts the type, not just the truthiness.
    """
    settings = AppSettings()
    settings.default_source_lang = "klingon"   # not in the table
    settings.default_target_lang = "klingon"

    dlg = _dialog(qapp, settings)
    out = AppSettings()
    dlg.apply_to_settings(out)

    for value in (out.default_source_lang, out.default_target_lang):
        assert isinstance(value, str) and value
        value[:2].lower()  # the call site that used to raise


# ── Migration v43 repairs configs the dialog already corrupted ───────────────

def test_migration_maps_display_names_to_codes():
    data = {"default_source_lang": "English", "default_target_lang": "Ukrainian"}
    out = _migrate_config(dict(data), from_version=42)
    assert out["default_source_lang"] == "en"
    assert out["default_target_lang"] == "uk"


def test_migration_replaces_none_with_the_default():
    out = _migrate_config(
        {"default_source_lang": None, "default_target_lang": None}, from_version=42
    )
    assert out["default_source_lang"] == "ru"
    assert out["default_target_lang"] == "uk"
    assert isinstance(out["default_source_lang"], str)


def test_migration_replaces_an_unknown_value():
    out = _migrate_config(
        {"default_source_lang": "klingon", "default_target_lang": ""}, from_version=42
    )
    assert out["default_source_lang"] in LANGUAGE_CODES
    assert out["default_target_lang"] in LANGUAGE_CODES


def test_migration_leaves_a_valid_code_alone():
    out = _migrate_config(
        {"default_source_lang": "de", "default_target_lang": "ptbr"}, from_version=42
    )
    assert out["default_source_lang"] == "de"
    assert out["default_target_lang"] == "ptbr"


def test_migration_repairs_every_display_name_in_the_table():
    for name, code in SUPPORTED_LANGUAGES:
        out = _migrate_config({"default_target_lang": name}, from_version=42)
        assert out["default_target_lang"] == code


def test_v20_still_maps_names_for_a_much_older_config():
    """v20 did this first; it must keep working from its own version."""
    out = _migrate_config({"default_target_lang": "Polish"}, from_version=19)
    assert out["default_target_lang"] == "pl"
