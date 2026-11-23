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

from pathlib import Path

from pyamp.config import Settings, SettingsStore


def test_round_trip(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.save(Settings(volume=0.3, shuffle=True))
    loaded = store.load()
    assert loaded.volume == 0.3
    assert loaded.shuffle is True


def test_missing_or_corrupt_file_gives_defaults(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    assert store.load() == Settings()
    store.settings_file.write_text("{not json")
    assert store.load() == Settings()


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    store.settings_file.write_text('{"volume": 0.5, "from_the_future": 1}')
    assert store.load().volume == 0.5
