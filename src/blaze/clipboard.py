"""Reading and writing the Wayland clipboard (wl-clipboard), and cutting long text into pieces
small enough for the model's context."""

import re
import subprocess

from . import hands  # (hands imports this module too, so look session_env up when it is called)

IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp")


class ClipboardError(Exception):
    pass


def _paste(*args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["wl-paste", "--no-newline", *args], capture_output=True, timeout=5,
                              env=hands.session_env())
    except FileNotFoundError as e:
        raise ClipboardError("wl-clipboard isn't installed (sudo pacman -S wl-clipboard).") from e
    except subprocess.TimeoutExpired as e:
        raise ClipboardError("The clipboard didn't answer.") from e


def types() -> list[str]:
    """What kinds of data are on the clipboard (empty if nothing is)."""
    p = _paste("--list-types")
    return p.stdout.decode(errors="replace").split() if p.returncode == 0 else []


def read_text() -> str:
    """The copied text, or "" if there's none."""
    if not any(t.startswith("text/") or t in ("STRING", "UTF8_STRING", "TEXT") for t in types()):
        return ""
    p = _paste("--type", "text")
    return p.stdout.decode(errors="replace") if p.returncode == 0 else ""


def read_image() -> bytes | None:
    """The copied image, or None if there isn't one."""
    kind = next((t for t in IMAGE_TYPES if t in types()), None)
    if kind is None:
        return None
    p = _paste("--type", kind)
    return p.stdout if p.returncode == 0 and p.stdout else None


def write_text(text: str) -> None:
    # wl-copy stays in the background to serve the clipboard, so it mustn't hold our pipes open.
    try:
        p = subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"], input=text.encode(),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, env=hands.session_env())
    except FileNotFoundError as e:
        raise ClipboardError("wl-clipboard isn't installed (sudo pacman -S wl-clipboard).") from e
    except subprocess.TimeoutExpired as e:
        raise ClipboardError("The clipboard didn't answer.") from e
    if p.returncode != 0:
        raise ClipboardError(f"Couldn't copy to the clipboard (exit {p.returncode}).")


def chunks(text: str, limit: int) -> list[str]:
    """Split text into pieces of at most `limit` characters, at paragraph breaks where possible,
    then at sentence ends, then wherever it has to."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    for part in re.split(r"(?<=\n\n)", text):
        if len(part) > limit:
            pieces += [s for s in re.split(r"(?<=[.!?。])\s+", part) if s]
        else:
            pieces.append(part)
    out = [""]
    for piece in pieces:
        while len(piece) > limit:  # one huge "sentence" (code, a URL list…)
            out += [piece[:limit], ""]
            piece = piece[limit:]
        if len(out[-1]) + len(piece) + 1 > limit:
            out.append("")
        out[-1] += piece if not out[-1] or out[-1].endswith(("\n", " ")) else " " + piece
    return [c for c in out if c.strip()]
