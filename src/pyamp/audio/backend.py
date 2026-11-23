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

from pyamp.core.models import PlaybackState


class BackendListener(Protocol):
    """Callbacks a backend uses to report what is happening."""

    def on_state_changed(self, state: PlaybackState) -> None: ...
    def on_position_changed(self, position_ms: int) -> None: ...
    def on_duration_changed(self, duration_ms: int) -> None: ...
    def on_end_of_track(self) -> None: ...
    def on_track_advanced(self, path: Path) -> None:
        """The queued track (see :meth:`AudioBackend.queue_next`) started without a gap."""
        ...

    def on_error(self, message: str) -> None: ...
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

    def on_track_advanced(self, path: Path) -> None:
        pass

    def on_error(self, message: str) -> None:
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

    def __init__(self) -> None:
        self._listener: BackendListener = _NullListener()

    def set_listener(self, listener: BackendListener) -> None:
        self._listener = listener

    @abstractmethod
    def load(self, path: Path) -> None:
        """Prepare ``path`` for playback, replacing the current source."""

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

    def queue_next(self, path: Path | None) -> None:
        """Play ``path`` straight after the current track, without a gap (or nothing if None).

        Replaces any previously queued track; :meth:`load` and :meth:`stop` discard
        it. When the transition happens the backend calls
        :meth:`BackendListener.on_track_advanced`. Only called on backends with
        :attr:`supports_gapless`.
        """
        return None

    def close(self) -> None:
        """Release native resources. Override if needed."""
        return None
