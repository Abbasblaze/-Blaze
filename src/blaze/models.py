"""Downloads the local model files Blaze needs (voice + face models).

Whisper downloads itself on first use; Ollama models are pulled with `ollama pull`.
"""

import logging
import shutil
import subprocess
from pathlib import Path

import httpx

from .paths import MODELS_DIR, ensure_dirs

log = logging.getLogger(__name__)

KOKORO_MODEL = MODELS_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES = MODELS_DIR / "voices-v1.0.bin"
FACE_DETECTOR = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
FACE_RECOGNIZER = MODELS_DIR / "face_recognition_sface_2021dec.onnx"
WHISPER_DIR = MODELS_DIR / "whisper"

_KOKORO = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models/"

DOWNLOADS: dict[Path, str] = {
    KOKORO_MODEL: _KOKORO + "kokoro-v1.0.onnx",
    KOKORO_VOICES: _KOKORO + "voices-v1.0.bin",
    FACE_DETECTOR: _ZOO + "face_detection_yunet/face_detection_yunet_2023mar.onnx",
    FACE_RECOGNIZER: _ZOO + "face_recognition_sface/face_recognition_sface_2021dec.onnx",
}


def _download(url: str, dest: Path) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream("GET", url, follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with tmp.open("wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if total:
                    print(f"\r  {dest.name}: {done * 100 // total}%", end="", flush=True)
    print()
    tmp.replace(dest)


def download_all() -> None:
    ensure_dirs()
    for dest, url in DOWNLOADS.items():
        if dest.exists():
            print(f"  {dest.name}: already here")
        else:
            _download(url, dest)


def pull_ollama_models(*names: str) -> None:
    if not shutil.which("ollama"):
        print("  ollama not found — install it from https://ollama.com")
        return
    for name in names:
        print(f"  ollama pull {name}")
        subprocess.run(["ollama", "pull", name], check=False)
