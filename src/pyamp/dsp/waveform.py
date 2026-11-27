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

"""The waveform for the oscilloscope: a stretch of samples boiled down to a few points.

Each point is the mean of its share of the samples, which smooths away what's too fast
to see at this size (so it doesn't alias into noise).
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

#: Points per waveform: enough for a smooth curve across the visualiser.
WAVE_POINTS = 96
#: How much sound one waveform shows (about one frame of the visualiser).
WAVE_SECONDS = 1 / 30


def waveform(samples: NDArray[np.float32], points: int = WAVE_POINTS) -> NDArray[np.float32]:
    """``points`` values in [-1, 1] tracing ``samples`` from first to last."""
    if len(samples) == 0:
        return np.zeros(points, dtype=np.float32)
    if len(samples) < points:  # too few to average: stretch them
        where = np.linspace(0, len(samples) - 1, points)
        stretched = np.interp(where, np.arange(len(samples)), samples)
        return np.clip(stretched, -1.0, 1.0).astype(np.float32)
    per_point = len(samples) // points
    chunks = samples[: per_point * points].reshape(points, per_point)
    return np.clip(chunks.mean(axis=1), -1.0, 1.0).astype(np.float32)


def waveforms(
    samples: NDArray[np.float32], hop: int, count: int, points: int = WAVE_POINTS
) -> NDArray[np.float32]:
    """One waveform per ``hop`` of ``samples``, for the first ``count`` hops."""
    if count == 0:
        return np.zeros((0, points), dtype=np.float32)
    per_point = max(hop // points, 1)
    blocks = samples[: count * hop].reshape(count, hop)
    if hop < points:
        return np.stack([waveform(block, points) for block in blocks])
    chunks = blocks[:, : per_point * points].reshape(count, points, per_point)
    return np.clip(chunks.mean(axis=2), -1.0, 1.0).astype(np.float32)
