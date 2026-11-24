# SPDX-License-Identifier: GPL-3.0-or-later
#
# PyAmp - a lightweight, Winamp-inspired music player
# Copyright (C) 2026 Kamran Zafar
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# Additional terms apply under section 7 of the GNU GPL; see ADDITIONAL-TERMS.md.
"""Which parts of Qt the app builds leave out (packaging/trim_qt.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "trim_qt", Path(__file__).resolve().parents[1] / "packaging" / "trim_qt.py"
)
assert _spec is not None and _spec.loader is not None
trim_qt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(trim_qt)

MAC_QT = "PySide6/Qt/lib"
MAC_PLUGINS = "PySide6/Qt/plugins"
WIN_PLUGINS = "PySide6/plugins"


@pytest.mark.parametrize(
    "dest",
    [
        f"{MAC_QT}/QtQuick.framework/Versions/A/QtQuick",
        f"{MAC_QT}/QtQml.framework/Resources/Info.plist",
        f"{MAC_QT}/QtQmlModels.framework/Versions/A/QtQmlModels",
        f"{MAC_QT}/QtVirtualKeyboard.framework/Versions/A/QtVirtualKeyboard",
        f"{MAC_QT}/QtOpenGL.framework",
        f"{MAC_PLUGINS}/platforminputcontexts/libqtvirtualkeyboardplugin.dylib",
        f"{MAC_PLUGINS}/imageformats/libqjpeg.dylib",
        f"{MAC_PLUGINS}/imageformats/libqtiff.dylib",
        f"{MAC_PLUGINS}/generic/libqtuiotouchplugin.dylib",
        f"{MAC_PLUGINS}/platforms/libqminimal.dylib",
        "PySide6\\Qt6Quick.dll",
        "PySide6\\Qt6QmlModels.dll",
        f"{WIN_PLUGINS}\\platforminputcontexts\\qtvirtualkeyboardplugin.dll".replace("/", "\\"),
        f"{WIN_PLUGINS}/imageformats/qwebp.dll",
    ],
)
def test_leaves_out_what_pyamp_never_uses(dest: str) -> None:
    assert trim_qt.is_unused(dest)


@pytest.mark.parametrize(
    "dest",
    [
        f"{MAC_QT}/QtCore.framework/Versions/A/QtCore",
        f"{MAC_QT}/QtGui.framework/Versions/A/QtGui",
        f"{MAC_QT}/QtWidgets.framework/Versions/A/QtWidgets",
        f"{MAC_QT}/QtMultimedia.framework/Versions/A/QtMultimedia",
        f"{MAC_QT}/QtNetwork.framework/Versions/A/QtNetwork",  # https streams
        f"{MAC_QT}/QtSvg.framework/Versions/A/QtSvg",  # the icons
        f"{MAC_QT}/QtDBus.framework/Versions/A/QtDBus",  # QtGui links it
        f"{MAC_QT}/libavcodec.63.dylib",
        f"{MAC_PLUGINS}/imageformats/libqsvg.dylib",
        f"{MAC_PLUGINS}/iconengines/libqsvgicon.dylib",
        f"{MAC_PLUGINS}/platforms/libqcocoa.dylib",
        f"{MAC_PLUGINS}/platforms/libqoffscreen.dylib",  # the self-test runs offscreen
        f"{MAC_PLUGINS}/multimedia/libffmpegmediaplugin.dylib",
        f"{MAC_PLUGINS}/tls/libqopensslbackend.dylib",
        "PySide6\\Qt6Core.dll",
        "PySide6\\Qt6Multimedia.dll",
        f"{WIN_PLUGINS}/platforms/qwindows.dll",
        f"{WIN_PLUGINS}/imageformats/qsvg.dll",
        "PySide6/QtQuick.abi3.so",  # Python bindings nothing imports: harmless, small
        "libmpv/libmpv.2.dylib",
        "numpy/_core/_multiarray_umath.cpython-313-darwin.so",
    ],
)
def test_keeps_everything_else(dest: str) -> None:
    assert not trim_qt.is_unused(dest)


def test_filters_pyinstaller_entries() -> None:
    toc = [
        (f"{MAC_QT}/QtQuick.framework/Versions/A/QtQuick", "/src/QtQuick", "BINARY"),
        (f"{MAC_QT}/QtCore.framework/Versions/A/QtCore", "/src/QtCore", "BINARY"),
    ]
    assert trim_qt.without_unused_qt(toc) == toc[1:]


def test_drops_links_to_what_it_leaves_out() -> None:
    # PyInstaller links each Qt library at the top of the app; a dangling link fails
    # macOS's signature check.
    quick = f"{MAC_QT}/QtQuick.framework/Versions/A/QtQuick"
    core = f"{MAC_QT}/QtCore.framework/Versions/A/QtCore"
    toc = [("QtQuick", quick, "SYMLINK"), ("QtCore", core, "SYMLINK")]
    assert trim_qt.without_unused_qt(toc) == toc[1:]
