from __future__ import annotations

import numpy as np

from pyamp.core.events import AudioFrame, EventBus, PlaybackStateChanged, TrackChanged
from pyamp.core.models import PlaybackState, RepeatMode
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from tests.conftest import FakeBackend, make_tracks


def test_play_starts_first_track(player: Player, playlist: Playlist, backend: FakeBackend) -> None:
    tracks = make_tracks(3)
    playlist.add(tracks)
    player.play()
    assert backend.loaded == [tracks[0].path]
    assert player.state is PlaybackState.PLAYING


def test_play_on_empty_playlist_does_nothing(player: Player, backend: FakeBackend) -> None:
    player.play()
    assert backend.loaded == []


def test_pause_toggles(player: Player, playlist: Playlist) -> None:
    playlist.add(make_tracks(1))
    player.play()
    player.pause()
    assert player.state is PlaybackState.PAUSED
    player.pause()
    assert player.state is PlaybackState.PLAYING


def test_auto_advance_and_stop_at_end(
    player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    playlist.add(make_tracks(2))
    player.play_index(0)
    backend.finish_track()
    assert playlist.current_index == 1
    backend.finish_track()
    assert player.state is PlaybackState.STOPPED


def test_repeat_all_wraps(player: Player, playlist: Playlist, backend: FakeBackend) -> None:
    playlist.add(make_tracks(2))
    player.set_repeat(RepeatMode.ALL)
    player.play_index(1)
    backend.finish_track()
    assert playlist.current_index == 0


def test_repeat_one_replays_but_next_still_advances(
    player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    playlist.add(make_tracks(3))
    player.set_repeat(RepeatMode.ONE)
    player.play_index(1)
    backend.finish_track()
    assert playlist.current_index == 1
    player.next()
    assert playlist.current_index == 2


def test_previous_restarts_track_after_threshold(
    player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    playlist.add(make_tracks(3))
    player.play_index(1)
    player.on_duration_changed(60_000)
    player.on_position_changed(10_000)
    player.previous()
    assert playlist.current_index == 1
    assert backend.position == 0


def test_volume_is_clamped(player: Player, backend: FakeBackend) -> None:
    player.set_volume(1.7)
    assert player.volume == backend.volume == 1.0


def test_events_are_published(
    player: Player, playlist: Playlist, backend: FakeBackend, bus: EventBus
) -> None:
    received: list[object] = []
    for event_type in (TrackChanged, PlaybackStateChanged, AudioFrame):
        bus.subscribe(event_type, received.append)
    playlist.add(make_tracks(1))
    player.play()
    backend.emit_audio(np.zeros(16, dtype=np.float32), 44100)
    assert [type(e) for e in received] == [PlaybackStateChanged, TrackChanged, AudioFrame]
