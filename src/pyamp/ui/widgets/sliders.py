"""Seek and volume sliders bound to the event bus."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QSlider, QStyle, QWidget

from pyamp.core.events import EventBus, PositionChanged, TrackChanged, VolumeChanged

VOLUME_STEPS = 100


class JumpSlider(QSlider):
    """A slider that jumps straight to the clicked position (instead of paging)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        if ev.button() is Qt.MouseButton.LeftButton and self.maximum() > self.minimum():
            value = QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), int(ev.position().x()), self.width()
            )
            self.setValue(value)
        super().mousePressEvent(ev)


class SeekSlider(JumpSlider):
    """Follows the playback position and emits :attr:`seek_requested` when the user seeks."""

    seek_requested = Signal(int)

    def __init__(self, bus: EventBus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 0)
        self.setToolTip("Seek")
        self.sliderReleased.connect(lambda: self.seek_requested.emit(self.value()))
        bus.subscribe(PositionChanged, self._on_position)
        bus.subscribe(TrackChanged, lambda _: self.setRange(0, 0))

    def mousePressEvent(self, ev: QMouseEvent) -> None:
        super().mousePressEvent(ev)
        # A click that is not followed by a drag still has to seek.
        if not self.isSliderDown():
            self.seek_requested.emit(self.value())

    def _on_position(self, event: PositionChanged) -> None:
        if self.isSliderDown():
            return  # don't fight the user while they drag
        self.blockSignals(True)
        self.setRange(0, event.duration_ms)
        self.setValue(event.position_ms)
        self.blockSignals(False)


class VolumeSlider(JumpSlider):
    """Emits :attr:`volume_requested` with a 0.0-1.0 value."""

    volume_requested = Signal(float)

    def __init__(self, bus: EventBus, initial: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, VOLUME_STEPS)
        self.setValue(round(initial * VOLUME_STEPS))
        self.setFixedWidth(90)
        self.setToolTip("Volume")
        self.valueChanged.connect(lambda v: self.volume_requested.emit(v / VOLUME_STEPS))
        bus.subscribe(VolumeChanged, self._on_volume)

    def _on_volume(self, event: VolumeChanged) -> None:
        self.blockSignals(True)
        self.setValue(round(event.volume * VOLUME_STEPS))
        self.blockSignals(False)
