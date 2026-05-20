#!/bin/bash
# Script to compile Qt translation files (.ts to .qm)

TS_FILE="gui/translations/uk_UA.ts"
QM_FILE="gui/translations/uk_UA.qm"

# List of possible lrelease binary names/paths
LRELEASE_COMMANDS=(
    "lrelease"
    "lrelease6"
    "pyside6-lrelease"
    "/usr/lib/qt6/bin/lrelease"
    "/usr/lib/qt5/bin/lrelease"
)

echo "Attempting to compile $TS_FILE to $QM_FILE..."

for cmd in "${LRELEASE_COMMANDS[@]}"; do
    if command -v "$cmd" &> /dev/null || [ -x "$cmd" ]; then
        echo "Using $cmd..."
        "$cmd" "$TS_FILE" -qm "$QM_FILE"
        if [ $? -eq 0 ]; then
            echo "Success! Translation file compiled."
            exit 0
        fi
    fi
done

echo "Error: lrelease not found. Please install Qt l10n tools or PySide6."
echo "On Ubuntu: sudo apt-get install qt6-l10n-tools"
echo "Via pip: pip install PySide6"
exit 1
