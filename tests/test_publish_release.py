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
"""packaging/publish_release.sh, run against a fake ``gh`` that records what it's asked."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "publish_release.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="needs bash"
)

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$GH_LOG"
if [ "$1 $2" = "release list" ]; then cat "$GH_EXISTING"; fi
"""


def publish(tmp_path: Path, *args: str, existing: int) -> tuple[int, list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    (tmp_path / "existing").write_text(str(existing))
    log = tmp_path / "gh.log"
    log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_EXISTING": str(tmp_path / "existing"),
    }
    result = subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, check=False)
    return result.returncode, log.read_text().splitlines()


def test_the_first_upload_creates_a_draft(tmp_path: Path) -> None:
    arm, intel = "PyAmp-1.2.0-macos-arm64.dmg", "PyAmp-1.2.0-macos-x86_64.dmg"
    code, calls = publish(tmp_path, "v1.2.0", f"dist/{arm}", f"dist/{intel}", existing=0)
    assert code == 0
    assert calls[1] == (
        "release create v1.2.0 --draft --verify-tag --title PyAmp 1.2.0 --generate-notes"
    )
    assert calls[2] == f"release upload v1.2.0 dist/{arm} dist/{intel} --clobber"


def test_later_uploads_add_to_the_existing_release(tmp_path: Path) -> None:
    setup = "PyAmp-1.2.0-windows-x86_64-setup.exe"
    code, calls = publish(tmp_path, "v1.2.0", setup, existing=1)
    assert code == 0
    assert not any(call.startswith("release create") for call in calls)
    assert calls[-1] == f"release upload v1.2.0 {setup} --clobber"


def test_refuses_files_of_another_version(tmp_path: Path) -> None:
    code, calls = publish(tmp_path, "v1.3.0", "PyAmp-1.2.0-macos-arm64.dmg", existing=0)
    assert code == 1
    assert calls == []  # nothing created or uploaded


def test_needs_a_tag_and_files(tmp_path: Path) -> None:
    code, calls = publish(tmp_path, "v1.2.0", existing=0)
    assert code == 2
    assert calls == []
