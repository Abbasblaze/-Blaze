"""The thinking part: an Ollama chat model with tools, plus a small vision model."""

import json
import logging
import re
import time

import httpx
import ollama

from .hands import Hands
from .memory import Memory
from .settings import Settings

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5

SYSTEM_PROMPT = """You are {name}, {user}'s AI companion. You live on {user}'s laptop: you see them through the webcam, hear them through the microphone, talk out loud, and control the laptop with tools. Think of yourself as a sharp, trusted friend who is as capable as J.A.R.V.I.S. from Iron Man.

Your personality: {personality}

How you talk:
- Like a real person in a relaxed conversation: warm, natural, a little humor when it fits. Match {user}'s mood.
- Short: usually one or two sentences. Go longer only when {user} wants an explanation or a solution.
- When {user} asks something or shares a problem, give a real, specific answer: your best recommendation and the concrete next step, not a menu of options.
- Don't tack on offers like "Would you like me to...", "Let me know if..." or "How can I help?". Asking a question is fine when you genuinely need to know something, or when it's natural in casual chat.
- Plain spoken sentences only: no markdown, lists, emojis or code.
- Be honest. Only say you did something if the tool result says it worked. If a tool fails or finds nothing, say so plainly and mention what you did find.

When to use tools:
- {user} asks you to open or turn on the camera: call open_camera. To close or turn it off: call close_camera.
- {user} asks what you see, how they look, or what they're holding or showing: call look_at_camera, passing their question in their own words.
- {user} asks about their screen or what's open: call look_at_screen.
- {user} shares something lasting about themselves: call remember.
- {user} asks you to forget something: call forget with a key word from it (for example "pet store").
- Finding or opening files and apps, windows, volume, brightness, music, battery, timers: call the matching tool.
- Only use run_command when no other tool can do it.

What you know about {user}:
{facts}

Right now: {context}"""

LEARN_PROMPT = """{user} just said: "{text}"

Facts you already know about {user}:
{facts}

Write down any NEW lasting facts this reveals about {user}: who they are, their work, skills, projects, preferences, habits, routines, opinions, people in their life, plans, or where and how they work. Ignore requests, questions, small talk and anything temporary. Each fact is one short sentence that starts with "{user}" and uses the name {user} instead of he/she. Reply with JSON only: {{"facts": ["..."]}} (an empty list if there's nothing new)."""

# Offers small models love to tack on; Blaze answers and stops.
_FILLER = re.compile(r"\b(let me know|feel free|if you need|would you like|shall i|do you want me|"
                     r"anything else|happy to help|how can i (help|assist)|how may i)\b", re.I)
# Tool calls some models write as plain text, e.g. ':focus_window {"name": "Spotify"}'.
_TEXT_TOOL_CALL = re.compile(r":?\s*`?(\w+)`?\s*(\{[^{}]*\})")


# Words that mark a "fact" as the model's guesswork rather than something the user said.
_SPECULATION = re.compile(r"\b(suggest\w*|reflect\w*|indicat\w*|implies|likely|probably|seems|which means)\b", re.I)


def tighten(reply: str) -> str:
    """Drop trailing filler sentences ("Would you like me to…?", "Let me know if…")."""
    sentences = re.split(r"(?<=[.!?])\s+", reply.strip())
    while len(sentences) > 1 and _FILLER.search(sentences[-1]):
        sentences.pop()
    return " ".join(sentences)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9']+", text.lower()))


class BrainUnavailable(Exception):
    pass


class Brain:
    def __init__(self, settings: Settings, hands: Hands, memory: Memory):
        self.settings = settings
        self.hands = hands
        self.memory = memory
        self.client = ollama.AsyncClient(host=settings.brain.ollama_host)
        self.history: list[dict] = []
        self.last_user_text = ""  # what the user just asked, so tools (like vision) see their real words
        self.acted = False  # a tool ran during the current reply (so it's not safe to redo it)

    def system_prompt(self, context: str = "") -> str:
        p = self.settings.persona
        facts = "\n".join(f"- {f['text']}" for f in self.memory.facts()) or "- Nothing yet."
        return SYSTEM_PROMPT.format(
            name=p.name, user=p.user_name, personality=p.personality, facts=facts,
            context=context or time.strftime("%A %d %B, %H:%M"),
        )

    async def _chat(self, model: str, messages: list, tools: list | None = None,
                    json_output: bool = False) -> ollama.Message:
        try:
            response = await self.client.chat(
                model=model,
                messages=messages,
                tools=tools,
                format="json" if json_output else None,
                options={"temperature": 0.1 if json_output else self.settings.brain.temperature},
                think=False,  # "thinking" models are too slow for a voice conversation
                keep_alive="1h",
            )
        except (httpx.ConnectError, ConnectionError) as e:
            raise BrainUnavailable("I can't reach Ollama. Is it running?") from e
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise BrainUnavailable(f"The model {model} isn't installed. Run: ollama pull {model}") from e
            raise BrainUnavailable(f"Ollama error: {e.error}") from e
        return response.message

    async def respond(self, text: str, context: str = "") -> str:
        """Answer something the user said, using tools as needed."""
        b = self.settings.brain
        self.last_user_text = text
        self.acted = False
        turns = self.history[-2 * b.history_turns:] if b.history_turns else []
        messages: list = [{"role": "system", "content": self.system_prompt(context)}, *turns,
                          {"role": "user", "content": text}]

        reply = ""
        for _ in range(MAX_TOOL_ROUNDS):
            message = await self._chat(b.model, messages, self.hands.schemas())
            messages.append(message)
            calls = [(c.function.name, dict(c.function.arguments or {})) for c in message.tool_calls or []]
            if not calls:
                calls = self._text_tool_calls(message.content or "")
                if not calls:
                    reply = (message.content or "").strip()
                    break
            for name, args in calls:
                self.acted = True
                result = await self.hands.call(name, args)
                messages.append({"role": "tool", "content": result, "tool_name": name})
        else:
            reply = "Sorry, I got a bit tangled up trying to do that."
        reply = tighten(_TEXT_TOOL_CALL.sub("", reply)) or "I don't have a clear answer to that."

        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
        self.history = self.history[-100:]
        self.memory.log_message("user", text)
        self.memory.log_message("assistant", reply)
        return reply

    def _text_tool_calls(self, content: str) -> list[tuple[str, dict]]:
        """Recover tool calls the model wrote into its reply instead of calling them."""
        calls = []
        for name, raw in _TEXT_TOOL_CALL.findall(content):
            if name in self.hands.tools:
                try:
                    calls.append((name, json.loads(raw)))
                except ValueError:
                    pass
        return calls

    async def learn(self, text: str) -> list[str]:
        """Pick out lasting facts about the user from what they said, and remember them."""
        user = self.settings.persona.user_name
        known = self.memory.facts()
        prompt = LEARN_PROMPT.format(user=user, text=text,
                                     facts="\n".join(f"- {f['text']}" for f in known) or "- Nothing yet.")
        try:
            message = await self._chat(self.settings.brain.model, [{"role": "user", "content": prompt}],
                                       json_output=True)
            found = json.loads(message.content or "{}").get("facts", [])
        except (BrainUnavailable, ValueError, AttributeError):
            return []
        added = []
        for fact in found if isinstance(found, list) else []:
            # Keep only clean, short facts actually about the user (small models embellish).
            if not isinstance(fact, str) or not fact.lower().startswith(user.lower()) \
                    or not 3 <= len(fact.split()) <= 16 or _SPECULATION.search(fact):
                continue
            # Skip near-duplicates of something already known (ignoring the name, which may differ).
            new = _words(fact.split(None, 1)[1])
            known_words = [_words(k["text"].split(None, 1)[-1]) for k in known]
            if any(len(new & kw) / max(len(new), 1) >= 0.6 for kw in known_words) or \
                    len(new & set().union(*known_words)) / max(len(new), 1) >= 0.8:  # just a mash-up of known facts
                continue
            self.memory.add_fact(fact.strip())
            known.append({"text": fact})
            added.append(fact)
        if added:
            log.info("Learned: %s", added)
        return added

    async def quick(self, instruction: str, context: str = "") -> str:
        """A one-off line Blaze says on his own (greetings, reminders). No tools, not logged."""
        messages = [
            {"role": "system", "content": self.system_prompt(context)},
            {"role": "user", "content": f"(System note, not from {self.settings.persona.user_name}: {instruction} "
                                        "Reply with just the one or two sentences you'd say out loud.)"},
        ]
        message = await self._chat(self.settings.brain.model, messages)
        return (message.content or "").strip().strip('"')

    async def see(self, image: bytes, question: str) -> str:
        """Ask the vision model about an image."""
        messages = [{"role": "user", "content": question or "Describe this image.", "images": [image]}]
        message = await self._chat(self.settings.brain.vision_model, messages)
        return (message.content or "").strip() or "I couldn't make anything out."

    async def warm_up(self) -> None:
        """Load the chat model into memory now, so the first real reply isn't slow."""
        try:
            await self._chat(self.settings.brain.model, [{"role": "user", "content": "hi"}])
            log.info("Model %s is loaded", self.settings.brain.model)
        except BrainUnavailable as e:
            log.warning("Warm-up failed: %s", e)

    async def installed_models(self) -> list[str]:
        try:
            listing = await self.client.list()
        except Exception:
            return []
        return sorted(m.model for m in listing.models)

    def reset(self) -> None:
        self.history.clear()
