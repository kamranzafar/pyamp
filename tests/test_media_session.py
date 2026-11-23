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

import random

import pytest

from pyamp.core.events import EventBus
from pyamp.core.models import PlaybackState, RepeatMode
from pyamp.core.play_order import ShuffleOrder
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.media_session import MediaCommands, MediaSession, NowPlaying, create_adapter
from pyamp.plugins import PluginContext, PluginManager
from pyamp.plugins.media_session import MediaSessionPlugin
from tests.conftest import FakeBackend, make_tracks


class RecordingAdapter:
    def __init__(self) -> None:
        self.updates: list[NowPlaying] = []
        self.seeks: list[int] = []
        self.closed = False

    @property
    def last(self) -> NowPlaying:
        return self.updates[-1]

    def update(self, now_playing: NowPlaying) -> None:
        self.updates.append(now_playing)

    def seeked(self, position_ms: int) -> None:
        self.seeks.append(position_ms)

    def close(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def adapter() -> RecordingAdapter:
    return RecordingAdapter()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def session(
    bus: EventBus, player: Player, playlist: Playlist, adapter: RecordingAdapter, clock: FakeClock
) -> MediaSession:
    return MediaSession(bus, player, playlist, adapter, clock=clock)


@pytest.fixture
def commands(player: Player) -> MediaCommands:
    return MediaCommands(player)


def start_playing(
    player: Player, playlist: Playlist, backend: FakeBackend, *, duration: int = 60_000
) -> None:
    playlist.add(make_tracks(3))
    player.play()
    player.on_duration_changed(duration)


# -- Snapshots ----------------------------------------------------------------- #


def test_initial_snapshot_is_sent_immediately(
    session: MediaSession, adapter: RecordingAdapter
) -> None:
    assert adapter.updates == [session.now_playing]
    assert adapter.last.track is None
    assert not adapter.last.can_play
    assert not adapter.last.can_seek


def test_playing_a_track_publishes_it(
    session: MediaSession,
    adapter: RecordingAdapter,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
) -> None:
    start_playing(player, playlist, backend)
    now = adapter.last
    assert now.track == playlist[0]
    assert now.state is PlaybackState.PLAYING
    assert now.duration_ms == 60_000
    assert now.can_go_next and now.can_go_previous and now.can_play and now.can_seek


def test_duration_falls_back_to_track_metadata(
    session: MediaSession, adapter: RecordingAdapter, player: Player, playlist: Playlist
) -> None:
    playlist.add(make_tracks(1))  # make_tracks sets duration_ms=60_000
    player.play()
    assert adapter.last.duration_ms == 60_000


@pytest.mark.parametrize(
    ("action", "check"),
    [
        (lambda p: p.set_volume(0.3), lambda n: n.volume == pytest.approx(0.3)),
        (lambda p: p.set_shuffle(True), lambda n: n.shuffle),
        (lambda p: p.set_repeat(RepeatMode.ONE), lambda n: n.repeat is RepeatMode.ONE),
        (lambda p: p.pause(), lambda n: n.state is PlaybackState.PAUSED),
    ],
)
def test_player_changes_are_published(
    session: MediaSession,
    adapter: RecordingAdapter,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
    action: object,
    check: object,
) -> None:
    start_playing(player, playlist, backend)
    before = len(adapter.updates)
    action(player)  # type: ignore[operator]
    assert len(adapter.updates) == before + 1
    assert check(adapter.last)  # type: ignore[operator]


def test_regular_position_ticks_are_not_forwarded(
    session: MediaSession,
    adapter: RecordingAdapter,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
    clock: FakeClock,
) -> None:
    start_playing(player, playlist, backend)
    before = len(adapter.updates)
    for second in range(1, 6):
        clock.now += 1
        player.on_position_changed(second * 1000 + 40)  # small jitter is fine
    assert len(adapter.updates) == before
    assert adapter.seeks == []


def test_seek_is_detected_and_reported(
    session: MediaSession,
    adapter: RecordingAdapter,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
    clock: FakeClock,
) -> None:
    start_playing(player, playlist, backend)
    clock.now += 2
    player.seek(30_000)  # FakeBackend reports the new position straight away
    assert adapter.seeks == [30_000]
    assert adapter.last.position_ms == 30_000

    # Playback continues normally from there: no further seeks.
    clock.now += 1
    player.on_position_changed(31_000)
    assert adapter.seeks == [30_000]


def test_position_does_not_advance_while_paused(
    session: MediaSession,
    adapter: RecordingAdapter,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
    clock: FakeClock,
) -> None:
    start_playing(player, playlist, backend)
    player.on_position_changed(10_000)  # seek before pausing
    player.pause()
    clock.now += 30
    player.on_position_changed(10_000)  # still there after 30 s: not a seek
    assert adapter.seeks == [10_000]
    assert adapter.last.position_ms == 10_000


def test_new_track_resets_position(
    session: MediaSession,
    adapter: RecordingAdapter,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
) -> None:
    start_playing(player, playlist, backend)
    player.on_position_changed(20_000)
    player.next()
    assert adapter.last.track == playlist[1]
    assert adapter.last.position_ms == 0


def test_can_go_next_follows_playlist_without_disturbing_shuffle(
    bus: EventBus, backend: FakeBackend, adapter: RecordingAdapter
) -> None:
    order = ShuffleOrder(random.Random(1))
    playlist = Playlist(bus, order)
    player = Player(backend, playlist, bus)
    MediaSession(bus, player, playlist, adapter)
    assert not adapter.last.can_go_next

    playlist.add(make_tracks(3))
    player.play()
    expected = [order.next_index(i, 3, wrap=False) for i in range(3)]
    player.on_end_of_track()
    player.on_end_of_track()
    assert adapter.last.can_go_next
    # The session never queried the play order, so the shuffle sequence is intact.
    assert [order.next_index(i, 3, wrap=False) for i in range(3)] == expected

    playlist.clear()
    assert not adapter.last.can_go_next


def test_close_unsubscribes_and_closes_adapter(
    session: MediaSession, adapter: RecordingAdapter, player: Player
) -> None:
    session.close()
    assert adapter.closed
    before = len(adapter.updates)
    player.set_volume(0.1)
    assert len(adapter.updates) == before


# -- Commands ------------------------------------------------------------------ #


def test_play_and_pause_are_idempotent(
    commands: MediaCommands, player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    playlist.add(make_tracks(2))
    commands.play()
    commands.play()
    assert backend.loaded == [playlist[0].path]
    assert player.state is PlaybackState.PLAYING

    commands.pause()
    commands.pause()
    assert player.state is PlaybackState.PAUSED

    commands.play()
    assert player.state is PlaybackState.PLAYING
    assert backend.loaded == [playlist[0].path]  # resumed, not restarted


def test_play_pause_toggles_and_starts_when_stopped(
    commands: MediaCommands, player: Player, playlist: Playlist
) -> None:
    playlist.add(make_tracks(1))
    commands.play_pause()
    assert player.state is PlaybackState.PLAYING
    commands.play_pause()
    assert player.state is PlaybackState.PAUSED
    commands.play_pause()
    assert player.state is PlaybackState.PLAYING


def test_transport_and_seek_commands(
    commands: MediaCommands, player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    start_playing(player, playlist, backend)
    commands.next()
    assert playlist.current_index == 1
    commands.previous()
    assert playlist.current_index == 0

    commands.seek_to(20_000)
    assert backend.position == 20_000
    commands.seek_by(-5_000)
    assert backend.position == 15_000
    assert commands.position_ms == 15_000

    commands.stop()
    assert player.state is PlaybackState.STOPPED


def test_setting_commands(commands: MediaCommands, player: Player) -> None:
    commands.set_volume(0.25)
    commands.set_shuffle(True)
    commands.set_repeat(RepeatMode.ALL)
    assert player.volume == pytest.approx(0.25)
    assert player.shuffle is True
    assert player.repeat is RepeatMode.ALL


# -- Adapter selection and plugin ---------------------------------------------- #


def test_unsupported_platform_has_no_adapter(commands: MediaCommands) -> None:
    assert create_adapter(commands, platform="win32") is None


def test_plugin_runs_session_with_adapter(
    bus: EventBus, player: Player, playlist: Playlist, adapter: RecordingAdapter
) -> None:
    plugin = MediaSessionPlugin(lambda commands: adapter)
    manager = PluginManager(PluginContext(bus, player, playlist))
    manager.register(plugin)
    assert plugin.active
    player.set_volume(0.5)
    assert adapter.last.volume == pytest.approx(0.5)

    manager.shutdown()
    assert adapter.closed
    assert not plugin.active


def test_plugin_is_inert_without_adapter(bus: EventBus, player: Player, playlist: Playlist) -> None:
    plugin = MediaSessionPlugin(lambda commands: None)
    manager = PluginManager(PluginContext(bus, player, playlist))
    manager.register(plugin)
    assert manager.active_plugins == (plugin,)
    assert not plugin.active
    manager.shutdown()
