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
        self.menu.addSeparator()
        self.menu.addAction("Close app (Blaze keeps running)", quit_app)
        self.muted = False

        client.event.connect(self._on_event)
        client.connection_changed.connect(lambda ok: ok or self._set_state("offline"))
        self._set_state("idle" if client.connected else "offline")

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

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_app()
        elif event.button() == Qt.MouseButton.RightButton:
            self.menu.popup(event.globalPosition().toPoint())
