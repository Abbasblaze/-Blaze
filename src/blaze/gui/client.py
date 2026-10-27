"""Talks to the Blaze daemon over its Unix socket, Qt-style (signals, no threads)."""

import json
from collections.abc import Callable

from PySide6.QtCore import QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket

from ..paths import SOCKET_PATH

Callback = Callable[[bool, object], None]  # (ok, data or error message)
# Turning Blaze off stops the model server too: together they hold about 4 GB of RAM.
SERVICES = ["blaze.service", "blaze-ollama.service"]


class DaemonClient(QObject):
    event = Signal(dict)
    connection_changed = Signal(bool)
    power_changed = Signal(str)  # "starting", "stopping", "" when done, or an error message

    def __init__(self, parent=None):
        super().__init__(parent)
        self.connected = False
        self._buffer = b""
        self._next_id = 1
        self._callbacks: dict[int, Callback] = {}
        self.powering = ""

        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._socket.readyRead.connect(self._on_ready_read)
        self._socket.errorOccurred.connect(lambda _: self._on_disconnected())

        self._retry = QTimer(self, interval=2000, timeout=self._connect)
        self._retry.start()
        self._connect()

    def set_power(self, on: bool) -> None:
        """Start Blaze and his model server, or stop both to free their memory."""
        if self.powering:
            return
        self.powering = "starting" if on else "stopping"
        self.power_changed.emit(self.powering)
        process = QProcess(self)

        def finished(code: int, _status) -> None:
            error = bytes(process.readAllStandardError()).decode().strip()
            self.powering = ""
            self.power_changed.emit("" if code == 0 else error or f"systemctl failed (exit {code})")
            process.deleteLater()
        process.finished.connect(finished)
        # Starting Blaze brings the model server up with him (the unit Wants it).
        process.start("systemctl", ["--user", "start" if on else "stop", *(SERVICES[:1] if on else SERVICES)])

    def _connect(self) -> None:
        if self._socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self._socket.connectToServer(str(SOCKET_PATH))

    def _on_connected(self) -> None:
        self.connected = True
        self._retry.stop()
        self.request("subscribe")
        self.connection_changed.emit(True)

    def _on_disconnected(self) -> None:
        if self.connected:
            self.connected = False
            self._buffer = b""
            for cb in self._callbacks.values():
                cb(False, "Lost connection to Blaze")
            self._callbacks.clear()
            self.connection_changed.emit(False)
        self._socket.abort()
        self._retry.start()

    def _on_ready_read(self) -> None:
        self._buffer += bytes(self._socket.readAll())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "event" in msg:
                self.event.emit(msg)
            elif (cb := self._callbacks.pop(msg.get("id"), None)) is not None:
                cb(msg.get("ok", False), msg.get("data") if msg.get("ok") else msg.get("error"))

    def request(self, cmd: str, callback: Callback | None = None, **args) -> None:
        if not self.connected and cmd != "subscribe":
            if callback:
                callback(False, "Blaze isn't running")
            return
        req_id = self._next_id
        self._next_id += 1
        if callback:
            self._callbacks[req_id] = callback
        self._socket.write(json.dumps({"id": req_id, "cmd": cmd, **args}).encode() + b"\n")
        self._socket.flush()
