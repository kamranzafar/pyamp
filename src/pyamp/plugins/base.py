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

"""Plugin interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pyamp.core.events import EventBus
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.library.sources import MediaSources


class PluginUi(Protocol):
    """What a plugin may add to the user interface."""

    def add_menu_action(
        self,
        menu: str,
        text: str,
        triggered: Callable[[], object],
        shortcut: str | None = None,
    ) -> None:
        """Add a menu item, in the menu titled ``menu`` (created before Help if needed)."""

    def add_menu_separator(self, menu: str) -> None:
        """Separate what's added next to ``menu`` from what's there, e.g. one plugin's
        items from another's (never two separators in a row, nor one at the top)."""

    def dialog_parent(self) -> object:
        """The widget to parent a plugin's windows and dialogs to (the main window)."""


@dataclass(frozen=True, slots=True)
class PluginContext:
    """The application services a plugin is allowed to use."""

    bus: EventBus
    player: Player
    playlist: Playlist
    #: The main window's native handle (an HWND on Windows), once it exists.
    window_handle: Callable[[], int | None] = field(default=lambda: None)
    #: The user interface, once it exists (None when running without one).
    ui: Callable[[], PluginUi | None] = field(default=lambda: None)
    #: Where the library window finds music; plugins may add sources (None: no library).
    sources: MediaSources | None = None


@runtime_checkable
class Plugin(Protocol):
    name: str

    def activate(self, context: PluginContext) -> None: ...

    def deactivate(self) -> None: ...
