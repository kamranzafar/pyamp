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

"""The saved radio stations, as a source in the library window.

Picking Radio lists every station; picking one lists just it. A station added as a
``.pls`` or ``.m3u`` link is looked up when it's listed (on the library's worker thread,
like a server's tracks), so what's played is the stream inside; the answer is kept for a
few minutes, so browsing doesn't ask the station again and again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from pyamp.core.models import StreamUrl, Track
from pyamp.library.loader import TrackLoader
from pyamp.library.radio_stations import RadioStations, Station
from pyamp.library.sources import Node, NodeAction

type StationCommand = Callable[[str], object]

#: How long a station's looked-up stream is reused.
RESOLVED_FOR_S = 300.0
#: How many stations are looked up at once.
LOOKUPS = 8


class RadioSource:
    """Every saved radio station; a station's track is its stream (shown as LIVE)."""

    name = "Radio"
    hint = "Right-click Radio to add a station"

    def __init__(
        self,
        store: RadioStations,
        loader: TrackLoader,
        *,
        add_station: Callable[[], object],
        edit_station: StationCommand,
        delete_station: StationCommand,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._loader = loader
        # Main-thread commands from the window (they show dialogs).
        self._add = add_station
        self._edit = edit_station
        self._delete = delete_station
        self._clock = clock
        self._lock = threading.Lock()
        self._resolved: dict[str, tuple[float, StreamUrl]] = {}

    def roots(self) -> list[Node]:
        return [Node(s.name, s.name, tooltip=s.url.url) for s in self._store.stations()]

    def children(self, node: Node) -> list[Node]:
        return []

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        if node is None:
            stations = self._store.stations()
        else:
            station = self._store.get(str(node.key))
            stations = [station] if station is not None else []
        words = search.casefold().split()
        stations = [s for s in stations if all(w in _search_text(s) for w in words)]
        if len(stations) <= 1:
            return [self._track(s) for s in stations]
        with ThreadPoolExecutor(min(LOOKUPS, len(stations))) as pool:
            return list(pool.map(self._track, stations))

    def actions(self, node: Node | None) -> list[NodeAction]:
        if node is None:
            return [NodeAction("&Add Station…", self._add)]
        name = str(node.key)
        return [
            NodeAction("&Edit…", lambda: self._edit(name)),
            NodeAction("&Delete…", lambda: self._delete(name)),
        ]

    def artwork(self, track: Track) -> bytes | None:
        return None

    def _track(self, station: Station) -> Track:
        return Track.for_stream(self._stream(station.url), station.name)

    def _stream(self, url: StreamUrl) -> StreamUrl:
        """The stream to play for ``url``: itself, or the first stream in the playlist it
        links to (itself again if that can't be had, so the player says what's wrong)."""
        now = self._clock()
        with self._lock:
            cached = self._resolved.get(url.url)
        if cached is not None and now - cached[0] < RESOLVED_FOR_S:
            return cached[1]
        stream = next(
            (
                item.location
                for item in self._loader.discover([url])
                if isinstance(item, Track) and isinstance(item.location, StreamUrl)
            ),
            url,
        )
        if stream != url:
            with self._lock:
                self._resolved[url.url] = (now, stream)
        return stream


def _search_text(station: Station) -> str:
    return f"{station.name}\n{station.url.url}".casefold()
