#!/bin/bash
# Compile all Qt translation files (.ts → .qm) in gui/translations/

TRANSLATIONS_DIR="gui/translations"

LRELEASE_COMMANDS=(
    "pyside6-lrelease"
    "lrelease"
    "lrelease6"
    "/usr/lib/qt6/bin/lrelease"
    "/usr/lib/qt5/bin/lrelease"
)

# Locate lrelease
LRELEASE=""
for cmd in "${LRELEASE_COMMANDS[@]}"; do
    if command -v "$cmd" &>/dev/null || [ -x "$cmd" ]; then
        LRELEASE="$cmd"
        break
    fi
done

if [ -z "$LRELEASE" ]; then
    echo "Error: lrelease not found. Install Qt l10n tools or PySide6."
    echo "  Ubuntu: sudo apt-get install qt6-l10n-tools"
    echo "  pip:    pip install PySide6"
    exit 1
fi

echo "Using $LRELEASE"

FAIL=0
for ts in "$TRANSLATIONS_DIR"/*.ts; do
    qm="${ts%.ts}.qm"
    echo "Compiling $ts → $qm"
    "$LRELEASE" "$ts" -qm "$qm" || { echo "FAILED: $ts"; FAIL=1; }
done

if [ $FAIL -eq 0 ]; then
    echo "All translations compiled successfully."
else
    echo "One or more compilations failed."
    exit 1
fi
