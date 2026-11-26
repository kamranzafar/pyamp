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

Auto-saves are written on a background thread from a snapshot of the tracks (a large
playlist takes a noticeable moment to write). There is one writer thread, so files are
written in order, and every other save, rename or delete waits for it first.

Once edits are written, :class:`SavedPlaylistEdited` is published (on the main thread),
so what shows the saved playlists (e.g. the library window) can show them again.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from pyamp.config import Settings
from pyamp.core.events import (
    EventBus,
    PlaylistChanged,
    SavedPlaylistEdited,
    SavedPlaylistsChanged,
)
from pyamp.core.models import Location, Track
from pyamp.core.playlist import Playlist
from pyamp.library.saved_playlists import SavedPlaylists, entry_track
from pyamp.ui.background_loader import BackgroundTrackLoader, LoadJob

log = logging.getLogger(__name__)

AUTOSAVE_MS = 500
DEFAULT_NAME = "Default"


class PlaylistSession(QObject):
    #: A playlist's edits were written (sent from the writer thread too; received on
    #: the main thread, which publishes them).
    _edited = Signal(str)

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
        self._autosave.timeout.connect(self._save_in_background)
        self._writer = QThreadPool(self)
        self._writer.setMaxThreadCount(1)  # one at a time, in order
        # A method of this (main thread) object, so the writer thread's are queued here.
        self._edited.connect(self._publish_edited)
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
        """Write the open playlist to its file now (skipped while it's still loading)."""
        edited = self._autosave.isActive()  # edits not written yet
        self._autosave.stop()
        self.wait_for_saves()
        if self._active is None or self.loading:
            return
        self._write(self._active, list(self._playlist), edited=edited)

    def wait_for_saves(self) -> None:
        """Block until background saves have been written."""
        self._writer.waitForDone()

    def _save_in_background(self) -> None:
        if self._active is None or self.loading:
            return
        name, tracks = self._active, list(self._playlist)  # tracks are immutable
        self._writer.start(lambda: self._write(name, tracks, edited=True))

    def _write(self, name: str, tracks: Sequence[Track], *, edited: bool = False) -> None:
        """Runs on the main or the writer thread: touches only the file (and says so if
        it holds ``edited`` tracks)."""
        try:
            self._saved.save(name, tracks)
        except OSError:
            log.exception("Could not save playlist %r", name)
            return
        if edited:
            self._edited.emit(name)

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

    def open_tracks(self, name: str, tracks: Sequence[Track]) -> None:
        """Open the playlist ``name`` (creating it) with ``tracks`` in place of its own.

        How the library plays tracks without changing the user's other playlists.
        """
        self.save_now()
        self._loader.cancel_all()
        if not self._saved.exists(name):
            self._saved.create(name)
        self._active = next(n for n in self._saved.names() if n.casefold() == name.casefold())
        self._loading = None
        self._playlist.replace(tracks)  # saved by the auto-save this starts
        self._remember()

    def create(self, base: str) -> str:
        """Make an empty playlist named ``base`` (or "base 2", ...), without opening it."""
        name = self._saved.create(base)
        self._announce()
        return name

    def add_to(self, name: str, tracks: Sequence[Track]) -> int:
        """Add ``tracks`` to the playlist ``name``, skipping those already in it (and
        repeats); returns how many were added. The open playlist changes in place."""
        if self._is_open(name):
            new = _not_in(tracks, {track.location for track in self._playlist})
            self._playlist.add(new)
            self.save_now()
        else:
            self.wait_for_saves()  # so this read and write come after any auto-save
            entries = self._saved.entries(name)
            new = _not_in(tracks, {entry.location for entry in entries})
            if new:
                self._saved.save(name, [*map(entry_track, entries), *new])
                self._edited.emit(name)
        return len(new)

    def rename(self, new_name: str, old: str | None = None) -> str:
        """Rename a playlist (the open one by default). Raises PlaylistNameError with a
        user-facing message."""
        old = old or self._active
        assert old is not None
        self.save_now()
        renamed = self._saved.rename(old, new_name)
        if self._is_open(old):
            self._active = renamed
            self._remember()
        else:
            self._announce()
        return renamed

    def delete(self, name: str | None = None) -> None:
        """Delete a playlist (the open one by default). Deleting the open one opens a
        neighbour (or a fresh "Default")."""
        if name is not None and not self._is_open(name):
            self.wait_for_saves()
            self._saved.delete(name)
            self._announce()
            return
        assert self._active is not None
        names = self._saved.names()
        index = next(i for i, n in enumerate(names) if n.casefold() == self._active.casefold())
        self._autosave.stop()
        self.wait_for_saves()  # so a late auto-save can't bring the file back
        self._loader.cancel_all()  # stop reading the file before it goes
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
        # Loading first, so emptying the list isn't taken for an edit: an auto-save then
        # would write back only what loaded, dropping files that aren't there now.
        # (The loaded tracks arrive later, on this thread, after the list is emptied.)
        self._loading = self._loader.submit([self._saved.path(self._active)])
        self._playlist.replace([])
        self._remember()

    def _remember(self) -> None:
        assert self._active is not None
        self._settings.active_playlist = self._active
        self._announce()

    def _announce(self) -> None:
        if self._active is not None:
            self._bus.publish(SavedPlaylistsChanged(tuple(self._saved.names()), self._active))

    def _is_open(self, name: str) -> bool:
        return self._active is not None and name.casefold() == self._active.casefold()

    def _create_default(self) -> None:
        """First run: keep the single playlist of earlier versions as "Default"."""
        name = self._saved.create(DEFAULT_NAME)
        if self._legacy is not None and self._legacy.is_file():
            try:
                shutil.copyfile(self._legacy, self._saved.path(name))
                log.info("Moved the previous session's playlist into %r", name)
            except OSError:
                log.exception("Could not migrate %s", self._legacy)

    @Slot(str)
    def _publish_edited(self, name: str) -> None:
        self._bus.publish(SavedPlaylistEdited(name))

    def _on_playlist_changed(self, _: PlaylistChanged) -> None:
        if self._active is not None and not self.loading:
            self._autosave.start()  # restarts: saves once edits pause

    def _on_job_finished(self, job: LoadJob) -> None:
        if job is self._loading:
            self._loading = None


def _not_in(tracks: Sequence[Track], present: set[Location]) -> list[Track]:
    """``tracks`` that aren't in ``present``, each once."""
    new: list[Track] = []
    for track in tracks:
        if track.location not in present:
            present.add(track.location)
            new.append(track)
    return new
