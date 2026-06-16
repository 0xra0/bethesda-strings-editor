#!/usr/bin/env bash
# Install MIME types and desktop entry for Bethesda Strings Editor.
# Run once after a source checkout / pip install.
# Requires: xdg-mime, desktop-file-install (package: xdg-utils)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MIME_XML="$PROJECT_DIR/packaging/bethesda-strings-editor-mime.xml"
DESKTOP_SRC="$PROJECT_DIR/packaging/bethesda-strings-editor.desktop"
APP_DIR="$HOME/.local/share/applications"
MIME_DIR="$HOME/.local/share/mime/packages"

# Detect the Python interpreter to use in the Exec= line
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: python3/python not found. Set PYTHON=/path/to/python and rerun." >&2
    exit 1
fi

echo "Using Python: $PYTHON"
echo "Project:      $PROJECT_DIR"

# Install MIME type definitions for the current user
mkdir -p "$MIME_DIR"
cp "$MIME_XML" "$MIME_DIR/bethesda-strings-editor.xml"
if command -v update-mime-database &>/dev/null; then
    update-mime-database "$HOME/.local/share/mime"
    echo "MIME database updated."
else
    echo "WARNING: update-mime-database not found — install shared-mime-info." >&2
fi

# Install desktop entry with the correct Exec= path for a source install
mkdir -p "$APP_DIR"
if command -v desktop-file-install &>/dev/null; then
    desktop-file-install \
        --dir="$APP_DIR" \
        --set-key=Exec \
        --set-value="$PYTHON $PROJECT_DIR/main.py %f" \
        "$DESKTOP_SRC"
else
    # Fallback: sed-substitute and copy
    sed "s|Exec=.*|Exec=$PYTHON $PROJECT_DIR/main.py %f|" \
        "$DESKTOP_SRC" > "$APP_DIR/bethesda-strings-editor.desktop"
fi

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_DIR"
    echo "Application database updated."
fi

echo "Done. You may need to log out and back in for file-manager associations to appear."
