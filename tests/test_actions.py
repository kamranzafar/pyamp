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
from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QObject

from pyamp.core.events import EventBus
from pyamp.core.models import ReplayGainMode
from pyamp.core.player import Player
from pyamp.ui.actions import create_player_actions


def test_replay_gain_actions_drive_and_follow_the_player(
    qt_app: QCoreApplication, player: Player, bus: EventBus
) -> None:
    parent = QObject()
    actions = create_player_actions(parent, player, bus).replay_gain
    assert [a.text() for a in actions.values()] == ["&Off", "&Track", "&Album"]
    assert actions[ReplayGainMode.OFF].isChecked()

    actions[ReplayGainMode.ALBUM].trigger()
    assert player.replay_gain is ReplayGainMode.ALBUM
    assert actions[ReplayGainMode.ALBUM].isChecked()
    assert not actions[ReplayGainMode.OFF].isChecked()

    player.set_replay_gain(ReplayGainMode.TRACK)  # e.g. restored from settings
    assert actions[ReplayGainMode.TRACK].isChecked()
    assert not actions[ReplayGainMode.ALBUM].isChecked()
