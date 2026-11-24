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
"""Help → About PyAmp."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QTextBrowser

from pyamp import __version__
from pyamp.app import Application
from pyamp.audio import registry
from pyamp.config import Settings, SettingsStore
from pyamp.library.saved_playlists import SavedPlaylists
from pyamp.ui import about_dialog
from pyamp.ui.about_dialog import AboutDialog, read_document
from pyamp.ui.skin import DEFAULT_SKIN
from tests.conftest import FakeBackend


def test_licence_files_are_found_in_a_source_checkout() -> None:
    licence = read_document("LICENSE")
    assert licence is not None
    assert "GNU GENERAL PUBLIC LICENSE" in licence
    assert "Version 3" in licence
    terms = read_document("ADDITIONAL-TERMS.md")
    assert terms is not None and "section 7" in terms
    notices = read_document("THIRD_PARTY_NOTICES.md")
    assert notices is not None and "libmpv" in notices


def test_packaged_app_folder_comes_first(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text("bundled copy")
    assert read_document("LICENSE", [tmp_path]) == "bundled copy"


def test_dialog_shows_version_copyright_and_licence(qt_app: QCoreApplication) -> None:
    dialog = AboutDialog(DEFAULT_SKIN)
    tabs = [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert tabs == ["About", "License", "Additional Terms", "Third-Party Notices"]
    notice = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert f"Version {__version__}" in notice
    assert "Copyright © 2026 Kamran Zafar" in notice
    assert "GNU General Public License" in notice
    assert "WITHOUT ANY WARRANTY" in notice
    licence = dialog.tabs.widget(1)
    assert isinstance(licence, QTextBrowser)
    assert "GNU GENERAL PUBLIC LICENSE" in licence.toPlainText()
    terms = dialog.tabs.widget(2)
    assert isinstance(terms, QTextBrowser)
    assert "No misrepresentation of origin" in terms.toPlainText()  # rendered Markdown
    assert DEFAULT_SKIN.lcd.lower() in terms.toHtml().lower()  # links in the skin's colour
    dialog.close()


def test_about_tab_fits_without_scrolling(qt_app: QCoreApplication) -> None:
    dialog = AboutDialog(DEFAULT_SKIN)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    dialog.show()
    QCoreApplication.processEvents()
    viewport = dialog.about_scroll.viewport()
    needed = dialog.about_layout.totalHeightForWidth(viewport.width())
    assert needed <= viewport.height()
    assert not dialog.about_scroll.verticalScrollBar().isVisible()
    dialog.close()


def test_links_to_other_documents_switch_tabs(
    qt_app: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(
        QDesktopServices,
        "openUrl",
        staticmethod(lambda u: opened.append(u.toString())),
    )
    dialog = AboutDialog(DEFAULT_SKIN)
    dialog.open_link(QUrl("LICENSE"))  # as linked from ADDITIONAL-TERMS.md
    assert dialog.tabs.currentIndex() == 1
    dialog.open_link(QUrl("https://www.gnu.org/licenses/"))
    assert opened == ["https://www.gnu.org/licenses/"]
    assert dialog.tabs.currentIndex() == 1
    dialog.close()


def test_missing_files_link_to_the_text_online(
    qt_app: QCoreApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(about_dialog, "read_document", lambda name: None)
    dialog = AboutDialog(DEFAULT_SKIN)
    licence = dialog.tabs.widget(1)
    assert isinstance(licence, QTextBrowser)
    assert "gnu.org/licenses/gpl-3.0" in licence.toHtml()
    dialog.close()


@pytest.fixture
def app(
    qt_app: QCoreApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Application]:
    monkeypatch.setattr(registry, "_registered", {"fake": FakeBackend})
    monkeypatch.setattr(Settings, "backend", "fake")
    args = argparse.Namespace(
        paths=[], backend="fake", no_plugins=True, log_level="WARNING", libmpv=None
    )
    application = Application(
        args, SettingsStore(tmp_path / "config"), SavedPlaylists(tmp_path / "playlists")
    )
    yield application
    application.loader.shutdown()
    application.window._quitting = True
    application.window.close()


def test_help_menu_opens_one_about_dialog(app: Application) -> None:
    window = app.window
    window.about_action.trigger()
    first = window.show_about()
    assert first.isVisible()
    window.about_action.trigger()
    assert window.show_about() is first  # reused
    first.close()
