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

"""Exercises the real MediaPlayer framework, so only runs on macOS."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from pyamp.core.events import EventBus
from pyamp.core.models import PlaybackState, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.media_session import MediaCommands, MediaSession, NowPlaying
from tests.conftest import FakeBackend, make_tracks

if sys.platform != "darwin":
    pytest.skip("macOS only", allow_module_level=True)

import MediaPlayer as mp  # noqa: N813  (pyobjc framework module)

from pyamp.media_session.macos import MacNowPlayingAdapter

SUCCESS = int(mp.MPRemoteCommandHandlerStatusSuccess)
FAILED = int(mp.MPRemoteCommandHandlerStatusCommandFailed)


@pytest.fixture
def adapter(player: Player) -> Iterator[MacNowPlayingAdapter]:
    adapter = MacNowPlayingAdapter(MediaCommands(player))
    yield adapter
    adapter.close()


def info() -> dict[str, Any]:
    current = mp.MPNowPlayingInfoCenter.defaultCenter().nowPlayingInfo()
    return dict(current) if current is not None else {}


def playback_state() -> int:
    return int(mp.MPNowPlayingInfoCenter.defaultCenter().playbackState())


def test_update_publishes_track_info(adapter: MacNowPlayingAdapter) -> None:
    track = Track(location=Path("/m/a.mp3"), title="Song", artist="Band", album="Record")
    adapter.update(
        NowPlaying(
            track=track,
            state=PlaybackState.PLAYING,
            position_ms=12_500,
            duration_ms=180_000,
            can_go_next=True,
        )
    )
    assert info() == {
        mp.MPMediaItemPropertyTitle: "Song",
        mp.MPMediaItemPropertyArtist: "Band",
        mp.MPMediaItemPropertyAlbumTitle: "Record",
        mp.MPMediaItemPropertyPlaybackDuration: 180.0,
        mp.MPNowPlayingInfoPropertyElapsedPlaybackTime: 12.5,
        mp.MPNowPlayingInfoPropertyPlaybackRate: 1.0,
        mp.MPNowPlayingInfoPropertyMediaType: mp.MPNowPlayingInfoMediaTypeAudio,
    }
    assert playback_state() == mp.MPNowPlayingPlaybackStatePlaying


def test_empty_tags_are_left_out(adapter: MacNowPlayingAdapter) -> None:
    adapter.update(NowPlaying(track=Track.from_path(Path("/m/untagged.mp3"))))
    assert info()[mp.MPMediaItemPropertyTitle] == "untagged"
    assert mp.MPMediaItemPropertyArtist not in info()
    assert mp.MPMediaItemPropertyAlbumTitle not in info()


def test_paused_track_has_zero_rate(adapter: MacNowPlayingAdapter) -> None:
    track = Track.from_path(Path("/m/a.mp3"))
    adapter.update(NowPlaying(track=track, state=PlaybackState.PAUSED, duration_ms=1000))
    assert info()[mp.MPNowPlayingInfoPropertyPlaybackRate] == 0.0
    assert playback_state() == mp.MPNowPlayingPlaybackStatePaused


def test_no_track_clears_info(adapter: MacNowPlayingAdapter) -> None:
    adapter.update(NowPlaying(track=Track.from_path(Path("/m/a.mp3"))))
    adapter.update(NowPlaying())
    assert info() == {}
    assert playback_state() == mp.MPNowPlayingPlaybackStateStopped


def test_commands_follow_availability(adapter: MacNowPlayingAdapter) -> None:
    center = mp.MPRemoteCommandCenter.sharedCommandCenter()
    adapter.update(NowPlaying())
    assert not center.nextTrackCommand().isEnabled()
    assert not center.changePlaybackPositionCommand().isEnabled()

    track = Track.from_path(Path("/m/a.mp3"))
    adapter.update(
        NowPlaying(track=track, duration_ms=1000, can_go_next=True, can_go_previous=True)
    )
    assert center.nextTrackCommand().isEnabled()
    assert center.previousTrackCommand().isEnabled()
    assert center.changePlaybackPositionCommand().isEnabled()


def test_command_handlers_drive_the_player(
    adapter: MacNowPlayingAdapter, player: Player, playlist: Playlist, backend: FakeBackend
) -> None:
    playlist.add(make_tracks(3))
    handlers = adapter.handlers

    assert handlers["toggle"](None) == SUCCESS
    assert player.state is PlaybackState.PLAYING
    assert handlers["pause"](None) == SUCCESS
    assert player.state is PlaybackState.PAUSED
    assert handlers["play"](None) == SUCCESS
    assert player.state is PlaybackState.PLAYING

    assert handlers["next"](None) == SUCCESS
    assert playlist.current_index == 1
    assert handlers["previous"](None) == SUCCESS
    assert playlist.current_index == 0

    player.on_duration_changed(60_000)
    assert handlers["seek"](SimpleNamespace(positionTime=lambda: 42.25)) == SUCCESS
    assert backend.position == 42_250

    assert handlers["stop"](None) == SUCCESS
    assert player.state is PlaybackState.STOPPED


def test_failing_command_reports_failure(adapter: MacNowPlayingAdapter) -> None:
    assert adapter.handlers["seek"](object()) == FAILED  # event without positionTime()


def test_close_clears_everything(player: Player) -> None:
    adapter = MacNowPlayingAdapter(MediaCommands(player))
    adapter.update(NowPlaying(track=Track.from_path(Path("/m/a.mp3")), state=PlaybackState.PLAYING))
    adapter.close()
    assert adapter.handlers == {}
    assert info() == {}
    assert playback_state() == mp.MPNowPlayingPlaybackStateStopped


def test_end_to_end_with_session(
    bus: EventBus, player: Player, playlist: Playlist, adapter: MacNowPlayingAdapter
) -> None:
    session = MediaSession(bus, player, playlist, adapter)
    playlist.add([Track(location=Path("/m/x.mp3"), title="X", artist="Y", duration_ms=90_000)])
    player.play()
    assert info()[mp.MPMediaItemPropertyTitle] == "X"
    assert info()[mp.MPMediaItemPropertyPlaybackDuration] == 90.0
    assert playback_state() == mp.MPNowPlayingPlaybackStatePlaying
    session.close()
    assert info() == {}
