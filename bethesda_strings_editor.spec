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
    # Word lists used by language-detection checkers
    ('data/english_words.txt',  'data/'),
    ('data/russian_words.txt',  'data/'),
    ('data/ukrainian_words.txt','data/'),
    # UI: application icon and base stylesheet
    ('resources/app_icon.ico',    'resources/'),
    ('resources/app_icon.png',    'resources/'),
    ('resources/app_icon_64.png', 'resources/'),
    ('resources/style.qss',       'resources/'),
    # Compiled Qt UI translation (build step: pyside6-lrelease uk_UA.ts → uk_UA.qm)
    ('gui/translations/uk_UA.qm', 'gui/translations/'),
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
