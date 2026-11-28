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
"""Downloading small text files (remote playlists such as a radio station's ``.pls``)."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from email.message import Message
from typing import Any

from pyamp import APP_NAME, __version__
from pyamp.tls import https_context

#: Give up on a remote playlist after this long, or when it's this big (it should be tiny).
TIMEOUT_S = 10.0
MAX_BYTES = 1_000_000
#: How much of an HTTP error's body is kept (e.g. a SOAP fault, a JSON error).
ERROR_BODY_BYTES = 64 * 1024


@contextmanager
def open_url(request: urllib.request.Request | str, timeout: float) -> Iterator[Any]:
    """``urllib.request.urlopen``, checking https certificates against the system's (see
    :mod:`pyamp.tls`), except that an HTTP error's connection is closed at once: urllib
    leaves its socket open until the error is garbage-collected. The error raised in its
    place still has the start of its body to read."""
    try:
        response = urllib.request.urlopen(request, timeout=timeout, context=https_context())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(ERROR_BODY_BYTES)
        except OSError:
            body = b""
        finally:
            exc.close()
        raise urllib.error.HTTPError(
            exc.url, exc.code, exc.msg, exc.hdrs, io.BytesIO(body)
        ) from None
    with response:
        yield response


def fetch_text(url: str, *, timeout: float = TIMEOUT_S, max_bytes: int = MAX_BYTES) -> str:
    """The text at an http(s) URL. Raises ``OSError`` (incl. HTTP errors) or ``ValueError``."""
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"not an http(s) URL: {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{__version__}"})
    with open_url(request, timeout) as response:
        data = response.read(max_bytes + 1)
        headers: Message = response.headers
    if len(data) > max_bytes:
        raise ValueError(f"{url} is larger than {max_bytes} bytes; not a playlist")
    charset = headers.get_content_charset() or "utf-8"
    return bytes(data).decode(charset, errors="replace")
