"""A minimal WebSocket client (RFC 6455), standard library only: text frames, ping/pong, close.

Enough for Home Assistant's WebSocket API; not a general library (no extensions, no
compression, messages are text). Reads never block past their timeout and never lose a partly
received frame.
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import ssl
import struct
import time
from urllib.parse import urlsplit

_GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"  # fixed by RFC 6455 section 1.3
MAX_MESSAGE = 16 * 1024 * 1024


def _accept(key: str) -> str:
    """The Sec-WebSocket-Accept value a server must answer for ``key`` (RFC 6455 section 4.2.2)."""
    return base64.b64encode(hashlib.sha1(key.encode() + _GUID).digest()).decode()


class WsError(Exception):
    pass


class WsClosed(WsError):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0, headers: dict[str, str] | None = None):
        parts = urlsplit(url)
        if parts.scheme not in ("ws", "wss") or not parts.hostname:
            raise WsError(f"not a ws:// or wss:// URL: {url}")
        port = parts.port or (443 if parts.scheme == "wss" else 80)
        raw = socket.create_connection((parts.hostname, port), timeout=timeout)
        self.sock: socket.socket = (
            ssl.create_default_context().wrap_socket(raw, server_hostname=parts.hostname)
            if parts.scheme == "wss"
            else raw
        )
        self.buf = b""
        self.fragments: list[bytes] = []
        self.closed = False
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {parts.hostname}:{port}",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
            *(f"{k}: {v}" for k, v in (headers or {}).items()),
        ]
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = self._read_until(b"\r\n\r\n", timeout)
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if " 101 " not in status:
            self.close()
            raise WsError(f"upgrade refused: {status}")
        got = {}
        for line in head.decode("latin-1").split("\r\n")[1:]:
            name, _, value = line.partition(":")
            got[name.strip().lower()] = value.strip()
        if got.get("sec-websocket-accept") != _accept(key):
            self.close()
            answered = got.get("sec-websocket-accept")
            raise WsError(f"bad Sec-WebSocket-Accept: the server answered {answered!r}")

    # -- reading --------------------------------------------------------------------------

    def _fill(self, deadline: float) -> bool:
        """Read more bytes into the buffer; False on timeout."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        self.sock.settimeout(remaining)
        try:
            chunk = self.sock.recv(65536)
        except TimeoutError:
            return False
        except ssl.SSLWantReadError:
            return False
        if not chunk:
            self.closed = True
            raise WsClosed("connection closed")
        self.buf += chunk
        return True

    def _read_until(self, marker: bytes, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while marker not in self.buf:
            if not self._fill(deadline):
                raise WsError("timed out during the upgrade")
        head, _, self.buf = self.buf.partition(marker)
        return head

    def _frame(self) -> tuple[int, bool, bytes] | None:
        """One complete frame from the buffer, or None if it is not all here yet."""
        b = self.buf
        if len(b) < 2:
            return None
        fin, opcode = bool(b[0] & 0x80), b[0] & 0x0F
        masked, length, pos = bool(b[1] & 0x80), b[1] & 0x7F, 2
        if length == 126:
            if len(b) < 4:
                return None
            length, pos = struct.unpack(">H", b[2:4])[0], 4
        elif length == 127:
            if len(b) < 10:
                return None
            length, pos = struct.unpack(">Q", b[2:10])[0], 10
        if length > MAX_MESSAGE:
            raise WsError("frame too large")
        mask = b""
        if masked:
            if len(b) < pos + 4:
                return None
            mask, pos = b[pos : pos + 4], pos + 4
        if len(b) < pos + length:
            return None
        payload = b[pos : pos + length]
        if masked:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.buf = b[pos + length :]
        return opcode, fin, payload

    def recv_text(self, timeout: float) -> str | None:
        """The next text message, or None if none arrived within ``timeout`` seconds."""
        deadline = time.monotonic() + timeout
        while True:
            frame = self._frame()
            if frame is None:
                if not self._fill(deadline):
                    return None
                continue
            opcode, fin, payload = frame
            if opcode == 0x9:  # ping
                self._send(0xA, payload)
            elif opcode == 0x8:  # close
                self.closed = True
                try:
                    self._send(0x8, payload[:2])
                except OSError:
                    pass
                raise WsClosed("closed by the server")
            elif opcode in (0x1, 0x2, 0x0):
                self.fragments.append(payload)
                if sum(map(len, self.fragments)) > MAX_MESSAGE:
                    raise WsError("message too large")
                if fin:
                    data, self.fragments = b"".join(self.fragments), []
                    return data.decode("utf-8")

    # -- writing --------------------------------------------------------------------------

    def _send(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        body = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        self.sock.settimeout(10)
        self.sock.sendall(head + mask + body)

    def send_text(self, text: str) -> None:
        self._send(0x1, text.encode("utf-8"))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._send(0x8, b"")
            except OSError:
                pass
        try:
            self.sock.close()
        except OSError:
            pass
