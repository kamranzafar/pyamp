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
"""Help → About PyAmp: version, copyright, licence and third-party notices."""

from __future__ import annotations

import platform
import sys
from collections.abc import Iterable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, QUrl, qVersion
from PySide6.QtGui import QColor, QDesktopServices, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import pyamp
from pyamp import APP_NAME, __version__
from pyamp.ui.icons import app_icon
from pyamp.ui.skin import Skin

COPYRIGHT = "Copyright © 2026 Kamran Zafar"
HOMEPAGE = "https://github.com/kamranzafar/pyamp"
GPL_URL = "https://www.gnu.org/licenses/gpl-3.0.html"

#: The files shown in the dialog: (tab title, file name, URL if the file isn't found).
DOCUMENTS = (
    ("License", "LICENSE", GPL_URL),
    ("Additional Terms", "ADDITIONAL-TERMS.md", f"{HOMEPAGE}/blob/main/ADDITIONAL-TERMS.md"),
    (
        "Third-Party Notices",
        "THIRD_PARTY_NOTICES.md",
        f"{HOMEPAGE}/blob/main/packaging/THIRD_PARTY_NOTICES.md",
    ),
)


def document_folders() -> list[Path]:
    """Where the licence files are: the packaged app, then a source checkout."""
    folders: list[Path] = []
    if meipass := getattr(sys, "_MEIPASS", None):
        folders.append(Path(meipass))  # PyInstaller: next to the app's other data
    repo = Path(pyamp.__file__).resolve().parents[2]  # src/pyamp/__init__.py -> repo root
    folders += [repo, repo / "packaging"]
    return folders


def read_document(name: str, folders: Iterable[Path] | None = None) -> str | None:
    """The text of a licence file, or None if this installation doesn't include it."""
    for folder in document_folders() if folders is None else folders:
        try:
            return (folder / name).read_text(encoding="utf-8")
        except OSError:
            continue
    try:  # an installed wheel keeps them in its metadata (pyproject's license-files)
        return distribution("pyamp").read_text(f"licenses/{name}")
    except PackageNotFoundError:
        return None


def recolour_links(document: QTextDocument, colour: QColor) -> None:
    """Give every link this colour (the skin's stylesheet keeps the palette's blue)."""
    cursor = QTextCursor(document)
    block = document.begin()
    while block.isValid():
        fragments = block.begin()
        while not fragments.atEnd():
            fragment = fragments.fragment()
            fragments += 1
            text_format = fragment.charFormat()
            if text_format.isAnchor():
                text_format.setForeground(colour)
                cursor.setPosition(fragment.position())
                cursor.setPosition(
                    fragment.position() + fragment.length(), QTextCursor.MoveMode.KeepAnchor
                )
                cursor.setCharFormat(text_format)
        block = block.next()


def notice_html(link_colour: str = "") -> str:
    link = f" style='color:{link_colour}'" if link_colour else ""
    return (
        f"<p style='font-size:15pt'><b>{APP_NAME}</b></p>"
        f"<p>Version {__version__}<br>"
        "A lightweight music player for your local library.</p>"
        f"<p>{COPYRIGHT}</p>"
        "<p>This program is free software: you can redistribute it and/or modify it "
        "under the terms of the GNU General Public License as published by the Free "
        "Software Foundation, either version 3 of the License, or (at your option) any "
        "later version.</p>"
        "<p>This program is distributed in the hope that it will be useful, but "
        "<b>WITHOUT ANY WARRANTY</b>; without even the implied warranty of "
        "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the License tab for "
        "details.</p>"
        "<p>Additional terms apply under section 7 of the GPL (see Additional Terms): "
        "modified versions must be marked as changed and use their own name and "
        "branding.</p>"
        f"<p><a href='{HOMEPAGE}'{link}>{HOMEPAGE}</a></p>"
        f"<p style='color:gray'>Qt {qVersion()} · PySide6 {pyside_version} · "
        f"Python {platform.python_version()}</p>"
    )


class AboutDialog(QDialog):
    def __init__(self, skin: Skin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setStyleSheet(
            skin.stylesheet() + f"QDialog, QTabWidget QWidget {{ background: {skin.window}; }}"
        )
        self.resize(560, 520)
        self._link_colour = QColor(skin.lcd)  # readable on the dark skin

        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(96, 96))
        icon.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        notice = QLabel(notice_html(skin.lcd))
        notice.setWordWrap(True)
        notice.setOpenExternalLinks(True)
        notice.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        about = QWidget()
        about_layout = QVBoxLayout(about)
        about_layout.addWidget(icon)
        about_layout.addWidget(notice)
        about_layout.addStretch(1)

        self.tabs = QTabWidget()
        self.tabs.addTab(about, "About")
        for title, name, url in DOCUMENTS:
            self.tabs.addTab(self._document(name, url), title)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs)
        layout.addWidget(buttons)

    def open_link(self, url: QUrl) -> None:
        """Web links open in the browser; links to the other documents switch tabs."""
        if url.scheme() in {"http", "https", "mailto"}:
            QDesktopServices.openUrl(url)
            return
        name = Path(url.path()).name
        for index, (_title, document, _url) in enumerate(DOCUMENTS, start=1):
            if document == name:
                self.tabs.setCurrentIndex(index)

    def _document(self, name: str, url: str) -> QTextBrowser:
        view = QTextBrowser()
        view.setOpenLinks(False)  # never navigate away from the document
        view.anchorClicked.connect(self.open_link)
        text = read_document(name)
        if text is None:
            view.setHtml(
                f"<p>{name} isn't included here. Read it at <a href='{url}'>{url}</a>.</p>"
            )
        elif name.endswith(".md"):
            view.setMarkdown(text)
            recolour_links(view.document(), self._link_colour)
        else:
            view.setPlainText(text)
        return view
