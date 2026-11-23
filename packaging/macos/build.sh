#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build PyAmp.app (with libmpv bundled) and a .dmg for this Mac's architecture.
#
#   packaging/macos/build.sh [output-dir]      (default: dist/)
#
# The pinned libmpv download is cached in build/libmpv-cache/ and checked against
# the SHA-256 in packaging/macos/libmpv.lock.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-"$ROOT/dist"}
ARCH=$(uname -m) # x86_64 or arm64
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# -- libmpv ------------------------------------------------------------------- #
read -r SHA URL < <(awk -v arch="$ARCH" '$1 == arch { print $2, $3 }' "$ROOT/packaging/macos/libmpv.lock") || true
if [ -z "${URL:-}" ]; then
  echo "error: no libmpv pinned for $ARCH in packaging/macos/libmpv.lock" >&2
  exit 1
fi
CACHE="$ROOT/build/libmpv-cache"
TARBALL="$CACHE/$(basename "$URL")"
mkdir -p "$CACHE"
if [ ! -f "$TARBALL" ]; then
  echo "Downloading $(basename "$URL")"
  curl -fsSL "$URL" -o "$TARBALL.part" && mv "$TARBALL.part" "$TARBALL"
fi
echo "$SHA  $TARBALL" | shasum -a 256 -c - >/dev/null || {
  echo "error: checksum mismatch for $TARBALL (deleted; re-run to download again)" >&2
  rm -f "$TARBALL"
  exit 1
}
mkdir -p "$WORK/libmpv"
tar -xzf "$TARBALL" -C "$WORK/libmpv" --strip-components=1

# -- App ---------------------------------------------------------------------- #
VERSION=$(cd "$ROOT" && uv run --group packaging python -c "import pyamp; print(pyamp.__version__)")
echo "Building PyAmp $VERSION for macOS $ARCH"
(
  cd "$ROOT"
  PYAMP_BUNDLE_LIBMPV="$WORK/libmpv" PYAMP_NOTICES="$ROOT/packaging/THIRD_PARTY_NOTICES.md" \
    uv run --group packaging pyinstaller --noconfirm --clean --log-level WARN \
    --distpath "$WORK/dist" --workpath "$WORK/build" packaging/macos/pyamp.spec
)
APP="$WORK/dist/PyAmp.app"
# Ad-hoc signature: not notarised, but Apple Silicon won't run unsigned code at all.
codesign --force --deep --sign - "$APP"

# -- Disk image ----------------------------------------------------------------- #
mkdir -p "$OUT" "$WORK/dmg"
rm -rf "$OUT/PyAmp.app"
cp -R "$APP" "$OUT/PyAmp.app"
cp -R "$APP" "$WORK/dmg/"
ln -s /Applications "$WORK/dmg/Applications" # drag-to-install
DMG="$OUT/PyAmp-$VERSION-macos-$ARCH.dmg"
rm -f "$DMG"
# hdiutil fails now and then on CI runners ("Resource busy"), so retry a few times.
for attempt in 1 2 3 4; do
  if hdiutil create -volname "PyAmp $VERSION" -srcfolder "$WORK/dmg" -format UDZO "$DMG"; then
    break
  fi
  if [ "$attempt" -eq 4 ]; then
    echo "error: hdiutil could not create $DMG" >&2
    exit 1
  fi
  echo "hdiutil failed (attempt $attempt); retrying in 5 s" >&2
  rm -f "$DMG"
  sleep 5
done

echo "App: $OUT/PyAmp.app ($(du -sh "$OUT/PyAmp.app" | cut -f1))"
echo "DMG: $DMG ($(du -sh "$DMG" | cut -f1))"
