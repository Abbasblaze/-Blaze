"""Speech-to-text with faster-whisper, running on the CPU so the GPU stays free for the LLM."""

import logging
import re

import numpy as np
from faster_whisper import WhisperModel

from .models import WHISPER_DIR

log = logging.getLogger(__name__)

# Whisper likes to "hear" these in silence or noise.
HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "okay"}


class Transcriber:
    def __init__(self, model_name: str = "base.en", language: str = "en"):
        log.info("Loading Whisper %s", model_name)
        try:  # use the copy on disk; only go online the first time a model is picked
            self.model = self._load(model_name, local_only=True)
        except Exception:
            self.model = self._load(model_name, local_only=False)
        # English-only models (".en") can only do English; otherwise None = auto-detect.
        self.language = "en" if model_name.endswith(".en") else (None if language == "auto" else language)

    @staticmethod
    def _load(model_name: str, local_only: bool) -> WhisperModel:
        return WhisperModel(model_name, device="cpu", compute_type="int8",
                            download_root=str(WHISPER_DIR), local_files_only=local_only)

    def transcribe(self, audio: np.ndarray, hint: str = "") -> str:
        """`hint` = words to expect (Blaze's name), so a short "Blaze" isn't heard as "Low"."""
        pcm = audio.astype(np.float32) / 32768.0
        segments, _ = self.model.transcribe(
            pcm,
            language=self.language,
            beam_size=1,
            condition_on_previous_text=False,
            vad_filter=False,
            hotwords=hint or None,
        )
        parts = [s.text for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0]
        text = " ".join(p.strip() for p in parts).strip()
        if text.lower().strip(" .!?,") in HALLUCINATIONS:
            return ""
        words = re.findall(r"[a-z']+", text.lower())
        # Noise often comes out as one word on repeat: "The The The", "A A A A A A".
        if len(words) >= 4 and max(words.count(w) for w in set(words)) / len(words) > 0.5:
            return ""
        return text
