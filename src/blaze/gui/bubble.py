"""The little floating Blaze bubble that stays on top of everything.

Left-click opens the app, right-click shows quick actions. Hyprland keeps it
floating, pinned to every workspace and in the corner (see `blaze install`).
"""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from .client import DaemonClient
from .orb import Orb
from .style import STATE_LABELS

BUBBLE_TITLE = "Blaze Bubble"  # Hyprland window rules match on this
BUBBLE_SIZE = 80


class Bubble(QWidget):
    def __init__(self, client: DaemonClient, open_app: Callable[[], None], quit_app: Callable[[], None]):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool)
        self.client = client
        self.open_app = open_app
        self.setWindowTitle(BUBBLE_TITLE)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(BUBBLE_SIZE, BUBBLE_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.orb = Orb(size=BUBBLE_SIZE)
        layout.addWidget(self.orb)

        self.menu = QMenu(self)
        self.menu.addAction("Open Blaze", open_app)
        self.menu.addAction("Talk", lambda: client.request("talk"))
        self.menu.addAction("Stop talking", lambda: client.request("stop"))
        self.mute_action = self.menu.addAction("Mute microphone", lambda: client.request(
            "mute", muted=not self.muted))
        self.camera_action = self.menu.addAction("Open camera", lambda: client.request(
            "camera", on=not self.camera_on))
        self.menu.addSeparator()
        self.power_action = self.menu.addAction("Turn off Blaze (free RAM)",
                                                lambda: client.set_power(not client.connected))
        self.menu.addAction("Close app (Blaze keeps running)", quit_app)
        self.muted = False
        self.camera_on = False

        client.event.connect(self._on_event)
        client.connection_changed.connect(self._on_connection)
        client.power_changed.connect(lambda _: self._on_connection(client.connected))
        self._on_connection(client.connected)
        self._set_state("idle" if client.connected else "offline")

    def _on_connection(self, connected: bool) -> None:
        if not connected:
            self._set_state("offline")
        for action in self.menu.actions()[1:5]:  # talk, stop, mute, camera need him running
            action.setEnabled(connected)
        self.power_action.setEnabled(not self.client.powering)
        self.power_action.setText(
            {"starting": "Starting Blaze…", "stopping": "Turning off Blaze…"}.get(self.client.powering)
            or ("Turn off Blaze (free RAM)" if connected else "Activate Blaze"))

    def _set_state(self, state: str) -> None:
        self.orb.set_state(state)
        self.setToolTip(f"Blaze — {STATE_LABELS.get(state, state)}")

    def _on_event(self, msg: dict) -> None:
        if msg["event"] == "state":
            self._set_state(msg["state"])
        elif msg["event"] == "status":
            self._set_state(msg["state"])
            self.orb.level = msg.get("mic_level", 0.0)
            self.muted = msg["muted"]
            self.mute_action.setText("Unmute microphone" if self.muted else "Mute microphone")
            self.camera_on = bool(msg.get("camera_on"))
            self.camera_action.setText("Close camera" if self.camera_on else "Open camera")

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_app()
        elif event.button() == Qt.MouseButton.RightButton:
            self.menu.popup(event.globalPosition().toPoint())
