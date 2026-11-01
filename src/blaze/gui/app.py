"""The Blaze desktop app: see what he's doing, chat, and change his settings."""

import sys

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QStackedWidget,
    QVBoxLayout, QWidget,
)

from ..paths import ICON_FILE, SOCKET_PATH
from ..settings import Settings
from .bubble import Bubble
from .client import DaemonClient
from .icons import icon
from .orb import Orb
from .pages import CameraPage, HomePage, MemoryPage, button
from .settings_page import SettingsPage
from .style import MUTED, STATE_COLORS, STATE_SHORT, STYLESHEET, TEXT

GUI_SOCKET = str(SOCKET_PATH.with_name("blaze-gui.sock"))


class StatusCard(QFrame):
    """Bottom of the sidebar: what Blaze is doing right now, plus the on/off switch."""

    def __init__(self, client: DaemonClient):
        super().__init__(objectName="statusCard")
        self.client = client
        self.waking = False  # started, but he's still loading and hasn't connected yet
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = QLabel(fixedWidth=10, fixedHeight=10)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.state = QLabel("Offline", objectName="statusText")
        self.sub = QLabel("Not running", objectName="statusSub")
        text.addWidget(self.state)
        text.addWidget(self.sub)
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addLayout(text, 1)
        layout.addLayout(top)
        self.power_btn = button("Turn off", "danger", lambda: client.set_power(not client.connected))
        self.power_btn.setIconSize(QSize(16, 16))
        self.power_btn.setToolTip("Stop Blaze and his model to free about 4 GB of RAM")
        layout.addWidget(self.power_btn)
        self.set_state("offline")

    def set_state(self, state: str) -> None:
        self.state.setText(STATE_SHORT.get(state, state.title()))
        self.dot.setStyleSheet(f"background:{STATE_COLORS.get(state, MUTED)}; border-radius:5px;")

    def on_power(self, step: str) -> None:
        if step == "starting":
            self.waking = True
        elif step == "" and self.waking and not self.client.connected:
            QTimer.singleShot(60_000, self._give_up_waking)
        else:
            self.waking = False
        self.set_power(self.client.connected)

    def _give_up_waking(self) -> None:
        self.waking = False
        self.set_power(self.client.connected)

    def set_power(self, connected: bool) -> None:
        if connected:
            self.waking = False
        busy = self.client.powering or ("starting" if self.waking else "")
        self.power_btn.setEnabled(not busy)
        if busy:
            self.power_btn.setText("Starting…" if busy == "starting" else "Turning off…")
            self.sub.setText("Starting up" if busy == "starting" else "Shutting down")
            return
        self.power_btn.setText("Turn off" if connected else "Turn on")
        self.power_btn.setObjectName("danger" if connected else "primary")
        self.power_btn.setIcon(icon("power", "#ff5a4f" if connected else "#1a0f08"))
        self.sub.setText("Running on this laptop" if connected else "RAM is free")
        self.power_btn.style().unpolish(self.power_btn)
        self.power_btn.style().polish(self.power_btn)


class MainWindow(QMainWindow):
    def __init__(self, client: DaemonClient):
        super().__init__()
        self.client = client
        self.setWindowTitle("Blaze")
        self.setWindowIcon(QIcon(str(ICON_FILE)))
        self.resize(980, 720)
        self.really_quit = False

        root = QWidget(objectName="root")
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(root)

        sidebar = QWidget(objectName="sidebar")
        sidebar.setFixedWidth(210)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 16, 12, 12)
        side.setSpacing(6)
        brand = QHBoxLayout()
        brand.setSpacing(8)
        self.brand_orb = Orb(size=34)
        self.brand_orb.set_state("idle")
        self.brand_name = QLabel("Blaze", objectName="brand")
        brand.addWidget(self.brand_orb)
        brand.addWidget(self.brand_name, 1)
        side.addLayout(brand)
        side.addSpacing(14)
        self.nav = QListWidget(objectName="nav")
        self.nav.setIconSize(QSize(18, 18))
        side.addWidget(self.nav, 1)
        self.status_card = StatusCard(client)
        side.addWidget(self.status_card)

        self.stack = QStackedWidget()
        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)

        self.home = HomePage(client)
        self.camera = CameraPage(client)
        self.memory = MemoryPage(client)
        self.settings = SettingsPage(client, lambda text: self.statusBar().showMessage(text, 6000))
        for name, ic, page in (("Home", "home", self.home), ("Camera", "camera", self.camera),
                               ("Memory", "memory", self.memory), ("Settings", "settings", self.settings)):
            self.nav.addItem(QListWidgetItem(icon(ic, MUTED, TEXT), name))
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        client.event.connect(self._on_event)
        client.connection_changed.connect(self._on_connection)
        client.power_changed.connect(self._on_power)
        self._on_connection(client.connected)
        self._refresh_names()

    def _refresh_names(self) -> None:
        s = Settings.load()
        self.home.user_name = s.persona.user_name
        self.setWindowTitle(s.persona.name)
        self.brand_name.setText(s.persona.name)

    def _on_connection(self, connected: bool) -> None:
        self.home.set_connected(connected)
        self.status_card.set_power(connected)
        if not connected:
            self._set_state("offline")
        if connected:
            self.statusBar().showMessage("Connected to Blaze")
            self.client.request("options", lambda ok, data: ok and self.settings.fill_options(data))
        else:
            self.statusBar().showMessage("Blaze isn't running")

    def _set_state(self, state: str) -> None:
        self.status_card.set_state(state)
        self.brand_orb.set_state(state)

    def _on_power(self, step: str) -> None:
        self.home.on_power(step)
        self.status_card.on_power(step)
        if step == "stopping":
            self.statusBar().showMessage("Turning Blaze off…")
        elif step == "starting":
            self.statusBar().showMessage("Starting Blaze…")
        elif step:
            self.statusBar().showMessage(f"Couldn't turn Blaze on or off: {step}", 10000)

    def _on_event(self, msg: dict) -> None:
        self.home.on_event(msg)
        if msg["event"] in ("state", "status"):
            self._set_state(msg["state"])
        if msg["event"] == "status":
            self.camera.on_status(msg)
            self.memory.on_status(msg)
        elif msg["event"] == "confirm" and not self.isVisible():
            self.show_window()

    def show_window(self) -> None:
        self._refresh_names()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        # Closing just hides the window: the bubble stays, and Blaze himself keeps running either way.
        if self.really_quit:
            event.accept()
        else:
            event.ignore()
            self.hide()


def ask_running_app(command: bytes) -> bool:
    """If the app is already open, pass it a command ("show" / "bubble") and return True."""
    sock = QLocalSocket()
    sock.connectToServer(GUI_SOCKET)
    if not sock.waitForConnected(500):
        return False
    sock.write(command)
    sock.waitForBytesWritten(500)
    sock.disconnectFromServer()
    return True


def listen_for_commands(handler) -> QLocalServer:
    server = QLocalServer()
    QLocalServer.removeServer(GUI_SOCKET)
    server.listen(GUI_SOCKET)

    def accept() -> None:
        conn = server.nextPendingConnection()
        conn.readyRead.connect(lambda: handler(bytes(conn.readAll()).strip()))
    server.newConnection.connect(accept)
    return server


def main(show_window: bool = True) -> None:
    """Open the app. With show_window=False only the floating bubble appears (used at login)."""
    app = QApplication(sys.argv)
    if ask_running_app(b"show" if show_window else b"bubble"):
        return
    app.setApplicationName("Blaze")
    app.setDesktopFileName("blaze")
    app.setWindowIcon(QIcon(str(ICON_FILE)))
    app.setStyleSheet(STYLESHEET)
    app.setQuitOnLastWindowClosed(False)

    client = DaemonClient()
    window = MainWindow(client)

    def quit_app() -> None:
        window.really_quit = True
        app.quit()

    bubble = Bubble(client, window.show_window, quit_app)
    bubble.show()

    def on_command(command: bytes) -> None:
        if command == b"show":
            window.show_window()
        bubble.show()
    server = listen_for_commands(on_command)  # noqa: F841 — must stay alive

    if show_window:
        window.show()
    sys.exit(app.exec())
