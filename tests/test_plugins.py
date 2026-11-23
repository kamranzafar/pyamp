from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from pyamp.core.events import EventBus, TrackChanged
from pyamp.core.models import Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist
from pyamp.plugins import PluginContext, PluginManager, manager
from pyamp.plugins.now_playing import NowPlayingLogPlugin


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


def test_now_playing_plugin_logs_and_unsubscribes(
    plugins: PluginManager, bus: EventBus, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pyamp.plugins.now_playing")
    plugins.register(NowPlayingLogPlugin())
    track = Track(path=Path("/m/a.mp3"), title="Song", artist="Band")

    bus.publish(TrackChanged(track, 0))
    assert "Now playing: Band - Song" in caplog.text

    plugins.shutdown()
    caplog.clear()
    bus.publish(TrackChanged(track, 0))
    assert caplog.text == ""
