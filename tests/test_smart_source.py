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

"""The smart playlists as a source in the library window."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import StreamUrl, Track
from pyamp.library.catalog import Catalog, FileStamp
from pyamp.library.smart_playlists import (
    Operator,
    Rule,
    RuleField,
    SmartPlaylist,
    SmartPlaylists,
)
from pyamp.library.smart_source import SmartPlaylistsSource
from pyamp.library.sources import Node

HOP = SmartPlaylist("Trip Hop", (Rule(RuleField.GENRE, Operator.CONTAINS, "hop"),))
EVERYTHING = SmartPlaylist("Everything", limit=1)


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    opened.put(
        [
            (Track(tmp_path / "a.mp3", "Teardrop", genre="Trip Hop"), FileStamp(1, 2)),
            (Track(tmp_path / "b.mp3", "Angel", genre="Trip Hop"), FileStamp(1, 2)),
            (Track(tmp_path / "c.mp3", "So What", genre="Jazz"), FileStamp(1, 2)),
        ]
    )
    yield opened
    opened.close()


@pytest.fixture
def store(tmp_path: Path) -> SmartPlaylists:
    playlists = SmartPlaylists(tmp_path / "smart.json")
    playlists.save(HOP)
    playlists.save(EVERYTHING)
    return playlists


@pytest.fixture
def commands() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def source(
    store: SmartPlaylists, catalog: Catalog, commands: list[tuple[str, str]]
) -> SmartPlaylistsSource:
    return SmartPlaylistsSource(
        store,
        catalog,
        new_playlist=lambda: commands.append(("new", "")),
        edit_playlist=lambda name: commands.append(("edit", name)),
        duplicate_playlist=lambda name: commands.append(("duplicate", name)),
        delete_playlist=lambda name: commands.append(("delete", name)),
    )


def test_each_playlist_is_listed_with_its_count(source: SmartPlaylistsSource) -> None:
    roots = source.roots()
    assert [(n.name, n.detail) for n in roots] == [("Everything", "1"), ("Trip Hop", "2")]
    assert all(not n.expandable and n.has_tracks for n in roots)
    assert source.children(roots[0]) == []


def test_picking_one_lists_what_matches(source: SmartPlaylistsSource) -> None:
    node = Node("Trip Hop", "Trip Hop")
    assert {t.title for t in source.tracks(node)} == {"Teardrop", "Angel"}
    assert [t.title for t in source.tracks(node, "tear")] == ["Teardrop"]  # then searched


def test_the_source_itself_and_gone_playlists_list_nothing(
    source: SmartPlaylistsSource, store: SmartPlaylists
) -> None:
    assert source.tracks(None) == []
    store.delete("Trip Hop")
    assert source.tracks(Node("Trip Hop", "Trip Hop")) == []
    assert [n.name for n in source.roots()] == ["Everything"]


def test_scans_change_what_it_lists(source: SmartPlaylistsSource, catalog: Catalog) -> None:
    catalog.put([(Track(Path("/x/d.mp3"), "Roads", genre="trip hop"), FileStamp(1, 2))])
    assert source.roots()[1].detail == "3"


def test_commands(source: SmartPlaylistsSource, commands: list[tuple[str, str]]) -> None:
    (new,) = source.actions(None)
    assert new.text == "&New Smart Playlist…"
    new.run()
    actions = source.actions(Node("Trip Hop", "Trip Hop"))
    assert [a.text for a in actions] == ["&Edit…", "D&uplicate", "&Delete…"]
    for action in actions:
        action.run()
    assert commands == [
        ("new", ""),
        ("edit", "Trip Hop"),
        ("duplicate", "Trip Hop"),
        ("delete", "Trip Hop"),
    ]


def test_artwork_is_the_files(source: SmartPlaylistsSource, tmp_path: Path) -> None:
    (tmp_path / "cover.jpg").write_bytes(b"jpeg")
    assert source.artwork(Track(tmp_path / "a.mp3", "Teardrop")) == b"jpeg"
    assert source.artwork(Track.for_stream(StreamUrl("https://radio.example/"))) is None
