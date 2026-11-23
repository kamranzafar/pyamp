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

"""Turns user input (files, folders, playlist files, stream URLs) into Tracks."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from pyamp.core.models import Location, StreamUrl, Track
from pyamp.library.metadata import MetadataReader, is_audio_file
from pyamp.library.playlist_formats import PlaylistEntry, PlaylistFormatRegistry
from pyamp.library.remote import fetch_text

log = logging.getLogger(__name__)

#: How deep remote playlists may point at further remote playlists.
MAX_REMOTE_DEPTH = 2


class TrackLoader:
    """Synchronous and toolkit-agnostic.

    Holds no mutable state, so it is safe to call from a worker thread. For
    non-blocking use in the GUI, see :mod:`pyamp.ui.background_loader`.
    """

    def __init__(
        self,
        reader: MetadataReader,
        playlist_formats: PlaylistFormatRegistry,
        fetch: Callable[[str], str] = fetch_text,
    ) -> None:
        self._reader = reader
        self._formats = playlist_formats
        self._fetch = fetch

    def load(self, locations: Iterable[Location]) -> list[Track]:
        """Expand folders and playlists, then read metadata for every audio file."""
        return list(self.iter_load(locations))

    def iter_load(self, locations: Iterable[Location]) -> Iterator[Track]:
        """Like :meth:`load`, but lazily: one track at a time, so callers can stream or stop."""
        for item in self.discover(locations):
            yield item if isinstance(item, Track) else self._reader.read(item)

    def discover(self, locations: Iterable[Location]) -> Iterator[Path | Track]:
        """Audio files to read tags from, and ready-made tracks for streams."""
        for location in locations:
            if isinstance(location, StreamUrl):
                yield from self._stream(location, None, depth=0)
            elif location.is_dir():
                yield from sorted(
                    p for p in location.rglob("*") if p.is_file() and is_audio_file(p)
                )
            elif self._formats.supports(location):
                try:
                    entries = self._formats.read(location)
                except OSError as exc:
                    log.warning("Could not read playlist %s: %s", location, exc)
                    continue
                yield from self._entries(entries, depth=0)
            elif location.is_file() and is_audio_file(location):
                yield location

    def _entries(self, entries: Iterable[PlaylistEntry], depth: int) -> Iterator[Path | Track]:
        for entry in entries:
            if isinstance(entry.location, StreamUrl):
                yield from self._stream(entry.location, entry.title, depth)
            elif entry.location.is_file():
                yield entry.location

    def _stream(self, url: StreamUrl, title: str | None, depth: int) -> Iterator[Path | Track]:
        """A stream, or the entries of a remote playlist (how radio stations publish)."""
        if not self._formats.supports(url):
            yield Track.for_stream(url, title)
            return
        if depth >= MAX_REMOTE_DEPTH:
            log.warning("Not following %s: too many nested remote playlists", url)
            return
        try:
            text = self._fetch(url.url)
        except (OSError, ValueError) as exc:
            log.warning("Could not download playlist %s: %s", url, exc)
            return
        entries = self._formats.for_path(url).parse(text, url)
        # A remote playlist may only point at streams, never at files on this computer.
        streams = [e for e in entries if isinstance(e.location, StreamUrl)]
        yield from self._entries(streams, depth + 1)
