"""The Home, Camera and Memory pages of the desktop app."""

import base64
import time

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from .client import DaemonClient
from .icons import icon, pixmap
from .orb import Orb
from .style import DANGER, FAINT, MUTED, STATE_LABELS, TEXT

INK = "#1a0f08"  # text/icon color on the orange accent
MAX_CHAT = 300   # bubbles kept on screen


def page_header(title: str, subtitle: str, extra: QWidget | None = None) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 10)
    layout.setSpacing(4)
    row = QHBoxLayout()
    row.addWidget(QLabel(title, objectName="title"))
    row.addStretch()
    if extra:
        row.addWidget(extra)
    layout.addLayout(row)
    layout.addWidget(QLabel(subtitle, objectName="subtitle", wordWrap=True))
    return box


def button(text: str, kind: str = "", slot=None, icon_name: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if icon_name:
        color = INK if kind in ("primary", "big", "round") else DANGER if kind == "danger" else TEXT
        b.setIcon(icon(icon_name, color))
        b.setIconSize(QSize(17, 17))
    if slot:
        b.clicked.connect(slot)
    return b


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


def presence_text(eyes: dict | None, user: str) -> str:
    if not eyes:
        return "Camera closed"
    if not eyes.get("camera_ok"):
        return "Camera not available"
    if not eyes.get("present"):
        return "Nobody at the desk"
    since = eyes.get("present_since") or time.time()
    minutes = int((time.time() - since) // 60)
    duration = f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"
    if eyes.get("who") == "someone" and eyes.get("knows_owner"):
        return f"Someone I don't know is here · {duration}"
    return f"{user} is at the desk · {duration}"


def day_name(ts: float) -> str:
    day = time.localtime(ts)[:3]
    if day == time.localtime()[:3]:
        return "Today"
    if day == time.localtime(time.time() - 86400)[:3]:
        return "Yesterday"
    return time.strftime("%A, %d %B", time.localtime(ts))


class ChatView(QScrollArea):
    """Chat as rounded bubbles: yours on the right, Blaze's on the left. Sticks to the bottom."""

    def __init__(self):
        super().__init__(objectName="chat", widgetResizable=True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(objectName="chatBody")
        self.rows = QVBoxLayout(body)
        self.rows.setContentsMargins(16, 14, 16, 14)
        self.rows.setSpacing(4)
        self.rows.addStretch()
        self.setWidget(body)

        self.empty = QLabel("No messages yet.\nSay “Hey Blaze”, or type below.", objectName="hint",
                            alignment=Qt.AlignmentFlag.AlignCenter)
        self.rows.addWidget(self.empty)
        self.bubbles: list[QLabel] = []
        self.last_day = None
        self.last_role = None
        self.stick = True
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(lambda _lo, hi: self.stick and bar.setValue(hi))
        bar.valueChanged.connect(lambda v: setattr(self, "stick", v >= bar.maximum() - 40))

    def clear(self) -> None:
        while self.rows.count() > 1:
            item = self.rows.takeAt(1)
            if item.widget() and item.widget() is not self.empty:
                item.widget().deleteLater()
        self.rows.addWidget(self.empty)
        self.empty.show()
        self.bubbles.clear()
        self.last_day = self.last_role = None
        self.stick = True

    def add(self, role: str, text: str, ts: float | None = None) -> None:
        ts = ts or time.time()
        self.empty.hide()
        day = day_name(ts)
        if day != self.last_day:
            self.rows.addSpacing(6)
            self.rows.addWidget(QLabel(day.upper(), objectName="day", alignment=Qt.AlignmentFlag.AlignCenter))
            self.rows.addSpacing(4)
            self.last_day, self.last_role = day, None
        if role != self.last_role and self.last_role is not None:
            self.rows.addSpacing(8)
        self.last_role = role

        mine = role == "user"
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        col = QVBoxLayout()
        col.setSpacing(3)
        bubble = QLabel(text, objectName="bubbleUser" if mine else "bubbleBlaze", wordWrap=True,
                        textFormat=Qt.TextFormat.PlainText)
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._fit(bubble, self._bubble_width())
        stamp = QLabel(time.strftime("%H:%M", time.localtime(ts)), objectName="stamp")
        align = Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft
        col.addWidget(bubble, 0, align)
        col.addWidget(stamp, 0, align)
        if mine:
            h.addStretch()
        h.addLayout(col)
        if not mine:
            h.addStretch()
        self.rows.addWidget(row)
        self.bubbles.append(bubble)

        if len(self.bubbles) > MAX_CHAT:  # drop the oldest so a long day doesn't slow the app down
            self.bubbles.pop(0)
            while self.rows.count() > 2:
                item = self.rows.takeAt(2)
                w = item.widget()
                if w:
                    w.deleteLater()
                    if w.findChild(QLabel, "bubbleUser") or w.findChild(QLabel, "bubbleBlaze"):
                        break

    def _bubble_width(self) -> int:
        return max(260, int(self.viewport().width() * 0.72))

    @staticmethod
    def _fit(bubble: QLabel, max_width: int) -> None:
        """Size a bubble to its text (word-wrapped QLabels otherwise wrap far too early)."""
        bubble.ensurePolished()  # so the stylesheet font is the one measured
        fm = bubble.fontMetrics()
        longest = max((fm.horizontalAdvance(line) for line in bubble.text().splitlines() or [""]), default=0)
        bubble.setFixedWidth(min(longest + 36, max_width))  # side padding + border + a little slack

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        width = self._bubble_width()
        for b in self.bubbles:
            self._fit(b, width)


class HomePage(QWidget):
    def __init__(self, client: DaemonClient):
        super().__init__(objectName="page")
        self.client = client
        self.user_name = "you"
        self.muted = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 18, 28, 20)
        layout.setSpacing(10)

        self.orb = Orb()
        self.orb.setMinimumSize(160, 160)
        layout.addWidget(self.orb)
        self.state_label = QLabel(STATE_LABELS["offline"], objectName="stateLabel",
                                  alignment=Qt.AlignmentFlag.AlignCenter)
        self.presence_label = QLabel("", objectName="subtitle", alignment=Qt.AlignmentFlag.AlignCenter,
                                     wordWrap=True)
        layout.addWidget(self.state_label)
        layout.addWidget(self.presence_label)

        controls = QHBoxLayout()
        controls.setSpacing(10)
        controls.addStretch()
        self.talk_btn = button("Talk", "big", lambda: client.request("talk"), "mic")
        self.stop_btn = button("Stop", "pill", lambda: client.request("stop"), "stop")
        self.stop_btn.setToolTip("Stop talking")
        self.mute_btn = button("Mute", "pill", self._toggle_mute, "mic-off")
        self.mute_btn.setToolTip("Turn the microphone off or on")
        for b in (self.talk_btn, self.stop_btn, self.mute_btn):
            controls.addWidget(b)
        controls.addStretch()
        layout.addSpacing(4)
        layout.addLayout(controls)
        self.heard_label = QLabel("", objectName="heard", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        layout.addWidget(self.heard_label)

        # "Should I…?" banner for risky actions
        self.banner = QFrame(objectName="banner", visible=False)
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 10, 10, 10)
        self.banner_label = QLabel(wordWrap=True)
        bl.addWidget(self.banner_label, 1)
        bl.addWidget(button("Yes", "primary", lambda: self._answer(True)))
        bl.addWidget(button("No", slot=lambda: self._answer(False)))
        layout.addWidget(self.banner)

        self.chat = ChatView()
        layout.addWidget(self.chat, 1)

        composer = QFrame(objectName="composer")
        row = QHBoxLayout(composer)
        row.setContentsMargins(14, 5, 6, 5)
        row.setSpacing(10)
        self.input = QLineEdit(placeholderText="Message Blaze…")
        self.input.returnPressed.connect(self._send)
        self.speak_box = QCheckBox("Speak", checked=True)
        self.speak_box.setToolTip("Also say replies out loud")
        self.send_btn = button("", "round", self._send)
        self.send_btn.setIcon(icon("send", INK))
        self.send_btn.setIconSize(QSize(18, 18))
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.setToolTip("Send (Enter)")
        row.addWidget(self.input, 1)
        row.addWidget(self.speak_box)
        row.addWidget(self.send_btn)
        layout.addWidget(composer)

    def on_power(self, step: str) -> None:
        if step in ("starting", "stopping"):
            self.presence_label.setText("Starting Blaze and his model…" if step == "starting"
                                        else "Stopping Blaze and his model to free up RAM…")
        elif step:
            self.presence_label.setText(f"Couldn't do that: {step}")

    def _toggle_mute(self) -> None:
        self.client.request("mute", muted=not self.muted)

    def _show_mute(self) -> None:
        self.mute_btn.setText("Unmute" if self.muted else "Mute")
        self.mute_btn.setIcon(icon("mic" if self.muted else "mic-off", DANGER if self.muted else TEXT))
        self.mute_btn.setProperty("active", self.muted)
        repolish(self.mute_btn)

    def _answer(self, yes: bool) -> None:
        self.client.request("confirm", answer=yes)
        self.banner.hide()

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.client.request("chat", text=text, speak=self.speak_box.isChecked())

    def add_message(self, role: str, text: str, ts: float | None = None) -> None:
        self.chat.add(role, text, ts)

    def on_event(self, msg: dict) -> None:
        match msg["event"]:
            case "state":
                self._set_state(msg["state"])
            case "status":
                self._set_state(msg["state"])
                if msg["muted"] != self.muted:
                    self.muted = msg["muted"]
                    self._show_mute()
                self.orb.level = msg.get("mic_level", 0.0)
                self.presence_label.setText(presence_text(msg.get("eyes"), self.user_name))
            case "message":
                self.chat.stick = True
                self.add_message(msg["role"], msg["text"], msg.get("ts"))
            case "heard":
                self.heard_label.setText(f"Heard: “{msg['text']}”")
            case "confirm":
                self.banner_label.setText(msg["question"])
                self.banner.show()
            case "confirm_done":
                self.banner.hide()

    def _set_state(self, state: str) -> None:
        self.orb.set_state(state)
        self.state_label.setText(STATE_LABELS.get(state, state))

    def set_connected(self, connected: bool) -> None:
        for w in (self.talk_btn, self.stop_btn, self.mute_btn, self.input, self.send_btn, self.speak_box):
            w.setEnabled(connected)
        if connected:
            def fill(ok: bool, messages) -> None:
                self.chat.clear()
                for m in messages if ok else []:
                    self.add_message(m["role"], m["text"], m["ts"])
            self.client.request("recent", fill)
        else:
            self._set_state("offline")
            if self.client.powering != "starting":
                self.presence_label.setText("Turned off, so his RAM is free. Press Turn on to bring him back.")
            self.banner.hide()


class CameraPage(QWidget):
    def __init__(self, client: DaemonClient):
        super().__init__(objectName="page")
        self.client = client
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)
        layout.addWidget(page_header(
            "Camera", "What Blaze sees. The camera stays closed until you open it here or say "
                      "“open the camera”. Frames stay on this laptop and are never saved."))

        frame = QFrame(objectName="card")
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(6, 6, 6, 6)
        self.view = QStackedWidget()
        placeholder = QWidget()
        pl = QVBoxLayout(placeholder)
        pl.addStretch()
        cam_icon = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        cam_icon.setPixmap(pixmap("camera", FAINT, 44))
        pl.addWidget(cam_icon)
        self.placeholder_text = QLabel("Camera closed", objectName="subtitle", alignment=Qt.AlignmentFlag.AlignCenter)
        pl.addWidget(self.placeholder_text)
        pl.addStretch()
        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(280)
        self.preview.setStyleSheet("background:#000; border-radius:10px;")
        self.view.addWidget(placeholder)
        self.view.addWidget(self.preview)
        fl.addWidget(self.view)
        layout.addWidget(frame, 1)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.chip_faces = QLabel(objectName="chip")
        self.chip_who = QLabel(objectName="chip")
        self.chip_learned = QLabel(objectName="chip")
        for c in (self.chip_faces, self.chip_who, self.chip_learned):
            chips.addWidget(c)
        self.info = QLabel(objectName="subtitle", wordWrap=True)
        chips.addWidget(self.info, 1)
        layout.addLayout(chips)

        row = QHBoxLayout()
        self.camera_on = False
        self.camera_btn = button("Open camera", "primary", self._toggle_camera, "camera")
        row.addWidget(self.camera_btn)
        self.learn_btn = button("Learn my face", "", self._learn, "face")
        row.addWidget(self.learn_btn)
        row.addStretch()
        row.addWidget(button("Forget my face", "danger", self._forget, "trash"))
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "Learning your face lets Blaze tell you apart from other people. Sit in front of the camera "
            "and move your head a little while it learns (about 10 seconds). Do it again in different "
            "lighting to make him better at recognizing you.", objectName="hint", wordWrap=True))

        self._set_chips(None)
        self._timer = QTimer(self, interval=500, timeout=self._refresh)

    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _refresh(self) -> None:
        if self.camera_on:
            self.client.request("snapshot", self._show)

    def _toggle_camera(self) -> None:
        self.camera_btn.setEnabled(False)

        def done(ok: bool, data) -> None:
            self.camera_btn.setEnabled(True)
            if ok and data not in ("The camera is open.", "The camera is closed."):
                QMessageBox.warning(self, "Blaze", str(data))

        self.client.request("camera", done, on=not self.camera_on)

    def _show(self, ok: bool, data) -> None:
        if not self.camera_on:
            return
        if not ok or not data:
            self.placeholder_text.setText("Opening the camera…")
            self.view.setCurrentIndex(0)
            return
        image = QImage.fromData(base64.b64decode(data), "JPG")
        pix = QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)
        self.view.setCurrentIndex(1)

    def _set_chips(self, eyes: dict | None) -> None:
        for c in (self.chip_faces, self.chip_who, self.chip_learned):
            c.setVisible(eyes is not None)
        if eyes:
            who = {"owner": "you", "someone": "someone"}.get(eyes["who"] or "", "nobody")
            self.chip_faces.setText(f"Faces: {eyes['faces']}")
            self.chip_who.setText(f"Recognized: {who}")
            self.chip_learned.setText("Face learned ✓" if eyes["knows_owner"] else "Face not learned yet")

    def on_status(self, msg: dict) -> None:
        on = bool(msg.get("camera_on"))
        if on != self.camera_on:
            self.camera_on = on
            self.camera_btn.setText("Close camera" if on else "Open camera")
            self.camera_btn.setObjectName("danger" if on else "primary")
            self.camera_btn.setIcon(icon("camera", DANGER if on else INK))
            repolish(self.camera_btn)
            if not on:
                self.preview.clear()
                self.placeholder_text.setText("Camera closed")
                self.view.setCurrentIndex(0)
            else:
                self.placeholder_text.setText("Opening the camera…")
        self.learn_btn.setEnabled(on)
        if not msg.get("camera_allowed", True):
            self._set_chips(None)
            self.info.setText("The camera is turned off in Settings.")
            self.camera_btn.setEnabled(False)
            return
        self.camera_btn.setEnabled(True)
        eyes = msg.get("eyes")
        if not on or not eyes:
            self._set_chips(None)
            self.info.setText("Blaze can't see anything until you open the camera.")
            return
        self.info.setText("")
        self._set_chips(eyes)

    def _learn(self) -> None:
        self.learn_btn.setEnabled(False)
        self.learn_btn.setText("Look at the camera…")

        def done(ok: bool, data) -> None:
            self.learn_btn.setEnabled(True)
            self.learn_btn.setText("Learn my face")
            if not ok:
                QMessageBox.warning(self, "Blaze", str(data))
            elif data < 5:
                QMessageBox.warning(self, "Blaze", "I couldn't see your face clearly. Make sure you're alone "
                                                   "in view and well lit, then try again.")
            else:
                QMessageBox.information(self, "Blaze", f"Got it — learned your face from {data} pictures.")

        self.client.request("learn_face", done)

    def _forget(self) -> None:
        if QMessageBox.question(self, "Blaze", "Forget your face?") == QMessageBox.StandardButton.Yes:
            self.client.request("forget_face")


class MemoryPage(QWidget):
    def __init__(self, client: DaemonClient):
        super().__init__(objectName="page")
        self.client = client
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(10)
        self.count = QLabel(objectName="chip")
        layout.addWidget(page_header(
            "Memory", "Things Blaze remembers about you. He adds to this himself as you talk, "
                      "and you can edit it here.", self.count))

        self.list = QListWidget(objectName="list")
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(
            lambda: self.remove_btn.setEnabled(bool(self.list.selectedItems())))
        layout.addWidget(self.list, 1)

        composer = QFrame(objectName="composer")
        row = QHBoxLayout(composer)
        row.setContentsMargins(14, 5, 6, 5)
        self.new_fact = QLineEdit(placeholderText="Teach Blaze something, e.g. “I study at night”")
        self.new_fact.returnPressed.connect(self._add)
        add = button("", "round", self._add)
        add.setIcon(icon("plus", INK))
        add.setIconSize(QSize(18, 18))
        add.setFixedSize(36, 36)
        add.setToolTip("Add (Enter)")
        row.addWidget(self.new_fact, 1)
        row.addWidget(add)
        layout.addWidget(composer)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("Select one or more and press Delete to remove them.", objectName="hint"))
        actions.addStretch()
        self.remove_btn = button("Remove selected", "danger", self._remove, "trash")
        self.remove_btn.setEnabled(False)
        actions.addWidget(self.remove_btn)
        layout.addLayout(actions)

        layout.addSpacing(8)
        card = QFrame(objectName="card")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(18, 16, 18, 16)
        cl.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(QLabel("Training data", objectName="section"))
        head.addStretch()
        self.msg_count = QLabel(objectName="chip")
        head.addWidget(self.msg_count)
        cl.addLayout(head)
        cl.addWidget(QLabel(
            "Every conversation is logged on this laptop so Blaze can later be fine-tuned on how you two "
            "actually talk. Export writes them in the standard chat fine-tuning format (JSONL).",
            objectName="subtitle", wordWrap=True))
        row2 = QHBoxLayout()
        row2.addWidget(button("Export training data", slot=self._export, icon_name="download"))
        row2.addWidget(button("Start a fresh conversation", slot=lambda: client.request("reset"),
                              icon_name="refresh"))
        row2.addStretch()
        cl.addLayout(row2)
        layout.addWidget(card)

    def showEvent(self, event) -> None:
        self.reload()
        super().showEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete and self.list.hasFocus():
            self._remove()
        else:
            super().keyPressEvent(event)

    def reload(self) -> None:
        def fill(ok: bool, facts) -> None:
            self.list.clear()
            for f in facts if ok else []:
                item = QListWidgetItem(f["text"])
                item.setData(Qt.ItemDataRole.UserRole, f["id"])
                self.list.addItem(item)
            n = self.list.count()
            self.count.setText(f"{n} thing{'s' if n != 1 else ''} remembered")
        self.client.request("facts", fill)

    def on_status(self, msg: dict) -> None:
        self.msg_count.setText(f"{msg.get('messages', 0):,} messages saved")

    def _add(self) -> None:
        text = self.new_fact.text().strip()
        if text:
            self.new_fact.clear()
            self.client.request("add_fact", lambda *_: self.reload(), text=text)

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.client.request("remove_fact", lambda *_: self.reload(), id=item.data(Qt.ItemDataRole.UserRole))

    def _export(self) -> None:
        def done(ok: bool, path) -> None:
            if ok:
                QMessageBox.information(self, "Blaze", f"Saved to:\n{path}")
            else:
                QMessageBox.warning(self, "Blaze", str(path))
        self.client.request("export_training", done)
