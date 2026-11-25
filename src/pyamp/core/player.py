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

"""The player: a Facade that coordinates the playlist and the audio backend."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from pyamp.audio.backend import AudioBackend
from pyamp.core.equalizer import Equalizer
from pyamp.core.events import (
    AudioFrame,
    AudioInfoChanged,
    EqualizerChanged,
    EventBus,
    PlaybackError,
    PlaybackStateChanged,
    PlaylistChanged,
    PlayModeChanged,
    PositionChanged,
    ReplayGainChanged,
    StreamTitleChanged,
    TrackChanged,
    Unsubscribe,
    VolumeChanged,
)
from pyamp.core.models import Location, PlaybackState, RepeatMode, ReplayGainMode, Track
from pyamp.core.play_order import SequentialOrder, ShuffleOrder
from pyamp.core.playlist import Playlist

log = logging.getLogger(__name__)

#: Pressing "previous" later than this restarts the current track instead.
RESTART_THRESHOLD_MS = 3000

type LocationResolver = Callable[[Location], Location]
"""Turns a playlist location into the one the backend plays (e.g. adds a server login)."""


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
        self._replay_gain = ReplayGainMode.OFF
        self._equalizer = Equalizer()
        # Gapless backends: whether a track is loaded, and what is queued after it.
        self._active = False
        self._queued: tuple[int, Location] | None = None
        self._stream_title = ""
        self._live = False
        self._audio_info = AudioInfoChanged(0, 0)
        self._resolvers: list[LocationResolver] = []
        # What the backend was given -> the playlist location it stands for, so a gapless
        # backend reporting the location it moved on to can be matched to the playlist.
        self._resolved: dict[Location, Location] = {}

        backend.set_listener(self)
        backend.set_volume(self._volume)
        if backend.supports_gapless:
            bus.subscribe(PlaylistChanged, lambda _: self._queue_next())

    # -- Read-only state ---------------------------------------------------- #

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def is_live(self) -> bool:
        """Whether the current track is a live stream (internet radio): no length, no seeking."""
        return self._live

    @property
    def stream_title(self) -> str:
        """The current stream's "now playing" title ("" if none, or not a stream)."""
        return self._stream_title

    @property
    def audio_info(self) -> AudioInfoChanged:
        """The playing audio's bitrate and sample rate (0 where unknown)."""
        return self._audio_info

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

    @property
    def replay_gain(self) -> ReplayGainMode:
        return self._replay_gain

    @property
    def equalizer(self) -> Equalizer:
        return self._equalizer

    @property
    def backend_name(self) -> str:
        """The audio backend in use, e.g. ``"qt"`` or ``"mpv"``."""
        return self._backend.name

    @property
    def provides_audio_frames(self) -> bool:
        """Whether the backend reports the audio it plays (for the visualiser)."""
        return self._backend.provides_audio_frames

    def stream_is_buffered(self) -> bool:
        """Whether the playing stream is wholly in memory (see :meth:`save_stream`)."""
        return self._backend.stream_is_buffered()

    def stream_fits_buffer(self) -> bool:
        """Whether the playing stream can be buffered whole (see the backend's)."""
        return self._backend.stream_fits_buffer()

    def buffered_range(self) -> tuple[float, float] | None:
        """The buffered stretch of the playing stream, in seconds (see the backend's)."""
        return self._backend.buffered_range()

    def save_stream(self, target: Path, start: float = 0.0, end: float | None = None) -> bool:
        """Save the buffered stream (or a stretch of it) to ``target``; any thread."""
        return self._backend.save_stream(target, start, end)

    @property
    def supports_equalizer(self) -> bool:
        """Whether the backend can apply :attr:`equalizer` (the setting is kept either way)."""
        return self._backend.supports_equalizer

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
        self._active = True
        self._queued = None
        self._apply_track_gain(track)
        self._resolved.clear()
        self._backend.load(self._resolve(track.location))
        self._backend.play()
        self._stream_title = ""
        # A stream without a known length (internet radio) is live: no duration, no seeking.
        self._live = track.is_stream and not track.duration_ms
        self._bus.publish(TrackChanged(track, index))
        self._queue_next()

    def pause(self) -> None:
        """Toggle pause (Winamp semantics: pressing pause while paused resumes)."""
        if self._state is PlaybackState.PLAYING:
            self._backend.pause()
        elif self._state is PlaybackState.PAUSED:
            self._backend.play()

    def stop(self) -> None:
        self._active = False
        self._queued = None
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
        if self._live:
            return
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
        self._queue_next()

    def set_repeat(self, mode: RepeatMode) -> None:
        self._repeat = mode
        self._publish_mode()
        self._queue_next()

    def set_replay_gain(self, mode: ReplayGainMode) -> None:
        self._replay_gain = mode
        if self._backend.applies_replay_gain:
            self._backend.set_replay_gain_mode(mode)
        else:
            self._apply_track_gain(self._playlist.current_track)
        self._bus.publish(ReplayGainChanged(mode))

    def set_equalizer(self, equalizer: Equalizer) -> None:
        self._equalizer = equalizer
        if self._backend.supports_equalizer:
            self._backend.set_equalizer(equalizer)
        self._bus.publish(EqualizerChanged(equalizer))

    def shutdown(self) -> None:
        self._backend.stop()
        self._backend.close()

    def add_location_resolver(self, resolver: LocationResolver) -> Unsubscribe:
        """Have ``resolver`` rewrite locations just before the backend gets them. Returns a
        function that undoes it.

        Playlists keep the plain location; a resolver can add what shouldn't be saved,
        such as a media server's login. It returns locations that aren't its own as they
        are; the first resolver to change a location wins. Applies from the next track
        loaded.
        """
        self._resolvers.append(resolver)

        def remove() -> None:
            if resolver in self._resolvers:
                self._resolvers.remove(resolver)

        return remove

    def _resolve(self, location: Location) -> Location:
        resolved = location
        for resolver in tuple(self._resolvers):
            try:
                resolved = resolver(location)
            except Exception:
                log.exception("Location resolver failed for %s; trying the others", location)
                continue
            if resolved != location:
                break
        self._resolved[resolved] = location
        return resolved

    def _publish_mode(self) -> None:
        self._bus.publish(PlayModeChanged(self._shuffle, self._repeat))

    def _apply_track_gain(self, track: Track | None) -> None:
        if self._backend.applies_replay_gain:
            return  # the backend reads the tags itself, exactly at track boundaries
        gains = track.replay_gain if track else None
        self._backend.set_track_gain(gains.factor(self._replay_gain) if gains else 1.0)

    def _queue_next(self) -> None:
        """Tell a gapless backend which track follows the current one."""
        if not self._backend.supports_gapless:
            return  # also keeps ShuffleOrder from being consulted early
        current = self._playlist.current_index
        index: int | None
        if not self._active or current is None:
            index = None
        elif self._repeat is RepeatMode.ONE:
            index = current
        else:
            index = self._playlist.next_index(wrap=self._repeat is not RepeatMode.OFF)
        queued = None if index is None else (index, self._playlist[index].location)
        if queued != self._queued:
            self._queued = queued
            self._backend.queue_next(self._resolve(queued[1]) if queued else None)

    # -- BackendListener ---------------------------------------------------- #

    def on_state_changed(self, state: PlaybackState) -> None:
        if state is not self._state:
            self._state = state
            self._bus.publish(PlaybackStateChanged(state))

    def on_position_changed(self, position_ms: int) -> None:
        self._position_ms = position_ms
        self._bus.publish(PositionChanged(position_ms, self._duration_ms))

    def on_duration_changed(self, duration_ms: int) -> None:
        # For live streams backends report how much they've buffered, which keeps growing;
        # it isn't a length, so a live track's duration stays 0.
        if self._live:
            duration_ms = 0
        self._duration_ms = duration_ms
        self._bus.publish(PositionChanged(self._position_ms, duration_ms))

    def on_end_of_track(self) -> None:
        current = self._playlist.current_index
        if self._repeat is RepeatMode.ONE and current is not None:
            self.play_index(current)
            return
        index = self._playlist.next_index(wrap=self._repeat is RepeatMode.ALL)
        if index is None:
            self.stop()
        else:
            self.play_index(index)

    def on_track_advanced(self, location: Location) -> None:
        location = self._resolved.get(location, location)
        self._resolved.clear()  # the backend won't report this one again
        queued, self._queued = self._queued, None
        index: int | None
        if queued is not None and queued[1] == location and self._points_to(queued[0], location):
            index = queued[0]
        else:
            # The playlist changed during the transition: find the track by its location.
            index = next((i for i, t in enumerate(self._playlist) if t.location == location), None)
        if index is None:
            log.warning("Backend advanced to %s, which is no longer in the playlist", location)
            self.stop()
            return
        track = self._playlist.set_current(index)
        assert track is not None
        self._position_ms = self._duration_ms = 0
        self._apply_track_gain(track)
        self._stream_title = ""
        # A stream without a known length (internet radio) is live: no duration, no seeking.
        self._live = track.is_stream and not track.duration_ms
        self._bus.publish(TrackChanged(track, index))
        self._queue_next()

    def _points_to(self, index: int, location: Location) -> bool:
        return 0 <= index < len(self._playlist) and self._playlist[index].location == location

    def on_stream_title(self, title: str) -> None:
        track = self._playlist.current_track
        if track is not None and track.is_stream and title != self._stream_title:
            self._stream_title = title
            self._bus.publish(StreamTitleChanged(title))

    def on_audio_info(self, bitrate: int, sample_rate: int) -> None:
        info = AudioInfoChanged(round(max(bitrate, 0) / 1000), max(sample_rate, 0))
        if info != self._audio_info:
            self._audio_info = info
            self._bus.publish(info)

    def on_error(self, message: str) -> None:
        track = self._playlist.current_track
        log.warning("Playback error for %s: %s", track.location if track else None, message)
        self._bus.publish(PlaybackError(message, track))

    def on_audio_frame(self, samples: NDArray[np.float32], sample_rate: int) -> None:
        self._bus.publish(AudioFrame(samples, sample_rate))
