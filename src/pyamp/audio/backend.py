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

"""The contract between the player and whatever actually produces sound.

A backend is an *Adapter* around a concrete audio library (QtMultimedia,
libmpv, GStreamer, ...). It is driven through :class:`AudioBackend` and reports
back through a :class:`BackendListener`. It never touches the playlist or the
event bus directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Protocol

import numpy as np
from numpy.typing import NDArray

from pyamp.core.equalizer import Equalizer
from pyamp.core.models import Location, OutputDevice, PlaybackState, ReplayGainMode


class BackendListener(Protocol):
    """Callbacks a backend uses to report what is happening."""

    def on_state_changed(self, state: PlaybackState) -> None: ...
    def on_position_changed(self, position_ms: int) -> None: ...
    def on_duration_changed(self, duration_ms: int) -> None: ...
    def on_end_of_track(self) -> None: ...
    def on_track_advanced(self, location: Location) -> None:
        """The queued track (see :meth:`AudioBackend.queue_next`) started without a gap."""
        ...

    def on_stream_title(self, title: str) -> None:
        """A stream reported what it's playing now (e.g. an internet radio's current song)."""
        ...

    def on_station_name(self, name: str) -> None:
        """A stream reported its own name (an internet radio station's ICY name)."""
        ...

    def on_error(self, message: str) -> None: ...
    def on_audio_info(self, bitrate: int, sample_rate: int) -> None:
        """The playing audio's bitrate (bits/s) and sample rate (Hz); 0 if unknown.

        Reported when either changes (VBR files and streams may vary as they play), and
        as 0, 0 once nothing is playing. Optional.
        """
        ...

    def on_output_devices_changed(self) -> None:
        """A device was connected or disconnected (see :meth:`AudioBackend.output_devices`)."""
        return None

    def on_audio_frame(self, samples: NDArray[np.float32], sample_rate: int) -> None:
        """Mono float32 PCM in [-1, 1] that is currently being played (optional)."""
        ...


class _NullListener:
    """Null Object used before a real listener is attached, so callers never check for None."""

    def on_state_changed(self, state: PlaybackState) -> None:
        pass

    def on_position_changed(self, position_ms: int) -> None:
        pass

    def on_duration_changed(self, duration_ms: int) -> None:
        pass

    def on_end_of_track(self) -> None:
        pass

    def on_track_advanced(self, location: Location) -> None:
        pass

    def on_stream_title(self, title: str) -> None:
        pass

    def on_station_name(self, name: str) -> None:
        pass

    def on_error(self, message: str) -> None:
        pass

    def on_audio_info(self, bitrate: int, sample_rate: int) -> None:
        pass

    def on_output_devices_changed(self) -> None:
        pass

    def on_audio_frame(self, samples: NDArray[np.float32], sample_rate: int) -> None:
        pass


class AudioBackend(ABC):
    """Base class for audio backends."""

    #: Identifier used in settings and on the command line (``--backend``).
    name: ClassVar[str]

    #: Whether :meth:`queue_next` is honoured. If not, the player loads each track
    #: only after the previous one has ended.
    supports_gapless: ClassVar[bool] = False

    #: Whether the backend reads ReplayGain tags itself (see :meth:`set_replay_gain_mode`).
    #: If not, the player computes each track's gain and calls :meth:`set_track_gain`.
    applies_replay_gain: ClassVar[bool] = False

    #: Whether :meth:`set_equalizer` has any effect.
    supports_equalizer: ClassVar[bool] = False

    #: Whether the backend reports the PCM it plays (:meth:`BackendListener.on_audio_frame`).
    #: If not, the visualiser is fed by analysing the file separately (``track_analysis``).
    provides_audio_frames: ClassVar[bool] = False

    #: Whether :meth:`output_devices` and :meth:`set_output_device` work.
    supports_output_devices: ClassVar[bool] = False

    def __init__(self) -> None:
        self._listener: BackendListener = _NullListener()

    def set_listener(self, listener: BackendListener) -> None:
        self._listener = listener

    def detach_listener(self) -> None:
        """Stop reporting to the listener (e.g. once closed, for callbacks still queued)."""
        self._listener = _NullListener()

    @abstractmethod
    def load(self, location: Location) -> None:
        """Prepare a file or stream for playback, replacing the current source."""

    @abstractmethod
    def play(self) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def seek(self, position_ms: int) -> None: ...

    @abstractmethod
    def set_volume(self, volume: float) -> None:
        """Set the volume on a perceptual 0.0-1.0 scale."""

    def queue_next(self, location: Location | None) -> None:
        """Play ``location`` straight after the current track, without a gap (or nothing if None).

        Replaces any previously queued track; :meth:`load` and :meth:`stop` discard
        it. When the transition happens the backend calls
        :meth:`BackendListener.on_track_advanced`. Only called on backends with
        :attr:`supports_gapless`.
        """
        return None

    def set_replay_gain_mode(self, mode: ReplayGainMode) -> None:
        """Apply ReplayGain from the files' own tags. Only for :attr:`applies_replay_gain`."""
        return None

    def set_track_gain(self, factor: float) -> None:
        """Scale the output by ``factor`` (linear) on top of the user's volume."""
        return None

    def set_equalizer(self, equalizer: Equalizer) -> None:
        """Filter the output through ``equalizer``. Only for :attr:`supports_equalizer`."""
        return None

    def output_devices(self) -> list[OutputDevice]:
        """The devices it can play through now, not counting the system default.

        Reports changes with :meth:`BackendListener.on_output_devices_changed`.
        """
        return []

    def set_output_device(self, device_id: str | None) -> None:
        """Play through the device with ``device_id`` (from :meth:`output_devices`), or
        through the system's default output if None, following it when it changes."""
        return None

    def buffered_range(self) -> tuple[float, float] | None:
        """The stretch of the playing stream that's buffered, in seconds on the same clock as
        the playback position (e.g. the next few seconds of live radio, or all of a song
        from a server); None if unknown."""
        return None

    def save_stream(self, target: Path, start: float, end: float) -> bool:
        """Write the buffered stream from ``start`` to ``end`` seconds to ``target``,
        without fetching it again; False if it can't. May be called from any thread.

        For the visualiser of backends without :attr:`provides_audio_frames`: it analyses
        a stream a piece at a time, as it's buffered.
        """
        return False

    def close(self) -> None:
        """Release native resources. Override if needed."""
        return None
