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

from __future__ import annotations

import random

import pytest

from pyamp.core.events import EventBus, PlaylistChanged
from pyamp.core.play_order import SequentialOrder, ShuffleOrder
from pyamp.core.playlist import Playlist
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
