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

"""What a visualiser style is, and the list of them.

A style only draws. The widget owns the levels and their animation (bars jump up and
fall, peak caps hold and fall) and hands every style the same :class:`Frame`, so a new
style is one class with a ``paint`` method, registered with :func:`register`. The
View menu, clicking the visualiser and the saved setting all read :data:`STYLES`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QGradient, QLinearGradient, QPainter

from pyamp.ui.skin import Skin


@dataclass(frozen=True, slots=True)
class Frame:
    """What there is to draw, every value in [0, 1], lowest frequency first."""

    bars: NDArray[np.float64]
    peaks: NDArray[np.float64]
    #: Past ``bars``, oldest first: ``(Style.history, band count)``, or empty rows for a
    #: style that asked for none.
    history: NDArray[np.float64]
    #: Counts the widget's animation frames (about 33 a second), for styles that move
    #: on their own, like waves.
    tick: int = 0
    #: The sound's latest waveform, values in [-1, 1], oldest first; empty for silence.
    waveform: NDArray[np.float64] = field(default_factory=lambda: np.zeros(0))

    @property
    def level(self) -> float:
        """One overall loudness for the frame (root mean square of the bands)."""
        return float(np.sqrt(np.mean(self.bars**2))) if len(self.bars) else 0.0

    @property
    def peak_level(self) -> float:
        return float(np.sqrt(np.mean(self.peaks**2))) if len(self.peaks) else 0.0


class Palette:
    """The skin's visualiser colours, ready for painting."""

    def __init__(self, skin: Skin) -> None:
        self.stops = [QColor(c) for c in skin.spectrum]
        self.peak = QColor(skin.spectrum_peak)
        self.background = QColor(skin.panel)

    def gradient(self, start: QPointF, end: QPointF, *, reflect: bool = False) -> QLinearGradient:
        """The spectrum gradient from ``start`` (quiet) to ``end`` (loud)."""
        gradient = QLinearGradient(start, end)
        for i, color in enumerate(self.stops):
            gradient.setColorAt(i / max(len(self.stops) - 1, 1), color)
        if reflect:
            gradient.setSpread(QGradient.Spread.ReflectSpread)
        return gradient

    def vertical(self, rect: QRectF) -> QLinearGradient:
        """Quiet at the bottom of ``rect``, loud at the top."""
        return self.gradient(QPointF(0, rect.bottom()), QPointF(0, rect.top()))

    def at(self, level: float) -> QColor:
        """The gradient's colour at ``level`` (0 quiet, 1 loud)."""
        if len(self.stops) == 1:
            return QColor(self.stops[0])
        position = min(max(level, 0.0), 1.0) * (len(self.stops) - 1)
        i = min(int(position), len(self.stops) - 2)
        low, high = self.stops[i], self.stops[i + 1]
        t = position - i
        return QColor.fromRgbF(
            low.redF() + (high.redF() - low.redF()) * t,
            low.greenF() + (high.greenF() - low.greenF()) * t,
            low.blueF() + (high.blueF() - low.blueF()) * t,
        )


class Style:
    """One way of drawing the levels. Subclass it, then :func:`register` the subclass.

    The widget makes one instance per style it shows, so a style may keep things it
    has worked out (an image, a colour table) between frames.
    """

    #: Saved in the settings; never change it once released.
    id: ClassVar[str]
    #: For the View → Visualiser menu, with an ``&`` before its shortcut letter.
    label: ClassVar[str]
    #: How many past frames of bars it needs in :attr:`Frame.history` (0: none).
    history: ClassVar[int] = 0
    #: True for a style that changes from frame to frame by itself (``Frame.tick``), so
    #: it's redrawn while there's sound even when the levels hold still.
    moves: ClassVar[bool] = False
    #: False for a style that shows nothing, so the levels aren't worked out at all.
    active: ClassVar[bool] = True

    def __init__(self, palette: Palette) -> None:
        self.palette = palette

    def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
        raise NotImplementedError


#: Every style, by id, in the order the menu and clicking go through them.
STYLES: dict[str, type[Style]] = {}
DEFAULT_STYLE = "bars"


def register[S: type[Style]](style: S) -> S:
    """Adds ``style`` to :data:`STYLES` (use it as a class decorator)."""
    if style.id in STYLES:
        raise ValueError(f"A visualiser style {style.id!r} is already registered")
    STYLES[style.id] = style
    return style


def style_id(saved: str) -> str:
    """``saved`` if it names a style, otherwise the default (e.g. one since removed)."""
    return saved if saved in STYLES else DEFAULT_STYLE


def next_style_id(current: str) -> str:
    ids = list(STYLES)
    return ids[(ids.index(style_id(current)) + 1) % len(ids)]
