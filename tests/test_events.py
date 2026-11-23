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

from pyamp.core.events import EventBus, PlaylistChanged, VolumeChanged


def test_handlers_receive_only_their_event_type(bus: EventBus) -> None:
    volumes: list[float] = []
    bus.subscribe(VolumeChanged, lambda e: volumes.append(e.volume))
    bus.publish(PlaylistChanged())
    bus.publish(VolumeChanged(0.5))
    assert volumes == [0.5]


def test_unsubscribe(bus: EventBus) -> None:
    seen: list[object] = []
    unsubscribe = bus.subscribe(PlaylistChanged, seen.append)
    unsubscribe()
    unsubscribe()  # idempotent
    bus.publish(PlaylistChanged())
    assert seen == []


def test_failing_handler_does_not_block_others(bus: EventBus) -> None:
    seen: list[object] = []

    def broken(_: PlaylistChanged) -> None:
        raise RuntimeError("boom")

    bus.subscribe(PlaylistChanged, broken)
    bus.subscribe(PlaylistChanged, seen.append)
    bus.publish(PlaylistChanged())
    assert len(seen) == 1
