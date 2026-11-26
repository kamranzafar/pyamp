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

"""Reading and writing PyAmp's own files: in one step, and patient with Windows."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyamp import files
from pyamp.config import SettingsStore
from pyamp.files import LOCK_RETRY_S, retry_while_locked
from pyamp.library.radio_stations import RadioStations
from pyamp.library.smart_playlists import SmartPlaylist, SmartPlaylists


def test_retries_while_windows_reports_the_file_in_use() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def busy_twice() -> str:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "The process cannot access the file")
        return "done"

    assert retry_while_locked(busy_twice, platform="win32", sleep=sleeps.append) == "done"
    assert len(calls) == 3
    assert len(sleeps) == 2


def test_gives_up_after_about_a_second() -> None:
    sleeps: list[float] = []

    def always_busy() -> None:
        raise PermissionError(32, "in use")

    with pytest.raises(PermissionError):
        retry_while_locked(always_busy, platform="win32", sleep=sleeps.append)
    assert sum(sleeps) == pytest.approx(LOCK_RETRY_S, abs=0.1)


def test_no_retries_elsewhere() -> None:
    calls: list[int] = []

    def denied() -> None:
        calls.append(1)
        raise PermissionError(13, "Permission denied")  # a real permission problem

    with pytest.raises(PermissionError):
        retry_while_locked(denied, platform="darwin", sleep=lambda _: None)
    assert calls == [1]


def test_a_file_is_written_whole_into_a_new_folder(tmp_path: Path) -> None:
    target = tmp_path / "new" / "folder" / "settings.json"
    files.write_text(target, "{}")
    files.write_text(target, '{"volume": 1}')  # replaced
    assert files.read_text(target) == '{"volume": 1}'
    assert sorted(p.name for p in target.parent.iterdir()) == ["settings.json"]  # no .tmp


def test_a_missing_file_is_an_error_to_the_caller(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        files.read_text(tmp_path / "nope.json")


@pytest.fixture
def windows_busy_once(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Act as Windows does while a file is replaced on another thread: the next read
    and the next replace each fail once. Returns what failed."""
    monkeypatch.setattr(sys, "platform", "win32")
    failed: list[str] = []
    real_read, real_replace = Path.read_text, Path.replace

    def read_text(self: Path, *args: object, **kwargs: object) -> str:
        if "read" not in failed:
            failed.append("read")
            raise PermissionError(13, "Permission denied")
        return real_read(self, *args, **kwargs)  # type: ignore[arg-type]

    def replace(self: Path, target: Path) -> Path:
        if "replace" not in failed:
            failed.append("replace")
            raise PermissionError(32, "The process cannot access the file")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "replace", replace)
    return failed


def test_every_store_waits_for_windows(tmp_path: Path, windows_busy_once: list[str]) -> None:
    """The stores read on worker threads while the main thread saves: each waits."""
    stations = RadioStations(tmp_path / "radio_stations.json")
    stations.save("Jazz", "https://jazz.example/live")
    assert [s.name for s in stations.stations()] == ["Jazz"]
    assert sorted(windows_busy_once) == ["read", "replace"]  # each failed once, then worked

    for make_busy in (smart_store, settings_store):
        windows_busy_once.clear()
        make_busy(tmp_path)
        assert sorted(windows_busy_once) == ["read", "replace"]


def smart_store(tmp_path: Path) -> None:
    store = SmartPlaylists(tmp_path / "smart_playlists.json")
    store.save(SmartPlaylist("Everything"))
    assert store.names() == ["Everything"]


def settings_store(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "config")
    settings = store.load()
    settings.volume = 0.25
    store.save(settings)
    assert store.load().volume == 0.25
