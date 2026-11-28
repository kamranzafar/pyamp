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

"""Finds the playing track's chords from the audio the :class:`AudioFeed` decodes, shared
with the visualiser: nothing is decoded, fetched or saved twice.

The notes heard (chroma) are kept frame by frame in a :class:`ChromaTimeline`, each at its
place in the track, so a stream's pieces can come in any order (after a seek, say). A file's
chords are found once it's all analysed; a stream's again as each piece comes, so the chart
grows as a song buffers, and follows a station (its last couple of minutes).
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, Signal, Slot

from pyamp.audio.track_analysis import ANALYSIS_RATE, LIVE_KEEP_S, AudioFeed, FeedSource
from pyamp.plugins.chords.analysis import (
    NOTE_COLUMNS,
    ChordChart,
    Frames,
    chart_from_frames,
    chroma_frames,
    frame_sizes,
)
from pyamp.plugins.chords.beats import onset_sizes, onset_strength
from pyamp.plugins.chords.cache import ChordCache

log = logging.getLogger(__name__)

#: A song from a server is known whole once this much of it is analysed (the frames where
#: its pieces meet aren't).
WHOLE_SONG = 0.95

type TrackerState = Literal["idle", "nothing", "finding", "listening", "done", "failed"]


class _Track:
    """Values per frame (rows of ``width``, or single values), by frame number: grown as
    they come, with gaps; only the last ``keep`` frames held, if given."""

    def __init__(self, width: int | None, keep: int | None) -> None:
        self._shape: tuple[int, ...] = (width,) if width else ()
        self._keep = keep
        self.first = 0  # the number of the first frame held
        self.count = 0  # frames held (gaps included), from first on
        self.values = np.zeros((256, *self._shape), dtype=np.float32)
        self.present = np.zeros(256, dtype=bool)

    def put(self, number: int, values: NDArray[Any]) -> None:
        """Frames ``number`` on (one per row). Frames older than what's held are ignored."""
        skip = max(self.first - number, 0)
        values, number = values[skip:], number + skip
        if not len(values):
            return
        at = number - self.first
        self._reserve(at + len(values))
        self.values[at : at + len(values)] = values
        self.present[at : at + len(values)] = True
        self.count = max(self.count, at + len(values))
        self._drop_old()

    def held(self) -> tuple[NDArray[Any], NDArray[np.bool_]]:
        """Copies of what's held (for another thread to work on)."""
        return self.values[: self.count].copy(), self.present[: self.count].copy()

    def _reserve(self, count: int) -> None:
        if count <= len(self.present):
            return
        size = max(count, 2 * len(self.present))  # doubling: a long file costs little
        grown = size - len(self.present)
        self.values = np.concatenate([self.values, np.zeros((grown, *self._shape), np.float32)])
        self.present = np.concatenate([self.present, np.zeros(grown, dtype=bool)])

    def _drop_old(self) -> None:
        if self._keep is None or self.count <= 2 * self._keep:
            return
        drop = self.count - self._keep
        values, present = np.zeros_like(self.values), np.zeros_like(self.present)
        values[: self.count - drop] = self.values[drop : self.count]
        present[: self.count - drop] = self.present[drop : self.count]
        self.values, self.present = values, present
        self.first += drop
        self.count -= drop


class ChromaTimeline:
    """What's heard through a track, frame by frame, filled in as samples come: each
    frame's notes (fine pitch energy) and loudness, and the onsets for the beats.

    Written on the analysis thread; :meth:`frames` copies it for the chords thread, and
    only :attr:`kept_from` is read on the main thread.
    """

    def __init__(self, sample_rate: int = ANALYSIS_RATE, keep_seconds: float | None = None):
        self.sample_rate = sample_rate
        self.frame, self.hop = frame_sizes(sample_rate)
        self.onset_frame, self.onset_hop = onset_sizes(sample_rate)
        self.cancelled = threading.Event()
        keep = (lambda hop: round(keep_seconds * sample_rate / hop)) if keep_seconds else None
        self._notes = _Track(NOTE_COLUMNS, keep(self.hop) if keep else None)
        self._loudness = _Track(None, keep(self.hop) if keep else None)
        self._onsets = _Track(None, keep(self.onset_hop) if keep else None)
        #: How far into the track the samples given reach, in seconds.
        self.end = 0.0

    @property
    def kept_from(self) -> float:
        """Seconds before which nothing is held any more (a station's oldest)."""
        return self._notes.first * self.hop / self.sample_rate

    @property
    def covered(self) -> float:
        """Seconds of the track analysed (the frames held)."""
        return float(self._notes.present[: self._notes.count].sum()) * self.hop / self.sample_rate

    def put(self, number: int, notes: NDArray[Any], loudness: NDArray[Any]) -> None:
        self._notes.put(number, notes)
        self._loudness.put(number, loudness)

    def put_onsets(self, number: int, onsets: NDArray[Any]) -> None:
        self._onsets.put(number, onsets)

    def frames(self) -> Frames:
        """A copy of what's held, to find the chords from."""
        notes, present = self._notes.held()
        loudness, _ = self._loudness.held()
        onsets, _ = self._onsets.held()
        return Frames(
            notes,
            loudness,
            self.end,
            self.sample_rate,
            first=self._notes.first,
            present=present,
            onsets=onsets,
            onsets_first=self._onsets.first,
        )

    def chart(self) -> ChordChart:
        return chart_from_frames(self.frames())


class _Framer:
    """Cuts a stretch's samples into frames of ``size`` on the track's grid of frames,
    ``hop`` apart (frames reaching outside the stretch, where two pieces meet, are left
    out)."""

    def __init__(self, size: int, hop: int, first_sample: int) -> None:
        self._size = size
        self._hop = hop
        self._next = -(-first_sample // hop)  # the first frame wholly in the stretch
        self._buffer_start = first_sample  # the sample number of the buffer's first
        self._buffer = np.zeros(0, dtype=np.float32)

    def put(self, samples: NDArray[np.float32]) -> tuple[int, NDArray[np.float32]] | None:
        """The frames now whole: (the first's number, the frames, one per row)."""
        size, hop = self._size, self._hop
        self._buffer = np.concatenate([self._buffer, samples])
        last = (self._buffer_start + len(self._buffer) - size) // hop
        made = None
        if last >= self._next:
            offset = self._next * hop - self._buffer_start
            usable = self._buffer[offset : offset + (last - self._next) * hop + size]
            made = (self._next, np.lib.stride_tricks.sliding_window_view(usable, size)[::hop])
            self._next = last + 1
        needed = self._next * hop - self._buffer_start  # what the next frame starts with
        if needed > 0:
            self._buffer = self._buffer[needed:]
            self._buffer_start += needed
        return made


class _ChromaAnalysis:
    """One stretch's samples into the timeline: its notes and its onsets."""

    def __init__(self, timeline: ChromaTimeline, first_sample: int) -> None:
        self._timeline = timeline
        self._first_sample = first_sample
        self._received = 0
        self._notes = _Framer(timeline.frame, timeline.hop, first_sample)
        self._onsets = _Framer(timeline.onset_frame, timeline.onset_hop, first_sample)
        self._spectrum: NDArray[np.float64] | None = None  # the last onset frame's

    def put(self, samples: NDArray[np.float32]) -> None:
        timeline = self._timeline
        self._received += len(samples)
        timeline.end = max(
            timeline.end, (self._first_sample + self._received) / timeline.sample_rate
        )
        if (notes := self._notes.put(samples)) is not None:
            number, frames = notes
            timeline.put(number, *chroma_frames(frames, timeline.sample_rate))
        if (onsets := self._onsets.put(samples)) is not None:
            number, frames = onsets
            flux, self._spectrum = onset_strength(frames, self._spectrum)
            timeline.put_onsets(number, flux)


class ChromaTarget:
    """A :class:`~pyamp.audio.track_analysis.SampleTarget` filling a chroma timeline;
    ``stretch_done`` is told (on the analysis thread) when each stretch is over."""

    def __init__(
        self, timeline: ChromaTimeline, stretch_done: Callable[[ChromaTarget, bool], None]
    ) -> None:
        self.timeline = timeline
        self._stretch_done = stretch_done

    @property
    def cancelled(self) -> threading.Event:
        return self.timeline.cancelled

    @property
    def kept_from(self) -> float:
        return self.timeline.kept_from

    def begin(self, start: float, piece: bool) -> Callable[[NDArray[np.float32]], None]:
        return _ChromaAnalysis(self.timeline, round(start * self.timeline.sample_rate)).put

    def end(self, piece: bool) -> None:
        if not self.cancelled.is_set():
            self._stretch_done(self, piece)


class ChordTracker(QObject):
    """The playing track's chords, found from the feed's audio while :meth:`start`-ed.

    :attr:`changed` says the :attr:`chart` or :attr:`state` changed: "nothing" (no track
    the feed can analyse), "finding" (a file, or a song coming from a server), "listening"
    (a station: its chords as they come), "done" (all found), "failed" (it couldn't be
    analysed), or "idle" (stopped).
    """

    changed = Signal()
    _analysed = Signal(object, object, bool)  # from the analysis thread: target, chart, whole

    def __init__(
        self, feed: AudioFeed, cache: ChordCache | None = None, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._feed = feed
        self._cache = cache or ChordCache()
        self._target: ChromaTarget | None = None
        self._source: FeedSource | None = None
        self._running = False
        self.chart: ChordChart | None = None
        self.state: TrackerState = "idle"
        # Charts are worked out on a thread of their own, so the shared analysis (and the
        # visualiser's levels) never waits for them.
        self._charts = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pyamp-chords")
        self._wanted = 0  # the latest chart asked for: an older partial one is skipped
        # Queued: received here, on the thread this object lives on.
        self._analysed.connect(self._on_analysed)

    def start(self) -> None:
        if not self._running:
            self._running = True
            self._feed.join(self)

    def stop(self) -> None:
        if self._running:
            self._running = False
            self._feed.leave(self)
            self._cancel()
            self.chart = None
            self._set_state("idle")

    def shutdown(self) -> None:
        self.stop()
        self._charts.shutdown(wait=False, cancel_futures=True)

    # -- FeedConsumer ----------------------------------------------------------------------- #

    def follow(self, source: FeedSource | None) -> ChromaTarget | None:
        self._cancel()
        self._source = source
        self.chart = None
        if source is None:
            self._set_state("nothing")
            return None
        if not source.endless:  # a file, or a song from a server: perhaps known already
            known = self._cache.load(source.location)
            if known is not None:
                self.chart = known
                self._set_state("done")
                return None
        keep = LIVE_KEEP_S if source.endless else None
        self._target = ChromaTarget(ChromaTimeline(keep_seconds=keep), self._stretch_done)
        self._set_state("listening" if source.endless else "finding")
        return self._target

    # -- Analysis ----------------------------------------------------------------------------- #

    def _stretch_done(self, target: ChromaTarget, piece: bool) -> None:
        """Analysis thread: hand what's heard so far to the chords thread (a copy, so the
        analysis goes on meanwhile), saying whether the track is all analysed."""
        source = self._source
        timeline = target.timeline
        if not piece:
            whole = True  # a file: decoded (or failed)
        elif source is None or source.endless or not source.track.duration_ms:
            whole = False  # a station never ends
        else:
            whole = timeline.covered >= WHOLE_SONG * source.track.duration_ms / 1000
        self._wanted += 1
        with contextlib.suppress(RuntimeError):  # shut down meanwhile
            self._charts.submit(self._build, target, timeline.frames(), whole, self._wanted)

    def _build(self, target: ChromaTarget, frames: Frames, whole: bool, number: int) -> None:
        """Chords thread: the chart, unless a later one is on its way (a partial one)."""
        if target.cancelled.is_set() or (not whole and number != self._wanted):
            return
        try:
            chart = chart_from_frames(frames)
        except Exception:
            log.exception("Finding the chords failed")
            chart = ChordChart(())
        self._analysed.emit(target, chart, whole)

    @Slot(object, object, bool)
    def _on_analysed(self, target: ChromaTarget, chart: ChordChart, whole: bool) -> None:
        if target is not self._target or target.cancelled.is_set():
            return  # for a track that's gone
        source = self._source
        self.chart = chart if chart.segments else None
        if whole:
            self._target = None
            if source is not None and chart.segments:
                self._cache.save(source.location, chart)
            self._set_state("done" if chart.segments else "failed")
        else:
            self.changed.emit()  # more of it found

    def _cancel(self) -> None:
        if self._target is not None:
            self._target.cancelled.set()
            self._target = None

    def _set_state(self, state: TrackerState) -> None:
        self.state = state
        self.changed.emit()
