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

"""The playlist model."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence

from pyamp.core.events import EventBus, PlaylistChanged
from pyamp.core.models import Track
from pyamp.core.play_order import PlayOrder, SequentialOrder


class Playlist(Sequence[Track]):
    """An ordered list of tracks with a "current" cursor.

    It is a read-only :class:`~collections.abc.Sequence` to the outside world.
    All mutation goes through methods that keep the cursor consistent and
    publish :class:`PlaylistChanged`.
    """

    def __init__(self, bus: EventBus, order: PlayOrder | None = None) -> None:
        self._bus = bus
        self._tracks: list[Track] = []
        self._current: int | None = None
        self._order: PlayOrder = order or SequentialOrder()

    # -- Sequence protocol -------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._tracks)

    def __getitem__(self, index: int) -> Track:  # type: ignore[override]
        return self._tracks[index]

    def __iter__(self) -> Iterator[Track]:
        return iter(self._tracks)

    # -- Cursor ------------------------------------------------------------- #

    @property
    def current_index(self) -> int | None:
        return self._current

    @property
    def current_track(self) -> Track | None:
        return None if self._current is None else self._tracks[self._current]

    def set_current(self, index: int | None) -> Track | None:
        if index is not None and not 0 <= index < len(self._tracks):
            raise IndexError(f"playlist index {index} out of range (len={len(self._tracks)})")
        self._current = index
        return self.current_track

    def next_index(self, *, wrap: bool) -> int | None:
        return self._order.next_index(self._current, len(self._tracks), wrap=wrap)

    def previous_index(self, *, wrap: bool) -> int | None:
        return self._order.previous_index(self._current, len(self._tracks), wrap=wrap)

    # -- Play order --------------------------------------------------------- #

    @property
    def play_order(self) -> PlayOrder:
        return self._order

    @play_order.setter
    def play_order(self, order: PlayOrder) -> None:
        self._order = order

    # -- Mutation ----------------------------------------------------------- #

    def add(self, tracks: Iterable[Track]) -> None:
        new_tracks = list(tracks)
        if new_tracks:
            first_new = len(self._tracks)
            self._tracks.extend(new_tracks)
            self._changed(appended_from=first_new)

    def remove(self, indices: Iterable[int]) -> None:
        doomed = {i for i in indices if 0 <= i < len(self._tracks)}
        if not doomed:
            return
        if self._current is not None:
            if self._current in doomed:
                self._current = None
            else:
                self._current -= sum(1 for i in doomed if i < self._current)
        self._tracks = [t for i, t in enumerate(self._tracks) if i not in doomed]
        self._changed()

    def clear(self) -> None:
        if self._tracks:
            self._tracks.clear()
            self._current = None
            self._changed()

    def _changed(self, *, appended_from: int | None = None) -> None:
        self._order.reset()
        self._bus.publish(PlaylistChanged(appended_from))
