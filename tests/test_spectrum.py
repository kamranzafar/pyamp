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
