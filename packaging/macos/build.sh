#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
#
# Build PyAmp.app (with libmpv bundled) and a .dmg for this Mac's architecture.
#
#   packaging/macos/build.sh [--app-only] [--libmpv <folder>] [output-dir]   (default: dist/)
#
# --app-only builds just PyAmp.app, without the disk image (quicker, for trying it out).
# The pinned libmpv download is cached in build/libmpv-cache/ and checked against
# the SHA-256 in packaging/macos/libmpv.lock. --libmpv bundles the libmpv in that folder
# instead (e.g. one just built by packaging/libmpv/build.sh, to test it in the app).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
LIBMPV_DIR=
APP_ONLY=
while [ $# -gt 0 ]; do
  case $1 in
    --libmpv)
      LIBMPV_DIR=$(cd "${2:?--libmpv needs a folder}" && pwd)
      shift 2
      ;;
    --app-only)
      APP_ONLY=1
      shift
      ;;
    -*)
      echo "error: unknown option $1" >&2
      exit 2
      ;;
    *) break ;;
  esac
done
OUT=${1:-"$ROOT/dist"}
ARCH=$(uname -m) # x86_64 or arm64
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# -- libmpv ------------------------------------------------------------------- #
if [ -n "$LIBMPV_DIR" ]; then
  mkdir -p "$WORK/libmpv"
  cp "$LIBMPV_DIR"/*.dylib "$WORK/libmpv/"
else
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
fi

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
# Drop the libraries' local symbols (about 13 MB; mostly libmpv's harfbuzz and PySide6's
# bindings). Exported symbols stay, so linking and Python imports are unaffected.
# Contents/MacOS/PyAmp is left alone: PyInstaller appends the Python archive to it.
find "$APP/Contents/Frameworks" -type f -print0 |
  while IFS= read -r -d '' file; do
    if file -b "$file" | grep -q '^Mach-O'; then
      strip -x "$file" 2>/dev/null || echo "warning: could not strip $file" >&2
    fi
  done
# Ad-hoc signature: not notarised, but Apple Silicon won't run unsigned code at all.
codesign --force --deep --sign - "$APP"

# Measured here: the copy in $OUT may be moved (e.g. to /Applications) straight away.
APP_SIZE=$(du -sh "$APP" | cut -f1)
mkdir -p "$OUT"
rm -rf "$OUT/PyAmp.app"
cp -R "$APP" "$OUT/PyAmp.app"
if [ -n "$APP_ONLY" ]; then
  echo "App: $OUT/PyAmp.app ($APP_SIZE)"
  exit 0
fi

# -- Disk image ----------------------------------------------------------------- #
mkdir -p "$WORK/dmg"
cp -R "$APP" "$WORK/dmg/"
ln -s /Applications "$WORK/dmg/Applications" # drag-to-install
DMG="$OUT/PyAmp-$VERSION-macos-$ARCH.dmg"
rm -f "$DMG"
# ULMO (LZMA, macOS 10.15+) makes the image about a quarter smaller than UDZO (zlib).
# hdiutil fails now and then on CI runners ("Resource busy"), so retry a few times.
for attempt in 1 2 3 4; do
  if hdiutil create -volname "PyAmp $VERSION" -srcfolder "$WORK/dmg" -format ULMO "$DMG"; then
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

echo "App: $OUT/PyAmp.app ($APP_SIZE)"
echo "DMG: $DMG ($(du -sh "$DMG" | cut -f1))"
