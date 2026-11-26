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

"""The parts dialogs are built from, and the one stylesheet that gives them their look."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QFormLayout, QWidget

from pyamp.ui import dialog_parts
from pyamp.ui.skin import DEFAULT_SKIN, Skin


@pytest.fixture(autouse=True)
def _qt(qt_app: QCoreApplication) -> None:
    """Widgets need the Qt application."""


@pytest.mark.parametrize(
    ("widget", "role"),
    [
        (lambda: dialog_parts.heading("Rules"), "heading"),
        (lambda: dialog_parts.hint("Nothing yet"), "hint"),
        (dialog_parts.status_label, "status"),
        (dialog_parts.separator, "separator"),
        (dialog_parts.inset, "inset"),
        (lambda: dialog_parts.link_button("+ Add"), "link"),
        (lambda: dialog_parts.remove_button("Remove"), "remove"),
    ],
)
def test_each_part_has_a_role_the_stylesheet_styles(widget: object, role: str) -> None:
    made = widget()  # type: ignore[operator]
    assert isinstance(made, QWidget)
    assert made.objectName() == role
    assert f"#{role}" in DEFAULT_SKIN.stylesheet()


def test_headings_are_capitalised() -> None:
    assert dialog_parts.heading("Smart Playlist").text() == "SMART PLAYLIST"


def test_buttons_that_arent_the_dialogs_main_one_never_become_it() -> None:
    for button in (
        dialog_parts.plain_button("Rescan"),
        dialog_parts.link_button("+ Add"),
        dialog_parts.remove_button("Remove"),
    ):
        assert not button.autoDefault()
    assert dialog_parts.remove_button("Remove this rule").toolTip() == "Remove this rule"


def test_the_status_is_coloured_by_its_state() -> None:
    label = dialog_parts.status_label()
    dialog_parts.set_status(label, "3 tracks match", "count")
    assert (label.text(), label.property("state")) == ("3 tracks match", "count")
    dialog_parts.set_status(label, "Needs a name.", "problem")
    assert label.property("state") == "problem"
    dialog_parts.set_status(label, "Hello")
    assert label.property("state") == ""
    sheet = DEFAULT_SKIN.stylesheet()
    for state in ("count", "problem"):
        assert f'QLabel#status[state="{state}"]' in sheet


def test_forms_are_left_aligned_and_fill_the_width() -> None:
    form = dialog_parts.form_layout()
    assert form.formAlignment() & Qt.AlignmentFlag.AlignLeft
    assert form.labelAlignment() & Qt.AlignmentFlag.AlignLeft
    assert form.fieldGrowthPolicy() is QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow


def test_the_stylesheet_uses_the_skins_colours() -> None:
    skin = Skin(window="#010203", warning="#0a0b0c", selection="#0d0e0f")
    sheet = skin.stylesheet()
    for colour in ("#010203", "#0a0b0c", "#0d0e0f"):
        assert colour in sheet
    assert "QPushButton:default" in sheet  # a dialog's main button stands out
    for kind in ("QDialog", "QLineEdit", "QComboBox", "QSpinBox", "QTabBar", "QTreeView"):
        assert kind in sheet
