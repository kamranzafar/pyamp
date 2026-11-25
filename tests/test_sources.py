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
"""Media sources: nodes, searching and the list of sources the library shows."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyamp.core.events import EventBus, MediaSourcesChanged
from pyamp.core.models import Track
from pyamp.library.sources import MediaSources, Node, NodeAction, matches


class Source:
    name = "Somewhere"
    hint = ""

    def roots(self) -> list[Node]:
        return []

    def children(self, node: Node) -> list[Node]:
        return []

    def tracks(self, node: Node | None, search: str = "") -> list[Track]:
        return []

    def actions(self, node: Node | None) -> list[NodeAction]:
        return []


def test_node_labels() -> None:
    assert Node("k", "Mezzanine").label == "Mezzanine"
    assert Node("k", "Mezzanine", "1998").label == "Mezzanine (1998)"


@pytest.mark.parametrize(
    ("search", "found"),
    [
        ("", True),
        ("tear MASSIVE", True),
        ("hop", True),  # genre
        ("take", True),  # file name
        ("tear jazz", False),  # every word must match
    ],
)
def test_matches_like_the_library_searches(search: str, found: bool) -> None:
    track = Track(Path("/m/live-take.mp3"), "Teardrop", artist="Massive Attack", genre="Trip Hop")
    assert matches(track, search) is found


def test_sources_are_listed_in_the_order_added(bus: EventBus) -> None:
    events: list[MediaSourcesChanged] = []
    bus.subscribe(MediaSourcesChanged, events.append)
    sources = MediaSources(bus)
    first, second = Source(), Source()
    sources.add(first)
    sources.add(second)
    sources.add(first)  # already there
    assert list(sources) == [first, second]
    assert len(sources) == 2
    sources.changed(second)
    sources.remove(first)
    sources.remove(first)  # already gone
    sources.changed(first)  # not a source any more: nothing to say
    assert list(sources) == [second]
    assert events == [
        MediaSourcesChanged(),
        MediaSourcesChanged(),
        MediaSourcesChanged(second),
        MediaSourcesChanged(),
    ]


def test_a_source_is_replaced_in_its_place(bus: EventBus) -> None:
    events: list[MediaSourcesChanged] = []
    bus.subscribe(MediaSourcesChanged, events.append)
    sources = MediaSources(bus)
    first, second, edited, new = Source(), Source(), Source(), Source()
    sources.add(first)
    sources.add(second)
    events.clear()
    sources.replace(first, edited)
    assert list(sources) == [edited, second]
    sources.replace(first, new)  # not there (any more): added at the end
    assert list(sources) == [edited, second, new]
    assert events == [MediaSourcesChanged(), MediaSourcesChanged()]
