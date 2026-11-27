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

"""Playback → Output Device: play through a chosen device instead of the system default."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMenu, QWidget

from pyamp.core.events import EventBus, OutputDeviceChanged
from pyamp.core.models import OutputDevice
from pyamp.core.player import Player

SYSTEM_DEFAULT = "&System Default"


class OutputDeviceMenu(QMenu):
    """Lists the devices there are each time it opens, with the chosen one ticked.

    A chosen device that's disconnected stays listed (greyed out, still ticked) and is
    used again once it's back; meanwhile the system default plays, and ``notify`` says
    so (and says when it's back).
    """

    def __init__(
        self,
        player: Player,
        bus: EventBus,
        notify: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Output &Device", parent)
        self._player = player
        self._notify = notify
        self._connected = player.output_device_connected
        self.device_actions: dict[str | None, QAction] = {}  # by device id; None: default
        self.aboutToShow.connect(self.rebuild)
        bus.subscribe(OutputDeviceChanged, self._on_changed)
        self.rebuild()
        if not player.supports_output_devices:
            self.setEnabled(False)
            self.setToolTip("This audio backend always plays through the system default")

    def rebuild(self) -> None:
        self.clear()
        self.device_actions = {}
        group = QActionGroup(self)
        chosen = self._player.output_device
        self._add(group, None, SYSTEM_DEFAULT, checked=chosen is None)
        self.addSeparator()
        devices = self._player.output_devices()
        for device in devices:
            self._add(group, device, _escaped(device.name), checked=device == chosen)
        if chosen is not None and all(d.id != chosen.id for d in devices):
            # Name it from the device list when it's there (names can change), else as saved.
            action = self._add(
                group, chosen, f"{_escaped(chosen.name)} (not connected)", checked=True
            )
            action.setEnabled(False)

    def _add(
        self, group: QActionGroup, device: OutputDevice | None, label: str, *, checked: bool
    ) -> QAction:
        action = QAction(label, group)
        action.setCheckable(True)
        action.setChecked(checked)
        action.triggered.connect(lambda _=False, d=device: self._player.set_output_device(d))
        self.addAction(action)
        self.device_actions[device.id if device is not None else None] = action
        return action

    def _on_changed(self, event: OutputDeviceChanged) -> None:
        if event.chosen is not None and event.connected != self._connected:
            if event.connected:
                self._notify(f"Playing through {event.chosen.name} again")
            else:
                self._notify(
                    f"{event.chosen.name} was disconnected: playing through the system default"
                )
        self._connected = event.connected
        if self.isVisible():
            self.rebuild()


def _escaped(name: str) -> str:
    """A device name as menu text: an ``&`` would otherwise mark a shortcut letter."""
    return name.replace("&", "&&")
