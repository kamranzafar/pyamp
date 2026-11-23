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


@dataclass(frozen=True, slots=True)
class Track:
    """A playable audio file together with its descriptive metadata."""

    path: Path
    title: str
    artist: str = ""
    album: str = ""
    duration_ms: int = 0

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
