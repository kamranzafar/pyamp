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
