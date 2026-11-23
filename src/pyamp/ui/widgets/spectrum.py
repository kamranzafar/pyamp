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

"""Bar spectrum visualiser with falling peak caps."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PySide6.QtCore import QRectF, QSize, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from pyamp.ui.skin import Skin

FRAME_MS = 30
BAR_FALL = 0.06  # level units per frame
PEAK_FALL = 0.012
PEAK_HOLD_FRAMES = 12


class SpectrumWidget(QWidget):
    """Draws levels pushed via :meth:`set_levels` and animates their decay.

    Bars jump up instantly and fall smoothly. That behaviour belongs to the
    view, so the analyser can stay a pure function of the audio.
    """

    def __init__(self, band_count: int, skin: Skin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._skin = skin
        self._target = np.zeros(band_count)
        self._bars = np.zeros(band_count)
        self._peaks = np.zeros(band_count)
        self._hold = np.zeros(band_count, dtype=int)
        self.setMinimumHeight(32)
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    def set_levels(self, levels: Sequence[float]) -> None:
        self._target = np.asarray(levels, dtype=float)

    def sizeHint(self) -> QSize:
        return QSize(len(self._bars) * 5, 40)

    def _animate(self) -> None:
        bars = np.maximum(self._target, self._bars - BAR_FALL).clip(0.0, 1.0)
        rising = bars >= self._peaks
        peaks = np.where(rising, bars, self._peaks)
        self._hold = np.where(rising, PEAK_HOLD_FRAMES, self._hold - 1)
        peaks = np.where(self._hold <= 0, np.maximum(bars, peaks - PEAK_FALL), peaks)
        if np.array_equal(bars, self._bars) and np.array_equal(peaks, self._peaks):
            return  # nothing moved; skip the repaint
        self._bars, self._peaks = bars, peaks
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        count = len(self._bars)
        if count == 0:
            return
        height = self.height()
        slot = self.width() / count
        bar_width = max(slot - 1.0, 1.0)

        gradient = QLinearGradient(0, height, 0, 0)
        stops = self._skin.spectrum
        for i, color in enumerate(stops):
            gradient.setColorAt(i / max(len(stops) - 1, 1), QColor(color))

        peak_color = QColor(self._skin.spectrum_peak)
        for i, (bar, peak) in enumerate(zip(self._bars, self._peaks, strict=True)):
            x = i * slot
            bar_height = bar * height
            if bar_height >= 1:
                painter.fillRect(QRectF(x, height - bar_height, bar_width, bar_height), gradient)
            if peak > 0.01:
                painter.fillRect(QRectF(x, height - peak * height - 1, bar_width, 1), peak_color)
