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
"""Scanning the library in the background: off the main thread, at low priority."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QThread, QTimer

from pyamp.core.events import EventBus, LibraryChanged
from pyamp.core.models import Track
from pyamp.library.catalog import Catalog
from pyamp.library.scanner import LibraryScanner, ScanSummary
from pyamp.ui import library_scan
from pyamp.ui.library_scan import BackgroundLibraryScanner
from tests.test_background_loader import GatedReader, make_files, wait_until


class RecordingReader(GatedReader):
    """Also records the priority of the thread that reads the tags."""

    def __init__(self) -> None:
        super().__init__()
        self.priorities: set[QThread.Priority] = set()

    def read(self, path: Path) -> Track:
        self.priorities.add(QThread.currentThread().priority())
        return super().read(path)


@pytest.fixture
def reader() -> RecordingReader:
    return RecordingReader()


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    yield opened
    opened.close()


@pytest.fixture
def library(
    qt_app: QCoreApplication, catalog: Catalog, reader: RecordingReader, bus: EventBus
) -> Iterator[BackgroundLibraryScanner]:
    background = BackgroundLibraryScanner(catalog, LibraryScanner(catalog, reader), bus)
    yield background
    reader.gate.set()
    background.shutdown()


class Recorder:
    def __init__(self, library: BackgroundLibraryScanner, bus: EventBus) -> None:
        self.finished: list[ScanSummary] = []
        self.progress: list[int] = []
        self.changes = 0
        self.change_threads: set[int] = set()
        library.finished.connect(self.finished.append)
        library.progress.connect(self.progress.append)
        bus.subscribe(LibraryChanged, self._on_changed)

    def _on_changed(self, _: LibraryChanged) -> None:
        self.changes += 1
        self.change_threads.add(threading.get_ident())


@pytest.fixture
def recorder(library: BackgroundLibraryScanner, bus: EventBus) -> Recorder:
    return Recorder(library, bus)


def test_scans_off_the_main_thread_at_low_priority(
    library: BackgroundLibraryScanner,
    reader: RecordingReader,
    recorder: Recorder,
    catalog: Catalog,
    tmp_path: Path,
) -> None:
    make_files(tmp_path / "music", 3)
    assert library.add_sources([tmp_path / "music"]) == 1
    wait_until(lambda: recorder.finished == [ScanSummary(added=3)])

    main = threading.get_ident()
    assert main not in reader.threads  # tags were read on the worker
    assert reader.priorities == {QThread.Priority.LowPriority}  # behind playback and the UI
    assert recorder.change_threads == {main}  # but the news arrived on the main thread
    assert recorder.changes == 1
    assert len(catalog) == 3
    assert not library.busy


def test_the_window_keeps_working_while_a_scan_runs(
    library: BackgroundLibraryScanner, reader: RecordingReader, tmp_path: Path
) -> None:
    make_files(tmp_path / "music", 2)
    reader.gate.clear()  # the worker blocks on the first file
    started = time.monotonic()
    library.add_sources([tmp_path / "music"])
    assert time.monotonic() - started < 0.1  # returned at once
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    wait_until(lambda: len(ticks) >= 5)  # the main thread's event loop kept running
    timer.stop()
    assert library.busy
    reader.gate.set()
    wait_until(lambda: not library.busy)


def test_progress_is_reported(
    library: BackgroundLibraryScanner,
    recorder: Recorder,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(library_scan, "PROGRESS_INTERVAL_S", 0)
    make_files(tmp_path / "music", 3)
    library.add_sources([tmp_path / "music"])
    wait_until(lambda: bool(recorder.finished))
    assert recorder.progress == [1, 2, 3]


def test_scanning_again_restarts_a_running_scan(
    library: BackgroundLibraryScanner,
    reader: RecordingReader,
    recorder: Recorder,
    catalog: Catalog,
    tmp_path: Path,
) -> None:
    make_files(tmp_path / "music", 3)
    reader.gate.clear()
    library.add_sources([tmp_path / "music"])
    library.scan()  # e.g. Rescan clicked while the first scan runs
    reader.gate.set()
    wait_until(lambda: not library.busy)
    assert len(recorder.finished) == 1  # only the scan that ran to the end reports
    assert len(catalog) == 3


def test_a_scan_with_no_changes_reports_but_changes_nothing(
    library: BackgroundLibraryScanner, recorder: Recorder, tmp_path: Path
) -> None:
    make_files(tmp_path / "music", 1)
    library.add_sources([tmp_path / "music"])
    wait_until(lambda: len(recorder.finished) == 1)
    library.scan()
    wait_until(lambda: len(recorder.finished) == 2)
    assert recorder.finished[-1] == ScanSummary(unchanged=1)
    assert recorder.changes == 1  # the window isn't refreshed for nothing


def test_adding_what_is_already_there_does_not_scan(
    library: BackgroundLibraryScanner, tmp_path: Path
) -> None:
    library.add_sources([tmp_path / "music"])
    wait_until(lambda: not library.busy)
    assert library.add_sources([tmp_path / "music" / "rock"]) == 0
    assert not library.busy


def test_removing_a_source_forgets_its_tracks(
    library: BackgroundLibraryScanner, recorder: Recorder, catalog: Catalog, tmp_path: Path
) -> None:
    make_files(tmp_path / "music", 2)
    library.add_sources([tmp_path / "music"])
    wait_until(lambda: not library.busy)
    library.remove_source(tmp_path / "music")
    assert len(catalog) == 0
    assert recorder.changes == 2
    assert (tmp_path / "music" / "000.mp3").exists()  # the files stay


def test_removing_a_source_mid_scan_rescans(
    library: BackgroundLibraryScanner, reader: RecordingReader, catalog: Catalog, tmp_path: Path
) -> None:
    make_files(tmp_path / "music", 3)
    reader.gate.clear()
    library.add_sources([tmp_path / "music"])
    library.remove_source(tmp_path / "music")
    reader.gate.set()
    wait_until(lambda: not library.busy)
    assert len(catalog) == 0  # nothing the cancelled scan read is left


def test_a_delayed_scan(library: BackgroundLibraryScanner, recorder: Recorder) -> None:
    library.scan_later(10)
    assert not library.busy
    wait_until(lambda: len(recorder.finished) == 1)


def test_a_failed_scan_is_logged(
    library: BackgroundLibraryScanner,
    recorder: Recorder,
    catalog: Catalog,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken() -> list[Path]:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(catalog, "sources", broken)
    library.scan()
    wait_until(lambda: not library.busy)
    assert "Library scan failed" in caplog.text
    assert recorder.finished == []


def test_shutdown_stops_a_scan_and_keeps_what_it_read(
    library: BackgroundLibraryScanner,
    reader: RecordingReader,
    recorder: Recorder,
    catalog: Catalog,
    tmp_path: Path,
) -> None:
    make_files(tmp_path / "music", 3)
    reader.gate.clear()
    library.scan_later(60_000)  # a pending start-up scan is dropped too
    library.add_sources([tmp_path / "music"])
    threading.Timer(0.1, reader.gate.set).start()  # the worker finishes its current file
    library.shutdown()
    QCoreApplication.processEvents()
    assert recorder.finished == []
    assert len(catalog) <= 1
