"""Discovers, activates and shuts down plugins."""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from pyamp.plugins.base import Plugin, PluginContext

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "pyamp.plugins"


class PluginManager:
    """Owns plugin lifecycles. A failing plugin is logged and skipped, never fatal."""

    def __init__(self, context: PluginContext) -> None:
        self._context = context
        self._active: list[Plugin] = []

    @property
    def active_plugins(self) -> tuple[Plugin, ...]:
        return tuple(self._active)

    def load_entry_points(self, disabled: frozenset[str] = frozenset()) -> None:
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if ep.name in disabled:
                continue
            try:
                plugin = ep.load()()
            except Exception:
                log.exception("Failed to load plugin %r", ep.name)
                continue
            self.register(plugin)

    def register(self, plugin: object) -> None:
        if not isinstance(plugin, Plugin):
            log.error("%r does not implement the Plugin protocol; skipped", plugin)
            return
        try:
            plugin.activate(self._context)
        except Exception:
            log.exception("Plugin %r failed to activate", plugin.name)
            return
        self._active.append(plugin)
        log.info("Activated plugin %r", plugin.name)

    def shutdown(self) -> None:
        for plugin in reversed(self._active):
            try:
                plugin.deactivate()
            except Exception:
                log.exception("Plugin %r failed to deactivate", plugin.name)
        self._active.clear()
