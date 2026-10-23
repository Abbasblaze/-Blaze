<p align="center">
  <img src="src/blaze/assets/blaze.svg" width="96" alt="Blaze logo">
</p>

<h1 align="center">Blaze</h1>

<p align="center">
  An always-on AI desk partner for Linux. He sees you through the webcam, talks with you by voice,
  remembers who you are, and runs your laptop for you: all locally, with open-source models.
</p>

<p align="center">
  <img src="docs/screenshots/home.png" width="820" alt="Blaze home screen">
</p>

## What he does

- **Talks with you.** Say *"Hey Blaze, …"* and have a normal back-and-forth conversation. After that
  you don't need to repeat his name. You can interrupt him mid-sentence.
- **Sees you.** He notices when you sit down or leave, recognizes *your* face, welcomes you back,
  reminds you to take breaks, and can look at what you're holding up to the camera.
- **Runs your laptop.** He finds files, searches inside them, opens apps and files, switches and closes
  windows, controls volume, brightness and music, takes screenshots, sets timers, and runs shell
  commands. Anything risky waits for your OK.
- **Remembers you.** He picks up lasting facts about you as you talk, and you can edit them. Every
  conversation is logged locally so you can later fine-tune his model on how the two of you talk.
- **Always there.** He runs as a systemd user service: he starts when you log in, restarts if he crashes,
  stops when you shut down, and handles suspend and resume.
- **Private.** Everything runs on your machine. Camera frames and audio are processed in memory and never saved.

| | |
|---|---|
| <img src="docs/screenshots/camera.png" alt="Camera page"> | <img src="docs/screenshots/memory.png" alt="Memory page"> |
| **Camera:** live view, presence and face recognition | **Memory:** what he knows about you, plus training data export |
| <img src="docs/screenshots/settings.png" alt="Settings page"> | <img src="docs/screenshots/bubble.png" alt="Floating bubble"> |
| **Settings:** every option, applied instantly | **Bubble:** floats on every workspace; click it to open the app |

## How it works

```
 Webcam ─► face detection + recognition (OpenCV) ─┐
 Mic ─► voice detection ─► speech-to-text ────────┼─► Brain (LLM + tools) ─► text-to-speech ─► speaker
                                                  │        │
                                    Memory (SQLite)        └─► Hands: files, apps, windows, media, shell
```

| Part   | Uses |
|--------|------|
| Brain  | [Ollama](https://ollama.com): a small tool-calling chat model plus a vision model (`qwen3.5:4b` does both) |
| Ears   | [webrtcvad](https://github.com/wiseman/py-webrtcvad) + [faster-whisper](https://github.com/SYSTRAN/faster-whisper) on the CPU |
| Voice  | [Kokoro](https://github.com/thewh1teagle/kokoro-onnx) (local, free) or [ElevenLabs](https://elevenlabs.io) (cloud) |
| Eyes   | OpenCV YuNet (face detection) + SFace (face recognition) |
| Hands  | `fd`, `ripgrep`, `hyprctl`, `pactl`, `brightnessctl`, `playerctl`, `grim` |
| App    | PySide6 (Qt 6) |

## Requirements

- Linux with PipeWire or PulseAudio, a webcam and a microphone
- [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com)
- An NVIDIA GPU with 4 GB or more is recommended (it also runs on CPU, just slower)
- Optional tools for laptop control: `fd`, `ripgrep`, `grim`, `playerctl`, `brightnessctl`
- [Hyprland](https://hyprland.org) for window control, the floating bubble and the push-to-talk key.
  The rest works on any desktop.

## Install

```sh
git clone <this repo> blaze && cd blaze
uv sync                        # Python 3.12 environment with everything
ollama pull qwen3.5:4b         # the brain (chat, tools and vision in one model)
.venv/bin/blaze install        # voice + face models, autostart, app launcher, `blaze` command
```

`blaze install`:
- downloads the Kokoro voice and the OpenCV face models (about 400 MB)
- creates the `blaze` and `blaze-ollama` systemd user services and enables them
- adds a *Blaze* entry to your app launcher and puts `blaze` in `~/.local/bin`
- on Hyprland, writes `~/.config/hypr/blaze.conf` (the bubble, its window rule and SUPER+M) and
  adds one `source` line to `hyprland.conf`

Then open the app and set **Settings → Personality → Your name**. Set **Brain → Chat model** and
**Vision model** to `qwen3.5:4b`.

## Use

| To… | Do this |
|---|---|
| Talk to him | Say **"Hey Blaze, …"**, then keep talking normally. Say "bye" to end the conversation. |
| Talk without the wake word | Press **SUPER+M**, or click **Talk** in the app |
| Interrupt him | Just start talking (headphones recommended) |
| Open the app | Click the bubble, pick *Blaze* in your launcher, or run `blaze` |
| Quick actions | Right-click the bubble |
| Use him from a script | `blaze chat "…"`, `blaze say "…"`, `blaze mute`, `blaze unmute`, `blaze status` |

He asks before doing anything risky, such as running a command or closing a window. Answer by voice,
or with the Yes/No bar in the app.

## Settings

Everything is in the app and saved to `~/.config/blaze/settings.json`:

- **Personality:** his name, your name, and how he behaves
- **Brain:** chat and vision models, creativity, how much conversation he keeps in mind
- **Voice:** Kokoro voice, speed, volume and speaker, or ElevenLabs (API key and voice ID)
- **Listening:** wake words, how long a conversation stays open, speech model, language, microphone,
  talk-over
- **Camera:** device, how often he looks around, greeting and break reminders
- **Computer control:** allow shell commands, confirm risky actions, search folders
- **Startup:** start at login, start, restart or stop the service

## Teaching him over time

1. **Memory.** He saves facts about you as you talk. Edit them on the Memory page.
2. **Your face.** Camera → *Learn my face*. Repeat it in different lighting to make recognition sturdier.
3. **Habits.** When you sit down and leave is logged in the `events` table of `~/.local/share/blaze/blaze.db`.
4. **Fine-tuning.** Memory → *Export training data* writes every conversation as chat-format JSONL.
   Use it for a LoRA fine-tune (for example with Unsloth on Colab), then load the result into Ollama
   with a `Modelfile`.

To check how well a model picks tools: `.venv/bin/python scripts/eval_tools.py qwen3.5:4b qwen2.5:3b`

## Project layout

```
src/blaze/
  daemon.py       Blaze himself: wires everything together
  brain.py        Ollama chat, tool calling, vision, learning facts
  hands.py        laptop-control tools
  ears.py         microphone + voice activity detection
  stt.py          speech → text
  voice.py        text → speech
  eyes.py         presence + face recognition
  memory.py       SQLite memory and conversation log
  settings.py     all settings
  ipc.py          daemon ↔ app socket protocol
  cli.py          the `blaze` command and installer
  gui/            the desktop app and the floating bubble
scripts/
  eval_tools.py   how reliably a model picks the right tool
  screenshots.py  regenerates the README screenshots from demo data
docs/screenshots/ images used in this README
```

## Troubleshooting

```sh
journalctl --user -u blaze -f          # live logs: everything he hears and says
systemctl --user restart blaze         # restart him
blaze uninstall                        # remove services, launcher and Hyprland config (keeps your data)
```

- **He doesn't react to "Hey Blaze".** Check the *Mic heard:* line on the Home page. If his name is
  being misheard, add that spelling under Settings → Listening → Wake words.
- **He interrupts himself.** You're probably on speakers. Turn off Settings → Listening →
  *Let me talk over him*.
- **His replies are weak.** Use a bigger or newer model. Small (3B) models struggle with tool use and
  following instructions.
