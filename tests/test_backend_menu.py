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
"""Playback → Audio Backend: choosing the backend PyAmp uses on its next start."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.app import Application
from pyamp.audio import registry
from pyamp.config import Settings, SettingsStore
from pyamp.library.saved_playlists import SavedPlaylists
from tests.conftest import FakeBackend
from tests.test_background_loader import wait_until


@pytest.fixture
def app(
    qt_app: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Application]:
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")
    args = argparse.Namespace(
        paths=[], backend="fake", no_plugins=True, log_level="WARNING", libmpv=None
    )
    application = Application(
        args, SettingsStore(tmp_path / "config"), SavedPlaylists(tmp_path / "playlists")
    )
    application.start([])
    wait_until(lambda: not application.playlists.loading)
    yield application
    application.loader.shutdown()
    application.window._quitting = True
    application.window.close()


def test_lists_every_backend_and_marks_the_one_in_use(app: Application) -> None:
    actions = app.window.backend_actions
    assert list(actions) == registry.available_backends()
    assert actions["fake"].text() == "fake (in use)"
    assert actions["qt"].text() == "Qt Multimedia"
    assert actions["mpv"].text() == "mpv"
    assert [name for name, a in actions.items() if a.isChecked()] == ["fake"]


def test_choosing_a_backend_saves_it_for_the_next_start(app: Application) -> None:
    window = app.window
    window.backend_actions["qt"].trigger()
    assert app.settings.backend == "qt"
    assert window.backend_actions["qt"].isChecked()
    assert not window.backend_actions["fake"].isChecked()
    assert app.player.backend_name == "fake"  # nothing changes until a restart
    assert "next time it starts" in window.statusBar().currentMessage()


def test_choosing_the_backend_in_use_again_keeps_it(app: Application) -> None:
    window = app.window
    window.backend_actions["qt"].trigger()
    window.backend_actions["fake"].trigger()
    assert app.settings.backend == "fake"
    assert window.statusBar().currentMessage() == "PyAmp will keep using fake"
