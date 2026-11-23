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
"""The libmpv loader's logic, tested without loading (or needing) libmpv."""

from __future__ import annotations

import ctypes.util
import os
from pathlib import Path
from types import ModuleType

import pytest

from pyamp.audio import libmpv_loader as loader
from pyamp.audio.libmpv_loader import ENV_VAR


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


# -- Where to look ----------------------------------------------------------------- #


def test_env_var_comes_first_then_bundled_then_user(tmp_path: Path) -> None:
    bundled = [tmp_path / "app"]
    user = tmp_path / "user"
    folders = loader.candidate_folders({ENV_VAR: "~/mine"}, bundled=bundled, user=user)
    assert folders == [
        (Path("~/mine").expanduser(), ENV_VAR),
        (tmp_path / "app", "bundled"),
        (user, "user folder"),
    ]
    assert loader.candidate_folders({}, bundled=[], user=user) == [(user, "user folder")]


def test_bundled_folders_only_when_frozen(tmp_path: Path) -> None:
    exe = tmp_path / "PyAmp.app" / "Contents" / "MacOS" / "pyamp"
    assert loader.bundled_folders(frozen=False, meipass=None, executable=str(exe)) == []
    folders = loader.bundled_folders(frozen=True, meipass="/tmp/_MEI1", executable=str(exe))
    macos_dir = exe.resolve().parent
    frameworks = macos_dir.parent / "Frameworks"  # PyAmp.app/Contents/Frameworks
    assert folders == [
        Path("/tmp/_MEI1/libmpv"),  # each location's "libmpv" subfolder first
        Path("/tmp/_MEI1"),
        macos_dir / "libmpv",
        macos_dir,
        frameworks / "libmpv",
        frameworks,
    ]


def test_user_folder_is_in_pyamps_data_dir() -> None:
    folder = loader.user_folder()
    assert folder.name == "libmpv"
    assert folder.parent.name.lower() == "pyamp"


# -- Finding the library ----------------------------------------------------------- #


@pytest.mark.parametrize(
    ("platform", "files", "expected"),
    [
        ("darwin", ["libavcodec.dylib", "libmpv.2.dylib"], "libmpv.2.dylib"),
        ("linux", ["libass.so.9", "libmpv.so.2"], "libmpv.so.2"),
        ("win32", ["libmpv-2.dll", "mpv-2.dll"], "mpv-2.dll"),  # python-mpv's preference
        ("darwin", ["libavcodec.dylib"], None),
    ],
)
def test_find_libmpv_in_folder(
    tmp_path: Path, platform: str, files: list[str], expected: str | None
) -> None:
    for name in files:
        touch(tmp_path / name)
    found = loader.find_libmpv(tmp_path, platform)
    assert (found.name if found else None) == expected


def test_find_libmpv_accepts_the_file_itself_and_ignores_missing(tmp_path: Path) -> None:
    lib = touch(tmp_path / "custom-libmpv.dylib")
    assert loader.find_libmpv(lib, "darwin") == lib
    assert loader.find_libmpv(tmp_path / "nope", "darwin") is None


def test_sibling_libraries_exclude_libmpv_and_non_libraries(tmp_path: Path) -> None:
    lib = touch(tmp_path / "libmpv.dylib")
    for name in ("libmpv.2.dylib", "libavcodec.dylib", "libass.dylib", "README.txt"):
        touch(tmp_path / name)
    (tmp_path / "sub.dylib").mkdir()
    names = [p.name for p in loader.sibling_libraries(lib, "darwin")]
    assert names == ["libass.dylib", "libavcodec.dylib"]


# -- Preloading -------------------------------------------------------------------- #


class FakeDyld:
    """Loads a library only once all its (fake) dependencies are loaded."""

    def __init__(self, deps: dict[str, set[str]]) -> None:
        self.deps = deps
        self.loaded: list[str] = []

    def __call__(self, path: str) -> object:
        name = Path(path).name
        missing = self.deps.get(name, set()) - set(self.loaded)
        if missing:
            raise OSError(f"{name}: Library not loaded: {sorted(missing)}")
        self.loaded.append(name)
        return object()


def test_preload_resolves_dependency_order(tmp_path: Path) -> None:
    dyld = FakeDyld({"avfilter": {"avcodec", "avutil"}, "avcodec": {"avutil"}})
    loader.preload([tmp_path / n for n in ("avfilter", "avcodec", "avutil")], dyld)
    assert dyld.loaded == ["avutil", "avcodec", "avfilter"]


def test_preload_reports_libraries_that_never_load(tmp_path: Path) -> None:
    dyld = FakeDyld({"avcodec": {"missing"}})
    with pytest.raises(OSError, match=r"could not load \['avcodec'\]"):
        loader.preload([tmp_path / "avutil", tmp_path / "avcodec"], dyld)
    assert dyld.loaded == ["avutil"]


# -- Importing python-mpv ---------------------------------------------------------- #

LIBASS_ERROR = OSError(
    "dlopen(/x/libmpv.dylib, 0x0006): symbol not found in flat namespace '_ass_alloc_event'"
)


def test_needs_libass() -> None:
    assert loader.needs_libass(LIBASS_ERROR)
    assert not loader.needs_libass(OSError("Symbol not found: _av_chroma_location_enum_to_pos"))
    assert not loader.needs_libass(OSError("image not found"))


def test_import_retries_once_with_system_libass(tmp_path: Path) -> None:
    lib = touch(tmp_path / "libmpv.dylib")
    loaded: list[str] = []
    attempts: list[int] = []
    module = ModuleType("mpv")

    def import_mpv() -> ModuleType:
        attempts.append(1)
        if str(Path("/sys/libass.dylib")) not in loaded:
            raise LIBASS_ERROR
        return module

    result = loader.import_from(
        lib,
        platform="darwin",
        load=loaded.append,
        import_mpv=import_mpv,
        find_libass=lambda: Path("/sys/libass.dylib"),
    )
    assert result is module
    assert len(attempts) == 2
    assert loaded == [str(Path("/sys/libass.dylib"))]


def test_missing_libass_gives_a_helpful_error(tmp_path: Path) -> None:
    lib = touch(tmp_path / "libmpv.dylib")

    def import_mpv() -> ModuleType:
        raise LIBASS_ERROR

    with pytest.raises(OSError, match="needs libass") as info:
        loader.import_from(
            lib,
            platform="darwin",
            load=lambda p: None,
            import_mpv=import_mpv,
            find_libass=lambda: None,
        )
    assert str(tmp_path) in str(info.value)


def test_other_load_errors_are_not_retried(tmp_path: Path) -> None:
    lib = touch(tmp_path / "libmpv.dylib")
    attempts: list[int] = []

    def import_mpv() -> ModuleType:
        attempts.append(1)
        raise OSError("Symbol not found: _av_something")

    with pytest.raises(OSError, match=r"could not load .*libmpv\.dylib"):
        loader.import_from(
            lib,
            platform="darwin",
            load=lambda p: None,
            import_mpv=import_mpv,
            find_libass=lambda: None,
        )
    assert attempts == [1]


def test_find_library_is_steered_only_for_libmpv_and_restored(tmp_path: Path) -> None:
    original = ctypes.util.find_library
    lib = tmp_path / "libmpv.dylib"
    with loader.steer_find_library(lib):
        assert ctypes.util.find_library("mpv") == str(lib)
        assert ctypes.util.find_library("mpv-2.dll") == str(lib)
        assert ctypes.util.find_library("c") == original("c")
    assert ctypes.util.find_library is original

    with pytest.raises(RuntimeError), loader.steer_find_library(lib):
        raise RuntimeError("import failed")
    assert ctypes.util.find_library is original


def test_env_var_without_libmpv_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(OSError, match=f"{ENV_VAR}=.*no libmpv found"):
        loader.import_mpv({ENV_VAR: str(tmp_path)})


def test_missing_filters_unknown_without_a_usable_libavfilter(tmp_path: Path) -> None:
    assert loader.missing_filters(["volume"], None) == []
    assert loader.missing_filters(["volume"], tmp_path / "nope.dylib") == []


def test_find_avfilter_prefers_the_copy_next_to_libmpv(tmp_path: Path) -> None:
    lib = touch(tmp_path / "libmpv.dylib")
    avfilter = touch(tmp_path / "libavfilter.dylib")
    found = loader.find_avfilter(loader.LibmpvSource(lib, "bundled"), "darwin")
    assert found == avfilter


# -- System lookup (macOS) --------------------------------------------------------- #


def test_homebrew_dir_is_added_to_dyld_defaults() -> None:
    result = loader.macos_library_path({}, ("/opt/homebrew/lib",))
    assert result == "~/lib:/usr/local/lib:/lib:/usr/lib:/opt/homebrew/lib"


def test_existing_dyld_value_is_kept_and_not_duplicated() -> None:
    env = {"DYLD_FALLBACK_LIBRARY_PATH": "/custom/lib:/opt/homebrew/lib"}
    result = loader.macos_library_path(env, ("/opt/homebrew/lib", "/opt/local/lib"))
    assert result == "/custom/lib:/opt/homebrew/lib:/opt/local/lib"


def test_system_lookup_only_touches_the_environment_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DYLD_FALLBACK_LIBRARY_PATH", raising=False)
    loader.prepare_system_lookup("linux")
    assert "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ
    loader.prepare_system_lookup("darwin")
    assert os.environ["DYLD_FALLBACK_LIBRARY_PATH"].startswith("~/lib:/usr/local/lib")


# -- The real thing, when CI points PYAMP_LIBMPV at a folder ----------------------- #


@pytest.mark.skipif(ENV_VAR not in os.environ, reason=f"{ENV_VAR} not set")
def test_mpv_backend_uses_the_env_folder() -> None:
    from pyamp.audio import mpv_backend  # noqa: PLC0415  (loads libmpv)

    source = mpv_backend.LIBMPV_SOURCE
    assert source.tier == ENV_VAR
    assert source.path is not None
    assert source.path.parent == loader.find_libmpv(Path(os.environ[ENV_VAR])).parent  # type: ignore[union-attr]


# -- Preference: system vs PyAmp's own copy ---------------------------------------- #


@pytest.mark.parametrize(
    ("preference", "platform", "expected"),
    [
        ("auto", "linux", "system"),
        ("auto", "darwin", "bundled"),
        ("auto", "win32", "bundled"),
        ("system", "darwin", "system"),
        ("bundled", "linux", "bundled"),
    ],
)
def test_effective_preference(preference: str, platform: str, expected: str) -> None:
    assert loader.effective_preference(preference, platform) == expected


def test_invalid_preference_is_rejected() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        loader.set_preference("newest")


@pytest.fixture
def own_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A libmpv in PyAmp's user folder."""
    folder = tmp_path / "user" / "libmpv"
    lib = touch(folder / "libmpv.dylib")
    touch(folder / "libmpv.so.2")  # the same copy, as Linux names it
    monkeypatch.setattr(loader, "user_folder", lambda: folder)
    return lib


def choose(
    *,
    preference: str,
    platform: str = "darwin",
    system: Path | None = Path("/usr/lib/libmpv.dylib"),
    broken: set[str] = frozenset(),  # type: ignore[assignment]
) -> loader.LibmpvSource:
    def load(kind: str) -> object:
        def load_lib(path: Path) -> ModuleType:
            if kind in broken:
                raise OSError(f"{kind} copy is broken")
            return ModuleType("mpv")

        return load_lib

    _, source = loader.import_mpv(
        {},
        preference=preference,
        platform=platform,
        find_system=lambda: system,
        load_folder=load("own"),  # type: ignore[arg-type]
        load_system=load("system"),  # type: ignore[arg-type]
    )
    return source


def test_linux_prefers_the_system_libmpv(own_copy: Path) -> None:
    assert choose(preference="auto", platform="linux").tier == "system"


def test_macos_prefers_its_own_copy(own_copy: Path) -> None:
    source = choose(preference="auto", platform="darwin")
    assert (source.tier, source.path) == ("user folder", own_copy)


def test_explicit_preference_overrides_the_platform_default(own_copy: Path) -> None:
    assert choose(preference="system", platform="darwin").tier == "system"
    assert choose(preference="bundled", platform="linux").tier == "user folder"


def test_falls_back_when_the_preferred_one_is_missing(own_copy: Path) -> None:
    assert choose(preference="system", system=None).tier == "user folder"


def test_falls_back_when_the_preferred_one_fails_to_load(own_copy: Path) -> None:
    assert choose(preference="bundled", broken={"own"}).tier == "system"
    assert choose(preference="system", broken={"system"}).tier == "user folder"


def test_nothing_usable_lists_every_reason(own_copy: Path) -> None:
    with pytest.raises(OSError, match=r"no usable libmpv.*user folder.*broken.*system.*broken"):
        choose(preference="bundled", broken={"own", "system"})
