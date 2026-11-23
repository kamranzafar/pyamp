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
from typing import Protocol


class PlayOrder(Protocol):
    def next_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        """Index to play after ``current``, or ``None`` if the end was reached."""
        ...

    def previous_index(self, current: int | None, length: int, *, wrap: bool) -> int | None:
        """Index to play before ``current``, or ``None`` if the start was reached."""
        ...

    def reset(self) -> None:
        """Forget any internal state; called whenever the playlist changes."""
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

    def reset(self) -> None:
        """Stateless; nothing to reset."""


class ShuffleOrder:
    """Plays every track exactly once in random order before repeating.

    Uses a shuffled permutation rather than picking randomly each time, so
    "previous" retraces the actual history and no track is played twice
    before all have been played.
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

    def reset(self) -> None:
        self._sequence.clear()

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
