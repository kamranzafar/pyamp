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

"""Skins: every colour the UI uses, in one place.

Widgets receive a :class:`Skin` instead of hard-coding colours. That is the
seam for future skinning work, e.g. a loader that builds a ``Skin`` (plus
bitmaps) from a classic ``.wsz`` file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Skin:
    name: str = "PyAmp Classic"
    window: str = "#23232f"
    panel: str = "#000000"
    #: A group of controls set into a window (e.g. a dialog's list of rules).
    inset: str = "#1a1a24"
    border: str = "#4a4a5e"
    button: str = "#2c2c38"
    button_edge: str = "#15151c"
    text: str = "#c9c9d6"
    text_dim: str = "#6f6f84"
    lcd: str = "#00e83a"
    accent: str = "#ffffff"
    #: Problems the user can fix (e.g. a form that isn't filled in yet).
    warning: str = "#f0a030"
    selection: str = "#1c3c8c"
    playlist_text: str = "#00e83a"
    playlist_current: str = "#ffffff"
    #: Spectrum gradient, bottom to top.
    spectrum: tuple[str, ...] = ("#18a818", "#d8d820", "#e83010")
    spectrum_peak: str = "#9a9aa8"

    def stylesheet(self) -> str:
        """The whole application's look, set once on the application, so every window
        and dialog (message boxes and plugins' dialogs too) is styled alike.

        Object names give widgets a role: ``heading``, ``hint`` and ``status`` labels
        (``status`` coloured by its ``state`` property: "count", "busy" or "problem"),
        ``inset`` and ``separator`` frames, and ``link`` and ``remove`` buttons. A
        dialog's default button is the primary one.
        """
        return self._windows() + self._inputs() + self._buttons() + self._lists()

    def _windows(self) -> str:
        return f"""
        QMainWindow, QDialog, QWidget#central {{ background: {self.window}; color: {self.text}; }}
        QLabel, QCheckBox, QRadioButton {{ color: {self.text}; }}
        QLabel:disabled, QCheckBox:disabled {{ color: {self.text_dim}; }}
        QLabel#heading {{
            color: {self.text_dim}; font-size: 11px; font-weight: bold; letter-spacing: 1px;
        }}
        QLabel#hint {{ color: {self.text_dim}; font-style: italic; }}
        QLabel#status {{ color: {self.text_dim}; }}
        QLabel#status[state="count"] {{ color: {self.lcd}; }}
        QLabel#status[state="problem"] {{ color: {self.warning}; }}
        QFrame#inset {{
            background: {self.inset}; border: 1px solid {self.border}; border-radius: 4px;
        }}
        QFrame#separator {{ background: {self.border}; border: 0; }}
        QFrame#lcdPanel {{
            background: {self.panel}; border: 1px solid {self.border}; border-radius: 2px;
        }}
        QStatusBar, QStatusBar QLabel {{ color: {self.text_dim}; }}
        QToolTip {{
            background: {self.panel}; color: {self.text}; border: 1px solid {self.border};
        }}
        QMenu {{ background: {self.window}; color: {self.text}; border: 1px solid {self.border}; }}
        QMenu::item {{ padding: 4px 18px; }}
        QMenu::item:selected {{ background: {self.selection}; }}
        QMenu::item:disabled {{ color: {self.text_dim}; }}
        QMenu::separator {{ height: 1px; background: {self.border}; margin: 4px 6px; }}
        QTabWidget::pane {{ border: 1px solid {self.border}; border-radius: 3px; top: -1px; }}
        QTabBar::tab {{
            background: {self.button}; color: {self.text_dim}; border: 1px solid {self.border};
            padding: 4px 12px; margin-right: 2px;
            border-top-left-radius: 3px; border-top-right-radius: 3px;
        }}
        QTabBar::tab:selected {{ background: {self.window}; color: {self.accent}; }}
        QTabBar::tab:hover {{ color: {self.text}; }}
        """

    def _inputs(self) -> str:
        field = (
            f"background: {self.panel}; color: {self.playlist_text};"
            f" border: 1px solid {self.border}; border-radius: 3px;"
            f" selection-background-color: {self.selection};"
            f" selection-color: {self.playlist_text};"
        )
        return f"""
        QLineEdit, QSpinBox, QDoubleSpinBox {{ {field} padding: 3px 6px; }}
        QComboBox {{ {field} padding: 3px 8px; min-height: 18px; }}
        QAbstractSpinBox QLineEdit, QComboBox QLineEdit {{
            background: transparent; border: 0; padding: 0;
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border-color: {self.lcd};
        }}
        QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{ color: {self.text_dim}; }}
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: 0; }}
        QComboBox QAbstractItemView {{
            background: {self.panel}; color: {self.playlist_text};
            selection-background-color: {self.selection}; border: 1px solid {self.border};
        }}
        QSlider::groove:horizontal {{
            height: 6px; background: #111118; border: 1px solid {self.border};
        }}
        QSlider::sub-page:horizontal {{ background: #3b5f2b; }}
        QSlider::handle:horizontal {{
            width: 14px; margin: -4px 0; border-radius: 2px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d8d0a0, stop:1 #8a8060);
        }}
        QSlider::handle:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{
            background: transparent;
        }}
        """

    def _buttons(self) -> str:
        return f"""
        QPushButton {{
            background: {self.button}; color: {self.text};
            border: 1px solid {self.button_edge}; border-radius: 3px; padding: 4px 14px;
        }}
        QPushButton:hover {{ color: {self.accent}; }}
        QPushButton:pressed {{ background: {self.button_edge}; }}
        QPushButton:disabled {{ color: {self.text_dim}; }}
        QPushButton:default {{ background: {self.selection}; color: {self.accent}; }}
        QPushButton:default:disabled {{ background: {self.button}; color: {self.text_dim}; }}
        QPushButton#link {{
            background: transparent; border: 0; color: {self.lcd}; padding: 2px 4px;
        }}
        QPushButton#link:hover {{ color: {self.accent}; }}
        QPushButton#remove {{
            background: transparent; border: 0; color: {self.text_dim};
            font-size: 15px; padding: 0;
        }}
        QPushButton#remove:hover {{ color: {self.warning}; }}
        QToolButton {{
            background: qlineargradient(
                x1:0, y1:0, x2:0, y2:1, stop:0 #4a4a5c, stop:1 {self.button}
            );
            color: {self.text}; border: 1px solid {self.button_edge}; border-radius: 2px;
            min-width: 26px; min-height: 18px; padding: 1px 4px;
        }}
        QToolButton:hover {{ color: {self.accent}; }}
        QToolButton:pressed, QToolButton:checked {{ background: #1a1a22; color: {self.lcd}; }}
        """

    def _lists(self) -> str:
        return f"""
        QTableView, QTreeView, QListView, QTextBrowser {{
            background: {self.panel}; color: {self.playlist_text};
            selection-background-color: {self.selection}; selection-color: {self.playlist_text};
            border: 1px solid {self.border}; outline: 0;
        }}
        QTextBrowser {{ color: {self.text}; }}
        QHeaderView::section {{
            background: {self.window}; color: {self.text_dim};
            border: 0; border-bottom: 1px solid {self.border}; padding: 2px 4px;
        }}
        """


DEFAULT_SKIN = Skin()
