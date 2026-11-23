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

"""Media keys and system "Now Playing" integration, as a plugin."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pyamp.media_session import MediaCommands, MediaSession, MediaSessionAdapter, create_adapter
from pyamp.plugins.base import PluginContext

log = logging.getLogger(__name__)

type AdapterFactory = Callable[[MediaCommands], MediaSessionAdapter | None]


class MediaSessionPlugin:
    name = "media-session"

    def __init__(self, adapter_factory: AdapterFactory = create_adapter) -> None:
        self._adapter_factory = adapter_factory
        self._session: MediaSession | None = None

    @property
    def active(self) -> bool:
        """Whether an OS integration is running (False on unsupported platforms)."""
        return self._session is not None

    def activate(self, context: PluginContext) -> None:
        adapter = self._adapter_factory(MediaCommands(context.player))
        if adapter is None:
            return
        self._session = MediaSession(context.bus, context.player, context.playlist, adapter)
        log.info("Media session integration active (%s)", type(adapter).__name__)

    def deactivate(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None
