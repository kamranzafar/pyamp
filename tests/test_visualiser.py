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

"""Visualiser styles: each one draws, they cycle and are remembered, and Off saves work."""

from __future__ import annotations

from collections.abc import Iterator

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QWidget

from pyamp.app import Application
from pyamp.core.events import AudioFrame, SpectrumUpdated
from pyamp.ui.skin import DEFAULT_SKIN
from pyamp.ui.visualiser import (
    DEFAULT_STYLE,
    STYLES,
    Frame,
    Palette,
    Style,
    next_style_id,
    register,
    style_id,
)
from pyamp.ui.widgets.spectrum import SpectrumWidget
from tests.test_background_loader import wait_until
from tests.test_playlist_ui import app  # noqa: F401  (fixture: the real app, offscreen)

BANDS = 20


def _frame(level: float, history: int = 0) -> Frame:
    bars = np.linspace(0.1, level, BANDS)
    wave = level * np.sin(np.linspace(0, 6 * np.pi, 96))
    return Frame(bars=bars, peaks=bars + 0.05, history=np.tile(bars, (history, 1)), waveform=wave)


def _paint(style: Style, frame: Frame) -> QImage:
    image = QImage(400, 40, QImage.Format.Format_RGB32)
    image.fill(QColor(DEFAULT_SKIN.panel))
    painter = QPainter(image)
    style.paint(painter, QRectF(image.rect()), frame)
    painter.end()
    return image


def _drawn(image: QImage) -> int:
    """How many pixels aren't the panel's colour."""
    panel = QColor(DEFAULT_SKIN.panel).rgb()
    return sum(
        image.pixel(x, y) != panel
        for x in range(0, image.width(), 2)
        for y in range(image.height())
    )


def test_the_built_in_styles_in_menu_order() -> None:
    assert list(STYLES) == [
        "bars",
        "dots",
        "mirrored",
        "line",
        "spectrogram",
        "meter",
        "radial",
        "waves",
        "oscilloscope",
        "off",
    ]
    assert DEFAULT_STYLE == "bars"


@pytest.mark.parametrize("name", [s for s in STYLES if STYLES[s].active])
def test_every_style_draws_sound_and_nothing_for_silence(
    qt_app: QCoreApplication, name: str
) -> None:
    cls = STYLES[name]
    style = cls(Palette(DEFAULT_SKIN))
    assert _drawn(_paint(style, _frame(0.9, cls.history))) > 50
    silence = Frame(
        bars=np.zeros(BANDS), peaks=np.zeros(BANDS), history=np.zeros((cls.history, BANDS))
    )
    if name != "meter":  # a meter's unlit segments still glow faintly
        assert _drawn(_paint(style, silence)) == 0


def test_off_draws_nothing(qt_app: QCoreApplication) -> None:
    off = STYLES["off"](Palette(DEFAULT_SKIN))
    assert not off.active
    assert _drawn(_paint(off, _frame(1.0))) == 0


def test_unknown_saved_styles_fall_back_and_cycling_wraps() -> None:
    assert style_id("no-such-style") == DEFAULT_STYLE
    assert style_id("line") == "line"
    assert next_style_id("bars") == "dots"
    assert next_style_id("off") == "bars"
    assert next_style_id("removed-one") == "dots"


@pytest.fixture
def extra_style() -> Iterator[type[Style]]:
    @register
    class Blocks(Style):
        id = "test-blocks"
        label = "Blocks"

        def paint(self, painter: QPainter, rect: QRectF, frame: Frame) -> None:
            painter.fillRect(rect, self.palette.at(frame.level))

    yield Blocks
    del STYLES[Blocks.id]


def test_a_registered_style_is_used_like_the_built_ins(
    qt_app: QCoreApplication, extra_style: type[Style]
) -> None:
    assert next_style_id("off") == "test-blocks"
    widget = SpectrumWidget(BANDS, DEFAULT_SKIN, style="test-blocks")
    assert widget.style_id == "test-blocks"
    with pytest.raises(ValueError, match="already registered"):
        register(extra_style)


def test_palette_at_follows_the_gradient() -> None:
    palette = Palette(DEFAULT_SKIN)
    assert palette.at(0.0) == QColor(DEFAULT_SKIN.spectrum[0])
    assert palette.at(1.0) == QColor(DEFAULT_SKIN.spectrum[-1])
    assert palette.at(0.5) == QColor(DEFAULT_SKIN.spectrum[1])


def _click(widget: QWidget) -> None:
    point = QPointF(5, 5)
    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        point,
        widget.mapToGlobal(point),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QCoreApplication.sendEvent(widget, event)


def test_clicking_moves_to_the_next_style(qt_app: QCoreApplication) -> None:
    widget = SpectrumWidget(BANDS, DEFAULT_SKIN, style="radial")
    seen: list[str] = []
    widget.style_changed.connect(seen.append)
    _click(widget)
    _click(widget)
    assert seen == ["waves", "oscilloscope"]
    assert widget.style_id == "oscilloscope"


def test_off_stops_animating_and_ignores_levels(qt_app: QCoreApplication) -> None:
    widget = SpectrumWidget(BANDS, DEFAULT_SKIN)
    widget.show()
    widget.set_levels([1.0] * BANDS)
    assert widget.animating
    widget.set_style("off")
    assert not widget.animating and not widget.active
    widget.set_levels([1.0] * BANDS)
    assert not widget.animating
    widget.close()


def test_the_spectrogram_scrolls_away_to_silence_then_stops(qt_app: QCoreApplication) -> None:
    widget = SpectrumWidget(BANDS, DEFAULT_SKIN, style="spectrogram")
    widget.show()
    widget.set_levels([1.0] * BANDS)
    widget._animate()
    widget.set_levels([0.0] * BANDS)
    assert widget.animating
    wait_until(lambda: not widget.animating, timeout=20)
    assert not widget._history.any()
    widget.close()


def test_the_window_offers_every_style_and_remembers_the_choice(
    app: Application,  # noqa: F811
) -> None:
    window = app.window
    assert set(window.visualiser_actions) == set(STYLES)
    window.visualiser_actions["line"].trigger()
    assert window._lcd.visualiser.style_id == "line"
    assert app.settings.visualiser_style == "line"
    _click(window._lcd.visualiser)  # line → spectrogram, and the menu follows
    assert window.visualiser_actions["spectrogram"].isChecked()
    assert app.settings.visualiser_style == "spectrogram"


def test_off_pauses_the_analysis(app: Application) -> None:  # noqa: F811
    window = app.window
    published: list[SpectrumUpdated] = []
    app.bus.subscribe(SpectrumUpdated, published.append)
    frame = AudioFrame(np.ones(2048, dtype=np.float32), 44_100)

    window.visualiser_actions["off"].trigger()
    assert window.seen and not window.visualiser_needed
    app.bus.publish(frame)
    assert not any(any(e.levels) for e in published)

    window.visualiser_actions["bars"].trigger()
    assert window.visualiser_needed
    app.bus.publish(frame)
    assert any(published[-1].levels)


def test_waves_move_on_with_each_frame(qt_app: QCoreApplication) -> None:
    waves = STYLES["waves"](Palette(DEFAULT_SKIN))
    bars = np.full(BANDS, 0.8)
    still = Frame(bars=bars, peaks=bars, history=np.zeros((0, BANDS)), tick=0)
    later = Frame(bars=bars, peaks=bars, history=np.zeros((0, BANDS)), tick=10)
    assert _paint(waves, still) == _paint(waves, still)
    assert _paint(waves, still) != _paint(waves, later)


def test_waves_keep_moving_while_the_levels_hold_still(qt_app: QCoreApplication) -> None:
    widget = SpectrumWidget(BANDS, DEFAULT_SKIN, style="waves")
    widget.resize(200, 40)
    widget.show()
    widget.set_levels([0.5] * BANDS)
    for _ in range(30):
        widget._animate()  # the bars have risen and hold still from here
    before = widget.grab().toImage()
    widget._animate()
    assert widget.animating
    assert widget.grab().toImage() != before
    widget.close()


def test_a_new_waveform_is_drawn_even_when_the_levels_hold_still(
    qt_app: QCoreApplication,
) -> None:
    widget = SpectrumWidget(BANDS, DEFAULT_SKIN, style="oscilloscope")
    widget.resize(200, 40)
    widget.show()
    levels = [0.5] * BANDS
    widget.set_levels(levels, (np.sin(np.linspace(0, 4 * np.pi, 96)) * 0.4).tolist())
    for _ in range(30):
        widget._animate()  # the bars have risen and hold still from here
    before = widget.grab().toImage()
    widget.set_levels(levels, (np.cos(np.linspace(0, 4 * np.pi, 96)) * 0.4).tolist())
    widget._animate()
    assert widget.grab().toImage() != before
    widget.set_levels([0.0] * BANDS)  # silence: no waveform
    assert not widget._wave.any()
    widget.close()
