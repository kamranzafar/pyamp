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

"""Audio backend built on libmpv (Adapter pattern), with gapless playback.

Needs libmpv on the system (``brew install mpv`` / ``apt install libmpv2``).
Importing this module without it raises ``OSError``, which the registry
reports as an unavailable backend.

mpv calls our observers on its own event thread. They only record state and
emit signals on :class:`_Bridge`, a ``QObject`` living on the Qt main thread,
so the listener is always called on the main thread (see "Threading model" in
the README). mpv exposes no decoded audio, so the visualiser stays flat.
"""

from __future__ import annotations

import locale
import threading
from pathlib import Path
from typing import Any, ClassVar

from pyamp.audio.libmpv_path import prepare_libmpv_lookup

prepare_libmpv_lookup()  # must run before python-mpv looks for libmpv

import mpv  # noqa: E402  (needs prepare_libmpv_lookup first)
from PySide6.QtCore import QObject, Signal, Slot  # noqa: E402

from pyamp.audio.backend import AudioBackend  # noqa: E402
from pyamp.core.models import PlaybackState  # noqa: E402

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
