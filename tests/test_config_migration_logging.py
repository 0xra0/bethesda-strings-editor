"""Tests for what config migration *reports*.

`_migrate_config` used to end with:

    if from_version < CONFIG_VERSION:
        logger.warning("Config version N is older than current M. "
                       "Some settings may use defaults.")

which is the exact condition `AppSettings.from_dict` tests before calling it —
so it fired after every successful migration and told the user their settings
might have been lost when nothing had gone wrong.

Meanwhile the case where that sentence *is* true — a config written by a newer
build, whose unknown keys `from_dict` silently drops — logged nothing at all,
because a newer config never takes the migration path.
"""

import logging

import pytest

from gui.app_settings import CONFIG_VERSION, AppSettings, _migrate_config


def test_successful_migration_does_not_warn(caplog):
    with caplog.at_level(logging.DEBUG, logger="gui.app_settings"):
        _migrate_config({"config_version": CONFIG_VERSION - 1}, CONFIG_VERSION - 1)

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [], f"a clean migration must not warn, got: {[r.getMessage() for r in warnings]}"


def test_successful_migration_reports_what_it_did(caplog):
    with caplog.at_level(logging.INFO, logger="gui.app_settings"):
        _migrate_config({"config_version": 20}, 20)

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"to v{CONFIG_VERSION}" in text


def test_migration_from_the_oldest_config_does_not_warn(caplog):
    """A v1 config walks every step; that is normal, not a problem."""
    with caplog.at_level(logging.DEBUG, logger="gui.app_settings"):
        _migrate_config({"config_version": 1}, 1)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_migration_brings_the_version_up_to_date():
    out = _migrate_config({"config_version": 20}, 20)
    assert out["config_version"] == CONFIG_VERSION


def test_config_from_a_newer_build_warns(caplog):
    """The case the old message was reaching for, which never fired."""
    with caplog.at_level(logging.DEBUG, logger="gui.app_settings"):
        AppSettings.from_dict({"config_version": CONFIG_VERSION + 5})

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "newer version" in warnings[0]


def test_newer_build_warning_names_the_dropped_settings(caplog):
    with caplog.at_level(logging.DEBUG, logger="gui.app_settings"):
        AppSettings.from_dict(
            {
                "config_version": CONFIG_VERSION + 1,
                "a_setting_from_the_future": 1,
                "another_one": "x",
                "quality_level": 5,  # known — must not be reported as dropped
            }
        )

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "a_setting_from_the_future" in warnings[0]
    assert "another_one" in warnings[0]
    assert "quality_level" not in warnings[0]


def test_a_current_config_is_silent(caplog):
    with caplog.at_level(logging.DEBUG, logger="gui.app_settings"):
        AppSettings.from_dict({"config_version": CONFIG_VERSION})

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.parametrize("version", [1, 20, CONFIG_VERSION - 1])
def test_from_dict_migrates_without_warning(caplog, version):
    """The end-to-end path a user actually takes when upgrading."""
    with caplog.at_level(logging.DEBUG, logger="gui.app_settings"):
        settings = AppSettings.from_dict({"config_version": version})

    assert isinstance(settings, AppSettings)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
