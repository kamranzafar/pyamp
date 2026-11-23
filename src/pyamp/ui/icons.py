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
"""PyAmp's icons, loaded from the SVGs in ``pyamp/resources``."""

from __future__ import annotations

from importlib.resources import as_file, files

from PySide6.QtGui import QIcon

APP_ICON = "icon.svg"
MENUBAR_ICON = "icon-menubar.svg"


def _load(name: str) -> QIcon:
    with as_file(files("pyamp").joinpath("resources", name)) as path:
        return QIcon(str(path))


def app_icon() -> QIcon:
    """The full-colour icon: window, Dock and taskbar."""
    return _load(APP_ICON)


def menubar_icon() -> QIcon:
    """The one-colour icon for the menu bar or system tray.

    Marked as a mask, so macOS treats it as a template image: tinted black or white
    to suit the menu bar, and dimmed when inactive.
    """
    icon = _load(MENUBAR_ICON)
    icon.setIsMask(True)
    return icon
