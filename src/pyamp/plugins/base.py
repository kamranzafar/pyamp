"""Plugin interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from pyamp.core.events import EventBus
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist


@dataclass(frozen=True, slots=True)
class PluginContext:
    """The application services a plugin is allowed to use."""

    bus: EventBus
    player: Player
    playlist: Playlist


@runtime_checkable
class Plugin(Protocol):
    name: str

    def activate(self, context: PluginContext) -> None: ...

    def deactivate(self) -> None: ...
