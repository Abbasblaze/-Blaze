"""Render README screenshots of the app with demo data (never your real camera, chat or memory).

Usage: QT_QPA_PLATFORM=offscreen .venv/bin/python scripts/screenshots.py
Writes PNGs to docs/screenshots/.
"""

import base64
import sys
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from blaze.gui import settings_page
from blaze.gui.app import MainWindow
from blaze.gui.bubble import BUBBLE_SIZE, Bubble
from blaze.gui.style import STYLESHEET
from blaze.settings import Settings

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SIZE = (1100, 760)
NOW = time.mktime(time.localtime()[:3] + (21, 20, 0, 0, 0, -1))  # a fixed evening, so the demo chat reads right

CHAT = [
    ("user", "Hey Blaze, how's it going?", -300),
    ("blaze", "Pretty good! You've been at it for two hours straight, so I'd say you're on a roll.", -296),
    ("user", "Can you find my project notes and open them?", -240),
    ("blaze", "Found notes.md in ~/Projects/robot-project and opened it.", -236),
    ("user", "What's my battery at?", -120),
    ("blaze", "82% and charging. You're good for the rest of the night.", -118),
    ("user", "Remind me in 20 minutes to stretch.", -60),
    ("blaze", "Done. I'll remind you to stretch at 21:40.", -58),
]
FACTS = [
    "Alex is a software engineer working on robotics projects.",
    "Alex prefers working late at night with lofi music.",
    "Alex's main project this month is a desk robot called Blaze.",
    "Alex takes coffee with no sugar.",
]
OPTIONS = {
    "models": ["qwen3.5:4b", "qwen2.5:3b", "moondream:1.8b"],
    "voices": ["af_heart", "am_michael", "bf_emma", "bm_george"],
    "devices": {"inputs": ["Built-in microphone", "Headset"], "outputs": ["Speakers", "Headset"]},
    "cameras": [{"index": 0, "name": "Integrated webcam (/dev/video0)"}],
}


def demo_camera_frame() -> str:
    """A drawn stand-in for a webcam frame: a person silhouette with Blaze's face box."""
    img = QImage(640, 480, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    bg = QLinearGradient(0, 0, 0, 480)
    bg.setColorAt(0, QColor("#3b4652"))
    bg.setColorAt(1, QColor("#1f252c"))
    p.fillRect(img.rect(), bg)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#5b6773"))
    p.drawRoundedRect(QRectF(150, 300, 340, 260), 120, 120)  # shoulders
    p.setBrush(QColor("#7a8794"))
    p.drawEllipse(QPointF(320, 220), 78, 92)  # head
    p.setPen(QPen(QColor("#ff8a1f"), 3))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRect(QRectF(232, 118, 176, 204))  # face box
    p.setFont(QFont("Sans", 13, QFont.Weight.Bold))
    p.drawText(QPointF(236, 110), "you · 0.82")
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPG")
    return base64.b64encode(bytes(buf.data())).decode()


class DemoClient(QObject):
    """Stands in for the daemon connection, answering with demo data."""
    event = Signal(dict)
    connection_changed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.connected = True
        self.frame = demo_camera_frame()

    def request(self, cmd: str, callback=None, **args) -> None:
        data = {
            "recent": [{"role": r, "text": t, "ts": NOW + dt} for r, t, dt in CHAT],
            "facts": [{"id": i, "text": f, "created": NOW} for i, f in enumerate(FACTS)],
            "snapshot": self.frame,
            "options": OPTIONS,
        }.get(cmd)
        if callback:
            QTimer.singleShot(0, lambda: callback(True, data))

    def status(self, state: str = "speaking") -> None:
        self.event.emit({
            "event": "status", "state": state, "muted": False, "mic_level": 0.0, "messages": 1284,
            "eyes": {"camera_ok": True, "present": True, "who": "owner", "present_since": NOW - 2 * 3600 - 300,
                     "faces": 1, "knows_owner": True},
        })


def demo_settings() -> Settings:
    s = Settings()
    s.persona.user_name = "Alex"
    s.brain.model = s.brain.vision_model = "qwen3.5:4b"
    return s


def save(widget, name: str) -> None:
    widget.grab().save(str(OUT / f"{name}.png"))
    print("wrote", OUT / f"{name}.png")


def bubble_shot(client: DemoClient) -> None:
    """The floating bubble in the corner of a (fake) desktop."""
    bubble = Bubble(client, lambda: None, lambda: None)
    bubble.orb.set_state("listening")
    bubble.orb.level = 0.4
    for _ in range(40):
        bubble.orb._tick()  # settle the color animation
    canvas = QImage(520, 240, QImage.Format.Format_ARGB32)
    p = QPainter(canvas)
    bg = QLinearGradient(0, 0, 520, 240)
    bg.setColorAt(0, QColor("#2b3a55"))
    bg.setColorAt(1, QColor("#141b2b"))
    p.fillRect(canvas.rect(), bg)
    p.fillRect(QRectF(0, 0, 520, 28), QColor(0, 0, 0, 120))  # top bar
    p.setPen(QColor("#c9d1e0"))
    p.setFont(QFont("Sans", 10))
    p.drawText(QRectF(12, 0, 200, 28), Qt.AlignmentFlag.AlignVCenter, "1  2  3  4")
    p.drawText(QRectF(0, 0, 508, 28), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, "21:20")
    p.setBrush(QColor("#1d1917"))
    p.setPen(QColor("#342c27"))
    p.drawRoundedRect(QRectF(24, 52, 330, 170), 10, 10)  # a window underneath
    # Render without a window background so the orb's glow blends into the desktop.
    bubble.orb.render(p, QPoint(520 - BUBBLE_SIZE - 20, 44), renderFlags=QWidget.RenderFlag.DrawChildren)
    p.end()
    canvas.save(str(OUT / "bubble.png"))
    print("wrote", OUT / "bubble.png")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings_page.Settings.load = staticmethod(demo_settings)  # don't show the real settings file
    settings_page.user_service = lambda *a: type("R", (), {"stdout": "enabled", "returncode": 0, "stderr": ""})()

    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    client = DemoClient()
    window = MainWindow(client)
    window.home.user_name = "Alex"
    window.resize(*SIZE)
    window.show()
    window.settings.fill_options(OPTIONS)
    window.statusBar().showMessage("Connected to Blaze")

    pages = [(0, "home"), (1, "camera"), (2, "memory"), (3, "settings")]

    def shoot(i: int = 0) -> None:
        if i:
            save(window, pages[i - 1][1])
        if i == len(pages):
            bubble_shot(client)
            app.quit()
            return
        window.nav.setCurrentRow(pages[i][0])
        client.status("speaking")
        QTimer.singleShot(1200, lambda: shoot(i + 1))

    window.home.on_event({"event": "heard", "text": "Remind me in 20 minutes to stretch."})
    QTimer.singleShot(500, shoot)
    app.exec()


if __name__ == "__main__":
    main()
