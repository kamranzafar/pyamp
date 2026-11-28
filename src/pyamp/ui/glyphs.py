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

"""The transport glyphs (play, pause, shuffle, …), drawn rather than typed.

Characters such as ⏮ ⏸ ⏹ aren't in every platform's interface font, so each system
borrows them from a different one: Windows draws them as coloured emoji, next to plain
« » and a tiny ▸. Drawing them as shapes makes them look the same everywhere, sharp at
any scale and in the skin's colours.

Shapes are drawn on a 16-by-16 grid, scaled to fit the space they're given.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QIcon,
    QIconEngine,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)

from pyamp.ui.skin import Skin

GRID = 16.0
_STROKE = 1.7


def _triangle(*points: tuple[float, float]) -> QPainterPath:
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    return path


def _rect(x: float, y: float, w: float, h: float) -> QPainterPath:
    path = QPainterPath()
    path.addRect(QRectF(x, y, w, h))
    return path


def _mirrored(path: QPainterPath) -> QPainterPath:
    return QTransform(-1, 0, 0, 1, GRID, 0).map(path)


def _previous() -> QPainterPath:
    return _rect(2.5, 3.5, 2.2, 9) | _triangle((13.5, 3.5), (5, 8), (13.5, 12.5))


def _rewind() -> QPainterPath:
    return _triangle((8, 3.5), (1.5, 8), (8, 12.5)) | _triangle((14.5, 3.5), (8, 8), (14.5, 12.5))


def _shuffle() -> tuple[QPainterPath, QPainterPath]:
    lines = QPainterPath()
    lines.moveTo(1.5, 4.5)
    lines.lineTo(4, 4.5)
    lines.cubicTo(8, 4.5, 7, 11.5, 11, 11.5)
    lines.moveTo(1.5, 11.5)
    lines.lineTo(4, 11.5)
    lines.cubicTo(8, 11.5, 7, 4.5, 11, 4.5)
    heads = _triangle((10.5, 1.8), (14.5, 4.5), (10.5, 7.2)) | _triangle(
        (10.5, 8.8), (14.5, 11.5), (10.5, 14.2)
    )
    return lines, heads


def _repeat(one: bool) -> tuple[QPainterPath, QPainterPath]:
    loop = QPainterPath()
    loop.moveTo(10.5, 4.5)
    loop.lineTo(5, 4.5)
    loop.quadTo(2, 4.5, 2, 7.5)
    loop.lineTo(2, 8.5)
    loop.quadTo(2, 11.5, 5, 11.5)
    loop.lineTo(11, 11.5)
    loop.quadTo(14, 11.5, 14, 8.5)
    loop.lineTo(14, 8)
    if one:
        loop.moveTo(6.9, 7.3)
        loop.lineTo(8.3, 6.4)
        loop.lineTo(8.3, 9.8)
    return loop, _triangle((10, 1.8), (13.6, 4.5), (10, 7.2))


# Each glyph: (outline drawn with a pen, shape filled), either may be empty.
_EMPTY = QPainterPath()
_GLYPHS: dict[str, Callable[[], tuple[QPainterPath, QPainterPath]]] = {
    "play": lambda: (_EMPTY, _triangle((4, 2.5), (13.5, 8), (4, 13.5))),
    "pause": lambda: (_EMPTY, _rect(3.5, 3, 3.2, 10) | _rect(9.3, 3, 3.2, 10)),
    "stop": lambda: (_EMPTY, _rect(3.5, 3.5, 9, 9)),
    "previous": lambda: (_EMPTY, _previous()),
    "next": lambda: (_EMPTY, _mirrored(_previous())),
    "rewind": lambda: (_EMPTY, _rewind()),
    "fast-forward": lambda: (_EMPTY, _mirrored(_rewind())),
    "eject": lambda: (_EMPTY, _triangle((2.5, 9), (8, 3), (13.5, 9)) | _rect(2.5, 11, 11, 2)),
    "shuffle": _shuffle,
    "repeat": lambda: _repeat(one=False),
    "repeat-one": lambda: _repeat(one=True),
}
NAMES = tuple(_GLYPHS)


def paint_glyph(painter: QPainter, rect: QRectF, name: str, colour: QColor) -> None:
    """Draw glyph ``name`` centred in ``rect``, as large as fits."""
    outline, fill = _GLYPHS[name]()
    side = min(rect.width(), rect.height())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.center())
    painter.scale(side / GRID, side / GRID)
    painter.translate(-GRID / 2, -GRID / 2)
    if not outline.isEmpty():
        pen = QPen(colour, _STROKE)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.strokePath(outline, pen)
    if not fill.isEmpty():
        painter.fillPath(fill, colour)
    painter.restore()


class _GlyphEngine(QIconEngine):
    """Paints a glyph at whatever size and pixel density it's asked for, coloured to
    match the skin's buttons: plain, highlighted (hovered, or picked in a menu), lit
    up in LCD green when its button is checked, and dim when disabled."""

    def __init__(self, name: str, skin: Skin) -> None:
        super().__init__()
        self._name = name
        self._skin = skin

    def colour(self, mode: QIcon.Mode, state: QIcon.State) -> QColor:
        skin = self._skin
        if mode is QIcon.Mode.Disabled:
            return QColor(skin.text_dim)
        if state is QIcon.State.On:
            return QColor(skin.lcd)
        if mode in (QIcon.Mode.Active, QIcon.Mode.Selected):
            return QColor(skin.accent)
        return QColor(skin.text)

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        paint_glyph(painter, QRectF(rect), self._name, self.colour(mode, state))

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    def scaledPixmap(  # noqa: N802 (Qt override)
        self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float
    ) -> QPixmap:
        pixmap = QPixmap(QSize(math.ceil(size.width() * scale), math.ceil(size.height() * scale)))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap

    def clone(self) -> QIconEngine:
        return _GlyphEngine(self._name, self._skin)

    def key(self) -> str:
        return "pyamp-glyph"


def glyph_icon(name: str, skin: Skin) -> QIcon:
    """An icon of glyph ``name`` (one of ``NAMES``) in ``skin``'s button colours."""
    if name not in _GLYPHS:
        raise KeyError(name)
    return QIcon(_GlyphEngine(name, skin))
