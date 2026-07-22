"""
Register the app as a handler for Bethesda file types — Linux and Windows.

Run it from the app itself, frozen or from source::

    bethesda-strings-editor --register-file-types
    bethesda-strings-editor --unregister-file-types

or directly::

    python -m gui.file_associations install [--force]
    python -m gui.file_associations uninstall

`main.py` already opens ``sys.argv[1]`` when it is a file, so the double-click
plumbing has always worked — what was missing is the registration itself, which
PyInstaller cannot do (it is an OS-level action, not a packaging one).

Everything is **per-user**: `$XDG_DATA_HOME` on Linux, `HKCU\\Software\\Classes`
on Windows. No root, no admin, no installer.

The module is deliberately split into *plan* (pure functions returning what
would be written) and *apply* (the side effects), so the interesting parts are
unit-testable on any platform — a Linux CI box can check the Windows registry
plan without a registry.

No Qt import: this runs before `QApplication` exists.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

APP_ID = "bethesda-strings-editor"
DESKTOP_ID = f"{APP_ID}.desktop"
ICON_NAME = APP_ID


@dataclass(frozen=True)
class FileType:
    """One handled file type, in both platforms' vocabularies."""
    mime: str           # freedesktop MIME type
    prog_id: str        # Windows ProgID
    description: str
    extensions: Tuple[str, ...]

    @property
    def icon_name(self) -> str:
        """Icon-theme name a file manager looks for: the MIME type, slash dashed."""
        return self.mime.replace("/", "-")


FILE_TYPES: Tuple[FileType, ...] = (
    FileType(
        "application/x-bethesda-strings",
        "BethesdaStringsEditor.Strings",
        "Bethesda Localization Strings",
        (".strings", ".dlstrings", ".ilstrings"),
    ),
    FileType(
        "application/x-bethesda-plugin",
        "BethesdaStringsEditor.Plugin",
        "Bethesda Plugin",
        (".esp", ".esm", ".esl"),
    ),
    FileType(
        "application/x-bethesda-archive",
        "BethesdaStringsEditor.Archive",
        "Bethesda Archive 2",
        (".ba2",),
    ),
)

# resources/ ships 64px and 512px. The sizes in between — the ones a file
# manager's list and icon views actually ask for — are rendered from the 512px
# source when Pillow is importable. It is *not* in a frozen build (the
# PyInstaller spec excludes it), so releases install the two shipped sizes and
# let the toolkit downscale.
_RENDERED_SIZES = (16, 22, 24, 32, 48, 128, 256)


# ── Where things live ────────────────────────────────────────────────────────

def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Directory holding `resources/` and `packaging/`, frozen or not."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def launch_argv() -> List[str]:
    """The argv prefix that starts this application.

    Frozen: the binary itself. From source: the interpreter plus `main.py` —
    and on Windows `pythonw.exe` where it exists, so a double-click does not
    also open a console window.
    """
    if is_frozen():
        return [sys.executable]
    python = Path(sys.executable)
    if sys.platform == "win32":
        windowed = python.with_name(python.name.replace("python", "pythonw", 1))
        if windowed.is_file():
            python = windowed
    return [str(python), str(resource_root() / "main.py")]


# ── Linux ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IconTarget:
    source: Path
    size: int
    context: str        # "apps" or "mimetypes"
    name: str


def desktop_entry_text(template: str, exec_command: str) -> str:
    """Return *template* with its Exec= and Icon= lines pointed at this install.

    The packaged .desktop ships a placeholder Exec (`/usr/bin/…`) that is right
    only for a distro package, so it is rewritten for wherever the app actually
    sits.
    """
    lines = []
    for line in template.splitlines():
        if line.startswith("Exec="):
            lines.append(f"Exec={exec_command} %f")
        elif line.startswith("Icon="):
            lines.append(f"Icon={ICON_NAME}")
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def icon_targets(sources: dict[int, Path]) -> List[IconTarget]:
    """Every icon to install, given {pixel size: source PNG}.

    Two contexts, and they are not interchangeable: `apps` backs the desktop
    entry's Icon=, while `mimetypes` is what the file manager draws per file.
    Within `mimetypes` two names are needed — the one the MIME XML declares
    (`<icon name="bethesda-strings-editor"/>`) and each type's own dashed name
    — because GIO asks for them in that order and stops at the first hit.
    """
    targets: List[IconTarget] = []
    for size, src in sorted(sources.items()):
        targets.append(IconTarget(src, size, "apps", ICON_NAME))
        targets.append(IconTarget(src, size, "mimetypes", ICON_NAME))
        for ft in FILE_TYPES:
            targets.append(IconTarget(src, size, "mimetypes", ft.icon_name))
    return targets


def _icon_sources(tmpdir: Optional[Path]) -> dict[int, Path]:
    root = resource_root() / "resources"
    sources: dict[int, Path] = {}
    if (root / "app_icon_64.png").is_file():
        sources[64] = root / "app_icon_64.png"
    large = root / "app_icon.png"
    if large.is_file():
        sources[512] = large
    if tmpdir is not None and large.is_file():
        try:
            from PIL import Image  # noqa: PLC0415 - optional, absent when frozen
        except ImportError:
            return sources
        try:
            im = Image.open(large).convert("RGBA")
            for size in _RENDERED_SIZES:
                out = tmpdir / f"{size}.png"
                im.resize((size, size), Image.LANCZOS).save(out)
                sources[size] = out
        except Exception as exc:  # noqa: BLE001 - rendering is a nicety
            logger.debug("Could not render intermediate icon sizes: %s", exc)
    return sources


def _run(cmd: Sequence[str]) -> bool:
    if not shutil.which(cmd[0]):
        return False
    try:
        subprocess.run(list(cmd), check=False, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return True
    except OSError as exc:
        logger.debug("%s failed: %s", cmd[0], exc)
        return False


def install_linux(*, restart_thunar: bool = True) -> List[str]:
    """Install MIME types, icons and the desktop entry into $XDG_DATA_HOME."""
    import tempfile

    root = resource_root()
    share = data_home()
    notes: List[str] = []

    mime_src = root / "packaging" / f"{APP_ID}-mime.xml"
    desktop_src = root / "packaging" / f"{APP_ID}.desktop"
    if not mime_src.is_file() or not desktop_src.is_file():
        raise FileNotFoundError(
            f"packaging/ files not found under {root} — a build that does not "
            "bundle them cannot register file associations"
        )

    (share / "mime" / "packages").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(mime_src, share / "mime" / "packages" / f"{APP_ID}.xml")
    if _run(["update-mime-database", str(share / "mime")]):
        notes.append("MIME database updated.")
    else:
        notes.append("WARNING: update-mime-database missing (install shared-mime-info).")

    with tempfile.TemporaryDirectory() as tmp:
        sources = _icon_sources(Path(tmp))
        targets = icon_targets(sources)
        for t in targets:
            dest = share / "icons" / "hicolor" / f"{t.size}x{t.size}" / t.context
            dest.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(t.source, dest / f"{t.name}.png")
    notes.append(f"Installed {len(targets)} icons across {len(sources)} sizes.")
    _run(["gtk-update-icon-cache", "-f", "-t", str(share / "icons" / "hicolor")])

    exec_command = " ".join(_quote_posix(part) for part in launch_argv())
    apps = share / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / DESKTOP_ID).write_text(
        desktop_entry_text(desktop_src.read_text(encoding="utf-8"), exec_command),
        encoding="utf-8",
    )
    _run(["update-desktop-database", str(apps)])
    notes.append(f"Desktop entry installed: Exec={exec_command} %f")

    for ft in FILE_TYPES:
        _run(["xdg-mime", "default", DESKTOP_ID, ft.mime])
    notes.append("Registered as the default handler for %s."
                 % ", ".join(ft.mime for ft in FILE_TYPES))

    # Thunar caches icons and MIME data for the life of its daemon, so a fresh
    # install keeps showing the old generic icons until it restarts. Quitting is
    # safe — it respawns on the next window.
    if restart_thunar and shutil.which("thunar") and _pgrep("thunar"):
        _run(["thunar", "-q"])
        notes.append("Restarted the Thunar daemon so it picks up the new icons.")
    return notes


def uninstall_linux() -> List[str]:
    share = data_home()
    removed = 0
    for path in [
        share / "mime" / "packages" / f"{APP_ID}.xml",
        share / "applications" / DESKTOP_ID,
    ]:
        if path.exists():
            path.unlink()
            removed += 1
    icons = share / "icons" / "hicolor"
    names = {ICON_NAME} | {ft.icon_name for ft in FILE_TYPES}
    if icons.is_dir():
        for png in icons.glob("*/*/*.png"):
            if png.stem in names:
                png.unlink()
                removed += 1
    _run(["update-mime-database", str(share / "mime")])
    _run(["update-desktop-database", str(share / "applications")])
    _run(["gtk-update-icon-cache", "-f", "-t", str(icons)])
    return [f"Removed {removed} files from {share}."]


def _pgrep(name: str) -> bool:
    try:
        return subprocess.run(["pgrep", "-x", name], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return False


def _quote_posix(part: str) -> str:
    return part if " " not in part else f'"{part}"'


# ── Windows ──────────────────────────────────────────────────────────────────

def windows_command() -> str:
    """The `shell\\open\\command` value: how Explorer launches us with a file."""
    return " ".join(f'"{part}"' for part in launch_argv()) + ' "%1"'


def windows_icon() -> str:
    """`DefaultIcon` value — the frozen .exe embeds the icon at index 0."""
    if is_frozen():
        return f"{sys.executable},0"
    return str(resource_root() / "resources" / "app_icon.ico")


def windows_registry_plan(
    command: str, icon: str, *, set_default: bool = False
) -> List[Tuple[str, str, str]]:
    """(subkey, value name, value) triples to write under HKCU\\Software\\Classes.

    `OpenWithProgids` is always written — that is what puts the app in Explorer's
    "Open with" list and gives the extension our icon. The extension's *default*
    handler is only claimed with ``set_default``: Windows 10+ protects a
    user-chosen default behind a UserChoice hash that no application may forge,
    and silently stealing `.esp` from a modding tool the user already picked
    would be worse than not being the default.
    """
    plan: List[Tuple[str, str, str]] = []
    for ft in FILE_TYPES:
        plan.append((ft.prog_id, "", ft.description))
        plan.append((f"{ft.prog_id}\\DefaultIcon", "", icon))
        plan.append((f"{ft.prog_id}\\shell\\open\\command", "", command))
        for ext in ft.extensions:
            plan.append((f"{ext}\\OpenWithProgids", ft.prog_id, ""))
            if set_default:
                plan.append((ext, "", ft.prog_id))
    return plan


def install_windows(*, set_default: bool = False) -> List[str]:
    import winreg  # noqa: PLC0415 - Windows only

    plan = windows_registry_plan(windows_command(), windows_icon(),
                                 set_default=set_default)
    for subkey, name, value in plan:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                f"Software\\Classes\\{subkey}", 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    _notify_shell()
    notes = [f"Wrote {len(plan)} registry values under HKCU\\Software\\Classes.",
             f"Command: {windows_command()}"]
    notes.append(
        "Set as the default handler." if set_default else
        "Added to 'Open with'. Pass --force to also claim the default handler "
        "for extensions you have not already assigned."
    )
    return notes


def uninstall_windows() -> List[str]:
    import winreg  # noqa: PLC0415 - Windows only

    removed = 0
    for ft in FILE_TYPES:
        for ext in ft.extensions:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    f"Software\\Classes\\{ext}\\OpenWithProgids", 0,
                                    winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, ft.prog_id)
                    removed += 1
            except OSError:
                pass
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    f"Software\\Classes\\{ext}", 0,
                                    winreg.KEY_ALL_ACCESS) as key:
                    current, _ = winreg.QueryValueEx(key, "")
                    if current == ft.prog_id:
                        winreg.DeleteValue(key, "")
                        removed += 1
            except OSError:
                pass
        for subkey in (f"{ft.prog_id}\\shell\\open\\command",
                       f"{ft.prog_id}\\shell\\open",
                       f"{ft.prog_id}\\shell",
                       f"{ft.prog_id}\\DefaultIcon",
                       ft.prog_id):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                                 f"Software\\Classes\\{subkey}")
                removed += 1
            except OSError:
                pass
    _notify_shell()
    return [f"Removed {removed} registry entries."]


def _notify_shell() -> None:
    """Tell Explorer the associations changed, so icons refresh without a logout."""
    try:
        import ctypes  # noqa: PLC0415

        SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0x0000
        ctypes.windll.shell32.SHChangeNotify(  # type: ignore[attr-defined]
            SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception as exc:  # noqa: BLE001 - cosmetic
        logger.debug("SHChangeNotify failed: %s", exc)


# ── Entry point ──────────────────────────────────────────────────────────────

def install(*, set_default: bool = False) -> List[str]:
    if sys.platform == "win32":
        return install_windows(set_default=set_default)
    if sys.platform == "linux":
        return install_linux()
    raise RuntimeError(f"File associations are not supported on {sys.platform}.")


def uninstall() -> List[str]:
    if sys.platform == "win32":
        return uninstall_windows()
    if sys.platform == "linux":
        return uninstall_linux()
    raise RuntimeError(f"File associations are not supported on {sys.platform}.")


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    force = "--force" in args
    args = [a for a in args if a != "--force"]
    action = args[0] if args else "install"

    try:
        if action in ("install", "--register-file-types"):
            notes = install(set_default=force)
        elif action in ("uninstall", "--unregister-file-types"):
            notes = uninstall()
        else:
            print(__doc__)
            return 2
    except Exception as exc:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for note in notes:
        print(note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
