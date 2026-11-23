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

"""Linux integration via MPRIS (https://specifications.freedesktop.org/mpris-spec/latest/).

Desktop media widgets, media keys and ``playerctl`` all talk to players over
MPRIS on the D-Bus session bus.

:class:`MprisService` implements the protocol on top of jeepney's message
objects and knows nothing about sockets, so it is unit-testable anywhere.
:class:`MprisAdapter` connects it to the session bus and reads incoming
messages through a ``QSocketNotifier``, so every handler runs on the Qt main
thread like the rest of the application.
"""

from __future__ import annotations

import contextlib
import logging
import os
import select
from collections.abc import Callable
from typing import Any

from jeepney import (
    DBusAddress,
    HeaderFields,
    Message,
    MessageFlag,
    MessageType,
    message_bus,
    new_error,
    new_method_return,
    new_signal,
)
from jeepney.io.blocking import DBusConnection, open_dbus_connection
from PySide6.QtCore import QSocketNotifier

from pyamp import APP_NAME
from pyamp.core.models import PlaybackState, RepeatMode
from pyamp.media_session.session import MediaCommands, NowPlaying

log = logging.getLogger(__name__)

BUS_NAME = "org.mpris.MediaPlayer2.pyamp"
OBJECT_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
INTROSPECTABLE_IFACE = "org.freedesktop.DBus.Introspectable"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

_DO_NOT_QUEUE = 4
_PRIMARY_OWNER = 1

_STATUS = {
    PlaybackState.PLAYING: "Playing",
    PlaybackState.PAUSED: "Paused",
    PlaybackState.STOPPED: "Stopped",
}
_LOOP_STATUS = {RepeatMode.OFF: "None", RepeatMode.ALL: "Playlist", RepeatMode.ONE: "Track"}
_REPEAT_MODES = {status: mode for mode, status in _LOOP_STATUS.items()}

_MIME_TYPES = [
    "audio/mpeg",
    "audio/flac",
    "audio/ogg",
    "audio/opus",
    "audio/mp4",
    "audio/aac",
    "audio/wav",
    "audio/aiff",
    "audio/x-ms-wma",
]

#: A D-Bus variant: (signature, value), as jeepney expects for ``v``.
type Variant = tuple[str, Any]
type Send = Callable[[Message], None]


class DBusError(Exception):
    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name


class MprisService:
    """The MPRIS object at ``/org/mpris/MediaPlayer2``, independent of any transport."""

    def __init__(self, commands: MediaCommands, send: Send) -> None:
        self._commands = commands
        self._send = send
        self._emitter = DBusAddress(OBJECT_PATH, interface=PROPERTIES_IFACE)
        self._now = NowPlaying()
        self._track_serial = 0
        self._player_methods: dict[str, Callable[..., None]] = {
            "Next": commands.next,
            "Previous": commands.previous,
            "Pause": commands.pause,
            "PlayPause": commands.play_pause,
            "Stop": commands.stop,
            "Play": commands.play,
            "Seek": self._seek,
            "SetPosition": self._set_position,
            "OpenUri": self._open_uri,
        }
        self._published: dict[str, dict[str, Variant]] = {
            ROOT_IFACE: self._root_properties(),
            PLAYER_IFACE: self._player_properties(),
        }

    # -- Adapter-facing API ------------------------------------------------- #

    def update(self, now_playing: NowPlaying) -> None:
        if now_playing.track != self._now.track:
            self._track_serial += 1
        self._now = now_playing
        current = {ROOT_IFACE: self._root_properties(), PLAYER_IFACE: self._player_properties()}
        for interface, properties in current.items():
            previous = self._published[interface]
            changed = {k: v for k, v in properties.items() if previous.get(k) != v}
            changed.pop("Position", None)  # the spec forbids signalling position changes
            if changed:
                self._emit_properties_changed(interface, changed)
        self._published = current

    def seeked(self, position_ms: int) -> None:
        emitter = DBusAddress(OBJECT_PATH, interface=PLAYER_IFACE)
        self._send(new_signal(emitter, "Seeked", "x", (position_ms * 1000,)))

    # -- Incoming messages -------------------------------------------------- #

    def handle(self, msg: Message) -> None:
        """Process one message from the bus (anything but method calls is ignored)."""
        if msg.header.message_type is not MessageType.method_call:
            return
        fields = msg.header.fields
        if fields.get(HeaderFields.path) != OBJECT_PATH:
            self._reply_error(msg, "org.freedesktop.DBus.Error.UnknownObject", "No such object")
            return
        interface = fields.get(HeaderFields.interface)
        member = fields.get(HeaderFields.member, "")
        try:
            signature, body = self._dispatch(interface, member, msg.body)
        except DBusError as exc:
            self._reply_error(msg, exc.name, str(exc))
            return
        except Exception:
            log.exception("MPRIS %s.%s failed", interface, member)
            self._reply_error(msg, "org.freedesktop.DBus.Error.Failed", "Internal error")
            return
        if not msg.header.flags & MessageFlag.no_reply_expected:
            self._send(new_method_return(msg, signature, body))

    def _dispatch(self, interface: str | None, member: str, args: tuple[Any, ...]) -> Variant:
        if interface in (None, INTROSPECTABLE_IFACE) and member == "Introspect":
            return "s", (_INTROSPECTION_XML,)
        if interface in (None, PROPERTIES_IFACE) and member in {"Get", "GetAll", "Set"}:
            return self._properties_call(member, args)
        if interface in (None, PLAYER_IFACE) and member in self._player_methods:
            self._player_methods[member](*args)
            return "", ()
        if interface in (None, ROOT_IFACE) and member in {"Raise", "Quit"}:
            return "", ()  # CanRaise and CanQuit are False
        raise DBusError("org.freedesktop.DBus.Error.UnknownMethod", f"Unknown method {member}")

    # -- org.freedesktop.DBus.Properties ------------------------------------ #

    def _properties_call(self, member: str, args: tuple[Any, ...]) -> Variant:
        if member == "Get":
            interface, name = args
            properties = self._properties_of(interface)
            if name not in properties:
                raise DBusError("org.freedesktop.DBus.Error.UnknownProperty", name)
            return "v", (properties[name],)
        if member == "GetAll":
            return "a{sv}", (self._properties_of(args[0]),)
        interface, name, (_, value) = args
        self._set_property(interface, name, value)
        return "", ()

    def _properties_of(self, interface: str) -> dict[str, Variant]:
        if interface == ROOT_IFACE:
            return self._root_properties()
        if interface == PLAYER_IFACE:
            return self._player_properties()
        raise DBusError("org.freedesktop.DBus.Error.UnknownInterface", interface)

    def _set_property(self, interface: str, name: str, value: Any) -> None:
        if interface == PLAYER_IFACE and name == "Volume":
            self._commands.set_volume(max(0.0, min(float(value), 1.0)))
        elif interface == PLAYER_IFACE and name == "Shuffle":
            self._commands.set_shuffle(bool(value))
        elif interface == PLAYER_IFACE and name == "LoopStatus":
            if value not in _REPEAT_MODES:
                raise DBusError("org.freedesktop.DBus.Error.InvalidArgs", f"Bad LoopStatus {value}")
            self._commands.set_repeat(_REPEAT_MODES[value])
        elif interface == PLAYER_IFACE and name == "Rate":
            pass  # only 1.0 is supported (MinimumRate == MaximumRate == 1.0)
        else:
            raise DBusError("org.freedesktop.DBus.Error.PropertyReadOnly", f"{name} is read-only")

    def _root_properties(self) -> dict[str, Variant]:
        return {
            "CanQuit": ("b", False),
            "CanRaise": ("b", False),
            "HasTrackList": ("b", False),
            "Identity": ("s", APP_NAME),
            "SupportedUriSchemes": ("as", ["file"]),
            "SupportedMimeTypes": ("as", _MIME_TYPES),
        }

    def _player_properties(self) -> dict[str, Variant]:
        now = self._now
        return {
            "PlaybackStatus": ("s", _STATUS[now.state]),
            "LoopStatus": ("s", _LOOP_STATUS[now.repeat]),
            "Rate": ("d", 1.0),
            "Shuffle": ("b", now.shuffle),
            "Metadata": ("a{sv}", self._metadata()),
            "Volume": ("d", now.volume),
            "Position": ("x", self._commands.position_ms * 1000),
            "MinimumRate": ("d", 1.0),
            "MaximumRate": ("d", 1.0),
            "CanGoNext": ("b", now.can_go_next),
            "CanGoPrevious": ("b", now.can_go_previous),
            "CanPlay": ("b", now.can_play),
            "CanPause": ("b", now.can_play),
            "CanSeek": ("b", now.can_seek),
            "CanControl": ("b", True),
        }

    def _metadata(self) -> dict[str, Variant]:
        track = self._now.track
        if track is None:
            return {"mpris:trackid": ("o", NO_TRACK)}
        metadata: dict[str, Variant] = {
            "mpris:trackid": ("o", self._track_id),
            "xesam:title": ("s", track.title),
            "xesam:url": ("s", track.path.absolute().as_uri()),
        }
        if self._now.duration_ms:
            metadata["mpris:length"] = ("x", self._now.duration_ms * 1000)
        if track.artist:
            metadata["xesam:artist"] = ("as", [track.artist])
        if track.album:
            metadata["xesam:album"] = ("s", track.album)
        return metadata

    @property
    def _track_id(self) -> str:
        return f"/org/pyamp/track/{self._track_serial}"

    # -- org.mpris.MediaPlayer2.Player methods ------------------------------ #

    def _seek(self, offset_us: int) -> None:
        self._commands.seek_by(offset_us // 1000)

    def _set_position(self, track_id: str, position_us: int) -> None:
        # Per the spec, stale track ids and out-of-range positions are ignored.
        now = self._now
        if now.track is None or track_id != self._track_id:
            return
        if 0 <= position_us <= now.duration_ms * 1000:
            self._commands.seek_to(position_us // 1000)

    def _open_uri(self, uri: str) -> None:
        raise DBusError("org.freedesktop.DBus.Error.NotSupported", "OpenUri is not supported")

    # -- Helpers ------------------------------------------------------------ #

    def _emit_properties_changed(self, interface: str, changed: dict[str, Variant]) -> None:
        body: tuple[str, dict[str, Variant], list[str]] = (interface, changed, [])
        self._send(new_signal(self._emitter, "PropertiesChanged", "sa{sv}as", body))

    def _reply_error(self, msg: Message, name: str, text: str) -> None:
        if not msg.header.flags & MessageFlag.no_reply_expected:
            self._send(new_error(msg, name, "s", (text,)))


class MprisAdapter:
    """Publishes an :class:`MprisService` on the D-Bus session bus."""

    def __init__(self, commands: MediaCommands, connection: DBusConnection, bus_name: str) -> None:
        self.bus_name = bus_name
        self._connection = connection
        self._service = MprisService(commands, connection.send)
        self._notifier = QSocketNotifier(connection.sock.fileno(), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self._read)
        self._read()  # handle anything that arrived during the handshake

    @classmethod
    def connect(cls, commands: MediaCommands) -> MprisAdapter | None:
        """Connect to the session bus, or return ``None`` if there isn't one."""
        try:
            connection = open_dbus_connection("SESSION")
        except (KeyError, OSError, StopIteration) as exc:
            log.info("No D-Bus session bus, MPRIS disabled: %s", exc or type(exc).__name__)
            return None
        try:
            bus_name = cls._request_name(connection)
        except Exception:
            connection.close()
            raise
        log.info("MPRIS: registered as %s", bus_name)
        return cls(commands, connection, bus_name)

    @staticmethod
    def _request_name(connection: DBusConnection) -> str:
        # A second PyAmp instance registers under a unique suffix, as the spec suggests.
        for name in (BUS_NAME, f"{BUS_NAME}.instance{os.getpid()}"):
            reply = connection.send_and_get_reply(
                message_bus.RequestName(name, _DO_NOT_QUEUE), timeout=2
            )
            if reply.body[0] == _PRIMARY_OWNER:
                return name
        raise RuntimeError("could not acquire an MPRIS bus name")

    def update(self, now_playing: NowPlaying) -> None:
        self._service.update(now_playing)

    def seeked(self, position_ms: int) -> None:
        self._service.seeked(position_ms)

    def close(self) -> None:
        self._notifier.setEnabled(False)
        self._notifier.activated.disconnect(self._read)
        with contextlib.suppress(OSError):
            self._connection.send(message_bus.ReleaseName(self.bus_name))
        self._connection.close()

    def _read(self) -> None:
        parser = self._connection.parser
        if self._notifier.isEnabled() and _readable(self._connection.sock.fileno()):
            data = self._connection.sock.recv(65536)
            if not data:
                log.warning("MPRIS: D-Bus connection closed")
                self._notifier.setEnabled(False)
                return
            parser.add_data(data)
        while (msg := parser.get_next_message()) is not None:
            self._service.handle(msg)


def _readable(fd: int) -> bool:
    ready, _, _ = select.select([fd], [], [], 0)
    return bool(ready)


_INTROSPECTION_XML = f"""\
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="{INTROSPECTABLE_IFACE}">
    <method name="Introspect"><arg name="xml_data" type="s" direction="out"/></method>
  </interface>
  <interface name="{PROPERTIES_IFACE}">
    <method name="Get">
      <arg name="interface_name" type="s" direction="in"/>
      <arg name="property_name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="GetAll">
      <arg name="interface_name" type="s" direction="in"/>
      <arg name="properties" type="a{{sv}}" direction="out"/>
    </method>
    <method name="Set">
      <arg name="interface_name" type="s" direction="in"/>
      <arg name="property_name" type="s" direction="in"/>
      <arg name="value" type="v" direction="in"/>
    </method>
    <signal name="PropertiesChanged">
      <arg name="interface_name" type="s"/>
      <arg name="changed_properties" type="a{{sv}}"/>
      <arg name="invalidated_properties" type="as"/>
    </signal>
  </interface>
  <interface name="{ROOT_IFACE}">
    <method name="Raise"/>
    <method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="{PLAYER_IFACE}">
    <method name="Next"/>
    <method name="Previous"/>
    <method name="Pause"/>
    <method name="PlayPause"/>
    <method name="Stop"/>
    <method name="Play"/>
    <method name="Seek"><arg name="Offset" type="x" direction="in"/></method>
    <method name="SetPosition">
      <arg name="TrackId" type="o" direction="in"/>
      <arg name="Position" type="x" direction="in"/>
    </method>
    <method name="OpenUri"><arg name="Uri" type="s" direction="in"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{{sv}}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>
"""
