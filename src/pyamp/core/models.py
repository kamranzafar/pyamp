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

"""Immutable domain value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from pathlib import Path


class PlaybackState(Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class RepeatMode(StrEnum):
    OFF = "off"
    ALL = "all"
    ONE = "one"

    def cycle(self) -> RepeatMode:
        """Return the next mode, for a single toggle button (off -> all -> one -> off)."""
        modes = list(RepeatMode)
        return modes[(modes.index(self) + 1) % len(modes)]


class ReplayGainMode(StrEnum):
    OFF = "off"
    TRACK = "track"
    ALBUM = "album"


@dataclass(frozen=True, slots=True)
class ReplayGain:
    """ReplayGain values from a file's tags: gains in dB, peaks as linear amplitude."""

    track_gain: float | None = None
    track_peak: float | None = None
    album_gain: float | None = None
    album_peak: float | None = None

    def factor(self, mode: ReplayGainMode, preamp_db: float = 0.0) -> float:
        """Linear volume multiplier for ``mode``; 1.0 if off or untagged.

        Album mode falls back to the track values. The result is capped so the
        loudest sample (``peak``) can't clip.
        """
        if mode is ReplayGainMode.OFF:
            return 1.0
        gain, peak = self.track_gain, self.track_peak
        if mode is ReplayGainMode.ALBUM and self.album_gain is not None:
            gain, peak = self.album_gain, self.album_peak
        if gain is None:
            return 1.0
        factor: float = 10 ** ((gain + preamp_db) / 20)
        if peak:
            factor = min(factor, 1 / peak)
        return factor


@dataclass(frozen=True, slots=True)
class Track:
    """A playable audio file together with its descriptive metadata."""

    path: Path
    title: str
    artist: str = ""
    album: str = ""
    duration_ms: int = 0
    replay_gain: ReplayGain | None = None

    @classmethod
    def from_path(cls, path: Path) -> Track:
        """Create a track with no metadata other than its file name."""
        return cls(path=path, title=path.stem)

    @property
    def display_title(self) -> str:
        """Winamp-style ``Artist - Title`` label."""
        return f"{self.artist} - {self.title}" if self.artist else self.title


def format_duration(ms: int) -> str:
    """Format milliseconds as ``m:ss`` (or ``h:mm:ss`` for long tracks)."""
    total_seconds = max(ms, 0) // 1000
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"
