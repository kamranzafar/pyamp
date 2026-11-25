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
"""The music on this computer (the folders and files added to the library), as a source."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from pyamp.core.models import Track
from pyamp.library.catalog import Catalog, Group, GroupKind
from pyamp.library.sources import Node, NodeAction

CATEGORIES = (
    (GroupKind.ARTIST, "Artists"),
    (GroupKind.ALBUM, "Albums"),
    (GroupKind.GENRE, "Genres"),
    (GroupKind.FOLDER, "Folders"),
)


def group_name(group: Group) -> str:
    """How an artist, album, genre or folder is named in the sidebar."""
    match group.kind:
        case GroupKind.ARTIST:
            return group.name or "Unknown Artist"
        case GroupKind.ALBUM:
            return f"{group.name} — {group.artist}" if group.artist else group.name
        case GroupKind.FOLDER:
            return Path(group.name).name or group.name
        case GroupKind.GENRE:
            return group.name


class LocalSource:
    """Browsed by artist, album, genre or folder; the whole source is every track."""

    name = "This Computer"
    hint = "Add a folder of music to start your library"

    def __init__(self, catalog: Catalog, remove_source: Callable[[Path], object]) -> None:
        self._catalog = catalog
        self._remove_source = remove_source  # also rescans and refreshes; main thread

    def roots(self) -> list[Node]:
        return [Node(kind, title, expandable=True, has_tracks=False) for kind, title in CATEGORIES]

    def children(self, node: Node) -> list[Node]:
        if not isinstance(node.key, GroupKind):
            return []
        return [
            Node(
                Group(group.kind, group.name, group.artist),  # without the count: a stable key
                group_name(group),
                detail=str(group.count),
                tooltip=group.name if group.kind is GroupKind.FOLDER else "",
            )
            for group in self._catalog.groups(node.key)
        ]

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        if node is None:
            return self._catalog.tracks(search)
        if isinstance(node.key, Group):
            return self._catalog.tracks(search, node.key)
        return []

    def actions(self, node: Node) -> list[NodeAction]:
        group = node.key
        if not isinstance(group, Group) or group.kind is not GroupKind.FOLDER:
            return []
        folder = Path(group.name)
        return [NodeAction("&Remove from Library", lambda: self._remove_source(folder))]
