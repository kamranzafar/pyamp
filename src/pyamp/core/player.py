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

"""The player: a Facade that coordinates the playlist and the audio backend."""

from __future__ import annotations

import logging

import numpy as np
from numpy.typing import NDArray

from pyamp.audio.backend import AudioBackend
from pyamp.core.events import (
    AudioFrame,
    EventBus,
    PlaybackError,
    PlaybackStateChanged,
    PlayModeChanged,
    PositionChanged,
    TrackChanged,
    VolumeChanged,
)
from pyamp.core.models import PlaybackState, RepeatMode
from pyamp.core.play_order import SequentialOrder, ShuffleOrder
from pyamp.core.playlist import Playlist

log = logging.getLogger(__name__)

#: Pressing "previous" later than this restarts the current track instead.
RESTART_THRESHOLD_MS = 3000


class Player:
    """High-level playback API used by the UI, keyboard shortcuts and plugins.

    The player is the backend's :class:`~pyamp.audio.backend.BackendListener`.
    It translates low-level backend callbacks into domain events on the bus.
    """

    def __init__(self, backend: AudioBackend, playlist: Playlist, bus: EventBus) -> None:
        self._backend = backend
        self._playlist = playlist
        self._bus = bus

        self._state = PlaybackState.STOPPED
        self._position_ms = 0
        self._duration_ms = 0
        self._volume = 0.8
        self._shuffle = False
        self._repeat = RepeatMode.OFF

        backend.set_listener(self)
        backend.set_volume(self._volume)

    # -- Read-only state ---------------------------------------------------- #

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def position_ms(self) -> int:
        return self._position_ms

    @property
    def duration_ms(self) -> int:
        return self._duration_ms

    @property
    def volume(self) -> float:
        return self._volume

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def repeat(self) -> RepeatMode:
        return self._repeat

    # -- Transport ---------------------------------------------------------- #

    def play(self) -> None:
        """Resume if paused, restart if stopped, or start the first track."""
        if self._state is PlaybackState.PAUSED:
            self._backend.play()
        elif self._playlist.current_index is not None:
            self.play_index(self._playlist.current_index)
        else:
            first = self._playlist.next_index(wrap=False)
            if first is not None:
                self.play_index(first)

    def play_index(self, index: int) -> None:
        track = self._playlist.set_current(index)
        assert track is not None
        self._position_ms = self._duration_ms = 0
        self._backend.load(track.path)
        self._backend.play()
        self._bus.publish(TrackChanged(track, index))

    def pause(self) -> None:
        """Toggle pause (Winamp semantics: pressing pause while paused resumes)."""
        if self._state is PlaybackState.PLAYING:
            self._backend.pause()
        elif self._state is PlaybackState.PAUSED:
            self._backend.play()

    def stop(self) -> None:
        self._backend.stop()

    def next(self) -> None:
        index = self._playlist.next_index(wrap=self._repeat is not RepeatMode.OFF)
        if index is not None:
            self.play_index(index)

    def previous(self) -> None:
        if self._position_ms > RESTART_THRESHOLD_MS:
            self.seek(0)
            return
        index = self._playlist.previous_index(wrap=self._repeat is not RepeatMode.OFF)
        if index is not None:
            self.play_index(index)

    def seek(self, position_ms: int) -> None:
        upper = self._duration_ms or position_ms
        self._backend.seek(max(0, min(position_ms, upper)))

    # -- Settings ----------------------------------------------------------- #

    def set_volume(self, volume: float) -> None:
        self._volume = max(0.0, min(volume, 1.0))
        self._backend.set_volume(self._volume)
        self._bus.publish(VolumeChanged(self._volume))

    def set_shuffle(self, enabled: bool) -> None:
        self._shuffle = enabled
        self._playlist.play_order = ShuffleOrder() if enabled else SequentialOrder()
        self._publish_mode()

    def set_repeat(self, mode: RepeatMode) -> None:
        self._repeat = mode
        self._publish_mode()

    def shutdown(self) -> None:
        self._backend.stop()
        self._backend.close()

    def _publish_mode(self) -> None:
        self._bus.publish(PlayModeChanged(self._shuffle, self._repeat))

    # -- BackendListener ---------------------------------------------------- #

    def on_state_changed(self, state: PlaybackState) -> None:
        if state is not self._state:
            self._state = state
            self._bus.publish(PlaybackStateChanged(state))

    def on_position_changed(self, position_ms: int) -> None:
        self._position_ms = position_ms
        self._bus.publish(PositionChanged(position_ms, self._duration_ms))

    def on_duration_changed(self, duration_ms: int) -> None:
        self._duration_ms = duration_ms
        self._bus.publish(PositionChanged(self._position_ms, duration_ms))

    def on_end_of_track(self) -> None:
        current = self._playlist.current_index
        if self._repeat is RepeatMode.ONE and current is not None:
            self.play_index(current)
            return
        index = self._playlist.next_index(wrap=self._repeat is RepeatMode.ALL)
        if index is None:
            self._backend.stop()
        else:
            self.play_index(index)

    def on_error(self, message: str) -> None:
        track = self._playlist.current_track
        log.warning("Playback error for %s: %s", track.path if track else None, message)
        self._bus.publish(PlaybackError(message, track))

    def on_audio_frame(self, samples: NDArray[np.float32], sample_rate: int) -> None:
        self._bus.publish(AudioFrame(samples, sample_rate))
