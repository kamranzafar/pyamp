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
