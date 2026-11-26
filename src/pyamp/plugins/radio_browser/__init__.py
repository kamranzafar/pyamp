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
"""Radio Directory plugin: find internet radio stations in Radio Browser's directory
(radio-browser.info), in the library window just below your own Radio stations.

Browse the top voted and most played stations, or by country, genre or language; or pick
Radio Directory and search it by name. Play a station, add it to a playlist, or
right-click it and Save to Radio… to keep it with your own stations.

Nothing is sent to Radio Browser until a list is opened. Playing a station found there
tells Radio Browser so (just the station's id), as it asks: its "most played" list counts
these.
"""

from __future__ import annotations

import threading

from pyamp.core.events import TrackChanged, Unsubscribe
from pyamp.core.models import StreamUrl
from pyamp.library.radio_source import RadioSource
from pyamp.plugins.base import PluginContext
from pyamp.plugins.radio_browser.client import RadioBrowserClient
from pyamp.plugins.radio_browser.source import RadioBrowserSource


class RadioBrowserPlugin:
    name = "radio-browser"

    def __init__(self, client: RadioBrowserClient | None = None) -> None:
        self._client = client or RadioBrowserClient()
        self._context: PluginContext | None = None
        self.source: RadioBrowserSource | None = None
        self._stop_listening: Unsubscribe | None = None

    def activate(self, context: PluginContext) -> None:
        if context.sources is None:  # no library
            return
        self._context = context
        self.source = RadioBrowserSource(self._client)
        context.sources.add(self.source, after=RadioSource.name)
        self._stop_listening = context.bus.subscribe(TrackChanged, self._on_track)

    def deactivate(self) -> None:
        if self._stop_listening is not None:
            self._stop_listening()
            self._stop_listening = None
        if self._context is not None and self._context.sources is not None and self.source:
            self._context.sources.remove(self.source)
        self.source = None
        self._context = None

    def _on_track(self, event: TrackChanged) -> None:
        location = event.track.location if event.track else None
        if not isinstance(location, StreamUrl):
            return
        station = self._client.station_id(location)
        if station is not None:  # off the main thread: it's a web request
            threading.Thread(
                target=self._client.count_play,
                args=(station,),
                name="pyamp-radio-play",
                daemon=True,
            ).start()
