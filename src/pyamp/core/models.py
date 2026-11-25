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
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import url2pathname

#: URL schemes PyAmp plays as streams (internet radio, media servers).
STREAM_SCHEMES = ("http", "https")


@dataclass(frozen=True, slots=True)
class StreamUrl:
    """An http(s) audio stream: internet radio, or a file on a media server."""

    url: str

    def __post_init__(self) -> None:
        parsed = urlparse(self.url)
        if parsed.scheme.lower() not in STREAM_SCHEMES or not parsed.netloc:
            raise ValueError(f"not an http(s) URL: {self.url!r}")

    @property
    def name(self) -> str:
        """The last part of the URL's path, or the host: for sorting and as a fallback title."""
        parsed = urlparse(self.url)
        segment = unquote(parsed.path.rstrip("/").rsplit("/", 1)[-1])
        return segment or parsed.hostname or self.url

    @property
    def stem(self) -> str:
        name = self.name
        return name.rsplit(".", 1)[0] if "." in name.strip(".") else name

    @property
    def suffix(self) -> str:
        name = self.name
        return f".{name.rsplit('.', 1)[1].lower()}" if "." in name.strip(".") else ""

    def __str__(self) -> str:
        return self.url


#: Where a track's audio comes from: a local file or a stream.
type Location = Path | StreamUrl


def _stream_url(text: str) -> StreamUrl | None:
    try:
        return StreamUrl(text)
    except ValueError:
        return None


def parse_location(text: str, base: Path | StreamUrl | None = None) -> Location | None:
    """A playlist entry or user input as a :data:`Location` (None if it isn't one).

    http(s) URLs become :class:`StreamUrl`; ``file://`` URLs and paths become paths, made
    absolute against ``base`` (a folder, or the URL of a remote playlist). Other URL
    schemes aren't supported.
    """
    text = text.strip()
    lowered = text.lower()
    if not text or lowered.startswith(tuple(f"{scheme}://" for scheme in STREAM_SCHEMES)):
        return _stream_url(text)
    if lowered.startswith("file://"):
        # url2pathname decodes %20 etc. and, on Windows, turns /C:/x into C:\x.
        text = url2pathname(urlparse(text).path)
    elif "://" in text:
        return None
    if isinstance(base, StreamUrl):  # entries of a remote playlist are relative to its URL
        return parse_location(urljoin(base.url, text.replace("\\", "/")))
    candidate = Path(text).expanduser()
    if candidate.is_absolute() or base is None:
        return candidate
    return (base / candidate).resolve()


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
    """Something playable (a file or a stream) together with its descriptive metadata."""

    location: Location
    title: str
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    genre: str = ""
    #: 0 when unknown, as for the numbers below.
    year: int = 0
    track_number: int = 0
    disc_number: int = 0
    duration_ms: int = 0
    replay_gain: ReplayGain | None = None

    @classmethod
    def from_path(cls, path: Path) -> Track:
        """Create a track with no metadata other than its file name."""
        return cls(location=path, title=path.stem)

    @classmethod
    def for_stream(cls, url: StreamUrl, title: str | None = None, duration_ms: int = 0) -> Track:
        """A stream, titled from its playlist entry or else its URL (0 length: live)."""
        return cls(location=url, title=title or url.stem or url.name, duration_ms=duration_ms)

    @property
    def is_stream(self) -> bool:
        return isinstance(self.location, StreamUrl)

    @property
    def name(self) -> str:
        """File name, or the last part of a stream's URL."""
        return self.location.name

    @property
    def uri(self) -> str:
        """``file://`` URI for files, the URL for streams."""
        if isinstance(self.location, StreamUrl):
            return self.location.url
        return self.location.absolute().as_uri()

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
