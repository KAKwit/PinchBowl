#!/usr/bin/env bash
# Build a /usr-staged tarball and an Architecture: all .deb from Meson.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -n "${VERSION:-}" ]]; then
  :
elif [[ -n "${1:-}" ]]; then
  VERSION="$1"
else
  VERSION="$(sed -n "s/^  version: '\\([^']*\\)',$/\\1/p" meson.build | head -n1)"
fi

if [[ -z "${VERSION}" ]]; then
  echo "Could not determine VERSION (set VERSION= or pass as \$1)" >&2
  exit 1
fi

OUT_DIR="${OUT_DIR:-$ROOT/dist}"
BUILD_DIR="$ROOT/build-release"
STAGE_DIR="$ROOT/stage"
DEBROOT="$ROOT/debroot"

TAR_NAME="pinchbowl-${VERSION}.tar.gz"
DEB_NAME="pinchbowl_${VERSION}_all.deb"

rm -rf "$BUILD_DIR" "$STAGE_DIR" "$DEBROOT"
mkdir -p "$OUT_DIR" "$STAGE_DIR" "$DEBROOT"

meson setup "$BUILD_DIR" --prefix=/usr
meson compile -C "$BUILD_DIR"
meson install -C "$BUILD_DIR" --destdir="$STAGE_DIR"

tar -C "$STAGE_DIR" -czf "$OUT_DIR/$TAR_NAME" usr

cp -a "$STAGE_DIR/." "$DEBROOT/"
mkdir -p "$DEBROOT/DEBIAN"

cat >"$DEBROOT/DEBIAN/control" <<EOF
Package: pinchbowl
Version: ${VERSION}
Section: gnome
Priority: optional
Architecture: all
Maintainer: KAKwit
Homepage: https://github.com/KAKwit/PinchBowl
Depends: python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-gsound-1.0
Description: GNOME recipe app inspired by Mela
 Pinch Bowl is a native GNOME recipe app inspired by Mela. Browse by
 tags, import from Schema.org or Mela exports, scale quantities, cook
 distraction-free, and keep a shared groceries checklist.
EOF

cat >"$DEBROOT/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if [ -d /usr/share/glib-2.0/schemas ] && command -v glib-compile-schemas >/dev/null 2>&1; then
  glib-compile-schemas /usr/share/glib-2.0/schemas || true
fi

if [ -d /usr/share/icons/hicolor ] && command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi

exit 0
EOF
chmod 755 "$DEBROOT/DEBIAN/postinst"

dpkg-deb --build "$DEBROOT" "$OUT_DIR/$DEB_NAME"

echo "Built:"
echo "  $OUT_DIR/$TAR_NAME"
echo "  $OUT_DIR/$DEB_NAME"
