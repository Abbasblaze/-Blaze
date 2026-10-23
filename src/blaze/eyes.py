"""The webcam watcher: is someone at the desk, and is it you?

Runs in its own thread at a couple of frames per second. Face detection uses
OpenCV's YuNet and recognition uses SFace — both tiny and CPU-only. Frames
never leave the laptop and are never written to disk.
"""

import logging
import os
import threading
import time
from collections.abc import Callable

os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
import cv2  # noqa: E402 — must come after the log level is set
import numpy as np

from .models import FACE_DETECTOR, FACE_RECOGNIZER
from .paths import FACE_FILE
from .settings import CameraSettings

log = logging.getLogger(__name__)

FPS = 2
DETECT_WIDTH = 320
MATCH_THRESHOLD = 0.36  # SFace cosine similarity; higher = same person
RECOGNIZE_EVERY = 5.0  # seconds between identity checks while someone is present
MAX_OWNER_SAMPLES = 200

EventHandler = Callable[[str, dict], None]


class Eyes:
    def __init__(self, settings: CameraSettings, on_event: EventHandler):
        self.settings = settings
        self.on_event = on_event  # called from the camera thread
        self.present = False
        self.who: str | None = None  # "owner", "someone" or None
        self.present_since: float | None = None
        self.last_seen = 0.0
        self.faces = 0
        self.camera_ok = False

        self._frame: np.ndarray | None = None
        self._frame_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_recognized = 0.0
        self._enroll_target = 0
        self._enroll_samples: list[np.ndarray] = []
        self._enroll_done = threading.Event()

        self._detector = cv2.FaceDetectorYN.create(str(FACE_DETECTOR), "", (DETECT_WIDTH, 240), 0.8)
        self._recognizer = cv2.FaceRecognizerSF.create(str(FACE_RECOGNIZER), "")
        self.owner: np.ndarray | None = np.load(FACE_FILE) if FACE_FILE.exists() else None

    # lifecycle ---------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="eyes", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        cap = None
        failures = 0
        while not self._stop.is_set():
            if cap is None:
                cap = cv2.VideoCapture(self.settings.device, cv2.CAP_V4L2)
                if not cap.isOpened():
                    self.camera_ok = False
                    cap = None
                    self._stop.wait(5)
                    continue
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                self.camera_ok = True
                log.info("Camera %s open", self.settings.device)

            ok, frame = cap.read()
            if not ok:
                failures += 1
                if failures > 10:  # e.g. after suspend/resume the device goes stale
                    log.warning("Camera stopped responding; reopening")
                    cap.release()
                    cap, failures = None, 0
                self._stop.wait(0.5)
                continue
            failures = 0

            with self._frame_lock:
                self._frame = frame
            try:
                self._look(frame)
            except Exception:
                log.exception("Face processing failed")
            self._stop.wait(1 / FPS)

        if cap is not None:
            cap.release()
        self.camera_ok = False

    # perception --------------------------------------------------------------

    def _detect(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        scale = DETECT_WIDTH / w
        small = cv2.resize(frame, (DETECT_WIDTH, int(h * scale)))
        self._detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self._detector.detect(small)
        if faces is None:
            return np.empty((0, 15), dtype=np.float32)
        faces = faces.copy()
        faces[:, :14] /= scale  # back to full-frame coordinates
        return faces

    def _embed(self, frame: np.ndarray, face: np.ndarray) -> np.ndarray:
        aligned = self._recognizer.alignCrop(frame, face)
        return self._recognizer.feature(aligned).flatten()

    def _identify(self, frame: np.ndarray, faces: np.ndarray) -> str:
        if self.owner is None:
            return "someone"
        for face in faces:
            feat = self._embed(frame, face)
            sims = self.owner @ feat / (np.linalg.norm(self.owner, axis=1) * np.linalg.norm(feat))
            if sims.max() >= MATCH_THRESHOLD:
                return "owner"
        return "someone"

    def _look(self, frame: np.ndarray) -> None:
        now = time.time()
        faces = self._detect(frame)
        self.faces = len(faces)

        if self._enroll_target and len(faces) == 1:
            self._enroll_samples.append(self._embed(frame, faces[0]))
            if len(self._enroll_samples) >= self._enroll_target:
                self._enroll_done.set()

        if len(faces):
            if not self.present:
                away = now - self.last_seen if self.last_seen else None
                self.present, self.present_since = True, now
                self.who = self._identify(frame, faces)
                self._last_recognized = now
                self.on_event("arrived", {"who": self.who, "away_seconds": away})
            elif now - self._last_recognized > RECOGNIZE_EVERY:
                self._last_recognized = now
                who = self._identify(frame, faces)
                if who != self.who:
                    self.who = who
                    self.on_event("identity", {"who": who})
            self.last_seen = now
        elif self.present and now - self.last_seen > self.settings.away_after_seconds:
            sat = self.last_seen - (self.present_since or self.last_seen)
            self.present, self.who, self.present_since = False, None, None
            self.on_event("left", {"sat_seconds": sat})

    # public helpers (called from other threads) -----------------------------

    def snapshot_jpeg(self, quality: int = 85) -> bytes | None:
        with self._frame_lock:
            frame = None if self._frame is None else self._frame.copy()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def enroll(self, samples: int = 20, timeout: float = 30) -> int:
        """Learn the owner's face from the next `samples` single-face frames."""
        self._enroll_samples = []
        self._enroll_done.clear()
        self._enroll_target = samples
        self._enroll_done.wait(timeout)
        self._enroll_target = 0
        collected = self._enroll_samples
        if len(collected) >= 5:
            new = np.stack(collected)
            # Keep adding samples over time (glasses, lighting, haircut…) but cap the size.
            self.owner = (new if self.owner is None else np.vstack([self.owner, new]))[-MAX_OWNER_SAMPLES:]
            np.save(FACE_FILE, self.owner)
            self.who = "owner" if self.present else None
        return len(collected)

    def forget_owner(self) -> None:
        self.owner = None
        FACE_FILE.unlink(missing_ok=True)

    def status(self) -> dict:
        return {
            "camera_ok": self.camera_ok,
            "present": self.present,
            "who": self.who,
            "present_since": self.present_since,
            "faces": self.faces,
            "knows_owner": self.owner is not None,
        }
