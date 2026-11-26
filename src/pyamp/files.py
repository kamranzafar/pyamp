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

"""Reading and writing PyAmp's own files (settings, playlists, stations, ...) safely.

Each is written to a temporary file and renamed over the old one, so a crash never
leaves it half written. These files are read on worker threads (e.g. the library
listing smart playlists) while the main thread saves them. Windows won't open a file
while it's being replaced, nor replace one another thread has open, and an antivirus
or indexer may hold one briefly. Every operation here retries for a moment while
Windows says the file is in use. macOS and Linux never refuse, so there it runs once.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path

#: How long to keep retrying a file operation that Windows refuses because the file is
#: in use elsewhere: the reads and writes that hold it take milliseconds.
LOCK_RETRY_S = 1.0
_LOCK_RETRY_STEP_S = 0.05


def retry_while_locked[T](
    operation: Callable[[], T],
    *,
    platform: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Run a file operation, retrying briefly while Windows reports the file in use.

    Windows won't delete or replace a file another thread has open, nor open one that's
    being replaced; macOS and Linux will. ``platform`` defaults to this one.
    """
    on_windows = (platform or sys.platform) == "win32"
    attempts = int(LOCK_RETRY_S / _LOCK_RETRY_STEP_S) if on_windows else 1
    for attempt in range(attempts):
        try:
            return operation()
        except PermissionError:
            if attempt == attempts - 1:
                raise
            sleep(_LOCK_RETRY_STEP_S)
    raise AssertionError("unreachable")


def read_text(path: Path) -> str:
    """The file's text (UTF-8). Raises ``OSError`` as reading does, e.g. if it's missing."""
    return retry_while_locked(lambda: path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    """Replace the file's text (UTF-8) in one step, making its folder if need be."""
    write_with(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))


def write_with(path: Path, write: Callable[[Path], object]) -> None:
    """Replace ``path`` in one step with what ``write`` puts in the temporary file it's
    given (for writers that take a path, e.g. a playlist format)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    write(tmp)
    retry_while_locked(lambda: tmp.replace(path))
