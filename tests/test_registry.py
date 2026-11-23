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

from __future__ import annotations

import pytest

from pyamp.audio import registry
from pyamp.audio.registry import (
    BackendUnavailableError,
    UnknownBackendError,
    available_backends,
    create_backend,
    register_backend,
)
from tests.conftest import FakeBackend


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "_registered", {})


def test_builtin_backends_are_listed() -> None:
    assert available_backends() == ["mpv", "qt"]


def test_registered_backend_is_listed_and_created() -> None:
    register_backend("fake", FakeBackend)
    assert available_backends() == ["fake", "mpv", "qt"]
    assert isinstance(create_backend("fake"), FakeBackend)


def test_registered_backend_overrides_builtin() -> None:
    register_backend("qt", FakeBackend)
    assert isinstance(create_backend("qt"), FakeBackend)


def test_builtin_backend_is_imported_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry._BUILTIN, "lazy", "tests.conftest:FakeBackend")
    assert isinstance(create_backend("lazy"), FakeBackend)


def test_unknown_backend_names_the_alternatives() -> None:
    with pytest.raises(UnknownBackendError, match=r"'nope'.*available: mpv, qt"):
        create_backend("nope")


def test_backend_with_missing_system_library_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_libmpv(name: str) -> None:
        raise OSError("Cannot find libmpv")

    monkeypatch.setattr("importlib.import_module", no_libmpv)
    with pytest.raises(BackendUnavailableError, match="'mpv' is unavailable: Cannot find libmpv"):
        create_backend("mpv")
