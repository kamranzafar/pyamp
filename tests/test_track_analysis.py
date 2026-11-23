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
from __future__ import annotations

import argparse
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.app import Application
from pyamp.audio import registry
from pyamp.audio.track_analysis import (
    FPS,
    AnalysedSpectrum,
    LevelTimeline,
    TrackAnalyser,
    equalizer_offsets,
)
from pyamp.config import Settings, SettingsStore
from pyamp.core.equalizer import Equalizer
from pyamp.core.events import (
    EqualizerChanged,
    EventBus,
    PlaybackStateChanged,
    PositionChanged,
    SpectrumUpdated,
    TrackChanged,
)
from pyamp.core.models import PlaybackState, Track
from pyamp.library.saved_playlists import SavedPlaylists
from tests.conftest import FakeBackend

BANDS = 20


def wait_until(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        QCoreApplication.processEvents()
        time.sleep(0.005)


def write_tones(path: Path, freqs: tuple[int, ...], seconds: float = 1.0) -> Path:
    rate = 44100
    t = np.arange(int(rate * seconds)) / rate
    audio = np.concatenate([np.sin(2 * np.pi * f * t) * 12000 for f in freqs]).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio.tobytes())
    return path


def band_of(hz: float) -> int:
    edges = np.geomspace(40.0, 16_000.0, BANDS + 1)
    return int(np.searchsorted(edges, hz) - 1)


# -- Timeline and EQ mapping (pure) ----------------------------------------------------- #


def test_timeline_lookup_by_time() -> None:
    timeline = LevelTimeline(Path("/m/a.mp3"))
    timeline.rows.extend(np.full(BANDS, i, dtype=np.float32) for i in range(FPS * 2))
    assert timeline.seconds == 2.0
    row = timeline.row_at(1.5)
    assert row is not None
    assert row[0] == int(1.5 * FPS)
    assert timeline.row_at(2.0) is None  # not analysed (yet)
    assert timeline.row_at(-1) is None


def test_equalizer_offsets_follow_the_curve() -> None:
    assert not equalizer_offsets(Equalizer(), BANDS).any()  # off: no change
    assert not equalizer_offsets(Equalizer(enabled=True), BANDS).any()  # flat: no change
    bass = equalizer_offsets(Equalizer(enabled=True).with_preset("Bass Boost"), BANDS)
    assert bass[0] == pytest.approx(7 / 70, abs=0.01)  # +7 dB at the bottom, on a 70 dB scale
    assert bass[-1] == pytest.approx(0.0, abs=1e-6)
    assert all(np.diff(bass[:10]) <= 1e-6)  # falls off towards the mids
    preamp = equalizer_offsets(Equalizer(enabled=True, preamp_db=-7), BANDS)
    assert preamp == pytest.approx(np.full(BANDS, -0.1))


# -- Real decoding ------------------------------------------------------------------------ #


@pytest.fixture
def analyser(qt_app: QCoreApplication) -> Iterator[TrackAnalyser]:
    analyser = TrackAnalyser(BANDS)
    yield analyser
    analyser.shutdown()


def test_analysis_finds_the_tones_at_the_right_times(
    analyser: TrackAnalyser, tmp_path: Path
) -> None:
    timeline = analyser.analyse(write_tones(tmp_path / "tones.wav", (100, 1000, 8000)))
    wait_until(timeline.finished.is_set)
    assert abs(len(timeline.rows) - 3 * FPS) <= 2
    for seconds, hz in ((0.5, 100), (1.5, 1000), (2.5, 8000)):
        row = timeline.row_at(seconds)
        assert row is not None
        assert abs(int(np.argmax(row)) - band_of(hz)) <= 1, (seconds, hz, row.round(2))


def test_analysing_another_file_cancels_the_first(analyser: TrackAnalyser, tmp_path: Path) -> None:
    first = analyser.analyse(write_tones(tmp_path / "a.wav", (440,), seconds=30))
    second = analyser.analyse(write_tones(tmp_path / "b.wav", (440,), seconds=1))
    assert first.cancelled.is_set()
    wait_until(lambda: first.finished.is_set() and second.finished.is_set())
    assert len(first.rows) < 30 * FPS
    assert abs(len(second.rows) - FPS) <= 2
    assert analyser.analyse(tmp_path / "b.wav") is second  # reused, not redone


def test_unreadable_file_finishes_empty(analyser: TrackAnalyser, tmp_path: Path) -> None:
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"not audio" * 100)
    timeline = analyser.analyse(broken)
    wait_until(timeline.finished.is_set)
    assert timeline.rows == []


def test_decoding_doesnt_print_file_details(
    analyser: TrackAnalyser, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    timeline = analyser.analyse(write_tones(tmp_path / "quiet.wav", (440,)))
    wait_until(timeline.finished.is_set)
    assert "Input #0" not in capfd.readouterr().err


# -- Publishing while playing --------------------------------------------------------------- #


class FakeAnalyser:
    def __init__(self, timeline: LevelTimeline) -> None:
        self.timeline = timeline
        self.requested: list[Path] = []

    def analyse(self, path: Path) -> LevelTimeline:
        self.requested.append(path)
        return self.timeline


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def ramp() -> LevelTimeline:
    """Row i has every band at level i/100, for 3 seconds."""
    timeline = LevelTimeline(Path("/m/song.mp3"))
    timeline.rows.extend(np.full(BANDS, i / 100, dtype=np.float32) for i in range(3 * FPS))
    return timeline


@pytest.fixture
def spectrum(
    qt_app: QCoreApplication, bus: EventBus, ramp: LevelTimeline
) -> Iterator[tuple[AnalysedSpectrum, Clock, list[SpectrumUpdated]]]:
    clock = Clock()
    service = AnalysedSpectrum(bus, FakeAnalyser(ramp), BANDS, clock=clock)  # type: ignore[arg-type]
    published: list[SpectrumUpdated] = []
    bus.subscribe(SpectrumUpdated, published.append)
    bus.publish(TrackChanged(Track.from_path(ramp.path), 0))
    yield service, clock, published
    service.shutdown()


def test_publishes_the_row_for_the_interpolated_position(
    spectrum: tuple[AnalysedSpectrum, Clock, list[SpectrumUpdated]], bus: EventBus
) -> None:
    service, clock, published = spectrum
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))
    bus.publish(PositionChanged(1000, 3000))  # the backend says 1.0 s
    clock.now += 0.5  # half a second later, no new position yet
    service.tick()
    assert published[-1].levels[0] == pytest.approx(int(1.5 * FPS) / 100)


def test_nothing_is_published_while_paused(
    spectrum: tuple[AnalysedSpectrum, Clock, list[SpectrumUpdated]], bus: EventBus
) -> None:
    service, clock, published = spectrum
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))
    bus.publish(PositionChanged(500, 3000))
    bus.publish(PlaybackStateChanged(PlaybackState.PAUSED))
    clock.now += 5
    service.tick()
    assert published == []
    assert service.position() == pytest.approx(0.5)  # frozen while paused


def test_equalizer_lifts_the_bars(
    spectrum: tuple[AnalysedSpectrum, Clock, list[SpectrumUpdated]], bus: EventBus
) -> None:
    service, _clock, published = spectrum
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))
    bus.publish(PositionChanged(1000, 3000))
    bus.publish(EqualizerChanged(Equalizer(enabled=True).with_preset("Bass Boost")))
    service.tick()
    levels = published[-1].levels
    assert levels[0] > levels[-1]  # bass lifted, treble untouched
    assert levels[-1] == pytest.approx(FPS / 100)
    assert max(levels) <= 1.0


def test_new_track_is_analysed(
    qt_app: QCoreApplication, bus: EventBus, ramp: LevelTimeline
) -> None:
    analyser = FakeAnalyser(ramp)
    service = AnalysedSpectrum(bus, analyser, BANDS)  # type: ignore[arg-type]
    bus.publish(TrackChanged(Track.from_path(Path("/m/next.mp3")), 1))
    assert analyser.requested == [Path("/m/next.mp3")]
    assert service.timeline is ramp
    bus.publish(TrackChanged(None, None))
    assert service.timeline is None
    service.shutdown()


# -- App wiring ---------------------------------------------------------------------------- #


class NoFramesBackend(FakeBackend):
    name = "no-frames"
    provides_audio_frames = False


@pytest.mark.parametrize(("backend", "expected"), [("fake", False), ("no-frames", True)])
def test_app_adds_analysis_only_when_the_backend_has_no_audio(
    qt_app: QCoreApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    expected: bool,
) -> None:
    monkeypatch.setattr(
        registry, "_registered", {"fake": FakeBackend, "no-frames": NoFramesBackend}
    )
    monkeypatch.setattr(Settings, "backend", "fake")
    args = argparse.Namespace(
        paths=[], backend=backend, no_plugins=True, log_level="WARNING", libmpv=None
    )
    application = Application(
        args, SettingsStore(tmp_path / "cfg"), SavedPlaylists(tmp_path / "pl")
    )
    try:
        assert (application.analysed_spectrum is not None) is expected
        application.shutdown()
    finally:
        application.loader.shutdown()
        application.window.close()
