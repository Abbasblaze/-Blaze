"""The Settings page: every knob Blaze has, saved to settings.json."""

import subprocess

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)

from ..settings import Settings
from .client import DaemonClient
from .pages import button, page_header

STT_MODELS = ["tiny.en", "base.en", "small.en", "medium.en", "tiny", "base", "small", "medium"]
LANGUAGES = ["en", "auto", "ur", "hi", "ar", "es", "fr", "de"]
SENSITIVITY = ["Very sensitive (0)", "Sensitive (1)", "Normal (2)", "Strict — noisy room (3)"]


def combo(items: list[str], editable: bool = False) -> QComboBox:
    c = QComboBox(editable=editable)
    c.addItems(items)
    return c


def set_combo(c: QComboBox, value: str, items: list[str] | None = None) -> None:
    """Fill a combo (keeping the current value even if it's not in the list) and select value."""
    if items is not None:
        c.clear()
        c.addItems(items)
    if c.findText(value) < 0:
        c.addItem(value)
    c.setCurrentText(value)


def spin(lo: float, hi: float, step: float = 1, decimals: int = 0, suffix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox(decimals=decimals)
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setSuffix(suffix)
    return s


def hint(text: str) -> QLabel:
    return QLabel(text, objectName="hint", wordWrap=True)


def user_service(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args, "blaze.service"], capture_output=True, text=True)


class SettingsPage(QWidget):
    def __init__(self, client: DaemonClient, notify):
        super().__init__(objectName="page")
        self.client = client
        self.notify = notify  # shows a message in the status bar

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 12)
        outer.addWidget(page_header("Settings", "Changes apply as soon as you press Save."))

        scroll = QScrollArea(widgetResizable=True)
        body = QWidget()
        self.form = QVBoxLayout(body)
        self.form.setContentsMargins(0, 0, 12, 0)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._build_persona()
        self._build_brain()
        self._build_voice()
        self._build_listening()
        self._build_camera()
        self._build_hands()
        self._build_startup()
        self.form.addStretch()

        bar = QHBoxLayout()
        bar.addStretch()
        bar.addWidget(button("Revert", slot=self.load))
        bar.addWidget(button("Save", "primary", self.save))
        outer.addLayout(bar)

        self.load()

    def _group(self, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.form.addWidget(box)
        return form

    # building --------------------------------------------------------------------

    def _build_persona(self) -> None:
        f = self._group("Personality")
        self.name = QLineEdit()
        self.user_name = QLineEdit()
        self.personality = QPlainTextEdit()
        self.personality.setMinimumHeight(110)
        f.addRow("His name", self.name)
        f.addRow("Your name", self.user_name)
        f.addRow("Personality", self.personality)
        f.addRow("", hint("Describe how Blaze should act — tone, humor, how serious or chill he is."))

    def _build_brain(self) -> None:
        f = self._group("Brain")
        self.model = combo([], editable=True)
        self.vision_model = combo([], editable=True)
        self.temperature = spin(0, 2, 0.1, 2)
        self.history_turns = spin(0, 50)
        self.ollama_host = QLineEdit()
        f.addRow("Chat model", self.model)
        f.addRow("Vision model", self.vision_model)
        f.addRow("Creativity", self.temperature)
        f.addRow("Conversation memory", self.history_turns)
        f.addRow("Ollama address", self.ollama_host)
        f.addRow("", hint("Models come from Ollama. To add one: ollama pull <name>. Creativity: lower is more "
                          "focused, higher is more playful. Conversation memory = how many back-and-forths "
                          "he keeps in mind."))

    def _build_voice(self) -> None:
        f = self._group("Voice")
        self.engine = combo(["kokoro", "elevenlabs", "off"])
        self.kokoro_voice = combo([])
        self.speed = spin(0.5, 2, 0.05, 2, "×")
        self.volume = spin(0, 2, 0.1, 1, "×")
        self.output_device = combo([])
        self.el_key = QLineEdit(echoMode=QLineEdit.EchoMode.Password, placeholderText="xi-…")
        self.el_voice = QLineEdit(placeholderText="voice ID from elevenlabs.io")
        self.el_model = QLineEdit()
        test = button("Save & test voice", slot=self._test_voice)
        f.addRow("Engine", self.engine)
        f.addRow("Kokoro voice", self.kokoro_voice)
        f.addRow("Speed", self.speed)
        f.addRow("Volume", self.volume)
        f.addRow("Speaker", self.output_device)
        f.addRow("ElevenLabs API key", self.el_key)
        f.addRow("ElevenLabs voice ID", self.el_voice)
        f.addRow("ElevenLabs model", self.el_model)
        f.addRow("", test)
        f.addRow("", hint("Kokoro runs on your laptop for free. Voice names: am_/af_ = American male/female, "
                          "bm_/bf_ = British, hm_/hf_ = Hindi. ElevenLabs sounds best but is paid per character."))

    def _build_listening(self) -> None:
        f = self._group("Listening")
        self.listen_enabled = QCheckBox("Listen for my voice")
        self.wake_words = QLineEdit()
        self.follow_up = spin(0, 300, 5, 0, " s")
        self.stt_model = combo(STT_MODELS, editable=True)
        self.language = combo(LANGUAGES, editable=True)
        self.input_device = combo([])
        self.vad = combo(SENSITIVITY)
        self.interruptible = QCheckBox("Let me talk over him (use with headphones)")
        f.addRow("", self.listen_enabled)
        f.addRow("Wake words", self.wake_words)
        f.addRow("Stay in conversation for", self.follow_up)
        f.addRow("Speech model", self.stt_model)
        f.addRow("Language", self.language)
        f.addRow("Microphone", self.input_device)
        f.addRow("Voice detection", self.vad)
        f.addRow("", self.interruptible)
        f.addRow("", hint("Wake words are comma-separated; extra spellings help when the name gets misheard. "
                          "Speech models: tiny/base are fast, small is more accurate. Models ending in .en are "
                          "English-only — for Urdu or Hindi pick 'small' and set the language."))

    def _build_camera(self) -> None:
        f = self._group("Camera")
        self.cam_enabled = QCheckBox("Let Blaze open the camera when I ask")
        self.cam_device = QComboBox()
        self.glance = spin(0, 120, 1, 1, " min")
        self.away_after = spin(10, 3600, 10, 0, " s")
        self.greet_after = spin(0, 600, 5, 0, " min")
        self.break_after = spin(0, 600, 5, 0, " min")
        f.addRow("", self.cam_enabled)
        f.addRow("Camera", self.cam_device)
        f.addRow("Look around every", self.glance)
        f.addRow("Count me as away after", self.away_after)
        f.addRow("Greet me after being away", self.greet_after)
        f.addRow("Remind me to take a break after", self.break_after)
        f.addRow("", hint("Set any of these to 0 to turn it off. 'Look around' uses the vision model to "
                          "understand what you're doing, so he has context when you talk."))

    def _build_hands(self) -> None:
        f = self._group("Computer control")
        self.allow_commands = QCheckBox("Let Blaze run terminal commands")
        self.confirm_risky = QCheckBox("Ask me before risky actions (recommended)")
        self.search_roots = QLineEdit()
        f.addRow("", self.allow_commands)
        f.addRow("", self.confirm_risky)
        f.addRow("Search folders", self.search_roots)
        f.addRow("", hint("Read-only commands like ls or df run straight away; anything else is confirmed "
                          "first by voice or with the Yes/No bar on the Home page."))

    def _build_startup(self) -> None:
        f = self._group("Startup")
        self.autostart = QCheckBox("Start Blaze when I log in")
        self.autostart.toggled.connect(self._set_autostart)
        row = QHBoxLayout()
        row.addWidget(button("Start", slot=lambda: self._service("start")))
        row.addWidget(button("Restart", slot=lambda: self._service("restart")))
        row.addWidget(button("Stop", "danger", slot=lambda: self._service("stop")))
        row.addStretch()
        f.addRow("", self.autostart)
        f.addRow("Blaze service", row)

    # loading / saving ------------------------------------------------------------

    def load(self) -> None:
        s = Settings.load()
        self.name.setText(s.persona.name)
        self.user_name.setText(s.persona.user_name)
        self.personality.setPlainText(s.persona.personality)

        set_combo(self.model, s.brain.model)
        set_combo(self.vision_model, s.brain.vision_model)
        self.temperature.setValue(s.brain.temperature)
        self.history_turns.setValue(s.brain.history_turns)
        self.ollama_host.setText(s.brain.ollama_host)

        self.engine.setCurrentText(s.voice.engine)
        set_combo(self.kokoro_voice, s.voice.kokoro_voice)
        self.speed.setValue(s.voice.speed)
        self.volume.setValue(s.voice.volume)
        set_combo(self.output_device, s.voice.output_device or "Default")
        self.el_key.setText(s.voice.elevenlabs_api_key)
        self.el_voice.setText(s.voice.elevenlabs_voice_id)
        self.el_model.setText(s.voice.elevenlabs_model)

        self.listen_enabled.setChecked(s.listening.enabled)
        self.wake_words.setText(", ".join(s.listening.wake_words))
        self.follow_up.setValue(s.listening.follow_up_seconds)
        set_combo(self.stt_model, s.listening.stt_model)
        set_combo(self.language, s.listening.language)
        set_combo(self.input_device, s.listening.input_device or "Default")
        self.vad.setCurrentIndex(s.listening.vad_aggressiveness)
        self.interruptible.setChecked(s.listening.interruptible)

        self.cam_enabled.setChecked(s.camera.enabled)
        if self.cam_device.findData(s.camera.device) < 0:
            self.cam_device.addItem(f"/dev/video{s.camera.device}", s.camera.device)
        self.cam_device.setCurrentIndex(self.cam_device.findData(s.camera.device))
        self.glance.setValue(s.camera.glance_minutes)
        self.away_after.setValue(s.camera.away_after_seconds)
        self.greet_after.setValue(s.camera.greet_after_minutes)
        self.break_after.setValue(s.camera.break_reminder_minutes)

        self.allow_commands.setChecked(s.hands.allow_commands)
        self.confirm_risky.setChecked(s.hands.confirm_risky)
        self.search_roots.setText(", ".join(s.hands.search_roots))

        self.autostart.blockSignals(True)
        self.autostart.setChecked(user_service("is-enabled").stdout.strip() == "enabled")
        self.autostart.blockSignals(False)

    def fill_options(self, options: dict) -> None:
        """Populate drop-downs with what's actually installed (from the daemon)."""
        set_combo(self.model, self.model.currentText(), options.get("models", []))
        set_combo(self.vision_model, self.vision_model.currentText(), options.get("models", []))
        set_combo(self.kokoro_voice, self.kokoro_voice.currentText(), options.get("voices", []))
        devices = options.get("devices", {})
        set_combo(self.input_device, self.input_device.currentText(), ["Default", *devices.get("inputs", [])])
        set_combo(self.output_device, self.output_device.currentText(), ["Default", *devices.get("outputs", [])])
        current = self.cam_device.currentData() or 0
        self.cam_device.clear()
        for cam in options.get("cameras", []):
            self.cam_device.addItem(cam["name"], cam["index"])
        if self.cam_device.findData(current) < 0:
            self.cam_device.addItem(f"/dev/video{current}", current)
        self.cam_device.setCurrentIndex(self.cam_device.findData(current))

    def _collect(self) -> Settings:
        def split(text: str) -> list[str]:
            return [w.strip() for w in text.split(",") if w.strip()]

        def device(c: QComboBox) -> str:
            return "" if c.currentText() == "Default" else c.currentText()

        return Settings.model_validate({
            "persona": {"name": self.name.text().strip() or "Blaze", "user_name": self.user_name.text().strip(),
                        "personality": self.personality.toPlainText().strip()},
            "brain": {"model": self.model.currentText().strip(), "vision_model": self.vision_model.currentText().strip(),
                      "temperature": self.temperature.value(), "history_turns": int(self.history_turns.value()),
                      "ollama_host": self.ollama_host.text().strip()},
            "voice": {"engine": self.engine.currentText(), "kokoro_voice": self.kokoro_voice.currentText(),
                      "speed": self.speed.value(), "volume": self.volume.value(),
                      "output_device": device(self.output_device), "elevenlabs_api_key": self.el_key.text().strip(),
                      "elevenlabs_voice_id": self.el_voice.text().strip(),
                      "elevenlabs_model": self.el_model.text().strip()},
            "listening": {"enabled": self.listen_enabled.isChecked(), "wake_words": split(self.wake_words.text()),
                          "follow_up_seconds": self.follow_up.value(), "stt_model": self.stt_model.currentText(),
                          "language": self.language.currentText().strip(),
                          "input_device": device(self.input_device),
                          "vad_aggressiveness": self.vad.currentIndex(),
                          "interruptible": self.interruptible.isChecked()},
            "camera": {"enabled": self.cam_enabled.isChecked(), "device": self.cam_device.currentData() or 0,
                       "glance_minutes": self.glance.value(), "away_after_seconds": self.away_after.value(),
                       "greet_after_minutes": self.greet_after.value(),
                       "break_reminder_minutes": self.break_after.value()},
            "hands": {"allow_commands": self.allow_commands.isChecked(),
                      "confirm_risky": self.confirm_risky.isChecked(),
                      "search_roots": split(self.search_roots.text()) or ["~"]},
        })

    def save(self, then=None) -> None:
        try:
            settings = self._collect()
        except ValidationError as e:
            QMessageBox.warning(self, "Blaze", f"Some settings aren't valid:\n\n{e}")
            return
        settings.save()

        def reloaded(ok: bool, err) -> None:
            self.notify("Saved — Blaze is using the new settings." if ok else f"Saved, but Blaze couldn't reload: {err}")
            if ok and then:
                then()
        if self.client.connected:
            self.client.request("reload", reloaded)
        else:
            self.notify("Saved. Blaze will use these next time he starts.")

    def _test_voice(self) -> None:
        line = f"Hey {self.user_name.text().strip() or 'there'}, this is how I sound now. What do you think?"
        self.save(then=lambda: self.client.request("say", text=line))

    def _set_autostart(self, on: bool) -> None:
        result = user_service("enable" if on else "disable")
        if result.returncode == 0:
            self.notify("Blaze will start when you log in." if on else "Blaze won't start automatically anymore.")
        else:
            self.notify(f"Couldn't change autostart: {result.stderr.strip()}")

    def _service(self, action: str) -> None:
        result = user_service(action)
        self.notify(f"Blaze: {action} ok." if result.returncode == 0 else f"{action} failed: {result.stderr.strip()}")
