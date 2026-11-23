from __future__ import annotations

import pytest

from pyamp.audio import registry
from pyamp.audio.registry import (
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
    assert available_backends() == ["qt"]


def test_registered_backend_is_listed_and_created() -> None:
    register_backend("fake", FakeBackend)
    assert available_backends() == ["fake", "qt"]
    assert isinstance(create_backend("fake"), FakeBackend)


def test_registered_backend_overrides_builtin() -> None:
    register_backend("qt", FakeBackend)
    assert isinstance(create_backend("qt"), FakeBackend)


def test_builtin_backend_is_imported_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(registry._BUILTIN, "lazy", "tests.conftest:FakeBackend")
    assert isinstance(create_backend("lazy"), FakeBackend)


def test_unknown_backend_names_the_alternatives() -> None:
    with pytest.raises(UnknownBackendError, match=r"'nope'.*available: qt"):
        create_backend("nope")
