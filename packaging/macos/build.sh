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
#
# The app is signed ad hoc unless PYAMP_SIGN_IDENTITY names a Developer ID Application
# identity in the keychain, e.g. "Developer ID Application: Jane Doe (AB12CD34EF)".
# With that and an App Store Connect API key (PYAMP_NOTARY_KEY: the .p8 file,
# PYAMP_NOTARY_KEY_ID, PYAMP_NOTARY_ISSUER), or the name of a keychain profile saved with
# `xcrun notarytool store-credentials` (PYAMP_NOTARY_PROFILE), the app and the .dmg are
# also notarised and stapled (not with --app-only), waiting up to PYAMP_NOTARY_TIMEOUT
# (default 2h) for each.
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
SIGN_IDENTITY=${PYAMP_SIGN_IDENTITY:-}
NOTARISE=
NOTARY_AUTH=()
if [ -n "${PYAMP_NOTARY_PROFILE:-}" ]; then
  NOTARY_AUTH=(--keychain-profile "$PYAMP_NOTARY_PROFILE")
elif [ -n "${PYAMP_NOTARY_KEY:-}${PYAMP_NOTARY_KEY_ID:-}${PYAMP_NOTARY_ISSUER:-}" ]; then
  if [ -z "${PYAMP_NOTARY_KEY:-}" ] || [ -z "${PYAMP_NOTARY_KEY_ID:-}" ] ||
    [ -z "${PYAMP_NOTARY_ISSUER:-}" ]; then
    echo "error: notarising needs PYAMP_NOTARY_KEY, PYAMP_NOTARY_KEY_ID and" \
      "PYAMP_NOTARY_ISSUER (or PYAMP_NOTARY_PROFILE)" >&2
    exit 2
  fi
  NOTARY_AUTH=(--key "$PYAMP_NOTARY_KEY" --key-id "$PYAMP_NOTARY_KEY_ID" --issuer "$PYAMP_NOTARY_ISSUER")
fi
if [ ${#NOTARY_AUTH[@]} -gt 0 ]; then
  if [ -z "$SIGN_IDENTITY" ]; then
    echo "error: notarising needs PYAMP_SIGN_IDENTITY" >&2
    exit 2
  fi
  NOTARISE=1
fi
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
if [ -n "$SIGN_IDENTITY" ]; then
  # Developer ID: sign inside out (libraries, then frameworks, then the app) with the
  # hardened runtime and a secure timestamp, as notarisation requires.
  echo "Signing with $SIGN_IDENTITY"
  sign() { codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY" "$@"; }
  find "$APP/Contents" -type f -print0 |
    while IFS= read -r -d '' file; do
      if [ "$file" != "$APP/Contents/MacOS/PyAmp" ] && file -b "$file" | grep -q '^Mach-O'; then
        printf '%s\0' "$file"
      fi
    done | xargs -0 -n 50 -P 4 codesign --force --options runtime --timestamp --sign "$SIGN_IDENTITY"
  # Deepest first, so a framework inside another is signed before the one around it.
  find "$APP/Contents" -type d -name '*.framework' |
    awk '{ print gsub("/", "/"), $0 }' | sort -rn | cut -d' ' -f2- |
    while IFS= read -r framework; do sign "$framework"; done
  sign --entitlements "$ROOT/packaging/macos/entitlements.plist" "$APP"
  codesign --verify --deep --strict "$APP"
else
  # Ad-hoc signature: not notarised, but Apple Silicon won't run unsigned code at all.
  codesign --force --deep --sign - "$APP"
fi

# Measured here: the copy in $OUT may be moved (e.g. to /Applications) straight away.
APP_SIZE=$(du -sh "$APP" | cut -f1)
mkdir -p "$OUT"
rm -rf "$OUT/PyAmp.app"
cp -R "$APP" "$OUT/PyAmp.app"
if [ -n "$APP_ONLY" ]; then
  echo "App: $OUT/PyAmp.app ($APP_SIZE)"
  exit 0
fi

notarise() {
  echo "Notarising $(basename "$1") (usually minutes; a new account's first ones can take hours)"
  local result
  result=$(xcrun notarytool submit "$1" "${NOTARY_AUTH[@]}" \
    --wait --timeout "${PYAMP_NOTARY_TIMEOUT:-2h}" --output-format json) || true
  echo "$result"
  if ! grep -q '"status" *: *"Accepted"' <<<"$result"; then
    local id
    id=$(sed -nE 's/.*"id" *: *"([^"]+)".*/\1/p' <<<"$result" | head -1)
    if [ -n "$id" ]; then
      xcrun notarytool log "$id" "${NOTARY_AUTH[@]}" >&2 || true
    fi
    echo "error: notarisation of $(basename "$1") failed" >&2
    exit 1
  fi
}

# -- Notarisation ----------------------------------------------------------------- #
# The app gets a ticket of its own (stapled to it) so it opens offline once copied out
# of the disk image; the disk image gets one too, checked when it's opened.
if [ -n "$NOTARISE" ]; then
  ditto -c -k --keepParent "$APP" "$WORK/PyAmp.zip"
  notarise "$WORK/PyAmp.zip"
  xcrun stapler staple "$APP"
  rm -rf "$OUT/PyAmp.app"
  cp -R "$APP" "$OUT/PyAmp.app"
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
if [ -n "$SIGN_IDENTITY" ]; then
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG"
fi
if [ -n "$NOTARISE" ]; then
  notarise "$DMG"
  xcrun stapler staple "$DMG"
fi

echo "App: $OUT/PyAmp.app ($APP_SIZE)"
echo "DMG: $DMG ($(du -sh "$DMG" | cut -f1))"
