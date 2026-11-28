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

"""The Chords window's choices, kept between sessions: ``chords.json`` in PyAmp's config
folder (``{"show_bass": true}``)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import platformdirs

from pyamp import APP_NAME, files

log = logging.getLogger(__name__)


def default_directory() -> Path:
    return Path(platformdirs.user_config_dir(APP_NAME.lower()))


class ChordSettings:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or default_directory()

    @property
    def file(self) -> Path:
        return self.directory / "chords.json"

    @property
    def show_bass(self) -> bool:
        """Whether chords show their bass note (C/E); yes unless turned off."""
        value = self._read().get("show_bass", True)
        return value if isinstance(value, bool) else True

    def set_show_bass(self, show: bool) -> None:
        try:
            files.write_text(self.file, json.dumps({**self._read(), "show_bass": show}, indent=2))
        except OSError as exc:
            log.warning("Couldn't keep the Chords settings in %s: %s", self.file, exc)

    def _read(self) -> dict[str, object]:
        try:
            raw = json.loads(files.read_text(self.file))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable Chords settings %s: %s", self.file, exc)
            return {}
        return raw if isinstance(raw, dict) else {}
