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
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QCoreApplication

from pyamp import app as app_module
from pyamp.selftest import run_self_test


def boom() -> str:
    raise RuntimeError("no audio for you")


def test_reports_each_check_and_overall_result(qt_app: QCoreApplication) -> None:
    lines: list[str] = []
    passed = run_self_test([("first", lambda: "fine"), ("second", boom)], out=lines.append)
    assert not passed
    assert lines == [
        "[ ok ] first: fine",
        "[FAIL] second: RuntimeError: no audio for you",
        "self-test FAILED",
    ]


def test_all_checks_passing(qt_app: QCoreApplication) -> None:
    lines: list[str] = []
    assert run_self_test([("only", lambda: "fine")], out=lines.append)
    assert lines[-1] == "self-test passed"


def test_main_exits_with_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    from pyamp import selftest  # noqa: PLC0415

    monkeypatch.setattr(selftest, "run_self_test", lambda: False)
    assert app_module.main(["--self-test"]) == 1
    monkeypatch.setattr(selftest, "run_self_test", lambda: True)
    assert app_module.main(["--self-test"]) == 0


@pytest.mark.skipif(
    sys.platform.startswith("linux"),
    reason="QAudioOutput() segfaults on the Ubuntu CI runner (PySide6 6.12), even with "
    "PulseAudio running; tracked separately",
)
def test_real_self_test_where_libmpv_is_available(qt_app: QCoreApplication) -> None:
    try:
        import pyamp.audio.mpv_backend  # noqa: F401, PLC0415
    except OSError as exc:
        pytest.skip(f"libmpv not available: {exc}")
    lines: list[str] = []
    assert run_self_test(out=lines.append), "\n".join(lines)
