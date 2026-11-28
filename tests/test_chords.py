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

"""The Chords plugin: finding a song's chords, keeping them, and following the playback."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtGui import QFontMetrics, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QMenu, QWidget

from pyamp.app import Application
from pyamp.audio import track_analysis
from pyamp.audio.track_analysis import ANALYSIS_RATE, AnalysedSpectrum, AudioFeed, TrackAnalyser
from pyamp.core.events import EventBus, PlaybackStateChanged, TrackChanged
from pyamp.core.models import PlaybackState, StreamUrl, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.plugins.base import PluginContext
from pyamp.plugins.chords import ChordsPlugin
from pyamp.plugins.chords import tracker as chords_tracker
from pyamp.plugins.chords.analysis import (
    NOTE_COLUMNS,
    QUALITIES,
    SAMPLE_RATE,
    VERSION,
    AnalysisCancelledError,
    Chord,
    ChordChart,
    ChordSegment,
    Frames,
    Key,
    chord_scores,
    chromagram,
    estimate_tuning,
    find_chords,
    onset_envelope,
    smooth,
)
from pyamp.plugins.chords.beats import onset_sizes, track_beats
from pyamp.plugins.chords.cache import ChordCache, stream_key
from pyamp.plugins.chords.settings import ChordSettings
from pyamp.plugins.chords.tracker import ChordTracker, ChromaTarget, ChromaTimeline
from pyamp.plugins.chords.window import NO_CHORD, ChordWindow, strip_label
from tests.conftest import FakeBackend, answer_dialog
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)
from tests.test_track_analysis import wait_until

G, D, E, C, A, F, B, B_FLAT = 7, 2, 4, 0, 9, 5, 11, 10


# -- Synthesised songs -------------------------------------------------------- #


def tone(
    midi: int, seconds: float, rate: int = SAMPLE_RATE, cents: float = 0.0
) -> NDArray[np.float64]:
    """A note with harmonics, like an instrument's (not a pure sine), ``cents`` off pitch."""
    t = np.arange(int(seconds * rate)) / rate
    freq = 440 * 2 ** ((midi - 69 + cents / 100) / 12)
    sound = np.zeros(t.size)
    for h in range(1, 9):
        if freq * h < rate / 2:
            sound += (0.7 ** (h - 1)) * np.sin(2 * np.pi * freq * h * t)
    return sound


def strum(
    root: int, quality: str, seconds: float, rate: int = SAMPLE_RATE, cents: float = 0.0
) -> NDArray[np.float64]:
    """A chord, with its root in the bass, fading a little as it rings."""
    sound = 1.5 * tone(36 + root, seconds, rate, cents)
    for step in QUALITIES[quality]:
        sound += tone(60 + root + step, seconds, rate, cents)
    t = np.arange(sound.size)
    return sound * np.minimum(1, t / 200) * np.exp(-t / rate * 0.4)


def song(
    chords: list[tuple[int, str]],
    seconds: float = 2.0,
    noise: float = 0.05,
    rate: int = SAMPLE_RATE,
    *,
    cents: float = 0.0,
    drums: float = 0.0,
) -> NDArray[np.int16]:
    """``chords`` strummed one after another, ``cents`` off pitch, with a drum every half
    second if ``drums`` (its loudness)."""
    audio = np.concatenate([strum(r, q, seconds, rate, cents) for r, q in chords])
    audio = audio / np.abs(audio).max() * 0.5
    rng = np.random.default_rng(1)
    hit = np.exp(-np.arange(2000) / 300)
    for beat in range(0, audio.size - hit.size, rate // 2) if drums else ():
        audio[beat : beat + hit.size] += drums * rng.normal(0, 1, hit.size) * hit
    audio = audio + rng.normal(0, noise, audio.size)
    samples: NDArray[np.int16] = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    return samples


PROGRESSION = [(G, ""), (D, ""), (E, "m"), (C, ""), (A, "m"), (D, "7"), (G, ""), (C, "maj7")]


def names(chart: ChordChart) -> list[str]:
    return [chart.chord_name(s.chord) for s in chart.segments if s.chord]


# -- Analysis ----------------------------------------------------------------- #


def test_finds_a_progression_with_its_timing_and_key() -> None:
    chart = find_chords(song(PROGRESSION))

    assert names(chart) == ["G", "D", "Em", "C", "Am", "D7", "G", "Cmaj7"]
    for number, segment in enumerate(chart.segments):
        assert segment.start == pytest.approx(2.0 * number, abs=0.25)
    assert chart.duration == pytest.approx(16.0)
    assert chart.key == Key(G)


def test_a_minor_key_and_flats_are_spelled_as_written() -> None:
    chart = find_chords(song([(D, "m"), (B_FLAT, ""), (F, ""), (C, ""), (D, "m"), (A, "")]))

    assert chart.key == Key(D, minor=True)
    assert names(chart) == ["Dm", "B♭", "F", "C", "Dm", "A"]


def test_silence_has_no_chords() -> None:
    gap = np.zeros(3 * SAMPLE_RATE, dtype=np.int16)
    chart = find_chords(np.concatenate([gap, song([(C, "")], 3.0, noise=0), gap]))

    assert [(s.chord, round(s.start)) for s in chart.segments] == [
        (None, 0),
        (Chord(C), 3),
        (None, 6),
    ]
    assert find_chords(np.zeros(0, dtype=np.int16)).segments == ()
    assert names(find_chords(np.zeros(SAMPLE_RATE, dtype=np.int16))) == []


def test_smoothing_ignores_a_blip_but_follows_a_change() -> None:
    scores = np.zeros((20, 3))
    scores[:, 0] = 0.9
    scores[8, 1] = 1.0  # one frame of something else
    scores[14:, 2] = 1.0  # a real change

    assert smooth(scores).tolist() == [0] * 14 + [2] * 6


def test_a_noisy_frame_matches_no_chord() -> None:
    chroma = np.ones((2, 12))
    chroma[0, [0, 4, 7]] = 30  # a clear C major
    scores = chord_scores(chroma, np.ones(2))

    assert scores[0].argmax() == 0  # C, the first chord
    assert scores[1].argmax() == scores.shape[1] - 1  # nothing clear: no chord


def test_analysis_can_be_stopped() -> None:
    with pytest.raises(AnalysisCancelledError):
        find_chords(song(PROGRESSION), cancelled=lambda: True)


@pytest.mark.parametrize("cents", [-48, -30, 30, 45])
def test_a_recording_off_standard_pitch_is_heard_at_its_own_tuning(cents: int) -> None:
    """An older record, or one tuned to A = 432 Hz (-32 cents), isn't heard a quarter-tone
    "off" (between two chords)."""
    audio = song(PROGRESSION, cents=cents)
    fine, _loudness = chromagram(audio)

    assert estimate_tuning(fine) == pytest.approx(cents / 100, abs=0.06)
    assert names(find_chords(audio)) == ["G", "D", "Em", "C", "Am", "D7", "G", "Cmaj7"]


def test_beats_are_found_where_they_fall() -> None:
    rng = np.random.default_rng(0)
    frame, hop = onset_sizes(ANALYSIS_RATE)
    for period in (0.5, 60 / 90):
        clicks = np.arange(0.3, 20, period)
        audio = rng.normal(0, 0.02, 20 * ANALYSIS_RATE)
        for at in clicks:
            start = int(at * ANALYSIS_RATE)
            audio[start : start + 1500] += rng.normal(0, 0.5, 1500) * np.exp(-np.arange(1500) / 150)
        onsets = onset_envelope(audio, ANALYSIS_RATE)
        found = (track_beats(onsets, hop / ANALYSIS_RATE) * hop + frame / 2) / ANALYSIS_RATE
        inside = found[(found > clicks[0] - 0.1) & (found < clicks[-1] + 0.1)]
        assert len(inside) == len(clicks), period
        assert max(np.min(np.abs(clicks - t)) for t in inside) < 0.02, period

    noise = onset_envelope(rng.normal(0, 0.1, 20 * ANALYSIS_RATE), ANALYSIS_RATE)
    assert len(track_beats(noise, hop / ANALYSIS_RATE)) == 0  # no beat: none made up


def test_with_a_beat_the_chords_change_on_it() -> None:
    chart = find_chords(song(PROGRESSION, drums=0.6))

    assert names(chart) == ["G", "D", "Em", "C", "Am", "D7", "G", "Cmaj7"]
    for number, segment in enumerate(chart.segments):
        assert segment.start == pytest.approx(2.0 * number, abs=0.05)


def sung(register: int, rate: int = SAMPLE_RATE) -> NDArray[np.int16]:
    """PROGRESSION with a loud tune over it, from MIDI note ``register`` up, that also
    sings notes outside each chord (a 2nd, 4th, 6th), as a voice does."""
    n = 2 * rate
    parts = []
    for root, quality in PROGRESSION:
        sound = strum(root, quality, 2.0, rate)
        for k, step in enumerate((0, 2, 4, 5, 7, 9, 7, 4)):
            note = register + (root + step) % 12
            part = slice(k * n // 8, (k + 1) * n // 8)
            sound[part] += 2.5 * tone(note, 0.3, rate)[: part.stop - part.start]
        parts.append(sound)
    audio = np.concatenate(parts)
    audio = audio / np.abs(audio).max() * 0.5 + np.random.default_rng(1).normal(0, 0.05, audio.size)
    samples: NDArray[np.int16] = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    return samples


@pytest.mark.parametrize("register", [60, 69], ids=["among the chords", "above them"])
def test_a_tune_sung_over_the_chords_doesnt_change_them(register: int) -> None:
    """The bass note and the lower notes count for more than a melody's."""
    chart = find_chords(sung(register))
    assert names(chart) == ["G", "D", "Em", "C", "Am", "D7", "G", "Cmaj7"]


def test_an_inversion_keeps_its_chord_and_shows_its_bass() -> None:
    """C with E in the bass is still C, not Em (any chord note may be the bass), and is
    shown as C/E."""
    rate = SAMPLE_RATE
    chords = [(C, ""), (A, "m"), (F, ""), (G, "")]
    audio = np.concatenate(
        [
            strum(root, quality, 2.0, rate)
            - 1.5 * tone(36 + root, 2.0, rate)  # no root bass
            + 1.5 * tone(36 + root + QUALITIES[quality][1], 2.0, rate)  # the 3rd instead
            for root, quality in chords
        ]
    )
    chart = find_chords(audio / np.abs(audio).max() * 0.5)
    found = [s.chord for s in chart.segments if s.chord]
    assert [c.name(bass=False).removesuffix("7").removesuffix("maj") for c in found] == [
        "C",
        "Am",
        "F",
        "G",
    ]
    assert [c.bass_name() for c in found] == ["E", "C", "A", "B"]  # shown as C/E, Am/C…


def strummed_over(bass_notes: list[list[int]], rate: int = SAMPLE_RATE) -> NDArray[np.float64]:
    """C Am F G, each over the bass notes given (MIDI), in equal parts of its two seconds."""
    chords = [(C, ""), (A, "m"), (F, ""), (G, "")]
    n = 2 * rate
    parts = []
    for (root, quality), line in zip(chords, bass_notes, strict=True):
        sound = np.zeros(n)
        for step in QUALITIES[quality]:
            sound += tone(60 + root + step, 2.0, rate)
        for k, note in enumerate(line):
            part = slice(k * n // len(line), (k + 1) * n // len(line))
            sound[part] += 1.5 * tone(note, 2.1, rate)[: part.stop - part.start]
        parts.append(sound)
    audio = np.concatenate(parts)
    scaled: NDArray[np.float64] = audio / np.abs(audio).max() * 0.5
    return scaled


def test_a_held_bass_note_shows_with_its_chord_and_the_root_doesnt() -> None:
    held = find_chords(strummed_over([[43], [40], [36], [38]]))  # each chord's 5th: G E C D
    assert names(held) == ["C/G", "Am/E", "F/C", "G/D"]
    rooted = find_chords(strummed_over([[36], [45], [41], [43]]))  # each chord's root
    assert names(rooted) == ["C", "Am", "F", "G"]


def test_a_walking_bass_shows_no_slashes() -> None:
    """Passing notes in the bass aren't written as slash chords: no single note holds."""
    walk = [[43, 43, 38, 40], [40, 40, 47, 48], [36, 36, 43, 45], [38, 38, 45, 47]]
    assert names(find_chords(strummed_over(walk))) == ["C", "Am", "F", "G"]


# -- Charts ------------------------------------------------------------------- #


CHART = ChordChart(
    (
        ChordSegment(0.0, 1.5, None),
        ChordSegment(1.5, 4.0, Chord(A, "m")),
        ChordSegment(4.0, 65.0, Chord(F, "maj7")),
    ),
    Key(C),
)


def test_the_chord_at_a_time() -> None:
    assert [CHART.index_at(t) for t in (-1, 0, 1.49, 1.5, 3.9, 4.0, 64.9, 65)] == [
        None,
        0,
        0,
        1,
        1,
        2,
        2,
        None,
    ]


def test_transposing_moves_chords_and_key_and_respells_them() -> None:
    up = CHART.transposed(1)

    assert up.key == Key(1)  # D♭ major: written with flats
    assert names(up) == ["B♭m", "G♭maj7"]
    assert names(CHART.transposed(-12)) == names(CHART)
    assert names(CHART.transposed(2)) == ["Bm", "Gmaj7"]  # D major: sharps


def test_a_chords_bass_note_is_named_transposed_and_spelled_for_the_key() -> None:
    c_over_e = Chord(C, "", E)
    assert c_over_e.name() == "C/E" and c_over_e.name(bass=False) == "C"
    assert c_over_e.bass_name() == "E" and Chord(C).bass_name() is None
    assert c_over_e.transposed(2).name() == "D/F♯"
    in_f = ChordChart((ChordSegment(0, 1, Chord(F, "", B_FLAT)),), Key(F))
    assert names(in_f) == ["F/B♭"]  # F major is written with flats
    assert in_f.sheet() == "Key: F major\n\n0:00  F/B♭\n"
    assert in_f.sheet(bass=False) == "Key: F major\n\n0:00  F\n"
    assert ChordChart.from_json(json.loads(json.dumps(in_f.to_json()))) == in_f


def test_the_chord_sheet() -> None:
    assert CHART.sheet("Band - Song") == "Band - Song\nKey: C major\n\n0:01  Am\n0:04  Fmaj7\n"
    assert ChordChart((ChordSegment(61, 62, Chord(1)),)).sheet() == "1:01  C♯\n"


def test_charts_survive_being_saved_and_older_ones_are_not_read() -> None:
    saved = json.loads(json.dumps(CHART.to_json()))

    assert ChordChart.from_json(saved) == CHART
    assert ChordChart.from_json({**saved, "version": VERSION - 1}) is None
    assert ChordChart.from_json({"version": VERSION, "segments": [[0, 1, 0, "13b9"]]}) is None
    assert ChordChart.from_json({"version": VERSION, "segments": "junk"}) is None
    assert ChordChart.from_json({}) is None


# -- Finding them from the shared audio -------------------------------------------- #


class RawDecoder:
    """ "Decodes" a file that holds raw samples already; counts decodes, and may hold them up."""

    def __init__(self) -> None:
        self.decoded: list[Path] = []
        self.hold = threading.Event()
        self.hold.set()

    def decode(
        self, source: Path, target: Path, sample_rate: int, cancelled: Callable[[], bool]
    ) -> bool:
        assert sample_rate == ANALYSIS_RATE
        self.decoded.append(source)
        while not self.hold.wait(0.01):
            if cancelled():
                return False
        if source.suffix == ".bad":
            return False
        with target.open("wb") as out:  # a named pipe, as mpv writes it
            out.write(source.read_bytes())
        return True


def raw_song(path: Path, chords: list[tuple[int, str]] = PROGRESSION) -> Path:
    song(chords, rate=ANALYSIS_RATE).tofile(path)
    return path


class RawSaver:
    """Stands in for the player streaming ``samples``: says what's "buffered", and saves
    a stretch of it as raw samples (as RawDecoder reads them)."""

    def __init__(self, samples: NDArray[np.int16]) -> None:
        self.samples = samples
        self.range: tuple[float, float] | None = None
        self.pieces: list[tuple[float, float]] = []

    def buffered_range(self) -> tuple[float, float] | None:
        return self.range

    def save_stream(self, target: Path, start: float, end: float) -> bool:
        self.pieces.append((start, end))
        self.samples[round(start * ANALYSIS_RATE) : round(end * ANALYSIS_RATE)].tofile(target)
        return True


@pytest.fixture
def decoder() -> RawDecoder:
    return RawDecoder()


@pytest.fixture
def cache(tmp_path: Path) -> ChordCache:
    return ChordCache(tmp_path / "cache")


@pytest.fixture
def analyser(qt_app: QCoreApplication, decoder: RawDecoder) -> Iterator[TrackAnalyser]:
    made = TrackAnalyser(20, decoder=decoder)
    yield made
    made.shutdown()


@pytest.fixture
def streamed() -> RawSaver:
    return RawSaver(song(PROGRESSION, rate=ANALYSIS_RATE))


@pytest.fixture
def feed(
    bus: EventBus,
    analyser: TrackAnalyser,
    streamed: RawSaver,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[AudioFeed]:
    monkeypatch.setattr(track_analysis, "STREAM_CHECK_MS", 5)
    monkeypatch.setattr(track_analysis, "LIVE_PIECE_MS", 5)
    made = AudioFeed(bus, analyser, streams=streamed)
    yield made
    made.shutdown()


@pytest.fixture
def tracker(feed: AudioFeed, cache: ChordCache) -> Iterator[ChordTracker]:
    made = ChordTracker(feed, cache)
    made.start()
    yield made
    made.shutdown()


def play(bus: EventBus, track: Track) -> None:
    bus.publish(TrackChanged(track, 0))
    bus.publish(PlaybackStateChanged(PlaybackState.PLAYING))


SONG_URL = "https://music.example/rest/stream?id=7&u=me&t=abc&s=salt1&v=1.16&c=pyamp"
SONG = Track.for_stream(StreamUrl(SONG_URL), "Song", 16_000)
RADIO = Track.for_stream(StreamUrl("https://radio.example/live"), "Radio")


def test_a_file_is_decoded_once_for_the_visualiser_and_the_chords(
    bus: EventBus,
    analyser: TrackAnalyser,
    feed: AudioFeed,
    tracker: ChordTracker,
    decoder: RawDecoder,
    cache: ChordCache,
    tmp_path: Path,
) -> None:
    spectrum = AnalysedSpectrum(bus, analyser, 20, feed=feed)
    path = raw_song(tmp_path / "song.raw")
    play(bus, Track.from_path(path))
    assert tracker.state == "finding"

    wait_until(lambda: tracker.state == "done")
    assert tracker.chart is not None and names(tracker.chart) == names(
        find_chords(song(PROGRESSION))
    )
    timeline = spectrum.timeline
    assert timeline is not None
    wait_until(timeline.finished.is_set)
    assert timeline.seconds == pytest.approx(16.0, abs=0.1)  # the visualiser's levels too
    assert decoder.decoded == [path]  # one decode for both
    assert cache.load(path) == tracker.chart

    play(bus, Track.from_path(path))  # again: the levels are kept, the chords known
    assert tracker.state == "done" and tracker.chart == cache.load(path)
    assert decoder.decoded == [path]
    spectrum.shutdown()


def test_chords_are_only_looked_for_while_wanted(
    bus: EventBus, feed: AudioFeed, cache: ChordCache, decoder: RawDecoder, tmp_path: Path
) -> None:
    tracker = ChordTracker(feed, cache)
    path = raw_song(tmp_path / "song.raw")
    play(bus, Track.from_path(path))
    QCoreApplication.processEvents()
    assert decoder.decoded == [] and tracker.state == "idle"

    tracker.start()  # the window opens: the playing track is analysed for it
    wait_until(lambda: tracker.state == "done")
    assert decoder.decoded == [path]
    tracker.stop()
    assert tracker.state == "idle" and tracker.chart is None
    tracker.shutdown()


def test_a_song_from_a_server_is_found_as_it_buffers_then_kept(
    bus: EventBus,
    tracker: ChordTracker,
    streamed: RawSaver,
    cache: ChordCache,
    decoder: RawDecoder,
) -> None:
    play(bus, SONG)
    streamed.range = (0.0, 8.0)  # the first half is in
    wait_until(lambda: tracker.chart is not None)
    assert tracker.state == "finding"
    assert tracker.chart is not None
    assert names(tracker.chart)[:4] == ["G", "D", "Em", "C"]  # its chords so far

    streamed.range = (0.0, 16.0)  # all of it
    wait_until(lambda: tracker.state == "done")
    assert streamed.pieces == [(0.0, 8.0), (8.0, 16.0)]  # each stretch saved once
    assert tracker.chart is not None
    assert names(tracker.chart) == ["G", "D", "Em", "C", "Am", "D7", "G", "Cmaj7"]

    # Next time it's known, though the server's login details in its address differ.
    again = Track.for_stream(StreamUrl(SONG_URL.replace("salt1", "salt2")), "Song", 16_000)
    pieces = len(decoder.decoded)
    play(bus, again)
    assert tracker.state == "done" and tracker.chart == cache.load(SONG.location)
    QCoreApplication.processEvents()
    assert len(decoder.decoded) == pieces


def test_a_station_is_followed_as_it_plays(
    bus: EventBus, tracker: ChordTracker, streamed: RawSaver, cache: ChordCache
) -> None:
    play(bus, RADIO)
    streamed.range = (0.0, 6.0)
    wait_until(lambda: tracker.chart is not None and len(names(tracker.chart)) >= 3)
    assert tracker.state == "listening"
    assert tracker.chart is not None and names(tracker.chart)[:3] == ["G", "D", "Em"]
    assert not list(cache.directory.glob("*.json"))  # a station's chords aren't kept


def test_a_file_that_cannot_be_decoded(
    bus: EventBus, tracker: ChordTracker, tmp_path: Path
) -> None:
    path = tmp_path / "song.bad"
    path.write_bytes(b"")
    play(bus, Track.from_path(path))
    wait_until(lambda: tracker.state == "failed")
    assert tracker.chart is None


def test_another_track_drops_the_first(
    bus: EventBus, tracker: ChordTracker, decoder: RawDecoder, tmp_path: Path
) -> None:
    first = raw_song(tmp_path / "first.raw")
    second = raw_song(tmp_path / "second.raw", [(C, ""), (F, ""), (C, "")])
    decoder.hold.clear()
    play(bus, Track.from_path(first))
    wait_until(lambda: decoder.decoded == [first])
    play(bus, Track.from_path(second))
    decoder.hold.set()
    wait_until(lambda: tracker.state == "done")
    assert tracker.chart is not None and names(tracker.chart) == ["C", "F", "C"]


def test_charts_are_worked_out_off_the_shared_analysis_thread(
    bus: EventBus, tracker: ChordTracker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The visualiser's levels come from the shared thread: the chords never hold it up."""
    threads: list[str] = []

    def chart_from_frames(frames: Frames) -> ChordChart:
        threads.append(threading.current_thread().name)
        return find_chords(song(PROGRESSION))

    monkeypatch.setattr(chords_tracker, "chart_from_frames", chart_from_frames)
    play(bus, Track.from_path(raw_song(tmp_path / "song.raw")))
    wait_until(lambda: tracker.state == "done")
    assert threads and all(name.startswith("pyamp-chords") for name in threads)


def test_pieces_can_come_in_any_order_and_gaps_have_no_chord() -> None:
    samples = (song(PROGRESSION, rate=ANALYSIS_RATE) / 32768).astype(np.float32)
    done: list[bool] = []
    timeline = ChromaTimeline()
    target = ChromaTarget(timeline, lambda _target, piece: done.append(piece))
    for start, end in ((8.0, 16.0), (0.0, 4.0)):  # the end first; 4..8 s never comes
        put = target.begin(start, piece=True)
        piece = samples[round(start * ANALYSIS_RATE) : round(end * ANALYSIS_RATE)]
        for batch in np.array_split(piece, 7):  # as the reader hands them on
            put(batch)
        target.end(piece=True)

    chart = timeline.chart()
    assert done == [True, True]
    assert [(round(s.start), chart.chord_name(s.chord)) for s in chart.segments] == [
        (0, "G"),
        (2, "D"),
        (4, "N.C."),  # not analysed
        (8, "Am"),
        (10, "D7"),
        (12, "G"),
        (14, "Cmaj7"),
    ]
    assert timeline.covered == pytest.approx(12.0, abs=1.0)  # less the edges of pieces


def test_a_station_keeps_only_its_last_minutes() -> None:
    timeline = ChromaTimeline(keep_seconds=10.0)
    count = round(60 * ANALYSIS_RATE / timeline.hop)  # a minute of frames
    timeline.put(0, np.ones((count, NOTE_COLUMNS)), np.ones(count))

    assert 45 < timeline.kept_from < 51
    timeline.put(0, np.ones((5, NOTE_COLUMNS)), np.ones(5))  # too old: ignored
    assert timeline.kept_from > 45
    assert timeline.frames().first * timeline.hop / ANALYSIS_RATE == timeline.kept_from


def test_a_changed_file_is_analysed_again(cache: ChordCache, tmp_path: Path) -> None:
    path = tmp_path / "song.raw"
    path.write_bytes(b"x")
    cache.save(path, CHART)
    assert cache.load(path) == CHART

    path.write_bytes(b"longer")
    assert cache.load(path) is None
    assert cache.load(tmp_path / "missing") is None


def test_the_cache_keeps_the_most_recently_used(tmp_path: Path) -> None:
    cache = ChordCache(tmp_path / "cache", limit=2)
    files = [tmp_path / f"{n}.raw" for n in range(3)]
    for file in files:
        file.write_bytes(file.name.encode())
        cache.save(file, CHART)

    assert len(list(cache.directory.glob("*.json"))) == 2


def test_a_streams_key_leaves_out_the_login() -> None:
    assert stream_key(StreamUrl(SONG_URL)) == "https://music.example/rest/stream?id=7"
    assert stream_key(StreamUrl("http://dlna.example/a.mp3")) == "http://dlna.example/a.mp3"


# -- The window ----------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.seconds = 0.0

    def __call__(self) -> float:
        return self.seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def sought() -> list[float]:
    return []


@pytest.fixture
def window(qt_app: QCoreApplication, clock: Clock, sought: list[float]) -> Iterator[ChordWindow]:
    made = ChordWindow(clock, sought.append)
    yield made
    made.close()


def test_the_window_follows_the_playback(window: ChordWindow, clock: Clock) -> None:
    window.show_track("Band - Song")
    window.show_chart(CHART)
    assert window.chord_label.text() == NO_CHORD  # no chord at first

    clock.seconds = 2.0
    window.tick()
    assert window.chord_label.text() == "Am"
    assert window.next_label.text() == "then  Fmaj7"
    assert window.key_label.text() == "Key: C major"

    clock.seconds = 70.0
    window.tick()
    assert window.chord_label.text() == NO_CHORD


def test_transposing_in_the_window_and_a_new_track_resets_it(
    window: ChordWindow, clock: Clock
) -> None:
    window.show_track("Song")
    window.show_chart(CHART)
    clock.seconds = 5.0

    window.set_transpose(2)
    assert window.chord_label.text() == "Gmaj7"
    assert window.transpose_label.text() == "+2"
    assert window.key_label.text() == "Key: D major"
    window.set_transpose(40)
    assert window.transpose == 11

    window.show_track("Another")
    assert window.transpose == 0
    assert window.chart is None
    assert not window.copy_button.isEnabled()


def test_clicking_a_chord_on_the_strip_goes_there(
    window: ChordWindow, clock: Clock, sought: list[float]
) -> None:
    window.resize(600, 300)
    window.show_chart(CHART)
    clock.seconds = 0.0
    window.tick()
    strip = window.strip
    # "Now" is a third of the way along; 2.5 s later is the Am.
    x = strip.width() / 3 + 2.5 * 48
    press = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(x, 10),
        QPointF(x, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    strip.mousePressEvent(press)

    assert sought == [1.5]
    strip.grab()  # paints without trouble


SLASHED = ChordChart(
    (ChordSegment(0.0, 2.0, Chord(C, "", E)), ChordSegment(2.0, 4.0, Chord(G, "", B))),
    Key(C),
)


def test_the_window_shows_bass_notes_smaller_and_can_leave_them_out(
    window: ChordWindow, clock: Clock
) -> None:
    turned: list[bool] = []
    window.show_bass_changed.connect(turned.append)
    window.show_chart(SLASHED)
    clock.seconds = 1.0
    window.tick()
    big = window.chord_label.text()
    assert big.startswith("C<span") and "/E</span>" in big  # the bass in its own style
    assert window.next_label.text() == "then  G/B"
    assert window.sheet() == "Key: C major\n\n0:00  C/E\n0:02  G/B\n"

    window.bass_box.setChecked(False)  # the box, as the user clicks it
    assert turned == [False] and not window.show_bass
    assert window.chord_label.text() == "C"
    assert window.next_label.text() == "then  G"
    assert window.sheet() == "Key: C major\n\n0:00  C\n0:02  G\n"
    assert not window.strip.show_bass


def test_a_narrow_block_drops_the_bass_note_before_cutting_the_name(
    window: ChordWindow,
) -> None:
    metrics = QFontMetrics(window.font())
    chord = Chord(C, "maj7", E)
    full = metrics.horizontalAdvance("Cmaj7/E")
    assert strip_label(SLASHED, chord, full + 2, metrics, bass=True) == "Cmaj7/E"
    assert strip_label(SLASHED, chord, full - 2, metrics, bass=True) == "Cmaj7"
    assert strip_label(SLASHED, chord, full + 2, metrics, bass=False) == "Cmaj7"
    tiny = strip_label(SLASHED, chord, metrics.horizontalAdvance("C…"), metrics, bass=True)
    assert tiny.endswith("…") and "/" not in tiny


def test_the_chord_sheet_is_copied_and_saved(
    window: ChordWindow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window.show_track("Band - Song")
    window.show_chart(CHART)
    window.set_transpose(1)
    window.set_transpose(0)  # back as found

    window.copy_sheet()
    assert QGuiApplication.clipboard().text() == CHART.sheet("Band - Song")

    target = tmp_path / "sheet.txt"
    asked = answer_dialog(monkeypatch, "choose_save_file", [target])
    window.save_sheet()
    assert asked == [(window, "Save Chord Sheet")]
    assert target.read_text(encoding="utf-8") == CHART.sheet("Band - Song")

    answer_dialog(monkeypatch, "choose_save_file", [tmp_path / "missing" / "x.txt"])
    window.save_sheet()  # can't be written: says so, doesn't fail
    assert "Couldn't save" in window._status.text()


# -- The plugin ------------------------------------------------------------------ #


class FakeUi:
    def __init__(self) -> None:
        self.parent = QWidget()
        self.actions: dict[tuple[str, str], Callable[[], object]] = {}
        self.shortcuts: list[str | None] = []

    def add_menu_action(
        self, menu: str, text: str, triggered: Callable[[], object], shortcut: str | None = None
    ) -> None:
        self.actions[(menu, text)] = triggered
        self.shortcuts.append(shortcut)

    def add_menu_separator(self, menu: str) -> None:
        pass

    def dialog_parent(self) -> object:
        return self.parent


def status(window: ChordWindow) -> str:
    return window._status.text()


def test_the_plugin_shows_the_chords_of_the_track_playing(
    bus: EventBus,
    player: Player,
    playlist: Playlist,
    feed: AudioFeed,
    decoder: RawDecoder,
    tmp_path: Path,
) -> None:
    ui = FakeUi()
    plugin = ChordsPlugin(lambda: ChordCache(tmp_path / "cache"))
    plugin.activate(PluginContext(bus, player, playlist, ui=lambda: ui, feed=feed))
    assert ui.shortcuts == ["K"]
    path = raw_song(tmp_path / "first.raw")
    play(bus, Track(path, "First", artist="Band"))
    assert decoder.decoded == []  # not while the window is closed

    window = ui.actions[("&View", "&Chords…")]()
    assert isinstance(window, ChordWindow)
    assert window.parent() is ui.parent
    assert window._track.text() == "Band - First"
    wait_until(lambda: window.chart is not None)
    assert window.chart is not None and names(window.chart)[:2] == ["G", "D"]
    assert status(window) == "8 chord changes found."

    play(bus, SONG)  # a song from a server: found as it comes
    assert window.chart is None and "as the song comes in" in status(window)

    play(bus, None)
    assert status(window) == "Nothing is playing."

    window.hide()  # nothing analysed while it's closed
    play(bus, Track.from_path(raw_song(tmp_path / "second.raw")))
    QCoreApplication.processEvents()
    assert decoder.decoded == [path]
    plugin.deactivate()
    assert plugin.window is None


def test_without_a_stream_buffer_streams_are_not_followed(
    bus: EventBus, player: Player, playlist: Playlist, analyser: TrackAnalyser, tmp_path: Path
) -> None:
    feed = AudioFeed(bus, analyser)  # e.g. the Qt backend: nothing to save streams from
    plugin = ChordsPlugin(lambda: ChordCache(tmp_path / "cache"))
    ui = FakeUi()
    plugin.activate(PluginContext(bus, player, playlist, ui=lambda: ui, feed=feed))
    window = plugin.show()
    assert window is not None

    play(bus, RADIO)

    assert status(window) == "Chords of streams need the mpv audio backend."
    plugin.deactivate()
    feed.shutdown()


def test_without_analysis_the_plugin_says_so(
    bus: EventBus, player: Player, playlist: Playlist
) -> None:
    plugin = ChordsPlugin()
    ui = FakeUi()
    plugin.activate(PluginContext(bus, player, playlist, ui=lambda: ui))
    play(bus, Track.from_path(Path("/m/song.mp3")))

    window = plugin.show()

    assert window is not None and "no audio analysis" in status(window)
    plugin.deactivate()


def test_the_plugin_is_in_the_real_view_menu(app: Application) -> None:  # noqa: F811
    plugin = ChordsPlugin()
    app.plugins.register(plugin)
    view = next(a.menu() for a in app.window.menuBar().actions() if a.text() == "&View")
    assert isinstance(view, QMenu)
    assert "&Chords…" in [a.text() for a in view.actions()]
    assert [a.text() for a in app.window.menuBar().actions()].count("&View") == 1
    window = plugin.show()  # the app's own feed serves it
    assert window is not None and status(window) == "Nothing is playing."
    app.plugins.shutdown()


def test_the_bass_notes_choice_is_kept(
    bus: EventBus, player: Player, playlist: Playlist, tmp_path: Path
) -> None:
    settings = ChordSettings(tmp_path / "config")
    assert settings.show_bass  # shown unless turned off
    ui = FakeUi()
    first = ChordsPlugin(settings=lambda: settings)
    first.activate(PluginContext(bus, player, playlist, ui=lambda: ui))
    window = first.show()
    assert window is not None and window.show_bass
    window.bass_box.setChecked(False)
    first.deactivate()

    again = ChordsPlugin(settings=lambda: ChordSettings(tmp_path / "config"))  # next session
    again.activate(PluginContext(bus, player, playlist, ui=lambda: ui))
    window = again.show()
    assert window is not None and not window.show_bass and not window.bass_box.isChecked()
    again.deactivate()


def test_unreadable_chords_settings_are_ignored(tmp_path: Path) -> None:
    settings = ChordSettings(tmp_path)
    settings.file.write_text("not json")
    assert settings.show_bass


def test_seeking_from_the_window_moves_the_player(
    bus: EventBus,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
    feed: AudioFeed,
    tmp_path: Path,
) -> None:
    plugin = ChordsPlugin(lambda: ChordCache(tmp_path / "cache"))
    ui = FakeUi()
    plugin.activate(PluginContext(bus, player, playlist, ui=lambda: ui, feed=feed))
    window = plugin.show()
    assert window is not None

    window.strip.seek_requested.emit(12.5)

    assert backend.position == 12_500
    plugin.deactivate()
