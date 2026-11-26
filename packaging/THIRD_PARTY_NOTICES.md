# Third-party software in PyAmp releases

PyAmp is free software under the GNU General Public License, version 3 or (at your
option) any later version; see `LICENSE`. Release builds bundle the components
below, each under its own licence. Where a licence requires it (GPL, LGPL), the
corresponding source is available at the links given.

## Audio engine (macOS: `Contents/Frameworks/libmpv/`)

Prebuilt by [media-kit/libmpv-darwin-build](https://github.com/media-kit/libmpv-darwin-build)
release **v0.7.3**, flavour `video-encodersgpl`. Build recipe and exact component
versions: <https://github.com/media-kit/libmpv-darwin-build/tree/v0.7.3>.

| Component | Licence | Source |
|---|---|---|
| mpv (libmpv) | GPL-2.0-or-later | <https://github.com/mpv-player/mpv> |
| FFmpeg (built with `--enable-gpl`) | GPL-2.0-or-later | <https://ffmpeg.org/download.html> |
| x264 | GPL-2.0-or-later | <https://code.videolan.org/videolan/x264> |
| libass | ISC | <https://github.com/libass/libass> |
| FreeType | FreeType License (FTL) | <https://freetype.org> |
| HarfBuzz | MIT ("Old MIT") | <https://github.com/harfbuzz/harfbuzz> |
| GNU FriBidi | LGPL-2.1-or-later | <https://github.com/fribidi/fribidi> |
| uchardet | MPL-1.1 / GPL-2.0-or-later / LGPL-2.1-or-later | <https://www.freedesktop.org/wiki/Software/uchardet/> |
| libpng | libpng License | <http://www.libpng.org/pub/png/libpng.html> |
| libvpx | BSD-3-Clause | <https://chromium.googlesource.com/webm/libvpx> |
| dav1d | BSD-2-Clause | <https://code.videolan.org/videolan/dav1d> |
| libogg, libvorbis | BSD-3-Clause | <https://xiph.org> |
| Mbed TLS | Apache-2.0 | <https://github.com/Mbed-TLS/mbedtls> |
| libxml2 | MIT | <https://gitlab.gnome.org/GNOME/libxml2> |

## Audio engine (Windows: `_internal\libmpv\libmpv-2.dll`)

PyAmp's own audio-only build: one DLL with mpv, FFmpeg and their libraries linked in,
cross-compiled by `packaging/windows/libmpv/build.sh` from the sources pinned in
`packaging/windows/libmpv/sources.lock`. The build, its exact sources and the commit
that built it are at
<https://github.com/kamranzafar/pyamp/releases/tag/libmpv-windows-audio-20261126>.

| Component | Licence | Source |
|---|---|---|
| mpv 0.42.0 (libmpv) | GPL-2.0-or-later | <https://github.com/mpv-player/mpv> |
| FFmpeg 9.1 | LGPL-2.1-or-later | <https://ffmpeg.org/download.html> |
| libplacebo 7.387.0 | LGPL-2.1-or-later | <https://code.videolan.org/videolan/libplacebo> |
| libass 0.17.5 | ISC | <https://github.com/libass/libass> |
| FreeType 2.15.0 | FreeType License (FTL) | <https://freetype.org> |
| HarfBuzz 15.2.0 | MIT ("Old MIT") | <https://github.com/harfbuzz/harfbuzz> |
| GNU FriBidi 1.0.18 | LGPL-2.1-or-later | <https://github.com/fribidi/fribidi> |
| zlib 1.4 | Zlib | <https://zlib.net> |
| GCC runtime (libgcc, libstdc++), mingw-w64 runtime and winpthreads | GPL-3.0-or-later with the GCC Runtime Library Exception; mingw-w64's own (ZPL-2.1, MIT) | <https://gcc.gnu.org>, <https://www.mingw-w64.org> |

The combination is distributed under the GPL, compatible with PyAmp's GPL-3.0-or-later.

## Application runtime

| Component | Licence | Source |
|---|---|---|
| Python | PSF-2.0 | <https://www.python.org> |
| Qt 6 / PySide6 (incl. Qt Multimedia's FFmpeg, LGPL build) | LGPL-3.0 | <https://code.qt.io> |
| python-mpv | GPL-2.0-or-later / LGPL-2.1-or-later | <https://github.com/jaseg/python-mpv> |
| mutagen | GPL-2.0-or-later | <https://github.com/quodlibet/mutagen> |
| NumPy | BSD-3-Clause | <https://numpy.org> |
| platformdirs | MIT | <https://github.com/tox-dev/platformdirs> |
| PyObjC (macOS) | MIT | <https://github.com/ronaldoussoren/pyobjc> |
| keyring (with jaraco.classes, jaraco.context, jaraco.functools, more-itertools) | MIT | <https://github.com/jaraco/keyring> |
| pywin32-ctypes (Windows) | BSD-3-Clause | <https://github.com/enthought/pywin32-ctypes> |
