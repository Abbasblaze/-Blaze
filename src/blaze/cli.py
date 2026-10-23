"""The `blaze` command.

  blaze                  open the app (same as `blaze gui`)
  blaze install          set up autostart, app launcher, Hyprland bubble + SUPER+M, download models
  blaze daemon           run Blaze in the foreground (systemd runs this)
  blaze gui              open the app
  blaze bubble           show just the floating bubble (runs at login)
  blaze talk             start listening without the wake word (SUPER+M)
  blaze say "text"       make Blaze say something
  blaze chat "text"      ask Blaze something by text
  blaze stop | mute | unmute | status
  blaze export-training  write your conversations out as fine-tuning data
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import ipc
from .paths import ICON_FILE

SYSTEMD_DIR = Path.home() / ".config/systemd/user"
APPS_DIR = Path.home() / ".local/share/applications"
BIN_DIR = Path.home() / ".local/bin"
HYPR_DIR = Path.home() / ".config/hypr"
HYPR_SOURCE_LINE = "source = ~/.config/hypr/blaze.conf"

BLAZE_UNIT = """[Unit]
Description=Blaze, your AI desk partner
Wants=blaze-ollama.service
After=blaze-ollama.service pipewire.service pipewire-pulse.service

[Service]
ExecStart={exe} daemon
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

OLLAMA_UNIT = """[Unit]
Description=Ollama model server (for Blaze)

[Service]
ExecStart={ollama} serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Blaze
Comment=Your AI desk partner
Exec={exe} gui
Icon={icon}
Terminal=false
Categories=Utility;
"""


HYPR_CONF = """# Blaze — written by `blaze install`. Remove the matching `source` line in hyprland.conf to disable.
exec-once = {exe} bubble
bind = SUPER, M, exec, {exe} talk
windowrule = match:title ^(Blaze Bubble)$, float on, pin on, size 80 80, move (monitor_w-100) 70, border_size 0, no_shadow on, no_blur on, no_initial_focus on, no_anim on, rounding 0
"""


def install_hyprland(exe: Path) -> None:
    """Floating bubble at login, push-to-talk on SUPER+M."""
    main_conf = HYPR_DIR / "hyprland.conf"
    if not main_conf.exists():
        print("  (Hyprland config not found — skipping bubble and SUPER+M)")
        return
    (HYPR_DIR / "blaze.conf").write_text(HYPR_CONF.format(exe=exe))
    if HYPR_SOURCE_LINE not in main_conf.read_text():
        with main_conf.open("a") as f:
            f.write(f"\n# Blaze, AI desk partner\n{HYPR_SOURCE_LINE}\n")
    subprocess.run(["hyprctl", "reload"], capture_output=True)


def systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args]).returncode


def install() -> None:
    exe = Path(sys.executable).with_name("blaze")  # the entry point inside this virtualenv
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    BIN_DIR.mkdir(parents=True, exist_ok=True)

    print("• Downloading voice and face models")
    from .models import download_all
    download_all()

    print("• Installing systemd user services")
    (SYSTEMD_DIR / "blaze.service").write_text(BLAZE_UNIT.format(exe=exe))
    ollama = shutil.which("ollama")
    if ollama:
        (SYSTEMD_DIR / "blaze-ollama.service").write_text(OLLAMA_UNIT.format(ollama=ollama))
    else:
        print("  (ollama not found — install it from https://ollama.com, then re-run `blaze install`)")

    print("• Adding Blaze to your app launcher")
    (APPS_DIR / "blaze.desktop").write_text(DESKTOP_ENTRY.format(exe=exe, icon=ICON_FILE))
    link = BIN_DIR / "blaze"
    if link.is_symlink() or not link.exists():
        link.unlink(missing_ok=True)
        link.symlink_to(exe)

    print("• Adding the floating bubble and SUPER+M (push-to-talk) to Hyprland")
    install_hyprland(exe)

    systemctl("daemon-reload")
    units = ["blaze-ollama.service", "blaze.service"] if ollama else ["blaze.service"]
    systemctl("enable", "--now", *units)
    print("\nBlaze is running and will start every time you log in.")
    print("Open the app from your launcher, by clicking the bubble, or run: blaze")
    # Let Hyprland own the bubble so it isn't tied to this terminal.
    subprocess.run(["hyprctl", "dispatch", "exec", f"{exe} bubble"], capture_output=True)


def uninstall() -> None:
    systemctl("disable", "--now", "blaze.service", "blaze-ollama.service")
    for f in (SYSTEMD_DIR / "blaze.service", SYSTEMD_DIR / "blaze-ollama.service",
              APPS_DIR / "blaze.desktop"):
        f.unlink(missing_ok=True)
    if (BIN_DIR / "blaze").is_symlink():
        (BIN_DIR / "blaze").unlink()
    main_conf = HYPR_DIR / "hyprland.conf"
    if main_conf.exists():
        lines = main_conf.read_text().splitlines(keepends=True)
        kept = [line for line in lines if line.strip() not in (HYPR_SOURCE_LINE, "# Blaze, AI desk partner")]
        main_conf.write_text("".join(kept))
        (HYPR_DIR / "blaze.conf").unlink(missing_ok=True)
        subprocess.run(["hyprctl", "reload"], capture_output=True)
    systemctl("daemon-reload")
    print("Removed the services and launcher. Your settings, memory and models are kept in ~/.config/blaze and ~/.local/share/blaze.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="blaze", description="Blaze, your AI desk partner.")
    sub = parser.add_subparsers(dest="cmd")
    for name in ("install", "uninstall", "daemon", "gui", "bubble", "talk", "stop", "mute", "unmute", "status",
                 "export-training"):
        sub.add_parser(name)
    for name in ("say", "chat"):
        sub.add_parser(name).add_argument("text", nargs="+")
    args = parser.parse_args()

    if args.cmd == "install":
        return install()
    if args.cmd == "uninstall":
        return uninstall()
    if args.cmd == "daemon":
        from .daemon import main as run_daemon
        return run_daemon()
    if args.cmd in (None, "gui", "bubble"):
        from .gui.app import main as run_gui
        return run_gui(show_window=args.cmd != "bubble")

    try:
        match args.cmd:
            case "say":
                ipc.request("say", text=" ".join(args.text))
            case "chat":
                print(ipc.request("chat", timeout=120, text=" ".join(args.text), speak=False))
            case "talk" | "stop":
                ipc.request(args.cmd)
            case "mute" | "unmute":
                ipc.request("mute", muted=args.cmd == "mute")
            case "status":
                print(json.dumps(ipc.request("status"), indent=2))
            case "export-training":
                print(ipc.request("export_training"))
    except (ConnectionError, RuntimeError, TimeoutError) as e:
        sys.exit(str(e))
