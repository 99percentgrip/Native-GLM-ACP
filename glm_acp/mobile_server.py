"""Optional loopback-only PWA approval companion.

This module deliberately uses only :mod:`http.server`. It never handles API
credentials and defaults to 127.0.0.1; callers must explicitly acknowledge an
unsafe public bind before ``0.0.0.0`` is accepted.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class MobileServerError(RuntimeError):
    pass


class MobileServer:
    """Serve the bundled PWA and resolve short-lived approval IDs."""

    def __init__(self, bind: str = "127.0.0.1:8765", *, allow_public: bool = False) -> None:
        host, separator, port_text = bind.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            raise MobileServerError("Mobile bind must be host:port")
        if host == "0.0.0.0" and not allow_public:
            raise MobileServerError("Refusing 0.0.0.0 without explicit acknowledgement")
        if host not in {"127.0.0.1", "localhost", "0.0.0.0"}:
            raise MobileServerError(
                "Mobile server may bind only to loopback or explicitly acknowledged 0.0.0.0"
            )
        self.host, self.port = host, int(port_text)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._approvals: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Future[bool], float]
        ] = {}
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def register_approval(self, future: asyncio.Future[bool], *, ttl_seconds: float = 120) -> str:
        approval_id = secrets.token_urlsafe(18)
        with self._lock:
            self._approvals[approval_id] = (
                asyncio.get_running_loop(), future, time.monotonic() + ttl_seconds
            )
        return approval_id

    def _resolve(self, approval_id: str, allowed: bool) -> bool:
        with self._lock:
            record = self._approvals.pop(approval_id, None)
        if record is None:
            return False
        loop, future, expires = record
        if expires < time.monotonic() or future.done():
            return False
        loop.call_soon_threadsafe(future.set_result, allowed)
        return True

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self
        asset_dir = Path(__file__).with_name("_mobile_pwa")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                # Request headers (and therefore credentials) are never logged.
                return

            def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                name = "index.html" if self.path in {"/", "/index.html"} else self.path.lstrip("/")
                if name not in {"index.html", "manifest.webmanifest", "app.js"}:
                    self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
                    return
                try:
                    body = (asset_dir / name).read_bytes()
                except OSError:
                    self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
                    return
                content_type = (
                    "application/manifest+json"
                    if name.endswith("webmanifest")
                    else "text/html" if name.endswith("html") else "application/javascript"
                )
                self._send(HTTPStatus.OK, body, content_type)

            def do_POST(self) -> None:  # noqa: N802
                prefix = "/approve/"
                if not self.path.startswith(prefix):
                    self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
                    return
                size = int(self.headers.get("Content-Length", "0") or 0)
                if size > 1024:
                    self._send(HTTPStatus.BAD_REQUEST, b"Invalid request", "text/plain")
                    return
                try:
                    payload = json.loads(self.rfile.read(size) or b"{}")
                    allowed = bool(payload.get("approved"))
                except (ValueError, UnicodeDecodeError):
                    self._send(HTTPStatus.BAD_REQUEST, b"Invalid request", "text/plain")
                    return
                if not owner._resolve(self.path[len(prefix) :], allowed):
                    self._send(HTTPStatus.NOT_FOUND, b"Unknown approval", "text/plain")
                    return
                self._send(HTTPStatus.OK, b'{"ok":true}', "application/json")

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=1)
            self._thread = None
