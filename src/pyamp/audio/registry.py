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

"""Backend registry and factory.

Built-in backends are referenced by import path and imported lazily, so a
missing optional dependency (e.g. libmpv) only matters if that backend is
actually requested, and the core never imports Qt.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable

from pyamp.audio.backend import AudioBackend

type BackendFactory = Callable[[], AudioBackend]

_BUILTIN: dict[str, str] = {
    "qt": "pyamp.audio.qt_backend:QtAudioBackend",
    "mpv": "pyamp.audio.mpv_backend:MpvAudioBackend",
}
_registered: dict[str, BackendFactory] = {}


class UnknownBackendError(LookupError):
    pass


class BackendUnavailableError(RuntimeError):
    """The backend exists but can't run here, e.g. a system library is missing."""

    def __init__(self, name: str, reason: str) -> None:
        super().__init__(f"audio backend {name!r} is unavailable: {reason}")
        self.backend = name
        self.reason = reason


def register_backend(name: str, factory: BackendFactory) -> None:
    """Make a backend available under ``name``. Plugins may use this."""
    _registered[name] = factory


def available_backends() -> list[str]:
    return sorted(_BUILTIN.keys() | _registered.keys())


def create_backend(name: str) -> AudioBackend:
    if name in _registered:
        return _registered[name]()
    if name in _BUILTIN:
        module_name, _, class_name = _BUILTIN[name].partition(":")
        try:
            module = importlib.import_module(module_name)
        except (ImportError, OSError) as exc:  # e.g. python-mpv without libmpv
            raise BackendUnavailableError(name, str(exc)) from exc
        backend_cls: type[AudioBackend] = getattr(module, class_name)
        return backend_cls()
    raise UnknownBackendError(
        f"unknown audio backend {name!r}; available: {', '.join(available_backends())}"
    )
