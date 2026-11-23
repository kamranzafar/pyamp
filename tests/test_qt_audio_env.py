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

from pathlib import Path

import pytest

from pyamp.audio.qt_audio_env import (
    ENV_VAR,
    avoid_incomplete_pipewire,
    pipewire_config_dirs,
    pipewire_is_incomplete,
)

HAS_LIB = {"pipewire-0.3": "libpipewire-0.3.so.0"}


def test_config_dirs_follow_pipewires_search_order() -> None:
    env = {"PIPEWIRE_CONFIG_DIR": "/custom", "HOME": "/home/me"}
    assert pipewire_config_dirs(env) == [
        Path("/custom"),
        Path("/home/me/.config/pipewire"),
        Path("/etc/pipewire"),
        Path("/usr/share/pipewire"),
        Path("/usr/local/share/pipewire"),
    ]
    assert pipewire_config_dirs({"XDG_CONFIG_HOME": "/xdg"})[0] == Path("/xdg/pipewire")


def test_no_pipewire_library_is_fine() -> None:
    assert not pipewire_is_incomplete({}, find_library=lambda _: None, exists=lambda _: False)


def test_library_without_client_conf_is_incomplete() -> None:
    assert pipewire_is_incomplete({}, find_library=HAS_LIB.get, exists=lambda _: False)


def test_library_with_client_conf_anywhere_is_complete() -> None:
    def exists(path: Path) -> bool:
        return path == Path("/usr/share/pipewire/client.conf")

    assert not pipewire_is_incomplete({}, find_library=HAS_LIB.get, exists=exists)


def test_pulseaudio_is_selected_when_incomplete(caplog: pytest.LogCaptureFixture) -> None:
    env: dict[str, str] = {}
    assert avoid_incomplete_pipewire(env, "linux", incomplete=lambda _: True)
    assert env[ENV_VAR] == "pulseaudio"
    assert "client.conf" in caplog.text


@pytest.mark.parametrize(
    ("env", "platform", "incomplete"),
    [
        ({}, "linux", False),  # nothing wrong
        ({ENV_VAR: "pipewire"}, "linux", True),  # the user chose a backend
        ({}, "darwin", True),  # Linux-only problem
        ({}, "win32", True),
    ],
)
def test_left_alone_otherwise(env: dict[str, str], platform: str, incomplete: bool) -> None:
    before = dict(env)
    assert not avoid_incomplete_pipewire(env, platform, incomplete=lambda _: incomplete)
    assert env == before
