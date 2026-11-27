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

"""Playing through a chosen output device, and falling back while it's disconnected."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication

from pyamp.app import Application
from pyamp.config import SettingsStore
from pyamp.core.events import EventBus, OutputDeviceChanged
from pyamp.core.models import OutputDevice
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.ui.output_device_menu import OutputDeviceMenu
from tests.conftest import FakeBackend
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)

DAC = OutputDevice("dac-uid", "USB DAC")
SPEAKERS = OutputDevice("BuiltInSpeakerDevice", "MacBook Pro Speakers")


def _events(bus: EventBus) -> list[OutputDeviceChanged]:
    events: list[OutputDeviceChanged] = []
    bus.subscribe(OutputDeviceChanged, events.append)
    return events


def test_the_system_default_until_a_device_is_chosen(player: Player, backend: FakeBackend) -> None:
    backend.plug(SPEAKERS)
    assert player.output_device is None
    assert player.output_device_connected
    assert backend.device_id is None
    assert player.output_devices() == [SPEAKERS]


def test_choosing_a_device_plays_through_it(
    player: Player, backend: FakeBackend, bus: EventBus
) -> None:
    backend.plug(DAC)
    events = _events(bus)
    player.set_output_device(DAC)
    assert backend.device_id == "dac-uid"
    assert events[-1] == OutputDeviceChanged(DAC, connected=True)
    player.set_output_device(None)
    assert backend.device_id is None
    assert events[-1] == OutputDeviceChanged(None, connected=True)


def test_unplugged_falls_back_to_the_default_and_comes_back(
    player: Player, backend: FakeBackend, bus: EventBus
) -> None:
    backend.plug(DAC)
    player.set_output_device(DAC)
    events = _events(bus)

    backend.unplug(DAC)
    assert backend.device_id is None  # the system default plays meanwhile
    assert player.output_device == DAC  # but the choice is kept
    assert not player.output_device_connected
    assert events[-1] == OutputDeviceChanged(DAC, connected=False)

    backend.plug(DAC)
    assert backend.device_id == "dac-uid"
    assert player.output_device_connected
    assert events[-1] == OutputDeviceChanged(DAC, connected=True)


def test_a_device_chosen_while_disconnected_waits_for_it(
    player: Player, backend: FakeBackend
) -> None:
    player.set_output_device(DAC)  # e.g. remembered from last time, not plugged in yet
    assert backend.device_id is None
    assert not player.output_device_connected
    backend.plug(DAC)
    assert backend.device_id == "dac-uid"


def test_backends_without_devices_keep_the_choice_quietly(bus: EventBus) -> None:
    class Plain(FakeBackend):
        supports_output_devices = False

    backend = Plain()
    player = Player(backend, Playlist(bus), bus)
    player.set_output_device(DAC)
    assert player.output_device == DAC
    assert player.output_device_connected  # nothing to warn about
    assert backend.device_id is None  # never asked


def test_the_menu_lists_and_chooses_devices(
    qt_app: QCoreApplication, player: Player, backend: FakeBackend, bus: EventBus
) -> None:
    backend.plug(SPEAKERS)
    backend.plug(OutputDevice("rock", "Rock & Roll"))
    messages: list[str] = []
    menu = OutputDeviceMenu(player, bus, messages.append)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["&System Default", "MacBook Pro Speakers", "Rock && Roll"]
    assert menu.device_actions[None].isChecked()

    menu.device_actions["BuiltInSpeakerDevice"].trigger()
    assert backend.device_id == "BuiltInSpeakerDevice"
    menu.rebuild()
    assert menu.device_actions["BuiltInSpeakerDevice"].isChecked()


def test_the_menu_shows_a_disconnected_choice_and_says_what_happened(
    qt_app: QCoreApplication, player: Player, backend: FakeBackend, bus: EventBus
) -> None:
    backend.plug(DAC)
    messages: list[str] = []
    menu = OutputDeviceMenu(player, bus, messages.append)
    player.set_output_device(DAC)

    backend.unplug(DAC)
    assert messages == ["USB DAC was disconnected: playing through the system default"]
    menu.rebuild()
    missing = menu.device_actions["dac-uid"]
    assert missing.text() == "USB DAC (not connected)"
    assert missing.isChecked() and not missing.isEnabled()

    backend.plug(DAC)
    assert messages[-1] == "Playing through USB DAC again"
    menu.rebuild()
    assert menu.device_actions["dac-uid"].isEnabled()


def test_the_choice_is_remembered(app: Application) -> None:  # noqa: F811
    app.player.set_output_device(DAC)
    app.shutdown()
    saved = SettingsStore(app._store.directory).load()
    assert (saved.output_device, saved.output_device_name) == ("dac-uid", "USB DAC")
