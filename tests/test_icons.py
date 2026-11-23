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
from __future__ import annotations

import importlib.util
import struct
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.ui.icons import app_icon, menubar_icon

ROOT = Path(__file__).resolve().parents[1]


def _load_make_icons() -> Any:
    """packaging/ isn't a package, so load the script from its path."""
    spec = importlib.util.spec_from_file_location(
        "make_icons", ROOT / "packaging" / "make_icons.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


make_icons = _load_make_icons()


def test_app_icon_renders(qt_app: QCoreApplication) -> None:
    icon = app_icon()
    assert not icon.isNull()
    pixmap = icon.pixmap(64, 64)
    assert not pixmap.isNull()
    corner = pixmap.toImage().pixelColor(2, 2)
    centre = pixmap.toImage().pixelColor(32, 32)
    assert corner.alpha() == 0  # outside the rounded tile: transparent
    assert centre.alpha() == 255


def test_menubar_icon_is_a_template_mask(qt_app: QCoreApplication) -> None:
    icon = menubar_icon()
    assert not icon.isNull()
    assert icon.isMask()  # macOS tints it for light and dark menu bars
    assert not icon.pixmap(18, 18).isNull()


@pytest.mark.parametrize(
    "path",
    ["packaging/macos/PyAmp.icns", "packaging/windows/PyAmp.ico", "packaging/linux/pyamp.png"],
)
def test_generated_icon_files_are_committed(path: str) -> None:
    assert (ROOT / path).stat().st_size > 1000


def test_ico_holds_every_size(tmp_path: Path) -> None:
    pngs = {size: b"\x89PNG fake " + bytes([size % 256]) for size in make_icons.ICO_SIZES}
    target = tmp_path / "test.ico"
    make_icons.write_ico(target, pngs)
    data = target.read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind, count) == (0, 1, len(make_icons.ICO_SIZES))
    for i, size in enumerate(sorted(make_icons.ICO_SIZES)):
        width, _, _, _, _, _, length, offset = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert width == (0 if size >= 256 else size)  # 0 means 256
        assert data[offset : offset + length] == pngs[size]


def test_committed_ico_has_every_size() -> None:
    data = (ROOT / "packaging/windows/PyAmp.ico").read_bytes()
    assert struct.unpack_from("<HHH", data)[2] == len(make_icons.ICO_SIZES)
