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

"""The oscilloscope's waveform: worked out live (Qt) and stored ahead (mpv)."""

from __future__ import annotations

import numpy as np

from pyamp.audio.track_analysis import FPS, LevelTimeline
from pyamp.core.events import AudioFrame, EventBus, SpectrumUpdated
from pyamp.dsp.spectrum import SpectrumService
from pyamp.dsp.waveform import WAVE_POINTS, waveform, waveforms

RATE = 44_100


def _tone(hz: float, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(round(RATE * seconds)) / RATE
    return (amplitude * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_a_slow_tone_keeps_its_shape_and_size() -> None:
    wave = waveform(_tone(60, 1 / 30))  # two cycles
    assert wave.shape == (WAVE_POINTS,)
    assert 0.4 < wave.max() <= 0.5 and -0.5 <= wave.min() < -0.4
    crossings = np.count_nonzero(np.diff(np.sign(wave)) != 0)
    assert 3 <= crossings <= 5


def test_what_is_too_fast_to_draw_is_smoothed_away() -> None:
    assert np.abs(waveform(_tone(15_000, 1 / 30))).max() < 0.1


def test_silence_and_short_blocks() -> None:
    assert not waveform(np.zeros(0, dtype=np.float32)).any()
    short = waveform(np.array([0.0, 1.0, 0.0], dtype=np.float32), points=5)
    assert short.tolist() == [0.0, 0.5, 1.0, 0.5, 0.0]
    assert waveform(np.full(500, 3.0, dtype=np.float32)).max() == 1.0  # clipped


def test_one_waveform_per_hop_like_one_at_a_time() -> None:
    samples = _tone(100, 0.2)
    hop = RATE // FPS
    count = len(samples) // hop
    rows = waveforms(samples, hop, count)
    assert rows.shape == (count, WAVE_POINTS)
    for n in range(count):
        np.testing.assert_allclose(rows[n], waveform(samples[n * hop : (n + 1) * hop]), atol=1e-6)
    assert waveforms(samples, hop, 0).shape == (0, WAVE_POINTS)


def test_the_timeline_keeps_waveforms_in_step_with_rows() -> None:
    timeline = LevelTimeline(keep_seconds=1.0)
    for n in range(4 * FPS):
        timeline.put(n, np.full(4, n, dtype=np.float32), np.full(WAVE_POINTS, n, np.float32))
    seconds = 3.5
    row, wave = timeline.row_at(seconds), timeline.wave_at(seconds)
    assert row is not None and wave is not None
    assert row[0] == wave[0] == int(seconds * FPS)
    assert timeline.wave_at((timeline.first_row + 0.5) / FPS) is not None  # kept alike
    timeline.put(4 * FPS + 2, np.zeros(4, np.float32))  # a gap, and a row with no waveform
    assert timeline.wave_at((4 * FPS + 1) / FPS) is None
    assert timeline.wave_at((4 * FPS + 2) / FPS) is None


def test_the_live_analysis_publishes_the_waveform() -> None:
    bus = EventBus()
    SpectrumService(bus)
    published: list[SpectrumUpdated] = []
    bus.subscribe(SpectrumUpdated, published.append)
    bus.publish(AudioFrame(_tone(60, 0.1), RATE))
    assert len(published[-1].waveform) == WAVE_POINTS
    assert max(published[-1].waveform) > 0.4
