"""Tools Blaze can use on the laptop: files, apps, windows, media, system, shell.

Each tool is a plain function returning a short string the LLM reads back.
Anything that could break or lose something asks for confirmation first.
"""

import asyncio
import configparser
import inspect
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import psutil

from . import clipboard, web
from .memory import Memory
from .reminders import describe, parse_when
from .settings import HandsSettings

log = logging.getLogger(__name__)

MAX_OUTPUT = 1500
APP_DIRS = [
    Path.home() / ".local/share/applications",
    Path("/usr/share/applications"),
    Path.home() / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
]
# Commands that only read things can run without asking (if they have no shell tricks).
SAFE_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "df", "du", "free", "uptime", "date", "whoami", "pwd",
    "echo", "which", "uname", "ps", "ip", "nmcli", "lsblk", "lsusb", "sensors", "nvidia-smi",
    "hostname", "stat", "file", "tree", "git",
}
SHELL_TRICKS = re.compile(r"[;&|<>`$\\]|\bsudo\b")
# Clipboard text, in characters, the model can take at once (its context is ~4000 tokens, and a
# translation into a non-Latin script like Urdu takes several times the tokens of the English).
SUMMARY_CHUNK = 10_000
REWRITE_CHUNK = {"translate": 2_500, "fix": 5_000, "rewrite": 4_000}
MAX_REWRITE = 6_000  # longer than this would take minutes: ask for a smaller piece
MAX_SUMMARY = 40_000
CLIPBOARD_TASKS = {
    "summarize": "Summarize this in two or three short, plain sentences that can be read out loud.",
    "explain": "Explain simply what this is and what it means, in a few short sentences that can be read "
               "out loud. If it's an error message, say what most likely caused it and how to fix it.",
    "translate": "Translate this into {detail}. Keep names, numbers and code as they are.",
    "fix": "Correct every spelling, grammar and punctuation mistake, including wrong verb forms, plurals and "
           "pronouns (\"me and him went\" → \"he and I went\"). Keep the meaning, tone and formatting, and "
           "don't reword sentences that are already correct. If it's all correct, return it unchanged.",
    "rewrite": "Rewrite this: {detail}. Keep the meaning.",
}
# Lead-ins small models put before the result ("Here's the corrected text:").
_LEAD_IN = re.compile(r"^\s*(?:sure[,!.]?\s*)?(?:here(?:'s| is| are)\b[^\n:]{0,60}|(?:the )?(?:corrected|fixed|"
                      r"translated|rewritten|revised) (?:text|version)[^\n:]{0,40}):\s*\n*", re.I)
# What people (and the model) call "the browser" when they don't name one.
BROWSER_WORDS = {"browser", "web browser", "the browser", "my browser", "internet", "web", "default browser"}


def session_env() -> dict[str, str]:
    """The environment with the *current* Wayland/Hyprland session filled in.

    The daemon runs as a systemd user service, which may start before (or
    outlive) the graphical session, so we look the session up each time.
    """
    env = dict(os.environ)
    runtime = Path(env.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    hypr = runtime / "hypr"
    if hypr.is_dir():
        instances = sorted(
            (d for d in hypr.iterdir() if (d / ".socket.sock").exists()),
            key=lambda d: d.stat().st_mtime,
        )
        if instances:
            env["HYPRLAND_INSTANCE_SIGNATURE"] = instances[-1].name
    sockets = sorted(runtime.glob("wayland-[0-9]"))
    if sockets and not (runtime / env.get("WAYLAND_DISPLAY", "-")).exists():
        env["WAYLAND_DISPLAY"] = sockets[0].name
    return env


def default_browser() -> tuple[str, str]:
    """The user's default browser as (desktop file id, name), e.g. ("google-chrome", "Google Chrome")."""
    try:
        desktop = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True,
                                 text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "", "the default browser"
    for directory in APP_DIRS:
        path = directory / desktop
        if desktop and path.is_file():
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(path, encoding="utf-8")
                return path.stem, cp["Desktop Entry"].get("Name", path.stem)
            except (configparser.Error, KeyError, UnicodeDecodeError):
                break
    return desktop.removesuffix(".desktop"), desktop.removesuffix(".desktop") or "the default browser"


def run(args: list[str], timeout: float = 15) -> str:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=session_env())
    except FileNotFoundError:
        return f"'{args[0]}' is not installed."
    except subprocess.TimeoutExpired:
        return "That took too long, so I stopped it."
    out = (p.stdout + p.stderr).strip()
    return out[:MAX_OUTPUT] if out else ("Done." if p.returncode == 0 else f"Failed (exit {p.returncode}).")


def launch(command: str) -> None:
    """Start a program owned by Hyprland, not by Blaze, so it survives Blaze restarting."""
    env = session_env()
    if shutil.which("hyprctl") and "HYPRLAND_INSTANCE_SIGNATURE" in env:
        subprocess.run(["hyprctl", "dispatch", "exec", command], env=env, capture_output=True, timeout=5)
    else:
        subprocess.Popen(command, shell=True, env=env, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., str | Awaitable[str]]
    params: dict[str, str] = field(default_factory=dict)  # name -> description
    optional: tuple[str, ...] = ()
    types: dict[str, str] = field(default_factory=dict)  # defaults to "string"

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        p: {"type": self.types.get(p, "string"), "description": d} for p, d in self.params.items()
                    },
                    "required": [p for p in self.params if p not in self.optional],
                },
            },
        }


class Hands:
    def __init__(
        self,
        settings: HandsSettings,
        memory: Memory,
        confirm: Callable[[str], Awaitable[bool]],
        look_camera: Callable[[str], Awaitable[str]],
        look_screen: Callable[[str], Awaitable[str]],
        rewrite: Callable[[str, str], Awaitable[str]],
        see: Callable[[bytes, str], Awaitable[str]],
        briefing: Callable[[], Awaitable[str]],
        open_camera: Callable[[], Awaitable[str]],
        close_camera: Callable[[], Awaitable[str]],
    ):
        self.settings = settings
        self.memory = memory
        self.confirm = confirm
        self._look_camera = look_camera
        self._look_screen = look_screen
        self._rewrite = rewrite
        self._see = see
        self._briefing = briefing
        self._open_camera = open_camera
        self._close_camera = close_camera
        self.browser_id, self.browser_name = default_browser()
        self.tools = {t.name: t for t in self._define()}

    def schemas(self) -> list[dict]:
        tools = self.tools.values()
        if not self.settings.allow_commands:
            tools = [t for t in tools if t.name != "run_command"]
        return [t.schema() for t in tools]

    async def call(self, name: str, args: dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return f"There's no tool called {name}."
        accepted = inspect.signature(tool.fn).parameters
        args = {k: v for k, v in (args or {}).items() if k in accepted}
        log.info("Tool %s %s", name, args)
        try:
            result = tool.fn(**args)
            if inspect.isawaitable(result):
                return await result
            return result
        except TypeError as e:
            return f"Bad arguments for {name}: {e}"
        except Exception as e:
            log.exception("Tool %s failed", name)
            return f"{name} failed: {e}"

    def _define(self) -> list[Tool]:
        return [
            Tool("find_files", "Find files or folders on the laptop by (part of) their name.", self.find_files,
                 {"name": "part of the file or folder name", "folder": "where to look, default home"}, ("folder",)),
            Tool("search_inside_files", "Find files that contain some text.", self.search_inside_files,
                 {"text": "text to look for", "folder": "where to look, default home"}, ("folder",)),
            Tool("open", "Open a file, folder or web URL in its default app.", self.open,
                 {"target": "absolute file path, folder path or URL"}),
            Tool("open_app", "Launch an installed application by name.", self.open_app,
                 {"name": "app name, e.g. firefox, spotify, files"}),
            Tool("list_windows", "List the open windows.", self.list_windows),
            Tool("focus_window", "Switch to an open window.", self.focus_window,
                 {"name": "part of the window's app name or title"}),
            Tool("close_window", "Close an open window.", self.close_window,
                 {"name": "part of the window's app name or title"}),
            Tool("system_status", "Battery, CPU, memory, disk and uptime.", self.system_status),
            Tool("set_volume", "Set speaker volume.", self.set_volume,
                 {"percent": "0-100"}, types={"percent": "integer"}),
            Tool("set_brightness", "Set screen brightness.", self.set_brightness,
                 {"percent": "1-100"}, types={"percent": "integer"}),
            Tool("media", "Control music/video playback.", self.media,
                 {"action": "play, pause, toggle, next, previous, or status"}),
            Tool("open_camera", "Turn the webcam on. It stays off until the user asks.", self._open_camera),
            Tool("close_camera", "Turn the webcam off.", self._close_camera),
            Tool("look_at_camera", "Look through the webcam to see the user or what they're holding or showing.",
                 self._look_camera, {"question": "the user's question, in their own words"}),
            Tool("look_at_screen", "Look at what's on the laptop screen right now.",
                 self._look_screen, {"question": "what you want to know about the screen"}),
            Tool("remember", "Save a lasting fact about the user (preferences, projects, people, plans).",
                 self.remember, {"fact": "the fact, as a short sentence"}),
            Tool("forget", "Delete remembered facts that match some text.", self.forget,
                 {"text": "text the fact contains"}),
            Tool("set_reminder", "Remind the user of something at a time, or after a while.", self.set_reminder,
                 {"when": "when, in the user's own words, e.g. 'at 5:30 pm', 'in 20 minutes', 'tomorrow at 9am'",
                  "message": "what to remind them of, e.g. 'call mom'"}),
            Tool("list_reminders", "The reminders that are still waiting to go off.", self.list_reminders),
            Tool("cancel_reminder", "Cancel waiting reminders.", self.cancel_reminder,
                 {"text": "a word from the reminder, e.g. 'mom', or 'all' to cancel every one"}),
            Tool("clipboard", "Work with what the user copied: summarize, explain, translate, fix the grammar, "
                 "rewrite it, read it out, or copy new text to the clipboard. Translate, fix and rewrite put "
                 "the result back on the clipboard, ready to paste.", self.clipboard,
                 {"action": "summarize, explain, translate, fix, rewrite, read, or copy",
                  "detail": "for translate: the language; for rewrite: how (e.g. 'more formal', 'shorter'); "
                            "for explain: the user's question; for copy: the exact text to copy"}, ("detail",)),
            Tool("web_search", "Search the internet. Use for prices, scores, people, releases and any fact "
                 "you're not sure of.", self.web_search,
                 {"query": "what to search for, like you'd type it into Google"}),
            Tool("news", "Latest news headlines about a topic.", self.news,
                 {"topic": "what the news should be about, e.g. Pakistan cricket"}),
            Tool("daily_briefing", "The user's briefing for today: weather, today's reminders and the top headlines.",
                 self._briefing),
            Tool("weather", "Current weather and the forecast for the next days.", self.weather,
                 {"place": "city name"}),
            Tool("read_webpage", "Read the text of a web page, e.g. a search result, when its snippet isn't enough.",
                 self.read_webpage, {"url": "the page's full web address"}),
            Tool("run_command", "Run a Linux shell command and return its output. Use only when no other tool fits.",
                 self.run_command, {"command": "the bash command"}),
        ]

    # web -----------------------------------------------------------------------

    async def web_search(self, query: str) -> str:
        return await web.search(query, self.settings)

    async def read_webpage(self, url: str) -> str:
        return await web.read_page(url)

    async def news(self, topic: str) -> str:
        return await web.news(topic)

    async def weather(self, place: str) -> str:
        return await web.weather(place)

    # files ---------------------------------------------------------------------

    def _folder(self, folder: str | None) -> Path:
        default = self.settings.search_roots[0] if self.settings.search_roots else "~"
        return Path(os.path.expanduser(folder or default))

    def find_files(self, name: str, folder: str = "") -> str:
        root = self._folder(folder)
        if shutil.which("fd"):
            out = run(["fd", "--ignore-case", "--max-results", "15", "--", name, str(root)])
        else:
            out = run(["find", str(root), "-iname", f"*{name}*", "-not", "-path", "*/.*"])
            out = "\n".join(out.splitlines()[:15])
        return out if out and out != "Done." else f"Nothing named like '{name}' in {root}."

    def search_inside_files(self, text: str, folder: str = "") -> str:
        root = self._folder(folder)
        out = run(["rg", "--files-with-matches", "--ignore-case", "--max-filesize", "2M", "--fixed-strings",
                   "--", text, str(root)], timeout=20)
        lines = out.splitlines()
        if not lines or out.startswith("Failed"):
            return f"No files in {root} contain '{text}'."
        return "\n".join(lines[:15]) + (f"\n…and {len(lines) - 15} more" if len(lines) > 15 else "")

    def open(self, target: str) -> str:
        target = os.path.expanduser(target)
        if not re.match(r"^[a-z]+://", target) and not Path(target).exists():
            return f"{target} doesn't exist."
        launch(f"xdg-open {shlex.quote(target)}")
        return f"Opened {target}."

    # apps & windows ------------------------------------------------------------

    def open_app(self, name: str) -> str:
        query = name.lower().strip()
        if query in BROWSER_WORDS:  # "open the browser" means the user's browser, not whichever is installed
            query = self.browser_name.lower()
        best = None
        for directory in APP_DIRS:
            for path in sorted(directory.glob("*.desktop")) if directory.is_dir() else []:
                cp = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    cp.read(path, encoding="utf-8")
                    entry = cp["Desktop Entry"]
                except (configparser.Error, KeyError, UnicodeDecodeError):
                    continue
                app_name, exec_line = entry.get("Name", ""), entry.get("Exec", "")
                if not exec_line or entry.get("NoDisplay", "false") == "true":
                    continue
                if app_name.lower() == query or path.stem.lower() == query:
                    best = (app_name, exec_line)
                    break
                if best is None and (query in app_name.lower() or query in path.stem.lower()):
                    best = (app_name, exec_line)
            if best and best[0].lower() == query:
                break
        if best is None:
            if shutil.which(query):
                launch(query)
                return f"Started {query}."
            return f"I couldn't find an app called {name}."
        command = re.sub(r"\s%[a-zA-Z]", "", best[1])
        launch(command)
        return f"Opened {best[0]}."

    def _clients(self) -> list[dict]:
        try:
            return json.loads(run(["hyprctl", "clients", "-j"]))
        except ValueError:
            return []

    def _find_window(self, name: str) -> dict | None:
        q = name.lower().strip()
        if q in BROWSER_WORDS or q == self.browser_name.lower():
            q = self.browser_id.lower() or q  # Chrome's window class is "Google-chrome"
        for c in self._clients():
            if q in c.get("class", "").lower() or q in c.get("title", "").lower():
                return c
        return None

    def _no_window(self, name: str) -> str:
        open_apps = sorted({c["class"] for c in self._clients() if c.get("class")})
        listing = ", ".join(open_apps) if open_apps else "none"
        return f"No open window matches '{name}'. Nothing was changed. Open windows: {listing}."

    def list_windows(self) -> str:
        clients = self._clients()
        if not clients:
            return "No windows open (or I can't reach Hyprland)."
        return "\n".join(f"{c['class']}: {c['title'][:60]} (workspace {c['workspace']['name']})" for c in clients)

    def focus_window(self, name: str) -> str:
        c = self._find_window(name)
        if not c:
            return self._no_window(name)
        run(["hyprctl", "dispatch", "focuswindow", f"address:{c['address']}"])
        return f"Switched to {c['class']}."

    async def close_window(self, name: str) -> str:
        c = self._find_window(name)
        if not c:
            return self._no_window(name)
        if self.settings.confirm_risky and not await self.confirm(f"close {c['class']} — {c['title'][:40]}"):
            return "The user said no, so I left it open."
        run(["hyprctl", "dispatch", "closewindow", f"address:{c['address']}"])
        return f"Closed {c['class']}."

    # system --------------------------------------------------------------------

    def system_status(self) -> str:
        parts = []
        battery = psutil.sensors_battery()
        if battery:
            state = "charging" if battery.power_plugged else "on battery"
            parts.append(f"Battery {battery.percent:.0f}% ({state})")
        parts.append(f"CPU {psutil.cpu_percent(interval=0.3):.0f}%")
        mem = psutil.virtual_memory()
        parts.append(f"RAM {mem.used / 2**30:.1f}/{mem.total / 2**30:.1f} GB")
        disk = psutil.disk_usage(str(Path.home()))
        parts.append(f"Disk {disk.free / 2**30:.0f} GB free")
        up = time.time() - psutil.boot_time()
        parts.append(f"Up {int(up // 3600)}h {int(up % 3600 // 60)}m")
        return ", ".join(parts)

    def set_volume(self, percent: int) -> str:
        percent = max(0, min(100, int(percent)))
        return run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{percent}%"]).replace("Done.", f"Volume {percent}%.")

    def set_brightness(self, percent: int) -> str:
        percent = max(1, min(100, int(percent)))
        run(["brightnessctl", "set", f"{percent}%"])
        return f"Brightness {percent}%."

    def media(self, action: str) -> str:
        action = action.lower().strip()
        commands = {"play": "play", "pause": "pause", "toggle": "play-pause", "next": "next",
                    "previous": "previous", "status": "status"}
        if action not in commands:
            return "Action must be play, pause, toggle, next, previous or status."
        if action == "status":
            return run(["playerctl", "metadata", "--format", "{{status}}: {{artist}} - {{title}}"])
        return run(["playerctl", commands[action]])

    # memory & time -------------------------------------------------------------

    def remember(self, fact: str) -> str:
        self.memory.add_fact(fact)
        return "Saved."

    def forget(self, text: str) -> str:
        n = self.memory.forget_matching(text)
        return f"Forgot {n} fact(s)." if n else "I didn't have anything like that saved."

    def set_reminder(self, when: str, message: str) -> str:
        due = parse_when(str(when))
        if due is None:
            return f"I couldn't work out when '{when}' is, so no reminder was set. Ask the user for a time."
        self.memory.add_reminder(due.timestamp(), message)
        return f"Reminder set for {describe(due)}: {message}."

    def list_reminders(self) -> str:
        waiting = self.memory.reminders()
        if not waiting:
            return "No reminders are waiting."
        return "\n".join(f"{describe(datetime.fromtimestamp(r['due']))}: {r['message']}" for r in waiting)

    def cancel_reminder(self, text: str) -> str:
        cancelled = self.memory.cancel_reminders("" if text.strip().lower() in ("all", "every", "everything") else text)
        if not cancelled:
            return f"No waiting reminder matches '{text}'. Nothing was cancelled."
        return "Cancelled: " + "; ".join(cancelled)

    # clipboard -----------------------------------------------------------------

    async def clipboard(self, action: str, detail: str = "") -> str:
        action, detail = action.lower().strip(), (detail or "").strip()
        try:
            if action == "copy":
                if not detail:
                    return "Nothing was copied (no text was given). The clipboard wasn't changed."
                await asyncio.to_thread(clipboard.write_text, detail)
                return "Copied to the clipboard."
            if action not in ("read", *CLIPBOARD_TASKS):
                return "Action must be summarize, explain, translate, fix, rewrite, read or copy."
            text = (await asyncio.to_thread(clipboard.read_text)).strip()
            if not text:
                return await self._clipboard_image(action, detail)
            if action == "read":
                more = "\n…(it goes on)" if len(text) > MAX_OUTPUT else ""
                return f"The clipboard has:\n{text[:MAX_OUTPUT]}{more}"
            if action in ("summarize", "explain"):
                return await self._clipboard_read(action, detail, text)
            return await self._clipboard_rewrite(action, detail, text)
        except clipboard.ClipboardError as e:
            return str(e)

    async def _clipboard_image(self, action: str, detail: str) -> str:
        image = await asyncio.to_thread(clipboard.read_image)
        if image is None:
            return "The clipboard is empty."
        if action not in ("read", "summarize", "explain"):
            return "The clipboard has an image, not text, so there's nothing to " + action + "."
        return "The clipboard has an image. " + await self._see(
            image, detail or "Describe this image. If it has text, read it; if it's an error, explain it.")

    async def _clipboard_read(self, action: str, detail: str, text: str) -> str:
        """Summarize or explain. Long text is summarized piece by piece, then the pieces together."""
        cut = len(text) > MAX_SUMMARY
        task = CLIPBOARD_TASKS[action] + (f" The user asks: {detail}" if detail else "")
        parts = clipboard.chunks(text[:MAX_SUMMARY], SUMMARY_CHUNK)
        if len(parts) > 1:
            notes = [await self._rewrite(p, "Summarize the key points of this part in a few sentences.")
                     for p in parts]
            text = "\n\n".join(notes)
        result = await self._rewrite(text, task)
        return result + ("\n(The text was very long; this covers the first part of it.)" if cut else "")

    async def _clipboard_rewrite(self, action: str, detail: str, text: str) -> str:
        """Translate, fix or rewrite, and put the result back on the clipboard."""
        if action == "rewrite" and not detail:
            return "Ask the user how to rewrite it (shorter, more formal, friendlier…)."
        if len(text) > MAX_REWRITE:
            return (f"The copied text is too long to {action} at once (about {len(text.split())} words). "
                    "Ask the user to copy a smaller part.")
        task = CLIPBOARD_TASKS[action].format(detail=detail or "English")
        result = ""
        for part in clipboard.chunks(text, REWRITE_CHUNK[action]):
            done = tidy(await self._rewrite(part, task), part)
            if not done:
                return f"I couldn't {action} part of it, so I left the clipboard as it was."
            # Rejoin the pieces the way they were split: between paragraphs, or mid-paragraph.
            result += done + ("\n\n" if part.endswith("\n") else " ")
        result = result.strip()
        if not result:
            return f"I couldn't {action} it. The clipboard wasn't changed."
        if action == "fix" and result == text:
            return "The copied text already looks correct. The clipboard wasn't changed."
        await asyncio.to_thread(clipboard.write_text, result)
        done = {"translate": f"Translated into {detail or 'English'}", "fix": "Fixed the grammar",
                "rewrite": "Rewrote it"}[action]
        shown = result if len(result) <= 400 else result[:400] + "…"
        return f"{done} and put it on the clipboard, ready to paste (the original is replaced). It reads:\n{shown}"

    # shell ---------------------------------------------------------------------

    async def run_command(self, command: str) -> str:
        if not self.settings.allow_commands:
            return "Running commands is turned off in settings."
        first = shlex.split(command)[0] if command.strip() else ""
        safe = first in SAFE_COMMANDS and not SHELL_TRICKS.search(command)
        if not safe and self.settings.confirm_risky and not await self.confirm(f"run the command: {command}"):
            return "The user said no, so I didn't run it."
        return await asyncio.to_thread(run, ["bash", "-c", command], 30)


def tidy(result: str, original: str) -> str:
    """Strip what small models wrap around a rewrite: a lead-in line, quotes, a code fence."""
    lead_in = _LEAD_IN.match(result)
    if lead_in and lead_in[0].strip().lower() not in original.lower():
        result = result[lead_in.end():].strip()
    if result.startswith("```") and not original.lstrip().startswith("```"):
        result = re.sub(r"^```\w*\n?|\n?```$", "", result).strip()
    if len(result) > 1 and result[0] == result[-1] and result[0] in "\"'“”" and original[:1] not in "\"'“":
        result = result[1:-1].strip()
    if result.endswith("”") and result.startswith("“") and not original.startswith("“"):
        result = result[1:-1].strip()
    return result


def screenshot() -> bytes | None:
    """Grab the whole screen as PNG bytes using grim."""
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        result = run(["grim", "-t", "png", f.name])
        if result != "Done.":
            log.warning("Screenshot failed: %s", result)
            return None
        return Path(f.name).read_bytes()
