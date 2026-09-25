"""Minimal server-side WebSocket (RFC 6455) on top of http.server's socket.

Covers what OpenWhispr's `ws` client uses: the upgrade handshake, masked
client frames, text/binary/continuation, ping/pong and close. No extensions:
the client offers permessage-deflate and we don't accept it, which RFC 6455
allows. Sends are locked so a worker thread can push events while the reader
thread answers pings.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_MESSAGE_BYTES = 16 * 1024 * 1024

OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA


class ConnectionClosed(Exception):
    pass


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def is_upgrade_request(headers) -> bool:
    return (
        headers.get("Upgrade", "").lower() == "websocket"
        and "upgrade" in headers.get("Connection", "").lower()
        and bool(headers.get("Sec-WebSocket-Key"))
    )


def handshake_response(headers) -> bytes:
    # Written by hand: BaseHTTPRequestHandler would answer "HTTP/1.0 101",
    # and an upgrade needs HTTP/1.1.
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(headers['Sec-WebSocket-Key'])}\r\n\r\n"
    ).encode()


def unmask(payload: bytes, mask: bytes) -> bytes:
    # XOR the whole payload as one big integer; a per-byte loop is ~50x slower.
    n = len(payload)
    if not n:
        return payload
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


def encode_frame(opcode: int, payload: bytes) -> bytes:
    n = len(payload)
    if n < 126:
        header = struct.pack("!BB", 0x80 | opcode, n)
    elif n < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 126, n)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, n)
    return header + payload


class WebSocket:
    def __init__(self, rfile, wfile) -> None:
        self._rfile = rfile
        self._wfile = wfile
        self._send_lock = threading.Lock()
        self.closed = False

    def _read_exact(self, n: int) -> bytes:
        data = self._rfile.read(n)
        if data is None or len(data) < n:
            raise ConnectionClosed("socket closed")
        return data

    def _read_frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read_exact(2)
        fin, opcode = bool(b0 & 0x80), b0 & 0x0F
        masked, length = bool(b1 & 0x80), b1 & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", self._read_exact(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", self._read_exact(8))
        if length > MAX_MESSAGE_BYTES:
            raise ConnectionClosed(f"frame too large ({length} bytes)")
        mask = self._read_exact(4) if masked else b""
        payload = self._read_exact(length)
        return fin, opcode, unmask(payload, mask) if masked else payload

    def receive(self) -> tuple[int, bytes]:
        """Next data message as (opcode, payload). Answers pings on the way.
        Raises ConnectionClosed when the client closes or the socket drops."""
        message_opcode, parts, size = OP_CONT, [], 0
        while True:
            fin, opcode, payload = self._read_frame()
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                self.close(payload[:2] if len(payload) >= 2 else b"")
                raise ConnectionClosed("client closed")
            if opcode != OP_CONT:
                message_opcode, parts, size = opcode, [], 0
            parts.append(payload)
            size += len(payload)
            if size > MAX_MESSAGE_BYTES:
                raise ConnectionClosed("message too large")
            if fin:
                return message_opcode, b"".join(parts)

    def _send(self, opcode: int, payload: bytes) -> None:
        with self._send_lock:
            if self.closed and opcode != OP_CLOSE:
                raise ConnectionClosed("already closed")
            try:
                self._wfile.write(encode_frame(opcode, payload))
                self._wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ValueError, OSError) as exc:
                self.closed = True
                raise ConnectionClosed(str(exc)) from None

    def send_text(self, text: str) -> None:
        self._send(OP_TEXT, text.encode("utf-8"))

    def close(self, code: bytes = b"") -> None:
        if self.closed:
            return
        try:
            self._send(OP_CLOSE, code or struct.pack("!H", 1000))
        except ConnectionClosed:
            pass
        self.closed = True
