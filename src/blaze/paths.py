"""Where Blaze keeps its settings, data and models (standard XDG locations)."""

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / fallback)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "blaze"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "blaze"
MODELS_DIR = DATA_DIR / "models"

SETTINGS_FILE = CONFIG_DIR / "settings.json"
DB_FILE = DATA_DIR / "blaze.db"
FACE_FILE = DATA_DIR / "owner_face.npy"
TRAINING_DIR = DATA_DIR / "training"

SOCKET_PATH = Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "blaze.sock"

ASSETS_DIR = Path(__file__).parent / "assets"
ICON_FILE = ASSETS_DIR / "blaze.svg"


def ensure_dirs() -> None:
    for d in (CONFIG_DIR, DATA_DIR, MODELS_DIR, TRAINING_DIR):
        d.mkdir(parents=True, exist_ok=True)
