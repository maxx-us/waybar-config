#!/usr/bin/env python3
"""Install this Waybar configuration under XDG_CONFIG_HOME/waybar."""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent.parent
FILES = ("config.jsonc", "style.css", "displays-menu.xml")
DIRECTORIES = ("icons", "modules", "scripts", "winmenu")
AUTHOR_PATH = "/home/sunskin/.config/waybar"
PACKAGE_SETS = {
    "pacman": {
        "core": ["waybar", "niri", "python-gobject", "gtk3", "gtk-layer-shell"],
        "full": ["networkmanager", "pipewire", "wireplumber", "playerctl", "swaylock",
                 "alacritty", "htop", "power-profiles-daemon", "python-gobject", "gtk3",
                 "gtk-layer-shell"],
    },
    "apt": {
        "core": ["waybar", "niri", "python3-gi", "gir1.2-gtk-3.0", "gir1.2-gtklayershell-0.1"],
        "full": ["network-manager", "pipewire", "wireplumber", "playerctl", "swaylock",
                 "alacritty", "htop", "power-profiles-daemon"],
    },
    "dnf": {
        "core": ["waybar", "niri", "python3-gobject", "gtk3", "gtk-layer-shell"],
        "full": ["NetworkManager", "pipewire", "wireplumber", "playerctl", "swaylock",
                 "alacritty", "htop", "power-profiles-daemon", "python3-gobject", "gtk3"],
    },
}


def backup_path(path: Path) -> Path:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.name}.bak-{stamp}")
    suffix = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.bak-{stamp}-{suffix}")
        suffix += 1
    return candidate


def install_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        backup = backup_path(destination)
        shutil.copy2(destination, backup)
        print(f"Backed up {destination} to {backup}")
    shutil.copy2(source, destination)


def rewrite_paths(config_dir: Path) -> None:
    config_path = config_dir / "config.jsonc"
    config_text = config_path.read_text(encoding="utf-8")
    json_path = json.dumps(str(config_dir))[1:-1]
    config_text = config_text.replace(AUTHOR_PATH, json_path)
    config_text = config_text.replace("~/.config/waybar", json_path)

    taskbar_module = config_dir / "modules" / "libniri_taskbar.so"
    if taskbar_module.is_file():
        config_text = config_text.replace(
            "/home/sunskin/.config/waybar/modules/libniri_taskbar.so",
            json.dumps(str(taskbar_module))[1:-1],
        )
    else:
        config_text = config_text.replace(
            '["custom/menu", "cffi/niri-taskbar"]', '["custom/menu"]'
        )
        config_text = re.sub(
            r'\n\s*"cffi/niri-taskbar":\s*\{[^}]*\},', "", config_text
        )

    niri_displays = shutil.which("niri-displays")
    config_text = config_text.replace(
        "/usr/local/bin/niri-displays", niri_displays or "niri-displays"
    )
    config_path.write_text(config_text, encoding="utf-8")

    css_path = config_dir / "style.css"
    css_path_text = css_path.read_text(encoding="utf-8")
    css_config_path = str(config_dir).replace("\\", "\\\\").replace('"', '\\"')
    css_path.write_text(
        css_path_text.replace(AUTHOR_PATH, css_config_path), encoding="utf-8"
    )


def select_profile() -> str:
    print("Choose Waybar install profile:")
    print("  1) Core: clock, workspaces, language picker")
    print("  2) Full: all configured modules and scripts")
    while True:
        choice = input("Selection [1/2]: ").strip()
        if choice in ("1", "2"):
            return "core" if choice == "1" else "full"
        print("Enter 1 or 2.")


def select_package_manager() -> str | None:
    for manager in ("pacman", "apt", "dnf"):
        if shutil.which(manager):
            return manager
    return None


def install_dependencies(manager: str, profile: str) -> None:
    packages = list(PACKAGE_SETS[manager]["core"])
    if profile == "full":
        packages += PACKAGE_SETS[manager]["full"]
    if manager == "pacman":
        command = ["pacman", "-S", "--needed", *packages]
    elif manager == "apt":
        command = ["apt", "install", *packages]
    else:
        command = ["dnf", "install", *packages]
    if os.geteuid() != 0:
        command.insert(0, "sudo")
    print(f"Installing packages with {manager}: {' '.join(packages)}")
    subprocess.run(command, check=True)


def configure_profile(config_dir: Path, profile: str) -> None:
    if profile != "core":
        return
    config_path = config_dir / "config.jsonc"
    config_text = config_path.read_text(encoding="utf-8")
    config_text = re.sub(
        r'("modules-left"\s*:\s*)\[.*?\]', r'\1[]', config_text, count=1, flags=re.S
    )
    config_text = re.sub(
        r'("modules-center"\s*:\s*)\[.*?\]',
        r'\1["clock", "niri/workspaces", "niri/language"]',
        config_text,
        count=1,
        flags=re.S,
    )
    config_text = re.sub(
        r'("modules-right"\s*:\s*)\[.*?\]', r'\1[]', config_text, count=1, flags=re.S
    )
    config_path.write_text(config_text, encoding="utf-8")


def check_dependencies(profile: str) -> None:
    missing = [command for command in ("waybar", "niri") if shutil.which(command) is None]
    if missing:
        print(f"Missing required command(s): {', '.join(missing)}")
    gtk_check = subprocess.run(
        ["python3", "-c", "import gi; gi.require_version('Gtk', '3.0'); gi.require_version('GtkLayerShell', '0.1')"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if gtk_check.returncode:
        print("The GTK language popup needs Python GTK 3 and gtk-layer-shell introspection.")
    if shutil.which("niri-displays") is None:
        print("niri-displays not found; the display module will stay hidden.")
    if profile == "full":
        for command in ("nmcli", "playerctl", "wpctl", "swaylock", "alacritty"):
            if shutil.which(command) is None:
                print(f"Full profile optional dependency not found: {command}")
    if not missing and not gtk_check.returncode:
        print("Found Waybar, Niri, and GTK layer-shell support.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install the Waybar config and scripts.")
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="Waybar config directory (default: $XDG_CONFIG_HOME/waybar)",
    )
    parser.add_argument("--profile", choices=("core", "full"), help="Skip the interactive profile selector")
    parser.add_argument("--install-deps", action="store_true", help="Install packages using pacman, apt, or dnf")
    parser.add_argument("--skip-deps", action="store_true", help="Do not ask about package installation")
    args = parser.parse_args()

    if args.config_dir:
        config_dir = args.config_dir.expanduser()
    else:
        xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
        if not xdg_config_home.is_absolute():
            parser.error("XDG_CONFIG_HOME must be an absolute path")
        config_dir = xdg_config_home / "waybar"
    config_dir = config_dir.resolve()

    for filename in FILES:
        install_file(ROOT / filename, config_dir / filename)
    for dirname in DIRECTORIES:
        source_dir = ROOT / dirname
        for source in sorted(source_dir.rglob("*")):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            install_file(source, config_dir / source.relative_to(ROOT))

    profile = args.profile or (select_profile() if sys.stdin.isatty() else "full")
    rewrite_paths(config_dir)
    configure_profile(config_dir, profile)
    print(f"Installed Waybar files to {config_dir}")
    manager = select_package_manager()
    should_install = args.install_deps
    if not should_install and not args.skip_deps and sys.stdin.isatty():
        answer = input(f"Install {profile} profile packages with {manager or 'a supported package manager'}? [y/N]: ")
        should_install = answer.strip().lower() in ("y", "yes")
    if should_install:
        if manager is None:
            parser.error("No supported package manager found (supported: pacman, apt, dnf)")
        install_dependencies(manager, profile)
    elif manager is None:
        print("No supported package manager found; install prerequisites manually.")
    check_dependencies(profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())