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

"""FFT spectrum analyser.

:class:`SpectrumAnalyzer` is pure maths and has no Qt or event-bus knowledge.
:class:`SpectrumService` connects it to the event bus. It turns
:class:`AudioFrame` events into :class:`SpectrumUpdated` events.

Visual dynamics (bar fall-off, peak caps) are presentation concerns and live
in the widget, not here.
"""

from __future__ import annotations

from functools import cache
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray

from pyamp.core.events import AudioFrame, EventBus, PlaybackStateChanged, SpectrumUpdated
from pyamp.core.models import PlaybackState


class SpectrumAnalyzer:
    """Maps a block of PCM samples to ``band_count`` log-spaced levels in [0, 1]."""

    def __init__(
        self,
        band_count: int = 20,
        fft_size: int = 2048,
        min_hz: float = 40.0,
        max_hz: float = 16_000.0,
        floor_db: float = -70.0,
    ) -> None:
        if fft_size & (fft_size - 1):
            raise ValueError("fft_size must be a power of two")
        self.band_count = band_count
        self.fft_size = fft_size
        self.min_hz = min_hz
        self.max_hz = max_hz
        self.floor_db = floor_db
        self._window = np.hanning(fft_size).astype(np.float32)
        # Scale so a full-scale sine wave reads ~0 dB.
        self._norm = 2.0 / float(self._window.sum())
        self._history = np.zeros(fft_size, dtype=np.float32)

    def analyze(self, samples: NDArray[np.float32], sample_rate: int) -> NDArray[np.float64]:
        """Analyse the most recent ``fft_size`` samples seen so far."""
        self._push(samples)
        magnitudes = np.abs(np.fft.rfft(self._history * self._window)) * self._norm
        levels = np.empty(self.band_count)
        for band, (lo, hi) in enumerate(self._band_bins(sample_rate)):
            levels[band] = magnitudes[lo:hi].max()
        db = 20.0 * np.log10(levels + 1e-12)
        result: NDArray[np.float64] = np.clip((db - self.floor_db) / -self.floor_db, 0.0, 1.0)
        return result

    def reset(self) -> None:
        self._history.fill(0.0)

    def _push(self, samples: NDArray[np.float32]) -> None:
        """Keep a rolling window of the last ``fft_size`` samples."""
        n = len(samples)
        if n >= self.fft_size:
            self._history[:] = samples[-self.fft_size :]
        elif n:
            self._history = np.roll(self._history, -n)
            self._history[-n:] = samples

    def _band_bins(self, sample_rate: int) -> tuple[tuple[int, int], ...]:
        return _log_band_bins(self.band_count, self.fft_size, sample_rate, self.min_hz, self.max_hz)


@cache
def _log_band_bins(
    band_count: int, fft_size: int, sample_rate: int, min_hz: float, max_hz: float
) -> tuple[tuple[int, int], ...]:
    """FFT bin ranges ``[lo, hi)`` for logarithmically spaced bands.

    Every band gets at least one bin, so narrow low-frequency bands still show
    something with small FFT sizes.
    """
    nyquist = sample_rate / 2
    edges = np.geomspace(min_hz, min(max_hz, nyquist), band_count + 1)
    bins = np.round(edges / nyquist * (fft_size // 2)).astype(int)
    ranges = []
    for lo, hi in pairwise(bins):
        ranges.append((int(lo), int(max(hi, lo + 1))))
    return tuple(ranges)


class SpectrumService:
    """Bus adapter: feeds audio frames to the analyser and publishes the result."""

    def __init__(self, bus: EventBus, analyzer: SpectrumAnalyzer | None = None) -> None:
        self._bus = bus
        self._analyzer = analyzer or SpectrumAnalyzer()
        bus.subscribe(AudioFrame, self._on_frame)
        bus.subscribe(PlaybackStateChanged, self._on_state)

    @property
    def band_count(self) -> int:
        return self._analyzer.band_count

    def _on_frame(self, event: AudioFrame) -> None:
        levels = self._analyzer.analyze(event.samples, event.sample_rate)
        self._bus.publish(SpectrumUpdated(tuple(levels.tolist())))

    def _on_state(self, event: PlaybackStateChanged) -> None:
        if event.state is not PlaybackState.PLAYING:
            self._analyzer.reset()
            self._bus.publish(SpectrumUpdated((0.0,) * self._analyzer.band_count))
