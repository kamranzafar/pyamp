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
