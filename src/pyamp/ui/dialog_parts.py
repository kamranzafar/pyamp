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

"""Parts that dialogs are built from, so they all look and behave alike.

Each part only sets an object name (its role); how a role looks is in the skin's
stylesheet (:meth:`pyamp.ui.skin.Skin.stylesheet`), which the application sets once.
"""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QLabel,
    QPushButton,
    QWidget,
)

type StatusState = Literal["count", "busy", "problem", ""]


#: Room in a combo box's popup for the check mark beside the current item, and padding.
_POPUP_ROOM = 56


class ComboBox(QComboBox):
    """A combo box whose popup is wide enough for its items. On macOS the popup can be
    narrower than the box, so the check mark crowds the item text out."""

    def showPopup(self) -> None:  # noqa: N802 (Qt's name)
        metrics = self.view().fontMetrics()
        widest = max(
            (metrics.horizontalAdvance(self.itemText(i)) for i in range(self.count())), default=0
        )
        self.view().setMinimumWidth(widest + _POPUP_ROOM)
        super().showPopup()


def heading(text: str) -> QLabel:
    """A section's small, capitalised title."""
    label = QLabel(text.upper())
    label.setObjectName("heading")
    return label


def hint(text: str) -> QLabel:
    """A quiet note, e.g. what an empty list means."""
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


def status_label() -> QLabel:
    """A dialog's one-line status, coloured by :func:`set_status`."""
    label = QLabel()
    label.setObjectName("status")
    label.setWordWrap(True)
    return label


def set_status(label: QLabel, text: str, state: StatusState = "") -> None:
    """Show ``text``: "count" (a result, in the LCD colour), "busy", "problem" (something
    the user can fix, in the warning colour) or "" (plain)."""
    label.setText(text)
    if label.property("state") != state:
        label.setProperty("state", state)
        label.style().unpolish(label)  # restyle for the new state
        label.style().polish(label)


def separator() -> QFrame:
    """A thin horizontal line between parts of a dialog."""
    line = QFrame()
    line.setObjectName("separator")
    line.setFixedHeight(1)
    return line


def inset() -> QFrame:
    """A panel set into the dialog, grouping controls (e.g. a list of rules)."""
    frame = QFrame()
    frame.setObjectName("inset")
    return frame


def plain_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """A button that never becomes a dialog's default (primary) one, so it isn't shown as
    one or pressed by Enter, e.g. a window's "Rescan"."""
    made = QPushButton(text, parent)
    made.setAutoDefault(False)
    return made


def link_button(text: str, parent: QWidget | None = None) -> QPushButton:
    """A flat button that reads like a link, e.g. "+ Add Rule"."""
    button = QPushButton(text, parent)
    button.setAutoDefault(False)
    button.setObjectName("link")
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


def remove_button(tooltip: str, parent: QWidget | None = None) -> QPushButton:
    """A small cross that removes the row it ends."""
    button = QPushButton("\N{MULTIPLICATION SIGN}", parent)
    button.setAutoDefault(False)
    button.setObjectName("remove")
    button.setToolTip(tooltip)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedWidth(28)
    return button


def form_layout() -> QFormLayout:
    """Labels beside fields, left-aligned and filling the width on every platform (macOS
    otherwise centres the form and right-aligns its labels)."""
    form = QFormLayout()
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    return form
