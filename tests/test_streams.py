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
"""Streams and internet radio across the app: player, widgets, menus, playlists."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtWidgets import QInputDialog, QMessageBox

from pyamp.app import Application
from pyamp.core.events import EventBus, PositionChanged, StreamTitleChanged, TrackChanged
from pyamp.core.models import StreamUrl, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.media_session import MediaSession, NowPlaying
from pyamp.ui.playlist_model import PlaylistModel, dropped_locations
from pyamp.ui.skin import DEFAULT_SKIN
from pyamp.ui.widgets.sliders import SeekSlider
from tests.conftest import FakeBackend
from tests.test_background_loader import wait_until
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)

STATION = Track.for_stream(StreamUrl("https://radio.example/groove"), "Groove Salad")
# resolve() so it is absolute on Windows too (C:\m\song.mp3)
SONG = Track(location=Path("/m/song.mp3").resolve(), title="Song", duration_ms=60_000)


def test_stream_titles_are_published_for_streams_only(
    player: Player, playlist: Playlist, backend: FakeBackend, bus: EventBus
) -> None:
    titles: list[str] = []
    bus.subscribe(StreamTitleChanged, lambda e: titles.append(e.title))
    playlist.add([SONG, STATION])
    player.play_index(0)
    backend._listener.on_stream_title("Ignored: a file's own title tag")
    player.play_index(1)
    backend._listener.on_stream_title("Artist - Track 1")
    backend._listener.on_stream_title("Artist - Track 1")  # unchanged: not repeated
    backend._listener.on_stream_title("Artist - Track 2")
    assert titles == ["Artist - Track 1", "Artist - Track 2"]
    assert player.stream_title == "Artist - Track 2"
    player.play_index(0)
    assert player.stream_title == ""  # reset by the next track
    assert backend.loaded[-2] == STATION.location


class Adapter:
    def __init__(self) -> None:
        self.shown: list[NowPlaying] = []

    def update(self, now_playing: NowPlaying) -> None:
        self.shown.append(now_playing)

    def seeked(self, position_ms: int) -> None:
        pass

    def close(self) -> None:
        pass


def test_system_media_widgets_show_song_and_station(
    player: Player, playlist: Playlist, backend: FakeBackend, bus: EventBus
) -> None:
    adapter = Adapter()
    session = MediaSession(bus, player, playlist, adapter)
    playlist.add([STATION])
    player.play_index(0)
    backend._listener.on_stream_title("Massive Attack - Teardrop")
    shown = adapter.shown[-1]
    assert shown.track is not None
    assert (shown.track.title, shown.track.album) == ("Massive Attack - Teardrop", "Groove Salad")
    assert not shown.can_seek  # live: no duration
    session.close()


def test_playlist_shows_live_for_streams(qt_app: QCoreApplication, bus: EventBus) -> None:
    playlist = Playlist(bus)
    playlist.add([SONG, STATION])
    model = PlaylistModel(playlist, bus, DEFAULT_SKIN)
    assert model.index(0, 1).data() == "1:00"
    assert model.index(1, 1).data() == "LIVE"
    assert model.index(1, 0).data() == "2. Groove Salad"


def test_dropped_links_and_files(qt_app: QCoreApplication) -> None:
    urls = [
        QUrl.fromLocalFile("/m/a.mp3"),
        QUrl("https://radio.example/live.pls"),
        QUrl("ftp://nope.example/x.mp3"),
    ]
    assert dropped_locations(urls) == [
        Path("/m/a.mp3"),
        StreamUrl("https://radio.example/live.pls"),
    ]


# -- Through the real window ------------------------------------------------------------- #


def test_add_url_adds_a_station(app: Application, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: ("https://radio.example/x", True))
    )
    app.window.add_url()
    wait_until(lambda: len(app.playlist) == 1)
    assert app.playlist[0].location == StreamUrl("https://radio.example/x")


def test_add_url_rejects_other_addresses(
    app: Application,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("ftp://x/y", True)))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    app.window.add_url()
    assert warnings and "http" in warnings[0]
    assert len(app.playlist) == 0


def test_lcd_window_and_menu_bar_show_the_song(app: Application) -> None:  # noqa: F811
    app.playlist.add([STATION])
    app.player.play_index(0)
    app.player.on_stream_title("Massive Attack - Teardrop")
    window = app.window
    assert window._lcd._title.text() == "1. Groove Salad — Massive Attack - Teardrop"
    assert window.windowTitle().startswith("Massive Attack - Teardrop — 1. Groove Salad")
    icon = window.menu_bar_icon
    assert (icon.title.text(), icon.subtitle.text()) == (
        "Massive Attack - Teardrop",
        "Groove Salad",
    )


def test_a_saved_playlist_keeps_its_station(app: Application) -> None:  # noqa: F811
    app.playlist.add([SONG, STATION])
    app.playlists.save_now()
    assert app.playlists.active is not None
    saved = app.playlists._saved.read_paths(app.playlists.active)
    assert saved == [SONG.location, STATION.location]


# -- Live streams: no length, no seeking ------------------------------------------------- #


def test_live_streams_have_no_length_and_cant_seek(
    player: Player, playlist: Playlist, backend: FakeBackend, bus: EventBus
) -> None:
    durations: list[int] = []
    bus.subscribe(PositionChanged, lambda e: durations.append(e.duration_ms))
    playlist.add([STATION, SONG])
    player.play_index(0)
    assert player.is_live
    for buffered in (731, 2455, 13008):  # what mpv reports for a radio stream
        backend._listener.on_duration_changed(buffered)
    assert set(durations) == {0}
    assert player.duration_ms == 0
    player.seek(5000)
    assert "seek" not in " ".join(map(str, backend.calls))
    player.play_index(1)
    assert not player.is_live


def test_a_stream_with_a_known_length_stays_seekable(
    player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    on_server = Track(
        location=StreamUrl("http://nas.local/music/a.flac"), title="A", duration_ms=240_000
    )
    playlist.add([on_server])
    player.play_index(0)
    assert not player.is_live
    backend._listener.on_duration_changed(240_000)
    assert player.duration_ms == 240_000


def test_seek_bar_is_disabled_without_a_length(qt_app: QCoreApplication, bus: EventBus) -> None:
    slider = SeekSlider(bus)
    bus.publish(PositionChanged(1000, 60_000))
    assert slider.isEnabled()
    bus.publish(TrackChanged(STATION, 0))
    bus.publish(PositionChanged(5000, 0))  # live: elapsed time, no length
    assert not slider.isEnabled()
    assert slider.maximum() == 0
