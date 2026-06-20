# PyInstaller spec file for Bethesda Strings Editor
#
# Build:
#   pyinstaller bethesda_strings_editor.spec
#
# Produces dist/bethesda-strings-editor/ — zip this directory for distribution.
# The GitHub Actions release workflow (`.github/workflows/release.yml`) runs
# this automatically on every `v*` tag push.

import sys

block_cipher = None

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
    # Default protected-terms list shipped with the app
    ('protected_terms_starfield_hq.txt', '.'),
    # Default glossary
    ('starfield_glossary.json', '.'),
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
