"""A label that scrolls its text when it doesn't fit, like Winamp's title display."""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

SEPARATOR = "  ***  "


class MarqueeLabel(QWidget):
    def __init__(self, color: QColor, font: QFont, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._offset = 0
        self._color = color
        self.setFont(font)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(self.fontMetrics().height() + 2)
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def text(self) -> str:
        return self._text

    def set_text(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self._offset = 0
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(150, self.fontMetrics().height() + 2)

    def _cycle_width(self) -> int:
        return self.fontMetrics().horizontalAdvance(self._text + SEPARATOR)

    def _needs_scroll(self) -> bool:
        return self.fontMetrics().horizontalAdvance(self._text) > self.width()

    def _tick(self) -> None:
        if self._needs_scroll():
            self._offset = (self._offset + 1) % max(self._cycle_width(), 1)
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setPen(self._color)
        baseline = self.fontMetrics().ascent() + 1
        if not self._needs_scroll():
            painter.drawText(0, baseline, self._text)
            return
        cycle = self._cycle_width()
        x = -self._offset
        while x < self.width():
            painter.drawText(x, baseline, self._text + SEPARATOR)
            x += cycle
