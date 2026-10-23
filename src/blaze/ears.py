"""Microphone capture, cut into utterances with voice-activity detection.

Audio arrives on a PortAudio thread, gets handed to the asyncio loop, and is
grouped into "someone said something" chunks that the daemon transcribes.
"""

import asyncio
import collections
import logging
import time
from collections.abc import AsyncIterator, Callable
from typing import NamedTuple

import numpy as np
import sounddevice as sd
import webrtcvad

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000

START_WINDOW = 10  # frames (300 ms) looked at to decide speech started
START_RATIO = 0.6
END_WINDOW = 40  # frames (1.2 s) of mostly silence ends an utterance — room for pauses mid-sentence
END_RATIO = 0.9
MIN_SPEECH_FRAMES = 10  # ignore blips shorter than 300 ms
MAX_UTTERANCE_FRAMES = 20_000 // FRAME_MS  # 20 s


class Utterance(NamedTuple):
    audio: np.ndarray  # int16, 16 kHz mono
    started: float  # time.monotonic() when the person started talking


class Ears:
    def __init__(self, device: str = "", aggressiveness: int = 2):
        self.device = device or None
        self.vad = webrtcvad.Vad(aggressiveness)
        self.muted = False  # set while Blaze is speaking (on speakers) so he doesn't hear himself
        self.on_speech_start: Callable[[], None] | None = None  # lets the user interrupt him
        self._current: list[bytes] | None = None  # the utterance being heard right now
        self.level = 0.0  # last frame loudness 0..1, for the UI
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=500)

    def _open_stream(self, loop: asyncio.AbstractEventLoop) -> sd.RawInputStream:
        def callback(data, frames, time_info, status):
            try:
                loop.call_soon_threadsafe(self._queue.put_nowait, bytes(data))
            except (asyncio.QueueFull, RuntimeError):
                pass  # loop is behind or shutting down; dropping audio is fine

        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=FRAME_SAMPLES,
            channels=1,
            dtype="int16",
            device=self.device,
            callback=callback,
        )
        stream.start()
        return stream

    async def utterances(self) -> AsyncIterator[Utterance]:
        """Yield one Utterance per thing said. Reopens the mic if it drops."""
        loop = asyncio.get_running_loop()
        while True:
            try:
                stream = self._open_stream(loop)
            except Exception as e:
                log.warning("Microphone unavailable (%s); retrying in 5 s", e)
                await asyncio.sleep(5)
                continue
            log.info("Microphone open: %s", stream.device)
            try:
                async for utterance in self._segment(stream):
                    yield utterance
            finally:
                stream.close()
            log.warning("Microphone stream stopped; reopening")
            await asyncio.sleep(1)

    async def _segment(self, stream: sd.RawInputStream) -> AsyncIterator[Utterance]:
        recent: collections.deque[tuple[bytes, bool]] = collections.deque(maxlen=START_WINDOW)
        tail: collections.deque[bool] = collections.deque(maxlen=END_WINDOW)
        voiced: list[bytes] = []
        speaking = False
        started = 0.0
        silent_checks = 0

        while stream.active:
            try:
                frame = await asyncio.wait_for(self._queue.get(), timeout=2)
            except TimeoutError:
                # A live mic always delivers frames, even in a quiet room. None for
                # 10 s means the device died (unplugged, Bluetooth dropped, resume).
                silent_checks += 1
                if silent_checks >= 5:
                    return
                continue
            silent_checks = 0
            if len(frame) != FRAME_SAMPLES * 2:
                continue
            samples = np.frombuffer(frame, dtype=np.int16)
            self.level = min(1.0, float(np.abs(samples).mean()) / 3000)

            if self.muted:
                recent.clear()
                voiced, speaking, self._current = [], False, None
                continue

            is_speech = self.vad.is_speech(frame, SAMPLE_RATE)
            if not speaking:
                recent.append((frame, is_speech))
                if len(recent) == START_WINDOW and sum(s for _, s in recent) >= START_RATIO * START_WINDOW:
                    speaking = True
                    started = time.monotonic() - START_WINDOW * FRAME_MS / 1000
                    self._current = voiced
                    if self.on_speech_start:
                        self.on_speech_start()
                    voiced = [f for f, _ in recent]
                    tail.clear()
                    recent.clear()
            else:
                voiced.append(frame)
                tail.append(is_speech)
                silent = len(tail) == END_WINDOW and tail.count(False) >= END_RATIO * END_WINDOW
                if silent or len(voiced) >= MAX_UTTERANCE_FRAMES:
                    if len(voiced) - END_WINDOW >= MIN_SPEECH_FRAMES:
                        yield Utterance(np.frombuffer(b"".join(voiced), dtype=np.int16), started)
                    voiced, speaking, self._current = [], False, None


    @property
    def in_speech(self) -> bool:
        """True while someone is in the middle of saying something."""
        return self._current is not None

    def current_audio(self) -> np.ndarray | None:
        """What's been said so far in the utterance still in progress, if any."""
        frames = self._current
        return np.frombuffer(b"".join(frames), dtype=np.int16) if frames else None


def list_devices() -> dict[str, list[str]]:
    inputs, outputs = [], []
    for d in sd.query_devices():
        if d["max_input_channels"] > 0:
            inputs.append(d["name"])
        if d["max_output_channels"] > 0:
            outputs.append(d["name"])
    return {"inputs": inputs, "outputs": outputs}
