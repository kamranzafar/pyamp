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

"""Example plugin: logs every track change. Use it as a template for your own plugins."""

from __future__ import annotations

import logging

from pyamp.core.events import TrackChanged, Unsubscribe
from pyamp.plugins.base import PluginContext

log = logging.getLogger(__name__)


class NowPlayingLogPlugin:
    name = "now-playing-log"

    def __init__(self) -> None:
        self._unsubscribe: list[Unsubscribe] = []

    def activate(self, context: PluginContext) -> None:
        self._unsubscribe.append(context.bus.subscribe(TrackChanged, self._on_track))

    def deactivate(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()

    def _on_track(self, event: TrackChanged) -> None:
        if event.track:
            log.info("Now playing: %s", event.track.display_title)
