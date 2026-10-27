"""Check how reliably a model picks the right tool. Usage: eval_tools.py MODEL [MODEL...]"""

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import ollama

from blaze.brain import Brain
from blaze.hands import Hands
from blaze.memory import Memory
from blaze.settings import Settings

CASES = [
    ("My favorite drink is chai, remember that.", "remember"),
    ("What can you see right now?", "look_at_camera"),
    ("How do I look today?", "look_at_camera"),
    ("What's on my screen?", "look_at_screen"),
    ("Can you open the camera?", "open_camera"),
    ("Open the camera and see what I'm doing", "open_camera"),
    ("Okay, close the camera now", "close_camera"),
    ("Open Firefox", "open_app"),
    ("Find my resume", "find_files"),
    ("Turn the volume down to 30", "set_volume"),
    ("Pause the music", "media"),
    ("How much battery do I have?", "system_status"),
    ("Remind me in 10 minutes to call mom", "set_reminder"),
    ("Remind me at 5:30 pm to send the invoice", "set_reminder"),
    ("Which windows are open?", "list_windows"),
    ("Summarize what I just copied", "clipboard"),
    ("Give me my morning briefing", "daily_briefing"),
    ("Fix the grammar in my clipboard", "clipboard"),
    ("Translate this to Urdu", "clipboard"),
    ("I'm working on a robot project called Blaze this month", "remember"),
    ("How are you doing?", None),
    ("Tell me a joke", None),
]


async def evaluate(model: str) -> None:
    settings = Settings()
    memory = Memory(Path(tempfile.mktemp()))

    async def nothing(*_):
        return ""
    hands = Hands(settings.hands, memory, nothing, nothing, nothing, nothing, nothing, nothing, nothing, nothing)
    brain = Brain(settings, hands, memory)
    client = ollama.AsyncClient()
    await client.chat(model=model, messages=[{"role": "user", "content": "hi"}])  # warm up

    score, start = 0, time.time()
    for text, expected in CASES:
        r = await client.chat(model=model, tools=hands.schemas(), options={"temperature": 0.3}, messages=[
            {"role": "system", "content": brain.system_prompt()}, {"role": "user", "content": text}])
        called = r.message.tool_calls[0].function.name if r.message.tool_calls else None
        ok = called == expected
        score += ok
        print(f"  {'✓' if ok else '✗'} {text!r}: expected {expected}, got {called}")
    print(f"{model}: {score}/{len(CASES)} in {time.time() - start:.0f}s\n")


async def main() -> None:
    for model in sys.argv[1:]:
        await evaluate(model)

asyncio.run(main())
