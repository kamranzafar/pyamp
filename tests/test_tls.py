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

"""https certificates are checked against the system's, even in a packaged PyAmp whose
OpenSSL looks for them where the build machine kept them."""

from __future__ import annotations

import logging
import ssl
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from pyamp import tls
from pyamp.library import remote


@pytest.fixture
def bare(monkeypatch: pytest.MonkeyPatch) -> None:
    """Contexts start with no certificate authorities (as on a Mac without Homebrew's)."""
    monkeypatch.setattr(
        ssl, "create_default_context", lambda: ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    )


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    """A certificate bundle, like the system's, from what this machine trusts."""
    machine = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # not create_default_context: see bare
    machine.load_default_certs()
    known = machine.get_ca_certs(binary_form=True)
    if not known:
        pytest.skip("no certificate authorities on this machine to copy")
    path = tmp_path / "cert.pem"
    path.write_text("".join(ssl.DER_cert_to_PEM_cert(der) for der in known[:3]))
    return path


def authorities(context: ssl.SSLContext) -> int:
    return context.cert_store_stats()["x509_ca"]


@pytest.mark.usefixtures("bare")
def test_without_opensslss_own_the_systems_are_used(
    monkeypatch: pytest.MonkeyPatch, bundle: Path, tmp_path: Path
) -> None:
    monkeypatch.setattr(tls, "_openssl_has_its_own", lambda: False)
    context = tls.create_context("darwin", (tmp_path / "missing.pem", bundle))
    assert authorities(context) == 3
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname


@pytest.mark.usefixtures("bare")
def test_opensslss_own_are_left_as_they_are(monkeypatch: pytest.MonkeyPatch, bundle: Path) -> None:
    monkeypatch.setattr(tls, "_openssl_has_its_own", lambda: True)
    assert authorities(tls.create_context("darwin", (bundle,))) == 0
    monkeypatch.setattr(tls, "_openssl_has_its_own", lambda: False)
    assert authorities(tls.create_context("win32", (bundle,))) == 0  # Windows' store


@pytest.mark.usefixtures("bare")
def test_with_none_anywhere_it_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(tls, "_openssl_has_its_own", lambda: False)
    with caplog.at_level(logging.WARNING):
        tls.create_context("linux", (tmp_path / "missing.pem",))
    assert "No certificate authorities" in caplog.text


def test_where_openssl_looks_is_checked(
    monkeypatch: pytest.MonkeyPatch, bundle: Path, tmp_path: Path
) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert tls._openssl_has_its_own()
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "missing.pem"))  # the build machine's
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "missing"))
    assert not tls._openssl_has_its_own()


def test_requests_use_the_context(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[Any] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            pass

    def urlopen(request: object, timeout: float, context: ssl.SSLContext) -> Response:
        used.append(context)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with remote.open_url("https://example.invalid/", 1.0):
        pass
    assert used == [tls.https_context()]
