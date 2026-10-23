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
ECHO_DELAY = 1.0  # seconds of speech to hear before deciding whether to stop talking


def is_echo(heard: str, said: str) -> bool:
    """True if the mic just picked up Blaze's own words (or nothing real)."""
    heard_words = re.findall(r"[a-z']+", heard.lower())
    if not heard_words:
        return True
    said_words = set(re.findall(r"[a-z']+", said.lower()))
    return sum(w in said_words for w in heard_words) / len(heard_words) >= 0.6


GOODBYE = re.compile(r"\b(bye|goodbye|see you|that's all|that is all|go to sleep|stop listening)\b", re.I)
GLANCE_PROMPT = "In one short sentence, what is the person in front of the camera doing?"


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
        self.last_glance: tuple[float, str] | None = None
        self.last_break_reminder = 0.0
        self.recent: collections.deque[dict] = collections.deque(maxlen=50)  # chat shown in the app
        self._said = ""  # what Blaze is saying / said last, to recognize his own echo
        self._echo_until = 0.0  # mic input before this time may contain his voice
        self._waiting: list[str] = []  # things you said that haven't been answered yet
        self._answering = False

        self.voice = Voice(self.settings.voice)
        self.stt: Transcriber | None = None
        self.ears: Ears | None = None
        self.eyes: Eyes | None = None
        self.hands = Hands(self.settings.hands, self.memory, self.confirm,
                           self.look_at_camera, self.look_at_screen, self.remind)
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
        except Exception:
            log.exception("Could not load speech recognition")
            self.stt = None

    def _start_eyes(self) -> None:
        if self.eyes:
            self.eyes.stop()
            self.eyes = None
        if not self.settings.camera.enabled:
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
        return "listening" if time.monotonic() < self.awake_until else "idle"

    def status(self) -> dict:
        return {
            "version": __version__,
            "state": self.state,
            "muted": self.muted,
            "model": self.settings.brain.model,
            "voice": self.settings.voice.engine,
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
        # On headphones the mic stays open so you can talk over him; on speakers he'd hear himself.
        if self.ears and not self.settings.listening.interruptible:
            self.ears.muted = True
        self.set_state("speaking")
        self._said, self._echo_until = text, float("inf")
        try:
            await self.voice.say(text)
        finally:
            self._echo_until = time.monotonic() + 0.5
            if self.ears:
                self.ears.muted = False
            self.set_state(self._resting_state())

    async def converse(self, text: str, speak: bool = True) -> str:
        async with self._turn_lock:
            self.awake_until = float("inf")  # stay in the conversation while thinking and talking
            self._post("user", text)
            self.set_state("thinking")
            try:
                reply = await self.brain.respond(text, self.context())
            except BrainUnavailable as e:
                reply = str(e)
            except Exception:
                log.exception("Thinking failed")
                reply = "Sorry, something went wrong in my head. Check my logs."
            if speak:
                await self.speak(reply)
            else:
                self._post("blaze", reply)
            self.awake_until = time.monotonic() + self.settings.listening.follow_up_seconds
            self.set_state(self._resting_state())
        self.spawn(self.brain.learn(text))  # quietly remember anything lasting about you
        return reply

    def _queue_speech(self, text: str) -> None:
        """Collect what you say; anything said while he's busy is answered together as one turn."""
        self._waiting.append(text)
        if not self._answering:
            self.spawn(self._answer_waiting())

    async def _answer_waiting(self) -> None:
        self._answering = True
        try:
            while self._waiting:
                text = " ".join(self._waiting)
                self._waiting.clear()
                await self.converse(text)
        finally:
            self._answering = False

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
        """Called by the ears the moment speech begins while the mic is open."""
        if self.voice.speaking and self.settings.listening.interruptible:
            self.spawn(self._maybe_interrupt())

    async def _maybe_interrupt(self) -> None:
        """Stop talking if you're really talking over him — not his own voice leaking into the mic."""
        await asyncio.sleep(ECHO_DELAY)
        audio = self.ears.current_audio() if self.ears else None
        if audio is None or not self.voice.speaking or self.stt is None:
            return
        heard = await asyncio.to_thread(self.stt.transcribe, audio, self.settings.persona.name)
        if not is_echo(heard, self._said) and self.voice.speaking:
            log.info("Interrupted by: %s", heard)
            self.voice.stop()

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
            if self.muted or self.stt is None:
                continue
            text = await asyncio.to_thread(self.stt.transcribe, utterance.audio, self.settings.persona.name)
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
            in_conversation = utterance.started < self.awake_until
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
            self._queue_speech(command)

    # confirmation ---------------------------------------------------------------

    async def confirm(self, action: str) -> bool:
        """Ask before doing something risky. Answer by voice or in the app."""
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

    async def look_at_camera(self, question: str) -> str:
        if not (self.eyes and self.eyes.camera_ok):
            return "The camera is off or not working."
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
            if self.state in ("idle", "listening", "muted", "starting"):
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

    def remind(self, seconds: float, message: str) -> None:
        async def fire():
            await asyncio.sleep(seconds)
            run(["notify-send", "-a", self.settings.persona.name, "Reminder", message])
            async with self._turn_lock:
                await self.speak(f"Hey {self.settings.persona.user_name}, reminder: {message}")
        self.spawn(fire())

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
            case "learn_face":
                if not self.eyes:
                    raise RuntimeError("The camera is off.")
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
