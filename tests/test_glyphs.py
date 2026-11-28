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

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QSize
from PySide6.QtGui import QColor, QIcon, QImage

from pyamp.core.events import EventBus
from pyamp.core.models import RepeatMode
from pyamp.core.player import Player
from pyamp.ui.actions import create_player_actions
from pyamp.ui.glyphs import NAMES, glyph_icon
from pyamp.ui.skin import DEFAULT_SKIN


def _colours(
    icon: QIcon,
    mode: QIcon.Mode = QIcon.Mode.Normal,
    state: QIcon.State = QIcon.State.Off,
    scale: float = 1.0,
) -> set[str]:
    image = icon.pixmap(QSize(16, 16), scale, mode, state).toImage()
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    return {
        QColor(image.pixel(x, y)).name()
        for y in range(image.height())
        for x in range(image.width())
        if QColor.fromRgba(image.pixel(x, y)).alpha() == 255
    }


@pytest.mark.parametrize("name", NAMES)
def test_each_glyph_is_drawn_in_the_skins_text_colour(qt_app: QCoreApplication, name: str) -> None:
    assert _colours(glyph_icon(name, DEFAULT_SKIN)) == {DEFAULT_SKIN.text}


def test_glyphs_follow_the_buttons_state(qt_app: QCoreApplication) -> None:
    icon = glyph_icon("shuffle", DEFAULT_SKIN)
    assert _colours(icon, state=QIcon.State.On) == {DEFAULT_SKIN.lcd}
    assert _colours(icon, mode=QIcon.Mode.Selected) == {DEFAULT_SKIN.accent}
    assert _colours(icon, mode=QIcon.Mode.Disabled) == {DEFAULT_SKIN.text_dim}


def test_glyphs_are_drawn_at_the_screens_pixel_density(qt_app: QCoreApplication) -> None:
    pixmap = glyph_icon("play", DEFAULT_SKIN).pixmap(QSize(16, 16), 2.0)
    assert pixmap.size() == QSize(32, 32)
    assert pixmap.devicePixelRatio() == 2.0


def test_unknown_glyph(qt_app: QCoreApplication) -> None:
    with pytest.raises(KeyError):
        glyph_icon("record", DEFAULT_SKIN)


def test_actions_are_named_and_repeat_shows_its_mode(
    qt_app: QCoreApplication, player: Player, bus: EventBus
) -> None:
    parent = QObject()
    actions = create_player_actions(parent, player, bus)
    assert [a.text() for a in actions.transport] == ["Previous", "Play", "Pause", "Stop", "Next"]
    assert all(not a.icon().isNull() for a in actions.transport)
    plain = actions.repeat.icon().pixmap(16).toImage()

    player.set_repeat(RepeatMode.ONE)
    assert actions.repeat.text() == "Repeat: One"
    assert actions.repeat.icon().pixmap(16).toImage() != plain

    player.set_repeat(RepeatMode.OFF)
    assert actions.repeat.text() == "Repeat: Off"
    assert actions.repeat.icon().pixmap(16).toImage() == plain
