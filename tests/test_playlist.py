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

import random
from pathlib import Path

import pytest

from pyamp.core.events import EventBus, PlaylistChanged
from pyamp.core.models import Track
from pyamp.core.play_order import SequentialOrder, ShuffleOrder
from pyamp.core.playlist import Playlist, SortKey
from tests.conftest import make_tracks


class TestSequentialOrder:
    order = SequentialOrder()

    def test_next(self) -> None:
        assert self.order.next_index(None, 3, wrap=False) == 0
        assert self.order.next_index(1, 3, wrap=False) == 2
        assert self.order.next_index(2, 3, wrap=False) is None
        assert self.order.next_index(2, 3, wrap=True) == 0

    def test_previous(self) -> None:
        assert self.order.previous_index(2, 3, wrap=False) == 1
        assert self.order.previous_index(0, 3, wrap=False) is None
        assert self.order.previous_index(0, 3, wrap=True) == 2

    def test_empty(self) -> None:
        assert self.order.next_index(None, 0, wrap=True) is None


class TestShuffleOrder:
    def test_visits_every_track_once_before_ending(self) -> None:
        order = ShuffleOrder(random.Random(42))
        visited, current = [], None
        while (current := order.next_index(current, 10, wrap=False)) is not None:
            visited.append(current)
        assert sorted(visited) == list(range(10))

    def test_previous_retraces_history(self) -> None:
        order = ShuffleOrder(random.Random(1))
        first = order.next_index(None, 5, wrap=False)
        second = order.next_index(first, 5, wrap=False)
        assert order.previous_index(second, 5, wrap=False) == first

    def test_wrap_does_not_repeat_last_track_immediately(self) -> None:
        for seed in range(50):
            order = ShuffleOrder(random.Random(seed))
            current = None
            for _ in range(3):
                current = order.next_index(current, 3, wrap=True)
            assert order.next_index(current, 3, wrap=True) != current


class TestPlaylist:
    def test_add_publishes_change(self, playlist: Playlist, bus: EventBus) -> None:
        changes: list[PlaylistChanged] = []
        bus.subscribe(PlaylistChanged, changes.append)
        playlist.add(make_tracks(2))
        playlist.add([])  # no-op, no event
        assert len(playlist) == 2
        assert len(changes) == 1

    def test_remove_before_current_shifts_cursor(self, playlist: Playlist) -> None:
        tracks = make_tracks(5)
        playlist.add(tracks)
        playlist.set_current(3)
        playlist.remove([0, 1])
        assert playlist.current_index == 1
        assert playlist.current_track == tracks[3]

    def test_remove_current_clears_cursor(self, playlist: Playlist) -> None:
        playlist.add(make_tracks(3))
        playlist.set_current(1)
        playlist.remove([1])
        assert playlist.current_index is None

    def test_set_current_validates(self, playlist: Playlist) -> None:
        playlist.add(make_tracks(1))
        with pytest.raises(IndexError):
            playlist.set_current(5)


def test_add_reports_where_tracks_were_appended(playlist: Playlist, bus: EventBus) -> None:
    changes: list[PlaylistChanged] = []
    bus.subscribe(PlaylistChanged, changes.append)
    playlist.add(make_tracks(2))
    playlist.add(make_tracks(3))
    playlist.remove([0])
    assert [c.appended_from for c in changes] == [0, 2, None]


# -- Editing: move, sort, reverse, duplicates, replace -------------------------------- #


def titles(playlist: Playlist) -> list[str]:
    return [t.title for t in playlist]


def named(*names: str, **durations: int) -> list[Track]:
    return [
        Track(location=Path(f"/m/{n}.mp3"), title=n, duration_ms=durations.get(n, 0)) for n in names
    ]


def test_move_block_down_and_up(playlist: Playlist) -> None:
    playlist.add(named("a", "b", "c", "d", "e"))
    assert playlist.move([0, 1], 4) == [2, 3]  # before "e"
    assert titles(playlist) == ["c", "d", "a", "b", "e"]
    assert playlist.move([3, 4], 0) == [0, 1]
    assert titles(playlist) == ["b", "e", "c", "d", "a"]


def test_move_to_end_and_ignores_bad_rows(playlist: Playlist) -> None:
    playlist.add(named("a", "b", "c"))
    assert playlist.move([0, 99], 3) == [2]
    assert titles(playlist) == ["b", "c", "a"]
    assert playlist.move([], 0) == []
    assert playlist.move([1], 1) == [1]  # moving onto itself changes nothing


def test_move_keeps_the_playing_track(playlist: Playlist) -> None:
    playlist.add(named("a", "b", "c", "d"))
    playlist.set_current(1)  # "b"
    playlist.move([0], 4)
    assert playlist.current_track is not None
    assert playlist.current_track.title == "b"
    assert playlist.current_index == 0


def test_move_publishes_one_change(playlist: Playlist, bus: EventBus) -> None:
    playlist.add(named("a", "b"))
    events: list[PlaylistChanged] = []
    bus.subscribe(PlaylistChanged, events.append)
    playlist.move([0], 2)
    assert events == [PlaylistChanged()]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (SortKey.TITLE, ["apple", "Banana", "cherry"]),
        (SortKey.ARTIST, ["Banana", "apple", "cherry"]),
        (SortKey.FILENAME, ["Banana", "apple", "cherry"]),
        (SortKey.DURATION, ["cherry", "Banana", "apple"]),
    ],
)
def test_sort(playlist: Playlist, key: SortKey, expected: list[str]) -> None:
    playlist.add(
        [
            Track(location=Path("/m/b.mp3"), title="apple", artist="Zed", duration_ms=300),
            Track(location=Path("/m/a.mp3"), title="Banana", artist="alpha", duration_ms=200),
            Track(location=Path("/m/c.mp3"), title="cherry", artist="Zed", duration_ms=100),
        ]
    )
    playlist.sort(key)
    assert titles(playlist) == expected


def test_sort_descending_and_stable(playlist: Playlist) -> None:
    playlist.add(named("x", "y", "z", x=1, y=1, z=2))
    playlist.sort(SortKey.DURATION, descending=True)
    assert titles(playlist) == ["z", "x", "y"]  # x and y tie: original order kept


def test_sort_and_reverse_keep_the_playing_track(playlist: Playlist) -> None:
    playlist.add(named("c", "a", "b"))
    playlist.set_current(0)  # "c"
    playlist.sort(SortKey.TITLE)
    assert playlist.current_index == 2
    playlist.reverse()
    assert titles(playlist) == ["c", "b", "a"]
    assert playlist.current_index == 0


def test_already_sorted_publishes_nothing(playlist: Playlist, bus: EventBus) -> None:
    playlist.add(named("a", "b"))
    events: list[PlaylistChanged] = []
    bus.subscribe(PlaylistChanged, events.append)
    playlist.sort(SortKey.TITLE)
    assert events == []


def test_remove_duplicates_keeps_the_playing_copy(playlist: Playlist) -> None:
    a, b = named("a", "b")
    playlist.add([a, b, a, b, a])
    playlist.set_current(2)  # the second "a"
    assert playlist.remove_duplicates() == 3
    assert titles(playlist) == ["b", "a"]
    assert playlist.current_index == 1


def test_replace_swaps_the_list_and_clears_the_cursor(playlist: Playlist) -> None:
    playlist.add(named("a", "b"))
    playlist.set_current(1)
    playlist.replace(named("x"))
    assert titles(playlist) == ["x"]
    assert playlist.current_index is None
