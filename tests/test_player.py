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
from pathlib import Path

import numpy as np
import pytest

from pyamp.core.equalizer import Equalizer
from pyamp.core.events import (
    AudioFrame,
    EqualizerChanged,
    EventBus,
    PlaybackStateChanged,
    ReplayGainChanged,
    TrackChanged,
)
from pyamp.core.models import PlaybackState, RepeatMode, ReplayGain, ReplayGainMode, Track
from pyamp.core.play_order import ShuffleOrder
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from tests.conftest import FakeBackend, FakeGaplessBackend, make_tracks


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


# -- Gapless backends -------------------------------------------------------- #


@pytest.fixture
def gapless() -> FakeGaplessBackend:
    return FakeGaplessBackend()


@pytest.fixture
def gplayer(gapless: FakeGaplessBackend, playlist: Playlist, bus: EventBus) -> Player:
    return Player(gapless, playlist, bus)


def test_next_track_is_queued_after_play(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    tracks = make_tracks(3)
    playlist.add(tracks)
    gplayer.play()
    assert gapless.next_up == tracks[1].path


def test_nothing_queued_while_stopped(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    playlist.add(make_tracks(3))
    assert gapless.queued == []  # adding files must not start anything
    gplayer.play()
    gplayer.stop()
    playlist.add(make_tracks(1))
    assert gapless.next_up == playlist[1].path  # unchanged: no re-queue after stop


def test_end_of_list_queues_nothing_unless_repeating(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    tracks = make_tracks(2)
    playlist.add(tracks)
    gplayer.play_index(1)
    assert gapless.next_up is None
    gplayer.set_repeat(RepeatMode.ALL)
    assert gapless.next_up == tracks[0].path
    gplayer.set_repeat(RepeatMode.ONE)
    assert gapless.next_up == tracks[1].path


def test_advance_switches_track_without_loading(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend, bus: EventBus
) -> None:
    tracks = make_tracks(3)
    playlist.add(tracks)
    gplayer.play()
    gplayer.on_position_changed(59_000)
    changes: list[TrackChanged] = []
    bus.subscribe(TrackChanged, changes.append)

    gapless.advance()

    assert changes == [TrackChanged(tracks[1], 1)]
    assert gapless.loaded == [tracks[0].path]  # the backend switched by itself
    assert gplayer.position_ms == 0
    assert gapless.next_up == tracks[2].path  # and the one after is queued


def test_repeat_one_requeues_the_same_track(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    tracks = make_tracks(2)
    playlist.add(tracks)
    gplayer.set_repeat(RepeatMode.ONE)
    gplayer.play()
    queued_before = len(gapless.queued)
    gapless.advance()
    assert playlist.current_index == 0
    assert len(gapless.queued) == queued_before + 1
    assert gapless.next_up == tracks[0].path


def test_playlist_and_mode_changes_requeue(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    tracks = make_tracks(2)
    playlist.add(tracks)
    gplayer.play_index(1)
    assert gapless.next_up is None
    extra = make_tracks(4)[3]
    playlist.add([extra])
    assert gapless.next_up == extra.path

    gplayer.set_shuffle(True)
    assert gapless.next_up in {t.path for t in playlist}


def test_stale_advance_is_resolved_by_path(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    tracks = make_tracks(3)
    playlist.add(tracks)
    gplayer.play()
    queued = gapless.next_up
    playlist.remove([0])  # indices shift while the backend is already switching
    gapless.advance(queued)
    assert playlist.current_track == tracks[1]


def test_advance_to_removed_track_stops(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    playlist.add(make_tracks(2))
    gplayer.play()
    gapless.advance(Path("/music/gone.mp3"))
    assert gplayer.state is PlaybackState.STOPPED


def test_end_of_track_still_works_when_nothing_was_queued(
    gplayer: Player, playlist: Playlist, gapless: FakeGaplessBackend
) -> None:
    playlist.add(make_tracks(1))
    gplayer.play()
    gapless.finish_track()
    assert gplayer.state is PlaybackState.STOPPED


def test_non_gapless_backend_never_queues_or_touches_shuffle(
    player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    order = ShuffleOrder(random.Random(3))
    playlist.play_order = order
    playlist.add(make_tracks(4))
    player.play_index(0)
    player.set_repeat(RepeatMode.ALL)
    # The order was only asked when playing, never ahead of time.
    assert order._sequence == []


# -- ReplayGain ---------------------------------------------------------------- #


def rg_track(name: str, **gains: float) -> Track:
    return Track(path=Path(f"/music/{name}.mp3"), title=name, replay_gain=ReplayGain(**gains))


def test_replay_gain_is_off_by_default(
    player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    playlist.add([rg_track("a", track_gain=-6.0)])
    player.play()
    assert player.replay_gain is ReplayGainMode.OFF
    assert backend.gains == [1.0]


def test_track_gain_applied_on_play_and_mode_change(
    player: Player, playlist: Playlist, backend: FakeBackend, bus: EventBus
) -> None:
    events: list[ReplayGainChanged] = []
    bus.subscribe(ReplayGainChanged, events.append)
    playlist.add([rg_track("a", track_gain=-6.0206, album_gain=-12.0412), make_tracks(1)[0]])
    player.play()

    player.set_replay_gain(ReplayGainMode.TRACK)  # applies to the current track at once
    assert backend.gains[-1] == pytest.approx(0.5, abs=1e-4)
    player.set_replay_gain(ReplayGainMode.ALBUM)
    assert backend.gains[-1] == pytest.approx(0.25, abs=1e-4)
    assert events == [
        ReplayGainChanged(ReplayGainMode.TRACK),
        ReplayGainChanged(ReplayGainMode.ALBUM),
    ]

    player.next()  # untagged track: no change in level
    assert backend.gains[-1] == 1.0
    assert backend.replay_gain_modes == []  # this backend doesn't read tags itself


def test_backend_that_reads_tags_gets_the_mode_instead(playlist: Playlist, bus: EventBus) -> None:
    class TagReadingBackend(FakeGaplessBackend):
        applies_replay_gain = True

    backend = TagReadingBackend()
    player = Player(backend, playlist, bus)
    playlist.add([rg_track("a", track_gain=-6.0), rg_track("b", track_gain=-3.0)])
    player.set_replay_gain(ReplayGainMode.ALBUM)
    player.play()
    backend.advance()
    assert backend.replay_gain_modes == [ReplayGainMode.ALBUM]
    assert backend.gains == []


# -- Equaliser ------------------------------------------------------------------- #


def test_equalizer_is_passed_to_supporting_backend(playlist: Playlist, bus: EventBus) -> None:
    class EqBackend(FakeBackend):
        supports_equalizer = True

    backend = EqBackend()
    player = Player(backend, playlist, bus)
    events: list[EqualizerChanged] = []
    bus.subscribe(EqualizerChanged, events.append)
    eq = Equalizer(enabled=True).with_preset("Rock")

    player.set_equalizer(eq)

    assert player.supports_equalizer
    assert backend.equalizers == [eq]
    assert player.equalizer == eq
    assert events == [EqualizerChanged(eq)]


def test_equalizer_setting_kept_without_backend_support(
    player: Player, backend: FakeBackend, bus: EventBus
) -> None:
    events: list[EqualizerChanged] = []
    bus.subscribe(EqualizerChanged, events.append)
    eq = Equalizer(enabled=True).with_gain(0, 6)
    player.set_equalizer(eq)
    assert not player.supports_equalizer
    assert backend.equalizers == []  # nothing to apply it to
    assert player.equalizer == eq  # but the setting is remembered
    assert events == [EqualizerChanged(eq)]
