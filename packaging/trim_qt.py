# SPDX-License-Identifier: GPL-3.0-or-later
# Additional terms apply under GPLv3 section 7; see ADDITIONAL-TERMS.md.
"""Parts of Qt that PyInstaller collects but PyAmp never uses, left out of the builds.

Used by the macOS and Windows specs, on the Analysis's ``binaries`` and ``datas``.

* Qt's on-screen (virtual) keyboard input plugin, and what only it needs: Qt Quick, QML
  and Qt OpenGL (about 14 MB). PyAmp is a Widgets app.
* Image format plugins other than SVG, JPEG and WebP (PyAmp's icons are SVG; album art
  is JPEG, PNG or WebP, and PNG is built into Qt).
* The touch-input ("generic") plugin and the "minimal" platform plugin. "offscreen" stays:
  the self-test and CI run the app without a screen.
* Windows: ``opengl32sw.dll`` (~20 MB), Qt's software OpenGL used only when an app asks
  for OpenGL without a working GPU driver. PyAmp never uses OpenGL (Widgets drawn by the
  CPU, audio only, mpv's video off).

Qt's network and TLS pieces stay: Qt Multimedia depends on them (``https://`` streams).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Qt modules (macOS frameworks, Windows Qt6*.dll) only the virtual keyboard needs.
_MODULES = (
    "quick",
    "qml",
    "qmlmodels",
    "qmlmeta",
    "qmlworkerscript",
    "virtualkeyboard",
    "virtualkeyboardqml",
    "opengl",
)
_UNUSED = re.compile(
    r"|".join(
        [
            # QtQuick.framework/... (macOS) or Qt6Quick.dll (Windows)
            rf"(^|/)qt6?({'|'.join(_MODULES)})(\.framework(/|$)|\.dll$)",
            r"/plugins/platforminputcontexts/",
            r"/plugins/generic/",
            r"/plugins/platforms/(lib)?qminimal\.",
            r"/plugins/imageformats/(lib)?q(gif|icns|ico|macheif|macjp2|tga|tiff|wbmp)\.",
            r"/opengl32sw\.dll$",
        ]
    )
)


def is_unused(dest: str) -> bool:
    """Whether a file bundled at ``dest`` (relative to the app) is a Qt part PyAmp skips."""
    return bool(_UNUSED.search("/" + dest.replace("\\", "/").lower()))


def without_unused_qt[T: tuple[str, ...]](toc: Iterable[T]) -> list[T]:
    """``toc`` (PyInstaller's (dest, source, kind) entries) minus :func:`is_unused` ones.

    Also drops links to them: PyInstaller links each Qt library at the top of the app
    (``Contents/Frameworks/QtQuick``), and a link left pointing at nothing makes macOS's
    signature check (``codesign --verify --strict``) fail.
    """
    return [
        entry
        for entry in toc
        if not is_unused(entry[0]) and not (entry[2] == "SYMLINK" and is_unused(entry[1]))
    ]
