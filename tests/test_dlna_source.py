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
"""A DLNA/UPnP server as a media source, against a pretend server."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from pyamp.library.sources import Node, NodeAction, SourceError
from pyamp.plugins.dlna.client import CANT_SEARCH, ContentDirectory, MediaServer, describe
from pyamp.plugins.dlna.source import DlnaSource
from tests.dlna_server import ALBUM_ART, DlnaServer


@pytest.fixture
def server() -> Iterator[DlnaServer]:
    with DlnaServer() as server:
        yield server


@pytest.fixture
def source(server: DlnaServer) -> DlnaSource:
    return DlnaSource(ContentDirectory(describe(server.location)), "DLNA — NAS")


def test_the_servers_own_folders(source: DlnaSource) -> None:
    music, videos, everything = source.roots()
    assert [(n.key, n.label, n.expandable, n.has_tracks) for n in (music, videos, everything)] == [
        ("1", "Music (1)", True, True),
        ("2", "Videos", False, True),  # empty: nothing to open
        ("many", "All Songs", True, True),  # unknown count: may have some
    ]
    (albums,) = source.children(music)
    (album,) = source.children(albums)
    assert album.label == "Mezzanine (4)"
    assert source.children(album) == []
    assert source.tracks(music) == []  # only folders in it
    assert [t.title for t in source.tracks(album)] == ["Teardrop", "Angel"]
    assert [t.title for t in source.tracks(album, "ang")] == ["Angel"]


def test_songs_have_their_album_art(source: DlnaSource, server: DlnaServer) -> None:
    (albums,) = source.children(source.roots()[0])
    teardrop, angel = source.tracks(source.children(albums)[0])
    assert source.artwork(teardrop) == ALBUM_ART
    assert source.artwork(angel) is None  # it has none
    assert source.artwork(replace(teardrop, artwork=f"{server.url}/art/page.html")) is None
    server.close()
    assert source.artwork(teardrop) is None  # unreachable: no picture, no message


def test_the_whole_server_is_searched(source: DlnaSource) -> None:
    assert source.tracks(None) == []
    assert source.tracks(None, "  ") == []
    assert [t.title for t in source.tracks(None, "tear")] == ["Teardrop"]


def test_a_server_that_cant_search_says_so() -> None:
    with DlnaServer(searchable=False) as server:
        source = DlnaSource(ContentDirectory(describe(server.location)), "NAS")
        with pytest.raises(SourceError, match=CANT_SEARCH):
            source.tracks(None, "tear")


def test_server_errors_become_messages(source: DlnaSource) -> None:
    with pytest.raises(SourceError, match="No such object"):
        source.children(Node("gone", "Gone"))
    with pytest.raises(SourceError, match="No such object"):
        source.tracks(Node("gone", "Gone"))


def test_an_unreachable_server_is_a_message() -> None:
    gone = MediaServer("uuid:x", "Gone", "http://127.0.0.1:9/d.xml", "http://127.0.0.1:9/c", "")
    source = DlnaSource(ContentDirectory(gone, timeout=2), "Gone")
    with pytest.raises(SourceError, match="Could not reach the server"):
        source.roots()


def test_the_servers_own_commands_are_on_its_name(source: DlnaSource) -> None:
    hide = NodeAction("&Hide Server", lambda: None)
    commands = DlnaSource(source.directory, "NAS", [hide])
    assert commands.actions(None) == [hide]
    assert commands.actions(Node("1", "Music")) == []
