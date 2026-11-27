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

"""User settings and their persistence."""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs

from pyamp import APP_NAME, files

log = logging.getLogger(__name__)


@dataclass
class Settings:
    volume: float = 0.8
    shuffle: bool = False
    repeat: str = "off"
    replay_gain: str = "off"
    equalizer_enabled: bool = False
    equalizer_preamp: float = 0.0
    equalizer_gains: list[float] = field(default_factory=lambda: [0.0] * 10)
    backend: str = "mpv"
    #: The device to play through (its id as the backends list it), "" for the system
    #: default; and its name, to show while it's disconnected.
    output_device: str = ""
    output_device_name: str = ""
    #: Which libmpv the mpv backend prefers: "auto", "system" or "bundled".
    libmpv: str = "auto"
    #: The saved playlist that was open last.
    active_playlist: str = ""
    last_directory: str = ""
    show_remaining_time: bool = False
    #: How the visualiser draws: an id from ``pyamp.ui.visualiser.STYLES``, or "off".
    visualiser_style: str = "bars"
    #: Show PyAmp's icon in the menu bar (macOS) or system tray.
    show_in_menu_bar: bool = True
    #: Closing the window only hides it and the music keeps playing, until Quit (the
    #: usual behaviour for macOS apps; elsewhere it needs the tray icon to get back).
    keep_running_when_closed: bool = True


class SettingsStore:
    """Loads and saves :class:`Settings` as JSON in the platform config directory.

    Unknown keys are ignored and missing keys fall back to defaults, so the
    file format can evolve without migrations.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(platformdirs.user_config_dir(APP_NAME.lower()))

    @property
    def settings_file(self) -> Path:
        return self.directory / "settings.json"

    @property
    def session_playlist(self) -> Path:
        """Where the playlist is saved on exit and restored on start."""
        return self.directory / "session.m3u8"

    def load(self) -> Settings:
        try:
            raw: Any = json.loads(files.read_text(self.settings_file))
        except FileNotFoundError:
            return Settings()
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Ignoring unreadable settings file %s: %s", self.settings_file, exc)
            return Settings()
        if not isinstance(raw, dict):
            return Settings()
        known = {f.name for f in dataclasses.fields(Settings)}
        return Settings(**{k: v for k, v in raw.items() if k in known})

    def save(self, settings: Settings) -> None:
        files.write_text(self.settings_file, json.dumps(dataclasses.asdict(settings), indent=2))
