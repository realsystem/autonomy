"""The WebSocket client against the standard, not against our own server."""

import base64
import re
import socket
import threading
from pathlib import Path

import pytest

from autonomy_core import wsclient

# RFC 6455 section 1.3: the key and the answer that the standard itself gives as its example.
RFC_KEY = base64.b64encode(b"the sample nonce").decode()  # built, so no secret scanner mistakes it for a key
RFC_ACCEPT = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
REPO = Path(__file__).resolve().parents[3]


def test_accept_matches_the_example_in_the_rfc():
    assert wsclient._accept(RFC_KEY) == RFC_ACCEPT


def test_the_lab_stand_in_uses_the_same_constant_as_the_standard():
    # The stand-in once shared the client's wrong constant, so the lab could not notice it.
    stub = (REPO / "ops/lab/farm/ha-stub/server.py").read_text()
    guid = re.search(r'GUID = b"([^"]+)"', stub).group(1)
    assert guid == "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _serve_once(reply: bytes):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        conn.recv(4096)
        conn.sendall(reply)
        conn.close()
        srv.close()

    threading.Thread(target=run, daemon=True).start()
    return srv.getsockname()[1]


def test_a_wrong_accept_is_refused_and_says_what_arrived():
    port = _serve_once(
        b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: upgrade\r\n"
        b"Sec-WebSocket-Accept: AAAA\r\n\r\n"
    )
    with pytest.raises(wsclient.WsError, match="bad Sec-WebSocket-Accept.*AAAA"):
        wsclient.WebSocket(f"ws://127.0.0.1:{port}/x", timeout=3)


def test_header_name_case_and_spacing_do_not_matter(monkeypatch):
    # aiohttp writes "Sec-WebSocket-Accept: value"; other servers use other capitalisation.
    monkeypatch.setattr(wsclient.os, "urandom", lambda n: bytes(range(n)))
    key = base64.b64encode(bytes(range(16))).decode()
    port = _serve_once(
        b"HTTP/1.1 101 Switching Protocols\r\nupgrade: websocket\r\nCONNECTION: Upgrade\r\n"
        b"sec-websocket-ACCEPT:   " + wsclient._accept(key).encode() + b"  \r\n\r\n"
    )
    ws = wsclient.WebSocket(f"ws://127.0.0.1:{port}/x", timeout=3)
    ws.close()
