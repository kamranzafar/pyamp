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

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QAction

from pyamp import app as app_module
from pyamp.app import Application, parse_args
from pyamp.audio import libmpv_loader, registry
from pyamp.audio.registry import BackendUnavailableError
from pyamp.config import Settings, SettingsStore
from pyamp.core.equalizer import Equalizer
from pyamp.core.models import RepeatMode, ReplayGainMode, Track
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.ui.background_loader import LoadJob
from tests.conftest import FakeBackend, FakeGaplessBackend


@pytest.fixture(autouse=True)
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never touch a real audio device: 'fake' is registered and also the fallback default."""
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path)


def build(store: SettingsStore, **overrides: object) -> Application:
    args = argparse.Namespace(
        paths=[], backend="fake", no_plugins=True, log_level="WARNING", libmpv=None
    )
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


def test_version_shows_libmpv_status(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        app_module, "version_text", lambda: "pyamp 9.9\nlibmpv: somewhere (user folder)"
    )
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "pyamp 9.9\nlibmpv: somewhere (user folder)\n"


def test_version_text_reports_missing_libmpv(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins  # noqa: PLC0415

    real_import = builtins.__import__

    def no_libmpv(name: str, *args: object, **kwargs: object) -> object:
        if name == "pyamp.audio.mpv_backend":
            raise OSError("Cannot find libmpv")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", no_libmpv)
    text = app_module.version_text()
    assert text.startswith("pyamp ")
    assert "libmpv: not available (Cannot find libmpv)" in text


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


def test_replay_gain_setting_is_restored_and_saved(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(replay_gain="album", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.replay_gain is ReplayGainMode.ALBUM
    application.player.set_replay_gain(ReplayGainMode.TRACK)
    application.shutdown()
    assert store.load().replay_gain == "track"


def test_invalid_replay_gain_setting_falls_back_to_off(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    store.save(Settings(replay_gain="loudest", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert application.player.replay_gain is ReplayGainMode.OFF


def test_equalizer_setting_is_restored_and_saved(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    gains = [3.0, 2.0, 1.0, 0.0, -1.0, -2.0, 0.0, 1.0, 2.0, 3.0]
    store.save(
        Settings(
            equalizer_enabled=True, equalizer_preamp=-3.0, equalizer_gains=gains, backend="fake"
        )
    )
    application = build(store)
    cleanup.append(application)
    assert application.player.equalizer == Equalizer(True, -3.0, tuple(gains))

    application.player.set_equalizer(Equalizer().with_preset("Vocal"))
    application.shutdown()
    saved = store.load()
    assert saved.equalizer_enabled is False
    assert tuple(saved.equalizer_gains) == Equalizer().with_preset("Vocal").gains_db


@pytest.mark.parametrize("gains", [[1.0, 2.0], ["loud"] * 10, None])
def test_malformed_equalizer_setting_falls_back_to_flat(
    store: SettingsStore, cleanup: list[Application], gains: object
) -> None:
    store.save(Settings(equalizer_enabled=True, equalizer_gains=gains, backend="fake"))  # type: ignore[arg-type]
    application = build(store)
    cleanup.append(application)
    assert application.player.equalizer == Equalizer()


def test_equalizer_window_is_opened_from_the_menu_and_reused(
    store: SettingsStore, cleanup: list[Application]
) -> None:
    application = build(store)
    cleanup.append(application)
    titles = [a.text() for a in application.window.findChildren(QAction)]
    assert "&Equaliser\u2026" in titles
    first = application.window.show_equalizer()
    assert application.window.show_equalizer() is first
    assert first.isVisible()
    first.close()


def test_libmpv_preference_from_flag_is_remembered(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libmpv_loader, "_state", {"preference": "auto"})
    application = build(store, libmpv="system")
    cleanup.append(application)
    assert libmpv_loader.get_preference() == "system"
    application.shutdown()
    assert store.load().libmpv == "system"


def test_invalid_libmpv_setting_falls_back_to_auto(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libmpv_loader, "_state", {"preference": "auto"})
    store.save(Settings(libmpv="newest", backend="fake"))
    application = build(store)
    cleanup.append(application)
    assert libmpv_loader.get_preference() == "auto"
    assert application.settings.libmpv == "auto"


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


def test_unavailable_backend_falls_back_to_default(
    store: SettingsStore,
    cleanup: list[Application],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(name: str) -> FakeBackend:
        if name == "mpv":
            raise BackendUnavailableError("mpv", "Cannot find libmpv")
        return FakeBackend()

    monkeypatch.setattr(app_module, "create_backend", unavailable)
    application = build(store, backend="mpv")
    cleanup.append(application)
    assert "'mpv' is unavailable" in caplog.text
    assert application.settings.backend == "fake"


def test_chosen_backend_is_remembered(
    store: SettingsStore, cleanup: list[Application], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(registry._registered, "other", FakeGaplessBackend)
    application = build(store, backend="other")
    cleanup.append(application)
    application.shutdown()
    assert store.load().backend == "other"  # the registry key, not the class's name


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
