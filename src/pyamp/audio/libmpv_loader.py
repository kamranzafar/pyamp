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
"""Finding and loading libmpv, including from a bundled or user-supplied folder.

python-mpv looks libmpv up itself, via ``ctypes.util.find_library``. That only finds
system installs, and on macOS not even Homebrew's on Apple Silicon. Releases bundle
libmpv inside the app, and users without a package manager can drop a prebuilt one
into a folder, so PyAmp searches, in order:

1. ``PYAMP_LIBMPV``: a folder, or the library file itself.
2. **Bundled**: next to a frozen (PyInstaller) app, incl. ``.app/Contents/Frameworks``.
3. **User folder**: ``<user data dir>/pyamp/libmpv``.
4. **System**: python-mpv's own lookup, plus the Homebrew/MacPorts dirs on macOS.

A folder's other libraries (its ffmpeg, libass, ...) are loaded by absolute path
first, so libmpv binds to *those* rather than to different system versions.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import importlib
import logging
import os
import re
import sys
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import platformdirs

from pyamp import APP_NAME

log = logging.getLogger(__name__)

ENV_VAR = "PYAMP_LIBMPV"

#: Where Homebrew (Apple Silicon, Intel) and MacPorts install libraries.
MACOS_LIBRARY_DIRS = ("/opt/homebrew/lib", "/usr/local/lib", "/opt/local/lib")
#: dyld's own fallback when DYLD_FALLBACK_LIBRARY_PATH is unset (see ``man dyld``).
DYLD_DEFAULT_FALLBACK = ("~/lib", "/usr/local/lib", "/lib", "/usr/lib")
#: The DLL names python-mpv tries on Windows, in its order.
WINDOWS_NAMES = ("mpv-2.dll", "libmpv-2.dll", "mpv-1.dll")
#: Names python-mpv passes to ``find_library`` for libmpv.
_LIBMPV_LOOKUP_NAMES = frozenset({"mpv", *WINDOWS_NAMES})

#: A missing libass symbol, as reported by the dynamic loader.
_MISSING_LIBASS = re.compile(r"symbol not found.*\b_?ass_\w+", re.IGNORECASE | re.DOTALL)

type Load = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class LibmpvSource:
    """Which libmpv was loaded, and why that one."""

    path: Path | None  # None if python-mpv's own lookup picked it and didn't say
    tier: str  # "PYAMP_LIBMPV", "bundled", "user folder" or "system"

    def __str__(self) -> str:
        return f"{self.path or 'system default'} ({self.tier})"


# -- Where to look ------------------------------------------------------------- #


def user_folder() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME.lower())) / "libmpv"


def bundled_folders(*, frozen: bool, meipass: str | None, executable: str) -> list[Path]:
    """Folders a packaged app keeps its libraries in (none when running from source)."""
    if not frozen:
        return []
    exe_dir = Path(executable).resolve().parent
    folders = [Path(meipass)] if meipass else []
    # A macOS app keeps libraries in Contents/Frameworks, next to Contents/MacOS/<exe>.
    return [*folders, exe_dir, exe_dir.parent / "Frameworks"]


def candidate_folders(
    env: Mapping[str, str], *, bundled: Sequence[Path], user: Path
) -> list[tuple[Path, str]]:
    folders: list[tuple[Path, str]] = []
    if env.get(ENV_VAR):
        folders.append((Path(env[ENV_VAR]).expanduser(), ENV_VAR))
    folders += [(folder, "bundled") for folder in bundled]
    folders.append((user, "user folder"))
    return folders


def find_libmpv(location: Path, platform: str = sys.platform) -> Path | None:
    """The libmpv library at ``location`` (a file, or a folder containing it)."""
    if location.is_file():
        return location
    if not location.is_dir():
        return None
    if platform == "win32":
        return next((location / n for n in WINDOWS_NAMES if (location / n).is_file()), None)
    pattern = "libmpv*.dylib" if platform == "darwin" else "libmpv.so*"
    return next(iter(sorted(location.glob(pattern))), None)


def sibling_libraries(libmpv: Path, platform: str = sys.platform) -> list[Path]:
    """The other shared libraries next to ``libmpv`` (its bundled dependencies)."""

    def is_shared(p: Path) -> bool:
        if platform == "darwin":
            return p.suffix == ".dylib"
        if platform == "win32":
            return p.suffix.lower() == ".dll"
        return ".so" in p.suffixes

    mpv_names = {p.name for p in libmpv.parent.glob("libmpv*")} | set(WINDOWS_NAMES)
    return sorted(
        p
        for p in libmpv.parent.iterdir()
        if is_shared(p) and p.is_file() and p.name not in mpv_names
    )


# -- Loading --------------------------------------------------------------------- #


def _load_global(path: str) -> object:
    return ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)


def preload(libraries: Sequence[Path], load: Load = _load_global) -> None:
    """Load ``libraries`` by path, in whatever order satisfies their mutual dependencies.

    Each round loads everything it can; a library whose dependency isn't loaded yet is
    retried next round. Stops with an error once a round makes no progress.
    """
    pending = list(libraries)
    while pending:
        failed: list[Path] = []
        errors: dict[str, str] = {}
        for library in pending:
            try:
                load(str(library))
            except OSError as exc:
                failed.append(library)
                errors[library.name] = str(exc)
        if len(failed) == len(pending):
            details = "; ".join(f"{name}: {error}" for name, error in errors.items())
            raise OSError(f"could not load {sorted(errors)} ({details})")
        pending = failed


def needs_libass(error: BaseException) -> bool:
    """Whether loading failed because libass's symbols weren't provided."""
    return bool(_MISSING_LIBASS.search(str(error)))


def system_libass(find: Callable[[str], str | None] = ctypes.util.find_library) -> Path | None:
    found = find("ass")
    if found:
        return Path(found)
    for folder in MACOS_LIBRARY_DIRS:
        candidate = Path(folder) / "libass.dylib"
        if candidate.is_file():
            return candidate
    return None


@contextlib.contextmanager
def steer_find_library(libmpv: Path) -> Iterator[None]:
    """Make ``ctypes.util.find_library`` answer ``libmpv`` for python-mpv's lookups."""
    original = ctypes.util.find_library

    def find_library(name: str) -> str | None:
        return str(libmpv) if name in _LIBMPV_LOOKUP_NAMES else original(name)

    ctypes.util.find_library = find_library
    try:
        yield
    finally:
        ctypes.util.find_library = original


def _import_python_mpv() -> ModuleType:
    return importlib.import_module("mpv")


def import_from(
    libmpv: Path,
    *,
    platform: str = sys.platform,
    load: Load = _load_global,
    import_mpv: Callable[[], ModuleType] = _import_python_mpv,
    find_libass: Callable[[], Path | None] = system_libass,
) -> ModuleType:
    """Import python-mpv bound to ``libmpv`` and the libraries next to it."""
    if platform == "win32":
        os.add_dll_directory(str(libmpv.parent))  # type: ignore[attr-defined,unused-ignore]
    else:
        preload(sibling_libraries(libmpv, platform), load)
    try:
        with steer_find_library(libmpv):
            return import_mpv()
    except OSError as exc:
        if not needs_libass(exc):
            raise OSError(f"could not load {libmpv}: {exc}") from exc
        libass = find_libass()
        if libass is None:
            raise OSError(
                f"{libmpv} needs libass, which wasn't found. Install it "
                f"(e.g. 'brew install libass') or put it in {libmpv.parent}"
            ) from exc
        log.info("Loading %s for %s", libass, libmpv.name)
        load(str(libass))
        with steer_find_library(libmpv):
            return import_mpv()


def macos_library_path(env: Mapping[str, str], extra_dirs: tuple[str, ...]) -> str:
    """``DYLD_FALLBACK_LIBRARY_PATH`` with ``extra_dirs`` appended (keeping dyld's defaults)."""
    current = env.get("DYLD_FALLBACK_LIBRARY_PATH")
    dirs = current.split(":") if current else list(DYLD_DEFAULT_FALLBACK)
    dirs += [d for d in extra_dirs if d not in dirs]
    return ":".join(dirs)


def prepare_system_lookup(platform: str = sys.platform) -> None:
    """Let python-mpv's own lookup see Homebrew/MacPorts on macOS. No-op elsewhere.

    ``find_library`` on macOS only searches dyld's fallback path, which excludes
    ``/opt/homebrew/lib`` (Homebrew on Apple Silicon).
    """
    if platform != "darwin":
        return
    existing = tuple(d for d in MACOS_LIBRARY_DIRS if Path(d).is_dir())
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = macos_library_path(os.environ, existing)


def import_mpv(env: Mapping[str, str] = os.environ) -> tuple[ModuleType, LibmpvSource]:
    """Import python-mpv with the best available libmpv. Raises ``OSError`` if there is none."""
    bundled = bundled_folders(
        frozen=bool(getattr(sys, "frozen", False)),
        meipass=getattr(sys, "_MEIPASS", None),
        executable=sys.executable,
    )
    for location, tier in candidate_folders(env, bundled=bundled, user=user_folder()):
        libmpv = find_libmpv(location)
        if libmpv is None:
            if tier == ENV_VAR:
                raise OSError(f"{ENV_VAR}={location}: no libmpv found there")
            continue
        module = import_from(libmpv)
        return module, LibmpvSource(libmpv, tier)

    prepare_system_lookup()
    module = _import_python_mpv()
    backend = getattr(module, "backend", None)
    name = getattr(backend, "_name", None)
    return module, LibmpvSource(Path(name) if name else None, "system")
