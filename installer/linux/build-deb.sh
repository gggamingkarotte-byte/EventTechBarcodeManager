#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ARCH="$(dpkg --print-architecture)"
if [[ "$ARCH" != "arm64" ]]; then
  echo "This installer must be built on a 64-bit ARM Linux runner (got: $ARCH)." >&2
  exit 1
fi

VERSION="$(python -c 'from config import APP_VERSION; print(APP_VERSION)')"
PACKAGE_ROOT="$ROOT_DIR/build/linux-deb-root"
OUTPUT_DIR="$ROOT_DIR/dist/linux"

python -m PyInstaller --noconfirm --clean EventTechBarcodeManager-linux.spec

rm -rf "$PACKAGE_ROOT"
mkdir -p \
  "$ROOT_DIR/build" \
  "$PACKAGE_ROOT/DEBIAN" \
  "$PACKAGE_ROOT/opt/eventtech-barcode-manager" \
  "$PACKAGE_ROOT/usr/share/applications" \
  "$PACKAGE_ROOT/usr/share/icons/hicolor/256x256/apps" \
  "$OUTPUT_DIR"

cp -a "$ROOT_DIR/dist/EventTechBarcodeManager/." \
  "$PACKAGE_ROOT/opt/eventtech-barcode-manager/"
install -m 0644 installer/linux/eventtech-barcode-manager.desktop \
  "$PACKAGE_ROOT/usr/share/applications/eventtech-barcode-manager.desktop"
python -c 'from PIL import Image; Image.open("assets/icon.ico").convert("RGBA").save("build/eventtech-barcode-manager.png")'
install -m 0644 build/eventtech-barcode-manager.png \
  "$PACKAGE_ROOT/usr/share/icons/hicolor/256x256/apps/eventtech-barcode-manager.png"

cat > "$PACKAGE_ROOT/DEBIAN/control" <<EOF
Package: eventtech-barcode-manager
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: libc6 (>= 2.31), libstdc++6, libgcc-s1, libgl1, libegl1, libopengl0,
 libfontconfig1, libglib2.0-0, libdbus-1-3, gnome-keyring, libsecret-1-0,
 libx11-xcb1, libxcb1, libxcb-cursor0, libxcb-icccm4, libxcb-keysyms1,
 libxcb-randr0, libxcb-render0, libxcb-render-util0, libxcb-image0,
 libxcb-shape0, libxcb-shm0, libxcb-sync1, libxcb-xfixes0, libxcb-xinerama0,
 libxcb-xkb1, libxkbcommon0, libxkbcommon-x11-0, libxext6, libxi6, libxrender1
Maintainer: EventTech
Description: Inventory and warehouse management for event technology
 Desktop application for managing articles, warehouse locations, barcodes,
 repairs, inventories, and scanner workflows.
EOF

dpkg-deb --build --root-owner-group "$PACKAGE_ROOT" \
  "$OUTPUT_DIR/eventtech-barcode-manager_${VERSION}_${ARCH}.deb"
