"""FieldDesk server intake bounds.

The POST form read used to trust Content-Length completely: no size cap
(an unauthenticated client could make the process read arbitrary bytes
into memory) and no timeout (a body that never finishes arriving pins a
handler thread; a negative length reads until EOF). These tests pin the
cap, the rejection, and the timeout wiring.
"""

from __future__ import annotations

import http.client
import socket
from urllib.parse import urlencode, urlparse

from packages.dispatch import mock_portal_server
from packages.dispatch.mock_portal_server import DEMO_EMAIL, DEMO_PASSWORD

# The cap the server enforces; the fallback keeps this test runnable
# against pre-fix code (where no cap exists and the post simply sails
# through to a 401) so the failure there is behavioral, not an import error.
MAX_FORM_BYTES = getattr(mock_portal_server, "MAX_FORM_BYTES", 64 * 1024)


def _port(base_url: str) -> int:
    return urlparse(base_url).port


def test_oversized_post_body_rejected_and_server_survives():
    server, base_url = mock_portal_server.serve()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", _port(base_url),
                                          timeout=10)
        conn.request("POST", "/login", body=b"x" * (MAX_FORM_BYTES + 1),
                     headers={"content-type":
                              "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 413
        conn.close()

        # The rejection is not a crash: a normal login still works.
        conn = http.client.HTTPConnection("127.0.0.1", _port(base_url),
                                          timeout=10)
        conn.request("POST", "/login",
                     body=urlencode({"email": DEMO_EMAIL,
                                     "password": DEMO_PASSWORD}),
                     headers={"content-type":
                              "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        resp.read()
        assert resp.status == 303
        conn.close()
    finally:
        server.shutdown()


def test_negative_content_length_rejected():
    server, base_url = mock_portal_server.serve()
    try:
        with socket.create_connection(("127.0.0.1", _port(base_url)),
                                      timeout=10) as sock:
            sock.sendall(b"POST /login HTTP/1.0\r\n"
                         b"Host: 127.0.0.1\r\n"
                         b"Content-Type: application/x-www-form-urlencoded\r\n"
                         b"Content-Length: -5\r\n\r\n")
            sock.shutdown(socket.SHUT_WR)
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        assert b" 413 " in response.split(b"\r\n", 1)[0]
    finally:
        server.shutdown()


def test_handler_applies_a_request_timeout():
    # socketserver applies Handler.timeout to every connection, so a
    # stalled body read times out instead of pinning the thread forever.
    assert mock_portal_server.Handler.timeout is not None
    assert mock_portal_server.Handler.timeout > 0
