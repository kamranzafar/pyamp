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

import os

import pytest

from pyamp.audio import libmpv_path
from pyamp.audio.libmpv_path import macos_library_path


def test_homebrew_dir_is_added_to_dyld_defaults() -> None:
    result = macos_library_path({}, ("/opt/homebrew/lib",))
    assert result == "~/lib:/usr/local/lib:/lib:/usr/lib:/opt/homebrew/lib"


def test_existing_value_is_kept_and_not_duplicated() -> None:
    env = {"DYLD_FALLBACK_LIBRARY_PATH": "/custom/lib:/opt/homebrew/lib"}
    result = macos_library_path(env, ("/opt/homebrew/lib", "/opt/local/lib"))
    assert result == "/custom/lib:/opt/homebrew/lib:/opt/local/lib"


def test_nothing_to_add() -> None:
    assert macos_library_path({}, ()) == "~/lib:/usr/local/lib:/lib:/usr/lib"


def test_prepare_only_touches_the_environment_on_macos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYLD_FALLBACK_LIBRARY_PATH", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    libmpv_path.prepare_libmpv_lookup()
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ

    monkeypatch.setattr("sys.platform", "darwin")
    libmpv_path.prepare_libmpv_lookup()
    assert os.environ["DYLD_FALLBACK_LIBRARY_PATH"].startswith("~/lib:/usr/local/lib")
