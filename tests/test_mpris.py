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

"""MPRIS tests.

The first part drives :class:`MprisService` with messages that go through
jeepney's real serialiser and parser, so D-Bus type signatures are checked on
every platform. The second part needs a D-Bus session bus (Linux CI runs the
tests under ``dbus-run-session``) and talks to the adapter over the real bus.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from jeepney import DBusAddress, HeaderFields, Message, MessageType, Parser, new_method_call
from PySide6.QtCore import QCoreApplication

from pyamp.core.events import EventBus
from pyamp.core.models import PlaybackState, RepeatMode, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.media_session import MediaCommands, MediaSession, NowPlaying
from pyamp.media_session.mpris import (
    BUS_NAME,
    NO_TRACK,
    OBJECT_PATH,
    PLAYER_IFACE,
    PROPERTIES_IFACE,
    ROOT_IFACE,
    MprisAdapter,
    MprisService,
)
from tests.conftest import FakeBackend, make_tracks

PLAYER = DBusAddress(OBJECT_PATH, bus_name=BUS_NAME, interface=PLAYER_IFACE)
PROPS = DBusAddress(OBJECT_PATH, bus_name=BUS_NAME, interface=PROPERTIES_IFACE)

TRACK = Track(path=Path("/music/song.flac"), title="Song", artist="Band", album="Record")


class WireBus:
    """Round-trips every message through the D-Bus wire format, like a real bus."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        self._serials = count(1)

    def send(self, msg: Message) -> None:
        self.sent.append(wire(msg, next(self._serials)))

    def signals(self, member: str) -> list[tuple[Any, ...]]:
        return [
            m.body
            for m in self.sent
            if m.header.message_type is MessageType.signal
            and m.header.fields[HeaderFields.member] == member
        ]

    def take(self) -> list[Message]:
        sent, self.sent = self.sent, []
        return sent


def wire(msg: Message, serial: int) -> Message:
    parser = Parser()
    parser.add_data(msg.serialise(serial=serial))
    parsed = parser.get_next_message()
    assert parsed is not None
    return parsed


@pytest.fixture
def wire_bus() -> WireBus:
    return WireBus()


@pytest.fixture
def service(player: Player, wire_bus: WireBus) -> MprisService:
    return MprisService(MediaCommands(player), wire_bus.send)


def call(service: MprisService, bus: WireBus, msg: Message) -> Message:
    """Deliver a method call and return the single reply."""
    service.handle(wire(msg, 1000))
    replies = [m for m in bus.take() if m.header.message_type is not MessageType.signal]
    assert len(replies) == 1
    return replies[0]


def get(service: MprisService, bus: WireBus, interface: str, name: str) -> tuple[str, Any]:
    reply = call(service, bus, new_method_call(PROPS, "Get", "ss", (interface, name)))
    assert reply.header.message_type is MessageType.method_return, reply.body
    variant: tuple[str, Any] = reply.body[0]
    return variant


def error_name(msg: Message) -> str:
    assert msg.header.message_type is MessageType.error
    name: str = msg.header.fields[HeaderFields.error_name]
    return name


# -- Service, over the wire format --------------------------------------------- #


def test_identity_and_capabilities(service: MprisService, wire_bus: WireBus) -> None:
    reply = call(service, wire_bus, new_method_call(PROPS, "GetAll", "s", (ROOT_IFACE,)))
    props = reply.body[0]
    assert props["Identity"] == ("s", "PyAmp")
    assert props["CanRaise"] == ("b", False)
    assert props["SupportedUriSchemes"] == ("as", ["file"])


def test_empty_player(service: MprisService, wire_bus: WireBus) -> None:
    assert get(service, wire_bus, PLAYER_IFACE, "PlaybackStatus") == ("s", "Stopped")
    assert get(service, wire_bus, PLAYER_IFACE, "Metadata") == (
        "a{sv}",
        {"mpris:trackid": ("o", NO_TRACK)},
    )
    assert get(service, wire_bus, PLAYER_IFACE, "CanPlay") == ("b", False)


def test_metadata_has_spec_types(service: MprisService, wire_bus: WireBus) -> None:
    service.update(NowPlaying(track=TRACK, state=PlaybackState.PLAYING, duration_ms=185_000))
    sig, metadata = get(service, wire_bus, PLAYER_IFACE, "Metadata")
    assert sig == "a{sv}"
    assert metadata["mpris:length"] == ("x", 185_000_000)  # int64 microseconds
    assert metadata["mpris:trackid"][0] == "o"
    assert metadata["xesam:title"] == ("s", "Song")
    assert metadata["xesam:artist"] == ("as", ["Band"])
    assert metadata["xesam:album"] == ("s", "Record")
    assert metadata["xesam:url"] == ("s", "file:///music/song.flac")


def test_position_is_read_live_in_microseconds(
    service: MprisService, wire_bus: WireBus, player: Player
) -> None:
    player.on_position_changed(12_345)
    assert get(service, wire_bus, PLAYER_IFACE, "Position") == ("x", 12_345_000)


def test_update_emits_only_changed_properties(service: MprisService, wire_bus: WireBus) -> None:
    service.update(NowPlaying(track=TRACK, state=PlaybackState.PLAYING, duration_ms=1000))
    wire_bus.take()
    service.update(
        NowPlaying(track=TRACK, state=PlaybackState.PAUSED, duration_ms=1000, position_ms=500)
    )
    [(interface, changed, invalidated)] = wire_bus.signals("PropertiesChanged")
    assert interface == PLAYER_IFACE
    assert changed == {"PlaybackStatus": ("s", "Paused")}  # Position is never signalled
    assert invalidated == []


def test_new_track_gets_new_track_id(service: MprisService, wire_bus: WireBus) -> None:
    service.update(NowPlaying(track=TRACK))
    first = get(service, wire_bus, PLAYER_IFACE, "Metadata")[1]["mpris:trackid"]
    service.update(NowPlaying(track=Track.from_path(Path("/music/other.mp3"))))
    second = get(service, wire_bus, PLAYER_IFACE, "Metadata")[1]["mpris:trackid"]
    assert first != second


def test_seeked_signal_is_int64_microseconds(service: MprisService, wire_bus: WireBus) -> None:
    service.seeked(42_000)
    assert wire_bus.signals("Seeked") == [(42_000_000,)]
    assert wire_bus.sent[-1].header.fields[HeaderFields.signature] == "x"


def test_transport_methods(
    service: MprisService,
    wire_bus: WireBus,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
) -> None:
    playlist.add(make_tracks(3))
    call(service, wire_bus, new_method_call(PLAYER, "PlayPause"))
    assert player.state is PlaybackState.PLAYING
    call(service, wire_bus, new_method_call(PLAYER, "Next"))
    assert playlist.current_index == 1
    call(service, wire_bus, new_method_call(PLAYER, "Previous"))
    assert playlist.current_index == 0
    call(service, wire_bus, new_method_call(PLAYER, "Pause"))
    assert player.state is PlaybackState.PAUSED
    call(service, wire_bus, new_method_call(PLAYER, "Play"))
    assert player.state is PlaybackState.PLAYING
    call(service, wire_bus, new_method_call(PLAYER, "Stop"))
    assert player.state is PlaybackState.STOPPED


def test_seek_and_set_position(
    service: MprisService,
    wire_bus: WireBus,
    player: Player,
    playlist: Playlist,
    backend: FakeBackend,
) -> None:
    playlist.add(make_tracks(1))
    player.play()
    player.on_duration_changed(60_000)
    service.update(NowPlaying(track=playlist[0], duration_ms=60_000))
    track_id = get(service, wire_bus, PLAYER_IFACE, "Metadata")[1]["mpris:trackid"][1]

    call(service, wire_bus, new_method_call(PLAYER, "Seek", "x", (10_000_000,)))
    assert backend.position == 10_000
    call(service, wire_bus, new_method_call(PLAYER, "Seek", "x", (-4_000_000,)))
    assert backend.position == 6_000

    call(service, wire_bus, new_method_call(PLAYER, "SetPosition", "ox", (track_id, 30_000_000)))
    assert backend.position == 30_000

    # Stale track ids and out-of-range positions are ignored, as the spec requires.
    call(service, wire_bus, new_method_call(PLAYER, "SetPosition", "ox", ("/x/y", 1_000_000)))
    call(service, wire_bus, new_method_call(PLAYER, "SetPosition", "ox", (track_id, 99_000_000)))
    assert backend.position == 30_000


def test_writable_properties(service: MprisService, wire_bus: WireBus, player: Player) -> None:
    def set_(name: str, variant: tuple[str, Any]) -> Message:
        body = (PLAYER_IFACE, name, variant)
        return call(service, wire_bus, new_method_call(PROPS, "Set", "ssv", body))

    set_("Volume", ("d", 1.7))
    assert player.volume == 1.0
    set_("Shuffle", ("b", True))
    assert player.shuffle is True
    set_("LoopStatus", ("s", "Track"))
    assert player.repeat is RepeatMode.ONE

    assert error_name(set_("LoopStatus", ("s", "Sideways"))).endswith("InvalidArgs")
    assert error_name(set_("CanPlay", ("b", True))).endswith("PropertyReadOnly")


def test_errors(service: MprisService, wire_bus: WireBus) -> None:
    unknown_method = call(service, wire_bus, new_method_call(PLAYER, "Explode"))
    assert error_name(unknown_method).endswith("UnknownMethod")

    unknown_prop = call(
        service, wire_bus, new_method_call(PROPS, "Get", "ss", (PLAYER_IFACE, "Nope"))
    )
    assert error_name(unknown_prop).endswith("UnknownProperty")

    other_object = DBusAddress("/somewhere/else", bus_name=BUS_NAME, interface=PLAYER_IFACE)
    unknown_object = call(service, wire_bus, new_method_call(other_object, "Play"))
    assert error_name(unknown_object).endswith("UnknownObject")

    open_uri = call(service, wire_bus, new_method_call(PLAYER, "OpenUri", "s", ("file:///x",)))
    assert error_name(open_uri).endswith("NotSupported")


def test_introspection_lists_interfaces(service: MprisService, wire_bus: WireBus) -> None:
    address = DBusAddress(
        OBJECT_PATH, bus_name=BUS_NAME, interface="org.freedesktop.DBus.Introspectable"
    )
    xml = call(service, wire_bus, new_method_call(address, "Introspect")).body[0]
    assert f'<interface name="{PLAYER_IFACE}">' in xml
    assert f'<interface name="{ROOT_IFACE}">' in xml


# -- Adapter, over a real session bus ------------------------------------------ #

needs_session_bus = pytest.mark.skipif(
    not sys.platform.startswith("linux") or "DBUS_SESSION_BUS_ADDRESS" not in os.environ,
    reason="needs a D-Bus session bus (Linux)",
)


def pump_until[T](qt_app: QCoreApplication, work: Callable[[], T], timeout: float = 5.0) -> T:
    """Run a blocking D-Bus client call on a thread while the Qt loop serves it."""
    result: list[T] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            result.append(work())
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive():
        if time.monotonic() > deadline:
            raise TimeoutError("D-Bus call did not complete")
        qt_app.processEvents()
        time.sleep(0.002)
    if errors:
        raise errors[0]
    return result[0]


@pytest.fixture
def live(
    qt_app: QCoreApplication, bus: EventBus, player: Player, playlist: Playlist
) -> Iterator[MprisAdapter]:
    adapter = MprisAdapter.connect(MediaCommands(player))
    assert adapter is not None
    session = MediaSession(bus, player, playlist, adapter)
    yield adapter
    session.close()


@needs_session_bus
def test_registers_under_the_mpris_name(live: MprisAdapter) -> None:
    assert live.bus_name == BUS_NAME


@needs_session_bus
def test_client_reads_state_and_controls_playback(
    qt_app: QCoreApplication, live: MprisAdapter, player: Player, playlist: Playlist
) -> None:
    from jeepney.io.blocking import open_dbus_connection  # noqa: PLC0415

    playlist.add([TRACK])
    player.play()
    player.on_duration_changed(200_000)

    with open_dbus_connection("SESSION") as client:

        def rpc(msg: Message) -> Message:
            return pump_until(qt_app, lambda: client.send_and_get_reply(msg, timeout=3))

        status = rpc(new_method_call(PROPS, "Get", "ss", (PLAYER_IFACE, "PlaybackStatus")))
        assert status.body[0] == ("s", "Playing")
        metadata = rpc(new_method_call(PROPS, "Get", "ss", (PLAYER_IFACE, "Metadata"))).body[0][1]
        assert metadata["mpris:length"] == ("x", 200_000_000)
        assert metadata["xesam:title"] == ("s", "Song")

        rpc(new_method_call(PLAYER, "PlayPause"))
        assert player.state is PlaybackState.PAUSED


@needs_session_bus
@pytest.mark.skipif(shutil.which("playerctl") is None, reason="playerctl not installed")
def test_playerctl_interop(
    qt_app: QCoreApplication, live: MprisAdapter, player: Player, playlist: Playlist
) -> None:
    playlist.add([TRACK])
    player.play()
    player.on_duration_changed(200_000)

    def playerctl(*args: str) -> str:
        cmd = ["playerctl", "--player=pyamp", *args]
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True)
        return done.stdout.strip()

    fmt = "{{xesam:artist}} - {{xesam:title}} [{{mpris:length}}] {{status}}"
    shown = pump_until(qt_app, lambda: playerctl("metadata", "--format", fmt))
    assert shown == "Band - Song [200000000] Playing"

    pump_until(qt_app, lambda: playerctl("pause"))
    assert player.state is PlaybackState.PAUSED
