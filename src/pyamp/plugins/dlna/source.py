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
"""A DLNA/UPnP media server as a source in the library window, browsed by its own
folders. No Qt here; the library window calls it on a worker thread."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from pyamp.core.models import Track
from pyamp.library.sources import Node, NodeAction, SourceError, matches
from pyamp.plugins.dlna.client import ContentDirectory, DlnaError, Folder, describe_error

log = logging.getLogger(__name__)

#: The top folder's id on every ContentDirectory.
TOP = "0"


class DlnaSource:
    """The server's folders as it offers them (e.g. Music → Artists → ...). A folder's
    tracks are the songs directly in it; picking the whole server searches it, if the
    server can search.

    ``commands`` are the server's own right-click commands (refresh, hide, remove),
    which the plugin carries out.
    """

    hint = "Pick a folder, or search"

    def __init__(
        self, directory: ContentDirectory, name: str, commands: Sequence[NodeAction] = ()
    ) -> None:
        self.directory = directory
        self.name = name
        self._commands = list(commands)

    def roots(self) -> list[Node]:
        return self._folders(TOP)

    def children(self, node: Node) -> list[Node]:
        return self._folders(str(node.key))

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        if node is None:
            return _ask(lambda: self.directory.search(search)) if search.strip() else []
        songs = _ask(lambda: self.directory.browse(str(node.key)))[1]
        return [song for song in songs if matches(song, search)]

    def actions(self, node: Node | None) -> list[NodeAction]:
        return list(self._commands) if node is None else []

    def artwork(self, track: Track) -> bytes | None:
        if not track.artwork:
            return None
        try:
            return self.directory.artwork(track.artwork)
        except (DlnaError, OSError, ValueError) as exc:
            log.debug("No album art for %s: %s", track.title, describe_error(exc))
            return None

    def _folders(self, folder_id: str) -> list[Node]:
        return [_node(f) for f in _ask(lambda: self.directory.browse(folder_id))[0]]


def _node(folder: Folder) -> Node:
    return Node(
        folder.id,
        folder.title,
        str(folder.child_count) if folder.child_count else "",
        expandable=folder.child_count != 0,  # unknown: may have some
    )


def _ask[T](call: Callable[[], T]) -> T:
    """Call the server, turning its errors into messages for the library window."""
    try:
        return call()
    except (DlnaError, OSError, ValueError) as exc:
        raise SourceError(describe_error(exc)) from exc
