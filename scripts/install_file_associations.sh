#!/usr/bin/env bash
# Register Bethesda Strings Editor as the handler for .strings / .esp / .ba2 —
# MIME types, icons, desktop entry and the "Open With" default.
#
#   scripts/install_file_associations.sh              # install
#   scripts/install_file_associations.sh uninstall    # remove
#
# Everything lands under $XDG_DATA_HOME (default ~/.local/share), so it needs no
# root and can be pointed somewhere harmless for a rehearsal:
#
#   XDG_DATA_HOME=/tmp/assoc-test scripts/install_file_associations.sh
#
# This is a thin wrapper: the work lives in gui/file_associations.py, which the
# app itself runs as `bethesda-strings-editor --register-file-types` on both
# Linux and Windows. Keeping one implementation is why this script is four
# lines of logic — do not reimplement the install here.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: python3/python not found. Set PYTHON=/path/to/python and rerun." >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec "$PYTHON" -m gui.file_associations "${@:-install}"
