"""
Tests for desktop/file-manager registration (`gui.file_associations`).

The module is split into *plan* (pure functions describing what would be
written) and *apply* (the side effects), which is what lets the Windows
registry layout be checked on a Linux CI box — the plan is data, and getting it
wrong is silent: a wrong ProgID or a `%1` that is not quoted produces an entry
Explorer accepts and then fails to launch, with nothing logged anywhere.

The Linux half is exercised end to end against a temporary `$XDG_DATA_HOME`, so
it touches nothing real.

No Qt, no registry, no root.
"""

import sys
from pathlib import Path

import pytest

from gui import file_associations as fa


# ── file-type table ──────────────────────────────────────────────────────────
def test_every_handled_extension_has_exactly_one_type():
    seen: dict[str, str] = {}
    for ft in fa.FILE_TYPES:
        for ext in ft.extensions:
            assert ext not in seen, f"{ext} claimed by {seen.get(ext)} and {ft.prog_id}"
            seen[ext] = ft.prog_id
            assert ext.startswith("."), ext
    assert set(seen) == {
        ".strings", ".dlstrings", ".ilstrings", ".esp", ".esm", ".esl", ".ba2",
    }


def test_icon_name_is_the_mime_type_with_the_slash_dashed():
    # This is the name a file manager looks up when the MIME XML's <icon> misses;
    # any other spelling silently yields the generic page icon.
    assert fa.FILE_TYPES[0].mime == "application/x-bethesda-strings"
    assert fa.FILE_TYPES[0].icon_name == "application-x-bethesda-strings"


def test_mime_types_match_the_packaged_xml():
    xml = (Path(__file__).resolve().parent.parent
           / "packaging" / "bethesda-strings-editor-mime.xml").read_text(encoding="utf-8")
    for ft in fa.FILE_TYPES:
        assert f'<mime-type type="{ft.mime}">' in xml


# ── launch command ───────────────────────────────────────────────────────────
def test_launch_argv_from_source_runs_main_py(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    argv = fa.launch_argv()
    assert len(argv) == 2
    assert Path(argv[1]).name == "main.py"


def test_launch_argv_frozen_is_the_binary_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/opt/bse/bethesda-strings-editor")
    assert fa.launch_argv() == ["/opt/bse/bethesda-strings-editor"]


# ── Windows plan ─────────────────────────────────────────────────────────────
def test_windows_command_quotes_the_file_placeholder():
    # An unquoted %1 breaks on every path with a space — i.e. most of them.
    cmd = fa.windows_command()
    assert cmd.endswith('"%1"')
    assert cmd.count('"%1"') == 1


def test_windows_plan_registers_progid_icon_and_command():
    plan = fa.windows_registry_plan("CMD", "ICON")
    keys = {subkey for subkey, _, _ in plan}
    for ft in fa.FILE_TYPES:
        assert ft.prog_id in keys
        assert f"{ft.prog_id}\\DefaultIcon" in keys
        assert f"{ft.prog_id}\\shell\\open\\command" in keys
    assert ("BethesdaStringsEditor.Strings\\shell\\open\\command", "", "CMD") in plan
    assert ("BethesdaStringsEditor.Strings\\DefaultIcon", "", "ICON") in plan


def test_windows_plan_lists_every_extension_under_open_with():
    plan = fa.windows_registry_plan("CMD", "ICON")
    open_with = {
        (subkey.split("\\")[0], name)
        for subkey, name, _ in plan if subkey.endswith("OpenWithProgids")
    }
    expected = {(ext, ft.prog_id) for ft in fa.FILE_TYPES for ext in ft.extensions}
    assert open_with == expected


def test_windows_plan_claims_the_default_handler_only_when_asked():
    # Windows 10+ guards a user-chosen default behind a UserChoice hash, and
    # quietly taking .esp from a modding tool the user picked is worse than not
    # being the default — so it must never happen by accident.
    def defaults(plan):
        return {subkey for subkey, name, _ in plan
                if name == "" and subkey.startswith(".")}

    assert defaults(fa.windows_registry_plan("C", "I")) == set()
    assert defaults(fa.windows_registry_plan("C", "I", set_default=True)) == {
        ".strings", ".dlstrings", ".ilstrings", ".esp", ".esm", ".esl", ".ba2",
    }


# ── desktop entry ────────────────────────────────────────────────────────────
def test_desktop_entry_rewrites_exec_and_icon_and_keeps_the_rest():
    template = (
        "[Desktop Entry]\n"
        "Name=Bethesda Strings Editor\n"
        "Exec=/usr/bin/bethesda-strings-editor %f\n"   # distro placeholder
        "Icon=whatever\n"
        "MimeType=application/x-bethesda-strings;\n"
    )
    out = fa.desktop_entry_text(template, "/opt/bse/bethesda-strings-editor")
    assert "Exec=/opt/bse/bethesda-strings-editor %f\n" in out
    assert f"Icon={fa.ICON_NAME}\n" in out
    assert "MimeType=application/x-bethesda-strings;\n" in out
    assert "Name=Bethesda Strings Editor\n" in out
    assert "/usr/bin/" not in out


def test_desktop_entry_keeps_exactly_one_field_placeholder():
    template = "[Desktop Entry]\nExec=x %f\n"
    assert fa.desktop_entry_text(template, "app").count("%f") == 1


# ── icons ────────────────────────────────────────────────────────────────────
def test_icon_targets_cover_both_contexts_and_both_mimetype_spellings():
    targets = fa.icon_targets({64: Path("a.png")})
    apps = {t.name for t in targets if t.context == "apps"}
    mimetypes = {t.name for t in targets if t.context == "mimetypes"}
    assert apps == {fa.ICON_NAME}
    # GIO asks for the MIME XML's declared name first and each type's dashed
    # name second; installing only one of the two leaves half the lookups blank.
    assert mimetypes == {fa.ICON_NAME} | {ft.icon_name for ft in fa.FILE_TYPES}


def test_icon_targets_multiply_by_size():
    one = fa.icon_targets({64: Path("a.png")})
    two = fa.icon_targets({64: Path("a.png"), 512: Path("b.png")})
    assert len(two) == 2 * len(one)
    assert {t.size for t in two} == {64, 512}


# ── Linux install, end to end in a temp data home ────────────────────────────
@pytest.mark.skipif(sys.platform != "linux", reason="Linux desktop integration")
def test_install_linux_writes_into_xdg_data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fa.install_linux(restart_thunar=False)

    desktop = tmp_path / "applications" / fa.DESKTOP_ID
    assert desktop.is_file()
    body = desktop.read_text(encoding="utf-8")
    exec_line = next(ln for ln in body.splitlines() if ln.startswith("Exec="))
    # Rewritten for this checkout, and still ending in the field code the file
    # manager substitutes the dropped/double-clicked path into.
    assert exec_line.endswith(" %f")
    assert "main.py" in exec_line
    assert f"Icon={fa.ICON_NAME}\n" in body
    assert (tmp_path / "mime" / "packages" / f"{fa.APP_ID}.xml").is_file()

    icons = sorted(p.name for p in (tmp_path / "icons" / "hicolor").glob("*/*/*.png"))
    assert f"{fa.ICON_NAME}.png" in icons
    for ft in fa.FILE_TYPES:
        assert f"{ft.icon_name}.png" in icons


@pytest.mark.skipif(sys.platform != "linux", reason="Linux desktop integration")
def test_uninstall_linux_removes_what_install_wrote(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fa.install_linux(restart_thunar=False)
    fa.uninstall_linux()

    assert not (tmp_path / "applications" / fa.DESKTOP_ID).exists()
    assert not (tmp_path / "mime" / "packages" / f"{fa.APP_ID}.xml").exists()
    left = list((tmp_path / "icons" / "hicolor").glob("*/*/*.png"))
    assert left == [], f"icons left behind: {left}"


@pytest.mark.skipif(sys.platform != "linux", reason="Linux desktop integration")
def test_install_linux_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    fa.install_linux(restart_thunar=False)
    first = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")
                   if p.is_file())
    fa.install_linux(restart_thunar=False)
    second = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*")
                    if p.is_file())
    assert first == second
