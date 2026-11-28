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

"""Finds a song's beats, so its chords can change on them.

1. **Onsets**: how much louder the spectrum gets from one short frame to the next (its
   "flux"), about 86 times a second: notes and drum hits stand out.
2. **Tempo**: the beat period at which the onsets repeat best (autocorrelation), between
   200 and 60 beats a minute, leaning towards 120.
3. **Beats**: the run of onsets, about a period apart, that's strongest overall (Ellis's
   dynamic programming, as most beat trackers do), so the beats follow a tempo that
   drifts a little.

No clear beat (ambient, a solo instrument played freely, a few seconds only) finds no beats.
"""

from __future__ import annotations

import functools
from typing import Any

import numpy as np
from numpy.typing import NDArray

#: Samples per onset frame and between them, at 44.1 kHz (scaled at other rates).
ONSET_FRAME = 1024
ONSET_HOP = 512
_ONSET_RATE = 44100
#: The tempos looked for, as beat periods (seconds), and the one most music is near.
SHORTEST_BEAT_S = 0.3
LONGEST_BEAT_S = 1.0
USUAL_BEAT_S = 0.5
#: How strongly the onsets must repeat at the tempo found (0..1) for it to be a beat.
MIN_PERIODICITY = 0.2
#: How far each onset is spread for finding the tempo.
SPREAD_S = 0.03
#: How firmly the beats keep to the tempo (higher: less drift between them).
TIGHTNESS = 100.0
#: The least audio to look for beats in.
MIN_SECONDS = 4.0


def onset_sizes(sample_rate: int) -> tuple[int, int]:
    scale = max(1, round(sample_rate / _ONSET_RATE))
    return ONSET_FRAME * scale, ONSET_HOP * scale


@functools.cache
def _window(size: int) -> NDArray[np.float64]:
    return np.hanning(size)


def onset_strength(
    frames: NDArray[Any], previous: NDArray[np.float64] | None
) -> tuple[NDArray[np.float32], NDArray[np.float64]]:
    """Each onset frame's flux (one frame per row; the first against ``previous``, the
    last frame's spectrum before, or nothing), and this block's last spectrum."""
    block = np.asarray(frames, dtype=np.float64)
    spectrum = np.log1p(10 * np.abs(np.fft.rfft(block * _window(block.shape[1]), axis=1)))
    before = np.vstack([previous if previous is not None else spectrum[:1], spectrum[:-1]])
    flux = np.maximum(spectrum - before, 0.0).mean(axis=1)
    return flux.astype(np.float32), spectrum[-1]


def _smoothed(envelope: NDArray[np.float64], hop_s: float) -> NDArray[np.float64]:
    """Onsets spread a little (by :data:`SPREAD_S`), so a tempo that drifts still lines up."""
    width = max(1, round(SPREAD_S / hop_s))
    kernel = np.exp(-0.5 * (np.arange(-3 * width, 3 * width + 1) / width) ** 2)
    return np.convolve(envelope, kernel / kernel.sum(), mode="same")


def tempo(onsets: NDArray[Any], hop_s: float) -> tuple[int, float]:
    """The beat period (in onset frames) and how strongly the onsets repeat at it (0..1)."""
    envelope = _smoothed(np.asarray(onsets, dtype=np.float64), hop_s)
    envelope = envelope - envelope.mean()
    size = 1 << int(np.ceil(np.log2(2 * len(envelope))))
    spectrum = np.fft.rfft(envelope, size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum))[: len(envelope)]
    if correlation[0] <= 0:
        return 0, 0.0
    shortest = max(1, round(SHORTEST_BEAT_S / hop_s))
    longest = min(len(envelope) - 1, round(LONGEST_BEAT_S / hop_s))
    if longest <= shortest:
        return 0, 0.0
    lags = np.arange(shortest, longest + 1)
    leaning = np.exp(-0.5 * np.log2(lags * hop_s / USUAL_BEAT_S) ** 2)
    best = int(lags[np.argmax(correlation[lags] * leaning)])
    return best, float(correlation[best] / correlation[0])


def track_beats(onsets: NDArray[Any], hop_s: float) -> NDArray[np.intp]:
    """The onset frames the beats fall on; none if there's no clear beat."""
    if len(onsets) * hop_s < MIN_SECONDS:
        return np.zeros(0, dtype=np.intp)
    period, strength = tempo(onsets, hop_s)
    if period == 0 or strength < MIN_PERIODICITY:
        return np.zeros(0, dtype=np.intp)
    envelope = np.asarray(onsets, dtype=np.float64)
    envelope = envelope / (envelope.std() or 1.0)
    count = len(envelope)
    # A beat's predecessor is between half a period and two periods before it, best one
    # period before. So the frames of each stretch half a period long depend only on the
    # frames before that stretch: a stretch is worked out at once.
    nearest = max(1, period // 2)
    lags = np.arange(nearest, 2 * period + 1)
    penalty = -TIGHTNESS * np.log(lags / period) ** 2
    score = envelope.copy()
    back = np.full(count, -1, dtype=np.intp)
    for start in range(nearest, count, nearest):
        frames = np.arange(start, min(start + nearest, count))
        before = frames[:, None] - lags[None, :]
        candidates = np.where(before >= 0, score[np.maximum(before, 0)] + penalty, -np.inf)
        best = candidates.argmax(axis=1)
        value = candidates[np.arange(len(frames)), best]
        linked = value > 0  # a run of beats only goes on while it gains
        score[frames] = envelope[frames] + np.where(linked, value, 0.0)
        back[frames] = np.where(linked, before[np.arange(len(frames)), best], -1)
    last = count - period + int(np.argmax(score[count - period :]))
    beats = [last]
    while back[beats[-1]] >= 0:
        beats.append(int(back[beats[-1]]))
    return np.array(beats[::-1], dtype=np.intp)
