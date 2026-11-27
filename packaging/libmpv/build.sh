#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
#
# Build PyAmp's audio-only libmpv: one library with mpv, FFmpeg and their libraries linked
# in, without video outputs, GPU APIs, video codecs or scripting.
#
#   packaging/libmpv/build.sh <target> [output-dir]      (default output: ./libmpv-out)
#
#   windows-x86_64   libmpv-2.dll, cross-compiled on Linux with mingw-w64 (posix threads)
#   macos-arm64      libmpv.dylib, built on an Apple Silicon Mac
#   macos-x86_64     libmpv.dylib, built on an Intel Mac
#
# FFmpeg has every audio decoder and filter, every demuxer, parser and protocol, and the
# Matroska muxer (the visualiser saves stream stretches as .mkv). TLS for https streams:
# Windows' own Schannel; Mbed TLS on macOS (Secure Transport is deprecated, without TLS
# 1.3). The macOS builds run on macOS 13 and later, like the app.
#
# Needs meson, ninja, nasm, pkg-config, cmake, git and curl; on Linux also mingw-w64.
# Sources and their hashes: sources.lock next to this script.
set -euo pipefail

TARGET=${1:-}
case "$TARGET" in
  windows-x86_64 | macos-arm64 | macos-x86_64) ;;
  *)
    echo "usage: $0 windows-x86_64|macos-arm64|macos-x86_64 [output-dir]" >&2
    exit 2
    ;;
esac
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "${2:-libmpv-out}" && cd "${2:-libmpv-out}" && pwd)
WORK=${PYAMP_LIBMPV_WORK:-$(mktemp -d)}
CACHE=${PYAMP_LIBMPV_CACHE:-$WORK/cache}
PREFIX=$WORK/prefix
SRC=$WORK/src
mkdir -p "$CACHE" "$PREFIX/lib/pkgconfig" "$PREFIX/include" "$SRC"

# Only what's built here is found by pkg-config: never the build machine's own libraries.
export PKG_CONFIG_LIBDIR=$PREFIX/lib/pkgconfig
export PKG_CONFIG_PATH=

if [ "$TARGET" = windows-x86_64 ]; then
  HOST=x86_64-w64-mingw32
  JOBS=$(nproc)
  export CC=$HOST-gcc-posix CXX=$HOST-g++-posix
  MESON_ENV=(--cross-file "$WORK/cross.txt")
  cat >"$WORK/cross.txt" <<EOF
[binaries]
c = '$HOST-gcc-posix'
cpp = '$HOST-g++-posix'
ar = '$HOST-ar'
strip = '$HOST-strip'
windres = '$HOST-windres'
dlltool = '$HOST-dlltool'
pkg-config = 'pkg-config'

[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF
else
  ARCH=${TARGET#macos-}
  [ "$(uname -m)" = "$ARCH" ] || {
    echo "error: build $TARGET on a $ARCH Mac (this one is $(uname -m))" >&2
    exit 1
  }
  JOBS=$(sysctl -n hw.ncpu)
  export CC=clang CXX=clang++ MACOSX_DEPLOYMENT_TARGET=13.0
  export CFLAGS="-mmacosx-version-min=13.0" CXXFLAGS="-mmacosx-version-min=13.0"
  export LDFLAGS="-mmacosx-version-min=13.0"
  MESON_ENV=()
fi

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
upper() { echo "$1" | tr '[:lower:]' '[:upper:]'; }
lock() { awk -v n="$1" '$1 == n { print $2, $3, $4 }' "$HERE/sources.lock"; }
step() { echo "== $* ($(date +%T))"; }

# fetch NAME: download, check and unpack a pinned tarball into $SRC/NAME.
fetch() {
  local sha url file
  read -r sha url _ < <(lock "$1")
  file="$CACHE/$(basename "$url")"
  if [ ! -f "$file" ]; then
    curl -fsSL --retry 3 "$url" -o "$file.part"
    mv "$file.part" "$file"
  fi
  [ "$(sha256 "$file")" = "$sha" ] || {
    echo "error: checksum mismatch for $file (deleted; run again)" >&2
    rm -f "$file"
    exit 1
  }
  rm -rf "${SRC:?}/$1" && mkdir -p "$SRC/$1"
  tar -xf "$file" -C "$SRC/$1" --strip-components=1
}

# meson_build NAME [options...]: a static library into $PREFIX.
meson_build() {
  local name=$1
  shift
  meson setup "$SRC/$name/_build" "$SRC/$name" ${MESON_ENV[@]+"${MESON_ENV[@]}"} \
    --prefix "$PREFIX" --libdir lib --buildtype release --wrap-mode nodownload \
    -Ddefault_library=static "$@"
  meson install -C "$SRC/$name/_build" >/dev/null
}

# -- zlib (FFmpeg: compressed Matroska tracks and gzip'd HTTP; macOS has its own) ---- #
if [ "$TARGET" = windows-x86_64 ]; then
  step zlib
  fetch zlib
  make -C "$SRC/zlib" -f win32/Makefile.gcc PREFIX="$HOST-" CC="$CC" libz.a -j"$JOBS" >/dev/null
  cp "$SRC/zlib/libz.a" "$PREFIX/lib/"
  cp "$SRC/zlib/zlib.h" "$SRC/zlib/zconf.h" "$PREFIX/include/"
  cat >"$PREFIX/lib/pkgconfig/zlib.pc" <<EOF
prefix=$PREFIX
Name: zlib
Description: zlib compression library
Version: 1.4
Libs: -L\${prefix}/lib -lz
Cflags: -I\${prefix}/include
EOF
fi

# -- Mbed TLS (macOS: FFmpeg's https) ------------------------------------------------- #
if [ "$TARGET" != windows-x86_64 ]; then
  step mbedtls
  fetch mbedtls
  cmake -S "$SRC/mbedtls" -B "$SRC/mbedtls/_build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_INSTALL_LIBDIR=lib \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 -DUSE_SHARED_MBEDTLS_LIBRARY=OFF \
    -DUSE_STATIC_MBEDTLS_LIBRARY=ON -DENABLE_PROGRAMS=OFF -DENABLE_TESTING=OFF \
    -DMBEDTLS_FATAL_WARNINGS=OFF >/dev/null
  cmake --build "$SRC/mbedtls/_build" -j"$JOBS" >/dev/null
  cmake --install "$SRC/mbedtls/_build" >/dev/null
fi

# -- libass and its text stack (mpv requires libass, even with no subtitles) ------- #
step freetype
fetch freetype
# hvf: Apple's variable-font library, in the newest SDKs only (not on older macOS).
meson_build freetype -Dbrotli=disabled -Dbzip2=disabled -Dharfbuzz=disabled -Dpng=disabled \
  -Dzlib=disabled -Dhvf=disabled -Dtests=disabled
step fribidi
fetch fribidi
meson_build fribidi -Ddocs=false -Dbin=false -Dtests=false
step harfbuzz
fetch harfbuzz
meson_build harfbuzz -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled \
  -Dchafa=disabled -Dicu=disabled -Dgraphite2=disabled -Dgdi=disabled -Ddirectwrite=disabled \
  -Dcoretext=disabled -Dpng=disabled -Dzlib=disabled -Dsubset=disabled -Dtests=disabled \
  -Dintrospection=disabled -Ddocs=disabled -Dutilities=disabled -Dbenchmark=disabled
step libass
fetch libass
if [ "$TARGET" = windows-x86_64 ]; then
  fonts=(-Ddirectwrite=enabled -Dcoretext=disabled)
else
  fonts=(-Ddirectwrite=disabled -Dcoretext=enabled)
fi
meson_build libass -Dfontconfig=disabled "${fonts[@]}" -Dasm=disabled \
  -Dlibunibreak=disabled -Drequire-system-font-provider=false -Dtest=disabled -Dcompare=disabled \
  -Dprofile=disabled -Dfuzz=disabled -Dcheckasm=disabled

# -- libplacebo (mpv requires it; no GPU APIs) ------------------------------------- #
step libplacebo
read -r commit repo tag < <(lock libplacebo)
rm -rf "$SRC/libplacebo"
git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" --recurse-submodules \
  --shallow-submodules "$repo" "$SRC/libplacebo"
actual=$(git -C "$SRC/libplacebo" rev-parse HEAD)
[ "$actual" = "$commit" ] || {
  echo "error: libplacebo $tag is $actual, expected $commit" >&2
  exit 1
}
meson_build libplacebo -Dvulkan=disabled -Dopengl=disabled -Dd3d11=disabled \
  -Dglslang=disabled -Dshaderc=disabled -Dlcms=disabled -Ddovi=disabled -Dlibdovi=disabled \
  -Dunwind=disabled -Dxxhash=disabled -Ddemos=false -Dtests=false -Dbench=false -Dfuzz=false
# Part of it is C++; a static libplacebo's .pc doesn't say so, and mpv links as C.
if [ "$TARGET" = windows-x86_64 ]; then cxxlib=-lstdc++; else cxxlib=-lc++; fi
pc=$PREFIX/lib/pkgconfig/libplacebo.pc
if grep -q '^Libs.private:' "$pc"; then
  sed -i.orig -E "s/^(Libs\.private:.*)$/\1 $cxxlib/" "$pc" && rm -f "$pc.orig"
else
  echo "Libs.private: $cxxlib" >>"$pc"
fi

# -- FFmpeg, audio only ------------------------------------------------------------ #
step ffmpeg
fetch ffmpeg
cd "$SRC/ffmpeg"
# Every audio decoder (the audio, PCM, DPCM and ADPCM sections of the codec list) and
# every audio filter; configure drops those whose external libraries aren't here.
decoders=$(sed -n '/^\/\* audio codecs \*\//,/^\/\* subtitles \*\//p' libavcodec/allcodecs.c |
  sed -nE 's/^extern const [A-Za-z]+ ff_([a-z0-9_]+)_decoder;.*/\1/p' | paste -sd, -)
filters=$(sed -nE 's/^extern const [A-Za-z]+ ff_(af|asrc|asink)_([a-z0-9_]+);.*/\2/p' \
  libavfilter/allfilters.c | paste -sd, -)
[ -n "$decoders" ] && [ -n "$filters" ] || {
  echo "error: could not list FFmpeg's audio decoders/filters" >&2
  exit 1
}
if [ "$TARGET" = windows-x86_64 ]; then
  platform=(--cross-prefix="$HOST-" --arch=x86_64 --target-os=mingw32
    --enable-w32threads --enable-schannel)
  tls=SCHANNEL
else
  # Mbed TLS is Apache-2.0, so FFmpeg is built as (L)GPL version 3.
  platform=(--arch="$ARCH" --enable-pthreads --enable-mbedtls --enable-version3)
  tls=MBEDTLS
fi
./configure --prefix="$PREFIX" --cc="$CC" --cxx="$CXX" "${platform[@]}" \
  --pkg-config=pkg-config --pkg-config-flags=--static \
  --extra-cflags="-I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib" \
  --enable-static --disable-shared --disable-programs --disable-doc --disable-debug \
  --disable-autodetect --enable-zlib --disable-avdevice --disable-everything \
  --enable-decoder="$decoders" --enable-filter="$filters" \
  --enable-demuxers --enable-parsers --enable-bsfs --enable-protocols \
  --enable-muxer=matroska >"$WORK/ffmpeg-configure.txt" || {
  tail -40 ffbuild/config.log >&2
  exit 1
}
make -j"$JOBS" >/dev/null
make install >/dev/null
cd - >/dev/null

# -- mpv: libmpv only ---------------------------------------------------------------- #
step mpv
fetch mpv
if [ "$TARGET" = windows-x86_64 ]; then
  mpv_platform=(-Dc_link_args="['-static', '-static-libgcc']" -Dwasapi=enabled
    -Dwin32-threads=enabled)
else
  # Export only mpv's API: the FFmpeg linked in stays private, apart from Qt's own.
  mpv_platform=(-Dc_link_args="['-Wl,-exported_symbol,_mpv_*', '-Wl,-dead_strip']"
    -Dcoreaudio=enabled)
  # CoreAudio output uses two CoreFoundation helpers (osdep/utils-mac.c) that mpv only
  # builds with cocoa, which needs Swift; build them for CoreAudio too.
  python3 - "$SRC/mpv/meson.build" <<'PATCH'
import sys
path = sys.argv[1]
text = open(path).read()
anchor = "if features['audiounit'] or features['coreaudio'] or features['avfoundation']\n"
if text.count(anchor) != 1:
    sys.exit("error: can't patch mpv's meson.build (CoreAudio helpers); has mpv changed?")
patch = ("if features['coreaudio'] and not features['cocoa']\n"
         "    sources += files('osdep/utils-mac.c')\nendif\n\n")
open(path, "w").write(text.replace(anchor, patch + anchor))
PATCH
fi
meson setup "$SRC/mpv/_build" "$SRC/mpv" ${MESON_ENV[@]+"${MESON_ENV[@]}"} \
  --prefix "$PREFIX" --libdir lib --buildtype release --wrap-mode nodownload \
  -Ddefault_library=shared -Dprefer_static=true "${mpv_platform[@]}" \
  -Dlibmpv=true -Dcplayer=false -Dgpl=true -Dtests=false -Dbuild-date=false \
  -Dauto_features=disabled -Dgl=disabled -Dlua=disabled -Dzlib=enabled
meson compile -C "$SRC/mpv/_build"

# -- The library, and checks: nothing to ship alongside it, and what PyAmp needs ------ #
if [ "$TARGET" = windows-x86_64 ]; then
  lib=libmpv-2.dll
  built=$(find "$SRC/mpv/_build" -maxdepth 1 -name "$lib" | head -1)
  [ -n "$built" ] || {
    echo "error: no $lib was built" >&2
    exit 1
  }
  "$HOST"-strip -s "$built" -o "$OUT/$lib"
  needs=$("$HOST"-objdump -p "$OUT/$lib" | sed -nE 's/^[[:space:]]*DLL Name: (.*)/\1/p' | sort -u)
  foreign=$(echo "$needs" |
    grep -iE '^(lib|zlib|av(codec|device|filter|format|util)-|sw(resample|scale)-|postproc-)' || true)
else
  lib=libmpv.dylib
  built=$(find "$SRC/mpv/_build" -maxdepth 1 -name 'libmpv.*.dylib' -type f | head -1)
  [ -n "$built" ] || {
    echo "error: no libmpv dylib was built" >&2
    exit 1
  }
  cp "$built" "$OUT/$lib"
  chmod u+w "$OUT/$lib"
  strip -x "$OUT/$lib"
  install_name_tool -id "@rpath/$lib" "$OUT/$lib"
  codesign --force --sign - "$OUT/$lib" # ad hoc: Apple Silicon won't load unsigned code
  needs=$(otool -L "$OUT/$lib" | tail -n +2 | awk '{print $1}' | grep -v "^@rpath/$lib$" | sort -u)
  foreign=$(echo "$needs" | grep -vE '^(/usr/lib/|/System/Library/)' || true)
  lipo -archs "$OUT/$lib" | grep -qx "$ARCH" || {
    echo "error: $lib is $(lipo -archs "$OUT/$lib"), not $ARCH" >&2
    exit 1
  }
  # It must load: a symbol mpv's build left out only shows up here.
  python3 -c 'import ctypes, sys; print("loads, client API", ctypes.CDLL(sys.argv[1]).mpv_client_api_version())' "$OUT/$lib" || {
    echo "error: $lib doesn't load" >&2
    exit 1
  }
  minos=$(otool -l "$OUT/$lib" | awk '/LC_BUILD_VERSION/ {b=1} b && $1 == "minos" {print $2; exit}')
  [ "$minos" = 13.0 ] || {
    echo "error: $lib needs macOS $minos, not 13.0" >&2
    exit 1
  }
fi
echo "Needs: $(echo "$needs" | paste -sd' ' -)"
[ -z "$foreign" ] || {
  echo "error: $lib needs libraries that aren't part of the system: $foreign" >&2
  exit 1
}
config=$SRC/ffmpeg/ffbuild/config.mak
for f in volume equalizer; do
  grep -q "^CONFIG_$(upper "$f")_FILTER=yes" "$config" || {
    echo "error: FFmpeg lacks the $f filter (the equaliser needs it)" >&2
    exit 1
  }
done
for c in MATROSKA_MUXER $tls HTTPS_PROTOCOL FLAC_DECODER OPUS_DECODER MP3_DECODER AAC_DECODER; do
  grep -q "^CONFIG_${c}=yes" "$config" || {
    echo "error: FFmpeg lacks $c" >&2
    exit 1
  }
done

{
  echo "PyAmp's audio-only libmpv for $TARGET, built $(date -u +%F) from:"
  if [ "$TARGET" = windows-x86_64 ]; then unused=mbedtls; else unused=zlib; fi
  grep -vE '^#|^$' "$HERE/sources.lock" |
    awk -v unused="$unused" '$1 != unused { print "  " $1 ": " $3 ($4 ? " " $4 : "") }'
  echo "Build script: packaging/libmpv/build.sh in https://github.com/kamranzafar/pyamp"
  echo "Needs: $(echo "$needs" | paste -sd' ' -)"
} >"$OUT/BUILDINFO.txt"
cp "$WORK/ffmpeg-configure.txt" "$OUT/"
ls -l "$OUT"
