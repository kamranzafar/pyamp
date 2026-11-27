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

"""Visualiser styles: how the spectrum levels are drawn (see :mod:`.style`)."""

from pyamp.ui.visualiser import styles as _styles  # noqa: F401  (registers the built-ins)
from pyamp.ui.visualiser.style import (
    DEFAULT_STYLE,
    STYLES,
    Frame,
    Palette,
    Style,
    next_style_id,
    register,
    style_id,
)

__all__ = [
    "DEFAULT_STYLE",
    "STYLES",
    "Frame",
    "Palette",
    "Style",
    "next_style_id",
    "register",
    "style_id",
]
