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

import threading
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.config import Settings
from pyamp.core.events import EventBus, SavedPlaylistEdited, SavedPlaylistsChanged
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


def test_saved_edits_are_announced_on_the_main_thread(
    session: PlaylistSession,
    playlist: Playlist,
    bus: EventBus,
    saved: SavedPlaylists,
    tmp_path: Path,
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    heard: list[tuple[str, int, bool]] = []  # name, tracks saved by then, on the main thread
    main = threading.current_thread()
    bus.subscribe(
        SavedPlaylistEdited,
        lambda e: heard.append(
            (e.name, len(saved.read_paths(e.name)), threading.current_thread() is main)
        ),
    )
    playlist.add([Track.from_path(f) for f in make_files(tmp_path / "add", 2)])
    wait_until(lambda: bool(heard))
    assert heard == [("Default", 2, True)]  # once written
    playlist.remove([0])
    session.save_now()  # e.g. switching playlists: pending edits are written at once
    assert heard[-1] == ("Default", 1, True)
    session.save_now()  # nothing new
    session.new("Other")
    assert len(heard) == 2


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


class SlowSaves(SavedPlaylists):
    """Background saves of tracks wait for :attr:`release`; every save is recorded.

    (Opening a playlist may also auto-save it while it's still empty, depending on how
    quickly it loads; those aren't held up, and tests look for the save they expect.)
    """

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.saves: list[tuple[bool, int]] = []  # (on the main thread, how many tracks)
        self.release = threading.Event()

    def save(self, name: str, tracks: Sequence[Track]) -> None:
        on_main = threading.current_thread() is threading.main_thread()
        self.saves.append((on_main, len(tracks)))
        if tracks and not on_main:
            self.release.wait(5)
        super().save(name, tracks)


def test_auto_saves_are_written_off_the_main_thread(
    playlist: Playlist,
    loader: BackgroundTrackLoader,
    bus: EventBus,
    settings: Settings,
    tmp_path: Path,
) -> None:
    saved = SlowSaves(tmp_path / "playlists")
    session = session_for(playlist, saved, loader, bus, settings)
    session.start()
    wait_until(lambda: loaded(session))
    playlist.add([Track.from_path(f) for f in make_files(tmp_path / "add", 3)])
    # The auto-save of the three tracks is under way on another thread, and the main
    # thread (the window) carried on meanwhile.
    wait_until(lambda: (False, 3) in saved.saves)
    assert saved.read_paths("Default") == []
    saved.release.set()
    session.wait_for_saves()
    assert len(saved.read_paths("Default")) == 3


def test_deleting_waits_for_a_pending_auto_save(
    playlist: Playlist,
    loader: BackgroundTrackLoader,
    bus: EventBus,
    settings: Settings,
    tmp_path: Path,
) -> None:
    saved = SlowSaves(tmp_path / "playlists")
    session = session_for(playlist, saved, loader, bus, settings)
    session.start()
    session.new("Doomed")
    wait_until(lambda: loaded(session))
    playlist.add([Track.from_path(f) for f in make_files(tmp_path / "add", 1)])
    wait_until(lambda: (False, 1) in saved.saves)  # the auto-save is under way
    threading.Timer(0.1, saved.release.set).start()
    session.delete()
    session.wait_for_saves()
    assert saved.names() == ["Default"]  # not brought back by the late auto-save


def test_open_tracks_fills_a_playlist_without_touching_the_others(
    session: PlaylistSession,
    playlist: Playlist,
    saved: SavedPlaylists,
    settings: Settings,
    tmp_path: Path,
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    playlist.add([Track.from_path(make_files(tmp_path / "d", 1)[0])])
    tracks = [Track.from_path(p) for p in make_files(tmp_path / "lib", 3)]

    session.open_tracks("Library", tracks)
    assert session.active == "Library"
    assert list(playlist) == tracks  # at once, nothing to load
    assert not session.loading
    assert settings.active_playlist == "Library"
    assert len(saved.read_paths("Default")) == 1  # saved as it was
    wait_until(lambda: saved.exists("Library") and len(saved.read_paths("Library")) == 3)

    session.open_tracks("library", tracks[:1])  # the same playlist, case-insensitively
    assert saved.names() == ["Default", "Library"]
    wait_until(lambda: len(saved.read_paths("Library")) == 1)


def test_adding_to_playlists_skips_what_they_have(
    session: PlaylistSession,
    playlist: Playlist,
    saved: SavedPlaylists,
    bus: EventBus,
    tmp_path: Path,
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    a, b, c = (Track.from_path(p) for p in make_files(tmp_path / "m", 3))
    events: list[SavedPlaylistsChanged] = []
    bus.subscribe(SavedPlaylistsChanged, events.append)
    edited: list[SavedPlaylistEdited] = []
    bus.subscribe(SavedPlaylistEdited, edited.append)

    assert session.add_to("default", [a, b, a]) == 2  # the open one, in place; repeats once
    assert list(playlist) == [a, b]
    assert saved.read_paths("Default") == [a.location, b.location]  # saved at once

    name = session.create("Mix")
    assert session.active == "Default"  # made, not opened
    assert session.add_to(name, [a]) == 1
    assert session.add_to(name, [a, c]) == 1
    assert saved.read_paths("Mix") == [a.location, c.location]
    assert session.add_to(name, [c]) == 0
    assert events[-1] == SavedPlaylistsChanged(("Default", "Mix"), "Default")
    assert [e.name for e in edited] == ["Default", "Mix", "Mix"]  # not when nothing's added


def test_other_playlists_can_be_renamed_and_deleted(
    session: PlaylistSession, saved: SavedPlaylists, settings: Settings
) -> None:
    session.start()
    wait_until(lambda: loaded(session))
    session.create("Mix")
    assert session.rename("Party", "mix") == "Party"
    assert session.active == "Default"
    assert saved.names() == ["Default", "Party"]
    session.delete("party")
    assert saved.names() == ["Default"]
    assert session.active == "Default"
    session.rename("Main")  # the open one, as before
    assert session.active == "Main" == settings.active_playlist
