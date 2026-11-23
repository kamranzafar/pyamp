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
"""Which saved playlist is open, and keeping it saved (auto-save).

Every edit to the open playlist is written back to its file shortly afterwards
(debounced), on switching to another playlist, and on quit. While a playlist is
still loading nothing is saved, so a partly loaded list never overwrites the
complete file.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from pyamp.config import Settings
from pyamp.core.events import EventBus, PlaylistChanged, SavedPlaylistsChanged
from pyamp.core.playlist import Playlist
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.ui.background_loader import BackgroundTrackLoader, LoadJob

log = logging.getLogger(__name__)

AUTOSAVE_MS = 500
DEFAULT_NAME = "Default"


class PlaylistSession(QObject):
    def __init__(
        self,
        playlist: Playlist,
        saved: SavedPlaylists,
        loader: BackgroundTrackLoader,
        bus: EventBus,
        *,
        settings: Settings,
        legacy_session: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._playlist = playlist
        self._saved = saved
        self._loader = loader
        self._bus = bus
        self._settings = settings
        self._legacy = legacy_session
        self._active: str | None = None
        self._loading: LoadJob | None = None

        self._autosave = QTimer(self)
        self._autosave.setSingleShot(True)
        self._autosave.setInterval(AUTOSAVE_MS)
        self._autosave.timeout.connect(self.save_now)
        bus.subscribe(PlaylistChanged, self._on_playlist_changed)
        loader.job_finished.connect(self._on_job_finished)

    # -- State -------------------------------------------------------------- #

    @property
    def active(self) -> str | None:
        return self._active

    @property
    def names(self) -> list[str]:
        return self._saved.names()

    @property
    def loading(self) -> bool:
        return self._loading is not None and not self._loading.done

    # -- Lifecycle ------------------------------------------------------------ #

    def start(self) -> None:
        """Open the playlist that was open last (creating "Default" if there are none)."""
        if not self._saved.names():
            self._create_default()
        remembered = self._settings.active_playlist
        names = self._saved.names()
        target = remembered if self._saved.exists(remembered) else names[0]
        self._open(target)

    def save_now(self) -> None:
        """Write the open playlist to its file (skipped while it's still loading)."""
        self._autosave.stop()
        if self._active is None or self.loading:
            return
        try:
            self._saved.save(self._active, list(self._playlist))
        except OSError:
            log.exception("Could not save playlist %r", self._active)

    def shutdown(self) -> None:
        if self.loading:
            log.info("Playlist %r still loading; keeping its saved file", self._active)
        self.save_now()

    # -- User actions ------------------------------------------------------------ #

    def switch(self, name: str) -> None:
        if self._active is not None and name.casefold() == self._active.casefold():
            return
        self.save_now()
        self._open(name)

    def new(self, base: str = "New playlist") -> str:
        self.save_now()
        name = self._saved.create(base)
        self._open(name)
        return name

    def rename(self, new_name: str) -> str:
        """Rename the open playlist. Raises PlaylistNameError with a user-facing message."""
        assert self._active is not None
        self.save_now()
        self._active = self._saved.rename(self._active, new_name)
        self._remember()
        return self._active

    def delete(self) -> None:
        """Delete the open playlist and open a neighbour (or a fresh "Default")."""
        assert self._active is not None
        names = self._saved.names()
        index = next(i for i, n in enumerate(names) if n.casefold() == self._active.casefold())
        self._autosave.stop()
        self._saved.delete(self._active)
        self._active = None  # nothing left to save it to
        remaining = self._saved.names()
        if not remaining:
            remaining = [self._saved.create(DEFAULT_NAME)]
        self._open(remaining[min(index, len(remaining) - 1)])

    # -- Internals ------------------------------------------------------------ #

    def _open(self, name: str) -> None:
        self._loader.cancel_all()
        self._active = next(n for n in self._saved.names() if n.casefold() == name.casefold())
        self._playlist.replace([])
        self._loading = self._loader.submit([self._saved.path(self._active)])
        self._remember()

    def _remember(self) -> None:
        assert self._active is not None
        self._settings.active_playlist = self._active
        self._bus.publish(SavedPlaylistsChanged(tuple(self._saved.names()), self._active))

    def _create_default(self) -> None:
        """First run: keep the single playlist of earlier versions as "Default"."""
        name = self._saved.create(DEFAULT_NAME)
        if self._legacy is not None and self._legacy.is_file():
            try:
                shutil.copyfile(self._legacy, self._saved.path(name))
                log.info("Moved the previous session's playlist into %r", name)
            except OSError:
                log.exception("Could not migrate %s", self._legacy)

    def _on_playlist_changed(self, _: PlaylistChanged) -> None:
        if self._active is not None and not self.loading:
            self._autosave.start()  # restarts: saves once edits pause

    def _on_job_finished(self, job: LoadJob) -> None:
        if job is self._loading:
            self._loading = None
