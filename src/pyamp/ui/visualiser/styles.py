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

"""The built-in visualiser styles, in menu order. Add one by registering a class."""

from __future__ import annotations

import math
from itertools import pairwise

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath, QPen

from pyamp.ui.visualiser.style import Frame, Palette, Style, register

#: Below this a level is drawn as nothing at all.
VISIBLE = 0.01


def _slots(rect: QRectF, count: int) -> tuple[float, float]:
    """The width of each band's slot and of the bar in it (one pixel gap between)."""
    slot = rect.width() / count
    return slot, max(slot - 1.0, 1.0)


@register
class Bars(Style):
    """Winamp's: gradient bars with falling peak caps."""

    id = "bars"
    label = "&Bars"

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        slot, width = _slots(rect, len(frame.bars))
        gradient = self.palette.vertical(rect)
        height = rect.height()
        for i, (bar, peak) in enumerate(zip(frame.bars, frame.peaks, strict=True)):
            x = rect.left() + i * slot
            if bar * height >= 1:
                painter.fillRect(
                    QRectF(x, rect.bottom() - bar * height, width, bar * height), gradient
                )
            if peak > VISIBLE:
                painter.fillRect(
                    QRectF(x, rect.bottom() - peak * height - 1, width, 1), self.palette.peak
                )


@register
class Dots(Style):
    """Only the falling peak caps, each in the gradient's colour for its height."""

    id = "dots"
    label = "&Dots"

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        slot, width = _slots(rect, len(frame.peaks))
        height = rect.height()
        for i, peak in enumerate(frame.peaks):
            if peak > VISIBLE:
                top = rect.bottom() - peak * (height - 2) - 2
                painter.fillRect(
                    QRectF(rect.left() + i * slot, top, width, 2), self.palette.at(peak)
                )


@register
class Mirrored(Style):
    """Bars growing up and down from the middle."""

    id = "mirrored"
    label = "&Mirrored"

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        slot, width = _slots(rect, len(frame.bars))
        middle = rect.center().y()
        half = rect.height() / 2
        gradient = self.palette.gradient(QPointF(0, middle), QPointF(0, rect.top()), reflect=True)
        for i, (bar, peak) in enumerate(zip(frame.bars, frame.peaks, strict=True)):
            x = rect.left() + i * slot
            if bar * half >= 0.5:
                painter.fillRect(QRectF(x, middle - bar * half, width, 2 * bar * half), gradient)
            if peak > VISIBLE:
                for y in (middle - peak * half - 1, middle + peak * half):
                    painter.fillRect(QRectF(x, y, width, 1), self.palette.peak)


@register
class Line(Style):
    """A smooth curve through the tops of the bands, filled underneath."""

    id = "line"
    label = "&Line"

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        if frame.bars.max() <= VISIBLE:
            return  # silence: not even a flat line
        count = len(frame.bars)
        slot = rect.width() / count
        points = [
            QPointF(rect.left() + (i + 0.5) * slot, rect.bottom() - bar * (rect.height() - 1))
            for i, bar in enumerate(frame.bars)
        ]
        # Through the middle of each pair of points, with the points as control points.
        curve = QPainterPath(QPointF(rect.left(), points[0].y()))
        curve.lineTo(points[0])
        for here, there in pairwise(points):
            curve.quadTo(here, (here + there) / 2)
        curve.lineTo(points[-1])
        curve.lineTo(QPointF(rect.right(), points[-1].y()))

        area = QPainterPath(curve)
        area.lineTo(rect.bottomRight())
        area.lineTo(rect.bottomLeft())
        area.closeSubpath()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        gradient = self.palette.vertical(rect)
        painter.setOpacity(0.45)
        painter.fillPath(area, gradient)
        painter.setOpacity(1.0)
        painter.strokePath(curve, QPen(gradient, 1.5))


@register
class Spectrogram(Style):
    """A scrolling picture of the last few seconds: time across, pitch up, loudness as colour."""

    id = "spectrogram"
    label = "&Spectrogram"
    history = 240  # frames, about 7 seconds at the widget's 30 ms

    def __init__(self, palette: Palette) -> None:
        super().__init__(palette)
        # Silence is the panel's colour, loud is the top of the gradient.
        colors = [palette.background, *palette.stops]
        table = np.empty(256, dtype=np.uint32)
        for n in range(256):
            position = n / 255 * (len(colors) - 1)
            i = min(int(position), len(colors) - 2)
            low, high, t = colors[i], colors[i + 1], position - i
            rgb = [
                round(a + (b - a) * t)
                for a, b in zip(
                    (low.red(), low.green(), low.blue()),
                    (high.red(), high.green(), high.blue()),
                    strict=True,
                )
            ]
            table[n] = 0xFF000000 | rgb[0] << 16 | rgb[1] << 8 | rgb[2]
        self._table = table
        self._pixels: NDArray[np.uint32] | None = None  # kept alive while QImage uses it

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        if not len(frame.history):
            return
        # One column per frame, the lowest band at the bottom.
        shades = (frame.history.T[::-1].clip(0.0, 1.0) * 255).astype(np.uint8)
        self._pixels = np.ascontiguousarray(self._table[shades])
        rows, columns = self._pixels.shape
        image = QImage(self._pixels.data, columns, rows, columns * 4, QImage.Format.Format_RGB32)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, image)


@register
class Meter(Style):
    """One LED level meter for the whole sound, with a held peak."""

    id = "meter"
    label = "Level M&eter"
    SEGMENT = 4.0  # pixels, plus a one-pixel gap

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        bar_height = min(rect.height() * 0.5, 14.0)
        top = rect.center().y() - bar_height / 2
        step = self.SEGMENT + 1
        count = max(int(rect.width() // step), 1)
        lit = round(frame.level * count)
        peak = min(round(frame.peak_level * count), count) - 1
        for n in range(count):
            share = (n + 1) / count
            color = self.palette.at(share)
            if n >= lit and n != peak:
                color.setAlphaF(0.12)  # unlit segments stay faintly visible, like LEDs
            painter.fillRect(QRectF(rect.left() + n * step, top, self.SEGMENT, bar_height), color)


@register
class Radial(Style):
    """The bars around a ring, lowest band on the left, mirrored above and below.

    The ring is an ellipse as wide as the strip allows, since a circle in the short
    visualiser strip would be tiny.
    """

    id = "radial"
    label = "&Radial"
    HOLE = 0.35  # the empty middle, as a share of the ring's size
    WIDEST = 4.0  # the ring is at most this many times wider than tall

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        count = len(frame.bars)
        centre = rect.center()
        ry = rect.height() / 2 - 1
        rx = min(rect.width() / 2 - 1, ry * self.WIDEST)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, bar in enumerate(frame.bars):
            if bar <= VISIBLE:
                continue
            angle = math.pi * (i + 0.5) / count  # 0 on the left, pi on the right
            reach = self.HOLE + bar * (1 - self.HOLE)
            pen = QPen(self.palette.at(bar), 2.0)
            pen.setCapStyle(Qt.PenCapStyle.FlatCap)
            painter.setPen(pen)
            for side in (-1, 1):  # above, then below
                dx, dy = -math.cos(angle) * rx, side * math.sin(angle) * ry
                painter.drawLine(
                    QLineF(
                        centre + QPointF(dx * self.HOLE, dy * self.HOLE),
                        centre + QPointF(dx * reach, dy * reach),
                    )
                )


@register
class Off(Style):
    """Nothing: the visualiser keeps its space, and the levels aren't worked out."""

    id = "off"
    label = "&Off"
    active = False

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        pass
