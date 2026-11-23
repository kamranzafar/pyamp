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
    border: str = "#4a4a5e"
    text: str = "#c9c9d6"
    text_dim: str = "#6f6f84"
    lcd: str = "#00e83a"
    accent: str = "#ffffff"
    selection: str = "#1c3c8c"
    playlist_text: str = "#00e83a"
    playlist_current: str = "#ffffff"
    #: Spectrum gradient, bottom to top.
    spectrum: tuple[str, ...] = ("#18a818", "#d8d820", "#e83010")
    spectrum_peak: str = "#9a9aa8"

    def stylesheet(self) -> str:
        return f"""
        QMainWindow, QWidget#central {{ background: {self.window}; color: {self.text}; }}
        QFrame#lcdPanel {{
            background: {self.panel}; border: 1px solid {self.border}; border-radius: 2px;
        }}
        QLabel {{ color: {self.text}; }}
        QToolButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a4a5c, stop:1 #2c2c38);
            color: {self.text}; border: 1px solid #15151c; border-radius: 2px;
            min-width: 26px; min-height: 18px; padding: 1px 4px;
        }}
        QToolButton:hover {{ color: {self.accent}; }}
        QToolButton:pressed, QToolButton:checked {{
            background: #1a1a22; color: {self.lcd};
        }}
        QSlider::groove:horizontal {{
            height: 6px; background: #111118; border: 1px solid {self.border};
        }}
        QSlider::sub-page:horizontal {{ background: #3b5f2b; }}
        QSlider::handle:horizontal {{
            width: 14px; margin: -4px 0; border-radius: 2px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d8d0a0, stop:1 #8a8060);
        }}
        QTableView {{
            background: {self.panel}; color: {self.playlist_text};
            selection-background-color: {self.selection}; selection-color: {self.playlist_text};
            border: 1px solid {self.border}; outline: 0;
        }}
        QStatusBar {{ color: {self.text_dim}; }}
        QComboBox {{
            background: {self.panel}; color: {self.playlist_text};
            border: 1px solid {self.border}; padding: 1px 6px;
        }}
        QComboBox QAbstractItemView {{
            background: {self.panel}; color: {self.playlist_text};
            selection-background-color: {self.selection};
        }}
        QMenu {{ background: {self.window}; color: {self.text}; }}
        QMenu::item:selected {{ background: {self.selection}; }}
        """


DEFAULT_SKIN = Skin()
