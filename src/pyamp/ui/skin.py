"""Skins: every colour the UI uses, in one place.

Widgets receive a :class:`Skin` instead of hard-coding colours. That is the
seam for future skinning work, e.g. a loader that builds a ``Skin`` (plus
bitmaps) from a classic ``.wsz`` file.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Skin:
    name: str = "PyAmp Classic"
    window: str = "#23232f"
    panel: str = "#000000"
    border: str = "#4a4a5e"
    text: str = "#c9c9d6"
    text_dim: str = "#6f6f84"
    lcd: str = "#00e83a"
    accent: str = "#ffffff"
    selection: str = "#1c3c8c"
    playlist_text: str = "#00e83a"
    playlist_current: str = "#ffffff"
    #: Spectrum gradient, bottom to top.
    spectrum: tuple[str, ...] = ("#18a818", "#d8d820", "#e83010")
    spectrum_peak: str = "#9a9aa8"

    def stylesheet(self) -> str:
        return f"""
        QMainWindow, QWidget#central {{ background: {self.window}; color: {self.text}; }}
        QFrame#lcdPanel {{
            background: {self.panel}; border: 1px solid {self.border}; border-radius: 2px;
        }}
        QLabel {{ color: {self.text}; }}
        QToolButton {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a4a5c, stop:1 #2c2c38);
            color: {self.text}; border: 1px solid #15151c; border-radius: 2px;
            min-width: 26px; min-height: 18px; padding: 1px 4px;
        }}
        QToolButton:hover {{ color: {self.accent}; }}
        QToolButton:pressed, QToolButton:checked {{
            background: #1a1a22; color: {self.lcd};
        }}
        QSlider::groove:horizontal {{
            height: 6px; background: #111118; border: 1px solid {self.border};
        }}
        QSlider::sub-page:horizontal {{ background: #3b5f2b; }}
        QSlider::handle:horizontal {{
            width: 14px; margin: -4px 0; border-radius: 2px;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #d8d0a0, stop:1 #8a8060);
        }}
        QTableView {{
            background: {self.panel}; color: {self.playlist_text};
            selection-background-color: {self.selection}; selection-color: {self.playlist_text};
            border: 1px solid {self.border}; outline: 0;
        }}
        QStatusBar {{ color: {self.text_dim}; }}
        QMenu {{ background: {self.window}; color: {self.text}; }}
        QMenu::item:selected {{ background: {self.selection}; }}
        """


DEFAULT_SKIN = Skin()
