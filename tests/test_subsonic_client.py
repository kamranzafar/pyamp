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
"""The Subsonic client, against a pretend server on localhost."""

from __future__ import annotations

import urllib.request
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from pyamp.core.models import StreamUrl, Track
from pyamp.plugins.subsonic.client import Item, SubsonicClient, SubsonicError
from tests.subsonic_server import COVERS, PASSWORD, USERNAME, SubsonicServer


@pytest.fixture
def server() -> Iterator[SubsonicServer]:
    with SubsonicServer() as server:
        yield server


@pytest.fixture
def client(server: SubsonicServer) -> SubsonicClient:
    return SubsonicClient(server.url + "/", USERNAME, PASSWORD, timeout=5)


def test_logs_in_with_a_salted_token_never_the_password(
    client: SubsonicClient, server: SubsonicServer
) -> None:
    client.ping()
    client.ping()
    (_, first), (_, second) = server.requests
    assert "p" not in first
    assert PASSWORD not in str(first)
    assert first["s"] != second["s"]  # a fresh salt each time
    assert (first["v"], first["c"], first["f"]) == ("1.16.1", "PyAmp", "json")


def test_a_wrong_password_is_reported(server: SubsonicServer) -> None:
    with pytest.raises(SubsonicError, match="Wrong username or password"):
        SubsonicClient(server.url, USERNAME, "nope", timeout=5).ping()


def test_something_that_isnt_subsonic(server: SubsonicServer) -> None:
    website = SubsonicClient(f"{server.url}/website", USERNAME, PASSWORD, timeout=5)
    with pytest.raises(SubsonicError, match="doesn't look like a Subsonic server"):
        website.ping()


def test_an_unreachable_server(server: SubsonicServer) -> None:
    with pytest.raises(OSError):
        SubsonicClient(f"{server.url}/nothing-here", USERNAME, PASSWORD, timeout=5).ping()


def test_the_server_must_be_a_web_address() -> None:
    with pytest.raises(ValueError, match="http"):
        SubsonicClient("music.example.com", USERNAME, PASSWORD)


def test_browses_artists_albums_and_playlists(client: SubsonicClient) -> None:
    assert client.artists() == [Item("ar1", "Massive Attack", "1 album")]
    assert client.albums("ar1") == [Item("al1", "Mezzanine", "Massive Attack · 1998")]
    assert client.playlists() == [Item("p1", "Mix", "1 song")]
    assert [t.title for t in client.playlist_tracks("p1")] == ["Angel"]


def test_an_albums_songs_are_streams_with_tags_and_length(client: SubsonicClient) -> None:
    teardrop, angel = client.album_tracks("al1")
    assert teardrop == Track(
        location=client.stream_url("s1"),
        title="Teardrop",
        artist="Massive Attack",
        album="Mezzanine",
        genre="Trip Hop",
        year=1998,
        track_number=1,
        disc_number=1,
        duration_ms=330_000,
        artwork="al-al1",
    )
    assert (angel.track_number, angel.year, angel.genre) == (2, 0, "")  # untagged: unknown
    assert angel.artwork == ""  # no cover


def test_covers_are_fetched_with_a_login(client: SubsonicClient, server: SubsonicServer) -> None:
    assert client.cover_art("al-al1") == COVERS["al-al1"]
    method, query = server.requests[-1]
    assert (method, query["id"], query["size"], query["u"]) == (
        "getCoverArt",
        "al-al1",
        "600",
        USERNAME,
    )
    with pytest.raises(SubsonicError, match="no picture"):
        client.cover_art("missing")  # the server says so in JSON


def test_search_finds_songs_and_their_albums(client: SubsonicClient) -> None:
    albums, songs = client.search("tear")
    assert [a.name for a in albums] == ["Mezzanine"]
    assert [s.title for s in songs] == ["Teardrop"]
    assert client.search("zzz") == ([], [])


def test_a_missing_item_is_an_error(client: SubsonicClient) -> None:
    with pytest.raises(SubsonicError, match="Not found"):
        client.album_tracks("nope")


def test_stream_urls_carry_no_login(client: SubsonicClient, server: SubsonicServer) -> None:
    url = client.stream_url("s1").url
    assert url.startswith(f"{server.url}/rest/stream?")
    assert set(parse_qs(urlsplit(url).query)) == {"id", "v", "c"}


def test_authorise_adds_a_fresh_login_to_this_servers_urls(
    client: SubsonicClient, server: SubsonicServer
) -> None:
    plain = client.stream_url("s1")
    signed = client.authorise(plain)
    assert isinstance(signed, StreamUrl)
    query = {k: v[0] for k, v in parse_qs(urlsplit(signed.url).query).items()}
    assert SubsonicServer.logged_in(query)
    assert query["id"] == "s1"
    assert client.authorise(plain) != signed  # new salt every time
    assert client.authorise(signed) == signed  # never twice


@pytest.mark.parametrize(
    "location",
    [
        StreamUrl("https://radio.example/live"),
        StreamUrl("http://127.0.0.2:1/rest/stream?id=1"),  # another server
        Path("/music/a.mp3"),
    ],
)
def test_authorise_leaves_everything_else_alone(
    client: SubsonicClient, location: StreamUrl | Path
) -> None:
    assert client.authorise(location) == location


def test_only_an_authorised_stream_plays(client: SubsonicClient) -> None:
    plain = client.stream_url("s1")
    with urllib.request.urlopen(str(client.authorise(plain)), timeout=5) as response:
        assert response.headers["Content-Type"] == "audio/wav"
        assert response.read(4) == b"RIFF"
    with urllib.request.urlopen(str(plain), timeout=5) as response:
        assert response.headers["Content-Type"] == "application/json"  # an error reply
