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
"""Visualiser levels for backends that don't expose the audio they play (mpv).

Instead of tapping the playback, the file is analysed separately: Qt's
``QAudioDecoder`` decodes it silently on a low-priority worker thread, much faster
than real time, and PyAmp's own :class:`~pyamp.dsp.spectrum.SpectrumAnalyzer`
turns it into one row of band levels per 1/30 s (a :class:`LevelTimeline`).
:class:`AnalysedSpectrum` then publishes the row for the current playback
position, 30 times a second, as ordinary ``SpectrumUpdated`` events.

A song streamed from a server (e.g. Subsonic) is analysed from the backend's own copy:
once the backend has all of it buffered, it saves that to a temporary file, so the song
is never fetched twice. A live stream (radio) never ends, so the backend saves what it
has newly buffered, a short piece at a time, just ahead of playback; each piece's levels
go in at its own place, and only the last couple of minutes are kept. The decoder only
ever opens local files: opening a URL would hold Python's interpreter lock while it
connects, freezing the window.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QLoggingCategory, QObject, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

from pyamp.audio.qt_backend import audio_buffer_to_mono
from pyamp.core.equalizer import BANDS_HZ, Equalizer
from pyamp.core.events import (
    EqualizerChanged,
    EventBus,
    PlaybackStateChanged,
    PositionChanged,
    SpectrumUpdated,
    TrackChanged,
)
from pyamp.core.models import PlaybackState, StreamUrl
from pyamp.dsp.spectrum import SpectrumAnalyzer

log = logging.getLogger(__name__)

#: Rows per second of audio, and how often levels are published while playing.
FPS = 30
#: How often to check whether a streamed song is wholly buffered yet, and to save the
#: newly buffered stretch of a live stream.
STREAM_CHECK_MS = 500
#: How much of a live stream's levels to keep (it may play all day).
LIVE_KEEP_S = 120.0
#: A live stream's buffer must be within this of the playback position, or its timing
#: isn't the player's and its bars would be out of step: then it isn't analysed.
LIVE_SLACK_S = 5.0
#: The shortest stretch of a live stream worth saving and analysing.
LIVE_MIN_PIECE_S = 0.25


class StreamSaver(Protocol):
    """Saves what the backend has buffered of the playing stream (the player, for it)."""

    def stream_is_buffered(self) -> bool: ...

    def stream_fits_buffer(self) -> bool: ...

    def buffered_range(self) -> tuple[float, float] | None: ...

    def save_stream(self, target: Path, start: float = 0.0, end: float | None = None) -> bool: ...


class LevelTimeline:
    """Band levels, one row per 1/FPS s of playback, filled in as analysis runs.

    A file's rows are appended in order; a live stream's arrive a piece at a time, each
    put at its own position, and only the last ``keep_seconds`` are kept. Rows are
    written by the worker thread and read by index, so no locking is needed: a reader
    at worst sees no row for a moment.
    """

    def __init__(self, path: Path | None = None, *, keep_seconds: float | None = None) -> None:
        self.path = path
        self.rows: list[NDArray[np.float32] | None] = []
        #: The row number of ``rows[0]`` (rows before it were dropped).
        self.first_row = 0
        self.finished = threading.Event()
        self.cancelled = threading.Event()
        self._keep_rows = round(keep_seconds * FPS) if keep_seconds else None
        #: The worker's analyser for a stream's pieces, so the bars run on smoothly.
        self.analyzer: SpectrumAnalyzer | None = None

    @property
    def seconds(self) -> float:
        """How far analysis has got (for a file: how much of it is done)."""
        return (self.first_row + len(self.rows)) / FPS

    def row_at(self, seconds: float) -> NDArray[np.float32] | None:
        index = int(seconds * FPS) - self.first_row
        rows = self.rows
        return rows[index] if 0 <= index < len(rows) else None

    def put(self, row_number: int, levels: NDArray[np.float32]) -> None:
        """Set the row for ``row_number`` (worker thread)."""
        index = row_number - self.first_row
        if index < 0:
            return  # older than what's kept
        if index >= len(self.rows):
            self.rows.extend([None] * (index - len(self.rows)))  # a gap: no bars there
            self.rows.append(levels)
        else:
            self.rows[index] = levels
        if self._keep_rows is not None and len(self.rows) > self._keep_rows * 2:
            drop = len(self.rows) - self._keep_rows
            self.first_row += drop
            self.rows = self.rows[drop:]  # a new list: a reader keeps the one it has


@dataclass(frozen=True, slots=True)
class _Job:
    path: Path
    timeline: LevelTimeline
    #: Where in the timeline the file's first row goes.
    first_row: int = 0
    #: A piece of a live stream: a temporary file, deleted once decoded.
    piece: bool = False


class _Worker(QObject):
    """Lives on the analysis thread: decodes one file (or piece) at a time."""

    def __init__(self, band_count: int) -> None:
        super().__init__()
        self._band_count = band_count
        self._queue: list[_Job] = []
        self._current: _Job | None = None
        self._decoder: QAudioDecoder | None = None
        self._row = 0

    @Slot(object)
    def enqueue(self, job: _Job) -> None:
        self._queue.append(job)
        if self._current is None:
            self._next()

    def _next(self) -> None:
        self._release()
        while self._queue and self._queue[0].timeline.cancelled.is_set():
            self._done(self._queue.pop(0))
        if not self._queue:
            return
        job = self._current = self._queue.pop(0)
        timeline = job.timeline
        if not job.piece or timeline.analyzer is None:
            timeline.analyzer = SpectrumAnalyzer(band_count=self._band_count)
        self._row = job.first_row
        self._pending = np.zeros(0, dtype=np.float32)
        decoder = self._decoder = QAudioDecoder(self)
        fmt = QAudioFormat()
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        fmt.setChannelCount(1)  # mono float: all the analyser needs
        decoder.setAudioFormat(fmt)
        # Ignore signals from a decoder that's no longer current (e.g. "finished" after
        # "error", or while it's being stopped), so no queued file is skipped.
        decoder.bufferReady.connect(lambda: self._on_buffer() if decoder is self._decoder else None)
        decoder.finished.connect(lambda: self._next() if decoder is self._decoder else None)
        decoder.error.connect(lambda e: self._on_error(e) if decoder is self._decoder else None)
        decoder.setSource(QUrl.fromLocalFile(str(job.path)))
        decoder.start()

    def _release(self) -> None:
        # Detach first: stopping a decoder can emit "finished" synchronously, and the
        # handlers ignore signals from a decoder that's no longer current.
        job, self._current = self._current, None
        decoder, self._decoder = self._decoder, None
        if decoder is not None:
            decoder.stop()
            decoder.deleteLater()
        if job is not None:
            self._done(job)

    @staticmethod
    def _done(job: _Job) -> None:
        if job.piece:
            _remove(job.path)  # the stream goes on: its timeline isn't finished
        else:
            job.timeline.finished.set()

    def _on_buffer(self) -> None:
        decoder, job = self._decoder, self._current
        if decoder is None or job is None:
            return
        buffer = decoder.read()
        timeline = job.timeline
        if timeline.cancelled.is_set():
            self._next()
            return
        samples = audio_buffer_to_mono(buffer)
        if samples is None or timeline.analyzer is None:
            return
        rate = buffer.format().sampleRate()
        hop = max(1, rate // FPS)
        self._pending = np.concatenate([self._pending, samples])
        while len(self._pending) >= hop:
            levels = timeline.analyzer.analyze(self._pending[:hop], rate)
            timeline.put(self._row, levels.astype(np.float32))
            self._row += 1
            self._pending = self._pending[hop:]

    def _on_error(self, _error: QAudioDecoder.Error) -> None:
        if self._decoder is not None and self._current is not None:
            log.info(
                "Can't analyse %s for the visualiser: %s",
                self._current.path.name,
                self._decoder.errorString(),
            )
        self._next()


class TrackAnalyser(QObject):
    """Analyses files on a low-priority background thread."""

    _enqueue = Signal(object)

    def __init__(self, band_count: int = 20, parent: QObject | None = None) -> None:
        super().__init__(parent)
        # Qt's decoder logs ffmpeg's file details (incl. the path) at info level.
        QLoggingCategory.setFilterRules(
            "qt.multimedia.ffmpeg*.info=false\nqt.multimedia.ffmpeg*.debug=false"
        )
        self._thread = QThread()
        self._thread.setObjectName("pyamp-track-analysis")
        self._worker = _Worker(band_count)
        self._worker.moveToThread(self._thread)
        self._enqueue.connect(self._worker.enqueue)
        self._thread.start(QThread.Priority.LowPriority)  # never compete with playback or the UI
        self._timelines: dict[Path, LevelTimeline] = {}

    def analyse(self, path: Path) -> LevelTimeline:
        """The timeline for ``path``; starts analysing it, cancelling any other file."""
        for other in list(self._timelines):
            if other != path:
                self._timelines.pop(other).cancelled.set()
        timeline = self._timelines.get(path)
        if timeline is None:
            timeline = self._timelines[path] = LevelTimeline(path)
            self._enqueue.emit(_Job(path, timeline))
        return timeline

    def analyse_piece(self, piece: Path, timeline: LevelTimeline, start: float) -> None:
        """Decode ``piece`` (a stretch of a live stream starting ``start`` seconds in) into
        ``timeline``, then delete it. Cancel by setting the timeline's ``cancelled``."""
        self._enqueue.emit(_Job(piece, timeline, round(start * FPS), piece=True))

    def cancel_all(self) -> None:
        """Stop analysing (whatever was being analysed starts afresh next time)."""
        for timeline in self._timelines.values():
            timeline.cancelled.set()
        self._timelines.clear()

    def shutdown(self) -> None:
        self.cancel_all()
        self._thread.quit()
        self._thread.wait(3000)


def equalizer_offsets(
    equalizer: Equalizer, band_count: int, floor_db: float = -70.0
) -> NDArray[np.float32]:
    """Per display band, how far the EQ moves its level (in the analyser's 0..1 scale).

    The display bands are log-spaced like the analyser's; the EQ's gain at each
    band's centre is interpolated (in log frequency) between the EQ's own bands.
    """
    if not equalizer.active:
        return np.zeros(band_count, dtype=np.float32)
    edges = np.geomspace(40.0, 16_000.0, band_count + 1)
    centres = np.sqrt(edges[:-1] * edges[1:])
    gains = np.interp(np.log(centres), np.log(BANDS_HZ), equalizer.gains_db) + equalizer.preamp_db
    return (gains / -floor_db).astype(np.float32)


class AnalysedSpectrum(QObject):
    """Publishes analysed levels for the current position while playing."""

    #: A copy (or None if it failed) of seconds ``start`` to ``end`` (-1: all of it) of the
    #: stream numbered ``number`` was saved: (number, copy, start, end).
    _stream_saved = Signal(int, object, float, float)

    def __init__(
        self,
        bus: EventBus,
        analyser: TrackAnalyser,
        band_count: int = 20,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
        *,
        streams: StreamSaver | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self._analyser = analyser
        self._band_count = band_count
        self._clock = clock
        self._timeline: LevelTimeline | None = None
        self._location: Path | None = None  # the current file, analysed while active
        # A streamed song (with a known length) is analysed from a saved copy.
        self._streams = streams
        self._stream: StreamUrl | None = None
        self._stream_number = 0  # bumped per track: a copy saved for an older one is dropped
        self._stream_copy: Path | None = None
        # A live stream (no length) is analysed a piece at a time into one timeline.
        self._live = False
        self._live_timeline: LevelTimeline | None = None
        self._live_to: float | None = None  # how far it's been saved
        self._pieces = 0  # numbers the pieces' files
        self._saving = False
        self._copies: Path | None = None  # a temporary folder, made when first needed
        self._stream_check = QTimer(self)
        self._stream_check.setInterval(STREAM_CHECK_MS)
        self._stream_check.timeout.connect(self._check_stream)
        self._stream_saved.connect(self._on_stream_saved)
        self._active = True
        self._playing = False
        self._position_ms = 0
        self._position_at = clock()
        self._offsets = np.zeros(band_count, dtype=np.float32)
        self._showing = False  # whether the bars show levels (not silence)
        self._timer = QTimer(self)
        self._timer.setInterval(round(1000 / FPS))
        self._timer.timeout.connect(self.tick)
        bus.subscribe(TrackChanged, self._on_track)
        bus.subscribe(PlaybackStateChanged, self._on_state)
        bus.subscribe(PositionChanged, self._on_position)
        bus.subscribe(EqualizerChanged, self._on_equalizer)

    @property
    def timeline(self) -> LevelTimeline | None:
        return self._timeline

    def position(self) -> float:
        """Playback position in seconds, interpolated between the backend's updates."""
        elapsed = self._clock() - self._position_at if self._playing else 0.0
        return self._position_ms / 1000 + elapsed

    def tick(self) -> None:
        if not self._playing:
            return
        row = self._timeline.row_at(self.position()) if self._timeline is not None else None
        if row is None:  # no levels for this moment (e.g. a stream still buffering)
            self._drop_bars()
            return
        levels = np.clip(row + self._offsets, 0.0, 1.0)
        self._bus.publish(SpectrumUpdated(tuple(float(v) for v in levels)))
        self._showing = True

    def _drop_bars(self) -> None:
        """Let the bars fall rather than freeze on the last levels (once, not every tick).

        The Qt backend's bars drop as it switches tracks (it stops in between); mpv goes
        straight on, so they're dropped here.
        """
        if self._showing:
            self._showing = False
            self._bus.publish(SpectrumUpdated((0.0,) * self._band_count))

    def set_active(self, active: bool) -> None:
        """Pause analysing and publishing (e.g. while nothing shows the visualiser).

        Decoding a whole file a second time is the costly part; it stops at once and
        restarts for the current file on resuming.
        """
        if active == self._active:
            return
        self._active = active
        if active:
            self._analyse_current()
            self._watch_stream()
            if self._playing:
                self._timer.start()
        else:
            self._timer.stop()
            self._stream_check.stop()
            self._analyser.cancel_all()
            self._drop_live_timeline()
            self._timeline = None

    def shutdown(self) -> None:
        self._timer.stop()
        self._stream_check.stop()
        self._stream_number += 1  # a copy still being saved is dropped
        if self._copies is not None:
            shutil.rmtree(self._copies, ignore_errors=True)

    def _on_track(self, event: TrackChanged) -> None:
        track = event.track
        location = track.location if track else None
        self._location = location if isinstance(location, Path) else None
        old_copy = self._forget_stream()
        if isinstance(location, StreamUrl) and track and self._streams:
            self._stream = location
            self._live = not track.duration_ms  # radio has no length: it never ends
        self._analyse_current()  # stops analysing the old copy...
        _remove(old_copy)  # ...before it's deleted
        self._watch_stream()
        self._position_ms, self._position_at = 0, self._clock()
        self._drop_bars()  # the next track's levels start from nothing

    def _analyse_current(self) -> None:
        if self._active and self._live:
            if self._live_timeline is None:
                self._live_timeline = LevelTimeline(keep_seconds=LIVE_KEEP_S)
            self._timeline = self._live_timeline  # filled in as pieces are saved
            return
        location = (self._location or self._stream_copy) if self._active else None
        self._timeline = self._analyser.analyse(location) if location is not None else None

    # -- Streamed songs -------------------------------------------------------------------- #

    def _watch_stream(self) -> None:
        if self._active and self._stream is not None and (self._live or not self._stream_copy):
            self._stream_check.start()

    def _check_stream(self) -> None:
        """Save a song once it's wholly buffered, or a live stream's newly buffered stretch,
        on a short-lived thread: writing it mustn't hold up the window."""
        streams = self._streams
        if self._saving or streams is None or self._stream is None:
            return
        end: float | None
        if self._live:
            piece = self._next_piece(streams)
            if piece is None:
                return
            start, end = piece
        elif streams.stream_is_buffered():
            self._stream_check.stop()
            start, end = 0.0, None
        elif not streams.stream_fits_buffer():
            # Too big to be buffered whole (e.g. an hour-long mix): a stretch at a time.
            log.debug("The stream won't fit the buffer whole; analysing it as it plays")
            self._live = True
            self._analyse_current()
            return
        else:
            return  # not yet; check again shortly
        self._saving = True
        number = self._stream_number
        if self._copies is None:
            self._copies = Path(tempfile.mkdtemp(prefix="pyamp-streams-"))
        self._pieces += 1
        target = self._copies / f"stream-{number}-{self._pieces}.mkv"

        def save() -> None:
            saved = streams.save_stream(target, start, end)
            self._stream_saved.emit(number, target if saved else None, start, end or -1.0)

        threading.Thread(target=save, name="pyamp-stream-copy", daemon=True).start()

    def _next_piece(self, streams: StreamSaver) -> tuple[float, float] | None:
        """The live stream's stretch to save next: what's buffered beyond the last piece."""
        buffered = streams.buffered_range()
        if buffered is None:
            return None
        start, end = buffered
        position = self.position()
        if not start - LIVE_SLACK_S <= position <= end + LIVE_SLACK_S:
            if self._playing:  # the buffer runs on another clock: its bars would be wrong
                log.info("The stream's timing doesn't match playback; no visualiser for it")
                self._forget_stream()
                self._timeline = None
            return None
        if self._live_to is not None and not start <= self._live_to <= end:
            # A seek: playback is in another stretch now. Its levels start afresh there
            # (the old ones may be far away, and only the last few minutes are kept).
            self._drop_live_timeline()
            self._analyse_current()
        begin = max(start, self._live_to if self._live_to is not None else position - 1)
        return (begin, end) if end - begin >= LIVE_MIN_PIECE_S else None

    def _on_stream_saved(self, number: int, copy: Path | None, start: float, end: float) -> None:
        self._saving = False
        if number != self._stream_number:  # the track changed meanwhile
            _remove(copy)
            self._watch_stream()  # the new track may be a stream too
            return
        if self._live:
            if copy is not None and self._live_timeline is not None:
                self._analyser.analyse_piece(copy, self._live_timeline, start)
                self._live_to = end
            else:
                _remove(copy)  # e.g. hidden meanwhile; the next check tries again
            return
        if copy is None:
            log.info("Can't save the stream for the visualiser")
            return
        self._stream_copy = copy
        self._analyse_current()

    def _forget_stream(self) -> Path | None:
        """Stop following the current stream; returns its copy, for the caller to delete."""
        self._stream_number += 1
        self._stream_check.stop()
        self._stream = None
        self._live = False
        self._drop_live_timeline()
        copy, self._stream_copy = self._stream_copy, None
        return copy

    def _drop_live_timeline(self) -> None:
        """Stop a live stream's pieces being analysed; it starts afresh when needed."""
        if self._live_timeline is not None:
            self._live_timeline.cancelled.set()  # queued pieces are skipped (and deleted)
        self._live_timeline = None
        self._live_to = None

    def _on_state(self, event: PlaybackStateChanged) -> None:
        # Freeze the interpolated position at the moment the state changes.
        self._position_ms, self._position_at = round(self.position() * 1000), self._clock()
        self._playing = event.state is PlaybackState.PLAYING
        if self._playing and self._active:
            self._timer.start()
        else:
            self._timer.stop()  # SpectrumService already clears the bars

    def _on_position(self, event: PositionChanged) -> None:
        self._position_ms, self._position_at = event.position_ms, self._clock()

    def _on_equalizer(self, event: EqualizerChanged) -> None:
        self._offsets = equalizer_offsets(event.equalizer, self._band_count)


def _remove(copy: Path | None) -> None:
    """Delete a saved stream. If it's still open (Windows), it goes with its folder at quit."""
    if copy is not None:
        with contextlib.suppress(OSError):
            copy.unlink(missing_ok=True)
