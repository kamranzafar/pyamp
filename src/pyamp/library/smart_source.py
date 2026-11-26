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

"""The smart playlists, as a source in the library window.

Each lists the library's tracks that match its rules right now, so it follows scans
without being saved again. Only the local library is searched.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pyamp.core.models import Track
from pyamp.library.artwork import file_artwork
from pyamp.library.catalog import Catalog
from pyamp.library.smart_playlists import SmartPlaylists
from pyamp.library.sources import Node, NodeAction, matches

type SmartCommand = Callable[[str], object]


class SmartPlaylistsSource:
    """Every smart playlist, with how many tracks it lists; picking one lists them."""

    name = "Smart Playlists"
    hint = "Right-click Smart Playlists to make one"

    def __init__(
        self,
        store: SmartPlaylists,
        catalog: Catalog,
        *,
        new_playlist: Callable[[], object],
        edit_playlist: SmartCommand,
        duplicate_playlist: SmartCommand,
        delete_playlist: SmartCommand,
    ) -> None:
        self._store = store
        self._catalog = catalog
        # Main-thread commands from the window (they show dialogs).
        self._new = new_playlist
        self._edit = edit_playlist
        self._duplicate = duplicate_playlist
        self._delete = delete_playlist

    def roots(self) -> list[Node]:
        nodes = []
        for name in self._store.names():
            if (playlist := self._store.get(name)) is not None:
                nodes.append(Node(name, name, str(self._catalog.smart_count(playlist))))
        return nodes

    def children(self, node: Node) -> list[Node]:
        return []

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        playlist = self._store.get(str(node.key)) if node is not None else None
        if playlist is None:
            return []
        return [t for t in self._catalog.smart_tracks(playlist) if matches(t, search)]

    def actions(self, node: Node | None) -> list[NodeAction]:
        if node is None:
            return [NodeAction("&New Smart Playlist…", self._new)]
        name = str(node.key)
        return [
            NodeAction("&Edit…", lambda: self._edit(name)),
            NodeAction("D&uplicate", lambda: self._duplicate(name)),
            NodeAction("&Delete…", lambda: self._delete(name)),
        ]

    def artwork(self, track: Track) -> bytes | None:
        return file_artwork(track.location) if isinstance(track.location, Path) else None
