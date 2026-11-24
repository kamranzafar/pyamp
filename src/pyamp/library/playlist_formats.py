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

"""Playlist file formats (Strategy + Registry).

Each :class:`PlaylistFormat` knows how to read and write one file type. The
:class:`PlaylistFormatRegistry` picks the right one by file extension.
Supporting XSPF later means adding one class and registering it.
"""

from __future__ import annotations

import configparser
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pyamp.core.models import Location, StreamUrl, Track, parse_location


class UnsupportedPlaylistError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PlaylistEntry:
    """One playlist entry: a file or a stream, with the title the playlist gives it."""

    location: Location
    title: str | None = None
    #: The length the playlist gives it (0 if unknown); keeps streams seekable.
    duration_ms: int = 0


def _seconds_to_ms(text: str) -> int:
    """A playlist's length in seconds as milliseconds (0 for "-1", blank or junk)."""
    try:
        seconds = float(text)
    except ValueError:
        return 0
    return int(seconds * 1000) if seconds > 0 else 0


class PlaylistFormat(ABC):
    extensions: ClassVar[tuple[str, ...]]
    description: ClassVar[str]

    def read(self, path: Path) -> list[PlaylistEntry]:
        """The entries of a playlist file, with paths made absolute."""
        return self.parse(path.read_text(encoding="utf-8", errors="replace"), path.parent)

    @abstractmethod
    def parse(self, text: str, base: Path | StreamUrl) -> list[PlaylistEntry]:
        """The entries of a playlist's text; relative entries resolve against ``base``
        (the playlist's folder, or its URL for a playlist downloaded from the web)."""

    @abstractmethod
    def write(self, path: Path, tracks: Sequence[Track]) -> None: ...


class M3UFormat(PlaylistFormat):
    """Extended M3U, always written as UTF-8 (i.e. effectively M3U8)."""

    extensions = (".m3u", ".m3u8")
    description = "M3U playlist"

    def parse(self, text: str, base: Path | StreamUrl) -> list[PlaylistEntry]:
        entries: list[PlaylistEntry] = []
        title: str | None = None
        duration_ms = 0
        for raw in text.splitlines():
            line = raw.strip()
            if line.upper().startswith("#EXTINF:"):
                # "#EXTINF:<seconds>,<title>": the title is whatever follows the comma.
                seconds, _, rest = line[len("#EXTINF:") :].partition(",")
                title = rest.strip() or None
                duration_ms = _seconds_to_ms(seconds.split()[0] if seconds.split() else "")
            elif line and not line.startswith("#"):
                location = parse_location(line, base)
                if location is not None:
                    entries.append(PlaylistEntry(location, title, duration_ms))
                title, duration_ms = None, 0
        return entries

    def write(self, path: Path, tracks: Sequence[Track]) -> None:
        lines = ["#EXTM3U"]
        for track in tracks:
            seconds = track.duration_ms // 1000 if track.duration_ms else -1
            lines.append(f"#EXTINF:{seconds},{track.display_title}")
            lines.append(str(track.location))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PLSFormat(PlaylistFormat):
    extensions = (".pls",)
    description = "PLS playlist"
    _FILE_KEY = re.compile(r"^file(\d+)$", re.IGNORECASE)

    def parse(self, text: str, base: Path | StreamUrl) -> list[PlaylistEntry]:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(text)
        section = next((s for s in parser.sections() if s.lower() == "playlist"), None)
        if section is None:
            return []
        values = {key.lower(): value for key, value in parser.items(section)}
        numbered = sorted(
            (int(m.group(1)), value)
            for key, value in values.items()
            if (m := self._FILE_KEY.match(key))
        )
        entries = []
        for n, entry in numbered:
            location = parse_location(entry, base)
            if location is not None:
                entries.append(
                    PlaylistEntry(
                        location,
                        values.get(f"title{n}") or None,
                        _seconds_to_ms(values.get(f"length{n}", "")),
                    )
                )
        return entries

    def write(self, path: Path, tracks: Sequence[Track]) -> None:
        lines = ["[playlist]"]
        for n, track in enumerate(tracks, start=1):
            lines += [
                f"File{n}={track.location}",
                f"Title{n}={track.display_title}",
                f"Length{n}={track.duration_ms // 1000 if track.duration_ms else -1}",
            ]
        lines += [f"NumberOfEntries={len(tracks)}", "Version=2"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PlaylistFormatRegistry:
    def __init__(self, formats: Iterable[PlaylistFormat]) -> None:
        self._by_extension: dict[str, PlaylistFormat] = {}
        for fmt in formats:
            for ext in fmt.extensions:
                self._by_extension[ext] = fmt

    @classmethod
    def default(cls) -> PlaylistFormatRegistry:
        return cls([M3UFormat(), PLSFormat()])

    def supports(self, path: Path | StreamUrl) -> bool:
        return path.suffix.lower() in self._by_extension

    def for_path(self, path: Path | StreamUrl) -> PlaylistFormat:
        try:
            return self._by_extension[path.suffix.lower()]
        except KeyError:
            raise UnsupportedPlaylistError(f"unsupported playlist type: {path.suffix}") from None

    def read(self, path: Path) -> list[PlaylistEntry]:
        return self.for_path(path).read(path)

    def write(self, path: Path, tracks: Sequence[Track]) -> None:
        self.for_path(path).write(path, tracks)

    def file_dialog_filter(self) -> str:
        """A Qt-style name filter, e.g. ``Playlists (*.m3u *.m3u8 *.pls)``."""
        patterns = " ".join(f"*{ext}" for ext in sorted(self._by_extension))
        return f"Playlists ({patterns})"
