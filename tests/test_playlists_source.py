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
"""The saved playlists as a source in the library window."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import StreamUrl, Track
from pyamp.library.catalog import Catalog, FileStamp
from pyamp.library.playlists_source import PlaylistsSource
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.library.sources import Node

RADIO = StreamUrl("https://radio.example/live")


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    tagged = Track(tmp_path / "a.mp3", "Teardrop", artist="Massive Attack", genre="Trip Hop")
    opened.put([(tagged, FileStamp(1, 2))])
    yield opened
    opened.close()


@pytest.fixture
def saved(tmp_path: Path) -> SavedPlaylists:
    playlists = SavedPlaylists(tmp_path / "playlists")
    playlists.save(
        "Mix",
        [
            Track(tmp_path / "a.mp3", "a"),  # in the library: its tags come from there
            Track(tmp_path / "b.mp3", "Outside - B side", duration_ms=61_000),  # not
            Track.for_stream(RADIO, "Radio", 0),
        ],
    )
    playlists.save("Empty", [])
    return playlists


@pytest.fixture
def commands() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def source(
    saved: SavedPlaylists, catalog: Catalog, commands: list[tuple[str, str]]
) -> PlaylistsSource:
    return PlaylistsSource(
        saved,
        catalog,
        open_playlist=lambda name: commands.append(("open", name)),
        rename_playlist=lambda name: commands.append(("rename", name)),
        delete_playlist=lambda name: commands.append(("delete", name)),
    )


def test_every_playlist_with_its_length(source: PlaylistsSource) -> None:
    assert [n.label for n in source.roots()] == ["Empty (0)", "Mix (3)"]
    assert source.children(source.roots()[1]) == []


def test_tracks_get_their_tags_from_the_library_without_reading_files(
    source: PlaylistsSource, tmp_path: Path
) -> None:
    teardrop, outside, radio = source.tracks(Node("Mix", "Mix"))
    assert (teardrop.title, teardrop.artist, teardrop.genre) == (
        "Teardrop",
        "Massive Attack",
        "Trip Hop",
    )
    assert (outside.location, outside.title, outside.duration_ms) == (
        tmp_path / "b.mp3",
        "Outside - B side",  # as the playlist gives it
        61_000,
    )
    assert (radio.location, radio.title) == (RADIO, "Radio")


def test_search_within_a_playlist(source: PlaylistsSource) -> None:
    assert [t.title for t in source.tracks(Node("Mix", "Mix"), "trip")] == ["Teardrop"]
    assert source.tracks(None) == []  # pick a playlist first
    assert source.tracks(Node("Gone", "Gone")) == []  # deleted meanwhile


def test_a_playlist_deleted_while_listing_is_left_out(
    source: PlaylistsSource, saved: SavedPlaylists, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = saved.entries

    def gone(name: str) -> list[object]:
        if name == "Empty":
            raise FileNotFoundError(name)
        return real(name)  # type: ignore[return-value]

    monkeypatch.setattr(saved, "entries", gone)
    assert [n.name for n in source.roots()] == ["Mix"]


def test_playlist_commands(source: PlaylistsSource, commands: list[tuple[str, str]]) -> None:
    actions = source.actions(Node("Mix", "Mix"))
    assert [a.text for a in actions] == ["&Open in Player", "Re&name…", "&Delete…"]
    for action in actions:
        action.run()
    assert commands == [("open", "Mix"), ("rename", "Mix"), ("delete", "Mix")]
    assert source.actions(None) == []  # nothing for the whole list


def test_artwork_for_files_only(source: PlaylistsSource, tmp_path: Path) -> None:
    (tmp_path / "folder.png").write_bytes(b"cover")
    assert source.artwork(Track(tmp_path / "song.mp3", "Song")) == b"cover"
    assert source.artwork(Track.for_stream(RADIO)) is None  # its server isn't known here
