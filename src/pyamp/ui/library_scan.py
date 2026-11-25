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
"""Keeps the media library up to date on a low-priority worker thread.

Threading contract (as in :mod:`pyamp.ui.background_loader`):

* The worker only runs :meth:`LibraryScanner.scan`: the file system, tag reading and
  the catalog (which is thread-safe). It never touches the event bus or a widget.
* Progress and results come back through Qt signals, queued to this main-thread
  object, so :class:`LibraryChanged` is always published on the main thread.
* One scan at a time, at low priority, so playback and the window come first. Asking
  for a scan while one runs cancels it and starts again, so a scan always ends with
  everything the library has at that point.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, QTimer, Signal

from pyamp.core.events import EventBus, LibraryChanged
from pyamp.library.catalog import Catalog
from pyamp.library.scanner import LibraryScanner, ScanSummary

log = logging.getLogger(__name__)

#: Progress is reported at most this often.
PROGRESS_INTERVAL_S = 0.25
SHUTDOWN_TIMEOUT_MS = 3000


class _ScanJob:
    def __init__(self) -> None:
        self._cancelled = threading.Event()  # set on the main thread, read by the worker

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()


class _WorkerSignals(QObject):
    """Lives on the main thread. Signals emitted from the worker are queued to it."""

    progress = Signal(object, int)  # (_ScanJob, files checked)
    done = Signal(object, object)  # (_ScanJob, ScanSummary | None if it failed)


class _ScanTask(QRunnable):
    def __init__(self, scanner: LibraryScanner, job: _ScanJob, signals: _WorkerSignals) -> None:
        super().__init__()
        self._scanner = scanner
        self._job = job
        self._signals = signals
        self._last_progress = 0.0

    def run(self) -> None:  # executes on the worker thread
        QThread.currentThread().setPriority(QThread.Priority.LowPriority)
        summary: ScanSummary | None = None
        try:
            summary = self._scanner.scan(lambda: self._job.cancelled, self._progress)
        except Exception:
            log.exception("Library scan failed")
        finally:
            self._signals.done.emit(self._job, summary)

    def _progress(self, checked: int) -> None:
        now = time.monotonic()
        if now - self._last_progress >= PROGRESS_INTERVAL_S:
            self._last_progress = now
            self._signals.progress.emit(self._job, checked)


class BackgroundLibraryScanner(QObject):
    """Adds and removes the library's folders and files, and rescans in the background."""

    #: How many files the running scan has checked so far (main thread, throttled).
    progress = Signal(int)
    #: A scan finished without being cancelled (main thread).
    finished = Signal(object)  # ScanSummary

    def __init__(
        self,
        catalog: Catalog,
        scanner: LibraryScanner,
        bus: EventBus,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._scanner = scanner
        self._bus = bus
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _WorkerSignals(self)
        self._signals.progress.connect(self._on_progress)
        self._signals.done.connect(self._on_done)
        self._jobs: list[_ScanJob] = []
        self._delayed = QTimer(self)
        self._delayed.setSingleShot(True)
        self._delayed.timeout.connect(self.scan)

    @property
    def busy(self) -> bool:
        return any(not job.cancelled for job in self._jobs)

    def add_sources(self, paths: Iterable[Path]) -> int:
        """Add folders and files to the library, then scan; returns how many were new."""
        added = sum(self.catalog.add_source(path) for path in paths)
        if added:
            self.scan()
        return added

    def remove_source(self, path: Path) -> None:
        """Take a folder or file out of the library (its files are left alone)."""
        self.catalog.remove_source(path)
        self._bus.publish(LibraryChanged())
        if self.busy:
            self.scan()  # the running scan may still add tracks from it

    def scan(self) -> None:
        """Bring the library up to date, cancelling a scan that's already running."""
        self._delayed.stop()
        for job in self._jobs:
            job.cancel()
        job = _ScanJob()
        self._jobs.append(job)
        self._pool.start(_ScanTask(self._scanner, job, self._signals))

    def scan_later(self, delay_ms: int) -> None:
        """Scan after ``delay_ms``, e.g. once PyAmp has started and the music is playing."""
        self._delayed.start(delay_ms)

    def shutdown(self) -> None:
        """Stop scanning: what was read so far is kept, the rest is read next time."""
        self._delayed.stop()
        for job in self._jobs:
            job.cancel()
        self._pool.clear()  # drop scans that never started
        if not self._pool.waitForDone(SHUTDOWN_TIMEOUT_MS):
            log.warning("Library scan did not stop within %d ms", SHUTDOWN_TIMEOUT_MS)

    # -- main-thread slots -------------------------------------------------- #

    def _on_progress(self, job: _ScanJob, checked: int) -> None:
        if not job.cancelled:
            self.progress.emit(checked)

    def _on_done(self, job: _ScanJob, summary: ScanSummary | None) -> None:
        if job in self._jobs:
            self._jobs.remove(job)
        if summary is None or job.cancelled:
            return
        if summary.added or summary.updated or summary.removed:
            self._bus.publish(LibraryChanged())
        self.finished.emit(summary)
