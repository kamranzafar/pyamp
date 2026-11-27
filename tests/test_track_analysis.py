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

import argparse
import threading
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.app import Application
from pyamp.audio import registry, track_analysis
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
from pyamp.core.models import PlaybackState, StreamUrl, Track
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


class WavDecoder:
    """Decodes these tests' WAV files in plain Python, so they run without libmpv (the
    hidden-libmpv decoder the app uses is tested in test_mpv_backend)."""

    def __init__(self) -> None:
        self.decoded: list[Path] = []

    def decode(
        self, source: Path, target: Path, sample_rate: int, cancelled: Callable[[], bool]
    ) -> bool:
        try:
            with wave.open(str(source), "rb") as w:
                assert w.getframerate() == sample_rate and w.getnchannels() == 1
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        except (wave.Error, EOFError, OSError):
            return False
        (pcm.astype(np.float32) / 32768).tofile(target)
        self.decoded.append(source)
        return True


@pytest.fixture
def analyser(qt_app: QCoreApplication) -> Iterator[TrackAnalyser]:
    analyser = TrackAnalyser(BANDS, decoder=WavDecoder())
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
    # The oscilloscope's waveforms come with the rows: the slow tone at its full size
    # (12000 of 32768), the fast one smoothed away.
    slow, fast = timeline.wave_at(0.5), timeline.wave_at(2.5)
    assert slow is not None and fast is not None
    assert 0.3 < np.abs(slow).max() < 0.4
    assert np.abs(fast).max() < 0.1


class StallingWavDecoder(WavDecoder):
    """Writes a file's first second, then waits to be cancelled before writing the rest,
    as a slow decode would: the first file is still being analysed when the second comes."""

    def decode(
        self, source: Path, target: Path, sample_rate: int, cancelled: Callable[[], bool]
    ) -> bool:
        with wave.open(str(source), "rb") as w:
            first = np.frombuffer(w.readframes(sample_rate), dtype="<i2")
            rest = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        with target.open("wb") as out:
            out.write((first.astype(np.float32) / 32768).tobytes())
            out.flush()
            if rest.size:
                wait_for(cancelled)
                if not cancelled():
                    out.write((rest.astype(np.float32) / 32768).tobytes())
        self.decoded.append(source)
        return True


def wait_for(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    """Like wait_until, off the main thread (no Qt events)."""
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.005)


@pytest.fixture
def stalling_analyser(qt_app: QCoreApplication) -> Iterator[TrackAnalyser]:
    analyser = TrackAnalyser(BANDS, decoder=StallingWavDecoder())
    yield analyser
    analyser.shutdown()


def test_analysing_another_file_cancels_the_first(
    stalling_analyser: TrackAnalyser, tmp_path: Path
) -> None:
    analyser = stalling_analyser
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


def test_scratch_files_are_cleaned_up(analyser: TrackAnalyser, tmp_path: Path) -> None:
    timeline = analyser.analyse(write_tones(tmp_path / "tidy.wav", (440,)))
    wait_until(timeline.finished.is_set)
    scratch = analyser._scratch
    assert list(scratch.iterdir()) == []  # the decoded samples went once analysed
    analyser.shutdown()
    assert not scratch.exists()


def test_without_libmpv_there_is_simply_no_analysis(
    qt_app: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def no_libmpv(name: str, *args: object, **kwargs: object) -> object:
        if name == "pyamp.audio.mpv_backend":
            raise OSError("Cannot find libmpv")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_libmpv)
    analyser = TrackAnalyser(BANDS)
    timeline = analyser.analyse(write_tones(tmp_path / "x.wav", (440,)))
    wait_until(timeline.finished.is_set)
    analyser.shutdown()
    assert timeline.rows == []


# -- Publishing while playing --------------------------------------------------------------- #


class FakeAnalyser:
    def __init__(self, timeline: LevelTimeline) -> None:
        self.timeline = timeline
        self.requested: list[Path] = []

    def analyse(self, path: Path) -> LevelTimeline:
        self.requested.append(path)
        return self.timeline

    def cancel_all(self) -> None:
        self.requested.append(Path("cancelled"))


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
    assert ramp.path is not None
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


def test_paused_while_nobody_can_see_it(
    qt_app: QCoreApplication, bus: EventBus, ramp: LevelTimeline
) -> None:
    analyser = FakeAnalyser(ramp)
    service = AnalysedSpectrum(bus, analyser, BANDS)  # type: ignore[arg-type]
    published: list[SpectrumUpdated] = []
    bus.subscribe(SpectrumUpdated, published.append)
    bus.publish(TrackChanged(Track.from_path(Path("/m/one.mp3")), 0))
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))

    service.set_active(False)  # the window was hidden
    assert analyser.requested == [Path("/m/one.mp3"), Path("cancelled")]
    bus.publish(TrackChanged(Track.from_path(Path("/m/two.mp3")), 1))
    service.tick()
    assert published == []
    assert analyser.requested[-1] == Path("cancelled")  # the new track isn't decoded

    service.set_active(True)  # shown again: analyse the track that's playing now
    assert analyser.requested[-1] == Path("/m/two.mp3")
    assert service.timeline is ramp
    service.shutdown()


def test_the_bars_drop_when_the_track_changes(
    spectrum: tuple[AnalysedSpectrum, Clock, list[SpectrumUpdated]], bus: EventBus
) -> None:
    """As with the Qt backend: no frozen bars while the next track gets going."""
    service, _clock, published = spectrum
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))
    bus.publish(PositionChanged(1000, 3000))
    service.tick()
    assert max(published[-1].levels) > 0
    bus.publish(TrackChanged(Track.for_stream(StreamUrl("https://radio.example/live")), 1))
    assert published[-1].levels == (0.0,) * BANDS  # dropped at once
    count = len(published)
    service.tick()  # nothing to show yet (still buffering)...
    service.tick()
    assert len(published) == count  # ...and silence isn't sent again and again


def test_the_bars_drop_where_there_are_no_levels(
    spectrum: tuple[AnalysedSpectrum, Clock, list[SpectrumUpdated]], bus: EventBus
) -> None:
    service, clock, published = spectrum
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))
    bus.publish(PositionChanged(1000, 3000))
    service.tick()
    clock.now += 10  # past what's been analysed
    service.tick()
    assert published[-1].levels == (0.0,) * BANDS


# -- Songs streamed from a server ---------------------------------------------------------- #


class FakeSaver:
    """Stands in for the player: says what's buffered of the stream, and saves it."""

    def __init__(self) -> None:
        self.range: tuple[float, float] | None = None  # the buffered stretch
        self.works = True
        self.saved: list[Path] = []
        self.pieces: list[tuple[float, float]] = []
        self.gate = threading.Event()
        self.gate.set()
        self.started = threading.Event()  # a save is under way

    def buffered_range(self) -> tuple[float, float] | None:
        return self.range

    def save_stream(self, target: Path, start: float, end: float) -> bool:
        self.started.set()
        self.gate.wait(timeout=5)
        if not self.works:
            return False
        target.write_bytes(b"song")
        self.saved.append(target)
        self.pieces.append((start, end))
        return True


SONG = Track.for_stream(StreamUrl("https://music.example/rest/stream?id=1"), "Song", 240_000)
RADIO = Track.for_stream(StreamUrl("https://radio.example/live"), "Radio")


@pytest.fixture
def saver() -> FakeSaver:
    return FakeSaver()


class PieceAnalyser(FakeAnalyser):
    """Takes a stream's pieces: fills one row per piece, at the piece's start."""

    def __init__(self, timeline: LevelTimeline) -> None:
        super().__init__(timeline)
        self.pieces: list[tuple[Path, float]] = []

    def analyse_piece(self, piece: Path, timeline: LevelTimeline, start: float) -> None:
        self.pieces.append((piece, start))
        timeline.put(round(start * FPS), np.full(BANDS, 0.5, dtype=np.float32))
        piece.unlink()


@pytest.fixture
def live(
    qt_app: QCoreApplication,
    bus: EventBus,
    ramp: LevelTimeline,
    saver: FakeSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[AnalysedSpectrum, PieceAnalyser, Clock]]:
    monkeypatch.setattr(track_analysis, "STREAM_CHECK_MS", 5)
    monkeypatch.setattr(track_analysis, "LIVE_PIECE_MS", 5)
    analyser, clock = PieceAnalyser(ramp), Clock()
    service = AnalysedSpectrum(bus, analyser, BANDS, clock=clock, streams=saver)  # type: ignore[arg-type]
    yield service, analyser, clock
    saver.gate.set()
    service.shutdown()


def play(bus: EventBus, track: Track) -> None:
    bus.publish(TrackChanged(track, 0))
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))


# -- Stations ------------------------------------------------------------------------------ #


def test_live_radio_is_analysed_a_piece_at_a_time(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    service, analyser, _clock = live
    published: list[SpectrumUpdated] = []
    bus.subscribe(SpectrumUpdated, published.append)
    play(bus, RADIO)  # no length: it never ends
    saver.range = (0.0, 3.0)  # the first seconds are buffered
    wait_until(lambda: len(analyser.pieces) == 1)
    saver.range = (0.0, 4.5)
    wait_until(lambda: len(analyser.pieces) == 2)
    assert saver.pieces == [(0.0, 3.0), (3.0, 4.5)]  # each stretch once, in order
    assert [start for _, start in analyser.pieces] == [0.0, 3.0]
    assert all(not piece.exists() for piece in saver.saved)
    assert analyser.requested == []  # no whole-file analysis
    service.tick()
    assert published and published[-1].levels[0] == pytest.approx(0.5)  # the bars move


def test_the_first_piece_is_looked_for_soon_then_less_often(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock],
    bus: EventBus,
    saver: FakeSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, analyser, _clock = live
    monkeypatch.setattr(track_analysis, "LIVE_PIECE_MS", 1500)
    play(bus, RADIO)
    assert service._stream_check.interval() == track_analysis.STREAM_CHECK_MS  # bars soon
    saver.range = (0.0, 3.0)
    wait_until(lambda: len(analyser.pieces) == 1)
    assert service._stream_check.interval() == 1500  # then fewer, longer pieces


@pytest.mark.parametrize("track", [RADIO, SONG], ids=["station", "song"])
def test_a_streams_bars_are_looked_for_as_soon_as_it_plays(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock],
    bus: EventBus,
    saver: FakeSaver,
    monkeypatch: pytest.MonkeyPatch,
    track: Track,
) -> None:
    """mpv plays a stream once it has buffered a little: its first position says so, and
    the first piece is saved then, not at the next check (up to STREAM_CHECK_MS later)."""
    _service, analyser, _clock = live
    monkeypatch.setattr(track_analysis, "STREAM_CHECK_MS", 60_000)  # no check comes
    play(bus, track)
    saver.range = (0.0, 3.0)
    bus.publish(PositionChanged(0, 0))  # not playing yet
    assert saver.pieces == []
    bus.publish(PositionChanged(100, 0))  # the sound starts
    wait_until(lambda: len(analyser.pieces) == 1, timeout=2.0)
    bus.publish(PositionChanged(200, 0))  # later positions leave it to the checks
    saver.range = (0.0, 4.5)
    bus.publish(PositionChanged(300, 0))
    assert saver.pieces == [(0.0, 3.0)]


def test_live_pieces_start_near_playback(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    _service, analyser, _clock = live
    play(bus, RADIO)
    bus.publish(PositionChanged(60_000, 0))  # a minute in, with lots buffered behind
    saver.range = (0.0, 63.0)
    wait_until(lambda: len(analyser.pieces) == 1)
    assert saver.pieces == [(59.0, 63.0)]  # not the minute already played


def test_a_stream_on_another_clock_is_left_alone(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock],
    bus: EventBus,
    saver: FakeSaver,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, analyser, _clock = live
    caplog.set_level("INFO")
    play(bus, RADIO)
    bus.publish(PositionChanged(2_000, 0))
    saver.range = (5000.0, 5004.0)  # buffered "times" nowhere near the position
    wait_until(lambda: "timing doesn't match" in caplog.text)
    assert analyser.pieces == [] and service.timeline is None


def test_stream_levels_go_with_the_stream(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    service, analyser, _clock = live
    play(bus, RADIO)
    saver.range = (0.0, 2.0)
    wait_until(lambda: len(analyser.pieces) == 1)
    timeline = service.timeline
    assert timeline is not None
    service.set_active(False)  # hidden: nothing is saved or kept
    assert timeline.cancelled.is_set() and service.timeline is None
    service.set_active(True)
    assert service.timeline is not None and service.timeline is not timeline
    bus.publish(TrackChanged(Track.from_path(Path("next.mp3")), 1))  # another track
    assert timeline.cancelled.is_set() and analyser.requested[-1] == Path("next.mp3")


def test_station_pieces_go_only_a_little_ahead_of_playback(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock],
    bus: EventBus,
    saver: FakeSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A station's levels are kept for a few minutes: analysing far ahead would push the
    levels being played out of them (the bars would vanish)."""
    _service, analyser, _clock = live
    monkeypatch.setattr(track_analysis, "PIECE_MAX_S", 600.0)
    play(bus, RADIO)
    bus.publish(PositionChanged(3_000, 0))
    saver.range = (0.0, 520.0)  # minutes buffered
    wait_until(lambda: len(analyser.pieces) == 1)
    assert saver.pieces == [(2.0, 3.0 + track_analysis.LIVE_AHEAD_S)]


def test_a_station_sought_back_past_its_levels_starts_afresh(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock],
    bus: EventBus,
    saver: FakeSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, analyser, _clock = live
    monkeypatch.setattr(track_analysis, "LIVE_KEEP_S", 1.0)
    play(bus, RADIO)
    bus.publish(PositionChanged(10_000, 0))
    saver.range = (0.0, 13.0)
    wait_until(lambda: len(analyser.pieces) == 1)
    timeline = service.timeline
    for n in range(10 * FPS, 13 * FPS):  # its levels are analysed, the oldest dropped
        assert timeline is not None
        timeline.put(n, np.zeros(BANDS, dtype=np.float32))
    bus.publish(PositionChanged(2_000, 0))  # back eight seconds, still buffered
    wait_until(lambda: len(analyser.pieces) == 2)
    assert saver.pieces[-1] == (1.0, 13.0)
    assert timeline is not None and timeline.cancelled.is_set()
    assert service.timeline is not timeline


def test_live_levels_keep_only_the_last_minutes() -> None:
    timeline = LevelTimeline(keep_seconds=1.0)
    row = np.zeros(BANDS, dtype=np.float32)
    for n in range(10 * FPS):  # ten seconds of radio
        timeline.put(n, row)
    assert len(timeline.rows) <= 2 * FPS
    assert timeline.row_at(9.9) is row
    assert timeline.row_at(1.0) is None  # long gone
    timeline.put(5, row)  # a late row for a stretch that's gone is ignored
    gap = LevelTimeline()
    gap.put(3, row)
    assert gap.rows[:3] == [None, None, None] and gap.row_at(3 / FPS) is row


# -- Songs from a server ------------------------------------------------------------------- #


def test_a_song_is_analysed_as_it_buffers_and_kept_whole(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    service, analyser, _clock = live
    play(bus, SONG)
    saver.range = (0.0, 2.0)  # the start is in
    wait_until(lambda: len(analyser.pieces) == 1)
    saver.range = (0.0, 70.0)  # more comes: analysed ahead, a piece after another
    wait_until(lambda: len(analyser.pieces) == 4)
    assert saver.pieces == [(0.0, 2.0), (2.0, 32.0), (32.0, 62.0), (62.0, 70.0)]
    timeline = service.timeline
    assert timeline is not None and timeline.first_row == 0  # nothing dropped
    assert timeline.row_at(0.0) is not None and timeline.row_at(62.0) is not None
    assert analyser.requested == []


def test_seeking_in_a_song_finds_its_bars_or_looks_for_them_at_once(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock],
    bus: EventBus,
    saver: FakeSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, analyser, _clock = live
    play(bus, SONG)
    saver.range = (0.0, 40.0)
    wait_until(lambda: len(analyser.pieces) == 2)  # (0, 30), (30, 40)
    timeline = service.timeline
    monkeypatch.setattr(track_analysis, "LIVE_PIECE_MS", 60_000)
    service._stream_check.setInterval(60_000)  # no check comes
    bus.publish(PositionChanged(100, 0))
    bus.publish(PositionChanged(35_000, 0))  # a seek into what's analysed: nothing to do
    QCoreApplication.processEvents()
    assert len(saver.pieces) == 2
    bus.publish(PositionChanged(120_000, 0))  # past what's buffered: nothing there yet,
    QCoreApplication.processEvents()  # but the song isn't given up on
    assert len(saver.pieces) == 2 and service.timeline is timeline
    saver.range = (119.0, 165.0)  # mpv fetches from there
    bus.publish(PositionChanged(150_000, 0))  # a seek into it: looked for at once
    wait_until(lambda: len(analyser.pieces) == 3, timeout=2.0)
    assert saver.pieces[-1] == (149.0, 165.0)
    assert service.timeline is timeline  # the levels already found stay


def test_a_piece_for_a_track_that_has_ended_is_dropped(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    _service, analyser, _clock = live
    saver.range = (0.0, 5.0)
    saver.gate.clear()  # saving takes a while
    play(bus, SONG)
    wait_until(saver.started.is_set)
    bus.publish(TrackChanged(Track.from_path(Path("next.mp3")), 1))  # meanwhile
    saver.gate.set()
    wait_until(lambda: len(saver.saved) == 1)
    wait_until(lambda: not saver.saved[0].exists())  # deleted, not analysed
    assert analyser.pieces == []
    assert analyser.requested == [Path("next.mp3")]


def test_a_piece_that_cant_be_saved_is_tried_again(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    _service, analyser, _clock = live
    saver.works = False
    saver.started.clear()
    play(bus, SONG)
    saver.range = (0.0, 5.0)
    wait_until(saver.started.is_set)
    time.sleep(0.05)
    QCoreApplication.processEvents()
    assert analyser.pieces == []
    saver.works = True
    wait_until(lambda: len(analyser.pieces) == 1)
    assert saver.pieces == [(0.0, 5.0)]


def test_streams_wait_while_nobody_can_see_the_visualiser(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    service, analyser, _clock = live
    service.set_active(False)
    saver.range = (0.0, 5.0)
    play(bus, SONG)
    time.sleep(0.05)
    QCoreApplication.processEvents()
    assert saver.saved == []  # nothing saved for a hidden visualiser
    service.set_active(True)
    wait_until(lambda: len(analyser.pieces) == 1)  # once shown again


def test_the_pieces_folder_goes_at_shutdown(
    live: tuple[AnalysedSpectrum, PieceAnalyser, Clock], bus: EventBus, saver: FakeSaver
) -> None:
    service, analyser, _clock = live
    play(bus, SONG)
    saver.range = (0.0, 5.0)
    wait_until(lambda: len(analyser.pieces) == 1)
    folder = saver.saved[0].parent
    service.shutdown()
    assert not folder.exists()


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
