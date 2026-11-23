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

"""Make libmpv findable on macOS before python-mpv is imported.

python-mpv locates libmpv with ``ctypes.util.find_library``, which on macOS only
searches dyld's fallback path. That excludes ``/opt/homebrew/lib``, so libmpv
installed by Homebrew on Apple Silicon would not be found.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

#: Where Homebrew (Apple Silicon, Intel) and MacPorts install libraries.
MACOS_LIBRARY_DIRS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")
#: dyld's own fallback when DYLD_FALLBACK_LIBRARY_PATH is unset (see ``man dyld``).
DYLD_DEFAULT_FALLBACK = ("~/lib", "/usr/local/lib", "/lib", "/usr/lib")


def macos_library_path(env: Mapping[str, str], extra_dirs: tuple[str, ...]) -> str:
    """``DYLD_FALLBACK_LIBRARY_PATH`` with ``extra_dirs`` appended (keeping dyld's defaults)."""
    current = env.get("DYLD_FALLBACK_LIBRARY_PATH")
    dirs = current.split(":") if current else list(DYLD_DEFAULT_FALLBACK)
    dirs += [d for d in extra_dirs if d not in dirs]
    return ":".join(dirs)


def prepare_libmpv_lookup() -> None:
    """Extend this process's library search path on macOS. No-op elsewhere."""
    if sys.platform != "darwin":
        return
    existing = tuple(d for d in MACOS_LIBRARY_DIRS if Path(d).is_dir())
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = macos_library_path(os.environ, existing)
