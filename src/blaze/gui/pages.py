"""The Home, Camera and Memory pages of the desktop app."""

import base64
import html
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

from .client import DaemonClient
from .orb import Orb
from .style import ACCENT, MUTED, STATE_LABELS


def page_header(title: str, subtitle: str) -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 8)
    t = QLabel(title, objectName="title")
    s = QLabel(subtitle, objectName="subtitle", wordWrap=True)
    layout.addWidget(t)
    layout.addWidget(s)
    return box


def button(text: str, kind: str = "", slot=None) -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if slot:
        b.clicked.connect(slot)
    return b


def presence_text(eyes: dict | None, user: str) -> str:
    if not eyes:
        return "Camera off"
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


class HomePage(QWidget):
    def __init__(self, client: DaemonClient):
        super().__init__(objectName="page")
        self.client = client
        self.user_name = "you"
        self.muted = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)

        self.orb = Orb()
        layout.addWidget(self.orb)
        self.state_label = QLabel(STATE_LABELS["offline"], objectName="stateLabel",
                                  alignment=Qt.AlignmentFlag.AlignCenter)
        self.presence_label = QLabel("", objectName="subtitle", alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.state_label)
        layout.addWidget(self.presence_label)

        controls = QHBoxLayout()
        controls.addStretch()
        self.talk_btn = button("Talk", "primary", lambda: client.request("talk"))
        self.stop_btn = button("Stop talking", slot=lambda: client.request("stop"))
        self.mute_btn = button("Mute mic", slot=self._toggle_mute)
        for b in (self.talk_btn, self.stop_btn, self.mute_btn):
            controls.addWidget(b)
        controls.addStretch()
        layout.addLayout(controls)
        self.heard_label = QLabel("", objectName="hint", alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        layout.addWidget(self.heard_label)

        # "Should I…?" banner for risky actions
        self.banner = QFrame(objectName="banner", visible=False)
        bl = QHBoxLayout(self.banner)
        self.banner_label = QLabel(wordWrap=True)
        bl.addWidget(self.banner_label, 1)
        bl.addWidget(button("Yes", "primary", lambda: self._answer(True)))
        bl.addWidget(button("No", slot=lambda: self._answer(False)))
        layout.addWidget(self.banner)

        self.chat = QTextBrowser(objectName="chat", openExternalLinks=True)
        layout.addWidget(self.chat, 1)

        row = QHBoxLayout()
        self.input = QLineEdit(placeholderText="Type to Blaze…")
        self.input.returnPressed.connect(self._send)
        self.speak_box = QCheckBox("Speak replies", checked=True)
        row.addWidget(self.input, 1)
        row.addWidget(self.speak_box)
        row.addWidget(button("Send", "primary", self._send))
        layout.addLayout(row)

    def _toggle_mute(self) -> None:
        self.client.request("mute", muted=not self.muted)

    def _answer(self, yes: bool) -> None:
        self.client.request("confirm", answer=yes)
        self.banner.hide()

    def _send(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.client.request("chat", text=text, speak=self.speak_box.isChecked())

    def add_message(self, role: str, text: str, ts: float | None = None) -> None:
        who, color = ("You", MUTED) if role == "user" else ("Blaze", ACCENT)
        stamp = time.strftime("%H:%M", time.localtime(ts))
        self.chat.append(
            f'<p style="margin:10px 0 2px 0"><b style="color:{color}">{who}</b>'
            f'<span style="color:{MUTED}; font-size:11px">&nbsp;&nbsp;{stamp}</span></p>'
            f'<p style="margin:0 0 6px 0">{html.escape(text)}</p>'
        )
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())

    def on_event(self, msg: dict) -> None:
        match msg["event"]:
            case "state":
                self._set_state(msg["state"])
            case "status":
                self._set_state(msg["state"])
                self.muted = msg["muted"]
                self.mute_btn.setText("Unmute mic" if self.muted else "Mute mic")
                self.orb.level = msg.get("mic_level", 0.0)
                self.presence_label.setText(presence_text(msg.get("eyes"), self.user_name))
            case "message":
                self.add_message(msg["role"], msg["text"], msg.get("ts"))
            case "heard":
                self.heard_label.setText(f"Mic heard: “{msg['text']}”")
            case "confirm":
                self.banner_label.setText(msg["question"])
                self.banner.show()
            case "confirm_done":
                self.banner.hide()

    def _set_state(self, state: str) -> None:
        self.orb.set_state(state)
        self.state_label.setText(STATE_LABELS.get(state, state))

    def set_connected(self, connected: bool) -> None:
        for w in (self.talk_btn, self.stop_btn, self.mute_btn, self.input):
            w.setEnabled(connected)
        if connected:
            def fill(ok: bool, messages) -> None:
                self.chat.clear()
                for m in messages if ok else []:
                    self.add_message(m["role"], m["text"], m["ts"])
            self.client.request("recent", fill)
        else:
            self._set_state("offline")
            self.presence_label.setText("Start him from Settings → Startup, or run: systemctl --user start blaze")
            self.banner.hide()


class CameraPage(QWidget):
    def __init__(self, client: DaemonClient):
        super().__init__(objectName="page")
        self.client = client
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.addWidget(page_header(
            "Camera", "What Blaze sees. Frames stay on this laptop and are never saved — "
                      "only what you see here, live."))

        self.preview = QLabel("No picture", alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(300)
        self.preview.setStyleSheet("background:#000; border-radius:12px; color:#777;")
        layout.addWidget(self.preview, 1)

        self.info = QLabel(objectName="subtitle", wordWrap=True)
        layout.addWidget(self.info)

        row = QHBoxLayout()
        self.learn_btn = button("Learn my face", "primary", self._learn)
        row.addWidget(self.learn_btn)
        row.addWidget(button("Forget my face", "danger", self._forget))
        row.addStretch()
        layout.addLayout(row)
        layout.addWidget(QLabel(
            "Learning your face lets Blaze tell you apart from other people. Sit in front of the camera "
            "and move your head a little while it learns (about 10 seconds). You can do it again in "
            "different lighting to make him better at recognizing you.", objectName="hint", wordWrap=True))

        self._timer = QTimer(self, interval=500, timeout=self._refresh)

    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _refresh(self) -> None:
        self.client.request("snapshot", self._show)

    def _show(self, ok: bool, data) -> None:
        if not ok or not data:
            self.preview.setText("No picture — camera off or not available")
            return
        image = QImage.fromData(base64.b64decode(data), "JPG")
        pix = QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pix)

    def on_status(self, msg: dict) -> None:
        eyes = msg.get("eyes")
        if not eyes:
            self.info.setText("The camera is turned off in Settings.")
            return
        knows = "knows your face" if eyes["knows_owner"] else "hasn't learned your face yet"
        who = {"owner": "you", "someone": "someone"}.get(eyes["who"] or "", "nobody")
        self.info.setText(f"Faces in view: {eyes['faces']} · Recognized: {who} · Blaze {knows}")

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
        layout.addWidget(page_header(
            "Memory", "Things Blaze remembers about you. He adds to this himself as you talk, "
                      "and you can edit it here."))

        self.list = QListWidget(objectName="list")
        layout.addWidget(self.list, 1)

        row = QHBoxLayout()
        self.new_fact = QLineEdit(placeholderText="Teach Blaze something, e.g. “I study at night”")
        self.new_fact.returnPressed.connect(self._add)
        row.addWidget(self.new_fact, 1)
        row.addWidget(button("Add", "primary", self._add))
        row.addWidget(button("Remove selected", "danger", self._remove))
        layout.addLayout(row)

        layout.addSpacing(16)
        layout.addWidget(QLabel("Training", objectName="stateLabel"))
        self.stats = QLabel(objectName="subtitle", wordWrap=True)
        layout.addWidget(self.stats)
        row2 = QHBoxLayout()
        row2.addWidget(button("Export training data", slot=self._export))
        row2.addWidget(button("Start a fresh conversation", slot=lambda: client.request("reset")))
        row2.addStretch()
        layout.addLayout(row2)

    def showEvent(self, event) -> None:
        self.reload()
        super().showEvent(event)

    def reload(self) -> None:
        def fill(ok: bool, facts) -> None:
            self.list.clear()
            for f in facts if ok else []:
                item = QListWidgetItem(f["text"])
                item.setData(Qt.ItemDataRole.UserRole, f["id"])
                self.list.addItem(item)
        self.client.request("facts", fill)

    def on_status(self, msg: dict) -> None:
        self.stats.setText(
            f"{msg.get('messages', 0)} messages saved. Every conversation is logged on this laptop so Blaze "
            "can later be fine-tuned on how you two actually talk. Export writes them in the standard "
            "chat fine-tuning format (JSONL).")

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
