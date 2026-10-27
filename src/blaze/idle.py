"""Noticing when you stop using the laptop, through the Wayland idle protocol (via swayidle).

Watching a video counts as using it: players ask the desktop not to go idle, and swayidle
respects that.
"""

import asyncio
import logging
import shutil
from collections.abc import Callable

from .hands import session_env

log = logging.getLogger(__name__)

IDLE_AFTER = 60  # seconds without keyboard or mouse input before you count as idle


async def watch_input(on_change: Callable[[bool], None]) -> None:
    """Call on_change(True) once there's been no input for IDLE_AFTER seconds, and on_change(False)
    as soon as there is again. Runs until cancelled. Restarts swayidle if it exits, which happens
    when Blaze starts before the desktop does."""
    if not shutil.which("swayidle"):
        log.warning("swayidle isn't installed, so I can't tell when you're away from the keyboard")
        return
    while True:
        process = await asyncio.create_subprocess_exec(
            "swayidle", "-w", "timeout", str(IDLE_AFTER), "echo idle", "resume", "echo active",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, env=session_env())
        try:
            while line := await process.stdout.readline():
                if line.strip() in (b"idle", b"active"):
                    on_change(line.strip() == b"idle")
        finally:
            if process.returncode is None:
                process.terminate()
                await process.wait()
        on_change(False)  # don't stay "idle" on stale news while it's down
        log.warning("swayidle stopped (exit %s); starting it again soon", process.returncode)
        await asyncio.sleep(30)
