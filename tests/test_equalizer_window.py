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

import time
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.core.equalizer import PRESETS, Equalizer
from pyamp.core.events import EqualizerChanged, EventBus
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.ui.equalizer_window import CUSTOM, STEPS_PER_DB, EqualizerWindow, band_label
from pyamp.ui.skin import DEFAULT_SKIN
from tests.conftest import FakeBackend


class EqBackend(FakeBackend):
    supports_equalizer = True


@pytest.fixture
def eq_backend() -> EqBackend:
    return EqBackend()


@pytest.fixture
def eq_player(eq_backend: EqBackend, playlist: Playlist, bus: EventBus) -> Player:
    return Player(eq_backend, playlist, bus)


@pytest.fixture
def window(qt_app: QCoreApplication, eq_player: Player, bus: EventBus) -> Iterator[EqualizerWindow]:
    window = EqualizerWindow(eq_player, bus, DEFAULT_SKIN)
    yield window
    window.close()


def test_band_labels() -> None:
    assert [band_label(hz) for hz in (60, 600, 1000, 16000)] == ["60", "600", "1K", "16K"]


def test_starts_from_the_players_equalizer(
    qt_app: QCoreApplication, eq_player: Player, bus: EventBus
) -> None:
    eq_player.set_equalizer(Equalizer(enabled=True, preamp_db=-2.5).with_preset("Rock"))
    window = EqualizerWindow(eq_player, bus, DEFAULT_SKIN)
    assert window.current() == eq_player.equalizer
    assert window.presets.currentText() == "Rock"
    assert window.note.isHidden()
    window.close()


def test_switch_and_sliders_drive_the_player(
    window: EqualizerWindow, eq_player: Player, eq_backend: EqBackend
) -> None:
    window.enabled.setChecked(True)
    assert eq_player.equalizer.enabled

    window.bands[0].setValue(6 * STEPS_PER_DB)
    window.bands[0].setValue(8 * STEPS_PER_DB)  # a drag: several moves...
    window.preamp.setValue(-3 * STEPS_PER_DB)
    assert eq_player.equalizer.gains_db[0] == 0.0  # ...throttled, not sent yet
    window.flush()
    assert eq_player.equalizer.gains_db[0] == 8.0
    assert eq_player.equalizer.preamp_db == -3.0
    assert len(eq_backend.equalizers) == 2  # the switch, then one batched update
    assert window.presets.currentText() == CUSTOM


def test_throttle_timer_sends_the_change(
    window: EqualizerWindow, eq_player: Player, qt_app: QCoreApplication
) -> None:
    window.bands[4].setValue(3 * STEPS_PER_DB)
    deadline = time.monotonic() + 2
    while eq_player.equalizer.gains_db[4] != 3.0 and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.005)
    assert eq_player.equalizer.gains_db[4] == 3.0


def test_preset_and_flat(window: EqualizerWindow, eq_player: Player) -> None:
    window.preamp.setValue(-4 * STEPS_PER_DB)
    window.flush()
    window.presets.textActivated.emit("Bass Boost")
    assert eq_player.equalizer.gains_db == PRESETS["Bass Boost"]
    assert eq_player.equalizer.preamp_db == -4.0  # presets keep the preamp
    assert window.presets.currentText() == "Bass Boost"

    window.flat.click()
    assert eq_player.equalizer.is_flat  # Flat also resets the preamp
    assert window.presets.currentText() == "Flat"


def test_follows_player_changes_without_echoing(
    window: EqualizerWindow, eq_player: Player, eq_backend: EqBackend, bus: EventBus
) -> None:
    events: list[EqualizerChanged] = []
    bus.subscribe(EqualizerChanged, events.append)
    eq = Equalizer(enabled=True, preamp_db=1.5).with_preset("Vocal")
    eq_player.set_equalizer(eq)  # e.g. restored from settings
    window.flush()
    assert window.current() == eq
    assert window.presets.currentText() == "Vocal"
    assert len(events) == 1  # the window didn't send it back
    assert eq_backend.equalizers == [eq]


def test_disabled_with_a_note_when_backend_lacks_support(
    qt_app: QCoreApplication, player: Player, bus: EventBus
) -> None:
    window = EqualizerWindow(player, bus, DEFAULT_SKIN)
    assert not window.note.isHidden()
    assert "--backend mpv" in window.note.text()
    assert not window.enabled.isEnabled()
    assert not any(s.isEnabled() for s in window.bands)
    window.close()
