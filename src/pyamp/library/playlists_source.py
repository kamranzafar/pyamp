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
"""The saved playlists, as a source in the library window.

A playlist's files get their tags from the library's index; files outside the library
keep the title and length the playlist gives them. No music file is read here.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pyamp.core.models import Track
from pyamp.library.artwork import file_artwork
from pyamp.library.catalog import Catalog
from pyamp.library.saved_playlists import SavedPlaylists, entry_track
from pyamp.library.sources import Node, NodeAction, matches

type PlaylistCommand = Callable[[str], object]


class PlaylistsSource:
    """Every saved playlist; picking one lists its tracks in playlist order."""

    name = "Playlists"
    hint = "Pick a playlist"

    def __init__(
        self,
        saved: SavedPlaylists,
        catalog: Catalog,
        *,
        open_playlist: PlaylistCommand,
        rename_playlist: PlaylistCommand,
        delete_playlist: PlaylistCommand,
    ) -> None:
        self._saved = saved
        self._catalog = catalog
        # Main-thread commands from the window (they may ask the user first).
        self._open = open_playlist
        self._rename = rename_playlist
        self._delete = delete_playlist

    def roots(self) -> list[Node]:
        nodes = []
        for name in self._saved.names():
            try:
                count = str(len(self._saved.entries(name)))
            except OSError:  # e.g. deleted a moment ago
                continue
            nodes.append(Node(name, name, count))
        return nodes

    def children(self, node: Node) -> list[Node]:
        return []

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        if node is None:
            return []
        try:
            entries = self._saved.entries(str(node.key))
        except OSError:
            return []
        files = [e.location for e in entries if isinstance(e.location, Path)]
        known = self._catalog.tracks_at(files)
        tracks = []
        for entry in entries:
            location = entry.location
            in_library = known.get(location) if isinstance(location, Path) else None
            tracks.append(in_library or entry_track(entry))
        return [track for track in tracks if matches(track, search)]

    def actions(self, node: Node | None) -> list[NodeAction]:
        if node is None:
            return []
        name = str(node.key)
        return [
            NodeAction("&Open in Player", lambda: self._open(name)),
            NodeAction("Re&name…", lambda: self._rename(name)),
            NodeAction("&Delete…", lambda: self._delete(name)),
        ]

    def artwork(self, track: Track) -> bytes | None:
        """A file's picture; streams have none here (a playlist doesn't say where their
        server keeps it)."""
        return file_artwork(track.location) if isinstance(track.location, Path) else None
