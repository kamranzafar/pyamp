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
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

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
from pyamp.core.models import PlaybackState
from pyamp.dsp.spectrum import SpectrumAnalyzer

log = logging.getLogger(__name__)

#: Rows per second of audio, and how often levels are published while playing.
FPS = 30


class LevelTimeline:
    """Band levels for one file, one row per 1/FPS s, filled in as analysis runs.

    Rows are only ever appended (by the worker thread) and read by index, so no
    locking is needed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.rows: list[NDArray[np.float32]] = []
        self.finished = threading.Event()
        self.cancelled = threading.Event()

    @property
    def seconds(self) -> float:
        """How much of the file has been analysed so far."""
        return len(self.rows) / FPS

    def row_at(self, seconds: float) -> NDArray[np.float32] | None:
        index = int(seconds * FPS)
        return self.rows[index] if 0 <= index < len(self.rows) else None


class _Worker(QObject):
    """Lives on the analysis thread: decodes one file at a time into its timeline."""

    def __init__(self, band_count: int) -> None:
        super().__init__()
        self._band_count = band_count
        self._queue: list[LevelTimeline] = []
        self._current: LevelTimeline | None = None
        self._decoder: QAudioDecoder | None = None

    @Slot(object)
    def enqueue(self, timeline: LevelTimeline) -> None:
        self._queue.append(timeline)
        if self._current is None:
            self._next()

    def _next(self) -> None:
        self._release()
        while self._queue and self._queue[0].cancelled.is_set():
            self._queue.pop(0).finished.set()
        if not self._queue:
            return
        timeline = self._current = self._queue.pop(0)
        self._analyzer = SpectrumAnalyzer(band_count=self._band_count)
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
        decoder.setSource(QUrl.fromLocalFile(str(timeline.path)))
        decoder.start()

    def _release(self) -> None:
        # Detach first: stopping a decoder can emit "finished" synchronously, and the
        # handlers ignore signals from a decoder that's no longer current.
        timeline, self._current = self._current, None
        decoder, self._decoder = self._decoder, None
        if timeline is not None:
            timeline.finished.set()
        if decoder is not None:
            decoder.stop()
            decoder.deleteLater()

    def _on_buffer(self) -> None:
        decoder, timeline = self._decoder, self._current
        if decoder is None or timeline is None:
            return
        buffer = decoder.read()
        if timeline.cancelled.is_set():
            self._next()
            return
        samples = audio_buffer_to_mono(buffer)
        if samples is None:
            return
        rate = buffer.format().sampleRate()
        hop = max(1, rate // FPS)
        self._pending = np.concatenate([self._pending, samples])
        while len(self._pending) >= hop:
            levels = self._analyzer.analyze(self._pending[:hop], rate)
            timeline.rows.append(levels.astype(np.float32))
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
            self._enqueue.emit(timeline)
        return timeline

    def shutdown(self) -> None:
        for timeline in self._timelines.values():
            timeline.cancelled.set()
        self._timelines.clear()
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

    def __init__(
        self,
        bus: EventBus,
        analyser: TrackAnalyser,
        band_count: int = 20,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._bus = bus
        self._analyser = analyser
        self._band_count = band_count
        self._clock = clock
        self._timeline: LevelTimeline | None = None
        self._playing = False
        self._position_ms = 0
        self._position_at = clock()
        self._offsets = np.zeros(band_count, dtype=np.float32)
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
        if self._timeline is None or not self._playing:
            return
        row = self._timeline.row_at(self.position())
        if row is not None:
            levels = np.clip(row + self._offsets, 0.0, 1.0)
            self._bus.publish(SpectrumUpdated(tuple(float(v) for v in levels)))

    def shutdown(self) -> None:
        self._timer.stop()

    def _on_track(self, event: TrackChanged) -> None:
        self._timeline = self._analyser.analyse(event.track.path) if event.track else None
        self._position_ms, self._position_at = 0, self._clock()

    def _on_state(self, event: PlaybackStateChanged) -> None:
        # Freeze the interpolated position at the moment the state changes.
        self._position_ms, self._position_at = round(self.position() * 1000), self._clock()
        self._playing = event.state is PlaybackState.PLAYING
        if self._playing:
            self._timer.start()
        else:
            self._timer.stop()  # SpectrumService already clears the bars

    def _on_position(self, event: PositionChanged) -> None:
        self._position_ms, self._position_at = event.position_ms, self._clock()

    def _on_equalizer(self, event: EqualizerChanged) -> None:
        self._offsets = equalizer_offsets(event.equalizer, self._band_count)
