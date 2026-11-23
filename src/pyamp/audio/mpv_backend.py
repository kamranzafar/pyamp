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
import threading
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
from pyamp.core.equalizer import BANDS_HZ, Equalizer
from pyamp.core.models import PlaybackState, ReplayGainMode

log = logging.getLogger(__name__)

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


class _Bridge(QObject):
    """Created on the main thread; signals emitted on mpv's thread are queued to it."""

    state = Signal(object)  # PlaybackState
    position = Signal(int)
    duration = Signal(int)
    advanced = Signal(str)
    end_of_track = Signal()
    error = Signal(str)

    def __init__(self, backend: MpvAudioBackend) -> None:
        super().__init__()
        self._backend = backend
        self.state.connect(self._on_state)
        self.position.connect(self._on_position)
        self.duration.connect(self._on_duration)
        self.advanced.connect(self._on_advanced)
        self.end_of_track.connect(self._on_end_of_track)
        self.error.connect(self._on_error)

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
        self._backend.listener.on_track_advanced(Path(path))

    @Slot()
    def _on_end_of_track(self) -> None:
        self._backend.listener.on_end_of_track()

    @Slot(str)
    def _on_error(self, message: str) -> None:
        self._backend.listener.on_error(message)


class MpvAudioBackend(AudioBackend):
    name: ClassVar[str] = "mpv"
    supports_gapless: ClassVar[bool] = True
    # mpv reads the tags itself and applies them per file while decoding, so the gain
    # changes exactly at gapless track boundaries.
    applies_replay_gain: ClassVar[bool] = True
    supports_equalizer: ClassVar[bool] = True

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

        # libmpv refuses to start unless LC_NUMERIC is "C" (mpv_create returns NULL and
        # python-mpv then crashes). python-mpv sets it on import, but creating the Qt
        # application resets it to the user's locale, so set it again here.
        locale.setlocale(locale.LC_NUMERIC, "C")
        self._mpv: Any = mpv.MPV(**{**_DEFAULT_OPTIONS, **options})
        self._mpv.observe_property("pause", self._observe_pause)
        self._mpv.observe_property("idle-active", self._observe_idle)
        self._mpv.observe_property("time-pos", self._observe_position)
        self._mpv.observe_property("duration", self._observe_duration)
        self._mpv.register_event_callback(self._on_event)

    @property
    def listener(self) -> Any:
        return self._listener

    # -- AudioBackend ------------------------------------------------------- #

    def load(self, path: Path) -> None:
        with self._lock:
            self._pending_loads += 1
        self._mpv.playlist_clear()
        self._mpv.loadfile(str(path), "replace")

    def queue_next(self, path: Path | None) -> None:
        self._mpv.playlist_clear()  # drops everything except the current entry
        if path is not None:
            self._mpv.loadfile(str(path), "append")

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

    def close(self) -> None:
        self._mpv.terminate()

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
