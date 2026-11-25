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
"""Runs slow calls (e.g. to a server) on worker threads, and hands results to the main thread.

The result, or the exception raised, comes back through a queued Qt signal, so the
``done`` callback always runs on the main thread and may touch widgets.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

SHUTDOWN_TIMEOUT_MS = 3000

type Done[T] = Callable[[T | None, BaseException | None], None]


class _Task(QRunnable):
    def __init__(self, call: Callable[[], Any], done: Done[Any], signals: BackgroundCalls) -> None:
        super().__init__()
        self._call, self._done, self._signals = call, done, signals

    def run(self) -> None:  # on the worker thread
        try:
            result = self._call()
        except Exception as exc:
            self._signals.finished.emit(self._done, None, exc)
        else:
            self._signals.finished.emit(self._done, result, None)


class BackgroundCalls(QObject):
    """Calls ``call`` on a worker thread, then ``done(result, error)`` on the main thread."""

    finished = Signal(object, object, object)  # (done, result, error)

    def __init__(self, parent: QObject | None = None, *, threads: int = 2) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(threads)
        self.finished.connect(self._deliver)

    def run[T](self, call: Callable[[], T], done: Done[T]) -> None:
        self._pool.start(_Task(call, done, self))

    def _deliver(self, done: Done[Any], result: Any, error: Any) -> None:
        done(result, error)  # a method slot, so it runs on this object's (the main) thread

    @property
    def busy(self) -> bool:
        """Whether a call is running or waiting to run."""
        return self._pool.activeThreadCount() > 0

    def drop_waiting(self) -> None:
        """Forget calls that haven't started; those running finish (and still report)."""
        self._pool.clear()

    def wait(self) -> bool:
        """Wait for calls in progress to finish; False if they didn't in time."""
        return self._pool.waitForDone(SHUTDOWN_TIMEOUT_MS)
