#!/usr/bin/env bash
# Register metatron-tui as an XDG handler for PDFs (Linux, XDG desktops).
#
# This only *registers* the app, so it shows up under "Open With" without
# stealing the system default PDF viewer.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
desktop="$here/metatron-tui.desktop"
icons_dir="$here/icons"
svg_icon="$here/icon.svg"
app_id="metatron-tui"
sizes="16 24 32 48 64 128 256"

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
apps_dir="$data_home/applications"

# 1. Install the desktop entry (canonical XDG, with a manual fallback).
if command -v xdg-desktop-menu >/dev/null 2>&1; then
    xdg-desktop-menu install --novendor "$desktop"
else
    mkdir -p "$apps_dir"
    install -m 644 "$desktop" "$apps_dir/$app_id.desktop"
fi

# 2. Install the icon set so the entry has a proper graphic.
if command -v xdg-icon-resource >/dev/null 2>&1; then
    for size in $sizes; do
        xdg-icon-resource install --novendor --context apps --size "$size" \
            "$icons_dir/$size.png" "$app_id"
    done
else
    for size in $sizes; do
        icon_dir="$data_home/icons/hicolor/${size}x${size}/apps"
        mkdir -p "$icon_dir"
        install -m 644 "$icons_dir/$size.png" "$icon_dir/$app_id.png"
    done
fi

# Scalable vector version (for HiDPI / anything that prefers SVG).
scalable_dir="$data_home/icons/hicolor/scalable/apps"
mkdir -p "$scalable_dir"
install -m 644 "$svg_icon" "$scalable_dir/$app_id.svg"

# Refresh caches so file managers pick up the entry and icon.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$apps_dir" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Registered. PDFs now offer 'Open With → Metatron'."
