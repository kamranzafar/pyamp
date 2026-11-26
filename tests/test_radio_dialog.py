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

"""The radio station dialog, and the commands that open it and delete stations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from pyamp.core.events import EventBus, RadioStationsChanged
from pyamp.core.models import StreamUrl
from pyamp.library.radio_stations import RadioStations, Station, StationError
from pyamp.ui.radio_commands import RadioCommands
from pyamp.ui.radio_station_dialog import RadioStationDialog


@pytest.fixture
def store(tmp_path: Path) -> RadioStations:
    return RadioStations(tmp_path / "radio_stations.json")


@pytest.fixture
def changes(bus: EventBus) -> list[RadioStationsChanged]:
    seen: list[RadioStationsChanged] = []
    bus.subscribe(RadioStationsChanged, seen.append)
    return seen


@pytest.fixture
def commands(
    qt_app: QCoreApplication, store: RadioStations, bus: EventBus
) -> Iterator[RadioCommands]:
    radio = RadioCommands(store, bus, parent=lambda: None)
    yield radio
    if radio.dialog is not None:
        radio.dialog.reject()


def test_the_dialog_checks_what_is_typed(qt_app: QCoreApplication) -> None:
    saved: list[tuple[str, str]] = []
    dialog = RadioStationDialog(save=lambda name, url: saved.append((name, url)))
    assert dialog.windowTitle() == "Add Station"
    assert dialog.ok_button.text() == "Add"
    assert not dialog.ok_button.isEnabled()
    assert "needs a name" in dialog.status.text()
    dialog.name.setText("Jazz")
    assert "http" in dialog.status.text()
    dialog.url.setText("jazz.example/live")  # no scheme
    assert not dialog.ok_button.isEnabled()
    dialog.url.setText(" https://jazz.example/live ")
    assert dialog.ok_button.isEnabled()
    assert dialog.status.text() == ""
    dialog.accept()
    assert saved == [("Jazz", " https://jazz.example/live ")]  # the store trims it
    assert dialog.result() == RadioStationDialog.DialogCode.Accepted


def test_a_save_that_fails_keeps_the_dialog_open(qt_app: QCoreApplication) -> None:
    def save(name: str, url: str) -> None:
        raise StationError(f'There is already a station called "{name}".')

    dialog = RadioStationDialog(save=save, name="Jazz", url="https://jazz.example/live")
    dialog.open()
    dialog.accept()
    assert dialog.isVisible()
    assert "already a station" in dialog.status.text()
    dialog.reject()


def test_add_saves_a_station_and_announces_it(
    commands: RadioCommands, store: RadioStations, changes: list[RadioStationsChanged]
) -> None:
    dialog = commands.add("https://jazz.example/live", "Jazz FM")
    assert dialog.url.text() == "https://jazz.example/live"
    assert dialog.name.text() == "Jazz FM"
    dialog.accept()
    assert store.stations() == [Station("Jazz FM", StreamUrl("https://jazz.example/live"))]
    assert changes == [RadioStationsChanged()]
    assert commands.dialog is None


def test_add_refuses_a_name_thats_taken(
    commands: RadioCommands, store: RadioStations, changes: list[RadioStationsChanged]
) -> None:
    store.save("Jazz", "https://jazz.example/live")
    dialog = commands.add("https://other.example/live", "jazz")
    dialog.accept()
    assert dialog.isVisible()
    assert "already a station" in dialog.status.text()
    assert changes == []


def test_edit_changes_a_station(
    commands: RadioCommands, store: RadioStations, changes: list[RadioStationsChanged]
) -> None:
    store.save("Jazz", "https://jazz.example/live")
    assert commands.edit("Nope") is None
    dialog = commands.edit("jazz")
    assert dialog is not None
    assert dialog.windowTitle() == "Edit Station"
    assert (dialog.name.text(), dialog.url.text()) == ("Jazz", "https://jazz.example/live")
    dialog.name.setText("Smooth Jazz")
    dialog.accept()
    assert store.stations() == [Station("Smooth Jazz", StreamUrl("https://jazz.example/live"))]
    assert changes == [RadioStationsChanged()]


def test_one_dialog_at_a_time(commands: RadioCommands) -> None:
    first = commands.new()
    second = commands.new()
    assert first.result() == RadioStationDialog.DialogCode.Rejected
    assert commands.dialog is second


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
def test_delete_asks_first(
    commands: RadioCommands,
    store: RadioStations,
    changes: list[RadioStationsChanged],
    monkeypatch: pytest.MonkeyPatch,
    answer: QMessageBox.StandardButton,
) -> None:
    store.save("Jazz", "https://jazz.example/live")
    asked: list[str] = []

    def question(*args: object) -> QMessageBox.StandardButton:
        asked.append(str(args[2]))
        return answer

    monkeypatch.setattr(QMessageBox, "question", question)
    commands.delete("Jazz")
    assert asked == ['Delete the radio station "Jazz"?']
    deleted = answer == QMessageBox.StandardButton.Yes
    assert [s.name for s in store.stations()] == ([] if deleted else ["Jazz"])
    assert changes == ([RadioStationsChanged()] if deleted else [])
