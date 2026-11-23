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

"""Platform-neutral bridge between the player and the OS media session.

:class:`MediaSession` turns bus events into :class:`NowPlaying` snapshots for a
platform adapter (macOS Now Playing, MPRIS, ...), and :class:`MediaCommands`
turns the OS's media-key and widget commands back into player calls.

No Qt and no platform APIs here, so all of the behaviour is unit-testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from pyamp.core.events import (
    EventBus,
    PlaybackStateChanged,
    PlaylistChanged,
    PlayModeChanged,
    PositionChanged,
    TrackChanged,
    Unsubscribe,
    VolumeChanged,
)
from pyamp.core.models import PlaybackState, RepeatMode, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist

#: A reported position this far from the extrapolated one is treated as a seek.
SEEK_THRESHOLD_MS = 1500


@dataclass(frozen=True, slots=True)
class NowPlaying:
    """Everything an OS media widget needs to show and control playback.

    ``position_ms`` is a sample taken when the snapshot was made. Adapters pass it
    to the OS together with the playback rate, and the OS extrapolates from there.
    """

    track: Track | None = None
    state: PlaybackState = PlaybackState.STOPPED
    position_ms: int = 0
    duration_ms: int = 0
    volume: float = 0.0
    shuffle: bool = False
    repeat: RepeatMode = RepeatMode.OFF
    can_go_next: bool = False
    can_go_previous: bool = False

    @property
    def playing(self) -> bool:
        return self.state is PlaybackState.PLAYING

    @property
    def can_play(self) -> bool:
        return self.track is not None or self.can_go_next

    @property
    def can_seek(self) -> bool:
        return self.track is not None and self.duration_ms > 0


class MediaSessionAdapter(Protocol):
    """A platform integration that shows a :class:`NowPlaying` snapshot."""

    def update(self, now_playing: NowPlaying) -> None:
        """Show this snapshot. Called only when something visible changed."""
        ...

    def seeked(self, position_ms: int) -> None:
        """Playback jumped to ``position_ms`` (called after :meth:`update`)."""
        ...

    def close(self) -> None:
        """Clear the OS widget and stop receiving commands."""
        ...


class MediaCommands:
    """OS media commands, mapped onto :class:`Player`.

    The OS sends explicit "play" and "pause" (Bluetooth headsets, Control Center),
    so unlike Winamp's toggling pause button these are idempotent.
    """

    def __init__(self, player: Player) -> None:
        self._player = player

    @property
    def position_ms(self) -> int:
        return self._player.position_ms

    def play(self) -> None:
        if self._player.state is not PlaybackState.PLAYING:
            self._player.play()

    def pause(self) -> None:
        if self._player.state is PlaybackState.PLAYING:
            self._player.pause()

    def play_pause(self) -> None:
        if self._player.state is PlaybackState.STOPPED:
            self._player.play()
        else:
            self._player.pause()  # toggles between playing and paused

    def stop(self) -> None:
        self._player.stop()

    def next(self) -> None:
        self._player.next()

    def previous(self) -> None:
        self._player.previous()

    def seek_to(self, position_ms: int) -> None:
        self._player.seek(position_ms)

    def seek_by(self, offset_ms: int) -> None:
        self._player.seek(self._player.position_ms + offset_ms)

    def set_volume(self, volume: float) -> None:
        self._player.set_volume(volume)

    def set_shuffle(self, enabled: bool) -> None:
        self._player.set_shuffle(enabled)

    def set_repeat(self, mode: RepeatMode) -> None:
        self._player.set_repeat(mode)


class MediaSession:
    """Keeps a :class:`MediaSessionAdapter` in sync with the player.

    Position ticks are *not* forwarded: OS widgets extrapolate the position from
    the last update, so the adapter is only told when the position deviates from
    that extrapolation (a seek), or alongside another change.
    """

    def __init__(
        self,
        bus: EventBus,
        player: Player,
        playlist: Playlist,
        adapter: MediaSessionAdapter,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._player = player
        self._playlist = playlist
        self._adapter = adapter
        self._clock = clock
        self._position_ms = player.position_ms
        self._shown = self._snapshot()
        self._shown_at = clock()
        adapter.update(self._shown)
        self._unsubscribe: list[Unsubscribe] = [
            bus.subscribe(TrackChanged, self._on_change),
            bus.subscribe(PlaybackStateChanged, self._on_change),
            bus.subscribe(PlayModeChanged, self._on_change),
            bus.subscribe(VolumeChanged, self._on_change),
            bus.subscribe(PlaylistChanged, self._on_change),
            bus.subscribe(PositionChanged, self._on_position),
        ]

    @property
    def now_playing(self) -> NowPlaying:
        """The snapshot most recently sent to the adapter."""
        return self._shown

    def close(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        self._adapter.close()

    # -- Event handlers ----------------------------------------------------- #

    def _on_change(self, event: object) -> None:
        if isinstance(event, TrackChanged):
            self._position_ms = 0
        self._push(self._snapshot())

    def _on_position(self, event: PositionChanged) -> None:
        expected = self._expected_position()
        self._position_ms = event.position_ms
        snapshot = self._snapshot()
        if abs(event.position_ms - expected) > SEEK_THRESHOLD_MS:
            self._push(snapshot, force=True)
            self._adapter.seeked(event.position_ms)
        else:
            self._push(snapshot)  # only sent if e.g. the duration became known

    # -- Helpers ------------------------------------------------------------ #

    def _snapshot(self) -> NowPlaying:
        track = self._playlist.current_track
        # Asking the play order for the next index could reshuffle as a side effect,
        # so "can go next/previous" simply means there is something to play.
        has_tracks = len(self._playlist) > 0
        return NowPlaying(
            track=track,
            state=self._player.state,
            position_ms=self._position_ms,
            duration_ms=self._player.duration_ms or (track.duration_ms if track else 0),
            volume=self._player.volume,
            shuffle=self._player.shuffle,
            repeat=self._player.repeat,
            can_go_next=has_tracks,
            can_go_previous=has_tracks,
        )

    def _expected_position(self) -> int:
        """Where the OS thinks playback is, extrapolating from the last update."""
        if not self._shown.playing:
            return self._shown.position_ms
        elapsed_ms = int((self._clock() - self._shown_at) * 1000)
        return self._shown.position_ms + elapsed_ms

    def _push(self, snapshot: NowPlaying, *, force: bool = False) -> None:
        unchanged = replace(snapshot, position_ms=0) == replace(self._shown, position_ms=0)
        if unchanged and not force:
            return
        self._shown = snapshot
        self._shown_at = self._clock()
        self._adapter.update(snapshot)
