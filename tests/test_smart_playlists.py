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

"""Smart playlists: their rules, where they're kept, and what they match in the library."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from pyamp.core.models import Track
from pyamp.library.catalog import Catalog, FileStamp, normalise
from pyamp.library.saved_playlists import PlaylistNameError
from pyamp.library.smart_playlists import (
    FORMAT_VERSION,
    OPERATORS,
    FieldKind,
    Operator,
    Rule,
    RuleField,
    SmartPlaylist,
    SmartPlaylists,
    SortOrder,
    default_file,
    file_type,
    from_json,
    problem,
    to_json,
)

DAY_NS = 86_400 * 1_000_000_000
NOW_NS = 1_000 * DAY_NS


def smart(*rules: Rule, **options: object) -> SmartPlaylist:
    return SmartPlaylist("Test", rules, **options)  # type: ignore[arg-type]


# -- Rules ------------------------------------------------------------------------- #


def test_every_field_has_operators_and_a_label() -> None:
    for rule_field in RuleField:
        assert OPERATORS[rule_field.kind]
        assert rule_field.label
    for operator in Operator:
        assert operator.label
    for order in SortOrder:
        assert order.label


@pytest.mark.parametrize(
    ("playlist", "message"),
    [
        (SmartPlaylist("  "), "needs a name"),
        (SmartPlaylist("x" * 101), "at most 100"),
        (SmartPlaylist("Tab\there"), "control characters"),
        (smart(limit=-1), "limit"),
        (smart(Rule(RuleField.GENRE, Operator.AT_LEAST, 3)), "can't use"),
        (smart(Rule(RuleField.GENRE, Operator.CONTAINS, " ")), "needs something to match"),
        (smart(Rule(RuleField.GENRE, Operator.CONTAINS, 3)), "expects text"),
        (smart(Rule(RuleField.YEAR, Operator.IS, "1999")), "expects a number"),
        (smart(Rule(RuleField.YEAR, Operator.BETWEEN, 1999, 1990)), "at least the first"),
        (smart(Rule(RuleField.ADDED, Operator.IN_LAST, 0)), "number of days"),
        (smart(Rule(RuleField.FILE_TYPE, Operator.IS, "")), "needs something to match"),
        (smart(Rule(RuleField.FOLDER, Operator.IN_FOLDER, "")), "needs something to match"),
    ],
)
def test_problems_are_explained(playlist: SmartPlaylist, message: str) -> None:
    assert message in problem(playlist)


def test_a_problem_names_its_rule() -> None:
    good = Rule(RuleField.GENRE, Operator.CONTAINS, "rock")
    bad = Rule(RuleField.YEAR, Operator.IS, -1)
    assert problem(smart(good, bad)).startswith("Rule 2:")


def test_sensible_playlists_have_no_problem() -> None:
    assert problem(smart()) == ""  # no rules: the whole library
    assert problem(smart(Rule(RuleField.GENRE, Operator.IS, ""))) == ""  # untagged
    assert problem(smart(Rule(RuleField.YEAR, Operator.BETWEEN, 1990, 1999), limit=5)) == ""


def test_file_types_ignore_dots_and_case() -> None:
    assert file_type(" .FLAC ") == file_type("flac") == "flac"


def test_json_round_trip() -> None:
    playlist = SmartPlaylist(
        "90s",
        (
            Rule(RuleField.YEAR, Operator.BETWEEN, 1990, 1999),
            Rule(RuleField.GENRE, Operator.NOT_CONTAINS, "pop"),
        ),
        match_all=False,
        sort=SortOrder.RANDOM,
        limit=20,
    )
    assert from_json(json.loads(json.dumps(to_json(playlist)))) == playlist


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "x", "rules": [{"field": "mood", "operator": "is", "value": "happy"}]},
        {"name": "x", "rules": [{"field": "year", "operator": "is", "value": "1999"}]},
        {"name": "x", "rules": [{"field": "year", "operator": "is", "value": True}]},
        {"name": "x", "rules": "year"},
        {"name": "x", "match": "most"},
        {"name": "x", "sort": "by mood"},
        {"name": ""},
    ],
)
def test_entries_that_cant_be_read_are_refused(entry: dict[str, object]) -> None:
    with pytest.raises((KeyError, TypeError, ValueError)):
        from_json(entry)


# -- Storage ------------------------------------------------------------------------ #


@pytest.fixture
def store(tmp_path: Path) -> SmartPlaylists:
    return SmartPlaylists(tmp_path / "smart_playlists.json")


ROCK = SmartPlaylist("Rock", (Rule(RuleField.GENRE, Operator.CONTAINS, "rock"),))


def test_none_until_one_is_saved(store: SmartPlaylists) -> None:
    assert store.names() == []
    assert store.get("Rock") is None
    assert not store.file.exists()


def test_saved_playlists_are_kept_by_name(store: SmartPlaylists) -> None:
    store.save(SmartPlaylist(" jazz "))
    store.save(ROCK)
    assert store.names() == ["jazz", "Rock"]  # trimmed, by name ignoring case
    assert store.get("ROCK") == ROCK
    assert SmartPlaylists(store.file).get("rock") == ROCK  # kept in the file
    saved = json.loads(store.file.read_text(encoding="utf-8"))
    assert saved["version"] == FORMAT_VERSION


def test_a_name_can_be_used_once(store: SmartPlaylists) -> None:
    store.save(ROCK)
    with pytest.raises(PlaylistNameError, match="already"):
        store.save(SmartPlaylist("rock"))


def test_saving_over_one_changes_or_renames_it(store: SmartPlaylists) -> None:
    store.save(ROCK)
    store.save(SmartPlaylist("rock", limit=5), replacing="Rock")  # a new case is fine
    assert store.names() == ["rock"]
    store.save(SmartPlaylist("Loud", limit=5), replacing="rock")
    assert store.names() == ["Loud"]
    assert store.get("Loud") == SmartPlaylist("Loud", limit=5)


def test_a_rename_cant_take_anothers_name(store: SmartPlaylists) -> None:
    store.save(ROCK)
    store.save(SmartPlaylist("Jazz"))
    with pytest.raises(PlaylistNameError):
        store.save(SmartPlaylist("Jazz"), replacing="Rock")
    assert store.names() == ["Jazz", "Rock"]


def test_a_playlist_with_problems_isnt_saved(store: SmartPlaylists) -> None:
    with pytest.raises(PlaylistNameError, match="Rule 1"):
        store.save(smart(Rule(RuleField.YEAR, Operator.IS, -5)))
    assert store.names() == []


def test_delete(store: SmartPlaylists) -> None:
    store.save(ROCK)
    store.delete("ROCK")
    store.delete("never there")
    assert store.names() == []


def test_unused_names(store: SmartPlaylists) -> None:
    store.save(ROCK)
    store.save(SmartPlaylist("Rock 2"))
    assert store.unused_name("Jazz") == "Jazz"
    assert store.unused_name("rock") == "rock 3"


def test_one_it_cant_read_is_hidden_but_kept(
    store: SmartPlaylists, caplog: pytest.LogCaptureFixture
) -> None:
    future = {"name": "Happy", "rules": [{"field": "mood", "operator": "is", "value": "happy"}]}
    store.file.write_text(json.dumps({"version": 2, "playlists": [future]}), encoding="utf-8")
    assert store.names() == []
    assert "Happy" in caplog.text
    store.save(ROCK)
    assert store.names() == ["Rock"]
    kept = json.loads(store.file.read_text(encoding="utf-8"))["playlists"]
    assert future in kept  # a newer PyAmp still has it
    with pytest.raises(PlaylistNameError):
        store.save(SmartPlaylist("happy"))  # nor is its name taken over


@pytest.mark.parametrize("text", ["not json", "[]", '{"playlists": {}}'])
def test_an_unreadable_file_counts_as_empty(
    store: SmartPlaylists, text: str, caplog: pytest.LogCaptureFixture
) -> None:
    store.file.write_text(text, encoding="utf-8")
    assert store.names() == []
    assert "unreadable" in caplog.text


def test_default_file_is_in_the_user_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("pyamp.library.smart_playlists.default_file", default_file)  # real
    monkeypatch.setattr("platformdirs.user_data_dir", lambda _: str(tmp_path))
    assert SmartPlaylists().file == tmp_path / "smart_playlists.json"


# -- What they match ---------------------------------------------------------------- #


@pytest.fixture
def library(tmp_path: Path) -> Iterator[Catalog]:
    catalog = Catalog(tmp_path / "library.sqlite3")
    music = [
        # (file, title, genre, year, seconds, days ago added)
        ("/m/teardrop.flac", "Teardrop", "Trip Hop", 1998, 330, 2),
        ("/m/angel.mp3", "Angel", "Trip Hop", 1998, 379, 40),
        ("/m/smells.MP3", "Smells Like Teen Spirit", "Grunge", 1991, 301, 400),
        ("/m/so what.m4a", "So What", "Jazz", 1959, 562, 10),
        ("/m/other/100% pure.ogg", "100% Pure", "", 0, 0, 1),
    ]
    catalog.put(
        (
            Track(
                normalise(Path(path)),  # absolute, as the scanner gives (C:\m\... on Windows)
                title,
                artist=title.split()[0],
                genre=genre,
                year=year,
                duration_ms=seconds * 1000 + 500,
            ),
            FileStamp(NOW_NS - days * DAY_NS, 1),
        )
        for path, title, genre, year, seconds, days in music
    )
    yield catalog
    catalog.close()


def titles(catalog: Catalog, *rules: Rule, **options: object) -> list[str]:
    tracks = catalog.smart_tracks(smart(*rules, **options), now_ns=NOW_NS)
    return [track.title for track in tracks]


def every(catalog: Catalog, *rules: Rule, **options: object) -> set[str]:
    return set(titles(catalog, *rules, **options))


ALL = {"Teardrop", "Angel", "Smells Like Teen Spirit", "So What", "100% Pure"}


def test_no_rules_match_the_whole_library(library: Catalog) -> None:
    assert every(library) == ALL
    assert every(library, match_all=False) == ALL


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (Rule(RuleField.GENRE, Operator.CONTAINS, "HOP"), {"Teardrop", "Angel"}),
        (
            Rule(RuleField.GENRE, Operator.NOT_CONTAINS, "hop"),
            {"Smells Like Teen Spirit", "So What", "100% Pure"},
        ),
        (Rule(RuleField.GENRE, Operator.IS, "jazz"), {"So What"}),
        (Rule(RuleField.GENRE, Operator.IS, ""), {"100% Pure"}),  # untagged
        (Rule(RuleField.GENRE, Operator.IS_NOT, "trip hop"), ALL - {"Teardrop", "Angel"}),
        (Rule(RuleField.TITLE, Operator.STARTS_WITH, "s"), {"Smells Like Teen Spirit", "So What"}),
        (Rule(RuleField.TITLE, Operator.CONTAINS, "100%"), {"100% Pure"}),  # % is literal
        (Rule(RuleField.TITLE, Operator.CONTAINS, "_"), set()),  # so is _
        (Rule(RuleField.ARTIST, Operator.IS, "smells"), {"Smells Like Teen Spirit"}),
        (Rule(RuleField.YEAR, Operator.IS, 1998), {"Teardrop", "Angel"}),
        (Rule(RuleField.YEAR, Operator.IS_NOT, 1998), ALL - {"Teardrop", "Angel"}),
        (Rule(RuleField.YEAR, Operator.AT_MOST, 1991), {"Smells Like Teen Spirit", "So What"}),
        (Rule(RuleField.YEAR, Operator.AT_LEAST, 1991), ALL - {"So What", "100% Pure"}),
        (Rule(RuleField.YEAR, Operator.BETWEEN, 1990, 1999), ALL - {"So What", "100% Pure"}),
        (Rule(RuleField.LENGTH, Operator.IS, 330), {"Teardrop"}),  # whole seconds
        (Rule(RuleField.LENGTH, Operator.AT_MOST, 330), {"Teardrop", "Smells Like Teen Spirit"}),
        (Rule(RuleField.LENGTH, Operator.AT_LEAST, 500), {"So What"}),
        (Rule(RuleField.FILE_TYPE, Operator.IS, "MP3"), {"Angel", "Smells Like Teen Spirit"}),
        (
            Rule(RuleField.FILE_TYPE, Operator.IS_NOT, ".mp3"),
            ALL - {"Angel", "Smells Like Teen Spirit"},
        ),
        (Rule(RuleField.FOLDER, Operator.IN_FOLDER, "/m/other"), {"100% Pure"}),
        (Rule(RuleField.FOLDER, Operator.NOT_IN_FOLDER, "/m/other"), ALL - {"100% Pure"}),
        (Rule(RuleField.ADDED, Operator.IN_LAST, 10), {"Teardrop", "So What", "100% Pure"}),
        (Rule(RuleField.ADDED, Operator.NOT_IN_LAST, 30), {"Angel", "Smells Like Teen Spirit"}),
    ],
)
def test_each_rule(library: Catalog, rule: Rule, expected: set[str]) -> None:
    assert every(library, rule) == expected


def test_every_field_and_operator_makes_a_query(library: Catalog) -> None:
    for rule_field in RuleField:
        text = rule_field.kind in (FieldKind.TEXT, FieldKind.FILE_TYPE, FieldKind.FOLDER)
        for operator in OPERATORS[rule_field.kind]:
            rule = Rule(rule_field, operator, "x" if text else 1, 2)
            assert problem(smart(rule)) == ""
            library.smart_tracks(smart(rule))  # doesn't raise
            library.smart_count(smart(rule))


def test_all_or_any(library: Catalog) -> None:
    jazz = Rule(RuleField.GENRE, Operator.IS, "jazz")
    nineties = Rule(RuleField.YEAR, Operator.BETWEEN, 1990, 1999)
    assert every(library, jazz, nineties) == set()
    assert every(library, jazz, nineties, match_all=False) == ALL - {"100% Pure"}


@pytest.mark.parametrize(
    ("order", "expected"),
    [
        (SortOrder.TITLE, ["100% Pure", "Angel", "Smells Like Teen Spirit", "So What", "Teardrop"]),
        (SortOrder.YEAR, ["100% Pure", "So What", "Smells Like Teen Spirit", "Angel", "Teardrop"]),
        (
            SortOrder.NEWEST,
            ["100% Pure", "Teardrop", "So What", "Angel", "Smells Like Teen Spirit"],
        ),
        (
            SortOrder.LONGEST,
            ["So What", "Angel", "Teardrop", "Smells Like Teen Spirit", "100% Pure"],
        ),
        (
            SortOrder.SHORTEST,
            ["100% Pure", "Smells Like Teen Spirit", "Teardrop", "Angel", "So What"],
        ),
    ],
)
def test_sort_orders(library: Catalog, order: SortOrder, expected: list[str]) -> None:
    assert titles(library, sort=order) == expected


def test_album_order_and_artist_order(library: Catalog) -> None:
    assert titles(library) == [t.title for t in library.tracks()]
    assert titles(library, sort=SortOrder.ARTIST)[0] == "100% Pure"  # artist "100%"


def test_a_limit_takes_the_first_in_its_order(library: Catalog) -> None:
    assert titles(library, sort=SortOrder.NEWEST, limit=2) == ["100% Pure", "Teardrop"]
    assert library.smart_count(smart(limit=2), now_ns=NOW_NS) == 2


def test_random_picks_a_new_selection(library: Catalog) -> None:
    picks = {tuple(titles(library, sort=SortOrder.RANDOM, limit=2)) for _ in range(40)}
    assert len(picks) > 1
    assert all(set(pick) <= ALL and len(pick) == 2 for pick in picks)


def test_counts(library: Catalog) -> None:
    hop = Rule(RuleField.GENRE, Operator.CONTAINS, "hop")
    assert library.smart_count(smart(hop), now_ns=NOW_NS) == 2
    assert library.smart_count(smart(), now_ns=NOW_NS) == 5
    assert library.smart_count(smart(limit=50)) == 5  # a limit is only a maximum
