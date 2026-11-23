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

"""macOS integration: media keys, Control Center and lock screen "Now Playing".

Uses the MediaPlayer framework through pyobjc. ``MPRemoteCommandCenter`` calls
our handlers on the main queue, which is the Qt main thread, so they may drive
the player directly (see "Threading model" in the README).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import MediaPlayer as mp  # noqa: N813  (pyobjc framework module)

from pyamp.core.models import PlaybackState
from pyamp.media_session.session import MediaCommands, NowPlaying

log = logging.getLogger(__name__)

type CommandHandler = Callable[[Any], int]

_PLAYBACK_STATES = {
    PlaybackState.PLAYING: mp.MPNowPlayingPlaybackStatePlaying,
    PlaybackState.PAUSED: mp.MPNowPlayingPlaybackStatePaused,
    PlaybackState.STOPPED: mp.MPNowPlayingPlaybackStateStopped,
}


class MacNowPlayingAdapter:
    def __init__(self, commands: MediaCommands) -> None:
        self._info_center: Any = mp.MPNowPlayingInfoCenter.defaultCenter()
        center: Any = mp.MPRemoteCommandCenter.sharedCommandCenter()
        self._next_command: Any = center.nextTrackCommand()
        self._previous_command: Any = center.previousTrackCommand()
        self._position_command: Any = center.changePlaybackPositionCommand()

        #: Registered handlers by command name, so tests can invoke them.
        self.handlers: dict[str, CommandHandler] = {}
        self._targets: list[tuple[Any, Any]] = []

        def seek(event: Any) -> None:
            commands.seek_to(int(event.positionTime() * 1000))

        self._bind("play", center.playCommand(), lambda _: commands.play())
        self._bind("pause", center.pauseCommand(), lambda _: commands.pause())
        self._bind("toggle", center.togglePlayPauseCommand(), lambda _: commands.play_pause())
        self._bind("stop", center.stopCommand(), lambda _: commands.stop())
        self._bind("next", self._next_command, lambda _: commands.next())
        self._bind("previous", self._previous_command, lambda _: commands.previous())
        self._bind("seek", self._position_command, seek)

    def update(self, now_playing: NowPlaying) -> None:
        self._next_command.setEnabled_(now_playing.can_go_next)
        self._previous_command.setEnabled_(now_playing.can_go_previous)
        self._position_command.setEnabled_(now_playing.can_seek)

        track = now_playing.track
        if track is None:
            self._info_center.setNowPlayingInfo_(None)
        else:
            info: dict[str, Any] = {
                mp.MPMediaItemPropertyTitle: track.title,
                mp.MPMediaItemPropertyPlaybackDuration: now_playing.duration_ms / 1000,
                mp.MPNowPlayingInfoPropertyElapsedPlaybackTime: now_playing.position_ms / 1000,
                mp.MPNowPlayingInfoPropertyPlaybackRate: 1.0 if now_playing.playing else 0.0,
                mp.MPNowPlayingInfoPropertyMediaType: mp.MPNowPlayingInfoMediaTypeAudio,
            }
            # Leave out empty tags so the widget doesn't show a blank artist line.
            if track.artist:
                info[mp.MPMediaItemPropertyArtist] = track.artist
            if track.album:
                info[mp.MPMediaItemPropertyAlbumTitle] = track.album
            self._info_center.setNowPlayingInfo_(info)
        self._info_center.setPlaybackState_(_PLAYBACK_STATES[now_playing.state])

    def seeked(self, position_ms: int) -> None:
        """Nothing to do: the preceding update already carried the new position."""

    def close(self) -> None:
        for command, token in self._targets:
            command.removeTarget_(token)
        self._targets.clear()
        self.handlers.clear()
        self._info_center.setNowPlayingInfo_(None)
        self._info_center.setPlaybackState_(mp.MPNowPlayingPlaybackStateStopped)

    def _bind(self, name: str, command: Any, action: Callable[[Any], None]) -> None:
        def handler(event: Any) -> int:
            try:
                action(event)
            except Exception:
                log.exception("Media command %r failed", name)
                return int(mp.MPRemoteCommandHandlerStatusCommandFailed)
            return int(mp.MPRemoteCommandHandlerStatusSuccess)

        command.setEnabled_(True)
        self._targets.append((command, command.addTargetWithHandler_(handler)))
        self.handlers[name] = handler
