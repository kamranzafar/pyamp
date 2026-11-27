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

"""A synchronous, type-keyed publish/subscribe event bus (Observer pattern).

Components never call each other to report a change. They publish immutable
event objects, and anything interested (UI widgets, the spectrum analyser,
plugins) subscribes by event type. This keeps the player and playlist free of
any knowledge about who is listening.

Handlers run synchronously on the publishing thread. In this application all
publishers run on the Qt main thread, so no locking is required.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pyamp.core.equalizer import Equalizer
from pyamp.core.models import OutputDevice, PlaybackState, RepeatMode, ReplayGainMode, Track

log = logging.getLogger(__name__)

type Unsubscribe = Callable[[], None]


class EventBus:
    """Dispatches events to handlers registered for the event's exact type."""

    def __init__(self) -> None:
        self._handlers: defaultdict[type[Any], list[Callable[[Any], None]]] = defaultdict(list)

    def subscribe[E](self, event_type: type[E], handler: Callable[[E], None]) -> Unsubscribe:
        """Register ``handler`` for ``event_type``. Returns a function that undoes it."""
        handlers = self._handlers[event_type]
        handlers.append(handler)

        def unsubscribe() -> None:
            if handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    def publish(self, event: object) -> None:
        """Deliver ``event`` to every subscriber.

        A failing handler is logged and skipped so one broken plugin or widget
        cannot take down playback.
        """
        for handler in tuple(self._handlers.get(type(event), ())):
            try:
                handler(event)
            except Exception:
                log.exception("Handler %r failed on %s", handler, type(event).__name__)


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class TrackChanged:
    track: Track | None
    index: int | None


@dataclass(frozen=True, slots=True)
class PlaybackStateChanged:
    state: PlaybackState


@dataclass(frozen=True, slots=True)
class PositionChanged:
    position_ms: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class VolumeChanged:
    volume: float  # 0.0 - 1.0, perceptual


@dataclass(frozen=True, slots=True)
class PlayModeChanged:
    shuffle: bool
    repeat: RepeatMode


@dataclass(frozen=True, slots=True)
class ReplayGainChanged:
    mode: ReplayGainMode


@dataclass(frozen=True, slots=True)
class OutputDeviceChanged:
    """The chosen output device changed, or the devices there are did."""

    #: The device chosen to play through; None for the system's default.
    chosen: OutputDevice | None
    #: False while ``chosen`` isn't connected: the system default plays until it's back.
    connected: bool


@dataclass(frozen=True, slots=True)
class EqualizerChanged:
    equalizer: Equalizer


@dataclass(frozen=True, slots=True)
class PlaylistChanged:
    """The playlist's contents changed (tracks added, removed or reordered).

    If ``appended_from`` is set, the *only* change was tracks appended from that
    index onwards, and listeners may update incrementally instead of rebuilding.
    """

    appended_from: int | None = None


@dataclass(frozen=True, slots=True)
class LibraryChanged:
    """The media library's tracks or folders changed (e.g. a scan finished)."""


@dataclass(frozen=True, slots=True)
class MediaSourcesChanged:
    """A media source was added or removed (``source`` None), or ``source``'s contents
    changed. The source is a :class:`pyamp.library.sources.MediaSource`."""

    source: object | None = None


@dataclass(frozen=True, slots=True)
class SavedPlaylistsChanged:
    """The saved playlists, or which one is open, changed."""

    names: tuple[str, ...]
    active: str


@dataclass(frozen=True, slots=True)
class SavedPlaylistEdited:
    """The saved playlist ``name`` has been written with different tracks (e.g. the
    open playlist's edits were saved)."""

    name: str


@dataclass(frozen=True, slots=True)
class SmartPlaylistsChanged:
    """A smart playlist was made, changed or deleted."""


@dataclass(frozen=True, slots=True)
class RadioStationsChanged:
    """A radio station was added, changed or deleted."""


@dataclass(frozen=True, slots=True)
class AudioInfoChanged:
    """What's playing: bitrate in kbps and sample rate in Hz (0 where unknown).

    Follows the audio, not the track: it carries on unchanged into a next track of the
    same format, and is 0, 0 once nothing plays.
    """

    bitrate_kbps: int
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class StreamTitleChanged:
    """What a stream (e.g. an internet radio station) says it's playing now.

    Published only while a stream is the current track; a new track resets it
    (consumers treat :class:`TrackChanged` as "no stream title yet").
    """

    title: str


@dataclass(frozen=True, slots=True)
class TrackRetitled:
    """The playing track was renamed where it is, and plays on (e.g. a stream known only
    by its URL was given the name its station announced). Not a new track: what showed
    :class:`TrackChanged` shows ``track`` instead, keeping any stream title."""

    track: Track
    index: int


@dataclass(frozen=True, slots=True)
class PlaybackError:
    message: str
    track: Track | None = None


@dataclass(frozen=True, slots=True, eq=False)
class AudioFrame:
    """A chunk of decoded PCM audio that is currently being played.

    ``samples`` is mono float32 in the range [-1, 1].
    """

    samples: NDArray[np.float32] = field(repr=False)
    sample_rate: int


@dataclass(frozen=True, slots=True)
class SpectrumUpdated:
    """Normalised (0..1) band levels, lowest frequency first."""

    levels: tuple[float, ...]
