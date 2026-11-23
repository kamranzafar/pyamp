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
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse
from urllib.request import url2pathname

from pyamp.core.models import Track


class UnsupportedPlaylistError(ValueError):
    pass


class PlaylistFormat(ABC):
    extensions: ClassVar[tuple[str, ...]]
    description: ClassVar[str]

    @abstractmethod
    def read(self, path: Path) -> list[Path]:
        """Return the absolute paths of the entries in the playlist file."""

    @abstractmethod
    def write(self, path: Path, tracks: Sequence[Track]) -> None: ...

    @staticmethod
    def _resolve(entry: str, base: Path) -> Path | None:
        entry = entry.strip()
        if entry.startswith("file://"):
            # url2pathname decodes %20 etc. and, on Windows, turns /C:/x into C:\x.
            entry = url2pathname(urlparse(entry).path)
        if not entry or "://" in entry:  # skip blanks and stream URLs for now
            return None
        candidate = Path(entry).expanduser()
        return candidate if candidate.is_absolute() else (base / candidate).resolve()


class M3UFormat(PlaylistFormat):
    """Extended M3U, always written as UTF-8 (i.e. effectively M3U8)."""

    extensions = (".m3u", ".m3u8")
    description = "M3U playlist"

    def read(self, path: Path) -> list[Path]:
        text = path.read_text(encoding="utf-8", errors="replace")
        entries = (line for line in text.splitlines() if not line.startswith("#"))
        return [p for e in entries if (p := self._resolve(e, path.parent))]

    def write(self, path: Path, tracks: Sequence[Track]) -> None:
        lines = ["#EXTM3U"]
        for track in tracks:
            lines.append(f"#EXTINF:{track.duration_ms // 1000},{track.display_title}")
            lines.append(str(track.path))
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class PLSFormat(PlaylistFormat):
    extensions = (".pls",)
    description = "PLS playlist"
    _FILE_KEY = re.compile(r"^file(\d+)$", re.IGNORECASE)

    def read(self, path: Path) -> list[Path]:
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"))
        section = next((s for s in parser.sections() if s.lower() == "playlist"), None)
        if section is None:
            return []
        numbered = sorted(
            (int(m.group(1)), value)
            for key, value in parser.items(section)
            if (m := self._FILE_KEY.match(key))
        )
        return [p for _, entry in numbered if (p := self._resolve(entry, path.parent))]

    def write(self, path: Path, tracks: Sequence[Track]) -> None:
        lines = ["[playlist]"]
        for n, track in enumerate(tracks, start=1):
            lines += [
                f"File{n}={track.path}",
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

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in self._by_extension

    def for_path(self, path: Path) -> PlaylistFormat:
        try:
            return self._by_extension[path.suffix.lower()]
        except KeyError:
            raise UnsupportedPlaylistError(f"unsupported playlist type: {path.suffix}") from None

    def read(self, path: Path) -> list[Path]:
        return self.for_path(path).read(path)

    def write(self, path: Path, tracks: Sequence[Track]) -> None:
        self.for_path(path).write(path, tracks)

    def file_dialog_filter(self) -> str:
        """A Qt-style name filter, e.g. ``Playlists (*.m3u *.m3u8 *.pls)``."""
        patterns = " ".join(f"*{ext}" for ext in sorted(self._by_extension))
        return f"Playlists ({patterns})"
