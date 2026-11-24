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
from __future__ import annotations

from pathlib import Path

import pytest

from pyamp.core.models import Track
from pyamp.library.saved_playlists import (
    LOCK_RETRY_S,
    PlaylistNameError,
    SavedPlaylists,
    retry_while_locked,
    validate_name,
)


@pytest.fixture
def saved(tmp_path: Path) -> SavedPlaylists:
    return SavedPlaylists(tmp_path / "playlists")


def tracks(tmp_path: Path, *names: str) -> list[Track]:
    return [Track.from_path(tmp_path / f"{n}.mp3") for n in names]


def test_empty_directory_has_no_playlists(saved: SavedPlaylists) -> None:
    assert saved.names() == []
    assert not saved.exists("Anything")


def test_create_picks_unique_names(saved: SavedPlaylists) -> None:
    assert saved.create() == "New playlist"
    assert saved.create() == "New playlist 2"
    assert saved.create("Morning") == "Morning"
    assert saved.names() == ["Morning", "New playlist", "New playlist 2"]
    assert saved.read_paths("Morning") == []


def test_save_and_read_round_trip(saved: SavedPlaylists, tmp_path: Path) -> None:
    songs = tracks(tmp_path, "a", "b")
    saved.save("Mix", songs)
    assert saved.read_paths("mix") == [t.path for t in songs]  # names are case-insensitive


def test_save_replaces_atomically(saved: SavedPlaylists, tmp_path: Path) -> None:
    saved.save("Mix", tracks(tmp_path, "a"))
    saved.save("Mix", tracks(tmp_path, "b", "c"))
    assert [p.name for p in saved.read_paths("Mix")] == ["b.mp3", "c.mp3"]
    assert sorted(p.name for p in saved.directory.iterdir()) == ["Mix.m3u8"]  # no temp left


def test_names_are_sorted_case_insensitively_and_ignore_other_files(
    saved: SavedPlaylists,
) -> None:
    for name in ("beta", "Alpha", "gamma"):
        saved.create(name)
    (saved.directory / "notes.txt").write_text("hi")
    (saved.directory / ".Mix.m3u8.tmp").write_text("")
    assert saved.names() == ["Alpha", "beta", "gamma"]


def test_rename(saved: SavedPlaylists, tmp_path: Path) -> None:
    saved.save("Old", tracks(tmp_path, "a"))
    assert saved.rename("old", "  New  ") == "New"
    assert saved.names() == ["New"]
    assert len(saved.read_paths("New")) == 1


def test_rename_changing_only_case_is_allowed(saved: SavedPlaylists) -> None:
    saved.create("jazz")
    assert saved.rename("jazz", "Jazz") == "Jazz"
    assert saved.names() == ["Jazz"]


def test_rename_onto_an_existing_name_is_refused(saved: SavedPlaylists) -> None:
    saved.create("One")
    saved.create("Two")
    with pytest.raises(PlaylistNameError, match='already a playlist called "Two"'):
        saved.rename("One", "two")
    assert saved.names() == ["One", "Two"]


def test_delete(saved: SavedPlaylists) -> None:
    saved.create("Gone")
    saved.delete("GONE")
    saved.delete("never existed")  # no error
    assert saved.names() == []


def test_missing_playlist_errors(saved: SavedPlaylists) -> None:
    with pytest.raises(FileNotFoundError):
        saved.read_paths("nope")
    with pytest.raises(FileNotFoundError):
        saved.rename("nope", "x")


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("", "needs a name"),
        ("   ", "needs a name"),
        ("a" * 101, "at most 100"),
        (".hidden", "can't start"),
        ("AC/DC", "can't start"),
        ('Say "hi"', "can't start"),
        ("tab\there", "can't start"),
    ],
)
def test_invalid_names(name: str, message: str) -> None:
    with pytest.raises(PlaylistNameError, match=message):
        validate_name(name)


def test_valid_names_are_trimmed() -> None:
    assert validate_name("  Road trip 2026 ") == "Road trip 2026"
    name = "Caf\N{LATIN SMALL LETTER E WITH ACUTE} \N{EN DASH} \N{EIGHTH NOTE}"
    assert validate_name(name) == name  # Unicode is fine


# -- Windows: files open elsewhere ------------------------------------------------------ #


def test_retries_while_windows_reports_the_file_in_use() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def busy_twice() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "The process cannot access the file")
        return "done"

    assert retry_while_locked(busy_twice, platform="win32", sleep=sleeps.append) == "done"
    assert len(calls) == 3
    assert len(sleeps) == 2


def test_gives_up_after_about_a_second() -> None:
    sleeps: list[float] = []

    def always_busy() -> None:
        raise PermissionError(32, "in use")

    with pytest.raises(PermissionError):
        retry_while_locked(always_busy, platform="win32", sleep=sleeps.append)
    assert sum(sleeps) == pytest.approx(LOCK_RETRY_S, abs=0.1)


def test_no_retries_elsewhere() -> None:
    calls: list[int] = []

    def denied() -> None:
        calls.append(1)
        raise PermissionError(13, "Permission denied")  # a real permission problem

    with pytest.raises(PermissionError):
        retry_while_locked(denied, platform="darwin", sleep=lambda _: None)
    assert calls == [1]
