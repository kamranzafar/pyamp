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

"""Loads tracks on a worker thread so the UI never freezes.

Threading contract:

* The worker thread only runs :meth:`TrackLoader.iter_load` (file system and
  tag reading). It never touches the playlist, the event bus or any widget.
* Results come back through Qt signals. Because the receiving ``QObject``
  lives on the main thread, Qt queues them, so every slot below runs on the
  main thread and may freely mutate the playlist.
* Tracks are delivered in batches. Large folders show up progressively, and
  the per-signal overhead (and playlist re-layout) stays low.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal

from pyamp.core.models import Location, Track
from pyamp.library.loader import TrackLoader

log = logging.getLogger(__name__)

BATCH_SIZE = 50
BATCH_INTERVAL_S = 0.15  # flush at least this often, so the first tracks appear quickly
SHUTDOWN_TIMEOUT_MS = 3000


class LoadJob:
    """Handle for one load request. Lets the requester track or cancel it."""

    def __init__(self, paths: Iterable[Location]) -> None:
        self.paths = tuple(paths)
        self.loaded = 0  # only touched on the main thread
        self.done = False  # True once the worker has fully finished (main thread)
        self._cancelled = threading.Event()  # read by the worker, set by the main thread

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()

    def __repr__(self) -> str:
        return f"<LoadJob paths={len(self.paths)} loaded={self.loaded} cancelled={self.cancelled}>"


class _WorkerSignals(QObject):
    """Lives on the main thread. Signals emitted from the worker are queued to it."""

    batch = Signal(object, list)  # (LoadJob, list[Track])
    done = Signal(object)  # LoadJob


class _LoadTask(QRunnable):
    def __init__(self, loader: TrackLoader, job: LoadJob, signals: _WorkerSignals) -> None:
        super().__init__()
        self._loader = loader
        self._job = job
        self._signals = signals

    def run(self) -> None:  # executes on the worker thread
        # Reading tags must never compete with playback or the window.
        QThread.currentThread().setPriority(QThread.Priority.LowPriority)
        try:
            self._load()
        except Exception:
            log.exception("Background load failed for %r", self._job)
        finally:
            self._signals.done.emit(self._job)

    def _load(self) -> None:
        batch: list[Track] = []
        last_flush = time.monotonic()
        for track in self._loader.iter_load(self._job.paths):
            if self._job.cancelled:
                return
            batch.append(track)
            if len(batch) >= BATCH_SIZE or time.monotonic() - last_flush >= BATCH_INTERVAL_S:
                self._signals.batch.emit(self._job, batch)
                batch = []
                last_flush = time.monotonic()
        if batch and not self._job.cancelled:
            self._signals.batch.emit(self._job, batch)


class BackgroundTrackLoader(QObject):
    """Asynchronous front-end to :class:`TrackLoader`.

    Uses a single worker thread on purpose. Jobs run in FIFO order, so if the
    user adds folder A and then folder B, A's tracks always come first. Tag
    reading is I/O-bound, so more threads would add ordering complexity for
    little gain.
    """

    #: Emitted on the main thread for each batch of a job that is still live.
    tracks_loaded = Signal(object, list)  # (LoadJob, list[Track])
    #: Emitted on the main thread when a job that was not cancelled completes.
    job_finished = Signal(object)  # LoadJob

    def __init__(self, loader: TrackLoader, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loader = loader
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._signals = _WorkerSignals(self)
        self._signals.batch.connect(self._on_batch)
        self._signals.done.connect(self._on_done)
        self._jobs: list[LoadJob] = []

    @property
    def busy(self) -> bool:
        return any(not job.cancelled for job in self._jobs)

    def submit(self, paths: Iterable[Location]) -> LoadJob:
        job = LoadJob(paths)
        self._jobs.append(job)
        self._pool.start(_LoadTask(self._loader, job, self._signals))
        return job

    def cancel_all(self) -> None:
        for job in self._jobs:
            job.cancel()

    def shutdown(self) -> None:
        """Cancel everything and wait briefly for the worker to notice."""
        self.cancel_all()
        self._pool.clear()  # drop jobs that never started
        if not self._pool.waitForDone(SHUTDOWN_TIMEOUT_MS):
            log.warning("Background loader did not stop within %d ms", SHUTDOWN_TIMEOUT_MS)

    # -- main-thread slots -------------------------------------------------- #

    def _on_batch(self, job: LoadJob, tracks: list[Track]) -> None:
        # A batch may already be queued when the job is cancelled. Drop it here
        # so a "clear playlist" is never followed by stale tracks reappearing.
        if job.cancelled:
            return
        job.loaded += len(tracks)
        self.tracks_loaded.emit(job, tracks)

    def _on_done(self, job: LoadJob) -> None:
        job.done = True
        if job in self._jobs:
            self._jobs.remove(job)
        if not job.cancelled:
            self.job_finished.emit(job)
