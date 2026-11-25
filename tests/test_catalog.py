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
"""The library's index: sources, tracks, search and the file it's kept in."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import ReplayGain, StreamUrl, Track
from pyamp.library.catalog import SCHEMA_VERSION, Catalog, FileStamp

STAMP = FileStamp(mtime_ns=1, size=2)


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    yield opened
    opened.close()


def track(path: str, title: str = "", **fields: object) -> Track:
    return Track(Path(path), title or Path(path).stem, **fields)  # type: ignore[arg-type]


# -- Sources ------------------------------------------------------------------------ #


def test_sources_are_kept_in_path_order(catalog: Catalog, tmp_path: Path) -> None:
    assert catalog.add_source(tmp_path / "b")
    assert catalog.add_source(tmp_path / "a" / "song.mp3")
    assert catalog.sources() == [tmp_path / "a" / "song.mp3", tmp_path / "b"]


def test_sources_are_made_absolute_and_tidy(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    catalog.add_source(Path("music/../music//rock"))
    assert catalog.sources() == [tmp_path / "music" / "rock"]


def test_a_source_already_covered_is_not_added(catalog: Catalog, tmp_path: Path) -> None:
    assert catalog.add_source(tmp_path / "music")
    assert not catalog.add_source(tmp_path / "music")
    assert not catalog.add_source(tmp_path / "music" / "rock")
    assert not catalog.add_source(tmp_path / "music" / "rock" / "song.mp3")
    assert catalog.sources() == [tmp_path / "music"]


def test_a_folder_replaces_the_sources_inside_it(catalog: Catalog, tmp_path: Path) -> None:
    catalog.add_source(tmp_path / "music" / "rock")
    catalog.add_source(tmp_path / "music" / "song.mp3")
    catalog.add_source(tmp_path / "musical")  # a sibling that only shares the prefix
    assert catalog.add_source(tmp_path / "music")
    assert catalog.sources() == [tmp_path / "music", tmp_path / "musical"]


def test_removing_a_source_forgets_only_its_tracks(catalog: Catalog, tmp_path: Path) -> None:
    catalog.add_source(tmp_path / "rock")
    catalog.add_source(tmp_path / "jazz")
    rock, jazz = tmp_path / "rock" / "a.mp3", tmp_path / "jazz" / "b.mp3"
    catalog.put([(track(str(rock)), STAMP), (track(str(jazz)), STAMP)])
    catalog.remove_source(tmp_path / "rock")
    assert catalog.sources() == [tmp_path / "jazz"]
    assert [t.location for t in catalog.tracks()] == [jazz]


# -- Tracks ------------------------------------------------------------------------- #


def test_tracks_keep_all_their_fields(catalog: Catalog) -> None:
    full = track(
        "/m/a.flac",
        "Teardrop",
        artist="Massive Attack",
        album="Mezzanine",
        album_artist="Massive Attack",
        genre="Trip Hop",
        year=1998,
        track_number=3,
        disc_number=1,
        duration_ms=330_000,
        replay_gain=ReplayGain(track_gain=-6.5, track_peak=0.98, album_gain=-7.0),
    )
    bare = track("/m/b.wav")
    catalog.put([(full, STAMP), (bare, STAMP)])
    assert catalog.tracks() == [bare, full]  # no album artist sorts first
    assert len(catalog) == 2


def test_putting_a_file_again_replaces_it(catalog: Catalog) -> None:
    catalog.put([(track("/m/a.mp3", "Old"), STAMP)])
    catalog.put([(track("/m/a.mp3", "New"), FileStamp(5, 6))])
    assert [t.title for t in catalog.tracks()] == ["New"]
    assert catalog.stamps() == {Path("/m/a.mp3"): FileStamp(5, 6)}


def test_tracks_can_be_removed(catalog: Catalog) -> None:
    catalog.put([(track("/m/a.mp3"), STAMP), (track("/m/b.mp3"), STAMP)])
    catalog.remove([Path("/m/a.mp3")])
    assert [t.title for t in catalog.tracks()] == ["b"]


def test_streams_are_not_library_tracks(catalog: Catalog) -> None:
    with pytest.raises(ValueError, match="only files"):
        catalog.put([(Track.for_stream(StreamUrl("https://radio.example/live")), STAMP)])


def test_tracks_come_in_album_order(catalog: Catalog) -> None:
    tracks = [
        track("/m/1.mp3", "Z", artist="beta", album="One", track_number=1),
        track("/m/2.mp3", "Y", artist="Alpha", album="Two", disc_number=2, track_number=1),
        track("/m/3.mp3", "X", artist="Alpha", album="Two", disc_number=1, track_number=9),
        track("/m/4.mp3", "W", artist="Guest", album_artist="alpha", album="Two", track_number=2),
    ]
    catalog.put((t, STAMP) for t in tracks)
    assert [t.title for t in catalog.tracks()] == ["W", "X", "Y", "Z"]


@pytest.mark.parametrize(
    ("search", "titles"),
    [
        ("", ["Ölstraße", "Angel", "Teardrop"]),  # Kraftwerk, then Massive Attack
        ("massive", ["Angel", "Teardrop"]),
        ("MASSIVE tear", ["Teardrop"]),
        ("hop", ["Teardrop"]),  # genre
        ("live-take", ["Angel"]),  # file name
        ("ÖLSTRASSE", ["Ölstraße"]),  # Unicode case folding
        ("100%", []),
        ("_", []),  # LIKE wildcards are matched literally
    ],
)
def test_search_matches_every_word_anywhere(
    catalog: Catalog, search: str, titles: list[str]
) -> None:
    catalog.put(
        [
            (track("/m/a.mp3", "Teardrop", artist="Massive Attack", genre="Trip Hop"), STAMP),
            (track("/m/live-take.mp3", "Angel", album_artist="Massive Attack"), STAMP),
            (track("/m/b.mp3", "Ölstraße", artist="Kraftwerk"), STAMP),
        ]
    )
    assert [t.title for t in catalog.tracks(search)] == titles


def test_can_be_used_from_several_threads(catalog: Catalog) -> None:
    def writer(n: int) -> None:
        catalog.put((track(f"/m/{n}-{i}.mp3"), STAMP) for i in range(50))

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(catalog) == 200


# -- The library file ---------------------------------------------------------------- #


def test_the_library_is_kept_between_runs(tmp_path: Path) -> None:
    first = Catalog(tmp_path / "library.sqlite3")
    first.add_source(tmp_path / "music")
    first.put([(track("/m/a.mp3"), STAMP)])
    first.close()
    second = Catalog(tmp_path / "library.sqlite3")
    assert second.sources() == [tmp_path / "music"]
    assert len(second) == 1
    second.close()


def test_default_location_is_the_user_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("platformdirs.user_data_dir", lambda _: str(tmp_path))
    opened = Catalog()
    assert opened.path == tmp_path / "library.sqlite3"
    assert opened.path.is_file()
    opened.close()


def test_an_unreadable_file_is_set_aside(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "library.sqlite3"
    path.write_bytes(b"not a database, but maybe worth keeping" * 100)
    opened = Catalog(path)
    assert opened.sources() == []
    opened.close()
    assert (tmp_path / "library.sqlite3.unusable").read_bytes().startswith(b"not a database")
    assert "Starting a new library" in caplog.text


def test_a_library_from_a_newer_pyamp_is_set_aside(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    db.close()
    Catalog(path).close()
    assert (tmp_path / "library.sqlite3.unusable").is_file()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (SCHEMA_VERSION,)
    db.close()
