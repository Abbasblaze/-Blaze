"""The Blaze desktop app: see what he's doing, chat, and change his settings."""

import sys

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QHBoxLayout, QListWidget, QMainWindow, QStackedWidget, QWidget

from ..paths import ICON_FILE, SOCKET_PATH
from ..settings import Settings
from .bubble import Bubble
from .client import DaemonClient
from .pages import CameraPage, HomePage, MemoryPage
from .settings_page import SettingsPage
from .style import STYLESHEET

GUI_SOCKET = str(SOCKET_PATH.with_name("blaze-gui.sock"))


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

        self.nav = QListWidget(objectName="nav")
        self.nav.setFixedWidth(180)
        self.nav.setIconSize(QSize(18, 18))
        self.stack = QStackedWidget()
        layout.addWidget(self.nav)
        layout.addWidget(self.stack, 1)

        self.home = HomePage(client)
        self.camera = CameraPage(client)
        self.memory = MemoryPage(client)
        self.settings = SettingsPage(client, lambda text: self.statusBar().showMessage(text, 6000))
        for name, page in (("Home", self.home), ("Camera", self.camera), ("Memory", self.memory),
                           ("Settings", self.settings)):
            self.nav.addItem(name)
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        client.event.connect(self._on_event)
        client.connection_changed.connect(self._on_connection)
        self._on_connection(client.connected)
        self._refresh_names()

    def _refresh_names(self) -> None:
        s = Settings.load()
        self.home.user_name = s.persona.user_name
        self.setWindowTitle(s.persona.name)

    def _on_connection(self, connected: bool) -> None:
        self.home.set_connected(connected)
        if connected:
            self.statusBar().showMessage("Connected to Blaze")
            self.client.request("options", lambda ok, data: ok and self.settings.fill_options(data))
        else:
            self.statusBar().showMessage("Blaze isn't running")

    def _on_event(self, msg: dict) -> None:
        self.home.on_event(msg)
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
