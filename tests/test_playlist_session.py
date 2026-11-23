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
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.config import Settings
from pyamp.core.events import EventBus, SavedPlaylistsChanged
from pyamp.core.models import Track
from pyamp.core.playlist import Playlist
from pyamp.library.loader import TrackLoader
from pyamp.library.playlist_formats import M3UFormat, PlaylistFormatRegistry
from pyamp.library.saved_playlists import PlaylistNameError, SavedPlaylists
from pyamp.ui import playlist_session
from pyamp.ui.background_loader import BackgroundTrackLoader
from pyamp.ui.playlist_session import PlaylistSession
from tests.test_background_loader import GatedReader, make_files, wait_until


@pytest.fixture
def reader() -> GatedReader:
    return GatedReader()


@pytest.fixture
def loader(
    qt_app: QCoreApplication, reader: GatedReader, playlist: Playlist
) -> Iterator[BackgroundTrackLoader]:
    background = BackgroundTrackLoader(TrackLoader(reader, PlaylistFormatRegistry.default()))
    background.tracks_loaded.connect(lambda job, tracks: playlist.add(tracks))  # as MainWindow does
    yield background
    reader.gate.set()
    background.shutdown()


@pytest.fixture
def saved(tmp_path: Path) -> SavedPlaylists:
    return SavedPlaylists(tmp_path / "playlists")


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture(autouse=True)
def fast_autosave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(playlist_session, "AUTOSAVE_MS", 20)


def session_for(
    playlist: Playlist,
    saved: SavedPlaylists,
    loader: BackgroundTrackLoader,
    bus: EventBus,
    settings: Settings,
    legacy: Path | None = None,
) -> PlaylistSession:
    return PlaylistSession(playlist, saved, loader, bus, settings=settings, legacy_session=legacy)


@pytest.fixture
def session(
    playlist: Playlist,
    saved: SavedPlaylists,
    loader: BackgroundTrackLoader,
    bus: EventBus,
    settings: Settings,
) -> PlaylistSession:
    return session_for(playlist, saved, loader, bus, settings)


def write_playlist(saved: SavedPlaylists, name: str, files: list[Path]) -> None:
    saved.save(name, [Track.from_path(f) for f in files])


def loaded(session: PlaylistSession) -> bool:
    return not session.loading


def saved_names(saved: SavedPlaylists, name: str) -> list[str]:
    return [p.name for p in saved.read_paths(name)]


def test_first_run_creates_an_empty_default(
    session: PlaylistSession, playlist: Playlist, saved: SavedPlaylists, settings: Settings
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    assert session.active == "Default"
    assert saved.names() == ["Default"]
    assert len(playlist) == 0
    assert settings.active_playlist == "Default"


def test_first_run_migrates_the_old_session_playlist(
    playlist: Playlist,
    saved: SavedPlaylists,
    loader: BackgroundTrackLoader,
    bus: EventBus,
    settings: Settings,
    tmp_path: Path,
) -> None:
    files = make_files(tmp_path / "music", 2)
    legacy = tmp_path / "session.m3u8"
    M3UFormat().write(legacy, [Track.from_path(f) for f in files])
    session = session_for(playlist, saved, loader, bus, settings, legacy)
    session.start()
    wait_until(lambda: loaded(session) and len(playlist) == 2)
    assert session.active == "Default"
    assert [t.location for t in playlist] == files


def test_reopens_the_playlist_that_was_open_last(
    session: PlaylistSession,
    playlist: Playlist,
    saved: SavedPlaylists,
    settings: Settings,
    tmp_path: Path,
) -> None:
    write_playlist(saved, "Morning", make_files(tmp_path / "m", 1))
    write_playlist(saved, "Running", make_files(tmp_path / "r", 3))
    settings.active_playlist = "Running"
    session.start()
    wait_until(lambda: loaded(session) and len(playlist) == 3)
    assert session.active == "Running"


def test_edits_are_saved_after_a_pause(
    session: PlaylistSession, playlist: Playlist, saved: SavedPlaylists, tmp_path: Path
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    new = make_files(tmp_path / "add", 2)
    playlist.add([Track.from_path(f) for f in new])
    assert saved.read_paths("Default") == []  # not instantly...
    wait_until(lambda: len(saved.read_paths("Default")) == 2)  # ...but shortly after
    playlist.move([1], 0)
    wait_until(lambda: saved_names(saved, "Default") == [new[1].name, new[0].name])


def test_nothing_is_saved_while_loading(
    session: PlaylistSession,
    playlist: Playlist,
    saved: SavedPlaylists,
    reader: GatedReader,
    tmp_path: Path,
    settings: Settings,
) -> None:
    files = make_files(tmp_path / "big", 5)
    write_playlist(saved, "Big", files)
    settings.active_playlist = "Big"
    reader.gate.clear()  # loading stalls
    session.start()
    assert session.loading
    session.save_now()
    session.shutdown()
    assert len(saved.read_paths("Big")) == 5  # the complete file was kept
    reader.gate.set()
    wait_until(lambda: loaded(session) and len(playlist) == 5)


def test_switch_saves_then_loads_the_other(
    session: PlaylistSession,
    playlist: Playlist,
    saved: SavedPlaylists,
    tmp_path: Path,
    bus: EventBus,
) -> None:
    events: list[SavedPlaylistsChanged] = []
    bus.subscribe(SavedPlaylistsChanged, events.append)
    session.start()  # creates and opens "Default"
    wait_until(lambda: loaded(session))
    write_playlist(saved, "Other", make_files(tmp_path / "o", 2))
    playlist.add([Track.from_path(make_files(tmp_path / "d", 1)[0])])
    session.switch("other")  # case-insensitive
    assert len(saved.read_paths("Default")) == 1  # saved immediately, not after the delay
    wait_until(lambda: loaded(session) and len(playlist) == 2)
    assert session.active == "Other"
    assert events[-1] == SavedPlaylistsChanged(("Default", "Other"), "Other")


def test_new_rename_delete(
    session: PlaylistSession, playlist: Playlist, saved: SavedPlaylists, settings: Settings
) -> None:
    session.start()
    assert session.new() == "New playlist"
    wait_until(lambda: loaded(session))
    assert session.rename("Road trip") == "Road trip"
    assert settings.active_playlist == "Road trip"
    with pytest.raises(PlaylistNameError):
        session.rename("default")
    session.delete()
    assert saved.names() == ["Default"]
    assert session.active == "Default"


def test_deleting_the_last_playlist_recreates_default(
    session: PlaylistSession, saved: SavedPlaylists
) -> None:
    session.start()
    session.delete()
    assert saved.names() == ["Default"]
    assert session.active == "Default"


def test_shutdown_saves_pending_edits(
    session: PlaylistSession,
    playlist: Playlist,
    saved: SavedPlaylists,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    playlist.add([Track.from_path(make_files(tmp_path / "x", 1)[0])])
    session.shutdown()  # before the auto-save timer fires
    assert len(saved.read_paths("Default")) == 1
