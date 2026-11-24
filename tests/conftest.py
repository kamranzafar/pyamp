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

import os
import threading
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication

from pyamp.audio.backend import AudioBackend
from pyamp.core.equalizer import Equalizer
from pyamp.core.events import EventBus
from pyamp.core.models import Location, PlaybackState, ReplayGainMode, Track
from pyamp.core.player import Player
from pyamp.core.playlist import Playlist

# Widget tests must never open real windows (and must work on CI without a display).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeBackend(AudioBackend):
    """In-memory backend that records calls and lets tests simulate callbacks."""

    name = "fake"
    provides_audio_frames = True  # tests drive the visualiser with emit_audio()

    def __init__(self) -> None:
        super().__init__()
        self.loaded: list[Location] = []
        self.calls: list[str] = []
        self.volume = 0.0
        self.position = 0
        self.gains: list[float] = []
        self.equalizers: list[Equalizer] = []
        self.replay_gain_modes: list[ReplayGainMode] = []

    def load(self, location: Location) -> None:
        self.loaded.append(location)

    def play(self) -> None:
        self.calls.append("play")
        self._listener.on_state_changed(PlaybackState.PLAYING)

    def pause(self) -> None:
        self.calls.append("pause")
        self._listener.on_state_changed(PlaybackState.PAUSED)

    def stop(self) -> None:
        self.calls.append("stop")
        self._listener.on_state_changed(PlaybackState.STOPPED)

    def seek(self, position_ms: int) -> None:
        self.position = position_ms
        self._listener.on_position_changed(position_ms)

    def set_volume(self, volume: float) -> None:
        self.volume = volume

    def set_equalizer(self, equalizer: Equalizer) -> None:
        self.equalizers.append(equalizer)

    def set_track_gain(self, factor: float) -> None:
        self.gains.append(factor)

    def set_replay_gain_mode(self, mode: ReplayGainMode) -> None:
        self.replay_gain_modes.append(mode)

    # -- simulation helpers --
    def finish_track(self) -> None:
        self._listener.on_end_of_track()

    def emit_audio(self, samples: NDArray[np.float32], rate: int) -> None:
        self._listener.on_audio_frame(samples, rate)


class FakeGaplessBackend(FakeBackend):
    """A gapless backend: records what is queued and can simulate the transition."""

    name = "fake-gapless"
    supports_gapless = True

    def __init__(self) -> None:
        super().__init__()
        self.queued: list[Location | None] = []

    @property
    def next_up(self) -> Location | None:
        return self.queued[-1] if self.queued else None

    def queue_next(self, location: Location | None) -> None:
        self.queued.append(location)

    # -- simulation helpers --
    def advance(self, path: Location | None = None) -> None:
        """The queued track (or ``path``) starts playing without a gap."""
        target = path or self.next_up
        assert target is not None, "nothing queued"
        self._listener.on_track_advanced(target)


def make_tracks(n: int) -> list[Track]:
    return [
        Track(location=Path(f"/music/{i}.mp3"), title=f"Song {i}", duration_ms=60_000)
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def isolated_playlists(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may touch the user's real saved playlists."""
    folder = tmp_path_factory.mktemp("saved-playlists")
    monkeypatch.setattr("pyamp.library.saved_playlists.default_directory", lambda: folder)


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def playlist(bus: EventBus) -> Playlist:
    return Playlist(bus)


@pytest.fixture
def player(backend: FakeBackend, playlist: Playlist, bus: EventBus) -> Player:
    return Player(backend, playlist, bus)


@pytest.fixture(scope="session")
def qt_app() -> Iterator[QCoreApplication]:
    """A Qt application object, needed for signals, threads and widgets (offscreen)."""
    yield QApplication.instance() or QApplication([])


class BackendRecorder:
    """A BackendListener that records every callback and the thread it ran on."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []
        self.threads: set[int] = set()

    def _record(self, name: str, value: object = None) -> None:
        self.threads.add(threading.get_ident())
        self.events.append((name, value))

    def names(self) -> list[str]:
        return [name for name, _ in self.events]

    def values(self, name: str) -> list[object]:
        return [value for n, value in self.events if n == name]

    def on_state_changed(self, state: PlaybackState) -> None:
        self._record("state", state)

    def on_position_changed(self, position_ms: int) -> None:
        self._record("position", position_ms)

    def on_duration_changed(self, duration_ms: int) -> None:
        self._record("duration", duration_ms)

    def on_end_of_track(self) -> None:
        self._record("end")

    def on_track_advanced(self, location: Location) -> None:
        self._record("advanced", location)

    def on_stream_title(self, title: str) -> None:
        self._record("stream_title", title)

    def on_error(self, message: str) -> None:
        self._record("error", message)

    def on_audio_info(self, bitrate: int, sample_rate: int) -> None:
        self._record("audio_info", (bitrate, sample_rate))

    def on_audio_frame(self, samples: NDArray[np.float32], sample_rate: int) -> None:
        self._record("audio")
