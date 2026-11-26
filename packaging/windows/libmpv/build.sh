#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
#
# Cross-compile an audio-only libmpv-2.dll for Windows x86_64 on Linux (mingw-w64).
#
#   packaging/windows/libmpv/build.sh [output-dir]      (default: ./libmpv-out)
#
# One DLL with everything linked in, like the general-purpose builds, but without
# video outputs, GPU APIs, video codecs or scripting: FFmpeg has every audio decoder
# and filter, every demuxer, parser and protocol (TLS through Windows' own Schannel),
# and the Matroska muxer (the visualiser saves stream stretches as .mkv).
#
# Needs (Ubuntu): mingw-w64 with the posix thread model, meson, ninja, nasm, pkg-config,
# cmake, git, curl, python3. Sources and their hashes: sources.lock next to this script.
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
OUT=$(mkdir -p "${1:-libmpv-out}" && cd "${1:-libmpv-out}" && pwd)
WORK=${PYAMP_LIBMPV_WORK:-$(mktemp -d)}
CACHE=${PYAMP_LIBMPV_CACHE:-$WORK/cache}
HOST=x86_64-w64-mingw32
PREFIX=$WORK/prefix
SRC=$WORK/src
JOBS=$(nproc 2>/dev/null || echo 4)
mkdir -p "$CACHE" "$PREFIX/lib/pkgconfig" "$PREFIX/include" "$SRC"

# Only what's built here is found: never the build machine's own libraries.
export PKG_CONFIG_LIBDIR=$PREFIX/lib/pkgconfig
export PKG_CONFIG_PATH=
export CC=$HOST-gcc-posix CXX=$HOST-g++-posix

CROSS=$WORK/cross.txt
cat >"$CROSS" <<EOF
[binaries]
c = '$HOST-gcc-posix'
cpp = '$HOST-g++-posix'
ar = '$HOST-ar'
strip = '$HOST-strip'
windres = '$HOST-windres'
pkg-config = 'pkg-config'

[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
EOF

lock() { awk -v n="$1" '$1 == n { print $2, $3, $4 }' "$HERE/sources.lock"; }

# fetch NAME: download, check and unpack a pinned tarball into $SRC/NAME.
fetch() {
  local sha url file
  read -r sha url _ < <(lock "$1")
  file="$CACHE/$(basename "$url")"
  if [ ! -f "$file" ]; then
    curl -fsSL --retry 3 "$url" -o "$file.part"
    mv "$file.part" "$file"
  fi
  echo "$sha  $file" | sha256sum -c - >/dev/null || {
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
  meson setup "$SRC/$name/_build" "$SRC/$name" --cross-file "$CROSS" --prefix "$PREFIX" \
    --libdir lib --buildtype release --wrap-mode nodownload -Ddefault_library=static "$@"
  meson install -C "$SRC/$name/_build" >/dev/null
}

step() { echo "== $* ($(date +%T))"; }

# -- zlib (FFmpeg: compressed Matroska tracks and gzip'd HTTP) -------------------- #
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

# -- libass and its text stack (mpv requires libass, even with no subtitles) ------- #
step freetype
fetch freetype
meson_build freetype -Dbrotli=disabled -Dbzip2=disabled -Dharfbuzz=disabled -Dpng=disabled \
  -Dzlib=disabled -Dtests=disabled
step fribidi
fetch fribidi
meson_build fribidi -Ddocs=false -Dbin=false -Dtests=false
step harfbuzz
fetch harfbuzz
meson_build harfbuzz -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled \
  -Dchafa=disabled -Dicu=disabled -Dgraphite2=disabled -Dgdi=disabled -Ddirectwrite=disabled \
  -Dpng=disabled -Dzlib=disabled -Dsubset=disabled -Dtests=disabled -Dintrospection=disabled \
  -Ddocs=disabled -Dutilities=disabled -Dbenchmark=disabled
step libass
fetch libass
meson_build libass -Dfontconfig=disabled -Ddirectwrite=enabled -Dasm=disabled \
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
sed -i -E 's/^(Libs\.private:.*)$/\1 -lstdc++/' "$PREFIX/lib/pkgconfig/libplacebo.pc"
grep -q '^Libs.private:.*-lstdc++' "$PREFIX/lib/pkgconfig/libplacebo.pc" ||
  echo 'Libs.private: -lstdc++' >>"$PREFIX/lib/pkgconfig/libplacebo.pc"

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
./configure --prefix="$PREFIX" --cross-prefix="$HOST-" --cc="$CC" --cxx="$CXX" \
  --arch=x86_64 --target-os=mingw32 --pkg-config=pkg-config --pkg-config-flags=--static \
  --extra-cflags="-I$PREFIX/include" --extra-ldflags="-L$PREFIX/lib" \
  --enable-static --disable-shared --disable-programs --disable-doc --disable-debug \
  --disable-autodetect --enable-w32threads --enable-zlib --enable-schannel \
  --disable-avdevice --disable-everything \
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
meson setup "$SRC/mpv/_build" "$SRC/mpv" --cross-file "$CROSS" --prefix "$PREFIX" \
  --libdir lib --buildtype release --wrap-mode nodownload \
  -Ddefault_library=shared -Dprefer_static=true \
  -Dc_link_args="['-static', '-static-libgcc']" \
  -Dlibmpv=true -Dcplayer=false -Dgpl=true -Dtests=false -Dbuild-date=false \
  -Dauto_features=disabled -Dlua=disabled -Dwasapi=enabled -Dwin32-threads=enabled \
  -Dzlib=enabled
meson compile -C "$SRC/mpv/_build"
dll=$(find "$SRC/mpv/_build" -maxdepth 1 -name 'libmpv-2.dll' | head -1)
[ -n "$dll" ] || {
  echo "error: no libmpv-2.dll was built" >&2
  exit 1
}
$HOST-strip -s "$dll" -o "$OUT/libmpv-2.dll"

# -- Checks: no MinGW runtime DLLs to ship alongside, and the features PyAmp needs ---- #
imports=$($HOST-objdump -p "$OUT/libmpv-2.dll" | sed -nE 's/^[[:space:]]*DLL Name: (.*)/\1/p' | sort -u)
echo "Imports: $(echo "$imports" | paste -sd" " -)"
if echo "$imports" | grep -qiE '^lib|^zlib|^av|^sw'; then
  echo "error: libmpv-2.dll needs DLLs that aren't part of Windows" >&2
  exit 1
fi
for f in volume equalizer; do
  grep -q "^CONFIG_${f^^}_FILTER=yes" "$SRC/ffmpeg/ffbuild/config.mak" || {
    echo "error: FFmpeg lacks the $f filter (the equaliser needs it)" >&2
    exit 1
  }
done
for c in MATROSKA_MUXER SCHANNEL HTTPS_PROTOCOL FLAC_DECODER OPUS_DECODER MP3_DECODER AAC_DECODER; do
  grep -q "^CONFIG_${c}=yes" "$SRC/ffmpeg/ffbuild/config.mak" || {
    echo "error: FFmpeg lacks $c" >&2
    exit 1
  }
done

{
  echo "PyAmp's audio-only libmpv for Windows x86_64, built $(date -u +%F) from:"
  grep -vE '^#|^$' "$HERE/sources.lock" | awk '{ print "  " $1 ": " $3 ($4 ? " " $4 : "") }'
  echo "Build script: packaging/windows/libmpv/build.sh in https://github.com/kamranzafar/pyamp"
  echo "Imports: $(echo "$imports" | paste -sd" " -)"
} >"$OUT/BUILDINFO.txt"
cp "$WORK/ffmpeg-configure.txt" "$OUT/"
ls -l "$OUT"
