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

import random
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import ReplayGain, StreamUrl, Track
from pyamp.library import catalog as catalog_module
from pyamp.library.catalog import (
    SCHEMA_VERSION,
    Catalog,
    FileStamp,
    Group,
    GroupKind,
    album_order,
    default_path,
)

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


def test_the_database_sorts_exactly_like_album_order(catalog: Catalog) -> None:
    """Sorting moved into SQL for speed; it must not change the order anywhere."""
    rng = random.Random(7)
    names = ["", "abba", "ABBA", "Öl", "ol", "Straße", "STRASSE", "zz", "Émile", "émile", "b"]
    tracks = [
        Track(
            Path(f"/m/{rng.choice(names)}/{n:03d} {rng.choice(names)}.mp3"),
            rng.choice(names) or "x",
            artist=rng.choice(names),
            album_artist=rng.choice(["", *names]),
            album=rng.choice(names),
            disc_number=rng.randint(0, 2),
            track_number=rng.randint(0, 3),
        )
        for n in range(400)
    ]
    catalog.put((t, STAMP) for t in tracks)
    assert catalog.tracks() == sorted(tracks, key=album_order)


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


# -- Browsing by artist, album, genre and folder -------------------------------------- #


@pytest.fixture
def browsable(catalog: Catalog, tmp_path: Path) -> Catalog:
    rock, jazz = tmp_path / "rock", tmp_path / "jazz"
    catalog.add_source(rock)
    catalog.add_source(jazz)
    catalog.add_source(tmp_path / "single.mp3")
    entries = [
        track(f"{rock}/1.mp3", "One", artist="Band", album="First", genre="Rock"),
        track(f"{rock}/2.mp3", "Two", artist="band", album="first", genre="rock"),
        track(f"{rock}/3.mp3", "Duet", artist="Guest", album_artist="Band", album="Second"),
        track(f"{jazz}/4.mp3", "Blue", artist="Trio", album="First", genre="Jazz"),
        track(f"{jazz}/sub/5.mp3", "Untagged"),
        track(f"{tmp_path}/single.mp3", "Single", artist="Solo"),
        track(f"{tmp_path}/rockabilly/6.mp3", "Outside"),  # shares rock's prefix only
    ]
    catalog.put((t, STAMP) for t in entries)
    return catalog


def test_artists_group_by_album_artist_ignoring_case(browsable: Catalog) -> None:
    assert browsable.groups(GroupKind.ARTIST) == [
        Group(GroupKind.ARTIST, "", count=2),  # untagged
        Group(GroupKind.ARTIST, "Band", count=3),  # incl. a guest on Band's album
        Group(GroupKind.ARTIST, "Solo", count=1),
        Group(GroupKind.ARTIST, "Trio", count=1),
    ]


def test_albums_are_told_apart_by_artist(browsable: Catalog) -> None:
    assert browsable.groups(GroupKind.ALBUM) == [
        Group(GroupKind.ALBUM, "First", "Band", 2),
        Group(GroupKind.ALBUM, "First", "Trio", 1),
        Group(GroupKind.ALBUM, "Second", "Band", 1),
    ]


def test_genres(browsable: Catalog) -> None:
    assert browsable.groups(GroupKind.GENRE) == [
        Group(GroupKind.GENRE, "Jazz", count=1),
        Group(GroupKind.GENRE, "Rock", count=2),
    ]


def test_folders_are_the_sources(browsable: Catalog, tmp_path: Path) -> None:
    assert browsable.groups(GroupKind.FOLDER) == [
        Group(GroupKind.FOLDER, str(tmp_path / "jazz"), count=2),
        Group(GroupKind.FOLDER, str(tmp_path / "rock"), count=3),
        Group(GroupKind.FOLDER, str(tmp_path / "single.mp3"), count=1),
    ]


@pytest.mark.parametrize(
    ("group", "titles"),
    [
        (Group(GroupKind.ARTIST, "BAND"), ["One", "Two", "Duet"]),
        (Group(GroupKind.ARTIST, ""), ["Outside", "Untagged"]),
        (Group(GroupKind.ALBUM, "first", "trio"), ["Blue"]),
        (Group(GroupKind.GENRE, "rock"), ["One", "Two"]),
        (Group(GroupKind.FOLDER, "{tmp}/jazz"), ["Untagged", "Blue"]),  # no artist first
        (Group(GroupKind.FOLDER, "{tmp}/rock"), ["One", "Two", "Duet"]),
        (Group(GroupKind.FOLDER, "{tmp}/single.mp3"), ["Single"]),
    ],
)
def test_tracks_of_a_group(
    browsable: Catalog, tmp_path: Path, group: Group, titles: list[str]
) -> None:
    # Folders written with "/" find their tracks on Windows too, where paths use backslashes.
    group = Group(group.kind, group.name.format(tmp=tmp_path), group.artist)
    assert [t.title for t in browsable.tracks(group=group)] == titles


def test_search_within_a_group(browsable: Catalog) -> None:
    band = Group(GroupKind.ARTIST, "Band")
    assert [t.title for t in browsable.tracks("second", band)] == ["Duet"]
    assert browsable.tracks("blue", band) == []


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
    monkeypatch.setattr("pyamp.library.catalog.default_path", default_path)  # not isolated
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


def test_tracks_can_be_looked_up_by_path(catalog: Catalog, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pyamp.library.catalog._LOOKUP_BATCH", 2)  # several queries
    tracks = [track(f"/m/{n}.mp3", artist="Band") for n in range(5)]
    catalog.put((t, STAMP) for t in tracks)
    wanted = [Path("/m/4.mp3"), Path("/m/0.mp3"), Path("/m/9.mp3"), Path("/m/0.mp3")]
    found = catalog.tracks_at(wanted)
    assert found == {Path("/m/4.mp3"): tracks[4], Path("/m/0.mp3"): tracks[0]}
    assert catalog.tracks_at([]) == {}


# -- When files were added ---------------------------------------------------------- #


def _added(catalog: Catalog) -> dict[str, int]:
    rows = catalog._db.execute("SELECT path, added_ns FROM tracks").fetchall()
    return dict(rows)


def test_a_file_is_added_at_its_modified_time_and_keeps_it(catalog: Catalog) -> None:
    song = track("/music/a.mp3", "Old Title")
    catalog.put([(song, FileStamp(mtime_ns=10, size=1))])
    catalog.put([(track("/music/a.mp3", "New Title"), FileStamp(mtime_ns=99, size=1))])
    assert _added(catalog) == {"/music/a.mp3": 10}
    assert catalog.stamps() == {Path("/music/a.mp3"): FileStamp(99, 1)}
    assert [t.title for t in catalog.tracks()] == ["New Title"]


def test_an_older_library_is_upgraded_keeping_its_tracks(tmp_path: Path) -> None:
    path = tmp_path / "library.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(f"BEGIN; {catalog_module._MIGRATIONS[0]} PRAGMA user_version = 1; COMMIT;")
        db.execute(
            "INSERT INTO tracks VALUES"
            " ('/m/a.mp3', 42, 1, 'A', '', '', '', '', 0, 0, 0, 0,"
            " NULL, NULL, NULL, NULL, '', '', '', 'a')"
        )
    db.close()
    upgraded = Catalog(path)
    try:
        assert [t.title for t in upgraded.tracks()] == ["A"]
        assert _added(upgraded) == {"/m/a.mp3": 42}  # its modified time: the best guess
        (version,) = upgraded._db.execute("PRAGMA user_version").fetchone()
        assert version == SCHEMA_VERSION == 2
    finally:
        upgraded.close()
