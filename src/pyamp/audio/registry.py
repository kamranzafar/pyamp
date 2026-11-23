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
}
_registered: dict[str, BackendFactory] = {}


class UnknownBackendError(LookupError):
    pass


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
        backend_cls: type[AudioBackend] = getattr(importlib.import_module(module_name), class_name)
        return backend_cls()
    raise UnknownBackendError(
        f"unknown audio backend {name!r}; available: {', '.join(available_backends())}"
    )
