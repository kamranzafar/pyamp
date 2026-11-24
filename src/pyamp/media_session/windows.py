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
"""Windows integration: media keys, headset buttons, the volume flyout and lock screen.

Uses the System Media Transport Controls (SMTC) through pywinrt. A desktop app gets
them for its main window (``ISystemMediaTransportControlsInterop.GetForWindow``).
Windows raises button presses on a background thread; a queued Qt signal carries them
to the main thread, where the player lives (see "Threading model" in the README).

The adapter talks to the SMTC through :class:`WinRtApi`, so its behaviour is testable
on any platform; only :meth:`WindowsMediaAdapter.connect` imports winrt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from PySide6.QtCore import QObject, Signal

from pyamp.core.models import PlaybackState
from pyamp.media_session.session import MediaCommands, NowPlaying

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WinRtApi:
    """The winrt types the adapter uses (real ones, or fakes in tests)."""

    status: Any  # MediaPlaybackStatus
    playback_type: Any  # MediaPlaybackType
    button: Any  # SystemMediaTransportControlsButton
    timeline: Callable[[], Any]  # SystemMediaTransportControlsTimelineProperties


class _MainThread(QObject):
    """Runs callables on the thread this object lives on (the Qt main thread)."""

    call = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.call.connect(self._run)

    @staticmethod
    def _run(function: Callable[[], None]) -> None:
        try:
            function()
        except Exception:
            log.exception("Media command failed")


class WindowsMediaAdapter:
    def __init__(self, commands: MediaCommands, controls: Any, api: WinRtApi) -> None:
        self._controls = controls
        self._api = api
        self._main = _MainThread()
        button = api.button
        #: What each button does, by button (also used by tests).
        self.actions: dict[Any, Callable[[], None]] = {
            button.PLAY: commands.play,
            button.PAUSE: commands.pause,
            button.STOP: commands.stop,
            button.NEXT: commands.next,
            button.PREVIOUS: commands.previous,
        }
        self._statuses = {
            PlaybackState.PLAYING: api.status.PLAYING,
            PlaybackState.PAUSED: api.status.PAUSED,
            PlaybackState.STOPPED: api.status.STOPPED,
        }

        def seek(position: timedelta) -> None:
            commands.seek_to(int(position.total_seconds() * 1000))

        self._seek = seek
        controls.is_enabled = True
        controls.is_play_enabled = True
        controls.is_pause_enabled = True
        controls.is_stop_enabled = True
        self._tokens = [
            ("button_pressed", controls.add_button_pressed(self._on_button)),
            (
                "playback_position_change_requested",
                controls.add_playback_position_change_requested(self._on_seek),
            ),
        ]

    @classmethod
    def connect(cls, commands: MediaCommands, window: int | None) -> WindowsMediaAdapter | None:
        """The adapter for this app's main window, or None if Windows won't provide one."""
        if not window:
            log.info("No main window yet; Windows media controls not connected")
            return None
        try:
            from winrt.windows.media import (  # noqa: PLC0415  (Windows only)
                MediaPlaybackStatus,
                MediaPlaybackType,
                SystemMediaTransportControlsButton,
                SystemMediaTransportControlsTimelineProperties,
            )
            from winrt.windows.media.interop import get_for_window  # noqa: PLC0415

            controls = get_for_window(window)
        except Exception:
            log.warning("Windows media controls are unavailable", exc_info=True)
            return None
        api = WinRtApi(
            status=MediaPlaybackStatus,
            playback_type=MediaPlaybackType,
            button=SystemMediaTransportControlsButton,
            timeline=SystemMediaTransportControlsTimelineProperties,
        )
        return cls(commands, controls, api)

    # -- MediaSessionAdapter -------------------------------------------------- #

    def update(self, now_playing: NowPlaying) -> None:
        controls = self._controls
        controls.is_next_enabled = now_playing.can_go_next
        controls.is_previous_enabled = now_playing.can_go_previous
        controls.playback_status = self._statuses[now_playing.state]

        display = controls.display_updater
        track = now_playing.track
        if track is None:
            display.clear_all()
            display.update()
            return
        display.type = self._api.playback_type.MUSIC
        music = display.music_properties
        music.title = track.title
        music.artist = track.artist
        music.album_title = track.album
        display.update()

        timeline = self._api.timeline()
        timeline.start_time = timedelta(0)
        timeline.min_seek_time = timedelta(0)
        timeline.end_time = timedelta(milliseconds=now_playing.duration_ms)
        timeline.max_seek_time = timedelta(milliseconds=now_playing.duration_ms)
        timeline.position = timedelta(milliseconds=now_playing.position_ms)
        controls.update_timeline_properties(timeline)

    def seeked(self, position_ms: int) -> None:
        """Nothing to do: the preceding update already carried the new position."""

    def close(self) -> None:
        for event, token in self._tokens:
            getattr(self._controls, f"remove_{event}")(token)
        self._tokens.clear()
        self._controls.display_updater.clear_all()
        self._controls.display_updater.update()
        self._controls.playback_status = self._api.status.CLOSED
        self._controls.is_enabled = False

    # -- Windows events (background thread) --------------------------------------- #

    def _on_button(self, _sender: Any, args: Any) -> None:
        action = self.actions.get(args.button)
        if action is not None:
            self._main.call.emit(action)

    def _on_seek(self, _sender: Any, args: Any) -> None:
        position = args.requested_playback_position
        self._main.call.emit(lambda: self._seek(position))
