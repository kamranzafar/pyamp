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
"""The equaliser window: an on/off switch, presets, a preamp and 10 band sliders."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from pyamp.core.equalizer import BANDS_HZ, GAIN_RANGE_DB, PRESETS, Equalizer
from pyamp.core.events import EqualizerChanged, EventBus
from pyamp.core.player import Player
from pyamp.ui.skin import Skin

#: Slider positions per dB (sliders are integer, gains move in 0.5 dB steps).
STEPS_PER_DB = 2
#: Slider changes are sent once a slider has been still this long (or is released).
#: The mpv backend rebuilds its filter chain on each change, so not continuously.
DEBOUNCE_MS = 150
CUSTOM = "Custom"
UNSUPPORTED_NOTE = (
    "The equaliser needs the mpv backend. Start PyAmp with <b>--backend mpv</b> to use it."
)


def band_label(hz: int) -> str:
    return f"{hz // 1000}K" if hz >= 1000 else str(hz)


class EqualizerWindow(QDialog):
    def __init__(
        self, player: Player, bus: EventBus, skin: Skin, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._player = player
        self.setWindowTitle("Equaliser")
        self.setObjectName("central")
        self.setStyleSheet(skin.stylesheet() + _extra_style(skin))

        self.enabled = QCheckBox("On")
        self.presets = QComboBox()
        self.presets.addItems([*PRESETS, CUSTOM])
        self.flat = QPushButton("Flat")
        self.note = QLabel(UNSUPPORTED_NOTE)
        self.note.setWordWrap(True)
        self.preamp = _gain_slider("Preamp")
        self.bands = [_gain_slider(f"{band_label(hz)} Hz") for hz in BANDS_HZ]

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._push)

        self._layout()
        self._show(player.equalizer)
        self._set_supported(player.supports_equalizer)

        self.enabled.toggled.connect(self._push)
        self.presets.textActivated.connect(self._on_preset)
        self.flat.clicked.connect(lambda: self._on_preset("Flat", reset_preamp=True))
        for slider in (self.preamp, *self.bands):
            slider.valueChanged.connect(self._on_slider)
            slider.sliderReleased.connect(self.flush)
        bus.subscribe(EqualizerChanged, lambda e: self._show(e.equalizer))

    # -- Widgets -> player ---------------------------------------------------- #

    def current(self) -> Equalizer:
        """The equaliser as the widgets show it."""
        return Equalizer(
            enabled=self.enabled.isChecked(),
            preamp_db=self.preamp.value() / STEPS_PER_DB,
            gains_db=tuple(s.value() / STEPS_PER_DB for s in self.bands),
        )

    def flush(self) -> None:
        """Send a pending slider change now."""
        if self._debounce.isActive():
            self._debounce.stop()
            self._push()

    def _on_slider(self) -> None:
        self._update_labels()
        self._debounce.start()  # restarts while the slider keeps moving

    def _on_preset(self, name: str, *, reset_preamp: bool = False) -> None:
        if name not in PRESETS:
            return
        eq = self.current().with_preset(name)
        self._push(eq.with_preamp(0.0) if reset_preamp else eq)

    def _push(self, equalizer: Equalizer | None = None) -> None:
        equalizer = equalizer if isinstance(equalizer, Equalizer) else self.current()
        if equalizer != self._player.equalizer:
            self._player.set_equalizer(equalizer)

    # -- Player -> widgets ---------------------------------------------------- #

    def _show(self, equalizer: Equalizer) -> None:
        """Reflect ``equalizer`` in the widgets without sending it back to the player."""
        widgets = [self.enabled, self.preamp, *self.bands]
        for widget in widgets:
            widget.blockSignals(True)
        self.enabled.setChecked(equalizer.enabled)
        self.preamp.setValue(round(equalizer.preamp_db * STEPS_PER_DB))
        for slider, gain in zip(self.bands, equalizer.gains_db, strict=True):
            slider.setValue(round(gain * STEPS_PER_DB))
        for widget in widgets:
            widget.blockSignals(False)
        self._update_labels()

    def _update_labels(self) -> None:
        eq = self.current()
        self.presets.blockSignals(True)
        self.presets.setCurrentText(eq.preset_name() or CUSTOM)
        self.presets.blockSignals(False)
        for slider in (self.preamp, *self.bands):
            slider.setToolTip(f"{slider.objectName()}: {slider.value() / STEPS_PER_DB:+.1f} dB")

    def _set_supported(self, supported: bool) -> None:
        self.note.setVisible(not supported)
        for widget in (self.enabled, self.presets, self.flat, self.preamp, *self.bands):
            widget.setEnabled(supported)

    def _layout(self) -> None:
        top = QHBoxLayout()
        top.addWidget(self.enabled)
        top.addStretch()
        top.addWidget(QLabel("Preset"))
        top.addWidget(self.presets)
        top.addWidget(self.flat)

        sliders = QGridLayout()
        sliders.setHorizontalSpacing(10)
        # Column 0: preamp. Column 1: a separator. Columns 2+: the bands.
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        sliders.addWidget(separator, 0, 1, 2, 1)
        columns = [(0, "Preamp", self.preamp)]
        columns += [
            (i + 2, band_label(hz), s)
            for i, (hz, s) in enumerate(zip(BANDS_HZ, self.bands, strict=True))
        ]
        for column, text, slider in columns:
            label = QLabel(text)
            label.setObjectName("bandLabel")
            sliders.addWidget(slider, 0, column, Qt.AlignmentFlag.AlignHCenter)
            sliders.addWidget(label, 1, column, Qt.AlignmentFlag.AlignHCenter)

        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.note)
        layout.addLayout(sliders)


def _gain_slider(name: str) -> QSlider:
    slider = QSlider(Qt.Orientation.Vertical)
    slider.setObjectName(name)
    limit = round(GAIN_RANGE_DB * STEPS_PER_DB)
    slider.setRange(-limit, limit)
    slider.setPageStep(STEPS_PER_DB * 3)
    slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
    slider.setTickInterval(limit)  # ticks at -12, 0 and +12 dB
    slider.setMinimumHeight(120)
    return slider


def _extra_style(skin: Skin) -> str:
    return f"""
    QDialog#central {{ background: {skin.window}; }}
    QSlider::groove:vertical {{
        width: 6px; background: #111118; border: 1px solid {skin.border};
    }}
    QSlider::add-page:vertical {{ background: #3b5f2b; }}
    QSlider::handle:vertical {{
        height: 10px; margin: 0 -5px; border-radius: 2px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #d8d0a0, stop:1 #8a8060);
    }}
    QSlider::add-page:vertical:disabled {{ background: #2a3326; }}
    QSlider::handle:vertical:disabled {{ background: #55524a; }}
    QLabel#bandLabel {{ color: {skin.text_dim}; font-size: 10px; }}
    QCheckBox {{ color: {skin.text}; }}
    QComboBox, QPushButton {{
        background: #2c2c38; color: {skin.text}; border: 1px solid #15151c; padding: 2px 6px;
    }}
    QCheckBox:disabled, QComboBox:disabled, QPushButton:disabled {{ color: {skin.text_dim}; }}
    """
