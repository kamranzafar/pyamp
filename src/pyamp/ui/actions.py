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

"""Playback commands as ``QAction`` objects (Command pattern).

Each action wraps one player operation. The same object drives the transport
button, the menu entry and the keyboard shortcut, so all three always agree.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence

from pyamp.core.events import EventBus, PlayModeChanged, ReplayGainChanged
from pyamp.core.models import RepeatMode, ReplayGainMode
from pyamp.core.player import Player

SEEK_STEP_MS = 5000

_REPEAT_LABELS = {
    RepeatMode.OFF: "Repeat: Off",
    RepeatMode.ALL: "Repeat: All",
    RepeatMode.ONE: "Repeat: One",
}
_REPLAY_GAIN_LABELS = {
    ReplayGainMode.OFF: "&Off",
    ReplayGainMode.TRACK: "&Track",
    ReplayGainMode.ALBUM: "&Album",
}
_REPEAT_GLYPHS = {RepeatMode.OFF: "⟳", RepeatMode.ALL: "⟳", RepeatMode.ONE: "⟳1"}


@dataclass(frozen=True, slots=True)
class PlayerActions:
    previous: QAction
    play: QAction
    pause: QAction
    stop: QAction
    next: QAction
    seek_back: QAction
    seek_forward: QAction
    shuffle: QAction
    repeat: QAction
    #: Mutually exclusive ReplayGain modes, for a "ReplayGain" submenu.
    replay_gain: dict[ReplayGainMode, QAction]

    @property
    def transport(self) -> tuple[QAction, ...]:
        return (self.previous, self.play, self.pause, self.stop, self.next)


def _action(parent: QObject, glyph: str, tooltip: str, shortcut: str) -> QAction:
    action = QAction(glyph, parent)
    action.setToolTip(f"{tooltip} ({shortcut})")
    action.setStatusTip(tooltip)
    action.setShortcut(QKeySequence(shortcut))
    action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
    return action


def create_player_actions(parent: QObject, player: Player, bus: EventBus) -> PlayerActions:
    """Build the actions. Keyboard shortcuts follow classic Winamp (Z X C V B)."""
    actions = PlayerActions(
        previous=_action(parent, "⏮", "Previous", "Z"),
        play=_action(parent, "▶", "Play", "X"),
        pause=_action(parent, "⏸", "Pause", "C"),
        stop=_action(parent, "⏹", "Stop", "V"),
        next=_action(parent, "⏭", "Next", "B"),
        seek_back=_action(parent, "«", "Rewind 5 s", "Left"),
        seek_forward=_action(parent, "»", "Fast-forward 5 s", "Right"),
        shuffle=_action(parent, "⤮", "Shuffle", "S"),
        repeat=_action(parent, "⟳", "Repeat", "R"),
        replay_gain=_replay_gain_actions(parent, player),
    )
    actions.previous.triggered.connect(player.previous)
    actions.play.triggered.connect(player.play)
    actions.pause.triggered.connect(player.pause)
    actions.stop.triggered.connect(player.stop)
    actions.next.triggered.connect(player.next)
    actions.seek_back.triggered.connect(lambda: player.seek(player.position_ms - SEEK_STEP_MS))
    actions.seek_forward.triggered.connect(lambda: player.seek(player.position_ms + SEEK_STEP_MS))

    actions.shuffle.setCheckable(True)
    actions.shuffle.toggled.connect(player.set_shuffle)
    actions.repeat.setCheckable(True)
    actions.repeat.triggered.connect(lambda: player.set_repeat(player.repeat.cycle()))

    def sync_modes(event: PlayModeChanged) -> None:
        # Reflect player state back into the actions (e.g. restored from settings).
        if actions.shuffle.isChecked() != event.shuffle:
            actions.shuffle.blockSignals(True)
            actions.shuffle.setChecked(event.shuffle)
            actions.shuffle.blockSignals(False)
        actions.repeat.setChecked(event.repeat is not RepeatMode.OFF)
        actions.repeat.setText(_REPEAT_GLYPHS[event.repeat])
        actions.repeat.setToolTip(f"{_REPEAT_LABELS[event.repeat]} (R)")

    def sync_replay_gain(event: ReplayGainChanged) -> None:
        actions.replay_gain[event.mode].setChecked(True)

    bus.subscribe(PlayModeChanged, sync_modes)
    bus.subscribe(ReplayGainChanged, sync_replay_gain)
    return actions


def _replay_gain_actions(parent: QObject, player: Player) -> dict[ReplayGainMode, QAction]:
    group = QActionGroup(parent)
    group.setExclusive(True)
    actions: dict[ReplayGainMode, QAction] = {}
    for mode, label in _REPLAY_GAIN_LABELS.items():
        action = QAction(label, group)
        action.setCheckable(True)
        action.setChecked(mode is player.replay_gain)
        action.setStatusTip(f"ReplayGain: {mode.value}")
        action.triggered.connect(lambda _checked=False, m=mode: player.set_replay_gain(m))
        actions[mode] = action
    return actions
