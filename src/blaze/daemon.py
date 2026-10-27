"""Blaze himself: wires ears, eyes, brain, voice and hands together.

Runs as a systemd user service, so he starts when you log in, restarts if he
crashes, and stops when the laptop shuts down.
"""

import asyncio
import base64
import collections
import logging
import re
import signal
import time
from pathlib import Path

from . import __version__
from .brain import Brain, BrainUnavailable
from .ears import Ears, list_devices
from .eyes import Eyes
from .hands import Hands, run, screenshot
from .idle import IDLE_AFTER, watch_input
from .ipc import IpcServer
from .memory import Memory
from .models import FACE_DETECTOR, FACE_RECOGNIZER
from .paths import DB_FILE, ensure_dirs
from .settings import Settings
from .stt import Transcriber
from .voice import Voice

log = logging.getLogger("blaze")

YES = re.compile(r"\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|confirm|please do)\b", re.I)
NO = re.compile(r"\b(no|nope|don't|do not|stop|cancel|never mind|wait)\b", re.I)
ECHO_DELAY = 0.6  # seconds of your speech to hear before deciding whether to stop talking
END_OF_TURN = 0.6  # extra quiet after you finish (on top of the mic's own pause) before he answers
MAX_HOLD = 2.5  # never wait longer than this after your last words for "more" (background voices)
STILL_TALKING = 4.0  # speech starting this soon after your last words is you carrying on, not someone else
REPLY_TIMEOUT = 180  # seconds; a reply that takes longer is given up, so Blaze can never freeze
WAKE_CHECK_SECONDS = 4  # how much of a long utterance gets re-checked for a misheard "Blaze"


def is_echo(heard: str, said: str) -> bool:
    """True if the mic just picked up Blaze's own words (or nothing real)."""
    heard_words = re.findall(r"[a-z']+", heard.lower())
    if not heard_words:
        return True
    said_words = set(re.findall(r"[a-z']+", said.lower()))
    return sum(w in said_words for w in heard_words) / len(heard_words) >= 0.6


GOODBYE = re.compile(r"\b(bye|goodbye|see you|that's all|that is all|go to sleep|stop listening)\b", re.I)
GLANCE_PROMPT = "In one short sentence, what is the person in front of the camera doing?"
WANTS_LOOK = re.compile(r"\b(see|look|watch|check|show|showing|holding|doing|wearing|what)\b", re.I)


def human_duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {minutes} minutes" if minutes else "")


def list_cameras() -> list[dict]:
    cams = []
    for dev in sorted(Path("/sys/class/video4linux").glob("video*")):
        index = int(dev.name.removeprefix("video"))
        name = (dev / "name").read_text().strip() if (dev / "name").exists() else dev.name
        cams.append({"index": index, "name": f"{name} (/dev/{dev.name})"})
    return cams


class Blaze:
    def __init__(self):
        ensure_dirs()
        self.settings = Settings.load()
        self.memory = Memory(DB_FILE)
        self.ipc = IpcServer(self.handle)
        self.state = "starting"
        self.muted = not self.settings.listening.enabled
        self.awake_until = 0.0  # follow-up window: listen without the wake word until then
        self.camera_on = False  # closed until you ask him to open it
        self._camera_opened = 0.0
        self.last_glance: tuple[float, str] | None = None
        self.last_break_reminder = 0.0
        # Auto-sleep: with you away, the model is unloaded to free its RAM.
        self.asleep = False
        self._last_active = time.monotonic()  # last time you used the laptop, talked to him or were seen
        self._input_idle = False  # no keyboard/mouse input for a while
        self.recent: collections.deque[dict] = collections.deque(maxlen=50)  # chat shown in the app
        self._said = ""  # what Blaze is saying / said last, to recognize his own echo
        self._echo_until = 0.0  # mic input before this time may contain his voice
        # Turn-taking: what you've said but he hasn't answered, and the answer in progress.
        self._pending: list[str] = []
        self._waiter: asyncio.Task | None = None
        self._answer_task: asyncio.Task | None = None
        self._answer_text = ""
        self._transcribing = False
        self._last_speech_end = 0.0
        self._last_heard = 0.0  # when your last words to him were heard
        self._finish_speaking = None  # set during a reply: says what's queued so far, then frees the speaker

        self.voice = Voice(self.settings.voice)
        self.stt: Transcriber | None = None
        self.quick_stt: Transcriber | None = None
        self.ears: Ears | None = None
        self.eyes: Eyes | None = None
        self.hands = Hands(self.settings.hands, self.memory, self.confirm, self.look_at_camera,
                           self.look_at_screen, self.open_camera, self.close_camera)
        self.brain = Brain(self.settings, self.hands, self.memory)

        self.loop: asyncio.AbstractEventLoop | None = None
        self._turn_lock = asyncio.Lock()
        self._confirm: asyncio.Future | None = None
        self._ear_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    # lifecycle -----------------------------------------------------------------

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            self.loop.add_signal_handler(sig, stop.set)

        await self.ipc.start()
        log.info("Blaze %s starting", __version__)
        self.spawn(self.brain.warm_up())
        await self._load_stt()
        self._start_eyes()
        self._ear_task = self.spawn(self._ear_loop())
        self.spawn(self._tick_loop())
        self.spawn(self._watch_loop())
        self.spawn(self._reminder_loop())
        self.spawn(self._sleep_loop())
        self.spawn(watch_input(self._on_input_idle))
        self.set_state("idle")
        await self.voice.chime()

        await stop.wait()
        log.info("Shutting down")
        self.voice.stop()
        if self.eyes:
            self.eyes.stop()
        for task in list(self._tasks):
            task.cancel()
        await self.ipc.stop()

    def spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return task

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("Background task crashed", exc_info=task.exception())

    async def _load_stt(self) -> None:
        ls = self.settings.listening
        try:
            self.stt = await asyncio.to_thread(Transcriber, ls.stt_model, ls.language)
            # A tiny, fast model just for "is that really you talking over me?"
            self.quick_stt = await asyncio.to_thread(Transcriber, "tiny.en", "en")
        except Exception:
            log.exception("Could not load speech recognition")
            self.stt = None

    def _start_eyes(self) -> None:
        if self.eyes:
            self.eyes.stop()
            self.eyes = None
        if not (self.camera_on and self.settings.camera.enabled):
            return
        if not (FACE_DETECTOR.exists() and FACE_RECOGNIZER.exists()):
            log.error("Face models missing — run `blaze setup`")
            return
        self.eyes = Eyes(self.settings.camera, lambda kind, data: self.loop.call_soon_threadsafe(
            self._on_sight, kind, data))
        self.eyes.start()

    async def reload(self) -> None:
        old, new = self.settings, Settings.load()
        self.settings = new
        self.brain.settings = new
        self.hands.settings = new.hands
        self.muted = not new.listening.enabled
        self.camera_on = self.camera_on and new.camera.enabled

        if new.brain.ollama_host != old.brain.ollama_host:
            self.brain = Brain(new, self.hands, self.memory)
        if new.voice != old.voice:
            self.voice.stop()
            if new.voice.engine == old.voice.engine:
                self.voice.settings = new.voice
            else:
                self.voice = await asyncio.to_thread(Voice, new.voice)
        if (new.listening.stt_model, new.listening.language) != (old.listening.stt_model, old.listening.language):
            await self._load_stt()
        if (new.listening.input_device, new.listening.vad_aggressiveness) != (
                old.listening.input_device, old.listening.vad_aggressiveness):
            self._ear_task.cancel()
            self._ear_task = self.spawn(self._ear_loop())
        if new.camera != old.camera:
            if self.eyes and new.camera.enabled and new.camera.device == old.camera.device:
                self.eyes.settings = new.camera
            else:
                await asyncio.to_thread(self._start_eyes)
        log.info("Settings reloaded")
        self.publish_status()

    # state & events ------------------------------------------------------------

    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.ipc.broadcast("state", state=state)

    def _resting_state(self) -> str:
        if self.muted:
            return "muted"
        if self.asleep:
            return "asleep"
        return "listening" if time.monotonic() < self.awake_until else "idle"

    def status(self) -> dict:
        return {
            "version": __version__,
            "state": self.state,
            "muted": self.muted,
            "asleep": self.asleep,
            "model": self.settings.brain.model,
            "voice": self.settings.voice.engine,
            "camera_on": self.camera_on,
            "camera_allowed": self.settings.camera.enabled,
            "eyes": self.eyes.status() if self.eyes else None,
            "mic_level": self.ears.level if self.ears else 0.0,
            "messages": self.memory.message_count(),
            "last_glance": self.last_glance,
        }

    def publish_status(self) -> None:
        self.ipc.broadcast("status", **self.status())

    def _owner_present(self) -> bool:
        """True if you're at the desk (or someone is, and Blaze hasn't learned your face yet)."""
        e = self.eyes
        return bool(e and e.present and (e.who == "owner" or e.owner is None))

    def context(self) -> str:
        user = self.settings.persona.user_name
        parts = [time.strftime("It's %A %d %B %Y, %H:%M.")]
        if self.eyes and self.eyes.camera_ok:
            if self.eyes.present:
                who = user if self.eyes.who == "owner" or self.eyes.owner is None else "someone you don't recognize"
                since = time.time() - (self.eyes.present_since or time.time())
                parts.append(f"{who} is at the desk (for {human_duration(since)}).")
            else:
                parts.append("Nobody is at the desk right now.")
        else:
            parts.append("Your camera is closed, so you can't see the desk.")
        if self.last_glance and time.time() - self.last_glance[0] < 30 * 60:
            ago = human_duration(time.time() - self.last_glance[0])
            parts.append(f"When you glanced at the camera {ago} ago you saw: {self.last_glance[1]}")
        return " ".join(parts)

    # talking ---------------------------------------------------------------------

    def _post(self, role: str, text: str) -> None:
        message = {"role": role, "text": text, "ts": time.time()}
        self.recent.append(message)
        self.ipc.broadcast("message", **message)

    async def speak(self, text: str) -> None:
        if not text:
            return
        log.info("Saying: %s", text)
        self._post("blaze", text)
        sentences: asyncio.Queue[str | None] = asyncio.Queue()
        sentences.put_nowait(text)
        sentences.put_nowait(None)
        await self._speak_from(sentences)

    async def _speak_from(self, sentences: asyncio.Queue[str | None]) -> None:
        """Say each sentence put on the queue, as it comes, until a None."""
        # On headphones the mic stays open so you can talk over him; on speakers he'd hear himself.
        if self.ears and not self.settings.listening.interruptible:
            self.ears.muted = True
        self.set_state("speaking")
        self._said, self._echo_until = "", float("inf")

        async def lines():
            while (sentence := await sentences.get()) is not None:
                self._said += " " + sentence
                yield sentence

        try:
            await self.voice.say_stream(lines())
        finally:
            self._echo_until = time.monotonic() + 0.5
            if self.ears:
                self.ears.muted = False
            self.set_state(self._resting_state())

    async def converse(self, text: str, speak: bool = True, post: bool = True) -> str:
        self.mark_active("you talked to me")
        async with self._turn_lock:
            self.awake_until = float("inf")  # stay in the conversation while thinking and talking
            if post:
                self._post("user", text)
            self.set_state("thinking")
            # Start talking as soon as the first sentence is written, instead of after the whole reply.
            sentences: asyncio.Queue[str | None] = asyncio.Queue()
            speaker: asyncio.Task | None = None

            def on_sentence(sentence: str) -> None:
                nonlocal speaker
                if speaker is None:
                    self.set_state("speaking")
                    speaker = asyncio.create_task(self._speak_from(sentences))
                sentences.put_nowait(sentence)

            async def finish_speaking() -> None:
                # A tool needs the speaker mid-reply (to ask "Should I…?"). The reply's speaker holds
                # it until the reply ends, so let it finish what it has and start a new one after.
                nonlocal speaker, sentences
                if speaker:
                    sentences.put_nowait(None)
                    await speaker
                    speaker, sentences = None, asyncio.Queue()
                    self.set_state("thinking")

            self._finish_speaking = finish_speaking
            try:
                async with asyncio.timeout(REPLY_TIMEOUT):
                    reply = await self.brain.respond(text, self.context(), on_sentence if speak else None)
            except BrainUnavailable as e:
                reply = str(e)
            except TimeoutError:
                log.error("Gave up on a reply after %d s", REPLY_TIMEOUT)
                reply = "Sorry, that took me way too long. Let's try again."
            except asyncio.CancelledError:  # you kept talking; the answer will be redone with everything
                if speaker:
                    self.voice.stop()
                    speaker.cancel()
                self.awake_until = time.monotonic() + self.settings.listening.follow_up_seconds
                self.set_state(self._resting_state())
                raise
            except Exception:
                log.exception("Thinking failed")
                reply = "Sorry, something went wrong in my head. Check my logs."
            finally:
                self._finish_speaking = None
            if speaker:
                sentences.put_nowait(None)
                log.info("Saying: %s", reply)
                self._post("blaze", reply)
                await speaker
            elif speak:
                await self.speak(reply)
            else:
                self._post("blaze", reply)
            self.awake_until = time.monotonic() + self.settings.listening.follow_up_seconds
            self.set_state(self._resting_state())
        self.spawn(self.brain.learn(text))  # quietly remember anything lasting about you
        return reply

    def _hear(self, text: str) -> None:
        """You said something to him: answer once you've finished talking."""
        self.awake_until = float("inf")  # you're talking to him now, so the rest of your sentence counts too
        self._last_heard = time.monotonic()
        self.mark_active("I heard you")  # start loading the model while you finish talking
        self._post("user", text)
        self._pending.append(text)
        if self._waiter is None or self._waiter.done():
            self._waiter = self.spawn(self._answer_when_done())

    def _you_are_talking(self) -> bool:
        return bool(self.ears and self.ears.in_speech) or self._transcribing

    async def _answer_when_done(self) -> None:
        """Wait until you've really finished (not just paused), then answer it all as one message.
        Voices in the background (a video, someone else in the room) could keep him waiting
        forever, so he waits at most MAX_HOLD after your last words."""
        while True:
            await asyncio.sleep(0.1)
            waiting = self._you_are_talking() or time.monotonic() - self._last_speech_end < END_OF_TURN
            if waiting and time.monotonic() - self._last_heard < MAX_HOLD:
                continue
            if self._answer_task and not self._answer_task.done():
                await asyncio.wait({self._answer_task})  # let the current answer finish, then re-check
                continue
            break
        if not self._pending:
            return
        self._answer_text = " ".join(self._pending)
        self._pending.clear()
        self._answer_task = self.spawn(self.converse(self._answer_text, post=False))

    async def proactive(self, instruction: str) -> None:
        """Say something on Blaze's own initiative — but never interrupt a conversation."""
        if self._turn_lock.locked() or self.voice.speaking:
            return
        async with self._turn_lock:
            try:
                line = await self.brain.quick(instruction, self.context())
            except BrainUnavailable as e:
                log.warning("Skipped proactive line: %s", e)
                return
            await self.speak(line)
            # He usually asks something ("how's it going?"), so let you answer without his name.
            self.awake_until = time.monotonic() + self.settings.listening.follow_up_seconds

    def _user_started_talking(self) -> None:
        """The ears heard sound begin while he's busy: check whether it's you, then make room."""
        busy = self.voice.speaking or (self._answer_task and not self._answer_task.done())
        if busy and self.settings.listening.interruptible:
            self.spawn(self._make_room_if_real())

    async def _make_room_if_real(self) -> None:
        """If that was really you talking (not noise, typing or his own echo), stop and listen."""
        await asyncio.sleep(ECHO_DELAY)
        audio = self.ears.current_audio() if self.ears else None
        stt = self.quick_stt or self.stt
        if audio is None or stt is None:
            return
        heard = await asyncio.to_thread(stt.transcribe, audio)
        if is_echo(heard, self._said):
            return
        task = self._answer_task
        if self.voice.speaking:
            log.info("Interrupted by: %s", heard)
            self.voice.stop()
        elif task and not task.done() and self.state == "thinking" and not self.brain.acted \
                and time.monotonic() - self._last_heard < STILL_TALKING:
            # You weren't finished: drop the half-made answer and redo it with everything you say.
            log.info("You're still talking (%s) — holding my answer", heard)
            task.cancel()
            self._pending.insert(0, self._answer_text)
            if self._waiter is None or self._waiter.done():
                self._waiter = self.spawn(self._answer_when_done())

    async def _transcribe(self, audio, in_conversation: bool) -> str:
        """Speech to text. The name hint is only used to rescue a misheard "Blaze" in real speech,
        because on its own it makes Whisper "hear" the name in background noise."""
        text = await asyncio.to_thread(self.stt.transcribe, audio)
        if not text or in_conversation or self._strip_wake_word(text) is not None:
            return text
        # Re-check only the start ("Hey Blaze, …"), so background chatter doesn't cost a second
        # full transcription that keeps the CPU busy and him deaf to you.
        name = self.settings.persona.name
        head = audio[:WAKE_CHECK_SECONDS * 16000]
        hinted = await asyncio.to_thread(self.stt.transcribe, head, name)
        if self._strip_wake_word(hinted) is None:
            return text
        return hinted if len(head) == len(audio) else await asyncio.to_thread(self.stt.transcribe, audio, name)

    async def wake(self) -> None:
        self.awake_until = time.monotonic() + max(self.settings.listening.follow_up_seconds, 6)
        self.set_state("listening")
        await self.voice.chime()

    def _strip_wake_word(self, text: str) -> str | None:
        """If the user addressed Blaze, return what they said to him; otherwise None."""
        words = "|".join(re.escape(w) for w in self.settings.listening.wake_words if w.strip())
        if not words:
            return None
        pattern = re.compile(rf"\b(?:(?:hey|hi|ok|okay|yo)[,\s]+)?(?:{words})\b[,.!?]?", re.I)
        if not pattern.search(text):
            return None
        return re.sub(r"\s+", " ", pattern.sub(" ", text, count=1)).strip(" ,.")

    async def _ear_loop(self) -> None:
        ls = self.settings.listening
        self.ears = Ears(ls.input_device, ls.vad_aggressiveness)
        self.ears.on_speech_start = self._user_started_talking
        async for utterance in self.ears.utterances():
            self._last_speech_end = time.monotonic()
            if self.muted or self.stt is None:
                continue
            in_conversation = utterance.started < self.awake_until
            self._transcribing = True
            try:
                text = await self._transcribe(utterance.audio, in_conversation)
            finally:
                self._transcribing = False
                self._last_speech_end = time.monotonic()
            if not text:
                continue
            if utterance.started < self._echo_until and is_echo(text, self._said):
                continue  # his own voice through the mic
            log.info("Heard: %s", text)
            self.ipc.broadcast("heard", text=text)  # so the app shows he's listening even when not addressed

            if self._confirm and not self._confirm.done():
                if NO.search(text):
                    self._confirm.set_result(False)
                elif YES.search(text):
                    self._confirm.set_result(True)
                continue

            # "Hey Blaze" starts a conversation; after that he answers everything until
            # you go quiet for a while or say bye. Judge by when you *started* talking,
            # so a long sentence that began in time still counts.
            command = self._strip_wake_word(text)
            if command is None:
                if not in_conversation:
                    continue
                command = text
            if GOODBYE.search(command) and len(command.split()) <= 6:
                self.awake_until = 0.0
                self.spawn(self.speak("Alright, just say Hey Blaze when you need me."))
                continue
            if len(command.strip(" .,!?")) < 2:  # just "Hey Blaze"
                self.spawn(self.wake())
                continue
            self._hear(command)

    # confirmation ---------------------------------------------------------------

    async def confirm(self, action: str) -> bool:
        """Ask before doing something risky. Answer by voice or in the app."""
        if self._finish_speaking:
            await self._finish_speaking()
        self._confirm = self.loop.create_future()
        self.ipc.broadcast("confirm", question=f"Should I {action}?")
        spoken = action if len(action) < 90 else action[:90] + "…"
        await self.speak(f"Should I {spoken}? Yes or no?")
        try:
            return await asyncio.wait_for(self._confirm, 45)
        except TimeoutError:
            return False
        finally:
            self._confirm = None
            self.ipc.broadcast("confirm_done")

    # seeing ----------------------------------------------------------------------

    async def set_camera(self, on: bool) -> str:
        """Open or close the webcam. It stays closed until you ask for it."""
        if on and not self.settings.camera.enabled:
            return "The camera is turned off in Settings, so I can't open it."
        if on != self.camera_on:
            self.camera_on = on
            self._camera_opened = time.monotonic()
            await asyncio.to_thread(self._start_eyes)
            if not on:
                self.last_glance = None
            log.info("Camera %s", "opened" if on else "closed")
            self.publish_status()
        if on and not await self._wait_for_picture():
            return "I opened the camera but I'm not getting a picture from it."
        if on and time.monotonic() - self._camera_opened < 1.5:
            await asyncio.sleep(1.5 - (time.monotonic() - self._camera_opened))  # let the exposure settle
        return "The camera is open." if on else "The camera is closed."

    async def _wait_for_picture(self, timeout: float = 6.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.eyes and self.eyes.camera_ok and self.eyes.snapshot_jpeg():
                return True
            await asyncio.sleep(0.2)
        return False

    async def open_camera(self) -> str:
        result = await self.set_camera(True)
        # "Open the camera and see what I'm doing": look right away instead of just opening it.
        if result == "The camera is open." and WANTS_LOOK.search(self.brain.last_user_text or ""):
            try:
                return f"The camera is open. {await self.look_at_camera('')}"
            except BrainUnavailable as e:
                return f"The camera is open, but looking through it failed: {e}"
        return result

    async def close_camera(self) -> str:
        return await self.set_camera(False)

    async def look_at_camera(self, question: str) -> str:
        if not self.camera_on:
            # Asking him to look is asking him to open it.
            result = await self.set_camera(True)
            if result != "The camera is open.":
                return result
        if not (self.eyes and self.eyes.camera_ok):
            return "The camera isn't working."
        image = self.eyes.snapshot_jpeg()
        if not image:
            return "I couldn't get a picture."
        # Small chat models often garble the question, so give the vision model the user's own words.
        asked = self.brain.last_user_text or question
        prompt = (f'The person in front of the camera asked: "{asked}". Answer it. If they are holding or '
                  "showing something, name exactly what it is (type, brand, colour, any visible text).")
        return await self.brain.see(image, prompt)

    async def look_at_screen(self, question: str) -> str:
        image = await asyncio.to_thread(screenshot)
        return await self.brain.see(image, question) if image else "I couldn't take a screenshot."

    def _on_sight(self, kind: str, data: dict) -> None:
        cam = self.settings.camera
        user = self.settings.persona.user_name
        self.memory.log_event(f"presence:{kind}", str(data))
        if self._owner_present():
            self.mark_active("I saw you")
        self.publish_status()
        if kind != "arrived" or not cam.greet_after_minutes:
            return
        if not self._owner_present():
            return
        away = data["away_seconds"]
        if away is None:
            self.spawn(self.proactive(f"{user} just sat down at the laptop and you're seeing them for the "
                                      "first time since you started up. Say a short, natural hello."))
        elif away > cam.greet_after_minutes * 60:
            self.spawn(self.proactive(f"{user} just came back to the desk after being away for "
                                      f"{human_duration(away)}. Welcome them back briefly."))

    # background loops ------------------------------------------------------------

    async def _tick_loop(self) -> None:
        """Keep the shown state fresh and notice when the laptop wakes from sleep."""
        drift = time.time() - time.monotonic()
        while True:
            await asyncio.sleep(1)
            new_drift = time.time() - time.monotonic()
            if new_drift - drift > 30:  # monotonic time doesn't advance while suspended
                log.info("Woke up from sleep after %s", human_duration(new_drift - drift))
                self.memory.log_event("resumed", str(int(new_drift - drift)))
            drift = new_drift
            if self.state in ("idle", "listening", "muted", "starting", "asleep"):
                self.set_state(self._resting_state())
            self.publish_status()

    async def _watch_loop(self) -> None:
        """Glance at the camera now and then, and nudge about breaks."""
        while True:
            await asyncio.sleep(30)
            cam, eyes = self.settings.camera, self.eyes
            if not self._owner_present():
                continue
            now = time.time()
            busy = self._turn_lock.locked() or self.voice.speaking

            if cam.glance_minutes and not busy and (
                    not self.last_glance or now - self.last_glance[0] >= cam.glance_minutes * 60):
                image = eyes.snapshot_jpeg()
                if image:
                    try:
                        seen = await self.brain.see(image, GLANCE_PROMPT)
                        self.last_glance = (now, seen)
                        self.memory.log_event("glance", seen)
                    except BrainUnavailable as e:
                        log.warning("Glance skipped: %s", e)

            sat = now - (eyes.present_since or now)
            gap = cam.break_reminder_minutes * 60
            if gap and sat > gap and now - self.last_break_reminder > gap:
                self.last_break_reminder = now
                self.spawn(self.proactive(
                    f"{self.settings.persona.user_name} has been sitting at the desk for {human_duration(sat)} "
                    "without a break. Suggest a short break — stretch, water, look away from the screen."))

    # auto-sleep -------------------------------------------------------------------

    def _on_input_idle(self, idle: bool) -> None:
        self._input_idle = idle
        if idle:  # your last input was IDLE_AFTER seconds ago
            self._last_active = max(self._last_active, time.monotonic() - IDLE_AFTER)
        else:
            self.mark_active("you're back on the laptop")

    def mark_active(self, why: str) -> None:
        """You're here: note it, and if he's asleep, load the model again."""
        self._last_active = time.monotonic()
        if self.asleep:
            self.asleep = False
            log.info("Waking up (%s): loading the model", why)
            self.memory.log_event("wake", why)
            self.set_state(self._resting_state())
            self.spawn(self.brain.warm_up())

    async def _sleep_loop(self) -> None:
        """Unload the model once you've been away for a while. Ears and voice stay on, so he
        still hears "Hey Blaze" and still gives reminders."""
        while True:
            await asyncio.sleep(30)
            if not self._input_idle or self._owner_present() or self._turn_lock.locked() or self.voice.speaking:
                self._last_active = time.monotonic()
                continue
            minutes = self.settings.brain.sleep_after_minutes
            away = time.monotonic() - self._last_active
            if self.asleep or not minutes or away < minutes * 60:
                continue
            self.asleep = True
            log.info("You've been away for %s: unloading the model to free its RAM", human_duration(away))
            self.memory.log_event("sleep", str(int(away)))
            self.set_state(self._resting_state())
            await self.brain.unload()

    async def _reminder_loop(self) -> None:
        """Give reminders when they're due. They're kept in the database and checked against the
        clock, so they survive a restart, and ones missed while the laptop was off or asleep are
        given as soon as he's back."""
        while True:
            for reminder in self.memory.due_reminders(time.time()):
                message, late = reminder["message"], time.time() - reminder["due"]
                run(["notify-send", "-a", self.settings.persona.name, "-u", "critical", "Reminder", message])
                if late > 5 * 60:
                    line = (f"Hey {self.settings.persona.user_name}, I missed this at "
                            f"{time.strftime('%-I:%M %p', time.localtime(reminder['due']))}: {message}")
                else:
                    line = f"Hey {self.settings.persona.user_name}, reminder: {message}"
                self.memory.log_event("reminder", message)
                async with self._turn_lock:  # after the current reply, never over it
                    await self.speak(line)
            await asyncio.sleep(5)

    # commands from the app / CLI ------------------------------------------------

    async def handle(self, cmd: str, args: dict):
        match cmd:
            case "status":
                return self.status()
            case "recent":
                return list(self.recent)
            case "chat":
                return await self.converse(args["text"], speak=args.get("speak", True))
            case "talk":
                self.spawn(self.wake())
            case "say":
                self.spawn(self.speak(args["text"]))
            case "stop":
                self.voice.stop()
            case "mute":
                self.muted = bool(args.get("muted", not self.muted))
                self.set_state(self._resting_state())
                return self.muted
            case "confirm":
                if self._confirm and not self._confirm.done():
                    self._confirm.set_result(bool(args.get("answer")))
            case "reload":
                await self.reload()
            case "reset":
                self.brain.reset()
            case "options":
                return {
                    "models": await self.brain.installed_models(),
                    "voices": self.voice.voices(),
                    "devices": list_devices(),
                    "cameras": list_cameras(),
                }
            case "facts":
                return self.memory.facts()
            case "add_fact":
                self.memory.add_fact(args["text"])
            case "remove_fact":
                self.memory.remove_fact(int(args["id"]))
            case "camera":
                return await self.set_camera(bool(args.get("on", not self.camera_on)))
            case "learn_face":
                if not self.eyes:
                    raise RuntimeError("Open the camera first.")
                return await asyncio.to_thread(self.eyes.enroll)
            case "forget_face":
                if self.eyes:
                    self.eyes.forget_owner()
            case "snapshot":
                image = self.eyes.snapshot_jpeg(70) if self.eyes else None
                return base64.b64encode(image).decode() if image else None
            case "export_training":
                return str(self.memory.export_training(self.brain.system_prompt()))
            case _:
                raise ValueError(f"Unknown command: {cmd}")
        return None


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(Blaze().run())
