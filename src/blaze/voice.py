"""Text-to-speech: Kokoro (local, free) or ElevenLabs (cloud, paid).

Text is spoken sentence by sentence, synthesizing the next sentence while the
current one plays, so Blaze starts talking quickly even on long replies.
"""

import asyncio
import logging
import re

import httpx
import numpy as np
import sounddevice as sd

from .models import KOKORO_MODEL, KOKORO_VOICES
from .settings import VoiceSettings

log = logging.getLogger(__name__)

# Kokoro voice names start with a letter for their language.
KOKORO_LANGS = {"a": "en-us", "b": "en-gb", "e": "es", "f": "fr-fr", "h": "hi", "i": "it", "j": "ja", "p": "pt-br", "z": "cmn"}

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

RATE = 24000  # Kokoro and our ElevenLabs format both produce 24 kHz audio
SENTENCE_GAP = np.zeros(int(RATE * 0.12), dtype=np.float32)


def split_sentences(text: str) -> list[str]:
    # Models sometimes sneak in markdown; don't read symbols or list bullets aloud.
    text = re.sub(r"^\s*(?:[-*•]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_#`]+", "", text)
    return [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]


class Voice:
    def __init__(self, settings: VoiceSettings):
        self.settings = settings
        self.speaking = False
        self._kokoro = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        if settings.engine == "kokoro":
            self._load_kokoro()

    def _load_kokoro(self) -> None:
        from kokoro_onnx import Kokoro

        if not KOKORO_MODEL.exists():
            log.error("Kokoro model missing — run `blaze setup`")
            return
        self._kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))

    def voices(self) -> list[str]:
        return sorted(self._kokoro.get_voices()) if self._kokoro else []

    # synthesis ---------------------------------------------------------------

    async def _synth(self, text: str) -> tuple[np.ndarray, int] | None:
        s = self.settings
        try:
            if s.engine == "kokoro" and self._kokoro:
                lang = KOKORO_LANGS.get(s.kokoro_voice[:1], "en-us")
                return await asyncio.to_thread(self._kokoro.create, text, voice=s.kokoro_voice, speed=s.speed, lang=lang)
            if s.engine == "elevenlabs":
                return await self._elevenlabs(text)
        except Exception:
            log.exception("Speech synthesis failed")
        return None

    async def _elevenlabs(self, text: str) -> tuple[np.ndarray, int] | None:
        s = self.settings
        if not (s.elevenlabs_api_key and s.elevenlabs_voice_id):
            log.error("ElevenLabs needs an API key and voice ID in settings")
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{s.elevenlabs_voice_id}",
                params={"output_format": "pcm_24000"},
                headers={"xi-api-key": s.elevenlabs_api_key},
                json={"text": text, "model_id": s.elevenlabs_model, "voice_settings": {"speed": s.speed}},
            )
            r.raise_for_status()
        return np.frombuffer(r.content, dtype=np.int16).astype(np.float32) / 32768.0, 24000

    # playback ----------------------------------------------------------------

    def _open_stream(self) -> sd.OutputStream:
        # One stream per reply: opening a new one per sentence adds a gap each time,
        # which is very noticeable on Bluetooth headphones.
        stream = sd.OutputStream(samplerate=RATE, channels=1, dtype="float32",
                                 device=self.settings.output_device or None)
        stream.start()
        return stream

    async def _play(self, stream: sd.OutputStream, audio: np.ndarray, rate: int) -> None:
        if rate != RATE:
            audio = np.interp(np.arange(0, len(audio), rate / RATE), np.arange(len(audio)), audio)
        audio = np.clip(audio * self.settings.volume, -1.0, 1.0).astype(np.float32)
        chunk = RATE // 10  # write 100 ms at a time so "stop" takes effect quickly
        for start in range(0, len(audio), chunk):
            if self._stop.is_set():
                return
            await asyncio.to_thread(stream.write, audio[start:start + chunk])

    async def say(self, text: str) -> None:
        if self.settings.engine == "off":
            return
        sentences = split_sentences(text)
        if not sentences:
            return
        async with self._lock:
            self._stop.clear()
            self.speaking = True
            upcoming = asyncio.create_task(self._synth(sentences[0]))
            stream = None
            try:
                stream = self._open_stream()
                for i in range(len(sentences)):
                    result = await upcoming
                    if i + 1 < len(sentences):
                        upcoming = asyncio.create_task(self._synth(sentences[i + 1]))
                    if self._stop.is_set():
                        break
                    if result is not None:
                        await self._play(stream, *result)
                        await self._play(stream, SENTENCE_GAP, RATE)
            except Exception:
                log.exception("Playback failed")
            finally:
                upcoming.cancel()
                if stream is not None:
                    # stop() lets buffered audio finish; abort() cuts it off when asked to stop.
                    await asyncio.to_thread(stream.abort if self._stop.is_set() else stream.stop)
                    stream.close()
                await asyncio.sleep(0.25)  # let the room echo die down before listening again
                self.speaking = False

    def stop(self) -> None:
        self._stop.set()

    async def chime(self) -> None:
        """A short two-note 'I'm listening' sound."""
        notes = []
        for freq in (660, 990):
            t = np.linspace(0, 0.09, int(RATE * 0.09), endpoint=False)
            envelope = np.minimum(1, np.minimum(t, t[::-1]) * 60)
            notes.append(0.25 * np.sin(2 * np.pi * freq * t) * envelope)
        async with self._lock:
            self._stop.clear()
            stream = self._open_stream()
            try:
                await self._play(stream, np.concatenate(notes), RATE)
                await asyncio.to_thread(stream.stop)
            finally:
                stream.close()
