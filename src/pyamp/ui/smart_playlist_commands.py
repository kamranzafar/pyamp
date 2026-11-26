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

"""What the Smart Playlists source's menu commands do: make, change, copy and delete.

Each change is saved to the store, then announced with :class:`SmartPlaylistsChanged`
so the library window shows it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtWidgets import QMessageBox, QWidget

from pyamp.core.events import EventBus, SmartPlaylistsChanged
from pyamp.library.catalog import Catalog
from pyamp.library.saved_playlists import MAX_NAME_LENGTH, PlaylistNameError
from pyamp.library.smart_playlists import SmartPlaylist, SmartPlaylists
from pyamp.ui.smart_playlist_dialog import SmartPlaylistDialog

COPY_SUFFIX = " copy"


class SmartPlaylistCommands:
    def __init__(
        self,
        store: SmartPlaylists,
        catalog: Catalog,
        bus: EventBus,
        *,
        parent: Callable[[], QWidget | None],
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._bus = bus
        self._parent = parent  # where to show dialogs: the window in front
        self.dialog: SmartPlaylistDialog | None = None  # the one open, if any

    def new(self) -> SmartPlaylistDialog:
        return self._ask(None)

    def edit(self, name: str) -> SmartPlaylistDialog | None:
        playlist = self._store.get(name)
        return self._ask(playlist) if playlist is not None else None

    def duplicate(self, name: str) -> None:
        playlist = self._store.get(name)
        if playlist is None:
            return
        base = playlist.name[: MAX_NAME_LENGTH - len(COPY_SUFFIX) - 4] + COPY_SUFFIX
        try:
            self._store.save(replace(playlist, name=self._store.unused_name(base)))
        except (OSError, PlaylistNameError) as exc:
            QMessageBox.warning(self._parent(), "Duplicate Smart Playlist", str(exc))
            return
        self._bus.publish(SmartPlaylistsChanged())

    def delete(self, name: str) -> None:
        answer = QMessageBox.question(
            self._parent(),
            "Delete Smart Playlist",
            f'Delete the smart playlist "{name}"?\n\nNo music files are deleted.',
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._store.delete(name)
        except OSError as exc:
            QMessageBox.warning(self._parent(), "Delete Smart Playlist", str(exc))
            return
        self._bus.publish(SmartPlaylistsChanged())

    def _ask(self, playlist: SmartPlaylist | None) -> SmartPlaylistDialog:
        """Open the dialog (not modal-blocking); OK saves and announces the change."""
        replacing = playlist.name if playlist is not None else None

        def save(changed: SmartPlaylist) -> None:
            try:
                self._store.save(changed, replacing)
            except OSError as exc:
                raise PlaylistNameError(f"Couldn't save the smart playlist: {exc}") from exc
            self._bus.publish(SmartPlaylistsChanged())

        if self.dialog is not None:
            self.dialog.reject()  # one at a time
        dialog = SmartPlaylistDialog(
            playlist,
            save=save,
            count=self._catalog.smart_count,
            folders=[str(folder) for folder in self._catalog.sources()],
            parent=self._parent(),
        )
        dialog.finished.connect(lambda _: self._closed(dialog))
        self.dialog = dialog
        dialog.open()
        return dialog

    def _closed(self, dialog: SmartPlaylistDialog) -> None:
        if self.dialog is dialog:
            self.dialog = None
        dialog.deleteLater()
