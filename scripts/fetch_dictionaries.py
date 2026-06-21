#!/usr/bin/env python3
"""Download Hunspell spell-check dictionaries into ``dicts/`` for bundling.

The app's spell-checker (``gui/spell_checker.py``) searches an app-bundled
``dicts/`` directory first.  Linux machines usually have system dictionaries,
but Windows/macOS builds don't — so the packaging step runs this script to fill
``dicts/`` and the installer ships the result.  ``dicts/`` is gitignored, so the
files never enter source control.

Dictionaries come from the LibreOffice dictionaries project
(https://github.com/LibreOffice/dictionaries).  Each language carries its own
upstream licence (GPL/LGPL/MPL/…); the licence/readme files are downloaded
alongside every dictionary so the bundle stays compliant.

Usage::

    python scripts/fetch_dictionaries.py                 # default language set
    python scripts/fetch_dictionaries.py uk_UA de_DE     # explicit subset
    python scripts/fetch_dictionaries.py --all           # every known language
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO = "LibreOffice/dictionaries"
RAW = f"https://raw.githubusercontent.com/{REPO}/master"
API = f"https://api.github.com/repos/{REPO}/contents"

# dict name (must match gui.spell_checker LANG_TO_DICT values) -> (repo_dir, stem)
SOURCES: dict[str, tuple[str, str]] = {
    "uk_UA": ("uk_UA", "uk_UA"),
    "de_DE": ("de", "de_DE_frami"),
    "es_ES": ("es", "es_ES"),
    "fr_FR": ("fr_FR", "fr"),
    "pl_PL": ("pl_PL", "pl_PL"),
    "cs_CZ": ("cs_CZ", "cs_CZ"),
    # Available but not bundled by default (Korean is ~14 MB and Hunspell
    # handles it poorly; the rest are extra target languages). Opt in with
    # an explicit arg or --all.
    "ko_KR": ("ko_KR", "ko_KR"),
    "ru_RU": ("ru_RU", "ru_RU"),
    "it_IT": ("it_IT", "it_IT"),
    "en_US": ("en", "en_US"),
    "pt_BR": ("pt_BR", "pt_BR"),
}

# The locales the app ships UI translations for, minus Korean (huge / niche).
DEFAULT_LANGS = ["uk_UA", "de_DE", "es_ES", "fr_FR", "pl_PL", "cs_CZ"]

# Filename fragments that mark an upstream licence/readme worth preserving.
LICENSE_HINTS = ("license", "licence", "copying", "readme", "gpl", "lgpl", "mpl")

DICTS_DIR = Path(__file__).resolve().parent.parent / "dicts"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "bse-fetch-dicts"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _download(url: str, dest: Path) -> None:
    dest.write_bytes(_get(url))
    print(f"    {dest.name}  ({dest.stat().st_size:,} bytes)")


def _license_files(repo_dir: str) -> list[tuple[str, str]]:
    """Return (filename, download_url) for licence/readme files in *repo_dir*."""
    try:
        items = json.loads(_get(f"{API}/{repo_dir}").decode())
    except Exception as exc:  # rate limit, network, etc. — non-fatal
        print(f"    ! could not list {repo_dir} for licences: {exc}")
        return []
    found = []
    for item in items:
        if item.get("type") != "file":
            continue
        name = item["name"]
        low = name.lower()
        if low.endswith((".dic", ".aff")):
            continue
        if any(h in low for h in LICENSE_HINTS):
            found.append((name, item["download_url"]))
    return found


def _looks_like_hunspell(aff: Path, dic: Path) -> bool:
    """Sanity-check that the files are plausible Hunspell .aff/.dic."""
    if not (aff.is_file() and dic.is_file()):
        return False
    if aff.stat().st_size == 0 or dic.stat().st_size == 0:
        return False
    # A .dic begins with a word count on the first line.
    first = dic.read_bytes().split(b"\n", 1)[0].strip()
    return first.isdigit()


def fetch(langs: list[str]) -> int:
    DICTS_DIR.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name in langs:
        if name not in SOURCES:
            print(f"[skip] {name}: no known source")
            failures += 1
            continue
        repo_dir, stem = SOURCES[name]
        print(f"[{name}] {repo_dir}/{stem}")
        aff, dic = DICTS_DIR / f"{name}.aff", DICTS_DIR / f"{name}.dic"
        try:
            _download(f"{RAW}/{repo_dir}/{stem}.aff", aff)
            _download(f"{RAW}/{repo_dir}/{stem}.dic", dic)
        except Exception as exc:
            print(f"    ! download failed: {exc}")
            failures += 1
            continue
        if not _looks_like_hunspell(aff, dic):
            print(f"    ! {name}: downloaded files don't look like Hunspell data")
            failures += 1
            continue
        for fname, url in _license_files(repo_dir):
            try:
                _download(url, DICTS_DIR / f"{name}.{fname}")
            except Exception as exc:
                print(f"    ! licence {fname}: {exc}")
    return failures


def main(argv: list[str]) -> int:
    if "--all" in argv:
        langs = list(SOURCES)
    else:
        langs = [a for a in argv if not a.startswith("-")] or DEFAULT_LANGS
    print(f"Downloading {len(langs)} dictionary set(s) into {DICTS_DIR}\n")
    failures = fetch(langs)
    print("\nDone." if not failures else f"\nDone with {failures} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
