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
"""Which DLNA servers to leave out of the library, and which were added by address."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import platformdirs

from pyamp import APP_NAME

log = logging.getLogger(__name__)


class DlnaSettings:
    """``dlna.json`` in PyAmp's config folder: ``{"hidden": [udn, ...], "added":
    [description address, ...]}``. Servers are hidden by their unique device name,
    which stays the same when their address changes."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(platformdirs.user_config_dir(APP_NAME.lower()))

    @property
    def file(self) -> Path:
        return self.directory / "dlna.json"

    def hidden(self) -> list[str]:
        return self._list("hidden")

    def hide(self, udn: str) -> None:
        if udn not in (hidden := self.hidden()):
            self._write(hidden=[*hidden, udn])

    def show(self, udn: str) -> None:
        self._write(hidden=[h for h in self.hidden() if h != udn])

    def show_all(self) -> None:
        self._write(hidden=[])

    def added(self) -> list[str]:
        """The description addresses of servers added by hand, in the order added."""
        return self._list("added")

    def add(self, location: str) -> None:
        if location not in (added := self.added()):
            self._write(added=[*added, location])

    def remove(self, location: str) -> None:
        self._write(added=[a for a in self.added() if a != location])

    def _list(self, name: str) -> list[str]:
        values = self._read().get(name, [])
        if not isinstance(values, list):
            return []
        return [v for v in values if isinstance(v, str)]

    def _write(self, **changes: list[str]) -> None:
        payload = {"hidden": self.hidden(), "added": self.added(), **changes}
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def _read(self) -> dict[str, object]:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable DLNA settings %s: %s", self.file, exc)
            return {}
        if not isinstance(raw, dict):
            log.warning("Ignoring unreadable DLNA settings %s", self.file)
            return {}
        return raw
