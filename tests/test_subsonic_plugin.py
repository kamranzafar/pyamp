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
"""The Subsonic plugin in the real app (offscreen), against a pretend server."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.backends.fail import Keyring as FailKeyring
from PySide6.QtWidgets import QDialog, QMenu

from pyamp.app import Application
from pyamp.core.models import StreamUrl
from pyamp.plugins.subsonic import SubsonicPlugin
from pyamp.plugins.subsonic.credentials import KEYRING_SERVICE, Account, AccountStore
from pyamp.plugins.subsonic.source import ALBUM, ARTIST, ARTISTS, PLAYLIST, PLAYLISTS
from pyamp.plugins.subsonic.ui import ServerDialog
from tests.conftest import FakeBackend
from tests.subsonic_server import PASSWORD, USERNAME, SubsonicServer
from tests.test_background_loader import wait_until
from tests.test_library_window import click, item, labels, pick, settle, titles
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.passwords: dict[tuple[str, str], str] = {}
        self.reads = 0  # each can make the system ask for permission

    def get_password(self, service: str, username: str) -> str | None:
        self.reads += 1
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.passwords[service, username] = password

    def delete_password(self, service: str, username: str) -> None:
        self.passwords.pop((service, username), None)


@pytest.fixture
def keychain() -> Iterator[MemoryKeyring]:
    before = keyring.get_keyring()
    memory = MemoryKeyring()
    keyring.set_keyring(memory)
    yield memory
    keyring.set_keyring(before)


@pytest.fixture
def server() -> Iterator[SubsonicServer]:
    with SubsonicServer() as server:
        yield server


@pytest.fixture
def accounts(tmp_path: Path) -> AccountStore:
    return AccountStore(tmp_path / "subsonic")


def start(app: Application, accounts: AccountStore) -> SubsonicPlugin:  # noqa: F811
    plugin = SubsonicPlugin(accounts)
    app.plugins.register(plugin)
    assert plugin in app.plugins.active_plugins
    return plugin


def remember(accounts: AccountStore, keychain: MemoryKeyring, server: SubsonicServer) -> Account:
    account = Account(server.url, USERNAME)
    accounts.save(account)
    keychain.set_password(KEYRING_SERVICE, account.key, PASSWORD)
    return account


def answer_dialog(
    monkeypatch: pytest.MonkeyPatch, server: str, password: str, *, remember: bool = True
) -> list[ServerDialog]:
    """Make the server dialog fill itself in and press Connect instead of waiting."""
    shown: list[ServerDialog] = []

    def run(dialog: ServerDialog) -> int:
        shown.append(dialog)
        dialog.remember_at_first = dialog.remember.isChecked()  # type: ignore[attr-defined]
        dialog.server.setText(server)
        dialog.username.setText(USERNAME)
        dialog.password.setText(password)
        dialog.remember.setChecked(remember)
        dialog.connect_button.click()
        wait_until(dialog.connect_button.isEnabled)
        return dialog.result()

    monkeypatch.setattr(ServerDialog, "exec", run)
    return shown


def test_adds_its_menu_and_waits_to_be_set_up(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
) -> None:
    plugin = start(app, accounts)
    assert plugin.client is None
    menu = next(a.menu() for a in app.window.menuBar().actions() if a.text() == "&Library")
    assert isinstance(menu, QMenu)
    # After PyAmp's own library items; the server is browsed in the library window.
    assert [a.text() for a in menu.actions()][-1] == "Subsonic &Server…"
    assert "Subsonic" not in " ".join(a.text() for a in menu.actions()[:-1])
    assert plugin.source is None
    assert len(app.sources) == 1  # just this computer


def test_connects_and_remembers_the_password_in_the_keychain(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, server.url + "/", PASSWORD)
    assert plugin.show_server_dialog()
    assert plugin.client is not None
    assert accounts.load() == Account(server.url, USERNAME)
    assert PASSWORD not in accounts.file.read_text()
    assert keychain.passwords == {(KEYRING_SERVICE, f"{USERNAME}@{server.url}"): PASSWORD}


def test_a_wrong_password_keeps_the_dialog_open(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = start(app, accounts)
    shown = answer_dialog(monkeypatch, server.url, "wrong")
    assert not plugin.show_server_dialog()
    assert shown[0].status.text() == "Wrong username or password"
    assert shown[0].result() != QDialog.DialogCode.Accepted
    assert plugin.client is None
    assert accounts.load() is None
    assert keychain.passwords == {}


def test_not_remembering_the_password(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    assert plugin.show_server_dialog()
    assert keychain.passwords == {}  # and an earlier one is forgotten
    assert not accounts.remembers_password()


def test_not_remembering_is_remembered_and_keeps_out_of_the_keychain(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    accounts.save(Account(server.url, USERNAME), remember_password=False)
    plugin = start(app, accounts)  # as PyAmp starts
    assert keychain.reads == 0  # so the system doesn't ask for anything
    assert plugin.client is None  # the password is asked for again this session
    shown = answer_dialog(monkeypatch, server.url, PASSWORD, remember=False)
    assert plugin.show_server_dialog()
    assert shown[0].remember_at_first is False  # type: ignore[attr-defined]  # as left
    assert keychain.reads == 0
    assert keychain.passwords == {}


def test_remembering_is_the_default(
    accounts: AccountStore, server: SubsonicServer, tmp_path: Path
) -> None:
    assert accounts.remembers_password()  # nothing saved yet
    accounts.directory.mkdir(parents=True, exist_ok=True)
    accounts.file.write_text(f'{{"server": "{server.url}", "username": "{USERNAME}"}}')
    assert accounts.remembers_password()  # saved by an earlier PyAmp
    accounts.file.write_text("[1, 2]")
    assert accounts.load() is None
    assert accounts.remembers_password()


def test_without_a_keychain_the_password_lasts_the_session(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    try:
        plugin = start(app, accounts)
        shown = answer_dialog(monkeypatch, server.url, PASSWORD, remember=True)
        assert plugin.show_server_dialog()
        assert not shown[0].remember.isEnabled()
        assert plugin.client is not None
        assert accounts.password(Account(server.url, USERNAME)) is None
    finally:
        keyring.set_keyring(before)


def test_a_remembered_account_is_used_at_start_and_plays_with_a_login(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    tracks = plugin.client.album_tracks("al1")
    app.window.play_from_library(tracks, 0)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    played = backend.loaded[-1]
    assert isinstance(played, StreamUrl)
    assert played.url.startswith(tracks[0].location.url + "&u=alice&t=")  # type: ignore[union-attr]
    assert app.playlist.current_track == tracks[0]


def test_saved_playlists_hold_no_login(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    app.window.play_from_library(plugin.client.album_tracks("al1"), 0)
    app.playlists.save_now()
    assert app.playlists.active is not None
    text = app.playlists._saved.path(app.playlists.active).read_text()
    assert "/rest/stream?id=s1" in text
    assert "#EXTINF:330,Massive Attack - Teardrop" in text
    for secret in ("u=", "t=", "s=", PASSWORD):
        assert secret not in text


def test_the_server_is_a_source_in_the_library(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.source is not None
    assert list(app.sources) == [app.local_source, plugin.source]
    assert plugin.source.name == f"Subsonic — {server.url.removeprefix('http://')}"
    answer_dialog(monkeypatch, server.url, PASSWORD)
    assert plugin.show_server_dialog()  # connecting again replaces it
    assert list(app.sources) == [app.local_source, plugin.source]
    plugin.deactivate()
    assert list(app.sources) == [app.local_source]


def test_browse_an_album_in_the_library_and_play_it(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    start(app, accounts)
    window = app.window.show_library()
    settle(window)
    assert labels(window, 1) == ["Artists", "Playlists"]
    assert labels(window, 1, (ARTISTS, "")) == ["Massive Attack (1 album)"]
    assert labels(window, 1, (ARTISTS, ""), (ARTIST, "ar1")) == [
        "Mezzanine (Massive Attack · 1998)"
    ]
    pick(window, 1, (ARTISTS, ""), (ARTIST, "ar1"), (ALBUM, "al1"))
    assert titles(window) == ["Teardrop", "Angel"]  # album order: by track number
    assert window.model.data(window.model.index(0, 6)) == "5:30"

    window.play_from(1)
    assert app.playlists.active == "Library"
    assert [t.title for t in app.playlist] == ["Teardrop", "Angel"]
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    played = backend.loaded[-1]
    assert isinstance(played, StreamUrl)
    assert "&u=alice&t=" in played.url  # the plugin added a login to play it


def test_search_the_server_and_its_playlists(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    start(app, accounts)
    window = app.window.show_library()
    settle(window)
    pick(window, 1)
    assert titles(window) == []  # nothing to list until you search
    window.search.setText("tear")
    window.search.returnPressed.emit()
    settle(window)
    assert titles(window) == ["Teardrop"]
    window.search.setText("")
    pick(window, 1, (PLAYLISTS, ""), (PLAYLIST, "p1"))
    assert titles(window) == ["Angel"]


def test_server_errors_are_shown_in_the_library(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    plugin.client._password = "changed on the server"
    window = app.window.show_library()
    settle(window)
    item(window, 1, (ARTISTS, "")).setExpanded(True)
    settle(window)
    assert window.status.text() == "Wrong username or password"


def test_deactivate_stops_adding_logins(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    plugin = start(app, accounts)
    assert plugin.client is not None
    track = plugin.client.album_tracks("al1")[0]
    plugin.deactivate()
    app.playlist.add([track])
    app.player.play_index(0)
    backend = app.player._backend
    assert isinstance(backend, FakeBackend)
    assert backend.loaded[-1] == track.location


def test_clicking_an_artist_shows_its_albums_and_songs(
    app: Application,  # noqa: F811
    accounts: AccountStore,
    keychain: MemoryKeyring,
    server: SubsonicServer,
) -> None:
    remember(accounts, keychain, server)
    start(app, accounts)
    window = app.window.show_library()
    settle(window)
    artists = item(window, 1, (ARTISTS, ""))
    click(window, artists)
    artist = item(window, 1, (ARTISTS, ""), (ARTIST, "ar1"))
    click(window, artist)
    assert artist.isExpanded()
    assert artist.child(0) is not None
    assert artist.child(0).text(0) == "Mezzanine (Massive Attack · 1998)"  # type: ignore[union-attr]
    assert titles(window) == ["Teardrop", "Angel"]  # every album's songs
