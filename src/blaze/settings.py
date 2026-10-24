"""All of Blaze's settings, stored as JSON in ~/.config/blaze/settings.json.

The desktop app edits this file and then asks the daemon to reload it.
"""

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .paths import SETTINGS_FILE, ensure_dirs

log = logging.getLogger(__name__)

DEFAULT_PERSONALITY = (
    "Friendly, relaxed and witty, like a close friend who also happens to be brilliant with tech. "
    "Calm and confident, genuinely curious about what the user is working on, and honest when you "
    "don't know something. Casual, not formal: no 'sir'."
)


class PersonaSettings(BaseModel):
    name: str = "Blaze"
    user_name: str = "Friend"  # set your own name in Settings → Personality
    personality: str = DEFAULT_PERSONALITY


class BrainSettings(BaseModel):
    ollama_host: str = "http://127.0.0.1:11434"
    model: str = "qwen3.5:4b"
    vision_model: str = "qwen3.5:4b"
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    history_turns: int = Field(10, ge=0, le=50)


class VoiceSettings(BaseModel):
    engine: Literal["kokoro", "elevenlabs", "off"] = "kokoro"
    kokoro_voice: str = "am_michael"
    speed: float = Field(1.15, ge=0.5, le=2.0)
    volume: float = Field(1.0, ge=0.0, le=2.0)
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = ""
    elevenlabs_model: str = "eleven_flash_v2_5"
    output_device: str = ""  # empty = system default


class ListeningSettings(BaseModel):
    enabled: bool = True
    # Any of these appearing in what you say wakes Blaze up. Extra spellings
    # catch common mis-hearings of the name.
    wake_words: list[str] = ["blaze", "blaise", "blase", "blaze's", "blazes", "blazed", "blades", "glaze", "braze"]
    follow_up_seconds: float = Field(30.0, ge=0.0, le=300.0)  # stay in the conversation this long after a reply
    stt_model: str = "small.en"
    language: str = "en"  # "auto" to detect
    input_device: str = ""  # empty = system default
    vad_aggressiveness: int = Field(2, ge=0, le=3)
    # Keep the mic open while he talks, so you can interrupt him. Needs headphones —
    # through laptop speakers he'd hear (and interrupt) himself.
    interruptible: bool = True


class CameraSettings(BaseModel):
    enabled: bool = True
    device: int = 0
    glance_minutes: float = Field(15.0, ge=0.0)  # 0 = never glance on its own
    away_after_seconds: float = Field(90.0, ge=10.0)
    greet_after_minutes: float = Field(10.0, ge=0.0)  # 0 = never greet
    break_reminder_minutes: float = Field(60.0, ge=0.0)  # 0 = never remind


class HandsSettings(BaseModel):
    allow_commands: bool = True
    confirm_risky: bool = True
    search_roots: list[str] = ["~"]
    # "free" needs no account; "ollama" and "brave" use their search APIs with an API key.
    web_search_engine: Literal["free", "ollama", "brave"] = "free"
    web_search_api_key: str = ""


class Settings(BaseModel):
    persona: PersonaSettings = PersonaSettings()
    brain: BrainSettings = BrainSettings()
    voice: VoiceSettings = VoiceSettings()
    listening: ListeningSettings = ListeningSettings()
    camera: CameraSettings = CameraSettings()
    hands: HandsSettings = HandsSettings()

    @classmethod
    def load(cls) -> "Settings":
        ensure_dirs()
        if not SETTINGS_FILE.exists():
            settings = cls()
            settings.save()
            return settings
        try:
            return cls.model_validate(json.loads(SETTINGS_FILE.read_text()))
        except (OSError, ValueError, ValidationError) as e:
            log.error("Settings file is broken (%s); using defaults", e)
            return cls()

    def save(self) -> None:
        ensure_dirs()
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2))
        tmp.chmod(0o600)  # may hold an API key
        tmp.replace(SETTINGS_FILE)
