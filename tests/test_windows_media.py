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
"""Windows media controls (SMTC): adapter behaviour with a fake, plus a real smoke test."""

from __future__ import annotations

import enum
import sys
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.core.models import PlaybackState, Track
from pyamp.media_session import MediaCommands, NowPlaying, create_adapter
from pyamp.media_session.windows import WindowsMediaAdapter, WinRtApi
from tests.test_background_loader import wait_until


class Status(enum.IntEnum):
    CLOSED = 0
    CHANGING = 1
    STOPPED = 2
    PLAYING = 3
    PAUSED = 4


class PlaybackType(enum.IntEnum):
    UNKNOWN = 0
    MUSIC = 1


class Button(enum.IntEnum):
    PLAY = 0
    PAUSE = 1
    STOP = 2
    NEXT = 6
    PREVIOUS = 7


class FakeDisplay:
    def __init__(self) -> None:
        self.music_properties = SimpleNamespace(title="", artist="", album_title="")
        self.type = PlaybackType.UNKNOWN
        self.updates = 0
        self.cleared = 0

    def update(self) -> None:
        self.updates += 1

    def clear_all(self) -> None:
        self.cleared += 1
        self.music_properties = SimpleNamespace(title="", artist="", album_title="")


class FakeControls:
    """Records what the adapter sets, like Windows' SystemMediaTransportControls."""

    def __init__(self) -> None:
        self.display_updater = FakeDisplay()
        self.handlers: dict[str, Any] = {}
        self.removed: list[str] = []
        self.timeline: Any = None
        self.playback_status = Status.CLOSED
        self.is_enabled = False
        self.is_next_enabled = False
        self.is_previous_enabled = False

    def add_button_pressed(self, handler: Any) -> str:
        self.handlers["button"] = handler
        return "button-token"

    def remove_button_pressed(self, token: str) -> None:
        self.removed.append(token)

    def add_playback_position_change_requested(self, handler: Any) -> str:
        self.handlers["seek"] = handler
        return "seek-token"

    def remove_playback_position_change_requested(self, token: str) -> None:
        self.removed.append(token)

    def update_timeline_properties(self, timeline: Any) -> None:
        self.timeline = timeline

    def press(self, button: Button) -> None:
        """Like Windows: on a background thread."""
        thread = threading.Thread(
            target=self.handlers["button"], args=(self, SimpleNamespace(button=button))
        )
        thread.start()
        thread.join()


API = WinRtApi(status=Status, playback_type=PlaybackType, button=Button, timeline=SimpleNamespace)
TRACK = Track(Path("/m/a.flac"), "Teardrop", artist="Massive Attack", album="Mezzanine")


class Commands:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.threads: set[int] = set()

    def __getattr__(self, name: str) -> Any:
        def record(*args: Any) -> None:
            self.calls.append((name, args[0] if args else None))
            self.threads.add(threading.get_ident())

        return record


@pytest.fixture
def setup(qt_app: QCoreApplication) -> Iterator[tuple[WindowsMediaAdapter, FakeControls, Commands]]:
    controls = FakeControls()
    commands = Commands()
    adapter = WindowsMediaAdapter(commands, controls, API)  # type: ignore[arg-type]
    yield adapter, controls, commands


def test_shows_the_track_and_state(
    setup: tuple[WindowsMediaAdapter, FakeControls, Commands],
) -> None:
    adapter, controls, _ = setup
    assert controls.is_enabled
    adapter.update(
        NowPlaying(
            track=TRACK,
            state=PlaybackState.PLAYING,
            position_ms=30_000,
            duration_ms=240_000,
            can_go_next=True,
        )
    )
    music = controls.display_updater.music_properties
    assert (music.title, music.artist, music.album_title) == (
        "Teardrop",
        "Massive Attack",
        "Mezzanine",
    )
    assert controls.display_updater.type is PlaybackType.MUSIC
    assert controls.playback_status is Status.PLAYING
    assert controls.is_next_enabled and not controls.is_previous_enabled
    assert controls.timeline.end_time == timedelta(minutes=4)
    assert controls.timeline.position == timedelta(seconds=30)


def test_nothing_playing_clears_the_flyout(
    setup: tuple[WindowsMediaAdapter, FakeControls, Commands],
) -> None:
    adapter, controls, _ = setup
    adapter.update(NowPlaying(track=TRACK, state=PlaybackState.PLAYING))
    adapter.update(NowPlaying())
    assert controls.display_updater.cleared == 1
    assert controls.playback_status is Status.STOPPED


def test_buttons_run_on_the_main_thread(
    setup: tuple[WindowsMediaAdapter, FakeControls, Commands],
) -> None:
    _, controls, commands = setup
    for button in (Button.PLAY, Button.PAUSE, Button.NEXT, Button.PREVIOUS, Button.STOP):
        controls.press(button)
    wait_until(lambda: len(commands.calls) == 5)
    assert [name for name, _ in commands.calls] == ["play", "pause", "next", "previous", "stop"]
    assert commands.threads == {threading.get_ident()}  # not Windows' background thread


def test_seek_from_the_flyout(setup: tuple[WindowsMediaAdapter, FakeControls, Commands]) -> None:
    _, controls, commands = setup
    args = SimpleNamespace(requested_playback_position=timedelta(seconds=95))
    threading.Thread(target=controls.handlers["seek"], args=(controls, args)).start()
    wait_until(lambda: bool(commands.calls))
    assert commands.calls == [("seek_to", 95_000)]


def test_close_unregisters_and_clears(
    setup: tuple[WindowsMediaAdapter, FakeControls, Commands],
) -> None:
    adapter, controls, _ = setup
    adapter.close()
    assert sorted(controls.removed) == ["button-token", "seek-token"]
    assert controls.playback_status is Status.CLOSED
    assert not controls.is_enabled


def test_no_window_no_adapter(qt_app: QCoreApplication) -> None:
    assert WindowsMediaAdapter.connect(Commands(), None) is None  # type: ignore[arg-type]
    assert create_adapter(Commands(), platform="win32") is None  # type: ignore[arg-type]


# -- The real thing ------------------------------------------------------------------------ #


@pytest.mark.skipif(sys.platform != "win32", reason="Windows media controls")
def test_real_controls_for_a_window(qt_app: QCoreApplication) -> None:
    import ctypes  # noqa: PLC0415
    from ctypes import wintypes  # noqa: PLC0415

    user32 = ctypes.windll.user32  # type: ignore[attr-defined,unused-ignore]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    hwnd = user32.CreateWindowExW(
        0, "STATIC", "PyAmp test", 0, 0, 0, 10, 10, None, None, None, None
    )
    assert hwnd
    try:
        commands = MediaCommands(SimpleNamespace(position_ms=0))  # type: ignore[arg-type]
        adapter = WindowsMediaAdapter.connect(commands, int(hwnd))
        assert adapter is not None
        adapter.update(
            NowPlaying(
                track=TRACK, state=PlaybackState.PLAYING, duration_ms=240_000, can_go_next=True
            )
        )
        adapter.update(NowPlaying(track=TRACK, state=PlaybackState.PAUSED, duration_ms=240_000))
        adapter.close()
    finally:
        user32.DestroyWindow(wintypes.HWND(hwnd))
