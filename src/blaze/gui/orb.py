"""Blaze's 'face' in the app: a glowing orb that breathes, listens and talks."""

import math

from PySide6.QtCore import QPointF, QTimer
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from .style import STATE_COLORS


class Orb(QWidget):
    def __init__(self, parent=None, size: int | None = None):
        super().__init__(parent)
        if size:  # fixed little orb (the floating bubble)
            self.setFixedSize(size, size)
        else:
            self.setMinimumSize(180, 180)
            self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.state = "offline"
        self.level = 0.0  # mic loudness 0..1
        self._phase = 0.0
        self._color = QColor(STATE_COLORS["offline"])
        self._timer = QTimer(self, interval=33, timeout=self._tick)
        self._timer.start()

    def set_state(self, state: str) -> None:
        self.state = state

    def _tick(self) -> None:
        speed = {"thinking": 0.16, "speaking": 0.22, "listening": 0.1}.get(self.state, 0.04)
        self._phase += speed
        target = QColor(STATE_COLORS.get(self.state, STATE_COLORS["idle"]))
        # Ease toward the state's color so changes glide instead of snapping.
        self._color = QColor(
            int(self._color.red() + (target.red() - self._color.red()) * 0.15),
            int(self._color.green() + (target.green() - self._color.green()) * 0.15),
            int(self._color.blue() + (target.blue() - self._color.blue()) * 0.15),
        )
        if self.isVisible():
            self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(self.width() / 2, self.height() / 2)
        base = min(self.width(), self.height()) * 0.28

        if self.state == "speaking":
            pulse = 0.08 * abs(math.sin(self._phase * 1.7)) + 0.04 * abs(math.sin(self._phase * 3.1))
        elif self.state == "listening":
            pulse = 0.04 + 0.25 * self.level
        elif self.state in ("muted", "offline"):
            pulse = 0.0
        else:
            pulse = 0.03 * math.sin(self._phase)
        radius = base * (1 + pulse)

        # soft outer glow — kept inside the widget so it never shows a square edge
        glow_radius = min(radius * 1.9, min(self.width(), self.height()) / 2)
        glow = QRadialGradient(center, glow_radius)
        c = QColor(self._color)
        c.setAlpha(90)
        glow.setColorAt(0.0, c)
        c.setAlpha(0)
        glow.setColorAt(1.0, c)
        p.setPen(QColor(0, 0, 0, 0))
        p.setBrush(glow)
        p.drawEllipse(center, glow_radius, glow_radius)

        # thinking: orbiting sparks
        if self.state == "thinking":
            for i in range(3):
                angle = self._phase + i * 2 * math.pi / 3
                dot = QPointF(center.x() + math.cos(angle) * radius * 1.35,
                              center.y() + math.sin(angle) * radius * 1.35)
                p.setBrush(self._color.lighter(130))
                p.drawEllipse(dot, 5, 5)

        # core
        core = QRadialGradient(QPointF(center.x() - radius * 0.3, center.y() - radius * 0.35), radius * 1.4)
        core.setColorAt(0.0, self._color.lighter(160))
        core.setColorAt(0.55, self._color)
        core.setColorAt(1.0, self._color.darker(220))
        p.setBrush(core)
        p.drawEllipse(center, radius, radius)

        # eyes
        if self.state != "offline":
            eye_y = center.y() - radius * 0.08
            blink = 0.15 if (self._phase % 25) < 0.35 else 1.0
            p.setBrush(QColor("#1a0f08"))
            for dx in (-0.3, 0.3):
                p.drawEllipse(QPointF(center.x() + dx * radius, eye_y), radius * 0.09, radius * 0.16 * blink)
        p.end()
