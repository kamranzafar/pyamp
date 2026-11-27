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

"""The visualiser: animates the spectrum levels and has the chosen style draw them."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent, QMouseEvent, QPainter, QPaintEvent, QShowEvent
from PySide6.QtWidgets import QWidget

from pyamp.ui.skin import Skin
from pyamp.ui.visualiser import (
    DEFAULT_STYLE,
    STYLES,
    Frame,
    Palette,
    Style,
    next_style_id,
    style_id,
)

FRAME_MS = 30
BAR_FALL = 0.06  # level units per frame
PEAK_FALL = 0.012
PEAK_HOLD_FRAMES = 12


class SpectrumWidget(QWidget):
    """Draws levels pushed via :meth:`set_levels` and animates their decay.

    Bars jump up instantly and fall smoothly. That behaviour belongs to the
    view, so the analyser can stay a pure function of the audio. How they are
    drawn is up to the :class:`~pyamp.ui.visualiser.Style`; clicking moves on
    to the next one.
    """

    #: The id of the style now shown, after a click or :meth:`set_style`.
    style_changed = Signal(str)

    def __init__(
        self,
        band_count: int,
        skin: Skin,
        parent: QWidget | None = None,
        *,
        style: str = DEFAULT_STYLE,
    ) -> None:
        super().__init__(parent)
        self._palette = Palette(skin)
        self._target = np.zeros(band_count)
        self._bars = np.zeros(band_count)
        self._peaks = np.zeros(band_count)
        self._hold = np.zeros(band_count, dtype=int)
        self._history = np.zeros((0, band_count))
        self._tick = 0
        self._style: Style = STYLES[DEFAULT_STYLE](self._palette)
        self.setMinimumHeight(32)
        self.setToolTip("Click to change the visualiser (also in View → Visualiser)")
        self._timer = QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._animate)  # runs only while something moves
        self._apply_style(style_id(style))

    @property
    def style_id(self) -> str:
        return self._style.id

    @property
    def active(self) -> bool:
        """Whether the style shows the levels at all (not "Off")."""
        return self._style.active

    def set_style(self, style: str) -> None:
        style = style_id(style)
        if style != self._style.id:
            self._apply_style(style)
            self.style_changed.emit(style)

    def _apply_style(self, style: str) -> None:
        self._style = STYLES[style](self._palette)
        rows = self._style.history
        self._history = np.zeros((rows, len(self._bars)))
        if not self._style.active:
            self._timer.stop()
            self._target[:] = self._bars[:] = self._peaks[:] = 0.0
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_style(next_style_id(self._style.id))
            event.accept()
            return
        super().mousePressEvent(event)

    def set_levels(self, levels: Sequence[float]) -> None:
        if not self._style.active:
            return
        self._target = np.asarray(levels, dtype=float)
        if self.isVisible() and not self._timer.isActive() and self._target.any():
            self._timer.start()

    @property
    def animating(self) -> bool:
        return self._timer.isActive()

    def _moving(self) -> bool:
        return bool(
            self._target.any() or self._bars.any() or self._peaks.any() or self._history.any()
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._style.active and self._moving():
            self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        self._timer.stop()

    def sizeHint(self) -> QSize:
        return QSize(len(self._bars) * 5, 40)

    def _animate(self) -> None:
        self._tick += 1
        bars = np.maximum(self._target, self._bars - BAR_FALL).clip(0.0, 1.0)
        rising = bars >= self._peaks
        peaks = np.where(rising, bars, self._peaks)
        self._hold = np.where(rising, PEAK_HOLD_FRAMES, self._hold - 1)
        peaks = np.where(self._hold <= 0, np.maximum(bars, peaks - PEAK_FALL), peaks)
        if len(self._history):
            # Time moves on even when the levels don't, until the picture has scrolled
            # away to silence.
            self._history = np.roll(self._history, -1, axis=0)
            self._history[-1] = bars
        elif (
            not (self._style.moves and bars.any())
            and np.array_equal(bars, self._bars)
            and np.array_equal(peaks, self._peaks)
        ):
            if not (bars.any() or peaks.any()):
                self._timer.stop()  # all fallen to zero: nothing to animate until new levels
            return  # nothing moved; skip the repaint
        self._bars, self._peaks = bars, peaks
        if not self._moving():
            self._timer.stop()
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        if not len(self._bars):
            return
        painter = QPainter(self)
        self._style.paint(
            painter,
            QRectF(self.rect()),
            Frame(bars=self._bars, peaks=self._peaks, history=self._history, tick=self._tick),
        )
