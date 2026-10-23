"""A tiny JSON-lines protocol over a Unix socket between the daemon and its clients.

Request:   {"id": 1, "cmd": "chat", "text": "hi"}
Response:  {"id": 1, "ok": true, "data": ...}   or   {"id": 1, "ok": false, "error": "..."}
Event:     {"event": "state", ...}               (only to clients that sent "subscribe")
"""

import asyncio
import json
import logging
import socket
from collections.abc import Awaitable, Callable
from typing import Any

from .paths import SOCKET_PATH

log = logging.getLogger(__name__)

Handler = Callable[[str, dict], Awaitable[Any]]


class IpcServer:
    def __init__(self, handler: Handler):
        self.handler = handler
        self._subscribers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        SOCKET_PATH.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._serve, path=str(SOCKET_PATH), limit=1 << 20)
        SOCKET_PATH.chmod(0o600)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
        SOCKET_PATH.unlink(missing_ok=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        tasks: set[asyncio.Task] = set()
        try:
            while line := await reader.readline():
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("cmd") == "subscribe":
                    self._subscribers.add(writer)
                    self._send(writer, {"id": msg.get("id"), "ok": True, "data": None})
                    continue
                # Handle each request concurrently so slow ones (chat, face learning) don't block events.
                task = asyncio.create_task(self._answer(writer, msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self._subscribers.discard(writer)
            writer.close()

    async def _answer(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        cmd = msg.pop("cmd", "")
        req_id = msg.pop("id", None)
        try:
            data = await self.handler(cmd, msg)
            self._send(writer, {"id": req_id, "ok": True, "data": data})
        except Exception as e:
            log.exception("Command %s failed", cmd)
            self._send(writer, {"id": req_id, "ok": False, "error": str(e)})

    @staticmethod
    def _send(writer: asyncio.StreamWriter, obj: dict) -> None:
        if not writer.is_closing():
            writer.write(json.dumps(obj).encode() + b"\n")

    def broadcast(self, event: str, **data: Any) -> None:
        for writer in list(self._subscribers):
            self._send(writer, {"event": event, **data})


def request(cmd: str, timeout: float = 10, **args: Any) -> Any:
    """Send one command to the running daemon and return its answer (blocking)."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        try:
            s.connect(str(SOCKET_PATH))
        except (FileNotFoundError, ConnectionRefusedError):
            raise ConnectionError("Blaze isn't running. Start him with: systemctl --user start blaze") from None
        s.sendall(json.dumps({"id": 1, "cmd": cmd, **args}).encode() + b"\n")
        with s.makefile() as f:
            resp = json.loads(f.readline())
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "unknown error"))
    return resp.get("data")
