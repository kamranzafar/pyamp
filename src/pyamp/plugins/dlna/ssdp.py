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
"""Finding UPnP media servers on the local network with SSDP. No Qt here.

A search is multicast to 239.255.255.250:1900, and each media server answers (to us
alone) with the address of its device description in a ``LOCATION`` header. A search
sent straight to one host finds the server there, for adding one by its address.
"""

from __future__ import annotations

import logging
import socket
import time

log = logging.getLogger(__name__)

#: Where SSDP searches are multicast.
SSDP_ADDRESS = ("239.255.255.250", 1900)
#: What to search for: media servers (DLNA servers are UPnP MediaServers).
MEDIA_SERVER = "urn:schemas-upnp-org:device:MediaServer:1"
#: How long to listen for answers by default, in seconds.
TIMEOUT_S = 2.5
#: Each search is sent this often: UDP may lose one.
SENDS = 2


def search_request(target: str = MEDIA_SERVER, wait_s: int = 2) -> bytes:
    """An M-SEARCH for ``target``; servers answer within ``wait_s`` seconds (MX)."""
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDRESS[0]}:{SSDP_ADDRESS[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {wait_s}\r\n"
        f"ST: {target}\r\n"
        "\r\n"
    ).encode("ascii")


def parse_response(data: bytes) -> str | None:
    """The description address in an answer to a search; None if it isn't one, or the
    address isn't http(s)."""
    lines = data.decode("utf-8", "replace").splitlines()
    if not lines or not lines[0].upper().startswith("HTTP/1.1 200"):
        return None  # e.g. another device's search, or a NOTIFY
    for line in lines[1:]:
        name, colon, value = line.partition(":")
        if colon and name.strip().lower() == "location":
            location = value.strip()
            return location if location.lower().startswith(("http://", "https://")) else None
    return None


def discover(timeout: float = TIMEOUT_S, target: tuple[str, int] = SSDP_ADDRESS) -> list[str]:
    """The description addresses of the media servers that answer within ``timeout``
    seconds, in the order they answered. Blocks for the whole ``timeout`` (unless the
    network can't be used: then nothing is found)."""
    locations: list[str] = []
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        request = search_request(wait_s=max(1, int(timeout)))
        try:
            for _ in range(SENDS):
                sock.sendto(request, target)
        except OSError as exc:  # no network, or the host's name is unknown
            log.info("Could not search for DLNA servers: %s", exc)
            return locations
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                data, _ = sock.recvfrom(65507)
            except TimeoutError:
                break
            except OSError as exc:  # e.g. Windows reports an unreachable host here
                log.info("Searching for DLNA servers stopped: %s", exc)
                break
            location = parse_response(data)
            if location is not None and location not in locations:
                locations.append(location)
    return locations
