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

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp.core.models import Track
from pyamp.library.loader import TrackLoader
from pyamp.library.playlist_formats import PlaylistFormatRegistry
from pyamp.ui import background_loader
from pyamp.ui.background_loader import BackgroundTrackLoader, LoadJob


class GatedReader:
    """Metadata reader that records its thread and can be paused by the test."""

    def __init__(self) -> None:
        self.gate = threading.Event()
        self.gate.set()
        self.threads: set[int] = set()

    def read(self, path: Path) -> Track:
        self.gate.wait(timeout=5)
        self.threads.add(threading.get_ident())
        return Track.from_path(path)


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise TimeoutError("condition not met")
        QCoreApplication.processEvents()
        time.sleep(0.005)


def make_files(folder: Path, n: int) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    files = [folder / f"{i:03d}.mp3" for i in range(n)]
    for f in files:
        f.touch()
    return files


@pytest.fixture
def reader() -> GatedReader:
    return GatedReader()


@pytest.fixture
def loader(qt_app: QCoreApplication, reader: GatedReader) -> Iterator[BackgroundTrackLoader]:
    bg = BackgroundTrackLoader(TrackLoader(reader, PlaylistFormatRegistry.default()))
    yield bg
    bg.shutdown()


class Recorder:
    def __init__(self, loader: BackgroundTrackLoader) -> None:
        self.tracks: list[Track] = []
        self.batches = 0
        self.finished: list[LoadJob] = []
        self.slot_threads: set[int] = set()
        loader.tracks_loaded.connect(self._on_batch)
        loader.job_finished.connect(self.finished.append)

    def _on_batch(self, _job: LoadJob, tracks: list[Track]) -> None:
        self.slot_threads.add(threading.get_ident())
        self.tracks.extend(tracks)
        self.batches += 1


def test_loads_off_main_thread_and_delivers_on_main_thread(
    loader: BackgroundTrackLoader, reader: GatedReader, tmp_path: Path
) -> None:
    files = make_files(tmp_path, 3)
    rec = Recorder(loader)
    job = loader.submit([tmp_path])
    wait_until(lambda: rec.finished == [job])

    main = threading.get_ident()
    assert [t.path for t in rec.tracks] == files
    assert main not in reader.threads  # tag reading happened on the worker
    assert rec.slot_threads == {main}  # but results arrived on the main thread
    assert job.loaded == 3
    assert not loader.busy


def test_submit_returns_immediately(
    loader: BackgroundTrackLoader, reader: GatedReader, tmp_path: Path
) -> None:
    make_files(tmp_path, 2)
    reader.gate.clear()  # worker blocks on the first file
    started = time.monotonic()
    loader.submit([tmp_path])
    assert time.monotonic() - started < 0.1
    assert loader.busy
    reader.gate.set()


def test_large_loads_are_batched(loader: BackgroundTrackLoader, tmp_path: Path) -> None:
    make_files(tmp_path, background_loader.BATCH_SIZE * 2 + 5)
    rec = Recorder(loader)
    loader.submit([tmp_path])
    wait_until(lambda: len(rec.finished) == 1)
    assert len(rec.tracks) == background_loader.BATCH_SIZE * 2 + 5
    assert rec.batches >= 3


def test_jobs_complete_in_submission_order(loader: BackgroundTrackLoader, tmp_path: Path) -> None:
    first = make_files(tmp_path / "a", 30)
    second = make_files(tmp_path / "b", 2)
    rec = Recorder(loader)
    loader.submit([tmp_path / "a"])
    loader.submit([tmp_path / "b"])
    wait_until(lambda: len(rec.finished) == 2)
    assert [t.path for t in rec.tracks] == first + second


def test_cancel_drops_pending_and_in_flight_batches(
    loader: BackgroundTrackLoader, reader: GatedReader, tmp_path: Path
) -> None:
    make_files(tmp_path, 10)
    rec = Recorder(loader)
    reader.gate.clear()
    job = loader.submit([tmp_path])
    loader.cancel_all()
    reader.gate.set()
    wait_until(lambda: not loader.busy and loader._pool.activeThreadCount() == 0)
    QCoreApplication.processEvents()  # flush anything the worker queued

    assert rec.tracks == []
    assert rec.finished == []  # cancelled jobs don't report completion
    assert job.cancelled
