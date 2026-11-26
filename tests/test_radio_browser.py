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
"""The Radio Directory plugin: its Radio Browser client, its library source, and the
plugin, against a pretend Radio Browser (no internet)."""

from __future__ import annotations

import socket
from collections.abc import Iterator, Sequence

import pytest

from pyamp import APP_NAME, __version__
from pyamp.core.events import EventBus, TrackChanged
from pyamp.core.models import StreamUrl, Track
from pyamp.library.sources import MediaSources, Node, SourceError
from pyamp.plugins.base import PluginContext
from pyamp.plugins.radio_browser import RadioBrowserPlugin
from pyamp.plugins.radio_browser import client as client_module
from pyamp.plugins.radio_browser.client import (
    Category,
    RadioBrowserClient,
    RadioBrowserError,
    Station,
    find_servers,
)
from pyamp.plugins.radio_browser.source import (
    COUNTRIES,
    COUNTRY,
    GENRE,
    GENRES,
    KEEP_S,
    LANGUAGE,
    LANGUAGES,
    MOST_PLAYED,
    TOP_VOTED,
    RadioBrowserSource,
)
from tests.radio_browser_server import LOGO, RadioBrowserServer
from tests.test_background_loader import wait_until

JAZZ = StreamUrl("http://jazz.example/live")


@pytest.fixture
def server() -> Iterator[RadioBrowserServer]:
    with RadioBrowserServer() as server:
        yield server


@pytest.fixture
def client(server: RadioBrowserServer) -> RadioBrowserClient:
    return RadioBrowserClient(lambda: [server.url], timeout=5)


def names(stations: Sequence[Station | Track]) -> list[str]:
    return [s.title if isinstance(s, Track) else s.name for s in stations]


# -- Client ---------------------------------------------------------------------------- #


def test_stations_are_listed_by_votes_or_plays(
    client: RadioBrowserClient, server: RadioBrowserServer
) -> None:
    assert names(client.top_voted()) == ["Jazz FM", "Radio Paris"]  # no usable stream: left out
    assert names(client.most_played()) == ["Radio Paris", "Jazz FM"]
    path, query = server.requests[0]
    assert path == "/json/stations/search"
    assert query == {"order": "votes", "reverse": "true", "hidebroken": "true", "limit": "200"}
    assert set(server.agents) == {f"{APP_NAME}/{__version__}"}  # nothing else about the user


def test_stations_by_country_genre_language_and_name(client: RadioBrowserClient) -> None:
    assert names(client.by_country("FR")) == ["Radio Paris"]
    assert names(client.by_genre("smooth jazz")) == ["Jazz FM"]
    assert names(client.by_language("english")) == ["Jazz FM", "Radio Paris"]
    assert names(client.search("paris")) == ["Radio Paris"]


def test_a_station_is_a_live_stream_with_its_details(client: RadioBrowserClient) -> None:
    jazz, paris = client.top_voted()
    track = jazz.track
    assert track.location == JAZZ  # the stream inside its playlist link
    assert track.duration_ms == 0  # live
    assert (track.title, track.album, track.genre) == (
        "Jazz FM",
        "United Kingdom · MP3 128 kbps",
        "jazz, smooth jazz",
    )
    assert track.artwork.endswith("/logo.png")
    assert paris.track.location == StreamUrl("http://paris.example/a")  # not resolved: as given
    assert (paris.track.title, paris.track.album) == ("Radio Paris", "France · AAC")


def test_countries_genres_and_languages(client: RadioBrowserClient) -> None:
    assert client.countries() == [
        Category("FR", "France", 1),
        Category("GB", "United Kingdom", 1200),
    ]  # one without a code can't be asked for
    assert client.genres() == [Category("jazz", "jazz", 1)]
    assert client.languages() == [Category("english", "English", 2)]


def test_a_play_is_counted_for_a_listed_station(
    client: RadioBrowserClient, server: RadioBrowserServer
) -> None:
    assert client.station_id(JAZZ) is None  # not listed yet
    client.top_voted()
    assert client.station_id(JAZZ) == "u-jazz"
    client.count_play("u-jazz")
    assert server.plays == ["u-jazz"]
    server.broken = True
    client.count_play("u-jazz")  # fails quietly: the station plays anyway


def test_a_failing_mirror_is_passed_over(server: RadioBrowserServer) -> None:
    with socket.socket() as closed:
        closed.bind(("127.0.0.1", 0))
        dead = f"http://127.0.0.1:{closed.getsockname()[1]}"  # nothing listening
    client = RadioBrowserClient(lambda: [dead, server.url], timeout=5)
    assert names(client.top_voted()) == ["Jazz FM", "Radio Paris"]
    assert client._mirrors() == [server.url, dead]  # tried last from now on


def test_errors_say_what_went_wrong(client: RadioBrowserClient, server: RadioBrowserServer) -> None:
    server.broken = True
    with pytest.raises(RadioBrowserError, match="Could not reach Radio Browser"):
        client.top_voted()
    server.broken, server.garbled = False, True
    with pytest.raises(RadioBrowserError, match="unreadable"):
        client.countries()


def test_a_logo_is_fetched_if_it_can_be(
    client: RadioBrowserClient, server: RadioBrowserServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.logo(f"{server.url}/logo.png") == LOGO
    assert client.logo("data:image/png;base64,AAAA") is None  # not a web address
    assert client.logo(f"{server.url}/missing.png") is None
    monkeypatch.setattr(client_module, "MAX_LOGO_BYTES", 4)
    assert client.logo(f"{server.url}/logo.png") is None  # too big


def test_mirrors_are_found_by_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    addresses = [(0, 0, 0, "", ("1.1.1.1", 443)), (0, 0, 0, "", ("2.2.2.2", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_, **__: addresses)
    hosts = {"1.1.1.1": "de1.api.example", "2.2.2.2": "nl1.api.example"}
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: (hosts[ip], [], [ip]))
    assert sorted(find_servers()) == ["https://de1.api.example", "https://nl1.api.example"]

    def offline(*_: object, **__: object) -> list[object]:
        raise OSError("no network")

    monkeypatch.setattr(socket, "getaddrinfo", offline)
    assert find_servers() == ["https://all.api.radio-browser.info"]


# -- Source ---------------------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def source(client: RadioBrowserClient) -> RadioBrowserSource:
    return RadioBrowserSource(client, clock=Clock())


def test_the_lists_cost_nothing_until_opened(
    source: RadioBrowserSource, server: RadioBrowserServer
) -> None:
    assert [n.name for n in source.roots()] == [
        "Top Voted",
        "Most Played",
        "Countries",
        "Genres",
        "Languages",
    ]
    assert source.tracks(None) == []  # the whole directory, without a search
    assert server.requests == []


def test_countries_genres_and_languages_hold_their_stations(source: RadioBrowserSource) -> None:
    countries = source.children(Node((COUNTRIES, ""), "Countries"))
    assert [n.label for n in countries] == ["France (1)", "United Kingdom (1,200)"]
    assert names(source.tracks(countries[0])) == ["Radio Paris"]
    (jazz,) = source.children(Node((GENRES, ""), "Genres"))
    assert names(source.tracks(jazz)) == ["Jazz FM"]
    (english,) = source.children(Node((LANGUAGES, ""), "Languages"))
    assert names(source.tracks(english)) == ["Jazz FM", "Radio Paris"]
    assert (countries[0].key, jazz.key, english.key) == (
        (COUNTRY, "FR"),
        (GENRE, "jazz"),
        (LANGUAGE, "english"),
    )
    assert source.children(Node((TOP_VOTED, ""), "Top Voted")) == []


def test_searching_the_directory_or_a_list(source: RadioBrowserSource) -> None:
    assert names(source.tracks(None, "PARIS")) == ["Radio Paris"]
    top = Node((TOP_VOTED, ""), "Top Voted")
    assert names(source.tracks(top, "jazz")) == ["Jazz FM"]  # by its genre, too
    assert names(source.tracks(Node((MOST_PLAYED, ""), "Most Played"))) == [
        "Radio Paris",
        "Jazz FM",
    ]
    assert source.tracks(Node("other", "Other")) == []


def test_answers_are_kept_for_a_few_minutes(
    client: RadioBrowserClient, server: RadioBrowserServer
) -> None:
    clock = Clock()
    source = RadioBrowserSource(client, clock=clock)
    top = Node((TOP_VOTED, ""), "Top Voted")
    source.tracks(top)
    source.tracks(top, "jazz")
    assert len(server.requests) == 1
    clock.now = KEEP_S + 1
    source.tracks(top)
    assert len(server.requests) == 2


def test_errors_are_shown_in_the_library(
    source: RadioBrowserSource, server: RadioBrowserServer
) -> None:
    server.broken = True
    with pytest.raises(SourceError, match="Could not reach Radio Browser"):
        source.tracks(Node((TOP_VOTED, ""), "Top Voted"))


def test_a_stations_artwork_is_its_logo(
    source: RadioBrowserSource, server: RadioBrowserServer
) -> None:
    jazz, paris = source.tracks(Node((TOP_VOTED, ""), "Top Voted"))
    assert source.artwork(jazz) == LOGO
    assert source.artwork(paris) is None  # it has none
    assert source.actions(None) == []


# -- Plugin ---------------------------------------------------------------------------- #


class Named:
    hint = ""

    def __init__(self, name: str) -> None:
        self.name = name


def test_the_directory_goes_just_below_the_users_own_stations(
    client: RadioBrowserClient, server: RadioBrowserServer, bus: EventBus
) -> None:
    sources = MediaSources(bus)
    for name in ("This Computer", "Radio", "Subsonic"):
        sources.add(Named(name))  # type: ignore[arg-type]
    plugin = RadioBrowserPlugin(client)
    context = PluginContext(bus=bus, player=None, playlist=None, sources=sources)  # type: ignore[arg-type]
    plugin.activate(context)
    assert [s.name for s in sources] == ["This Computer", "Radio", "Radio Directory", "Subsonic"]

    client.top_voted()  # the user browses it...
    bus.publish(TrackChanged(Track.for_stream(JAZZ, "Jazz FM"), 0))  # ...and plays a station
    wait_until(lambda: server.plays == ["u-jazz"])
    bus.publish(TrackChanged(Track.for_stream(StreamUrl("http://elsewhere.example/")), 0))
    bus.publish(TrackChanged(None, -1))
    plugin.deactivate()
    assert [s.name for s in sources] == ["This Computer", "Radio", "Subsonic"]
    bus.publish(TrackChanged(Track.for_stream(JAZZ, "Jazz FM"), 0))  # no longer listened to
    assert server.plays == ["u-jazz"]


def test_without_a_library_the_plugin_does_nothing(bus: EventBus) -> None:
    plugin = RadioBrowserPlugin(RadioBrowserClient(list))
    plugin.activate(PluginContext(bus=bus, player=None, playlist=None))  # type: ignore[arg-type]
    assert plugin.source is None
    plugin.deactivate()


def test_a_source_goes_at_the_end_without_one_to_follow(bus: EventBus) -> None:
    sources = MediaSources(bus)
    sources.add(Named("This Computer"))  # type: ignore[arg-type]
    sources.add(Named("Directory"), after="Radio")  # type: ignore[arg-type]
    assert [s.name for s in sources] == ["This Computer", "Directory"]
