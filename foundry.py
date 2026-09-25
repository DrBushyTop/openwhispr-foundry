"""Azure plumbing shared by every route: az CLI token, pooled HTTPS
connections, and one POST helper that handles 401 refresh and stale sockets."""

from __future__ import annotations

import http.client
import json
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse

TENANT_ID = "7135bcf1-5a12-4e82-ad41-c263afa243e8"  # huuhka.net
TOKEN_RESOURCE = "https://cognitiveservices.azure.com"
UPSTREAM_TIMEOUT_S = 120
# Azure closed idle connections somewhere between 90s and 180s in testing, so
# older ones are dropped instead of tried. A stale one fails instantly anyway.
POOL_MAX_IDLE_S = 100


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- auth

class AzCliToken:
    """Caches an az CLI access token until 5 minutes before it expires."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0

    def get(self) -> str:
        with self._lock:
            if time.time() < self._expires_at - 300:
                return self._token
            out = subprocess.run(
                ["az", "account", "get-access-token",
                 "--tenant", TENANT_ID,
                 "--resource", TOKEN_RESOURCE,
                 "--query", "{t:accessToken,e:expires_on}", "-o", "json"],
                check=True, capture_output=True, text=True,
            )
            data = json.loads(out.stdout)
            self._token = data["t"]
            self._expires_at = float(data["e"])
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0.0


TOKEN = AzCliToken()


class UpstreamError(Exception):
    def __init__(self, status: int, body: bytes) -> None:
        super().__init__(f"Azure HTTP {status}: {body[:500].decode('utf-8', 'replace')}")
        self.status = status
        self.body = body


# ---------------------------------------------------------------- connection reuse
# A fresh TLS connection to Azure costs ~170ms (measured: tcp ~55ms + tls ~110ms).
# Keeping connections open between requests skips that on every warm request.

class ConnectionPool:
    """Idle HTTPS connections per host. Thread-safe; one request per connection at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._idle: dict[str, list[tuple[http.client.HTTPSConnection, float]]] = {}

    def get(self, host: str) -> tuple[http.client.HTTPSConnection, bool]:
        """Return (connection, reused)."""
        with self._lock:
            conns = self._idle.get(host, [])
            while conns:
                conn, last_used = conns.pop()
                if time.monotonic() - last_used < POOL_MAX_IDLE_S:
                    return conn, True
                conn.close()
        return http.client.HTTPSConnection(host, timeout=UPSTREAM_TIMEOUT_S), False

    def put(self, host: str, conn: http.client.HTTPSConnection) -> None:
        with self._lock:
            self._idle.setdefault(host, []).append((conn, time.monotonic()))


POOL = ConnectionPool()

# Errors that mean an idle pooled connection was closed by Azure before we used it.
STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected, http.client.CannotSendRequest,
    http.client.BadStatusLine, ConnectionError, ssl.SSLError,
)


class PooledResponse:
    """An http.client response that returns its connection to the pool on close,
    but only if the body was fully read and the server didn't ask to close."""

    def __init__(self, host: str, conn: http.client.HTTPSConnection,
                 resp: http.client.HTTPResponse, reused: bool) -> None:
        self._host, self._conn, self._resp = host, conn, resp
        self.status = resp.status
        self.headers = resp.headers
        self.reused = reused

    def read(self) -> bytes:
        return self._resp.read()

    def __iter__(self):
        return iter(self._resp)  # yields lines as they arrive; used for SSE streams

    def close(self) -> None:
        if self._resp.isclosed() and not self._resp.will_close:
            POOL.put(self._host, self._conn)
        else:
            self._resp.close()
            self._conn.close()

    def __enter__(self) -> "PooledResponse":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def send_pooled(url: str, body: bytes, headers: dict[str, str]) -> PooledResponse:
    parts = urllib.parse.urlsplit(url)
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    while True:
        conn, reused = POOL.get(parts.netloc)
        try:
            conn.request("POST", path, body=body, headers=headers)
            return PooledResponse(parts.netloc, conn, conn.getresponse(), reused)
        except STALE_CONNECTION_ERRORS:
            conn.close()
            if not reused:
                raise
            # Azure closed the idle connection; take another one or open a fresh one.


def open_azure(url: str, body: bytes, content_type: str) -> PooledResponse:
    """POST with the az CLI token and return the open response.
    Retries once with a fresh token on 401. Raises UpstreamError on other errors."""
    for attempt in (1, 2):
        resp = send_pooled(url, body, {
            "Content-Type": content_type,
            "Authorization": f"Bearer {TOKEN.get()}",
        })
        if resp.status < 400:
            return resp
        with resp:
            detail = resp.read()
        if resp.status == 401 and attempt == 1:
            TOKEN.invalidate()  # token revoked or clock skew; fetch a fresh one once
            continue
        raise UpstreamError(resp.status, detail)
    raise UpstreamError(401, b"Azure rejected the token twice")
