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

Instead of tapping the playback, the file is analysed separately on a low-priority
worker thread: a hidden libmpv (:class:`~pyamp.audio.mpv_backend.MpvPcmDecoder`, nothing
to do with the one playing) decodes it to raw samples as fast as it can, and PyAmp's own
:class:`~pyamp.dsp.spectrum.SpectrumAnalyzer` turns them, a block at a time, into one row
of band levels per 1/30 s (a :class:`LevelTimeline`). :class:`AnalysedSpectrum` then
publishes the row for the current playback position, 30 times a second, as ordinary
``SpectrumUpdated`` events. (Qt's decoder did this before, at 4-6 times the CPU.)

A stream (a song from a server, e.g. Subsonic, or live radio) is analysed from the
backend's own copy, so it's never fetched twice: the backend saves what it has newly
buffered, a piece at a time (up to :data:`PIECE_MAX_S`), just ahead of playback, and each
piece's levels go in at its own place. A song's levels are all kept; a live stream never
ends, so only its last couple of minutes are.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

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
from pyamp.dsp.waveform import waveforms

log = logging.getLogger(__name__)

#: Rows per second of audio, and how often levels are published while playing.
FPS = 30
#: Files are decoded for analysis at this rate (mono), and read back this much at a time.
ANALYSIS_RATE = 44100
BLOCK_SAMPLES = ANALYSIS_RATE * 10
_HOP = ANALYSIS_RATE // FPS
#: How long the analysis waits for the decoder to write more.
_WAIT_FOR_SAMPLES_S = 0.02
#: After the first bars, samples are analysed at least this many at a time.
MIN_BATCH_SAMPLES = ANALYSIS_RATE
#: How often to look for a stream's first piece (it's also looked for as soon as the
#: stream starts to play).
STREAM_CHECK_MS = 500
#: How often to save and analyse a stream's newly buffered stretch, once its first piece
#: is under way. Stations send a few seconds ahead, so the levels are still ready before
#: they're heard; fewer, longer pieces cost less (each is a file and a decoder to start).
LIVE_PIECE_MS = 1500
#: The longest piece: the first bars come quickly, and a song buffered far ahead is
#: analysed a piece after another rather than in one long go.
PIECE_MAX_S = 30.0
#: How much of a live stream's levels to keep (it may play all day). A song's are all kept,
#: so seeking in it finds its bars.
LIVE_KEEP_S = 120.0
#: How far ahead of playback a live stream's pieces go: further, and analysing them would
#: push the levels being played out of the few minutes that are kept.
LIVE_AHEAD_S = 30.0
#: A station's buffer must be within this of the playback position, or its timing isn't
#: the player's and its bars would be out of step: then it isn't analysed.
LIVE_SLACK_S = 5.0
#: The shortest stretch of a stream worth saving and analysing.
LIVE_MIN_PIECE_S = 0.25
#: A position this far from where playback was heading is a seek: its bars are looked
#: for at once.
SEEK_S = 2.0


class StreamSaver(Protocol):
    """Saves what the backend has buffered of the playing stream (the player, for it)."""

    def buffered_range(self) -> tuple[float, float] | None: ...

    def save_stream(self, target: Path, start: float, end: float) -> bool: ...


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
        #: The waveform for each row (for the oscilloscope), kept in step with ``rows``.
        self.waves: list[NDArray[np.float32] | None] = []
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
        return self._at(self.rows, seconds)

    def wave_at(self, seconds: float) -> NDArray[np.float32] | None:
        return self._at(self.waves, seconds)

    def _at(
        self, values: list[NDArray[np.float32] | None], seconds: float
    ) -> NDArray[np.float32] | None:
        index = int(seconds * FPS) - self.first_row
        return values[index] if 0 <= index < len(values) else None

    def put(
        self,
        row_number: int,
        levels: NDArray[np.float32],
        wave: NDArray[np.float32] | None = None,
    ) -> None:
        """Set the row (and its waveform) for ``row_number`` (worker thread)."""
        index = row_number - self.first_row
        if index < 0:
            return  # older than what's kept
        rows, waves = self.rows, self.waves
        if index >= len(rows):
            rows.extend([None] * (index - len(rows)))  # a gap: no bars there
            waves.extend([None] * (index - len(waves)))
            # The waveform first: a reader that finds the row then finds its waveform too.
            waves.append(wave)
            rows.append(levels)
        else:
            waves[index] = wave
            rows[index] = levels
        if self._keep_rows is not None and len(rows) > self._keep_rows * 2:
            drop = len(rows) - self._keep_rows
            self.first_row += drop
            # New lists: a reader keeps the ones it has.
            self.waves = waves[drop:]
            self.rows = rows[drop:]


class PcmDecoder(Protocol):
    """Decodes a file to raw mono float32 samples in another file, writing as it goes, so
    they can be analysed while it's still decoding. Stops when ``cancelled()`` says so."""

    def decode(
        self, source: Path, target: Path, sample_rate: int, cancelled: Callable[[], bool]
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class _Job:
    path: Path
    timeline: LevelTimeline
    #: Where in the timeline the file's first row goes.
    first_row: int = 0
    #: A piece of a live stream: a temporary file, deleted once decoded.
    piece: bool = False


class _Worker(QObject):
    """Lives on the analysis thread: decodes and analyses one file (or piece) at a time.

    Each file is decoded to raw samples (in a scratch folder), then read back a block at a
    time and analysed a block at a time: little memory, and cancelling is noticed between
    blocks.
    """

    def __init__(self, band_count: int, decoder: PcmDecoder, scratch: Path) -> None:
        super().__init__()
        self._band_count = band_count
        self._decoder = decoder
        self._scratch = scratch
        self._queue: list[_Job] = []
        self._busy = False

    @Slot(object)
    def enqueue(self, job: _Job) -> None:
        self._queue.append(job)
        if self._busy:
            return  # the loop below takes it
        self._busy = True
        try:
            while self._queue:
                job = self._queue.pop(0)
                try:
                    if not job.timeline.cancelled.is_set():
                        self._analyse(job)
                except Exception:
                    log.exception("Analysing %s for the visualiser failed", job.path.name)
                finally:
                    self._done(job)
        finally:
            self._busy = False

    def _analyse(self, job: _Job) -> None:
        """Decode on a helper thread, and analyse the samples as they're written: the
        first bars are ready almost at once, not after the whole file is decoded."""
        timeline = job.timeline
        raw = self._scratch / "decoded.f32"
        _remove(raw)
        outcome: list[bool] = []

        def decode() -> None:
            outcome.append(
                self._decoder.decode(job.path, raw, ANALYSIS_RATE, timeline.cancelled.is_set)
            )

        decoding = threading.Thread(target=decode, name="pyamp-decode", daemon=True)
        decoding.start()
        if not job.piece or timeline.analyzer is None:
            timeline.analyzer = SpectrumAnalyzer(band_count=self._band_count)
        reader = _GrowingFile(raw, timeline.analyzer, timeline, job.first_row)
        try:
            while not timeline.cancelled.is_set():
                finished = not decoding.is_alive()
                if reader.read_more(finished=finished):
                    continue
                if finished:
                    break  # decoded, and all of it read
                time.sleep(_WAIT_FOR_SAMPLES_S)
            decoding.join()
        finally:
            reader.close()
            _remove(raw)
        if outcome and not outcome[0] and not reader.rows:
            log.info("Can't analyse %s for the visualiser", job.path.name)

    @staticmethod
    def _done(job: _Job) -> None:
        if job.piece:
            _remove(job.path)  # the stream goes on: its timeline isn't finished
        else:
            job.timeline.finished.set()


class _NoDecoder:
    """Without libmpv nothing can be analysed (the analysis is only for mpv anyway)."""

    def decode(
        self, source: Path, target: Path, sample_rate: int, cancelled: Callable[[], bool]
    ) -> bool:
        return False


def _default_decoder() -> PcmDecoder:
    try:
        from pyamp.audio.mpv_backend import MpvPcmDecoder  # noqa: PLC0415  (loads libmpv)
    except OSError as exc:
        log.info("No visualiser analysis without libmpv: %s", exc)
        return _NoDecoder()
    return MpvPcmDecoder()


class _GrowingFile:
    """Reads a file of float32 samples while it's still being written, and analyses each
    stretch into the timeline (worker thread)."""

    def __init__(
        self, path: Path, analyzer: SpectrumAnalyzer, timeline: LevelTimeline, row: int
    ) -> None:
        self._path = path
        self._analyzer = analyzer
        self._timeline = timeline
        self._row = row
        self._file: BinaryIO | None = None
        self._partial = b""  # bytes of a sample still being written
        self._leftover = np.zeros(0, dtype=np.float32)  # samples short of a whole row
        self.rows = 0

    def read_more(self, *, finished: bool) -> bool:
        """Analyse what has been written since; False if there wasn't enough yet.

        The first stretch is taken as soon as there's any (the first bars come at once);
        then a second's worth at a time (the analysis is far ahead of playback by then),
        or the rest once the decoder has ``finished``: fewer, bigger batches cost less.
        """
        if self._file is None:
            if not self._path.exists():
                return False
            self._file = self._path.open("rb")
        if self.rows and not finished:
            written = os.fstat(self._file.fileno()).st_size - self._file.tell()
            if written < MIN_BATCH_SAMPLES * 4:
                return False
        data = self._file.read(BLOCK_SAMPLES * 4)
        if not data:
            return False
        data = self._partial + data
        whole = len(data) - len(data) % 4
        self._partial = data[whole:]
        samples = np.concatenate([self._leftover, np.frombuffer(data[:whole], np.float32)])
        levels = self._analyzer.analyze_blocks(samples, ANALYSIS_RATE, _HOP)
        waves = waveforms(samples, _HOP, len(levels))
        self._leftover = samples[len(levels) * _HOP :]
        for values, wave in zip(levels, waves, strict=True):
            self._timeline.put(self._row, values, wave)
            self._row += 1
        self.rows += len(levels)
        return True

    def close(self) -> None:
        if self._file is not None:
            self._file.close()


class TrackAnalyser(QObject):
    """Analyses files on a low-priority background thread."""

    _enqueue = Signal(object)

    def __init__(
        self,
        band_count: int = 20,
        parent: QObject | None = None,
        *,
        decoder: PcmDecoder | None = None,
    ) -> None:
        """``decoder``: by default a hidden libmpv (the analysis is for the mpv backend)."""
        super().__init__(parent)
        if decoder is None:
            decoder = _default_decoder()
        self._scratch = Path(tempfile.mkdtemp(prefix="pyamp-analysis-"))
        self._thread = QThread()
        self._thread.setObjectName("pyamp-track-analysis")
        self._worker = _Worker(band_count, decoder, self._scratch)
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
        shutil.rmtree(self._scratch, ignore_errors=True)


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

    #: A copy (or None if it failed) of seconds ``start`` to ``end`` of the stream
    #: numbered ``number`` was saved: (number, copy, start, end).
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
        # A stream (a station or a song from a server) is analysed a piece at a time, as
        # it's buffered, into one timeline.
        self._streams = streams
        self._stream: StreamUrl | None = None
        self._stream_number = 0  # bumped per track: a piece saved for an older one is dropped
        self._endless = False  # a station: no length, and only its last minutes are kept
        self._pieces_timeline: LevelTimeline | None = None
        self._analysed: list[tuple[float, float]] = []  # the stretches saved, in order
        self._pieces = 0  # numbers the pieces' files
        self._heard = False  # whether the current track has started to play
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
        timeline = self._timeline
        seconds = self.position()
        row = timeline.row_at(seconds) if timeline is not None else None
        if timeline is None or row is None:  # no levels now (e.g. a stream still buffering)
            self._drop_bars()
            return
        levels = np.clip(row + self._offsets, 0.0, 1.0)
        wave = timeline.wave_at(seconds)
        self._bus.publish(
            SpectrumUpdated(
                tuple(float(v) for v in levels),
                tuple(float(v) for v in wave) if wave is not None else (),
            )
        )
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
            self._drop_pieces()
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
        self._forget_stream()
        if isinstance(location, StreamUrl) and track and self._streams:
            self._stream = location
            self._endless = not track.duration_ms  # radio has no length: it never ends
        self._analyse_current()
        self._watch_stream()
        self._position_ms, self._position_at = 0, self._clock()
        self._heard = False
        self._drop_bars()  # the next track's levels start from nothing

    def _analyse_current(self) -> None:
        if self._active and self._stream is not None:
            if self._pieces_timeline is None:
                keep = LIVE_KEEP_S if self._endless else None
                self._pieces_timeline = LevelTimeline(keep_seconds=keep)
            self._timeline = self._pieces_timeline  # filled in as pieces are saved
            return
        location = self._location if self._active else None
        self._timeline = self._analyser.analyse(location) if location is not None else None

    # -- Streams ---------------------------------------------------------------------------- #

    def _watch_stream(self) -> None:
        if self._active and self._stream is not None:
            # Soon at first, so the bars start quickly; then at LIVE_PIECE_MS (see
            # _check_stream).
            self._stream_check.start(STREAM_CHECK_MS)

    def _check_stream(self) -> None:
        """Save the stream's next stretch to analyse on a short-lived thread: writing it
        mustn't hold up the window."""
        streams = self._streams
        if self._saving or streams is None or self._stream is None:
            return
        piece = self._next_piece(streams)
        if piece is None:
            return
        start, end = piece
        self._stream_check.setInterval(LIVE_PIECE_MS)  # the bars are on their way
        self._saving = True
        number = self._stream_number
        if self._copies is None:
            self._copies = Path(tempfile.mkdtemp(prefix="pyamp-streams-"))
        self._pieces += 1
        target = self._copies / f"stream-{number}-{self._pieces}.mkv"

        def save() -> None:
            saved = streams.save_stream(target, start, end)
            self._stream_saved.emit(number, target if saved else None, start, end)

        threading.Thread(target=save, name="pyamp-stream-copy", daemon=True).start()

    def _next_piece(self, streams: StreamSaver) -> tuple[float, float] | None:
        """The stretch to save next: buffered but not yet analysed, from just before the
        playback position on (what's playing, then what's coming)."""
        buffered = streams.buffered_range()
        timeline = self._pieces_timeline
        if buffered is None or timeline is None:
            return None
        start, end = buffered
        position = self.position()
        if not start - LIVE_SLACK_S <= position <= end + LIVE_SLACK_S:
            # A song's times are its file's: it was sought where mpv hasn't fetched yet. A
            # station's buffer may run on another clock: its bars would be wrong.
            if self._endless and self._playing:
                log.info("The stream's timing doesn't match playback; no visualiser for it")
                self._forget_stream()
                self._timeline = None
            return None
        if timeline.first_row and position - 1 < timeline.first_row / FPS:
            # A station sought back past the levels it keeps: they start afresh there.
            self._drop_pieces()
            self._analyse_current()
        begin, gap_end = self._unanalysed(max(start, position - 1))
        end = min(end, gap_end, begin + PIECE_MAX_S)
        if self._endless:
            end = min(end, position + LIVE_AHEAD_S)
        return (begin, end) if end - begin >= LIVE_MIN_PIECE_S else None

    def _unanalysed(self, after: float) -> tuple[float, float]:
        """The first stretch from ``after`` on that no piece has covered: (start, end)."""
        begin = after
        for start, end in self._analysed:
            if start <= begin < end:
                begin = end
            elif begin < start:
                return begin, start
        return begin, float("inf")

    def _mark_analysed(self, start: float, end: float) -> None:
        spans = sorted([*self._analysed, (start, end)])
        merged = [spans[0]]
        for a, b in spans[1:]:
            last_a, last_b = merged[-1]
            if a <= last_b + 1e-3:  # touching or overlapping
                merged[-1] = (last_a, max(last_b, b))
            else:
                merged.append((a, b))
        self._analysed = merged

    def _on_stream_saved(self, number: int, copy: Path | None, start: float, end: float) -> None:
        self._saving = False
        timeline = self._pieces_timeline
        if number != self._stream_number or copy is None or timeline is None:
            _remove(copy)  # for an older track, or hidden meanwhile; failed: tried again
            return
        self._analyser.analyse_piece(copy, timeline, start)
        self._mark_analysed(start, end)
        if end - start >= PIECE_MAX_S - 1e-3:
            self._check_stream()  # more is buffered (a song): on to the next piece

    def _forget_stream(self) -> None:
        """Stop following the current stream (pieces saved for it meanwhile are dropped)."""
        self._stream_number += 1
        self._stream_check.stop()
        self._stream = None
        self._endless = False
        self._drop_pieces()

    def _drop_pieces(self) -> None:
        """Stop a stream's pieces being analysed; it starts afresh when needed."""
        if self._pieces_timeline is not None:
            self._pieces_timeline.cancelled.set()  # queued pieces are skipped (and deleted)
        self._pieces_timeline = None
        self._analysed = []

    def _on_state(self, event: PlaybackStateChanged) -> None:
        # Freeze the interpolated position at the moment the state changes.
        self._position_ms, self._position_at = round(self.position() * 1000), self._clock()
        self._playing = event.state is PlaybackState.PLAYING
        if self._playing and self._active:
            self._timer.start()
        else:
            self._timer.stop()  # SpectrumService already clears the bars

    def _on_position(self, event: PositionChanged) -> None:
        heading = self.position()
        self._position_ms, self._position_at = event.position_ms, self._clock()
        starts = event.position_ms > 0 and not self._heard
        seek = self._heard and abs(event.position_ms / 1000 - heading) > SEEK_S
        self._heard = self._heard or starts
        # A stream plays once it has buffered a little, and a seek lands where it may not
        # have been analysed: look now, rather than at the next check (up to
        # LIVE_PIECE_MS later), so the bars come with the sound.
        if (starts or seek) and self._active and self._stream is not None:
            self._check_stream()

    def _on_equalizer(self, event: EqualizerChanged) -> None:
        self._offsets = equalizer_offsets(event.equalizer, self._band_count)


def _remove(copy: Path | None) -> None:
    """Delete a saved stream. If it's still open (Windows), it goes with its folder at quit."""
    if copy is not None:
        with contextlib.suppress(OSError):
            copy.unlink(missing_ok=True)
