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
"""A small UPnP ContentDirectory client: a media server's description, and browsing and
searching its music (MiniDLNA, Jellyfin, Plex, a NAS, ...). No Qt here: the library
window calls it on a worker thread.

Folders and songs come as DIDL-Lite (XML) inside SOAP replies. Songs play from the
plain http addresses the server gives, which need no login.

Replies come from any device on the network, so they're read with care: they're
capped in size, XML with a DOCTYPE or entities is refused (DIDL never needs them, and
they're how XML bombs work), and only http(s) addresses are followed or played.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape

from pyamp import APP_NAME, __version__
from pyamp.core.models import StreamUrl, Track
from pyamp.library.artwork import MAX_ARTWORK_BYTES

TIMEOUT_S = 10.0
MAX_BYTES = 20_000_000
#: Folders and songs are fetched this many at a time.
PAGE = 200
#: A folder's first this many entries are listed (a flat folder of every song, say).
MAX_ENTRIES = 10_000
CONTENT_DIRECTORY = "urn:schemas-upnp-org:service:ContentDirectory:"
CANT_SEARCH = "This server can't be searched; browse its folders"

NS = {
    "device": "urn:schemas-upnp-org:device-1-0",
    "soap": "http://schemas.xmlsoap.org/soap/envelope/",
    "control": "urn:schemas-upnp-org:control-1-0",
    "didl": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "upnp": "urn:schemas-upnp-org:metadata-1-0/upnp/",
}
#: What a search looks in, if the server can search it.
SEARCH_FIELDS = ("dc:title", "upnp:artist", "upnp:album")

_DURATION = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?$")
_UNSAFE_XML = re.compile(rb"<!(?:DOCTYPE|ENTITY)", re.IGNORECASE)


class DlnaError(Exception):
    """The server refused a request, or its reply couldn't be read."""


@dataclass(frozen=True, slots=True)
class MediaServer:
    """A media server, from its device description."""

    #: Its unique device name: the same server keeps it when its address changes.
    udn: str
    name: str
    #: The address of its device description.
    location: str
    control_url: str
    #: The ContentDirectory version it offers, for the SOAP action header.
    service_type: str

    @property
    def host(self) -> str:
        return urlsplit(self.location).netloc


@dataclass(frozen=True, slots=True)
class Folder:
    """A container: a folder, or a category such as Artists or an album."""

    id: str
    title: str
    #: How many entries it has (None: the server didn't say).
    child_count: int | None = None


def describe(location: str, *, timeout: float = TIMEOUT_S) -> MediaServer:
    """Read the device description at ``location``. Raises :class:`DlnaError` if it isn't
    a media server, ``ValueError`` for a bad address, ``OSError`` if unreachable."""
    root = _xml(_fetch(urllib.request.Request(_web(location)), timeout))
    base = (root.findtext("device:URLBase", "", NS) or "").strip() or location
    for device in root.iter(f"{{{NS['device']}}}device"):
        for service in device.iterfind("device:serviceList/device:service", NS):
            service_type = (service.findtext("device:serviceType", "", NS) or "").strip()
            if not service_type.startswith(CONTENT_DIRECTORY):
                continue
            control = (service.findtext("device:controlURL", "", NS) or "").strip()
            name = (device.findtext("device:friendlyName", "", NS) or "").strip()
            return MediaServer(
                udn=(device.findtext("device:UDN", "", NS) or "").strip() or location,
                name=name or urlsplit(location).netloc,
                location=location,
                control_url=_web(urljoin(base, control)),
                service_type=service_type,
            )
    raise DlnaError("That address isn't a media server")


class ContentDirectory:
    """Browses and searches one media server."""

    def __init__(self, server: MediaServer, *, timeout: float = TIMEOUT_S) -> None:
        self.server = server
        self._timeout = timeout

    def browse(self, folder_id: str) -> tuple[list[Folder], list[Track]]:
        """The folders and songs in a folder ("0": the top), in the server's order."""
        folders: list[Folder] = []
        tracks: list[Track] = []
        start = 0
        while start < MAX_ENTRIES:
            reply = self._call(
                "Browse",
                ObjectID=folder_id,
                BrowseFlag="BrowseDirectChildren",
                Filter="*",
                StartingIndex=start,
                RequestedCount=PAGE,
                SortCriteria="",
            )
            page_folders, page_tracks = parse_didl(reply.get("Result", ""))
            folders += page_folders
            tracks += page_tracks
            returned = _int(reply.get("NumberReturned"))
            start += returned
            if returned == 0 or start >= _int(reply.get("TotalMatches")):
                break
        return folders, tracks

    def search(self, text: str) -> list[Track]:
        """Songs whose title, artist or album has every word of ``text`` (as far as the
        server can search those). Raises :class:`DlnaError` if it can't search."""
        capabilities = self._call("GetSearchCapabilities").get("SearchCaps", "")
        known = {c.strip() for c in capabilities.split(",")}
        fields = [f for f in SEARCH_FIELDS if f in known or "*" in known]
        if not fields:
            raise DlnaError(CANT_SEARCH)
        words = [w.replace("\\", "\\\\").replace('"', '\\"') for w in text.split()]
        if not words:
            return []
        either = (" or ".join(f'{f} contains "{w}"' for f in fields) for w in words)
        criteria = 'upnp:class derivedfrom "object.item.audioItem"' + "".join(
            f" and ({fields_match})" for fields_match in either
        )
        reply = self._call(
            "Search",
            ContainerID="0",
            SearchCriteria=criteria,
            Filter="*",
            StartingIndex=0,
            RequestedCount=PAGE,
            SortCriteria="",
        )
        return parse_didl(reply.get("Result", ""))[1]

    def artwork(self, url: str) -> bytes:
        """The picture at ``url`` (a song's album art address), as image bytes."""
        request = urllib.request.Request(_web(url))
        request.add_header("User-Agent", f"{APP_NAME}/{__version__} UPnP/1.0")
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            image = response.headers.get_content_type().startswith("image/")
            data: bytes = response.read(MAX_ARTWORK_BYTES + 1)
        if not image:
            raise DlnaError("That isn't a picture")
        if len(data) > MAX_ARTWORK_BYTES:
            raise DlnaError("The picture was too large")
        return data

    def _call(self, action: str, **arguments: object) -> dict[str, str]:
        """Run a ContentDirectory action; its reply's values by name."""
        service = self.server.service_type
        values = "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in arguments.items())
        body = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<s:Envelope xmlns:s="{NS["soap"]}" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
            f'<s:Body><u:{action} xmlns:u="{service}">{values}</u:{action}></s:Body>'
            "</s:Envelope>"
        )
        request = urllib.request.Request(
            self.server.control_url,
            data=body.encode("utf-8"),
            headers={
                "Content-Type": 'text/xml; charset="utf-8"',
                "SOAPAction": f'"{service}#{action}"',
            },
        )
        try:
            data = _fetch(request, self._timeout)
        except urllib.error.HTTPError as exc:  # a SOAP fault comes as a 500
            data = exc.read(MAX_BYTES + 1)
            exc.close()
            _raise_fault(data, exc.code)
            raise
        body_element = _xml(data).find("soap:Body", NS)
        response = next(iter(body_element), None) if body_element is not None else None
        if response is None:
            raise DlnaError("The server's reply couldn't be read")
        if response.tag == f"{{{NS['soap']}}}Fault":
            _raise_fault(data, 500)
        return {child.tag.rpartition("}")[2]: child.text or "" for child in response}


def parse_didl(text: str) -> tuple[list[Folder], list[Track]]:
    """The folders and songs in a DIDL-Lite document (other items, such as videos and
    pictures, are left out)."""
    if not text.strip():
        return [], []
    root = _xml(text.encode("utf-8"))
    folders = [
        Folder(c.get("id", ""), _text(c, "dc:title"), _count(c.get("childCount")))
        for c in root.iterfind("didl:container", NS)
    ]
    tracks = [t for t in (_track(i) for i in root.iterfind("didl:item", NS)) if t is not None]
    return folders, tracks


def parse_duration(value: str | None) -> int:
    """``H:MM:SS.fff`` (or ``MM:SS``) in milliseconds; 0 if it isn't one."""
    match = _DURATION.match((value or "").strip())
    if match is None:
        return 0
    hours, minutes, seconds, fraction = match.groups()
    millis = int((fraction or "0")[:3].ljust(3, "0"))
    return ((int(hours or 0) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + millis


def describe_error(error: BaseException) -> str:
    """A short message for the user."""
    if isinstance(error, DlnaError | ValueError):
        return str(error)
    if isinstance(error, OSError):
        reason = getattr(error, "reason", None) or error.strerror or error
        return f"Could not reach the server: {reason}"
    return f"Something went wrong: {error}"


def _track(item: ET.Element) -> Track | None:
    kind = _text(item, "upnp:class")
    resources = [r for r in item.iterfind("didl:res", NS) if _is_web(r.text)]
    audio = [r for r in resources if _mime(r.get("protocolInfo")).startswith("audio/")]
    if not audio and kind.startswith("object.item.audioItem"):
        audio = resources  # an audio item whose file type the server didn't give
    if not audio:
        return None
    resource = audio[0]
    year = re.match(r"\d{4}", _text(item, "dc:date"))
    return Track(
        location=StreamUrl((resource.text or "").strip()),
        title=_text(item, "dc:title") or "Untitled",
        artist=_text(item, "upnp:artist") or _text(item, "dc:creator"),
        album=_text(item, "upnp:album"),
        genre=_text(item, "upnp:genre"),
        artwork=_album_art(item),
        year=int(year.group()) if year else 0,
        track_number=_int(_text(item, "upnp:originalTrackNumber")),
        duration_ms=parse_duration(resource.get("duration")),
    )


def _album_art(item: ET.Element) -> str:
    """The address of the item's album art, if it gives an http(s) one."""
    addresses = ((art.text or "").strip() for art in item.iterfind("upnp:albumArtURI", NS))
    return next((url for url in addresses if _is_web(url)), "")


def _mime(protocol_info: str | None) -> str:
    """The file type in a resource's protocolInfo (``http-get:*:audio/mpeg:...``)."""
    parts = (protocol_info or "").split(":")
    return parts[2].strip().lower() if len(parts) > 2 and parts[0] == "http-get" else ""


def _fetch(request: urllib.request.Request, timeout: float) -> bytes:
    request.add_header("User-Agent", f"{APP_NAME}/{__version__} UPnP/1.0")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise DlnaError("The server's reply was too large")
    return data


def _xml(data: bytes) -> ET.Element:
    if _UNSAFE_XML.search(data):
        raise DlnaError("The server's reply couldn't be read")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise DlnaError("The server's reply couldn't be read") from None


def _raise_fault(data: bytes, status: int) -> None:
    """Raise the UPnP error a SOAP fault describes, if ``data`` is one."""
    try:
        root = _xml(data)
    except DlnaError:
        return
    error = root.find(".//control:UPnPError", NS)
    if error is not None:
        message = (error.findtext("control:errorDescription", "", NS) or "").strip()
        code = (error.findtext("control:errorCode", "", NS) or "").strip()
        raise DlnaError(message or f"The server refused the request (error {code or status})")


def _web(url: str) -> str:
    """``url`` if it's http(s): a server mustn't point PyAmp at local files."""
    if not _is_web(url):
        raise ValueError("The address must start with http:// or https://")
    return url.strip()


def _is_web(url: str | None) -> bool:
    parts = urlsplit((url or "").strip())
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def _text(element: ET.Element, path: str) -> str:
    return (element.findtext(path, "", NS) or "").strip()


def _int(value: object) -> int:
    try:
        return max(int(str(value).strip()), 0)
    except ValueError:
        return 0


def _count(value: str | None) -> int | None:
    return _int(value) if value is not None and value.strip().isdigit() else None
