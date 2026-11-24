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

"""The playlist model."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from enum import StrEnum

from pyamp.core.events import EventBus, PlaylistChanged
from pyamp.core.models import Track
from pyamp.core.play_order import PlayOrder, SequentialOrder


class SortKey(StrEnum):
    TITLE = "title"
    ARTIST = "artist"
    ALBUM = "album"
    FILENAME = "filename"
    DURATION = "duration"


_SORT_KEYS: dict[SortKey, Callable[[Track], tuple[object, ...]]] = {
    SortKey.TITLE: lambda t: (t.title.casefold(),),
    SortKey.ARTIST: lambda t: (t.artist.casefold(), t.album.casefold(), t.title.casefold()),
    SortKey.ALBUM: lambda t: (t.album.casefold(), t.name.casefold()),
    SortKey.FILENAME: lambda t: (t.name.casefold(),),
    SortKey.DURATION: lambda t: (t.duration_ms,),
}


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
        self._total_ms = 0  # kept up to date, so it's cheap to show while loading
        self._order: PlayOrder = order or SequentialOrder()

    # -- Sequence protocol -------------------------------------------------- #

    def __len__(self) -> int:
        return len(self._tracks)

    def __getitem__(self, index: int) -> Track:  # type: ignore[override]
        return self._tracks[index]

    def __iter__(self) -> Iterator[Track]:
        return iter(self._tracks)

    @property
    def total_duration_ms(self) -> int:
        """The length of all tracks together (live streams count as 0)."""
        return self._total_ms

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
            self._total_ms += sum(t.duration_ms for t in new_tracks)
            self._order.appended(first_new, len(self._tracks), self._current)
            self._changed(appended_from=first_new)

    def remove(self, indices: Iterable[int]) -> None:
        doomed = {i for i in indices if 0 <= i < len(self._tracks)}
        if not doomed:
            return
        new_index: list[int | None] = []
        kept = 0
        for i in range(len(self._tracks)):
            new_index.append(None if i in doomed else kept)
            kept += i not in doomed
        self._order.rearranged(new_index, self._current)
        if self._current is not None:
            if self._current in doomed:
                self._current = None
            else:
                self._current -= sum(1 for i in doomed if i < self._current)
        self._total_ms -= sum(self._tracks[i].duration_ms for i in doomed)
        self._tracks = [t for i, t in enumerate(self._tracks) if i not in doomed]
        self._changed()

    def replace(self, tracks: Iterable[Track]) -> None:
        """Swap in a whole new list (e.g. another saved playlist). Clears the cursor."""
        self._tracks = list(tracks)
        self._total_ms = sum(t.duration_ms for t in self._tracks)
        self._current = None
        self._order.reset()
        self._changed()

    def move(self, rows: Iterable[int], to: int) -> list[int]:
        """Move ``rows`` (as a block, keeping their order) to before row ``to``.

        Returns the rows the moved tracks ended up at, e.g. to keep them selected.
        """
        moving = sorted({r for r in rows if 0 <= r < len(self._tracks)})
        if not moving:
            return []
        to = max(0, min(to, len(self._tracks)))
        insert_at = to - sum(1 for r in moving if r < to)
        moving_set = set(moving)
        order = [i for i in range(len(self._tracks)) if i not in moving_set]
        order[insert_at:insert_at] = moving
        self._reorder(order)
        return list(range(insert_at, insert_at + len(moving)))

    def sort(self, key: SortKey, *, descending: bool = False) -> None:
        """Sort by ``key`` (case-insensitive and stable: ties keep their order)."""
        sort_key = _SORT_KEYS[key]
        order = sorted(
            range(len(self._tracks)),
            key=lambda i: sort_key(self._tracks[i]),
            reverse=descending,
        )
        self._reorder(order)

    def reverse(self) -> None:
        self._reorder(list(reversed(range(len(self._tracks)))))

    def remove_duplicates(self) -> int:
        """Drop repeated files (keeping the playing copy, else the first). Returns how many."""
        keep: dict[object, int] = {}
        for i, track in enumerate(self._tracks):
            keep.setdefault(track.location, i)
        if self._current is not None:
            keep[self._tracks[self._current].location] = self._current
        doomed = [i for i in range(len(self._tracks)) if keep[self._tracks[i].location] != i]
        self.remove(doomed)
        return len(doomed)

    def _reorder(self, order: list[int]) -> None:
        """Rearrange so that new position ``n`` holds old track ``order[n]``."""
        if order == list(range(len(self._tracks))):
            return
        self._tracks = [self._tracks[i] for i in order]
        new_index: list[int | None] = [None] * len(order)
        for new, old in enumerate(order):
            new_index[old] = new
        self._order.rearranged(new_index, self._current)
        if self._current is not None:
            self._current = new_index[self._current]  # the playing track keeps playing
        self._changed()

    def clear(self) -> None:
        if self._tracks:
            self._tracks.clear()
            self._total_ms = 0
            self._current = None
            self._order.reset()
            self._changed()

    def _changed(self, *, appended_from: int | None = None) -> None:
        self._bus.publish(PlaylistChanged(appended_from))
