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
"""A Subsonic server as a source in the library window: its artists, albums and playlists.

No Qt here; the library window calls it on a worker thread.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from pyamp.core.models import Track
from pyamp.library.sources import Node, NodeAction, SourceError, matches
from pyamp.plugins.subsonic.client import Item, SubsonicClient, SubsonicError, describe

log = logging.getLogger(__name__)

ARTISTS, ARTIST, ALBUM, PLAYLISTS, PLAYLIST = "artists", "artist", "album", "playlists", "playlist"


#: Shown for a server whose password PyAmp doesn't have this session.
SIGN_IN = "Right-click the server and choose Sign In… to browse it"


class SubsonicSource:
    """Artists → albums, and playlists. An artist's tracks are all its albums' songs;
    picking the whole server searches it.

    Without a ``client`` (the password wasn't remembered) it has nothing to show until
    the user signs in. ``commands`` are the server's own right-click commands (sign in,
    edit, remove), which the plugin carries out.
    """

    def __init__(
        self, client: SubsonicClient | None, name: str, commands: Sequence[NodeAction] = ()
    ) -> None:
        self.client = client
        self.name = name
        self.hint = "Search, or pick an album or playlist" if client else SIGN_IN
        self._commands = list(commands)

    def roots(self) -> list[Node]:
        if self.client is None:
            return []
        return [
            Node((ARTISTS, ""), "Artists", expandable=True, has_tracks=False),
            Node((PLAYLISTS, ""), "Playlists", expandable=True, has_tracks=False),
        ]

    def children(self, node: Node) -> list[Node]:
        client = self._signed_in()
        kind, item_id = _key(node)
        if kind == ARTISTS:
            items = _ask(client.artists)
            return [_node(ARTIST, i, expandable=True) for i in items]
        if kind == ARTIST:
            return [_node(ALBUM, i) for i in _ask(lambda: client.albums(item_id))]
        if kind == PLAYLISTS:
            return [_node(PLAYLIST, i) for i in _ask(client.playlists)]
        return []

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        if node is None and not search.strip():
            return []
        client = self._signed_in()
        if node is None:
            return _ask(lambda: client.search(search)[1])
        kind, item_id = _key(node)
        if kind == ARTIST:  # every album's songs
            songs = _ask(lambda: self._artist_songs(client, item_id))
        elif kind == ALBUM:
            songs = _ask(lambda: client.album_tracks(item_id))
        elif kind == PLAYLIST:
            songs = _ask(lambda: client.playlist_tracks(item_id))
        else:
            return []
        return [song for song in songs if matches(song, search)]

    def actions(self, node: Node | None) -> list[NodeAction]:
        return list(self._commands) if node is None else []

    def artwork(self, track: Track) -> bytes | None:
        if self.client is None or not track.artwork:
            return None
        try:
            return self.client.cover_art(track.artwork)
        except (SubsonicError, OSError, ValueError) as exc:
            log.debug("No cover for %s: %s", track.title, describe(exc))
            return None

    def _signed_in(self) -> SubsonicClient:
        if self.client is None:
            raise SourceError(SIGN_IN)
        return self.client

    @staticmethod
    def _artist_songs(client: SubsonicClient, artist_id: str) -> list[Track]:
        albums = client.albums(artist_id)
        return [song for album in albums for song in client.album_tracks(album.id)]


def _key(node: Node) -> tuple[str, str]:
    """(kind, server id) of a node made by this source; ("", "") for anything else."""
    key = node.key
    if isinstance(key, tuple) and len(key) == 2:
        return str(key[0]), str(key[1])
    return "", ""


def _node(kind: str, item: Item, *, expandable: bool = False, has_tracks: bool = True) -> Node:
    return Node(
        (kind, item.id), item.name, item.detail, expandable=expandable, has_tracks=has_tracks
    )


def _ask[T](call: Callable[[], T]) -> T:
    """Call the server, turning its errors into messages for the library window."""
    try:
        return call()
    except (SubsonicError, OSError, ValueError) as exc:
        raise SourceError(describe(exc)) from exc
