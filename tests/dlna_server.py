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
"""A pretend DLNA/UPnP media server on localhost, for the DLNA plugin's tests (no network).

It answers SSDP searches sent to its UDP port, serves its device description, and
answers ContentDirectory Browse, Search and GetSearchCapabilities over SOAP from a
small tree::

    0 ─┬─ 1 Music ── 11 Albums ── al1 Mezzanine: Teardrop, Angel (+ a video, a picture)
       ├─ 2 Videos (empty)
       └─ many: 450 songs (served a page at a time)
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
import xml.etree.ElementTree as ET
from types import TracebackType
from xml.sax.saxutils import escape, quoteattr

CONTENT_DIRECTORY = "urn:schemas-upnp-org:service:ContentDirectory:1"
UDN = "uuid:4d696e69-444c-164e-9d41-001122334455"
MANY = 450

_DIDL = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">{}</DIDL-Lite>'
)


#: The album art the server serves for Mezzanine.
ALBUM_ART = b"\xff\xd8mezzanine-art"


def container(id_: str, title: str, count: int | None) -> str:
    counted = f' childCount="{count}"' if count is not None else ""
    return (
        f'<container id="{id_}" parentID="0" restricted="1"{counted}>'
        f"<dc:title>{escape(title)}</dc:title>"
        "<upnp:class>object.container.storageFolder</upnp:class></container>"
    )


class DlnaServer:
    def __init__(
        self,
        name: str = "Living Room NAS",
        udn: str = UDN,
        *,
        searchable: bool = True,
        url_base: bool = False,
        description_path: str = "/description.xml",
    ) -> None:
        """``description_path``: where its device description is (MiniDLNA's is
        ``/rootDesc.xml``)."""
        self.name = name
        self.udn = udn
        self.searchable = searchable
        self.requests: list[tuple[str, dict[str, str]]] = []  # (action, arguments)
        self.searches = 0  # SSDP searches answered
        self.unsafe = False  # reply with an XML bomb's DOCTYPE
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_GET(self) -> None:
                if self.path == description_path:
                    self._send(server.description(url_base).encode(), "text/xml")
                elif self.path.startswith("/media/"):
                    self._send(b"RIFF....WAVE", "audio/wav")
                elif self.path == "/art/al1.jpg":
                    self._send(ALBUM_ART, "image/jpeg")
                elif self.path == "/art/page.html":
                    self._send(b"<html></html>", "text/html")
                else:
                    self.send_error(404)

            def do_POST(self) -> None:
                if self.path != "/ctl/ContentDir":
                    self.send_error(404)
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                action = self.headers.get("SOAPAction", "").strip('"').rpartition("#")[2]
                arguments = server.arguments(body)
                server.requests.append((action, arguments))
                reply = server.reply(action, arguments)
                if reply is None:
                    self._send(server.fault(701, "No such object").encode(), "text/xml", 500)
                    return
                values = "".join(f"<{k}>{escape(v)}</{k}>" for k, v in reply.items())
                envelope = (
                    '<?xml version="1.0"?><s:Envelope '
                    'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                    f'<u:{action}Response xmlns:u="{CONTENT_DIRECTORY}">{values}'
                    f"</u:{action}Response></s:Body></s:Envelope>"
                )
                if server.unsafe:
                    envelope = '<!DOCTYPE lol [<!ENTITY lol "lol">]>' + envelope
                self._send(envelope.encode(), "text/xml")

            def _send(self, data: bytes, content_type: str, status: int = 200) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_: object) -> None:
                pass

        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self.location = f"{self.url}{description_path}"
        self.host = self.url.removeprefix("http://")
        threading.Thread(
            target=self._httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp.bind(("127.0.0.1", 0))
        self.ssdp_target = ("127.0.0.1", self._udp.getsockname()[1])
        threading.Thread(target=self._answer_searches, daemon=True).start()

    # -- SSDP ------------------------------------------------------------------ #

    def _answer_searches(self) -> None:
        while True:
            try:
                data, sender = self._udp.recvfrom(65507)
            except OSError:
                return  # closed
            if not data.startswith(b"M-SEARCH") or b"MediaServer" not in data:
                continue
            self.searches += 1
            self._udp.sendto(b"NOTIFY * HTTP/1.1\r\nLOCATION: http://ignored/\r\n\r\n", sender)
            answer = (
                "HTTP/1.1 200 OK\r\n"
                "CACHE-CONTROL: max-age=1800\r\n"
                f"ST: urn:schemas-upnp-org:device:MediaServer:1\r\n"
                f"Location: {self.location}\r\n"
                f"USN: {self.udn}::urn:schemas-upnp-org:device:MediaServer:1\r\n\r\n"
            )
            self._udp.sendto(answer.encode(), sender)

    # -- Device description ------------------------------------------------------ #

    def description(self, url_base: bool) -> str:
        base = f"<URLBase>{self.url}/</URLBase>" if url_base else ""
        control = "ctl/ContentDir" if url_base else "/ctl/ContentDir"
        return (
            '<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">'
            f"{base}<device>"
            "<deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>"
            f"<friendlyName>{escape(self.name)}</friendlyName><UDN>{self.udn}</UDN>"
            "<serviceList>"
            "<service><serviceType>urn:schemas-upnp-org:service:ConnectionManager:1"
            "</serviceType><controlURL>/ctl/ConnectionMgr</controlURL></service>"
            f"<service><serviceType>{CONTENT_DIRECTORY}</serviceType>"
            f"<controlURL>{control}</controlURL></service>"
            "</serviceList></device></root>"
        )

    # -- ContentDirectory ---------------------------------------------------------- #

    @staticmethod
    def arguments(body: bytes) -> dict[str, str]:
        root = ET.fromstring(body)
        action = next(iter(next(iter(root))))  # Envelope → Body → the action
        return {child.tag: child.text or "" for child in action}

    def reply(self, action: str, arguments: dict[str, str]) -> dict[str, str] | None:
        if action == "GetSearchCapabilities":
            return {"SearchCaps": "dc:title,upnp:artist,upnp:class" if self.searchable else ""}
        if action == "Search":
            return self.search(arguments["SearchCriteria"])
        if action != "Browse":
            return None
        entries = self.children(arguments["ObjectID"])
        if entries is None:
            return None
        start = int(arguments["StartingIndex"])
        page = entries[start : start + int(arguments["RequestedCount"])]
        return {
            "Result": _DIDL.format("".join(page)),
            "NumberReturned": str(len(page)),
            "TotalMatches": str(len(entries)),
            "UpdateID": "1",
        }

    def search(self, criteria: str) -> dict[str, str]:
        words = [w.lower() for w in re.findall(r'contains "([^"]*)"', criteria)]
        found = [
            didl
            for title, artist, didl in self.songs()
            if all(w in f"{title} {artist}".lower() for w in words)
        ]
        return {
            "Result": _DIDL.format("".join(found)),
            "NumberReturned": str(len(found)),
            "TotalMatches": str(len(found)),
        }

    def children(self, object_id: str) -> list[str] | None:  # noqa: PLR0911
        match object_id:
            case "0":
                return [
                    container("1", "Music", 1),
                    container("2", "Videos", 0),
                    container("many", "All Songs", None),
                ]
            case "1":
                return [container("11", "Albums", 1)]
            case "11":
                return [container("al1", "Mezzanine", 4)]
            case "al1":
                return [xml for _, _, xml in self.songs()] + [self.video(), self.picture()]
            case "2":
                return []
            case "many":
                return [self.song(f"m{n}", f"Song {n}", "Various") for n in range(MANY)]
        return None

    def songs(self) -> list[tuple[str, str, str]]:
        """(title, artist, DIDL) of the album's songs."""
        teardrop = self.song(
            "s1",
            "Teardrop",
            "Massive Attack",
            extra="<upnp:album>Mezzanine</upnp:album><upnp:genre>Trip Hop</upnp:genre>"
            "<upnp:originalTrackNumber>1</upnp:originalTrackNumber>"
            "<dc:date>1998-04-20</dc:date>"
            # a server mustn't point PyAmp at a local file: the next one is taken
            "<upnp:albumArtURI>file:///etc/passwd</upnp:albumArtURI>"
            f"<upnp:albumArtURI>{self.url}/art/al1.jpg</upnp:albumArtURI>",
            duration="0:05:30.250",
        )
        angel = (  # the artist only as dc:creator; a FLAC; no hours in its length
            '<item id="s2" parentID="al1" restricted="1"><dc:title>Angel</dc:title>'
            "<dc:creator>Massive Attack</dc:creator>"
            "<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
            '<res protocolInfo="http-get:*:image/jpeg:*">'
            f"{self.url}/media/s2.jpg</res>"
            '<res protocolInfo="http-get:*:audio/x-flac:*" duration="6:19">'
            f"{self.url}/media/s2.flac</res></item>"
        )
        return [
            ("Teardrop", "Massive Attack", teardrop),
            ("Angel", "Massive Attack", angel),
        ]

    def song(
        self, id_: str, title: str, artist: str, *, extra: str = "", duration: str = "0:03:00"
    ) -> str:
        return (
            f'<item id="{id_}" parentID="al1" restricted="1">'
            f"<dc:title>{escape(title)}</dc:title><upnp:artist>{escape(artist)}</upnp:artist>"
            f"{extra}<upnp:class>object.item.audioItem.musicTrack</upnp:class>"
            f'<res protocolInfo="http-get:*:audio/mpeg:DLNA.ORG_PN=MP3" '
            f'duration="{duration}">{self.url}/media/{id_}.mp3</res></item>'
        )

    def video(self) -> str:
        return (
            '<item id="v1" parentID="al1" restricted="1"><dc:title>Making Of</dc:title>'
            "<upnp:class>object.item.videoItem</upnp:class>"
            f'<res protocolInfo="http-get:*:video/mp4:*">{self.url}/media/v1.mp4</res></item>'
        )

    def picture(self) -> str:
        return (
            '<item id="i1" parentID="al1" restricted="1"><dc:title>Cover</dc:title>'
            "<upnp:class>object.item.imageItem.photo</upnp:class>"
            f"<res protocolInfo={quoteattr('http-get:*:image/jpeg:*')}>"
            f"{self.url}/media/i1.jpg</res></item>"
        )

    @staticmethod
    def fault(code: int, description: str) -> str:
        return (
            '<?xml version="1.0"?><s:Envelope '
            'xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><s:Fault>'
            "<faultcode>s:Client</faultcode><faultstring>UPnPError</faultstring><detail>"
            '<UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
            f"<errorCode>{code}</errorCode><errorDescription>{description}"
            "</errorDescription></UPnPError></detail></s:Fault></s:Body></s:Envelope>"
        )

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._udp.close()

    def __enter__(self) -> DlnaServer:
        return self

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _exc: BaseException | None,
        _tb: TracebackType | None,
    ) -> None:
        self.close()
