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
"""Where the library window finds music: the local library and, from plugins, servers.

Each :class:`MediaSource` is browsed as a tree of :class:`Node` objects (artists, albums,
folders, playlists, ...) whose tracks can be listed and searched. Sources keep their own
index: the local library has its catalog, a Subsonic server its own database. No Qt
here. A source may be slow (a server on the network), so the library window calls it
on a worker thread: sources must be safe to call from one.
"""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterator
from dataclasses import dataclass
from typing import Protocol

from pyamp.core.events import EventBus, MediaSourcesChanged
from pyamp.core.models import Track
from pyamp.library.catalog import search_text


class SourceError(Exception):
    """A source couldn't do what was asked; the message is meant for the user."""


@dataclass(frozen=True, slots=True)
class Node:
    """Something to browse in a source: a category, artist, album, folder or playlist."""

    #: What the source knows it by; the same thing keeps the same key between calls.
    key: Hashable
    name: str
    #: Shown after the name, e.g. a track count or an album's artist and year.
    detail: str = ""
    tooltip: str = ""
    #: Whether it has nodes inside it (listed by :meth:`MediaSource.children`).
    expandable: bool = False
    #: Whether it has tracks of its own (listed by :meth:`MediaSource.tracks`).
    has_tracks: bool = True

    @property
    def label(self) -> str:
        return f"{self.name} ({self.detail})" if self.detail else self.name


@dataclass(frozen=True, slots=True)
class NodeAction:
    """A command for a node's right-click menu, e.g. removing a folder from the library."""

    text: str
    #: Runs on the main thread.
    run: Callable[[], object]


class MediaSource(Protocol):
    #: Shown at the top of its part of the library's sidebar.
    name: str
    #: Shown when the whole source is picked and it has nothing to list.
    hint: str

    def roots(self) -> list[Node]:
        """What's at the top of the source, e.g. Artists, Albums, Playlists."""
        ...

    def children(self, node: Node) -> list[Node]:
        """What's inside an expandable node."""
        ...

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        """The tracks of ``node`` (None: the whole source) that match ``search``, in the
        order to show them (e.g. album order). See :func:`matches` for how searches match.
        """
        ...

    def actions(self, node: Node) -> list[NodeAction]:
        """Commands for a node's right-click menu (often none)."""
        ...


def matches(track: Track, search: str) -> bool:
    """Whether every word of ``search`` is in the track's title, artist, album, album
    artist, genre or file name, ignoring case (as the local library searches)."""
    text = search_text(track)
    return all(word in text for word in search.casefold().split())


class MediaSources:
    """The sources the library shows, in the order they were added (local first).

    Publishes :class:`MediaSourcesChanged` when a source is added or removed, or when
    one says its contents changed. Used on the main thread.
    """

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._sources: list[MediaSource] = []

    def __iter__(self) -> Iterator[MediaSource]:
        return iter(list(self._sources))

    def __len__(self) -> int:
        return len(self._sources)

    def add(self, source: MediaSource) -> None:
        if source not in self._sources:
            self._sources.append(source)
            self._bus.publish(MediaSourcesChanged())

    def remove(self, source: MediaSource) -> None:
        if source in self._sources:
            self._sources.remove(source)
            self._bus.publish(MediaSourcesChanged())

    def changed(self, source: MediaSource) -> None:
        """Tell the library window that ``source``'s contents changed."""
        if source in self._sources:
            self._bus.publish(MediaSourcesChanged(source))
