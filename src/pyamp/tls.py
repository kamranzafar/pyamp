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

"""Verifying https certificates against the system's certificate authorities.

Python's ``ssl`` looks for them where the OpenSSL it was built with keeps them. In a
packaged PyAmp that's the build machine's (e.g. Homebrew's ``/usr/local/etc/openssl@3``),
which another Mac doesn't have: every https request would fail ("unable to get local
issuer certificate"). So where OpenSSL's own aren't there, the system's are used: the
ones macOS ships (kept current by its updates, as mpv uses too), or a Linux
distribution's. Windows' own store is read by Python already.
"""

from __future__ import annotations

import functools
import logging
import os
import ssl
import sys
from pathlib import Path

log = logging.getLogger(__name__)

#: The certificate authorities macOS ships, kept current by its updates.
MACOS_CA_FILE = Path("/etc/ssl/cert.pem")
#: Where systems keep theirs, macOS's first (also Alpine's), then Linux distributions'.
SYSTEM_CA_FILES = (
    MACOS_CA_FILE,
    Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian, Ubuntu, Arch
    Path("/etc/pki/tls/certs/ca-bundle.crt"),  # Fedora, RHEL
    Path("/etc/ssl/ca-bundle.pem"),  # openSUSE
)


def _openssl_has_its_own() -> bool:
    """Whether the certificates OpenSSL looks for (or SSL_CERT_FILE/DIR names) are there."""
    paths = ssl.get_default_verify_paths()
    if paths.cafile and Path(paths.cafile).is_file():
        return True
    return bool(paths.capath and Path(paths.capath).is_dir() and os.listdir(paths.capath))


def system_ca_file(candidates: tuple[Path, ...] = SYSTEM_CA_FILES) -> Path | None:
    return next((path for path in candidates if path.is_file()), None)


def create_context(
    platform: str = sys.platform, candidates: tuple[Path, ...] = SYSTEM_CA_FILES
) -> ssl.SSLContext:
    """An https context that verifies certificates (as Python's default), against the
    system's certificate authorities where OpenSSL has none of its own."""
    context = ssl.create_default_context()
    if platform != "win32" and not _openssl_has_its_own():
        found = system_ca_file(candidates)
        if found is not None:
            context.load_verify_locations(cafile=str(found))
        else:
            log.warning("No certificate authorities found: https connections will fail")
    return context


@functools.cache
def https_context() -> ssl.SSLContext:
    """The context for PyAmp's https requests (made once: loading them takes a moment)."""
    return create_context()
