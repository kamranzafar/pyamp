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

from __future__ import annotations

import numpy as np

from pyamp.core.events import AudioFrame, EventBus, SpectrumUpdated
from pyamp.dsp.spectrum import SpectrumAnalyzer, SpectrumService

RATE = 44_100


def sine(freq: float, n: int = 4096) -> np.ndarray:
    t = np.arange(n) / RATE
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def test_silence_is_zero() -> None:
    levels = SpectrumAnalyzer().analyze(np.zeros(4096, dtype=np.float32), RATE)
    assert levels.max() == 0.0


def test_tone_peaks_in_matching_band() -> None:
    analyzer = SpectrumAnalyzer(band_count=20)
    low = analyzer.analyze(sine(100), RATE)
    analyzer.reset()
    high = analyzer.analyze(sine(8000), RATE)
    assert int(np.argmax(low)) < int(np.argmax(high))
    assert low.max() > 0.9  # full-scale sine reads near 0 dB


def test_small_frames_accumulate() -> None:
    analyzer = SpectrumAnalyzer()
    signal = sine(1000)
    for chunk in np.split(signal, 16):
        levels = analyzer.analyze(chunk, RATE)
    assert levels.max() > 0.9


def test_service_publishes_levels(bus: EventBus) -> None:
    updates: list[SpectrumUpdated] = []
    bus.subscribe(SpectrumUpdated, updates.append)
    service = SpectrumService(bus, SpectrumAnalyzer(band_count=8))
    bus.publish(AudioFrame(sine(440), RATE))
    assert len(updates) == 1
    assert len(updates[0].levels) == service.band_count == 8


def test_service_pauses_while_inactive(bus: EventBus) -> None:
    service = SpectrumService(bus, SpectrumAnalyzer(band_count=8, fft_size=256))
    published: list[SpectrumUpdated] = []
    bus.subscribe(SpectrumUpdated, published.append)
    loud = np.sin(np.arange(256) / 3).astype(np.float32)
    service.set_active(False)
    assert published == [SpectrumUpdated((0.0,) * 8)]  # bars drop at once
    bus.publish(AudioFrame(loud, 44_100))
    assert len(published) == 1  # no analysis while nobody can see it
    service.set_active(True)
    bus.publish(AudioFrame(loud, 44_100))
    assert any(published[-1].levels)


def test_blocks_give_the_same_rows_as_one_at_a_time() -> None:
    """The batch path (for whole songs) must draw exactly the bars the frame path does."""
    rng = np.random.default_rng(3)
    rate, hop = 44100, 44100 // 30
    t = np.arange(rate * 3) / rate
    audio = (np.sin(2 * np.pi * 440 * t) * 0.3 + rng.normal(0, 0.05, t.size)).astype(np.float32)
    one, batch = SpectrumAnalyzer(), SpectrumAnalyzer()
    expected = np.array(
        [one.analyze(audio[i : i + hop], rate) for i in range(0, len(audio) - hop + 1, hop)]
    )
    first = batch.analyze_blocks(audio[: hop * 40 + 100], rate, hop)  # in two goes, a partial
    rest = batch.analyze_blocks(audio[hop * 40 :], rate, hop)  # hop left out of the first
    assert len(first) == 40
    np.testing.assert_allclose(np.concatenate([first, rest]), expected, atol=1e-5)


def test_blocks_of_nothing() -> None:
    assert SpectrumAnalyzer().analyze_blocks(np.zeros(10, dtype=np.float32), 44100, 1470).shape == (
        0,
        20,
    )
