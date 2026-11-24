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

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMenu

from pyamp.app import Application
from pyamp.core.events import EventBus, TrackChanged
from pyamp.core.models import Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.plugins import PluginContext, PluginManager, manager
from pyamp.plugins.now_playing import NowPlayingLogPlugin
from pyamp.ui.main_window import MainWindow
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)


class RecordingPlugin:
    def __init__(self, name: str, log: list[str], *, fail_on: str = "") -> None:
        self.name = name
        self._log = log
        self._fail_on = fail_on
        self.contexts: list[PluginContext] = []

    def activate(self, context: PluginContext) -> None:
        if self._fail_on == "activate":
            raise RuntimeError("activate failed")
        self.contexts.append(context)
        self._log.append(f"activate {self.name}")

    def deactivate(self) -> None:
        self._log.append(f"deactivate {self.name}")
        if self._fail_on == "deactivate":
            raise RuntimeError("deactivate failed")


class FakeEntryPoint:
    def __init__(self, name: str, load: Callable[[], object]) -> None:
        self.name = name
        self._load = load

    def load(self) -> Callable[[], object]:
        return self._load


@pytest.fixture
def context(bus: EventBus, player: Player, playlist: Playlist) -> PluginContext:
    return PluginContext(bus, player, playlist)


@pytest.fixture
def plugins(context: PluginContext) -> PluginManager:
    return PluginManager(context)


def test_register_activates_with_context(plugins: PluginManager, context: PluginContext) -> None:
    calls: list[str] = []
    plugin = RecordingPlugin("a", calls)
    plugins.register(plugin)
    assert plugins.active_plugins == (plugin,)
    assert plugin.contexts == [context]


def test_object_without_plugin_protocol_is_rejected(
    plugins: PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    plugins.register(object())
    assert plugins.active_plugins == ()
    assert "does not implement the Plugin protocol" in caplog.text


def test_failing_activation_is_skipped(
    plugins: PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    plugins.register(RecordingPlugin("bad", calls, fail_on="activate"))
    good = RecordingPlugin("good", calls)
    plugins.register(good)
    assert plugins.active_plugins == (good,)
    assert "failed to activate" in caplog.text


def test_shutdown_deactivates_in_reverse_order_despite_errors(
    plugins: PluginManager, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    for plugin in (
        RecordingPlugin("a", calls),
        RecordingPlugin("b", calls, fail_on="deactivate"),
        RecordingPlugin("c", calls),
    ):
        plugins.register(plugin)
    calls.clear()
    plugins.shutdown()
    assert calls == ["deactivate c", "deactivate b", "deactivate a"]
    assert plugins.active_plugins == ()
    assert "failed to deactivate" in caplog.text


def test_entry_points_are_loaded_honouring_disabled_and_failures(
    plugins: PluginManager, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []

    def broken() -> object:
        raise ImportError("missing dependency")

    eps = [
        FakeEntryPoint("one", lambda: RecordingPlugin("one", calls)),
        FakeEntryPoint("off", lambda: RecordingPlugin("off", calls)),
        FakeEntryPoint("broken", broken),
        FakeEntryPoint("two", lambda: RecordingPlugin("two", calls)),
    ]

    def fake_entry_points(*, group: str) -> list[FakeEntryPoint]:
        assert group == manager.ENTRY_POINT_GROUP
        return eps

    monkeypatch.setattr(manager, "entry_points", fake_entry_points)
    plugins.load_entry_points(disabled=frozenset({"off"}))
    assert [p.name for p in plugins.active_plugins] == ["one", "two"]
    assert "Failed to load plugin 'broken'" in caplog.text


def test_builtin_plugin_is_registered_as_entry_point() -> None:
    names = {ep.name: ep.value for ep in entry_points(group=manager.ENTRY_POINT_GROUP)}
    assert names.get("now-playing-log") == "pyamp.plugins.now_playing:NowPlayingLogPlugin"
    assert names.get("subsonic") == "pyamp.plugins.subsonic:SubsonicPlugin"


def test_now_playing_plugin_logs_and_unsubscribes(
    plugins: PluginManager, bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pyamp.plugins.now_playing")
    plugins.register(NowPlayingLogPlugin())
    track = Track(location=Path("/m/a.mp3"), title="Song", artist="Band")

    bus.publish(TrackChanged(track, 0))
    assert "Now playing: Band - Song" in caplog.text

    plugins.shutdown()
    caplog.clear()
    bus.publish(TrackChanged(track, 0))
    assert caplog.text == ""


def test_plugins_can_add_menus_before_help(app: Application) -> None:  # noqa: F811
    ui = app.plugins._context.ui()
    assert isinstance(ui, MainWindow)
    calls: list[str] = []
    ui.add_menu_action("&Library", "Browse…", lambda: calls.append("browse"), "Ctrl+B")
    ui.add_menu_action("&Library", "Server…", lambda: calls.append("server"))
    titles = [a.text() for a in ui.menuBar().actions()]
    assert titles[-2:] == ["&Library", "&Help"]
    library = next(a.menu() for a in ui.menuBar().actions() if a.text() == "&Library")
    assert isinstance(library, QMenu)
    browse, server = library.actions()
    assert browse.shortcut().toString() == "Ctrl+B"
    server.trigger()
    assert calls == ["server"]
    assert ui.dialog_parent() is ui
