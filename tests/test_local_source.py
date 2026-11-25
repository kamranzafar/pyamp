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
"""The local library as a media source."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import Track
from pyamp.library.catalog import Catalog, FileStamp, Group, GroupKind
from pyamp.library.local_source import LocalSource, group_name
from pyamp.library.sources import Node

STAMP = FileStamp(1, 2)


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    folder = tmp_path / "music"
    opened.add_source(folder)
    opened.put(
        [
            (Track(folder / "1.mp3", "Teardrop", artist="Massive Attack", genre="Trip Hop"), STAMP),
            (Track(folder / "2.mp3", "Glory Box", artist="Portishead", album="Dummy"), STAMP),
        ]
    )
    yield opened
    opened.close()


@pytest.fixture
def removed() -> list[Path]:
    return []


@pytest.fixture
def source(catalog: Catalog, removed: list[Path]) -> LocalSource:
    return LocalSource(catalog, removed.append)


def test_browsed_by_artist_album_genre_and_folder(source: LocalSource) -> None:
    roots = source.roots()
    assert [(n.name, n.expandable, n.has_tracks) for n in roots] == [
        ("Artists", True, False),
        ("Albums", True, False),
        ("Genres", True, False),
        ("Folders", True, False),
    ]
    artists = source.children(roots[0])
    assert [n.label for n in artists] == ["Massive Attack (1)", "Portishead (1)"]
    assert artists[0].key == Group(GroupKind.ARTIST, "Massive Attack")  # no count: stable
    assert source.children(artists[0]) == []  # artists have no nodes inside them


def test_folders_show_their_name_and_path(source: LocalSource, tmp_path: Path) -> None:
    (folder,) = source.children(source.roots()[3])
    assert (folder.label, folder.tooltip) == ("music (2)", str(tmp_path / "music"))


def test_tracks_of_the_whole_library_or_a_group(source: LocalSource) -> None:
    assert [t.title for t in source.tracks(None)] == ["Teardrop", "Glory Box"]
    assert [t.title for t in source.tracks(None, "glory")] == ["Glory Box"]
    genre = Node(Group(GroupKind.GENRE, "Trip Hop"), "Trip Hop")
    assert [t.title for t in source.tracks(genre)] == ["Teardrop"]
    assert source.tracks(source.roots()[0]) == []  # a category has none of its own


def test_folders_can_be_removed(source: LocalSource, removed: list[Path], tmp_path: Path) -> None:
    artists, _, _, folders = source.roots()
    assert source.actions(source.children(artists)[0]) == []
    assert source.actions(folders) == []
    assert source.actions(None) == []  # nothing for the whole library
    (folder,) = source.children(folders)
    (remove,) = source.actions(folder)
    assert remove.text == "&Remove from Library"
    remove.run()
    assert removed == [tmp_path / "music"]


@pytest.mark.parametrize(
    ("group", "name"),
    [
        (Group(GroupKind.ARTIST, ""), "Unknown Artist"),
        (Group(GroupKind.ALBUM, "Dummy", "Portishead"), "Dummy — Portishead"),
        (Group(GroupKind.ALBUM, "Dummy"), "Dummy"),
        (Group(GroupKind.GENRE, "Rock"), "Rock"),
        (Group(GroupKind.FOLDER, "/music/rock"), "rock"),
        (Group(GroupKind.FOLDER, "/"), "/"),
    ],
)
def test_group_names(group: Group, name: str) -> None:
    assert group_name(group) == name
