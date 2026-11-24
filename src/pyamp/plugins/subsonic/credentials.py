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
"""Which Subsonic server to use, and its password (kept in the system keychain)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring
import platformdirs
from keyring.backends.fail import Keyring as FailKeyring

from pyamp import APP_NAME

log = logging.getLogger(__name__)

#: The keychain entry's service name; its account is ``user@server``.
KEYRING_SERVICE = f"{APP_NAME} Subsonic"


@dataclass(frozen=True, slots=True)
class Account:
    server: str
    username: str

    @property
    def key(self) -> str:
        return f"{self.username}@{self.server}"


class AccountStore:
    """The server and username in ``subsonic.json`` (no secrets), the password in the
    system keychain: macOS Keychain, Windows Credential Manager or Secret Service."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(platformdirs.user_config_dir(APP_NAME.lower()))

    @property
    def file(self) -> Path:
        return self.directory / "subsonic.json"

    def load(self) -> Account | None:
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            return Account(server=str(raw["server"]), username=str(raw["username"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("Ignoring unreadable Subsonic settings %s: %s", self.file, exc)
            return None

    def save(self, account: Account) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(account), indent=2), encoding="utf-8")
        tmp.replace(self.file)

    def password(self, account: Account) -> str | None:
        try:
            return keyring.get_password(KEYRING_SERVICE, account.key)
        except Exception as exc:  # no keychain (e.g. a Linux box without Secret Service)
            log.info("Could not read the Subsonic password from the keychain: %s", exc)
            return None

    def save_password(self, account: Account, password: str) -> bool:
        """Remember the password; False if there's no usable keychain."""
        try:
            keyring.set_password(KEYRING_SERVICE, account.key, password)
        except Exception as exc:
            log.warning("Could not save the Subsonic password in the keychain: %s", exc)
            return False
        return True

    def forget_password(self, account: Account) -> None:
        try:
            keyring.delete_password(KEYRING_SERVICE, account.key)
        except Exception as exc:
            log.info("No Subsonic password to remove from the keychain: %s", exc)

    @staticmethod
    def has_keychain() -> bool:
        """Whether there's a keychain to keep the password in."""
        try:
            return not isinstance(keyring.get_keyring(), FailKeyring)
        except Exception:
            return False
