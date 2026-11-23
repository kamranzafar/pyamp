from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.app import Application, parse_args
from pyamp.audio import registry
from pyamp.config import Settings, SettingsStore
from pyamp.core.models import RepeatMode, Track
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.ui.background_loader import LoadJob
from tests.conftest import FakeBackend


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real audio device: 'fake' is registered and also the fallback default."""
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path)


def build(store: SettingsStore, **overrides: object) -> Application:
    args = argparse.Namespace(paths=[], backend="fake", no_plugins=True, log_level="WARNING")
    for key, value in overrides.items():
        setattr(args, key, value)
    return Application(args, store)


@pytest.fixture
def cleanup(qt_app: QCoreApplication) -> Iterator[list[Application]]:
    apps: list[Application] = []
    yield apps
    for application in apps:
        application.loader.shutdown()
        application.window.close()


def test_parse_args_defaults() -> None:
    args = parse_args([])
    assert args.paths == []
    assert args.backend is None
    assert args.no_plugins is False
    assert args.log_level == "WARNING"


def test_parse_args_paths_and_flags() -> None:
    args = parse_args(["a.mp3", "dir", "--no-plugins", "--log-level", "INFO"])
    assert args.paths == [Path("a.mp3"), Path("dir")]
    assert args.no_plugins is True
    assert args.log_level == "INFO"


def test_saved_settings_are_applied_on_startup(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(volume=0.25, shuffle=True, repeat="one", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.volume == pytest.approx(0.25)
    assert application.player.shuffle is True
    assert application.player.repeat is RepeatMode.ONE


def test_invalid_repeat_setting_falls_back_to_off(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(repeat="sideways", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.repeat is RepeatMode.OFF


def test_unknown_backend_in_settings_falls_back_to_default(
    store: SettingsStore, cleanup: list[Application], caplog: pytest.LogCaptureFixture
) -> None:
    store.save(Settings(backend="gone"))
    application = build(store, backend=None)
    cleanup.append(application)
    assert "Unknown backend 'gone'" in caplog.text
    assert application.player is not None


def test_shutdown_saves_settings_and_playlist(
    store: SettingsStore, cleanup: list[Application], tmp_path: Path
) -> None:
    application = build(store)
    cleanup.append(application)
    application.player.set_volume(0.4)
    application.player.set_shuffle(True)
    application.player.set_repeat(RepeatMode.ALL)
    tracks = [Track.from_path(tmp_path / "a.mp3"), Track.from_path(tmp_path / "b.mp3")]
    application.playlist.add(tracks)

    application.shutdown()

    saved = store.load()
    assert saved.volume == pytest.approx(0.4)
    assert saved.shuffle is True
    assert saved.repeat == "all"
    assert PlaylistFormatRegistry.default().read(store.session_playlist) == [t.path for t in tracks]


def test_shutdown_keeps_saved_playlist_while_restore_is_incomplete(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.directory.mkdir(parents=True, exist_ok=True)
    store.session_playlist.write_text("#EXTM3U\n/music/original.mp3\n")
    application = build(store)
    cleanup.append(application)
    application._restore_job = LoadJob([store.session_playlist])  # still running

    application.shutdown()

    assert "original.mp3" in store.session_playlist.read_text()


def test_shutdown_survives_unwritable_config_dir(
    tmp_path: Path, cleanup: list[Application], caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    application = build(SettingsStore(blocker))
    cleanup.append(application)
    application.shutdown()  # must not raise
    assert "Could not save session" in caplog.text
