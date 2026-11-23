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
