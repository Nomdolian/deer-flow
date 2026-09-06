"""Minimal /health endpoint.

Deliberately dependency-free: asyncio's own server, no web framework. It
reports the numbers that tell you whether the bot is actually alive rather
than merely running — feed age above all.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class HealthServer:
    def __init__(self, port: int, snapshot: Callable[[], dict], host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.snapshot = snapshot
        self.started_ts = time.time()
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        log.info("health: listening on http://%s:%d/health", self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readline(), timeout=5)
        except TimeoutError:
            writer.close()
            return
        path = request.decode(errors="ignore").split(" ")[1] if b" " in request else "/"
        payload = dict(self.snapshot())
        payload["uptime_s"] = round(time.time() - self.started_ts, 1)
        healthy = bool(payload.get("healthy", True))
        status = "200 OK" if healthy else "503 Service Unavailable"
        if path.startswith("/health"):
            body = json.dumps(payload, default=str).encode()
        else:
            status, body = "404 Not Found", b'{"error":"not found"}'
        writer.write(
            f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
        )
        await writer.drain()
        writer.close()
