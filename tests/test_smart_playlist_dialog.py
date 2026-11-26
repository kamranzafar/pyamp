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

"""The smart playlist dialog, and the commands that open it, copy and delete playlists."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from pyamp.core.events import EventBus, SmartPlaylistsChanged
from pyamp.core.models import Track
from pyamp.library.catalog import Catalog, FileStamp
from pyamp.library.saved_playlists import PlaylistNameError
from pyamp.library.smart_playlists import (
    Operator,
    Rule,
    RuleField,
    SmartPlaylist,
    SmartPlaylists,
    SortOrder,
)
from pyamp.ui.smart_playlist_commands import SmartPlaylistCommands
from pyamp.ui.smart_playlist_dialog import SmartPlaylistDialog
from tests.test_background_loader import wait_until

NINETIES = SmartPlaylist(
    "90s",
    (Rule(RuleField.YEAR, Operator.BETWEEN, 1990, 1999),),
    match_all=False,
    sort=SortOrder.RANDOM,
    limit=20,
)


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[Catalog]:
    opened = Catalog(tmp_path / "library.sqlite3")
    opened.add_source(tmp_path / "music")
    opened.put(
        [
            (Track(tmp_path / "music/a.mp3", "Teardrop", year=1998), FileStamp(1, 2)),
            (Track(tmp_path / "music/b.flac", "So What", year=1959), FileStamp(1, 2)),
        ]
    )
    yield opened
    opened.close()


@pytest.fixture
def make_dialog(
    qt_app: QCoreApplication, catalog: Catalog
) -> Iterator[Callable[..., SmartPlaylistDialog]]:
    dialogs: list[SmartPlaylistDialog] = []

    def make(
        playlist: SmartPlaylist | None = None,
        save: Callable[[SmartPlaylist], object] = lambda _: None,
    ) -> SmartPlaylistDialog:
        dialog = SmartPlaylistDialog(
            playlist, save=save, count=catalog.smart_count, folders=["/music"]
        )
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.reject()


def test_a_new_one_starts_with_a_rule_to_fill_in(
    make_dialog: Callable[..., SmartPlaylistDialog],
) -> None:
    dialog = make_dialog()
    assert dialog.windowTitle() == "New Smart Playlist"
    assert [row.rule() for row in dialog.rows] == [Rule(RuleField.GENRE, Operator.CONTAINS, "")]
    assert not dialog.ok_button.isEnabled()
    assert "needs a name" in dialog.status.text()
    dialog.name.setText("Jazz")
    assert "Rule 1: needs something to match" in dialog.status.text()
    dialog.rows[0].text.setText("jazz")
    assert dialog.ok_button.isEnabled()
    assert dialog.playlist() == SmartPlaylist(
        "Jazz", (Rule(RuleField.GENRE, Operator.CONTAINS, "jazz"),)
    )


def test_editing_fills_in_the_playlist(make_dialog: Callable[..., SmartPlaylistDialog]) -> None:
    dialog = make_dialog(NINETIES)
    assert dialog.windowTitle() == "Edit Smart Playlist"
    assert dialog.playlist() == NINETIES
    row = dialog.rows[0]
    assert row.number.isVisibleTo(dialog) and row.to.isVisibleTo(dialog)
    assert not row.text.isVisibleTo(dialog)


def test_the_value_editor_follows_the_field(
    make_dialog: Callable[..., SmartPlaylistDialog],
) -> None:
    dialog = make_dialog(SmartPlaylist("x"))
    row = dialog.add_rule()
    row.field.setCurrentIndex(row.field.findData(RuleField.YEAR))
    assert row.operator.itemData(row.operator.count() - 1) == Operator.BETWEEN
    assert row.number.isVisibleTo(dialog) and not row.to.isVisibleTo(dialog)
    assert row.rule() == Rule(RuleField.YEAR, Operator.IS, 2000)
    row.operator.setCurrentIndex(row.operator.findData(Operator.BETWEEN))
    assert row.to.isVisibleTo(dialog)
    assert row.rule() == Rule(RuleField.YEAR, Operator.BETWEEN, 2000, 2000)

    row.field.setCurrentIndex(row.field.findData(RuleField.FILE_TYPE))
    assert row.choice.isVisibleTo(dialog) and not row.number.isVisibleTo(dialog)
    assert row.rule() == Rule(RuleField.FILE_TYPE, Operator.IS, "flac")
    row.field.setCurrentIndex(row.field.findData(RuleField.FOLDER))
    assert row.rule() == Rule(RuleField.FOLDER, Operator.IN_FOLDER, "/music")
    row.field.setCurrentIndex(row.field.findData(RuleField.ADDED))
    assert row.rule() == Rule(RuleField.ADDED, Operator.IN_LAST, 30)
    assert row.number.minimum() == 1


def test_rules_can_be_added_and_removed(
    make_dialog: Callable[..., SmartPlaylistDialog],
) -> None:
    dialog = make_dialog(NINETIES)
    dialog.add_rule_button.click()
    assert len(dialog.rows) == 2
    dialog.rows[0].remove.click()
    assert [row.rule() for row in dialog.rows] == [Rule(RuleField.ARTIST, Operator.CONTAINS, "")]
    dialog.rows[0].remove.click()
    assert dialog.rows == []
    assert dialog.ok_button.isEnabled()  # no rules: the whole library


def test_it_counts_what_the_rules_match(
    make_dialog: Callable[..., SmartPlaylistDialog],
) -> None:
    dialog = make_dialog(SmartPlaylist("All"))
    wait_until(lambda: dialog.status.text() == "2 tracks match (no rules: every track)")
    dialog.add_rule(Rule(RuleField.YEAR, Operator.AT_LEAST, 1990))
    wait_until(lambda: dialog.status.text() == "1 track matches")


def test_the_limit_and_sort(make_dialog: Callable[..., SmartPlaylistDialog]) -> None:
    dialog = make_dialog(SmartPlaylist("All"))
    assert not dialog.limit.isEnabled()
    dialog.limited.setChecked(True)
    dialog.limit.setValue(7)
    dialog.sort.setCurrentIndex(dialog.sort.findData(SortOrder.NEWEST))
    assert dialog.playlist() == SmartPlaylist("All", limit=7, sort=SortOrder.NEWEST)


def test_ok_saves_and_a_refusal_keeps_it_open(
    make_dialog: Callable[..., SmartPlaylistDialog],
) -> None:
    saved: list[SmartPlaylist] = []

    def save(playlist: SmartPlaylist) -> None:
        if playlist.name == "Taken":
            raise PlaylistNameError('There is already a smart playlist called "Taken".')
        saved.append(playlist)

    dialog = make_dialog(SmartPlaylist("Taken"), save)
    dialog.open()
    dialog.ok_button.click()
    assert dialog.isVisible()
    assert "already" in dialog.status.text()
    dialog.name.setText("Fresh")
    dialog.ok_button.click()
    assert not dialog.isVisible()
    assert saved == [SmartPlaylist("Fresh")]


# -- Commands ----------------------------------------------------------------------- #


@pytest.fixture
def store(tmp_path: Path) -> SmartPlaylists:
    return SmartPlaylists(tmp_path / "smart.json")


@pytest.fixture
def changes() -> list[SmartPlaylistsChanged]:
    return []


@pytest.fixture
def commands(
    qt_app: QCoreApplication,
    store: SmartPlaylists,
    catalog: Catalog,
    changes: list[SmartPlaylistsChanged],
) -> Iterator[SmartPlaylistCommands]:
    bus = EventBus()
    bus.subscribe(SmartPlaylistsChanged, changes.append)
    made = SmartPlaylistCommands(store, catalog, bus, parent=lambda: None)
    yield made
    if made.dialog is not None:
        made.dialog.reject()


def test_new_saves_and_announces(
    commands: SmartPlaylistCommands, store: SmartPlaylists, changes: list[object]
) -> None:
    dialog = commands.new()
    assert commands.dialog is dialog
    dialog.name.setText("Old")
    dialog.rows[0].remove.click()
    dialog.ok_button.click()
    assert store.names() == ["Old"]
    assert len(changes) == 1
    QApplication.processEvents()
    assert commands.dialog is None


def test_edit_replaces_it(
    commands: SmartPlaylistCommands, store: SmartPlaylists, changes: list[object]
) -> None:
    store.save(NINETIES)
    dialog = commands.edit("90S")
    assert dialog is not None and dialog.playlist() == NINETIES
    dialog.name.setText("Nineties")
    dialog.ok_button.click()
    assert store.names() == ["Nineties"]
    assert commands.edit("gone") is None


def test_one_dialog_at_a_time(commands: SmartPlaylistCommands) -> None:
    first = commands.new()
    second = commands.new()
    assert not first.isVisible()
    assert commands.dialog is second


def test_duplicate(
    commands: SmartPlaylistCommands, store: SmartPlaylists, changes: list[object]
) -> None:
    store.save(NINETIES)
    commands.duplicate("90s")
    commands.duplicate("90s")
    commands.duplicate("gone")
    assert store.names() == ["90s", "90s copy", "90s copy 2"]
    assert store.get("90s copy 2") == SmartPlaylist(
        "90s copy 2", NINETIES.rules, NINETIES.match_all, NINETIES.sort, NINETIES.limit
    )
    assert len(changes) == 2


@pytest.mark.parametrize("answer", [QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No])
def test_delete_asks_first(
    commands: SmartPlaylistCommands,
    store: SmartPlaylists,
    changes: list[object],
    monkeypatch: pytest.MonkeyPatch,
    answer: QMessageBox.StandardButton,
) -> None:
    store.save(NINETIES)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: answer)
    commands.delete("90s")
    deleted = answer == QMessageBox.StandardButton.Yes
    assert store.names() == ([] if deleted else ["90s"])
    assert len(changes) == int(deleted)
