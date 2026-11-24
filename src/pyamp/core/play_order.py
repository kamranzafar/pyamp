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

"""Play-order strategies (Strategy pattern).

A :class:`PlayOrder` decides which playlist index comes next or before. The
playlist delegates to whichever strategy is active, so adding a new mode (e.g.
"shuffle by album") means adding a class here and nothing else.

Repeat handling is deliberately *not* a strategy. Strategies only answer
"what comes next, and may we wrap around?". The player decides when wrapping
is allowed and how repeat-one behaves.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Protocol


class PlayOrder(Protocol):
    def next_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        """Index to play after ``current``, or ``None`` if the end was reached."""
        ...

    def previous_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        """Index to play before ``current``, or ``None`` if the start was reached."""
        ...

    def appended(self, start: int, length: int, current: int | None) -> None:
        """Tracks ``start`` to ``length - 1`` were added at the end of the playlist."""
        ...

    def rearranged(self, new_index: Sequence[int | None], previous: int | None) -> None:
        """Tracks were removed or moved: old index ``i`` is now ``new_index[i]`` (None if
        removed). ``previous`` is the index of the current track before the change."""
        ...

    def reset(self) -> None:
        """Forget any internal state (the playlist was replaced or cleared)."""
        ...


class SequentialOrder:
    """Plays tracks in playlist order."""

    def next_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        if length == 0:
            return None
        if current is None:
            return 0
        if current + 1 < length:
            return current + 1
        return 0 if wrap else None

    def previous_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        if length == 0:
            return None
        if current is None:
            return length - 1 if wrap else 0
        if current > 0:
            return current - 1
        return length - 1 if wrap else None

    # Stateless: playlist changes don't affect it.
    def appended(self, start: int, length: int, current: int | None) -> None:
        pass

    def rearranged(self, new_index: Sequence[int | None], previous: int | None) -> None:
        pass

    def reset(self) -> None:
        pass


class ShuffleOrder:
    """Plays every track exactly once in random order before repeating.

    Uses a shuffled permutation rather than picking randomly each time, so
    "previous" retraces the actual history and no track is played twice
    before all have been played. Editing the playlist keeps that promise: added tracks
    are shuffled into the part not played yet, and removed or moved tracks keep their
    place in the order.
    """

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self._sequence: list[int] = []

    def next_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        if length == 0:
            return None
        self._ensure_sequence(length, current)
        if current is None:
            return self._sequence[0]
        position = self._sequence.index(current)
        if position + 1 < length:
            return self._sequence[position + 1]
        if not wrap:
            return None
        self._reshuffle(length, avoid_first=current)
        return self._sequence[0]

    def previous_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        if length == 0:
            return None
        self._ensure_sequence(length, current)
        if current is None:
            return self._sequence[0]
        position = self._sequence.index(current)
        if position > 0:
            return self._sequence[position - 1]
        return self._sequence[-1] if wrap else None

    def appended(self, start: int, length: int, current: int | None) -> None:
        if not self._sequence:
            return  # not shuffled yet; built when first needed
        if len(self._sequence) != start:
            self._sequence.clear()  # out of step (shouldn't happen): start afresh
            return
        played = self._played_count(current)
        # Inserting each at a uniformly random place among the unplayed tracks keeps that
        # part a uniformly random order.
        for index in range(start, length):
            self._sequence.insert(self._rng.randint(played, len(self._sequence)), index)

    def rearranged(self, new_index: Sequence[int | None], previous: int | None) -> None:
        if not self._sequence:
            return
        if len(self._sequence) != len(new_index):
            self._sequence.clear()
            return
        played_count = self._played_count(previous)
        played = [j for i in self._sequence[:played_count] if (j := new_index[i]) is not None]
        unplayed = [j for i in self._sequence[played_count:] if (j := new_index[i]) is not None]
        if previous is not None and new_index[previous] is None:
            # The playing track went away, so nothing is current: what comes "next" (from
            # no current track) is the first track not played yet.
            self._sequence = unplayed + played
        else:
            self._sequence = played + unplayed

    def reset(self) -> None:
        self._sequence.clear()

    def _played_count(self, current: int | None) -> int:
        """How many tracks of the order have been played (up to and including ``current``)."""
        if current is None:
            return 0
        try:
            return self._sequence.index(current) + 1
        except ValueError:
            return 0

    def _ensure_sequence(self, length: int, current: int | None) -> None:
        if len(self._sequence) == length:
            return
        self._sequence = list(range(length))
        self._rng.shuffle(self._sequence)
        # The track playing right now counts as already played.
        if current is not None and 0 <= current < length:
            self._sequence.remove(current)
            self._sequence.insert(0, current)

    def _reshuffle(self, length: int, *, avoid_first: int) -> None:
        self._sequence = list(range(length))
        self._rng.shuffle(self._sequence)
        # Avoid playing the same song twice in a row across the wrap boundary.
        if length > 1 and self._sequence[0] == avoid_first:
            self._sequence[0], self._sequence[-1] = self._sequence[-1], self._sequence[0]
