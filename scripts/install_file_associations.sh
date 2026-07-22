#!/usr/bin/env bash
# Install MIME types, icons and the desktop entry for Bethesda Strings Editor.
# Run once after a source checkout / pip install.
# Requires: xdg-mime, desktop-file-install (package: xdg-utils)
#
# Installs into $XDG_DATA_HOME (default ~/.local/share), so it needs no root and
# can be pointed somewhere harmless for a dry run:
#
#     XDG_DATA_HOME=/tmp/assoc-test scripts/install_file_associations.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
MIME_XML="$PROJECT_DIR/packaging/bethesda-strings-editor-mime.xml"
DESKTOP_SRC="$PROJECT_DIR/packaging/bethesda-strings-editor.desktop"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/applications"
MIME_DIR="$DATA_HOME/mime/packages"
ICON_DIR="$DATA_HOME/icons/hicolor"

DESKTOP_ID="bethesda-strings-editor.desktop"
ICON_NAME="bethesda-strings-editor"

# The MIME types declared in packaging/bethesda-strings-editor-mime.xml.
MIME_TYPES=(
    application/x-bethesda-strings
    application/x-bethesda-plugin
    application/x-bethesda-archive
)

# Detect the Python interpreter to use in the Exec= line
PYTHON="${PYTHON:-$(command -v python3 || command -v python)}"
if [[ -z "$PYTHON" ]]; then
    echo "ERROR: python3/python not found. Set PYTHON=/path/to/python and rerun." >&2
    exit 1
fi

echo "Using Python: $PYTHON"
echo "Project:      $PROJECT_DIR"
echo "Data home:    $DATA_HOME"

# ── MIME type definitions ────────────────────────────────────────────────────
mkdir -p "$MIME_DIR"
cp "$MIME_XML" "$MIME_DIR/bethesda-strings-editor.xml"
if command -v update-mime-database &>/dev/null; then
    update-mime-database "$DATA_HOME/mime"
    echo "MIME database updated."
else
    echo "WARNING: update-mime-database not found — install shared-mime-info." >&2
fi

# ── Icons ────────────────────────────────────────────────────────────────────
# Without this step the desktop entry's Icon= and the <icon name=…> in the MIME
# XML both point at an icon that was never installed, so Thunar falls back to a
# blank/generic page for every .strings, .esp and .ba2 file.
#
# Two contexts are needed, and they are not interchangeable:
#   apps/       — the launcher icon, looked up by the desktop entry's Icon= key
#   mimetypes/  — the per-file icon a file manager draws, looked up by the MIME
#                 XML's <icon name="…"/> and, failing that, by the type's own
#                 name with the slash replaced by a dash
#                 (application/x-bethesda-strings → application-x-bethesda-strings)
ICON_512="$PROJECT_DIR/resources/app_icon.png"
ICON_64="$PROJECT_DIR/resources/app_icon_64.png"

install_icon() {  # install_icon <src png> <pixel size> <context> <icon name>
    local src="$1" size="$2" context="$3" name="$4"
    if command -v xdg-icon-resource &>/dev/null; then
        # --noupdate: refresh the cache once at the end instead of per icon.
        xdg-icon-resource install --mode user --noupdate \
            --context "$context" --size "$size" "$src" "$name"
    else
        local dest="$ICON_DIR/${size}x${size}/$context"
        mkdir -p "$dest"
        cp "$src" "$dest/$name.png"
    fi
}

# Render the intermediate sizes a file manager actually asks for (Thunar draws
# 16/24/32/48 in list and icon view). Pillow is optional — with only the two
# shipped sizes the theme still resolves, GTK just downscales 64→16 itself.
TMP_ICONS=""
SIZES=(64 512)
if "$PYTHON" -c 'import PIL' &>/dev/null; then
    TMP_ICONS="$(mktemp -d)"
    trap '[[ -n "$TMP_ICONS" ]] && rm -rf "$TMP_ICONS"' EXIT
    if "$PYTHON" - "$ICON_512" "$TMP_ICONS" <<'PY'
import sys
from PIL import Image
src, out = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGBA")
for size in (16, 22, 24, 32, 48, 128, 256):
    im.resize((size, size), Image.LANCZOS).save(f"{out}/{size}.png")
PY
    then
        SIZES=(16 22 24 32 48 64 128 256 512)
        echo "Rendered intermediate icon sizes with Pillow."
    fi
fi

icon_src_for() {  # echo the source PNG for a given pixel size
    case "$1" in
        64)  echo "$ICON_64"  ;;
        512) echo "$ICON_512" ;;
        *)   echo "$TMP_ICONS/$1.png" ;;
    esac
}

for size in "${SIZES[@]}"; do
    src="$(icon_src_for "$size")"
    [[ -f "$src" ]] || continue
    install_icon "$src" "$size" apps "$ICON_NAME"
    # The MIME XML names this icon explicitly, so it must exist in the
    # mimetypes context too — an apps/ icon of the same name is not consulted.
    install_icon "$src" "$size" mimetypes "$ICON_NAME"
    for type in "${MIME_TYPES[@]}"; do
        install_icon "$src" "$size" mimetypes "${type/\//-}"
    done
done

if command -v xdg-icon-resource &>/dev/null; then
    xdg-icon-resource forceupdate --mode user
elif command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "$ICON_DIR" || true
fi
echo "Icons installed (${#SIZES[@]} sizes, apps + mimetypes)."

# ── Desktop entry ────────────────────────────────────────────────────────────
mkdir -p "$APP_DIR"
if command -v desktop-file-install &>/dev/null; then
    desktop-file-install \
        --dir="$APP_DIR" \
        --set-key=Exec \
        --set-value="$PYTHON $PROJECT_DIR/main.py %f" \
        --set-key=Icon \
        --set-value="$ICON_NAME" \
        "$DESKTOP_SRC"
else
    # Fallback: sed-substitute and copy
    sed "s|Exec=.*|Exec=$PYTHON $PROJECT_DIR/main.py %f|" \
        "$DESKTOP_SRC" > "$APP_DIR/$DESKTOP_ID"
fi

if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$APP_DIR"
    echo "Application database updated."
fi

# ── Default handler ──────────────────────────────────────────────────────────
# Puts the app at the top of Thunar's "Open With" menu and makes a double-click
# open it. Other applications that claim these types stay in the submenu.
if command -v xdg-mime &>/dev/null; then
    for type in "${MIME_TYPES[@]}"; do
        xdg-mime default "$DESKTOP_ID" "$type"
    done
    echo "Registered as the default handler for: ${MIME_TYPES[*]}"
else
    echo "WARNING: xdg-mime not found — 'Open With' default not set." >&2
fi

# ── XFCE / Thunar refresh ────────────────────────────────────────────────────
# Thunar caches icons and MIME data for the life of its daemon, so a fresh
# install shows the old blank icons until it is restarted. Quitting is safe: it
# respawns on the next window and open windows are reopened by the session.
if [[ -z "${XDG_DATA_HOME:-}" ]] && command -v thunar &>/dev/null \
   && pgrep -x thunar >/dev/null 2>&1; then
    thunar -q || true
    echo "Restarted the Thunar daemon so the new icons are picked up."
fi

echo
echo "Done. Verify with:"
echo "  xdg-mime query default application/x-bethesda-strings"
echo "  gio info some-file.strings | grep -i icon"
echo "If icons still look stale, log out and back in (or run: thunar -q)."
