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

"""Integration with the operating system's media controls.

Media keys, headphone buttons and system "Now Playing" widgets (macOS Control
Center, MPRIS clients on Linux) show the current track and control playback.
Platform modules are imported lazily, so their dependencies only matter on the
platform that uses them.
"""

from __future__ import annotations

import logging
import sys

from pyamp.media_session.session import (
    MediaCommands,
    MediaSession,
    MediaSessionAdapter,
    NowPlaying,
)

log = logging.getLogger(__name__)

__all__ = [
    "MediaCommands",
    "MediaSession",
    "MediaSessionAdapter",
    "NowPlaying",
    "create_adapter",
]


def create_adapter(
    commands: MediaCommands, platform: str = sys.platform
) -> MediaSessionAdapter | None:
    """The adapter for this platform, or ``None`` if it has no supported integration."""
    if platform == "darwin":
        from pyamp.media_session.macos import MacNowPlayingAdapter  # noqa: PLC0415

        return MacNowPlayingAdapter(commands)
    if platform.startswith("linux"):
        from pyamp.media_session.mpris import MprisAdapter  # noqa: PLC0415

        return MprisAdapter.connect(commands)
    log.info("No media session integration for platform %r", platform)
    return None
