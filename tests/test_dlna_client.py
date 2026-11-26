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
"""The DLNA plugin's SSDP search and ContentDirectory client, against a pretend server."""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest

from pyamp.core.models import StreamUrl
from pyamp.plugins.dlna import client, ssdp
from pyamp.plugins.dlna.client import (
    CANT_SEARCH,
    ContentDirectory,
    DlnaError,
    Folder,
    MediaServer,
    describe,
    describe_error,
    parse_didl,
    parse_duration,
)
from tests.dlna_server import ALBUM_ART, CONTENT_DIRECTORY, MANY, UDN, DlnaServer


@pytest.fixture
def server() -> Iterator[DlnaServer]:
    with DlnaServer() as server:
        yield server


@pytest.fixture
def directory(server: DlnaServer) -> ContentDirectory:
    return ContentDirectory(describe(server.location))


# -- SSDP ---------------------------------------------------------------------------- #


def test_search_request() -> None:
    request = ssdp.search_request(wait_s=3).decode()
    assert request.startswith("M-SEARCH * HTTP/1.1\r\n")
    assert "HOST: 239.255.255.250:1900\r\n" in request
    assert 'MAN: "ssdp:discover"\r\n' in request
    assert "MX: 3\r\n" in request
    assert f"ST: {ssdp.MEDIA_SERVER}\r\n" in request
    assert request.endswith("\r\n\r\n")


@pytest.mark.parametrize(
    ("answer", "location"),
    [
        (b"HTTP/1.1 200 OK\r\nLocation: http://10.0.0.2:8200/d.xml\r\n\r\n", "http://10.0.0.2:8200/d.xml"),
        (b"HTTP/1.1 200 OK\nLOCATION:  http://h/d.xml \n\n", "http://h/d.xml"),  # bare newlines
        (b"HTTP/1.1 200 OK\r\nST: x\r\n\r\n", None),  # no location
        (b"HTTP/1.1 200 OK\r\nLOCATION: file:///etc/passwd\r\n\r\n", None),  # not the web
        (b"NOTIFY * HTTP/1.1\r\nLOCATION: http://h/d.xml\r\n\r\n", None),  # not an answer
        (b"", None),
        (b"\xff\xfe garbage", None),
    ],
)  # fmt: skip
def test_answers_give_the_description_address(answer: bytes, location: str | None) -> None:
    assert ssdp.parse_response(answer) == location


def test_discover_finds_the_servers_that_answer(server: DlnaServer) -> None:
    assert ssdp.discover(timeout=0.5, target=server.ssdp_target) == [server.location]
    assert server.searches == ssdp.SENDS  # each answered; the same server listed once


def test_discover_without_a_network_finds_nothing() -> None:
    assert ssdp.discover(timeout=0.2, target=("no-such-host.invalid", 1900)) == []


def test_discover_with_nobody_answering_waits_out_the_timeout() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as quiet:
        quiet.bind(("127.0.0.1", 0))
        assert ssdp.discover(timeout=0.2, target=quiet.getsockname()) == []


# -- Device description ------------------------------------------------------------------ #


def test_describe_a_media_server(server: DlnaServer) -> None:
    described = describe(server.location)
    assert described == MediaServer(
        udn=UDN,
        name="Living Room NAS",
        location=server.location,
        control_url=f"{server.url}/ctl/ContentDir",
        service_type=CONTENT_DIRECTORY,
    )
    assert described.host == server.url.removeprefix("http://")


def test_the_control_address_is_resolved_against_url_base() -> None:
    with DlnaServer(url_base=True) as server:
        assert describe(server.location).control_url == f"{server.url}/ctl/ContentDir"


def test_something_else_is_not_a_media_server(server: DlnaServer) -> None:
    with pytest.raises(OSError, match="Not Found"):
        describe(f"{server.url}/nothing.xml")
    with pytest.raises(ValueError, match="http"):
        describe("file:///etc/hosts")  # never read a local file for a device


def test_a_device_without_a_content_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(request: object, timeout: float) -> bytes:
        return (
            b'<root xmlns="urn:schemas-upnp-org:device-1-0"><device><friendlyName>Router'
            b"</friendlyName><serviceList/></device></root>"
        )

    monkeypatch.setattr(client, "_fetch", fetch)
    with pytest.raises(DlnaError, match="isn't a media server"):
        describe("http://router/desc.xml")


def test_a_control_address_that_isnt_the_web_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(request: object, timeout: float) -> bytes:
        return (
            b'<root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList><service>'
            b"<serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>"
            b"<controlURL>file:///etc/passwd</controlURL></service></serviceList></device></root>"
        )

    monkeypatch.setattr(client, "_fetch", fetch)
    with pytest.raises(ValueError, match="http"):
        describe("http://nas/desc.xml")


# -- Browsing -------------------------------------------------------------------------- #


def test_browse_folders(directory: ContentDirectory) -> None:
    folders, tracks = directory.browse("0")
    assert folders == [
        Folder("1", "Music", 1),
        Folder("2", "Videos", 0),
        Folder("many", "All Songs", None),
    ]
    assert tracks == []


def test_browse_an_album(directory: ContentDirectory, server: DlnaServer) -> None:
    folders, (teardrop, angel) = directory.browse("al1")  # the video and picture left out
    assert folders == []
    assert teardrop.location == StreamUrl(f"{server.url}/media/s1.mp3")
    assert (teardrop.title, teardrop.artist, teardrop.album, teardrop.genre) == (
        "Teardrop",
        "Massive Attack",
        "Mezzanine",
        "Trip Hop",
    )
    assert (teardrop.track_number, teardrop.year, teardrop.duration_ms) == (1, 1998, 330_250)
    assert angel.location == StreamUrl(f"{server.url}/media/s2.flac")  # not its cover
    assert (angel.artist, angel.duration_ms) == ("Massive Attack", 379_000)  # dc:creator
    assert teardrop.artwork == f"{server.url}/art/al1.jpg"  # the http one
    assert angel.artwork == ""


def test_album_art(directory: ContentDirectory, server: DlnaServer) -> None:
    assert directory.artwork(f"{server.url}/art/al1.jpg") == ALBUM_ART
    with pytest.raises(DlnaError, match="isn't a picture"):
        directory.artwork(f"{server.url}/art/page.html")
    with pytest.raises(ValueError, match="http"):
        directory.artwork("file:///etc/passwd")


def test_big_folders_are_read_a_page_at_a_time(
    directory: ContentDirectory, server: DlnaServer
) -> None:
    _, tracks = directory.browse("many")
    assert [t.title for t in tracks] == [f"Song {n}" for n in range(MANY)]
    starts = [a["StartingIndex"] for action, a in server.requests if action == "Browse"]
    assert starts == ["0", "200", "400"]


def test_a_missing_folder_is_the_servers_error(directory: ContentDirectory) -> None:
    with pytest.raises(DlnaError, match="No such object"):
        directory.browse("gone")


def test_xml_with_a_doctype_is_refused(directory: ContentDirectory, server: DlnaServer) -> None:
    server.unsafe = True
    with pytest.raises(DlnaError, match="couldn't be read"):
        directory.browse("0")


def test_too_large_a_reply_is_refused(
    directory: ContentDirectory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client, "MAX_BYTES", 100)
    with pytest.raises(DlnaError, match="too large"):
        directory.browse("0")


def test_an_unreachable_server_is_a_message() -> None:
    gone = MediaServer("uuid:x", "Gone", "http://127.0.0.1:9/d.xml", "http://127.0.0.1:9/c", "")
    with pytest.raises(OSError) as raised:
        ContentDirectory(gone, timeout=2).browse("0")
    assert describe_error(raised.value).startswith("Could not reach the server")


# -- Searching ----------------------------------------------------------------------------- #


def test_search_every_word(directory: ContentDirectory, server: DlnaServer) -> None:
    assert [t.title for t in directory.search("massive tear")] == ["Teardrop"]
    criteria = server.requests[-1][1]["SearchCriteria"]
    assert criteria == (
        'upnp:class derivedfrom "object.item.audioItem"'
        ' and (dc:title contains "massive" or upnp:artist contains "massive")'
        ' and (dc:title contains "tear" or upnp:artist contains "tear")'
    )  # only what the server can search (not the album)
    assert directory.search("  ") == []


def test_search_quotes_are_escaped(directory: ContentDirectory, server: DlnaServer) -> None:
    directory.search('say "hi"\\')
    assert 'dc:title contains "say"' in server.requests[-1][1]["SearchCriteria"]
    assert r'dc:title contains "\"hi\"\\"' in server.requests[-1][1]["SearchCriteria"]


def test_a_server_that_cant_search() -> None:
    with DlnaServer(searchable=False) as server, pytest.raises(DlnaError, match=CANT_SEARCH):
        ContentDirectory(describe(server.location)).search("tear")


# -- DIDL and lengths ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "millis"),
    [
        ("0:05:30.250", 330_250),
        ("1:00:00", 3_600_000),
        ("6:19", 379_000),
        ("0:00:01.5", 1_500),
        ("", 0),
        (None, 0),
        ("soon", 0),
    ],
)
def test_durations(text: str | None, millis: int) -> None:
    assert parse_duration(text) == millis


def test_didl_audio_without_a_file_type_and_odd_items() -> None:
    didl = (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        '<item id="a"><upnp:class>object.item.audioItem</upnp:class>'
        '<res protocolInfo="http-get:*:*:*">http://nas/a</res></item>'  # audio, type unknown
        '<item id="b"><dc:title>Local</dc:title><upnp:class>object.item.audioItem</upnp:class>'
        '<res protocolInfo="http-get:*:audio/mpeg:*">file:///music/b.mp3</res></item>'
        '<item id="c"><dc:title>Odd number</dc:title>'
        "<upnp:originalTrackNumber>x</upnp:originalTrackNumber>"
        '<res protocolInfo="http-get:*:audio/mpeg:*">http://nas/c</res></item>'
        '<container id="d" childCount="many"><dc:title>Odd count</dc:title></container>'
        "</DIDL-Lite>"
    )
    folders, tracks = parse_didl(didl)
    assert folders == [Folder("d", "Odd count", None)]
    assert [(t.title, t.location, t.track_number) for t in tracks] == [
        ("Untitled", StreamUrl("http://nas/a"), 0),
        ("Odd number", StreamUrl("http://nas/c"), 0),
    ]  # the one pointing at a local file is left out
    assert parse_didl("") == ([], [])
    with pytest.raises(DlnaError):
        parse_didl("<not xml")
