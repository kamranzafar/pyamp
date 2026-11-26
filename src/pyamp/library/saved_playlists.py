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
"""Named playlists the user keeps, stored as one ``.m3u8`` file each.

No Qt here: :class:`pyamp.ui.playlist_session.PlaylistSession` decides when to save.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import platformdirs

from pyamp import APP_NAME, files
from pyamp.core.models import Location, StreamUrl, Track
from pyamp.library.playlist_formats import M3UFormat, PlaylistEntry

SUFFIX = ".m3u8"
MAX_NAME_LENGTH = 100
_FORBIDDEN = set('/\\:*?"<>|')


class PlaylistNameError(ValueError):
    """A playlist name that can't be used; the message is meant for the user."""


def default_directory() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME.lower())) / "playlists"


def validate_name(name: str) -> str:
    """The name, trimmed, if it can be a playlist name (and a file name)."""
    name = name.strip()
    if not name:
        raise PlaylistNameError("A playlist needs a name.")
    if len(name) > MAX_NAME_LENGTH:
        raise PlaylistNameError(f"Playlist names can be at most {MAX_NAME_LENGTH} characters.")
    if name.startswith(".") or _FORBIDDEN & set(name) or any(ord(c) < 32 for c in name):
        raise PlaylistNameError(
            'Playlist names can\'t start with "." or contain / \\ : * ? " < > |.'
        )
    return name


def entry_track(entry: PlaylistEntry) -> Track:
    """A track from what a playlist says about it (no tags are read)."""
    location = entry.location
    if isinstance(location, StreamUrl):
        return Track.for_stream(location, entry.title, entry.duration_ms)
    return Track(location, entry.title or location.stem, duration_ms=entry.duration_ms)


class SavedPlaylists:
    """The playlists in one directory. Names compare case-insensitively."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_directory()
        self._format = M3UFormat()

    def names(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        files = (p for p in self.directory.iterdir() if p.suffix == SUFFIX and p.is_file())
        return sorted((p.stem for p in files), key=str.casefold)

    def exists(self, name: str) -> bool:
        return self._find(name) is not None

    def path(self, name: str) -> Path:
        return self._find(name) or self.directory / f"{validate_name(name)}{SUFFIX}"

    def create(self, base: str = "New playlist") -> str:
        """Create an empty playlist named ``base`` (or "base 2", ...) and return its name."""
        base = validate_name(base)
        name, n = base, 1
        while self.exists(name):
            n += 1
            name = f"{base} {n}"
        self.save(name, [])
        return name

    def save(self, name: str, tracks: Sequence[Track]) -> None:
        files.write_with(self.path(name), lambda tmp: self._format.write(tmp, tracks))

    def read_paths(self, name: str) -> list[Location]:
        return [entry.location for entry in self.entries(name)]

    def entries(self, name: str) -> list[PlaylistEntry]:
        """What the playlist lists, with the titles and lengths it gives them."""
        found = self._find(name)
        if found is None:
            raise FileNotFoundError(f"no saved playlist {name!r}")
        # Retried: on Windows a save replacing the file (an auto-save, on its own thread)
        # makes opening it fail for a moment.
        return files.retry_while_locked(lambda: self._format.read(found))

    def rename(self, old: str, new: str) -> str:
        source = self._find(old)
        if source is None:
            raise FileNotFoundError(f"no saved playlist {old!r}")
        new = validate_name(new)
        clash = self._find(new)
        if clash is not None and clash != source:
            raise PlaylistNameError(f'There is already a playlist called "{clash.stem}".')
        files.retry_while_locked(lambda: source.rename(self.directory / f"{new}{SUFFIX}"))
        return new

    def delete(self, name: str) -> None:
        found = self._find(name)
        if found is not None:
            files.retry_while_locked(found.unlink)

    def _find(self, name: str) -> Path | None:
        wanted = name.strip().casefold()
        return next(
            (
                self.directory / f"{stem}{SUFFIX}"
                for stem in self.names()
                if stem.casefold() == wanted
            ),
            None,
        )
