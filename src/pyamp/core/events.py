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

from pyamp.core.models import PlaybackState, RepeatMode, Track

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
class PlaylistChanged:
    """The playlist's contents changed (tracks added, removed or reordered).

    If ``appended_from`` is set, the *only* change was tracks appended from that
    index onwards, and listeners may update incrementally instead of rebuilding.
    """

    appended_from: int | None = None


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
