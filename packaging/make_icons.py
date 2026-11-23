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
"""Generate the platform icon files from ``src/pyamp/resources/icon.svg``.

    uv run python packaging/make_icons.py

Writes ``packaging/macos/PyAmp.icns`` (needs macOS's ``iconutil``),
``packaging/windows/PyAmp.ico`` and ``packaging/linux/pyamp.png``. The outputs are
committed, so run this only after changing the SVG.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "pyamp" / "resources" / "icon.svg"
ICNS = ROOT / "packaging" / "macos" / "PyAmp.icns"
ICO = ROOT / "packaging" / "windows" / "PyAmp.ico"
PNG = ROOT / "packaging" / "linux" / "pyamp.png"

#: macOS iconset: (points, scale) pairs iconutil expects.
ICONSET = [
    (16, 1),
    (16, 2),
    (32, 1),
    (32, 2),
    (128, 1),
    (128, 2),
    (256, 1),
    (256, 2),
    (512, 1),
    (512, 2),
]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]


def render(renderer: QSvgRenderer, size: int) -> bytes:
    """The SVG as a square ``size``-pixel PNG with a transparent background."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(data.data())


def write_ico(path: Path, pngs: dict[int, bytes]) -> None:
    """An .ico holding one PNG per size (Windows Vista and later read PNG entries)."""
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(header) + 16 * len(pngs)
    entries, images = b"", b""
    for size, png in sorted(pngs.items()):
        side = 0 if size >= 256 else size  # 0 means 256 in the ICO format
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(png), offset)
        images += png
        offset += len(png)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + images)


def write_icns(path: Path, renderer: QSvgRenderer) -> None:
    iconutil = shutil.which("iconutil")
    if iconutil is None:
        print(f"skipping {path.name}: iconutil (macOS only) not found", file=sys.stderr)
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "PyAmp.iconset"
        iconset.mkdir()
        for points, scale in ICONSET:
            suffix = "" if scale == 1 else "@2x"
            (iconset / f"icon_{points}x{points}{suffix}.png").write_bytes(
                render(renderer, points * scale)
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([iconutil, "-c", "icns", str(iconset), "-o", str(path)], check=True)


def main() -> None:
    app = QGuiApplication([])  # noqa: F841  (QPainter needs an application object)
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise SystemExit(f"invalid SVG: {SOURCE}")
    write_icns(ICNS, renderer)
    write_ico(ICO, {size: render(renderer, size) for size in ICO_SIZES})
    PNG.parent.mkdir(parents=True, exist_ok=True)
    PNG.write_bytes(render(renderer, 512))
    for path in (ICNS, ICO, PNG):
        if path.exists():
            print(f"{path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
