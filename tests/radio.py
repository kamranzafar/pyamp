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
"""A pretend internet radio station on localhost, for the stream tests (no internet).

It answers like Icecast/Shoutcast: if the player asks for ``Icy-MetaData``, the audio
is interleaved every ``METAINT`` bytes with a metadata block carrying
``StreamTitle='<song>';``, the way stations announce what's playing.
"""

from __future__ import annotations

import http.server
import io
import threading
import time
import wave
from types import TracebackType

import numpy as np

METAINT = 4096
#: tone_wav's bytes per second of audio (16-bit mono, 22.05 kHz).
BYTES_PER_S = 22050 * 2


def tone_wav(seconds: float, rate: int = 22050, hz: float = 440.0) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    samples = (np.sin(2 * np.pi * hz * t) * 8000).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buffer.getvalue()


def _metadata_block(title: str) -> bytes:
    text = f"StreamTitle='{title}';".encode()
    blocks = -(-len(text) // 16)  # length in 16-byte units, rounded up
    return bytes([blocks]) + text.ljust(blocks * 16, b"\0")


class RadioStation:
    """Serves ``/live`` (ICY stream, ``title`` as its current song) and ``/plain.wav``."""

    def __init__(
        self, title: str = "Test Artist - Test Song", seconds: float = 4.0, *, paced: bool = False
    ) -> None:
        """``paced``: send ``/live`` at playing speed (a little ahead), like a real station."""
        self.title = title
        self.requests: list[str] = []  # the paths asked for, in order
        audio = tone_wav(seconds)
        station = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:
                station.requests.append(self.path)
                if self.path == "/plain.wav":
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(audio)))
                    self.end_headers()
                    self.wfile.write(audio)
                    return
                if self.path != "/live":
                    self.send_error(404)
                    return
                icy = self.headers.get("Icy-MetaData") == "1"
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("icy-name", "PyAmp Test Radio")
                if icy:
                    self.send_header("icy-metaint", str(METAINT))
                self.end_headers()
                began = time.monotonic()
                try:
                    for start in range(0, len(audio), METAINT):
                        while paced and start / BYTES_PER_S - (time.monotonic() - began) > 2:
                            time.sleep(0.02)  # stay about two seconds ahead of playback
                        self.wfile.write(audio[start : start + METAINT])
                        if icy:
                            self.wfile.write(_metadata_block(station.title))
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the player hung up

            def log_message(self, *_: object) -> None:
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def __enter__(self) -> RadioStation:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()
