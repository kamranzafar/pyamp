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

"""Audio backend built on libmpv (Adapter pattern), with gapless playback.

Needs libmpv: from the system (``brew install mpv`` / ``apt install libmpv2``), bundled
with the app, or in a folder (see :mod:`pyamp.audio.libmpv_loader`).
Importing this module without it raises ``OSError``, which the registry
reports as an unavailable backend.

mpv calls our observers on its own event thread. They only record state and
emit signals on :class:`_Bridge`, a ``QObject`` living on the Qt main thread,
so the listener is always called on the main thread (see "Threading model" in
the README). mpv exposes no decoded audio, so the visualiser stays flat.
"""

from __future__ import annotations

import functools
import locale
import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QObject, Signal, Slot

from pyamp.audio.backend import AudioBackend
from pyamp.audio.libmpv_loader import (
    LibmpvSource,
    find_avfilter,
    import_mpv,
    missing_filters,
)
from pyamp.audio.qt_audio_env import pipewire_is_incomplete
from pyamp.core.equalizer import BANDS_HZ, Equalizer
from pyamp.core.models import Location, OutputDevice, PlaybackState, ReplayGainMode, parse_location

log = logging.getLogger(__name__)

#: mpv's event for a file's end (played, failed or stopped).
_END_FILE = 7
#: How often a decode for the visualiser checks whether to stop.
_CANCEL_CHECK_S = 0.1

#: How much of a stream mpv reads ahead (and half that it keeps behind).
STREAM_BUFFER_BYTES = 32 * 1024 * 1024

#: The certificate authorities macOS ships, kept current by its updates. FFmpeg checks
#: https certificates, and with Mbed TLS (PyAmp's macOS libmpv) it needs them as a file;
#: Windows' Schannel and Linux distributions' builds use the system's own store.
MACOS_CA_FILE = Path("/etc/ssl/cert.pem")


def tls_options(platform: str = sys.platform, ca_file: Path = MACOS_CA_FILE) -> dict[str, str]:
    """mpv options so https streams can be verified on ``platform`` ({} if none needed)."""
    if platform == "darwin" and ca_file.is_file():
        return {"tls_ca_file": str(ca_file)}
    return {}


#: python-mpv, bound to the best libmpv available (see libmpv_loader).
mpv: Any
#: Which libmpv that is, e.g. for ``pyamp --version``.
LIBMPV_SOURCE: LibmpvSource
mpv, LIBMPV_SOURCE = import_mpv()
log.info("Using libmpv: %s", LIBMPV_SOURCE)

#: Position updates closer together than this are dropped (mpv reports very often).
POSITION_INTERVAL_S = 0.1

_END_EOF = mpv.MpvEventEndFile.EOF
_END_ERROR = mpv.MpvEventEndFile.ERROR

_DEFAULT_OPTIONS: dict[str, Any] = {
    "video": False,
    "audio_display": False,
    "gapless_audio": "weak",
    "prefetch_playlist": True,
    "idle": True,
    "keep_open": False,
    "config": False,
    # How much of a stream mpv holds in memory: minutes of audio ahead for a bad network,
    # without keeping a whole hi-res album side in RAM (mpv's defaults: 150 + 50 MiB).
    # A song too big for this is visualised a stretch at a time.
    "demuxer_max_bytes": STREAM_BUFFER_BYTES,
    "demuxer_max_back_bytes": STREAM_BUFFER_BYTES // 2,
    "input_default_bindings": False,
    "terminal": False,
    # ReplayGain is off until the player sets a mode; never let it clip, and leave
    # untagged files at their natural level.
    "replaygain": "no",
    "replaygain_clip": False,
    "replaygain_fallback": 0,
}

#: Label of our filter in mpv's audio filter chain.
EQ_LABEL = "pyamp-eq"
#: Band filter width (Q): about one octave, so neighbouring bands blend smoothly.
EQ_Q = 1.41


def equalizer_graph(equalizer: Equalizer) -> str:
    """The lavfi graph for ``equalizer``: a preamp, then one peaking filter per band.

    Filter instances are named (``volume@pre``, ``equalizer@b0``..``equalizer@b9``) to
    make the graph readable in mpv's logs.
    """
    bands = ",".join(
        f"equalizer@b{i}=f={hz}:t=q:w={EQ_Q}:g={gain}"
        for i, (hz, gain) in enumerate(zip(BANDS_HZ, equalizer.gains_db, strict=True))
    )
    return f"volume@pre=volume={equalizer.preamp_db}dB,{bands}"


#: ffmpeg filters the equaliser graph uses.
EQ_FILTERS = ("volume", "equalizer")


@functools.cache
def missing_eq_filters() -> tuple[str, ...]:
    """The equaliser filters this libmpv's ffmpeg lacks (checked once)."""
    return tuple(missing_filters(EQ_FILTERS, find_avfilter(LIBMPV_SOURCE)))


_REPLAYGAIN = {
    ReplayGainMode.OFF: "no",
    ReplayGainMode.TRACK: "track",
    ReplayGainMode.ALBUM: "album",
}


#: mpv outputs that don't play through a device: nothing to list or choose.
DEVICELESS_OUTPUTS = ("null", "pcm")


def lists_devices(
    ao: str | None = None,
    platform: str = sys.platform,
    env: Mapping[str, str] = os.environ,
    incomplete: Callable[[Mapping[str, str]], bool] = pipewire_is_incomplete,
) -> bool:
    """Whether to ask mpv for the output devices, for its ``ao`` option.

    Listing them starts mpv's device watchers (PulseAudio, PipeWire, ...) on its core
    thread. Outputs without devices (tests' "null") don't need them, and on CI's
    virtual audio setup starting them has now and then hung mpv for good. With an
    incomplete PipeWire (libpipewire without its client.conf) PyAmp also sticks to the
    system default.
    """
    if ao in DEVICELESS_OUTPUTS:
        return False
    return not (platform.startswith("linux") and incomplete(env))


#: mpv's name for "the system's default output, following it when it changes".
AUTO_DEVICE = "auto"


def device_id_of(mpv_name: str) -> str:
    """The device's own id, without mpv's audio-output prefix: ``coreaudio/<uid>`` gives
    CoreAudio's UID, as Qt names it too, so a chosen device survives a change of backend."""
    return mpv_name.split("/", 1)[1] if "/" in mpv_name else mpv_name


class _Bridge(QObject):
    """Created on the main thread; signals emitted on mpv's thread are queued to it."""

    state = Signal(object)  # PlaybackState
    position = Signal(int)
    duration = Signal(int)
    advanced = Signal(str)
    stream_title = Signal(str)
    station_name = Signal(str)
    audio_changed = Signal()  # bitrate or sample rate may have changed
    end_of_track = Signal()
    error = Signal(str)
    devices_changed = Signal()

    def __init__(self, backend: MpvAudioBackend) -> None:
        super().__init__()
        self._backend = backend
        self.state.connect(self._on_state)
        self.position.connect(self._on_position)
        self.duration.connect(self._on_duration)
        self.advanced.connect(self._on_advanced)
        self.stream_title.connect(self._on_stream_title)
        self.station_name.connect(self._on_station_name)
        self.audio_changed.connect(self._on_audio_changed)
        self.end_of_track.connect(self._on_end_of_track)
        self.error.connect(self._on_error)
        self.devices_changed.connect(self._on_devices_changed)

    # Look the listener up on every call: it may be replaced after construction.
    @Slot(object)
    def _on_state(self, state: PlaybackState) -> None:
        self._backend.listener.on_state_changed(state)

    @Slot(int)
    def _on_position(self, position_ms: int) -> None:
        self._backend.listener.on_position_changed(position_ms)

    @Slot(int)
    def _on_duration(self, duration_ms: int) -> None:
        self._backend.listener.on_duration_changed(duration_ms)

    @Slot(str)
    def _on_advanced(self, path: str) -> None:
        location = parse_location(path)
        if location is not None:
            self._backend.listener.on_track_advanced(location)

    @Slot(str)
    def _on_stream_title(self, title: str) -> None:
        self._backend.listener.on_stream_title(title)

    @Slot(str)
    def _on_station_name(self, name: str) -> None:
        self._backend.listener.on_station_name(name)

    @Slot()
    def _on_audio_changed(self) -> None:
        self._backend.report_audio_info()

    @Slot()
    def _on_end_of_track(self) -> None:
        self._backend.listener.on_end_of_track()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._backend.listener.on_error(message)

    @Slot()
    def _on_devices_changed(self) -> None:
        self._backend.listener.on_output_devices_changed()


class MpvAudioBackend(AudioBackend):
    name: ClassVar[str] = "mpv"
    supports_gapless: ClassVar[bool] = True
    # mpv reads the tags itself and applies them per file while decoding, so the gain
    # changes exactly at gapless track boundaries.
    applies_replay_gain: ClassVar[bool] = True
    supports_equalizer: ClassVar[bool] = True
    supports_output_devices: ClassVar[bool] = True

    def __init__(self, **options: Any) -> None:
        """``options`` are extra mpv options, e.g. ``ao="null"`` in tests."""
        super().__init__()
        self._bridge = _Bridge(self)
        self._lock = threading.Lock()
        self._pending_loads = 0  # files started via load() whose start-file hasn't arrived
        self._advancing = False  # the file being started came from the queue
        self._ended = False  # the last file reached its end (not stopped or failed)
        self._paused = False
        self._idle = True
        self._last_position = -1.0
        self._equalizer: Equalizer | None = None  # what's currently in mpv's filter chain
        self._closed = False

        # libmpv refuses to start unless LC_NUMERIC is "C" (mpv_create returns NULL and
        # python-mpv then crashes). python-mpv sets it on import, but creating the Qt
        # application resets it to the user's locale, so set it again here.
        locale.setlocale(locale.LC_NUMERIC, "C")
        self._mpv: Any = mpv.MPV(**{**_DEFAULT_OPTIONS, **tls_options(), **options})
        self._mpv.observe_property("pause", self._observe_pause)
        self._mpv.observe_property("idle-active", self._observe_idle)
        self._mpv.observe_property("time-pos", self._observe_position)
        self._mpv.observe_property("duration", self._observe_duration)
        self._mpv.observe_property("metadata", self._observe_metadata)
        self._mpv.observe_property("audio-bitrate", self._observe_audio)
        self._mpv.observe_property("audio-params", self._observe_audio)
        self._lists_devices = lists_devices(options.get("ao"))
        if self._lists_devices:
            self._mpv.observe_property("audio-device-list", self._observe_devices)
        elif options.get("ao") not in DEVICELESS_OUTPUTS:
            log.warning(
                "PipeWire's client library is installed without its configuration "
                "(client.conf); playing through the system default output only"
            )
        self._mpv.register_event_callback(self._on_event)
        # mpv 0.31+ can write what it has buffered to a file (for the visualiser).
        self._can_dump_cache = any(c.get("name") == "dump-cache" for c in self._mpv.command_list)

    @property
    def listener(self) -> Any:
        return self._listener

    # -- AudioBackend ------------------------------------------------------- #

    def load(self, location: Location) -> None:
        with self._lock:
            self._pending_loads += 1
        self._mpv.playlist_clear()
        self._mpv.loadfile(str(location), "replace")

    def queue_next(self, location: Location | None) -> None:
        self._mpv.playlist_clear()  # drops everything except the current entry
        if location is not None:
            self._mpv.loadfile(str(location), "append")

    def play(self) -> None:
        self._mpv.pause = False

    def pause(self) -> None:
        self._mpv.pause = True

    def stop(self) -> None:
        self._mpv.command("stop")

    def seek(self, position_ms: int) -> None:
        if not self._idle:
            self._mpv.seek(position_ms / 1000, "absolute", "exact")

    def set_volume(self, volume: float) -> None:
        # mpv's software volume is already cubic, i.e. perceptual like our 0..1 scale.
        self._mpv.volume = max(0.0, min(volume, 1.0)) * 100

    def set_replay_gain_mode(self, mode: ReplayGainMode) -> None:
        self._mpv.replaygain = _REPLAYGAIN[mode]

    def set_equalizer(self, equalizer: Equalizer) -> None:
        if equalizer == self._equalizer:
            return
        if self._equalizer is not None:
            self._mpv.command("af", "remove", f"@{EQ_LABEL}")
            self._equalizer = None
        if equalizer.active and (missing := missing_eq_filters()):
            # mpv would accept the graph now and only fail once playback starts.
            message = (
                "The equaliser isn't available with this libmpv "
                f"({LIBMPV_SOURCE}): its ffmpeg lacks the {', '.join(missing)} filter. "
                "Try '--libmpv bundled'."
            )
            log.error(message)
            self._listener.on_error(message)
            return
        if equalizer.active:
            # The chain is rebuilt on every change. Retuning it live with af-command
            # crashes libmpv (seen with mpv 0.37 and 0.42), so the window only sends
            # changes once a slider stops moving.
            self._mpv.command("af", "add", f"@{EQ_LABEL}:lavfi=[{equalizer_graph(equalizer)}]")
            self._equalizer = equalizer

    def buffered_range(self) -> tuple[float, float] | None:
        """The cached stretch around the playback position. After a seek mpv may hold
        several; the one being played is wanted, else the last (where it's reading)."""
        if self._closed or not self._can_dump_cache:
            return None
        try:
            ranges = (self._mpv.demuxer_cache_state or {}).get("seekable-ranges") or []
            position = self._mpv.time_pos
        except (AttributeError, RuntimeError):  # nothing playing
            return None
        stretches = [(float(r["start"]), float(r["end"])) for r in ranges]
        playing = [s for s in stretches if position is not None and s[0] <= position <= s[1]]
        candidates = playing or stretches
        return candidates[-1] if candidates else None

    def save_stream(self, target: Path, start: float, end: float) -> bool:
        """Write mpv's cache of the playing file to ``target`` (Matroska, by its suffix)."""
        if self._closed or not self._can_dump_cache:
            return False
        try:
            # Always an explicit end: with "no" (to the end) mpv keeps the command running
            # until playback reaches the end of the file, however much is already cached.
            self._mpv.command("dump-cache", f"{start:.3f}", f"{end:.3f}", str(target))
        except Exception as exc:  # e.g. the file changed or mpv closed meanwhile
            log.debug("Could not save the stream for the visualiser: %s", exc)
            return False
        return target.is_file() and target.stat().st_size > 0

    def output_devices(self) -> list[OutputDevice]:
        return list(self._devices().values())

    def set_output_device(self, device_id: str | None) -> None:
        if self._closed:
            return
        target = AUTO_DEVICE
        if device_id is not None:
            target = next(
                (name for name, d in self._devices().items() if d.id == device_id), AUTO_DEVICE
            )
        if self._mpv.audio_device != target:  # setting it at all restarts the output
            self._mpv.audio_device = target

    def _devices(self) -> dict[str, OutputDevice]:
        """mpv's device names (``coreaudio/<uid>``) -> the devices, without "auto"."""
        if self._closed or not self._lists_devices:
            return {}
        try:
            entries = self._mpv.audio_device_list or []
        except (AttributeError, RuntimeError):
            return {}
        devices: dict[str, OutputDevice] = {}
        for entry in entries:
            name = entry.get("name") or ""
            if name and name != AUTO_DEVICE:
                device_id = device_id_of(name)
                if all(d.id != device_id for d in devices.values()):
                    devices[name] = OutputDevice(device_id, entry.get("description") or name)
        return devices

    def close(self) -> None:
        self._closed = True
        # Events mpv reported just before closing may still be queued for the main thread;
        # delivered now they'd make the player call into an mpv that's gone (a crash).
        self.detach_listener()
        self._mpv.terminate()

    def report_audio_info(self) -> None:
        """Tell the listener the playing audio's bitrate and sample rate (main thread).

        Read from mpv rather than taken from its change notifications: on loading a file
        with the same format, mpv reports the sample rate as gone and never back.
        """
        if self._closed:
            return
        try:
            bitrate = self._mpv.audio_bitrate
            params = self._mpv.audio_params
        except (AttributeError, RuntimeError):  # mpv has nothing playing
            bitrate, params = None, None
        sample_rate = params.get("samplerate") if isinstance(params, dict) else None
        self._listener.on_audio_info(int(bitrate or 0), int(sample_rate or 0))

    # -- mpv event thread --------------------------------------------------- #

    def _observe_pause(self, _name: str, paused: bool | None) -> None:
        self._paused = bool(paused)
        self._emit_state()

    def _observe_idle(self, _name: str, idle: bool | None) -> None:
        self._idle = bool(idle)
        self._emit_state()
        if self._idle and self._ended:
            self._ended = False
            self._bridge.end_of_track.emit()

    def _observe_position(self, _name: str, seconds: float | None) -> None:
        if seconds is None:
            return
        if abs(seconds - self._last_position) >= POSITION_INTERVAL_S:
            self._last_position = seconds
            self._bridge.position.emit(int(seconds * 1000))

    def _observe_duration(self, _name: str, seconds: float | None) -> None:
        self._bridge.duration.emit(int((seconds or 0) * 1000))

    def _observe_metadata(self, _name: str, metadata: dict[str, str] | None) -> None:
        # Internet radio (Icecast/Shoutcast) sends its name and the current song as ICY
        # metadata. The name goes first, so the song is shown with it.
        icy = {key.lower(): value for key, value in (metadata or {}).items()}
        if name := icy.get("icy-name", "").strip():
            self._bridge.station_name.emit(name)
        if title := icy.get("icy-title", "").strip():
            self._bridge.stream_title.emit(title)

    def _observe_devices(self, _name: str, _value: object) -> None:
        self._bridge.devices_changed.emit()  # read on the main thread, like the rest

    def _observe_audio(self, _name: str, _value: object) -> None:
        # The bitrate varies as VBR files and streams play (mpv updates it about once a
        # second). The values are read on the main thread: reading mpv properties from
        # its own event thread can hang mpv while it shuts down.
        self._bridge.audio_changed.emit()

    def _on_event(self, event: Any) -> None:
        event_id = event.event_id.value
        if event_id == mpv.MpvEventID.START_FILE:
            with self._lock:
                self._advancing = self._pending_loads == 0
                self._pending_loads = max(0, self._pending_loads - 1)
            self._ended = False
            self._last_position = -1.0
        elif event_id == mpv.MpvEventID.FILE_LOADED:
            if self._advancing:
                self._advancing = False
                self._bridge.advanced.emit(self._mpv.path)
        elif event_id == mpv.MpvEventID.END_FILE:
            reason = event.data.reason
            if reason == _END_EOF:
                self._ended = True
            elif reason == _END_ERROR:
                self._bridge.error.emit(f"mpv could not play the file (error {event.data.error})")

    def _emit_state(self) -> None:
        if self._idle:
            state = PlaybackState.STOPPED
        elif self._paused:
            state = PlaybackState.PAUSED
        else:
            state = PlaybackState.PLAYING
        self._bridge.state.emit(state)


class MpvPcmDecoder:
    """Decodes a file to raw samples with a hidden libmpv, for the visualiser's analysis.

    It shares nothing with the libmpv that plays: it writes to a file instead of a sound
    card, as fast as it can (``untimed``). Several times quicker than Qt's decoder. Safe
    to call from a worker thread; each call has its own instance.
    """

    #: Longest a decode may take (a corrupt or endless file stops there).
    TIMEOUT_S = 120.0

    def decode(
        self,
        source: Path,
        target: Path,
        sample_rate: int,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> bool:
        """Write ``source`` to ``target`` as mono float32 at ``sample_rate``, as it goes
        (the samples can be read while it's still writing); False if it can't be decoded
        or ``cancelled`` says to stop, which is checked several times a second."""
        decoder: Any = None
        ended = threading.Event()
        try:
            decoder = mpv.MPV(
                video=False,
                audio_display=False,
                config=False,
                terminal=False,
                input_default_bindings=False,
                ao="pcm",
                ao_pcm_file=str(target),
                ao_pcm_waveheader=False,
                audio_format="float",
                audio_channels="mono",
                audio_samplerate=sample_rate,
                untimed=True,
                replaygain="no",
            )
            decoder.register_event_callback(
                lambda event: ended.set() if event.event_id.value == _END_FILE else None
            )
            decoder.play(str(source))
            deadline = time.monotonic() + self.TIMEOUT_S
            while not ended.wait(_CANCEL_CHECK_S):
                if cancelled() or time.monotonic() > deadline:
                    return False
        except Exception as exc:  # unreadable, unplayable, or it took too long
            log.debug("Could not decode %s for the visualiser: %s", source.name, exc)
            return False
        finally:
            if decoder is not None:
                decoder.terminate()
        return target.is_file() and target.stat().st_size > 0
