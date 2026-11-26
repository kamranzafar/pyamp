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
"""Radio Browser's directory as a source in the library window: top voted and most
played stations, and stations by country, genre or language; picking the whole directory
searches it.

Nothing is asked of Radio Browser until a list is opened. Answers are kept for a few
minutes, so moving around the directory doesn't ask again and again. No Qt here; the
library window calls it on a worker thread.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from pyamp.core.models import Track
from pyamp.library.sources import Node, NodeAction, SourceError, matches
from pyamp.plugins.radio_browser.client import (
    Category,
    RadioBrowserClient,
    RadioBrowserError,
    Station,
    describe,
)

TOP_VOTED, MOST_PLAYED = "top-voted", "most-played"
COUNTRIES, GENRES, LANGUAGES = "countries", "genres", "languages"
COUNTRY, GENRE, LANGUAGE = "country", "genre", "language"
#: How long an answer is kept.
KEEP_S = 300.0


class RadioBrowserSource:
    name = "Radio Directory"
    hint = "Search the directory, or pick a list"

    def __init__(
        self, client: RadioBrowserClient, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.client = client
        self._clock = clock
        self._lock = threading.Lock()
        self._kept: dict[tuple[str, str], tuple[float, Any]] = {}

    def roots(self) -> list[Node]:
        return [
            Node((TOP_VOTED, ""), "Top Voted"),
            Node((MOST_PLAYED, ""), "Most Played"),
            Node((COUNTRIES, ""), "Countries", expandable=True, has_tracks=False),
            Node((GENRES, ""), "Genres", expandable=True, has_tracks=False),
            Node((LANGUAGES, ""), "Languages", expandable=True, has_tracks=False),
        ]

    def children(self, node: Node) -> list[Node]:
        kind, _ = _key(node)
        lists: dict[str, tuple[str, Callable[[], list[Category]]]] = {
            COUNTRIES: (COUNTRY, self.client.countries),
            GENRES: (GENRE, self.client.genres),
            LANGUAGES: (LANGUAGE, self.client.languages),
        }
        if kind not in lists:
            return []
        each, ask = lists[kind]
        categories: list[Category] = self._ask((kind, ""), ask)
        return [Node((each, c.key), c.name, f"{c.count:,}") for c in categories]

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        if node is None:
            words = search.strip()
            if not words:
                return []
            found: list[Station] = self._ask(
                ("search", words.casefold()), lambda: self.client.search(words)
            )
            return [s.track for s in found]
        kind, key = _key(node)
        asks: dict[str, Callable[[], list[Station]]] = {
            TOP_VOTED: self.client.top_voted,
            MOST_PLAYED: self.client.most_played,
            COUNTRY: lambda: self.client.by_country(key),
            GENRE: lambda: self.client.by_genre(key),
            LANGUAGE: lambda: self.client.by_language(key),
        }
        if kind not in asks:
            return []
        stations: list[Station] = self._ask((kind, key), asks[kind])
        return [t for t in (s.track for s in stations) if matches(t, search)]

    def actions(self, node: Node | None) -> list[NodeAction]:
        return []

    def artwork(self, track: Track) -> bytes | None:
        return self.client.logo(track.artwork) if track.artwork else None

    def _ask[T](self, key: tuple[str, str], call: Callable[[], T]) -> T:
        """``call``'s answer, kept for KEEP_S; its errors as messages for the library."""
        now = self._clock()
        with self._lock:
            kept = self._kept.get(key)
        if kept is not None and now - kept[0] < KEEP_S:
            return kept[1]  # type: ignore[no-any-return]
        try:
            answer = call()
        except RadioBrowserError as exc:
            raise SourceError(describe(exc)) from exc
        with self._lock:
            self._kept = {k: v for k, v in self._kept.items() if now - v[0] < KEEP_S}
            self._kept[key] = (now, answer)
        return answer


def _key(node: Node) -> tuple[str, str]:
    """(kind, key) of a node made by this source; ("", "") for anything else."""
    key = node.key
    if isinstance(key, tuple) and len(key) == 2:
        return str(key[0]), str(key[1])
    return "", ""
