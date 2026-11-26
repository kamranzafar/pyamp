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
"""Which Subsonic servers to use, and their passwords (kept in the system keychain)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

import keyring
import platformdirs
from keyring.backends.fail import Keyring as FailKeyring

from pyamp import APP_NAME, files

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
    """The servers and usernames in ``subsonic.json`` (no secrets), in the order they were
    added; the passwords in the system keychain: macOS Keychain, Windows Credential
    Manager or Secret Service.

    The file is ``{"servers": [{"server", "username", "remember_password"}, ...]}``. One
    written by an earlier PyAmp, with a single server's fields at the top, is read as a
    list of that one server.
    """

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or Path(platformdirs.user_config_dir(APP_NAME.lower()))

    @property
    def file(self) -> Path:
        return self.directory / "subsonic.json"

    def accounts(self) -> list[Account]:
        return [account for account, _ in self._entries()]

    def remembers_password(self, account: Account) -> bool:
        """Whether the user chose to keep the account's password in the keychain (the
        default, also for an account not added yet)."""
        return next((r for a, r in self._entries() if a == account), True)

    def put(
        self,
        account: Account,
        *,
        remember_password: bool = True,
        replacing: Account | None = None,
    ) -> None:
        """Add ``account``, or update it where it (or ``replacing``, an account it was
        edited from) is, keeping the order."""
        entries = self._entries()
        replaced = (account, replacing)
        position = next((i for i, (a, _) in enumerate(entries) if a in replaced), len(entries))
        entries = [entry for entry in entries if entry[0] not in replaced]
        entries.insert(position, (account, remember_password))
        self._write(entries)

    def remove(self, account: Account) -> None:
        """Forget the account (its keychain password is left to the caller)."""
        self._write([entry for entry in self._entries() if entry[0] != account])

    def _entries(self) -> list[tuple[Account, bool]]:
        raw = self._read()
        servers = raw.get("servers", [raw] if "server" in raw else [])
        if not isinstance(servers, list):
            log.warning("Ignoring unreadable Subsonic settings %s", self.file)
            return []
        entries: list[tuple[Account, bool]] = []
        for server in servers:
            try:
                account = Account(server=str(server["server"]), username=str(server["username"]))
            except (KeyError, TypeError):
                log.warning("Ignoring an unreadable server in %s", self.file)
                continue
            if account not in (a for a, _ in entries):
                entries.append((account, server.get("remember_password") is not False))
        return entries

    def _write(self, entries: list[tuple[Account, bool]]) -> None:
        servers = [{**asdict(a), "remember_password": remember} for a, remember in entries]
        files.write_text(self.file, json.dumps({"servers": servers}, indent=2))

    def _read(self) -> dict[str, object]:
        try:
            raw = json.loads(files.read_text(self.file))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable Subsonic settings %s: %s", self.file, exc)
            return {}
        if not isinstance(raw, dict):
            log.warning("Ignoring unreadable Subsonic settings %s", self.file)
            return {}
        return raw

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
