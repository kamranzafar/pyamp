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

"""The saved radio stations, and the Radio source that lists them in the library."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyamp.core.models import StreamUrl
from pyamp.library.loader import TrackLoader
from pyamp.library.metadata import MutagenMetadataReader
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.library.radio_source import RESOLVED_FOR_S, RadioSource
from pyamp.library.radio_stations import RadioStations, Station, StationError
from pyamp.library.sources import Node

PLS_URL = "https://radio.example/station.pls"
STREAM = "https://stream.example/live.mp3"
PLS = f"[playlist]\nNumberOfEntries=2\nFile1={STREAM}\nTitle1=Example FM\nFile2=https://mirror.example/live.mp3\n"


@pytest.fixture
def store(tmp_path: Path) -> RadioStations:
    return RadioStations(tmp_path / "radio_stations.json")


# -- The store --------------------------------------------------------------------- #


def test_stations_are_saved_and_listed_by_name(store: RadioStations) -> None:
    store.save("  jazz fm ", "https://jazz.example/live")
    store.save("Ambient", PLS_URL)
    assert store.stations() == [
        Station("Ambient", StreamUrl(PLS_URL)),
        Station("jazz fm", StreamUrl("https://jazz.example/live")),
    ]
    assert store.get("JAZZ FM") == Station("jazz fm", StreamUrl("https://jazz.example/live"))
    assert store.get("Nope") is None
    saved = json.loads(store.file.read_text(encoding="utf-8"))
    assert saved["version"] == 1


def test_names_must_be_unique_ignoring_case(store: RadioStations) -> None:
    store.save("Jazz", "https://jazz.example/live")
    with pytest.raises(StationError, match="already a station"):
        store.save("JAZZ", "https://other.example/live")


def test_a_station_can_be_renamed_and_moved(store: RadioStations) -> None:
    store.save("Jazz", "https://jazz.example/live")
    store.save("Jazz", "https://jazz.example/hq", replacing="jazz")  # same name: fine
    store.save("Smooth Jazz", "https://jazz.example/hq", replacing="Jazz")
    assert store.stations() == [Station("Smooth Jazz", StreamUrl("https://jazz.example/hq"))]


@pytest.mark.parametrize(
    ("name", "url", "message"),
    [
        ("", "https://a.example/", "needs a name"),
        ("x" * 101, "https://a.example/", "at most"),
        ("bad\tname", "https://a.example/", "control characters"),
        ("Local", "/Users/me/song.mp3", "http"),
        ("FTP", "ftp://a.example/live", "http"),
    ],
)
def test_bad_stations_are_refused(store: RadioStations, name: str, url: str, message: str) -> None:
    with pytest.raises(StationError, match=message):
        store.save(name, url)
    assert store.stations() == []


def test_a_station_is_deleted(store: RadioStations) -> None:
    store.save("Jazz", "https://jazz.example/live")
    store.save("Rock", "https://rock.example/live")
    store.delete("jazz")
    assert [s.name for s in store.stations()] == ["Rock"]


def test_an_unreadable_file_is_ignored(store: RadioStations) -> None:
    store.file.write_text("not json", encoding="utf-8")
    assert store.stations() == []
    store.file.write_text('{"stations": "nope"}', encoding="utf-8")
    assert store.stations() == []


def test_an_unreadable_station_is_skipped_but_kept(store: RadioStations) -> None:
    store.file.write_text(
        json.dumps({"version": 1, "stations": [{"name": "Odd", "url": "gopher://x"}]}),
        encoding="utf-8",
    )
    assert store.stations() == []
    store.save("Jazz", "https://jazz.example/live")
    names = [e["name"] for e in json.loads(store.file.read_text(encoding="utf-8"))["stations"]]
    assert names == ["Odd", "Jazz"]


# -- The source -------------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def fetched() -> list[str]:
    return []


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def commands() -> list[tuple[str, str]]:
    return []


@pytest.fixture
def source(
    store: RadioStations, fetched: list[str], clock: Clock, commands: list[tuple[str, str]]
) -> RadioSource:
    def fetch(url: str) -> str:
        fetched.append(url)
        if url == PLS_URL:
            return PLS
        raise OSError("offline")

    loader = TrackLoader(MutagenMetadataReader(), PlaylistFormatRegistry.default(), fetch)
    return RadioSource(
        store,
        loader,
        add_station=lambda: commands.append(("add", "")),
        edit_station=lambda name: commands.append(("edit", name)),
        delete_station=lambda name: commands.append(("delete", name)),
        clock=clock,
    )


def test_the_source_lists_each_station(store: RadioStations, source: RadioSource) -> None:
    assert source.roots() == []
    store.save("Jazz", "https://jazz.example/live")
    store.save("Ambient", PLS_URL)
    roots = source.roots()
    assert [(n.name, n.tooltip, n.expandable, n.has_tracks) for n in roots] == [
        ("Ambient", PLS_URL, False, True),
        ("Jazz", "https://jazz.example/live", False, True),
    ]
    assert source.children(roots[0]) == []


def test_a_stations_track_is_its_live_stream(store: RadioStations, source: RadioSource) -> None:
    store.save("Jazz", "https://jazz.example/live")
    [track] = source.tracks(Node("Jazz", "Jazz"))
    assert track.title == "Jazz"
    assert track.location == StreamUrl("https://jazz.example/live")
    assert track.is_stream and track.duration_ms == 0  # shown as LIVE
    assert source.artwork(track) is None
    assert source.tracks(Node("Gone", "Gone")) == []


def test_a_playlist_link_plays_the_first_stream_in_it(
    store: RadioStations, source: RadioSource, fetched: list[str], clock: Clock
) -> None:
    store.save("Example", PLS_URL)
    [track] = source.tracks(Node("Example", "Example"))
    assert track.location == StreamUrl(STREAM)
    assert track.title == "Example"  # the station's name, not the playlist's
    source.tracks(None)
    assert fetched == [PLS_URL]  # looked up once, then reused for a while
    clock.now += RESOLVED_FOR_S
    source.tracks(None)
    assert fetched == [PLS_URL, PLS_URL]


def test_a_link_that_cant_be_looked_up_is_listed_as_it_is(
    store: RadioStations, source: RadioSource, fetched: list[str]
) -> None:
    store.save("Offline", "https://down.example/station.m3u")
    [track] = source.tracks(None)
    assert track.location == StreamUrl("https://down.example/station.m3u")
    source.tracks(None)
    assert len(fetched) == 2  # a failure isn't kept: it's tried again


def test_picking_radio_lists_every_station_and_searches(
    store: RadioStations, source: RadioSource
) -> None:
    store.save("Jazz", "https://jazz.example/live")
    store.save("Rock", "https://rock.example/live")
    store.save("Example", PLS_URL)
    assert [t.title for t in source.tracks(None)] == ["Example", "Jazz", "Rock"]
    assert [t.title for t in source.tracks(None, "ROCK")] == ["Rock"]
    assert [t.title for t in source.tracks(None, "jazz.example")] == ["Jazz"]  # the URL too
    assert source.tracks(Node("Jazz", "Jazz"), "rock") == []


def test_the_menus_add_edit_and_delete(
    store: RadioStations, source: RadioSource, commands: list[tuple[str, str]]
) -> None:
    [add] = source.actions(None)
    assert add.text == "&Add Station…"
    add.run()
    store.save("Jazz", "https://jazz.example/live")
    actions = source.actions(source.roots()[0])
    assert [a.text for a in actions] == ["&Edit…", "&Delete…"]
    for action in actions:
        action.run()
    assert commands == [("add", ""), ("edit", "Jazz"), ("delete", "Jazz")]
