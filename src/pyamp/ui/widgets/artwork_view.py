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
"""A track's picture (e.g. its album cover), scaled to fit and kept in proportion."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage, QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

NO_ARTWORK = "No artwork"
MIN_SIZE = 120


class ArtworkView(QLabel):
    """Shows an image file's bytes, or says there's no artwork."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("artwork")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(MIN_SIZE, MIN_SIZE)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._image = QImage()
        self.show_image(None)

    @property
    def has_image(self) -> bool:
        return not self._image.isNull()

    def show_image(self, data: bytes | None) -> None:
        """Show the image in ``data`` (an image file's bytes); none, or one that can't be
        read, shows that there's no artwork."""
        self._image = QImage.fromData(data) if data else QImage()
        if self._image.isNull():
            self.clear()
            self.setText(NO_ARTWORK)
        else:
            self._scale()

    def sizeHint(self) -> QSize:
        return QSize(MIN_SIZE * 2, MIN_SIZE * 2)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self.has_image:
            self._scale()

    def _scale(self) -> None:
        ratio = self.devicePixelRatioF()
        pixmap = QPixmap.fromImage(
            self._image.scaled(
                self.size() * ratio,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        pixmap.setDevicePixelRatio(ratio)
        self.setPixmap(pixmap)
