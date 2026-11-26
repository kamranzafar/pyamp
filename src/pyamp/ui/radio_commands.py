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

"""What the Radio source's menu commands do: add, change and delete stations.

Each change is saved to the store, then announced with :class:`RadioStationsChanged`
so the library window shows it.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QMessageBox, QWidget

from pyamp.core.events import EventBus, RadioStationsChanged
from pyamp.library.radio_stations import RadioStations, StationError
from pyamp.ui.radio_station_dialog import RadioStationDialog


class RadioCommands:
    def __init__(
        self, store: RadioStations, bus: EventBus, *, parent: Callable[[], QWidget | None]
    ) -> None:
        self._store = store
        self._bus = bus
        self._parent = parent  # where to show dialogs: the window in front
        self.dialog: RadioStationDialog | None = None  # the one open, if any

    def new(self) -> RadioStationDialog:
        """Ask for a new station (a menu command: takes no arguments)."""
        return self._ask(None, "", "")

    def add(self, url: str, name: str) -> RadioStationDialog:
        """Ask for a new station with ``url`` and ``name`` filled in (e.g. a stream in
        the playlist, and its title)."""
        return self._ask(None, name, url)

    def edit(self, name: str) -> RadioStationDialog | None:
        station = self._store.get(name)
        if station is None:
            return None
        return self._ask(station.name, station.name, station.url.url)

    def delete(self, name: str) -> None:
        answer = QMessageBox.question(
            self._parent(), "Delete Station", f'Delete the radio station "{name}"?'
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._store.delete(name)
        except OSError as exc:
            QMessageBox.warning(self._parent(), "Delete Station", str(exc))
            return
        self._bus.publish(RadioStationsChanged())

    def _ask(self, replacing: str | None, name: str, url: str) -> RadioStationDialog:
        """Open the dialog (not modal-blocking); OK saves and announces the change."""

        def save(new_name: str, new_url: str) -> None:
            try:
                self._store.save(new_name, new_url, replacing)
            except OSError as exc:
                raise StationError(f"Couldn't save the station: {exc}") from exc
            self._bus.publish(RadioStationsChanged())

        if self.dialog is not None:
            self.dialog.reject()  # one at a time
        dialog = RadioStationDialog(
            save=save, name=name, url=url, editing=replacing is not None, parent=self._parent()
        )
        dialog.finished.connect(lambda _: self._closed(dialog))
        self.dialog = dialog
        dialog.open()
        return dialog

    def _closed(self, dialog: RadioStationDialog) -> None:
        if self.dialog is dialog:
            self.dialog = None
        dialog.deleteLater()
