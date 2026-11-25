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
"""A Subsonic server as a media source, against a pretend server."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from pyamp.library.sources import Node, SourceError
from pyamp.plugins.subsonic.client import SubsonicClient
from pyamp.plugins.subsonic.source import ALBUM, ARTISTS, PLAYLISTS, SubsonicSource
from tests.subsonic_server import PASSWORD, USERNAME, SubsonicServer


@pytest.fixture
def server() -> Iterator[SubsonicServer]:
    with SubsonicServer() as server:
        yield server


@pytest.fixture
def source(server: SubsonicServer) -> SubsonicSource:
    return SubsonicSource(SubsonicClient(server.url, USERNAME, PASSWORD))


def test_artists_albums_and_playlists(source: SubsonicSource) -> None:
    artists, playlists = source.roots()
    assert (artists.name, playlists.name) == ("Artists", "Playlists")
    (artist,) = source.children(artists)
    assert (artist.label, artist.expandable, artist.has_tracks) == (
        "Massive Attack (1 album)",
        True,
        False,
    )
    (album,) = source.children(artist)
    assert album.label == "Mezzanine (Massive Attack · 1998)"
    assert [t.title for t in source.tracks(album)] == ["Teardrop", "Angel"]
    assert [t.title for t in source.tracks(album, "ang")] == ["Angel"]
    (mix,) = source.children(playlists)
    assert [t.title for t in source.tracks(mix)] == ["Angel"]
    assert source.children(album) == []
    assert source.tracks(artists) == []  # categories and artists have no tracks of their own


def test_the_whole_server_is_searched(source: SubsonicSource) -> None:
    assert source.tracks(None) == []
    assert source.tracks(None, "  ") == []
    assert [t.title for t in source.tracks(None, "tear")] == ["Teardrop"]
    assert source.actions(source.roots()[0]) == []


def test_nodes_from_elsewhere_have_nothing(source: SubsonicSource) -> None:
    assert source.children(Node("something", "else")) == []
    assert source.tracks(Node((ARTISTS, "x", "y"), "odd")) == []


def test_server_errors_become_messages(server: SubsonicServer) -> None:
    source = SubsonicSource(SubsonicClient(server.url, USERNAME, "wrong"))
    with pytest.raises(SourceError, match="Wrong username or password"):
        source.children(Node((ARTISTS, ""), "Artists"))
    assert source.tracks(Node((PLAYLISTS, ""), "x")) == []  # not asked: nothing to list
    with pytest.raises(SourceError, match="Wrong username or password"):
        source.tracks(Node((ALBUM, "al1"), "x"))


def test_an_unreachable_server_is_a_message(server: SubsonicServer) -> None:
    source = SubsonicSource(SubsonicClient("http://127.0.0.1:9", USERNAME, PASSWORD, timeout=2))
    with pytest.raises(SourceError, match="Could not reach the server"):
        source.tracks(None, "anything")
