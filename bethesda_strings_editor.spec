# PyInstaller spec file for Bethesda Strings Editor
#
# Build:
#   pyinstaller bethesda_strings_editor.spec
#
# Produces dist/bethesda-strings-editor/ — zip this directory for distribution.
# The GitHub Actions release workflow (`.github/workflows/release.yml`) runs
# this automatically on every `v*` tag push.

import sys
from pathlib import Path

block_cipher = None

# ── Windows version-info resource ──────────────────────────────────────────────
# A frozen .exe with no embedded version metadata (CompanyName / ProductName /
# FileVersion) scores higher on antivirus heuristics and SmartScreen.  Generate
# a VSVersionInfo file from the app version so the binary carries proper
# metadata.  Windows-only; returns None (ignored) elsewhere.
def _win_version_info():
    if sys.platform != 'win32':
        return None
    try:
        from _version import __version__ as _v
    except Exception:
        _v = 'dev'
    nums = []
    for chunk in str(_v).replace('-', '.').split('.'):
        if chunk.isdigit():
            nums.append(int(chunk))
        if len(nums) == 4:
            break
    while len(nums) < 4:
        nums.append(0)
    vers = tuple(nums[:4])
    vstr = '.'.join(str(n) for n in vers)
    repo = 'https://github.com/0xra0/bethesda-strings-editor'
    vinfo = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={vers}, prodvers={vers},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '0xra'),
        StringStruct('FileDescription', 'Bethesda Strings Editor'),
        StringStruct('FileVersion', '{vstr}'),
        StringStruct('InternalName', 'bethesda-strings-editor'),
        StringStruct('LegalCopyright', 'Copyright (c) 2026 0xra — MIT License — {repo}'),
        StringStruct('OriginalFilename', 'bethesda-strings-editor.exe'),
        StringStruct('ProductName', 'Bethesda Strings Editor'),
        StringStruct('ProductVersion', '{vstr}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    out = Path('version_info.txt')
    out.write_text(vinfo, encoding='utf-8')
    return str(out)


_version_file = _win_version_info()

# Data files that must be present at runtime alongside the frozen modules.
# Format: (source_glob, dest_dir_relative_to_sys._MEIPASS)
# Mirrors the source tree layout so that Path(__file__).parent… resolution
# in word checkers and main.py works identically in frozen and development mode.
datas = [
    # Word lists for every language-detection checker (en/ru/uk/de/fr/es/it/pl/pt/ko).
    # Globbed so a newly-added *_words.txt is bundled automatically.
    *[(str(p), 'data/') for p in __import__('pathlib').Path('data').glob('*_words.txt')],
    # Visual-context preview: game-UI reference images + bundled UI fonts.
    *[(str(p), 'data/') for p in __import__('pathlib').Path('data').glob('*.png')],
    *[(str(p), 'data/fonts/') for p in __import__('pathlib').Path('data/fonts').glob('*.ttf')],
    # UI: application icon and base stylesheet
    ('resources/app_icon.ico',    'resources/'),
    ('resources/app_icon.png',    'resources/'),
    ('resources/app_icon_64.png', 'resources/'),
    ('resources/style.qss',       'resources/'),
    # Compiled Qt UI translations (build step: scripts/compile_translations.sh)
    *[(str(p), 'gui/translations/') for p in __import__('pathlib').Path('gui/translations').glob('*.qm')],
    # Desktop entry + MIME definitions. gui/file_associations.py reads these out
    # of sys._MEIPASS when the frozen app runs --register-file-types, so a
    # release can register itself with the desktop; without them it can't.
    ('packaging/bethesda-strings-editor.desktop',  'packaging/'),
    ('packaging/bethesda-strings-editor-mime.xml', 'packaging/'),
    # protected_terms_starfield_hq.txt and starfield_glossary.json are no longer
    # tracked or bundled. The terms file is a user extension point the app reads
    # from its own directory when present (main_window), and nothing ever opened
    # the glossary by that name — GlossaryManager reads <config>/glossary.json —
    # so bundling it only added ~6 MB of dead weight to every build. Globbed
    # rather than listed, because PyInstaller aborts on a missing datas path.
    *[(p, '.') for p in ('protected_terms_starfield_hq.txt',)
      if __import__('pathlib').Path(p).is_file()],
]

a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # PySide6 modules that PyInstaller's hook may not detect via static import
        'PySide6.QtSvg',
        'PySide6.QtPrintSupport',
        'PySide6.QtXml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Trim unused stdlib / third-party packages to reduce bundle size
    excludes=['tkinter', 'matplotlib', 'numpy', 'scipy', 'PIL', 'cv2'],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='bethesda-strings-editor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Windows: hide the console window; Linux: keep it so log output is visible
    console=sys.platform != 'win32',
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Embed Windows version metadata to lower AV/SmartScreen heuristic scores.
    version=_version_file,
    icon='resources/app_icon.ico' if sys.platform == 'win32' else 'resources/app_icon.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='bethesda-strings-editor',
)
