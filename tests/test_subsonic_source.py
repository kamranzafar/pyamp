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

from pyamp.library.sources import Node, NodeAction, SourceError
from pyamp.plugins.subsonic.client import SubsonicClient
from pyamp.plugins.subsonic.source import ALBUM, ARTISTS, PLAYLISTS, SIGN_IN, SubsonicSource
from tests.subsonic_server import PASSWORD, USERNAME, SubsonicServer


@pytest.fixture
def server() -> Iterator[SubsonicServer]:
    with SubsonicServer() as server:
        yield server


@pytest.fixture
def source(server: SubsonicServer) -> SubsonicSource:
    return SubsonicSource(SubsonicClient(server.url, USERNAME, PASSWORD), "Subsonic")


def test_artists_albums_and_playlists(source: SubsonicSource) -> None:
    artists, playlists = source.roots()
    assert (artists.name, playlists.name) == ("Artists", "Playlists")
    (artist,) = source.children(artists)
    assert (artist.label, artist.expandable, artist.has_tracks) == (
        "Massive Attack (1 album)",
        True,
        True,
    )
    (album,) = source.children(artist)
    assert album.label == "Mezzanine (Massive Attack · 1998)"
    assert [t.title for t in source.tracks(album)] == ["Teardrop", "Angel"]
    assert [t.title for t in source.tracks(album, "ang")] == ["Angel"]
    (mix,) = source.children(playlists)
    assert [t.title for t in source.tracks(mix)] == ["Angel"]
    assert source.children(album) == []
    assert source.tracks(artists) == []  # a category has no tracks of its own
    assert [t.title for t in source.tracks(artist)] == ["Teardrop", "Angel"]  # all its albums
    assert [t.title for t in source.tracks(artist, "tear")] == ["Teardrop"]


def test_the_whole_server_is_searched(source: SubsonicSource) -> None:
    assert source.tracks(None) == []
    assert source.tracks(None, "  ") == []
    assert [t.title for t in source.tracks(None, "tear")] == ["Teardrop"]
    assert source.actions(source.roots()[0]) == []


def test_nodes_from_elsewhere_have_nothing(source: SubsonicSource) -> None:
    assert source.children(Node("something", "else")) == []
    assert source.tracks(Node((ARTISTS, "x", "y"), "odd")) == []


def test_server_errors_become_messages(server: SubsonicServer) -> None:
    source = SubsonicSource(SubsonicClient(server.url, USERNAME, "wrong"), "Subsonic")
    with pytest.raises(SourceError, match="Wrong username or password"):
        source.children(Node((ARTISTS, ""), "Artists"))
    assert source.tracks(Node((PLAYLISTS, ""), "x")) == []  # not asked: nothing to list
    with pytest.raises(SourceError, match="Wrong username or password"):
        source.tracks(Node((ALBUM, "al1"), "x"))


def test_an_unreachable_server_is_a_message(server: SubsonicServer) -> None:
    client = SubsonicClient("http://127.0.0.1:9", USERNAME, PASSWORD, timeout=2)
    source = SubsonicSource(client, "Subsonic")
    with pytest.raises(SourceError, match="Could not reach the server"):
        source.tracks(None, "anything")


def test_the_servers_own_commands_are_on_its_name(server: SubsonicServer) -> None:
    remove = NodeAction("&Remove Server", lambda: None)
    source = SubsonicSource(SubsonicClient(server.url, USERNAME, PASSWORD), "S", [remove])
    assert source.actions(None) == [remove]
    assert source.actions(source.roots()[0]) == []  # nothing for its artists or albums


def test_a_server_not_signed_in_asks_to_sign_in() -> None:
    sign_in = NodeAction("Sign &In…", lambda: None)
    source = SubsonicSource(None, "Subsonic — music.example", [sign_in])
    assert source.roots() == []
    assert source.hint == SIGN_IN
    assert source.tracks(None) == []  # picking it: the hint
    with pytest.raises(SourceError, match="Sign In"):
        source.tracks(None, "tear")
    with pytest.raises(SourceError, match="Sign In"):
        source.children(Node((ARTISTS, ""), "Artists"))
    assert source.actions(None) == [sign_in]
