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

"""Finds a song's chords from its samples, with numpy alone.

1. **Chroma**: each frame's spectrum (about 1/10 s apart) is folded onto the twelve pitch
   classes, C to B, so a C played in any octave counts as C. It's kept at a fifth of a
   semitone first, so the recording's own tuning is found and the notes taken around it
   (an older record, or one tuned to A = 432 Hz, isn't a quarter-tone "off").
2. **Templates**: each frame is compared with every chord's template (major, minor, 7th,
   minor 7th and major 7th, on all twelve roots). A template counts each note's first
   few harmonics too: a played C also sounds G and E a little.
3. **Beats** (see :mod:`~pyamp.plugins.chords.beats`): the frames' matches are averaged
   over each beat, so the chords change on the beats, and a passing note counts for less.
4. **Smoothing**: a Viterbi pass picks the most likely run of chords, where changing chord
   costs a little, so a passing note or a drum hit doesn't flicker it.
5. **Key**: the song's overall chroma against Krumhansl-Kessler key profiles, used to
   spell the chords (B♭ in F major, A♯ in B major).

Good for clear harmony (most pop, rock and folk). Dense mixes, jazz voicings and heavy
distortion are weaker.
"""

from __future__ import annotations

import bisect
import functools
import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
from numpy.typing import NDArray

from pyamp.plugins.chords.beats import onset_sizes, onset_strength, track_beats

#: The rate the frame sizes below are for (enough for the notes that make chords, below
#: ~2 kHz); at other rates they're scaled (see :func:`frame_sizes`).
SAMPLE_RATE = 11025
FRAME = 4096
HOP = 1024
#: The bands heard: the bass (its lowest note says much about the chord, and no melody
#: is down there), and above it the chords' own notes, up to where they thin out.
BASS_LOW_HZ = 40.0
BASS_HIGH_HZ = 250.0
LOW_HZ = 200.0
HIGH_HZ = 2000.0
#: Above this the upper band counts for less and less (half at it): melodies and vocals
#: sit higher than most of a chord's notes.
TILT_HZ = 900.0
#: How much the bass note counts in a chord's match, and for a chord note other than
#: the root (an inversion: C with E in the bass is still C).
BASS_WEIGHT = 0.15
BASS_OTHER_NOTE = 0.9
#: A frame's bass note counts only if the bass is this loud, against its usual level.
BASS_HEARD = 0.1
#: A chord is shown with its bass note (C/E) when one note other than its root is in the
#: bass for at least this much of it: a held inversion, not a walking bass line.
SLASH_SHARE = 0.65
#: Frames analysed at a time (bounds the memory a long file takes).
BLOCK_FRAMES = 256
#: A gap in the analysis this long (or more) has no chord; a shorter one keeps the chord.
MISSING_RUN_S = 1.0
#: Pitch bins kept per semitone, so the recording's tuning can be found.
FINE = 5
FINE_BINS = 12 * FINE
#: A frame's notes as kept: the upper band's fine pitch bins, then the bass's.
NOTE_COLUMNS = 2 * FINE_BINS
#: A stretch this long without beats (none found there) gets a chord per frame instead.
BEATLESS_S = 2.0
#: The stretch a chord match counts in full for, in the smoothing (a longer one more).
SCORE_SECONDS = 0.186
#: Bumped when the analysis changes, so charts found by an older one are found again.
VERSION = 5

SHARP_NAMES = ("C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B")
FLAT_NAMES = ("C", "D♭", "D", "E♭", "E", "F", "G♭", "G", "A♭", "A", "B♭", "B")

#: Chord qualities, by the suffix they're written with, and their notes above the root.
QUALITIES: Mapping[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "m": (0, 3, 7),
    "7": (0, 4, 7, 10),
    "m7": (0, 3, 7, 10),
    "maj7": (0, 4, 7, 11),
}
#: Four-note chords need a clearly heard seventh to win over the triad they contain.
SEVENTH_WEIGHT = 0.97
#: How well a frame must match a chord to beat "no chord".
NO_CHORD_SCORE = 0.55
#: What changing chord costs the smoothing, in template-match units.
SWITCH_COST = 0.3
#: A frame quieter than this, relative to the loudest, is silence (no chord).
SILENCE = 0.02

#: Krumhansl-Kessler key profiles, from the tonic up.
_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
#: Keys written with flats: major tonics F, B♭, E♭, A♭, D♭, G♭; minor D, G, C, F, B♭, E♭.
_FLAT_MAJOR = frozenset({5, 10, 3, 8, 1, 6})
_FLAT_MINOR = frozenset({2, 7, 0, 5, 10, 3})


@dataclass(frozen=True, slots=True)
class Chord:
    root: int  # pitch class, 0 = C
    quality: str = ""  # a key of QUALITIES
    #: The note in the bass, when it isn't the root (C/E): a pitch class, or None.
    bass: int | None = None

    def transposed(self, semitones: int) -> Chord:
        bass = (self.bass + semitones) % 12 if self.bass is not None else None
        return Chord((self.root + semitones) % 12, self.quality, bass)

    def name(self, *, flats: bool = False, bass: bool = True) -> str:
        """E.g. "Am7", or "C/E" with its bass note (unless ``bass`` is False)."""
        names = FLAT_NAMES if flats else SHARP_NAMES
        below = f"/{names[self.bass]}" if bass and self.bass is not None else ""
        return names[self.root] + self.quality + below

    def bass_name(self, *, flats: bool = False) -> str | None:
        """The bass note's name ("E" in C/E), if it has one."""
        return (FLAT_NAMES if flats else SHARP_NAMES)[self.bass] if self.bass is not None else None

    def notes(self) -> tuple[int, ...]:
        return tuple((self.root + step) % 12 for step in QUALITIES[self.quality])


@dataclass(frozen=True, slots=True)
class Key:
    tonic: int
    minor: bool = False

    @property
    def flats(self) -> bool:
        return self.tonic in (_FLAT_MINOR if self.minor else _FLAT_MAJOR)

    def transposed(self, semitones: int) -> Key:
        return Key((self.tonic + semitones) % 12, self.minor)

    def name(self) -> str:
        tonic = (FLAT_NAMES if self.flats else SHARP_NAMES)[self.tonic]
        return f"{tonic} {'minor' if self.minor else 'major'}"


@dataclass(frozen=True, slots=True)
class ChordSegment:
    start: float  # seconds
    end: float
    chord: Chord | None  # None: no chord (silence, or nothing clear)


@dataclass(frozen=True, slots=True)
class ChordChart:
    """A song's chords, in order, with the times they play."""

    segments: tuple[ChordSegment, ...]
    key: Key | None = None

    @property
    def duration(self) -> float:
        return self.segments[-1].end if self.segments else 0.0

    def index_at(self, seconds: float) -> int | None:
        """The segment playing at ``seconds``, if any."""
        if not self.segments or seconds < 0 or seconds >= self.duration:
            return None
        starts = [segment.start for segment in self.segments]
        return max(0, bisect.bisect_right(starts, seconds) - 1)

    def transposed(self, semitones: int) -> ChordChart:
        if semitones % 12 == 0:
            return self
        return ChordChart(
            tuple(
                ChordSegment(s.start, s.end, s.chord.transposed(semitones) if s.chord else None)
                for s in self.segments
            ),
            self.key.transposed(semitones) if self.key else None,
        )

    @property
    def flats(self) -> bool:
        return self.key.flats if self.key else False

    def chord_name(self, chord: Chord | None, *, bass: bool = True) -> str:
        return chord.name(flats=self.flats, bass=bass) if chord else "N.C."

    def sheet(self, title: str = "", *, bass: bool = True) -> str:
        """The chart as plain text: each chord with the time it starts (and its bass
        note, as C/E, unless ``bass`` is False)."""
        lines = [title] if title else []
        if self.key:
            lines.append(f"Key: {self.key.name()}")
        if lines:
            lines.append("")
        lines += [
            f"{_clock(s.start)}  {self.chord_name(s.chord, bass=bass)}"
            for s in self.segments
            if s.chord
        ]
        return "\n".join(lines) + "\n"

    def to_json(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "key": [self.key.tonic, self.key.minor] if self.key else None,
            "segments": [[s.start, s.end, *_chord_json(s.chord)] for s in self.segments],
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> ChordChart | None:
        """The chart saved by :meth:`to_json`; None if it's from another version, or broken."""
        try:
            if data.get("version") != VERSION:
                return None
            key = data.get("key")
            segments = []
            for item in data["segments"]:
                chord = None
                if len(item) in (4, 5):
                    if item[3] not in QUALITIES:
                        return None
                    bass = int(item[4]) % 12 if len(item) == 5 else None
                    chord = Chord(int(item[2]) % 12, str(item[3]), bass)
                segments.append(ChordSegment(float(item[0]), float(item[1]), chord))
            return cls(tuple(segments), Key(int(key[0]) % 12, bool(key[1])) if key else None)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            return None


def _chord_json(chord: Chord | None) -> list[Any]:
    if chord is None:
        return []
    return [chord.root, chord.quality] + ([chord.bass] if chord.bass is not None else [])


def _clock(seconds: float) -> str:
    whole = int(seconds)
    return f"{whole // 60}:{whole % 60:02d}"


# -- Analysis ---------------------------------------------------------------- #

#: Every chord the analysis knows, in template order.
CHORDS: tuple[Chord, ...] = tuple(Chord(root, q) for q in QUALITIES for root in range(12))


def _note_profile() -> NDArray[np.float64]:
    """How a note sounds across the pitch classes: its harmonics, each a little quieter."""
    profile = np.zeros(12)
    for harmonic in range(1, 7):
        step = round(12 * np.log2(harmonic)) % 12
        profile[step] += 0.6 ** (harmonic - 1)
    return profile


def _templates() -> NDArray[np.float64]:
    note = _note_profile()
    rows = []
    for chord in CHORDS:
        row = np.zeros(12)
        for pitch in chord.notes():
            row += np.roll(note, pitch)
        row -= row.min()
        rows.append(row / np.linalg.norm(row))
    return np.array(rows)


_TEMPLATES = _templates()
_WEIGHTS = np.array([SEVENTH_WEIGHT if len(QUALITIES[c.quality]) > 3 else 1.0 for c in CHORDS])


@functools.cache
def frame_sizes(sample_rate: int) -> tuple[int, int]:
    """Samples per frame (about 0.37 s: fine enough to tell low notes apart) and between
    frames (about 0.09 s: several to a beat), at ``sample_rate``."""
    scale = max(1, round(sample_rate / SAMPLE_RATE))
    return FRAME * scale, HOP * scale


def _band_fold(
    freqs: NDArray[np.float64], low: float, high: float, tilt_hz: float | None
) -> tuple[NDArray[np.intp], NDArray[np.float64]]:
    """A band's spectrum bins, and the matrix folding them onto :data:`FINE_BINS` pitch
    bins (each shared between the two nearest), fading above ``tilt_hz`` if given."""
    bins = np.nonzero((freqs >= low) & (freqs <= high))[0]
    position = (12 * np.log2(freqs[bins] / 440.0) + 69) * FINE  # in fine bins from C-1
    below = np.floor(position)
    share = position - below
    weight = 1 / (1 + (freqs[bins] / tilt_hz) ** 2) if tilt_hz else np.ones(bins.size)
    fold = np.zeros((bins.size, FINE_BINS))
    rows = np.arange(bins.size)
    np.add.at(fold, (rows, below.astype(np.intp) % FINE_BINS), (1 - share) * weight)
    np.add.at(fold, (rows, (below.astype(np.intp) + 1) % FINE_BINS), share * weight)
    return bins, fold


@functools.cache
def _pitch_fold(sample_rate: int) -> tuple[NDArray[np.intp], NDArray[np.float64], NDArray[Any]]:
    """The spectrum's bins used, the matrix folding them onto :data:`NOTE_COLUMNS` (the
    upper band's pitch bins, then the bass's), and the frame's window."""
    frame, _hop = frame_sizes(sample_rate)
    freqs = np.fft.rfftfreq(frame, 1 / sample_rate)
    upper, upper_fold = _band_fold(freqs, LOW_HZ, HIGH_HZ, TILT_HZ)
    bass, bass_fold = _band_fold(freqs, BASS_LOW_HZ, BASS_HIGH_HZ, None)
    bins = np.union1d(upper, bass)
    fold = np.zeros((bins.size, NOTE_COLUMNS))
    fold[np.searchsorted(bins, upper), :FINE_BINS] = upper_fold
    fold[np.searchsorted(bins, bass), FINE_BINS:] = bass_fold
    return bins, fold, np.hanning(frame)


def chroma_frames(
    frames: NDArray[Any], sample_rate: int
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Frames' energy in each fine pitch bin, upper band then bass (frames x
    :data:`NOTE_COLUMNS`), and their loudness, for frames of :func:`frame_sizes`
    samples (one per row)."""
    bins, fold, window = _pitch_fold(sample_rate)
    block = np.asarray(frames, dtype=np.float64)
    spectrum = np.abs(np.fft.rfft(block * window, axis=1))[:, bins]
    fine = ((spectrum**2) @ fold).astype(np.float32)
    return fine, np.sqrt(np.mean(block**2, axis=1)).astype(np.float32)


def estimate_tuning(fine: NDArray[Any]) -> float:
    """How far the recording's notes sit from standard pitch (A = 440 Hz), in semitones
    (-0.5..0.5): where in each semitone the energy gathers, on average."""
    total = np.asarray(fine, dtype=np.float64).sum(axis=0)
    by_offset = total.reshape(-1, 12, FINE).sum(axis=(0, 1))  # both bands alike
    turns = np.sum(by_offset * np.exp(2j * np.pi * np.arange(FINE) / FINE))
    if abs(turns) < 1e-12:
        return 0.0
    return float(np.angle(turns) / (2 * np.pi))


@functools.cache
def _tuned_fold(cents: int) -> NDArray[np.float64]:
    """Fine pitch bins onto the twelve notes, for notes ``cents`` off standard pitch."""
    pitch = np.arange(FINE_BINS) / FINE - cents / 100  # each fine bin, in tuned semitones
    distance = (pitch[:, None] - np.arange(12)[None, :] + 6) % 12 - 6
    return np.where(np.abs(distance) < 0.5, np.cos(np.pi * distance) ** 2, 0.0)


def fold(fine: NDArray[Any], tuning: float = 0.0) -> NDArray[np.float64]:
    """Each frame's twelve notes (not normalised), for a recording ``tuning`` semitones off."""
    weights = _tuned_fold(round(tuning * 100))
    return np.sqrt(np.asarray(fine, dtype=np.float64) @ weights)


def chromagram(
    samples: NDArray[Any],
    sample_rate: int = SAMPLE_RATE,
    cancelled: Callable[[], bool] = lambda: False,
) -> tuple[NDArray[np.float32], NDArray[np.float32]]:
    """Each frame's fine pitch energy (see :func:`chroma_frames`), and its loudness."""
    frame, hop = frame_sizes(sample_rate)
    audio = _padded(samples, frame)
    frames = np.lib.stride_tricks.sliding_window_view(audio, frame)[::hop]
    fine = np.empty((len(frames), NOTE_COLUMNS), dtype=np.float32)
    loudness = np.empty(len(frames), dtype=np.float32)
    for start in range(0, len(frames), BLOCK_FRAMES):
        if cancelled():
            raise AnalysisCancelledError
        block = slice(start, start + BLOCK_FRAMES)
        fine[block], loudness[block] = chroma_frames(frames[block], sample_rate)
    return fine, loudness


def onset_envelope(samples: NDArray[Any], sample_rate: int = SAMPLE_RATE) -> NDArray[np.float32]:
    """The onset strength (see :mod:`~pyamp.plugins.chords.beats`) through ``samples``."""
    frame, hop = onset_sizes(sample_rate)
    frames = np.lib.stride_tricks.sliding_window_view(_padded(samples, frame), frame)[::hop]
    parts, previous = [], None
    for start in range(0, len(frames), 8 * BLOCK_FRAMES):
        flux, previous = onset_strength(frames[start : start + 8 * BLOCK_FRAMES], previous)
        parts.append(flux)
    return np.concatenate(parts)


def _padded(samples: NDArray[Any], size: int) -> NDArray[np.float64]:
    audio = np.asarray(samples, dtype=np.float64)
    return np.pad(audio, (0, size - audio.size)) if audio.size < size else audio


class AnalysisCancelledError(Exception):
    """The analysis was stopped (e.g. another track started)."""


def _normalised(chroma: NDArray[np.float64]) -> NDArray[np.float64]:
    """Each frame's chroma as a direction: log-compressed (so one loud note doesn't
    drown the rest), its floor removed (noise counts alike for every pitch), unit length."""
    peak = chroma.max(initial=0.0) or 1.0
    shaped = np.log1p(100 * chroma / peak)
    shaped -= shaped.min(axis=1, keepdims=True, initial=np.inf) if len(shaped) else 0.0
    norms = np.linalg.norm(shaped, axis=1, keepdims=True)
    return np.divide(shaped, norms, out=np.zeros_like(shaped), where=norms > 1e-9)


def _bass_expectations() -> NDArray[np.float64]:
    """For each chord (rows) and bass note (columns), how well the note fits: its root
    best, its other notes (an inversion) nearly as well, anything else not at all."""
    return np.array(
        [
            [
                1.0 if p == c.root else (BASS_OTHER_NOTE if p in c.notes() else 0.0)
                for p in range(12)
            ]
            for c in CHORDS
        ]
    )


_BASS_FITS = _bass_expectations()


def bass_notes(bass: NDArray[np.float64]) -> tuple[NDArray[np.intp], NDArray[np.bool_]]:
    """Each frame's bass note (its strongest low pitch), and whether there's a bass to
    hear in it at all (against how loud the bass usually is)."""
    peak = bass.max(axis=1)
    usual = float(np.median(peak[peak > 0])) if (peak > 0).any() else 0.0
    return bass.argmax(axis=1), (peak > BASS_HEARD * usual) & (peak > 0)


def chord_scores(
    chroma: NDArray[np.float64],
    loudness: NDArray[Any],
    bass: NDArray[np.float64] | None = None,
) -> NDArray[np.float64]:
    """How well each frame matches each chord (frames x chords), "no chord" last: its
    notes against the chord's, and, where there's a ``bass`` (frames x 12), its bass
    note against the notes the chord can have there."""
    unit = _normalised(chroma)
    matches = (unit @ _TEMPLATES.T) * _WEIGHTS
    silent = (loudness < SILENCE * (loudness.max(initial=0.0) or 1.0)) | ~unit.any(axis=1)
    if bass is not None and len(bass):
        note, heard = bass_notes(bass)
        heard &= ~silent
        matches[heard] = (1 - BASS_WEIGHT) * matches[heard] + BASS_WEIGHT * _BASS_FITS[
            :, note[heard]
        ].T
    matches[silent] = 0.0
    none = np.where(silent, 1.0, NO_CHORD_SCORE)
    return np.column_stack([matches, none])


def smooth(scores: NDArray[np.float64], switch_cost: float = SWITCH_COST) -> NDArray[np.intp]:
    """The best run of chords (a Viterbi path), where each change costs ``switch_cost``."""
    frames, states = scores.shape
    if frames == 0:
        return np.zeros(0, dtype=np.intp)
    back = np.empty((frames, states), dtype=np.intp)
    back[0] = np.arange(states)
    total = scores[0].copy()
    stay = np.arange(states)
    for t in range(1, frames):
        best = int(total.argmax())
        switched = total[best] - switch_cost
        change = switched > total
        back[t] = np.where(change, best, stay)
        total = np.where(change, switched, total) + scores[t]
    path = np.empty(frames, dtype=np.intp)
    path[-1] = int(total.argmax())
    for t in range(frames - 1, 0, -1):
        path[t - 1] = back[t, path[t]]
    return path


def find_key(chroma: NDArray[np.float64], loudness: NDArray[Any]) -> Key | None:
    """The key whose profile best matches the song's overall chroma."""
    heard = loudness >= SILENCE * (loudness.max(initial=0.0) or 1.0)
    profile = _normalised(chroma)[heard].sum(axis=0)
    if not profile.any():
        return None
    best: tuple[float, Key] | None = None
    for minor, reference in ((False, _MAJOR_PROFILE), (True, _MINOR_PROFILE)):
        for tonic in range(12):
            fit = float(np.corrcoef(profile, np.roll(reference, tonic))[0, 1])
            if best is None or fit > best[0]:
                best = (fit, Key(tonic, minor))
    return best[1] if best else None


@dataclass(frozen=True, slots=True)
class Frames:
    """What was heard through a track (or the part of it analysed): each frame's fine
    pitch energy and loudness from frame ``first`` on, and its onsets for the beats."""

    fine: NDArray[Any]  # frames x NOTE_COLUMNS
    loudness: NDArray[Any]
    end: float  # seconds: how far into the track the audio goes
    sample_rate: int = SAMPLE_RATE
    first: int = 0
    #: Which frames were analysed (None: all); the others don't vote.
    present: NDArray[np.bool_] | None = None
    onsets: NDArray[Any] | None = None
    onsets_first: int = 0


def _frame_edge(frame: int | NDArray[np.intp], sample_rate: int) -> Any:
    """Where frame ``frame``'s stretch of time begins (between it and the one before)."""
    size, hop = frame_sizes(sample_rate)
    return (frame * hop + (size - hop) / 2) / sample_rate


def _frame_centre(frame: NDArray[np.intp], sample_rate: int) -> NDArray[np.float64]:
    size, hop = frame_sizes(sample_rate)
    centre: NDArray[np.float64] = (frame * hop + size / 2) / sample_rate
    return centre


def beat_times(frames: Frames) -> NDArray[np.float64]:
    """When the beats fall (seconds into the track); none if there's no clear beat."""
    if frames.onsets is None or not len(frames.onsets):
        return np.zeros(0)
    size, hop = onset_sizes(frames.sample_rate)
    beats = track_beats(frames.onsets, hop / frames.sample_rate)
    times: NDArray[np.float64] = ((frames.onsets_first + beats) * hop + size / 2) / (
        frames.sample_rate
    )
    return times


def _edges(frames: Frames, beats: NDArray[np.float64]) -> NDArray[np.float64]:
    """Where the stretches that each get one chord begin and end: the beats, or, where
    there are none (no clear beat, or a long stretch without them), the frames."""
    count = len(frames.fine)
    start = 0.0 if frames.first == 0 else float(_frame_edge(frames.first, frames.sample_rate))
    between_frames = _frame_edge(frames.first + np.arange(1, count), frames.sample_rate)
    points = [start, *beats[(beats > start) & (beats < frames.end)], frames.end]
    edges = [np.asarray(points)]
    for a, b in itertools.pairwise(points):
        if b - a > BEATLESS_S:  # no beats here: chords may change at any frame
            edges.append(between_frames[(between_frames > a) & (between_frames < b)])
    joined = np.unique(np.concatenate(edges))
    return joined[(joined >= start) & (joined <= frames.end)]


def chart_from_frames(frames: Frames) -> ChordChart:
    """The chart for what was heard. Its notes are taken at the recording's own tuning;
    its chords change on the beats where it has some. Frames not analysed (e.g. where a
    stream's pieces meet) don't vote: a short gap keeps the chord around it; a long one
    has no chord."""
    count = len(frames.fine)
    present = frames.present if frames.present is not None else np.ones(count, dtype=bool)
    if not present.any():
        return ChordChart(())
    tuning = estimate_tuning(frames.fine[present])
    chroma = fold(frames.fine[:, :FINE_BINS], tuning)
    bass = fold(frames.fine[:, FINE_BINS:], tuning)
    scores = np.zeros((count, len(CHORDS) + 1))
    scores[present] = chord_scores(chroma[present], frames.loudness[present], bass[present])
    edges = _edges(frames, beat_times(frames))
    stretches = len(edges) - 1
    if stretches < 1:
        return ChordChart(())
    centres = _frame_centre(frames.first + np.arange(count), frames.sample_rate)
    which = np.clip(np.searchsorted(edges, centres, side="right") - 1, 0, stretches - 1)
    sums = np.zeros((stretches, len(CHORDS) + 1))
    np.add.at(sums, which[present], scores[present])
    heard = np.bincount(which[present], minlength=stretches)
    lengths = np.diff(edges)
    # Each stretch's average match, weighted by how long it is: a change costs the same
    # whether the chords change on beats or frames.
    emissions = sums / np.maximum(heard, 1)[:, None] * (lengths / SCORE_SECONDS)[:, None]
    path = smooth(emissions)
    _no_chord_in_long_gaps(path, heard == 0, lengths)
    segments = _with_bass_notes(_segments(path, edges), bass, present, centres)
    return ChordChart(segments, find_key((chroma + bass)[present], frames.loudness[present]))


def _with_bass_notes(
    segments: tuple[ChordSegment, ...],
    bass: NDArray[np.float64],
    present: NDArray[np.bool_],
    centres: NDArray[np.float64],
) -> tuple[ChordSegment, ...]:
    """Each chord with the note in its bass, where one other than the root holds most of
    it (:data:`SLASH_SHARE`): a held inversion shows (C/E); a walking bass doesn't."""
    notes, heard = bass_notes(bass)
    marked = []
    for segment in segments:
        chord = segment.chord
        inside = present & (centres >= segment.start) & (centres < segment.end)
        played = notes[inside & heard]
        if chord is not None and len(played) * 2 >= inside.sum() > 0:
            votes = np.bincount(played, minlength=12)
            note = int(votes.argmax())
            if note != chord.root and votes[note] >= SLASH_SHARE * inside.sum():
                chord = replace(chord, bass=note)
        marked.append(ChordSegment(segment.start, segment.end, chord))
    return tuple(marked)


def _no_chord_in_long_gaps(
    path: NDArray[np.intp], unheard: NDArray[np.bool_], lengths: NDArray[np.float64]
) -> None:
    run_start = None
    for index in range(len(path) + 1):
        missing = index < len(path) and bool(unheard[index])
        if missing and run_start is None:
            run_start = index
        elif not missing and run_start is not None:
            if lengths[run_start:index].sum() >= MISSING_RUN_S:
                path[run_start:index] = len(CHORDS)  # no chord
            run_start = None


def _segments(path: NDArray[np.intp], edges: NDArray[np.float64]) -> tuple[ChordSegment, ...]:
    """Runs of the same chord over the stretches between ``edges``."""
    segments: list[ChordSegment] = []
    run = 0
    for index in range(1, len(path) + 1):
        if index < len(path) and path[index] == path[run]:
            continue
        state = int(path[run])
        chord = CHORDS[state] if state < len(CHORDS) else None
        start, stop = round(float(edges[run]), 3), round(float(edges[index]), 3)  # as kept
        if stop > start:
            segments.append(ChordSegment(start, stop, chord))
        run = index
    return tuple(segments)


def find_chords(
    samples: NDArray[Any],
    sample_rate: int = SAMPLE_RATE,
    cancelled: Callable[[], bool] = lambda: False,
) -> ChordChart:
    """A song's chords from its mono samples (any scale: int16 or floats)."""
    if samples.size == 0:
        return ChordChart(())
    fine, loudness = chromagram(samples, sample_rate, cancelled)
    onsets = onset_envelope(samples, sample_rate)
    if cancelled():
        raise AnalysisCancelledError
    return chart_from_frames(
        Frames(fine, loudness, samples.size / sample_rate, sample_rate, onsets=onsets)
    )
