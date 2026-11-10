#!/usr/bin/env python3
"""Keyboard-layout popup for the Waybar Niri language module."""

import atexit
import glob
import os
from pathlib import Path
import re
import signal
import subprocess
import sys


WIDTH = 280
BAR_GAP = 40
MODULE_OFFSET = 120
PIDFILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "waybar-lang-menu.pid")
COLORS = {
    "bg": "#212121",
    "fg": "#e0e0e0",
    "dim": "#8a8a8a",
    "blue": "#63a4ff",
    "green": "#b5e8a9",
    "red": "#ff9a9e",
}


def toggle_existing():
    current_pid = os.getpid()
    try:
        old_pid = int(Path(PIDFILE).read_text().strip())
        command = Path(f"/proc/{old_pid}/cmdline").read_bytes()
        if old_pid != current_pid and b"lang-menu.py" in command:
            os.kill(old_pid, signal.SIGUSR1)
            sys.exit(0)
    except (OSError, ValueError):
        pass

    Path(PIDFILE).write_text(str(current_pid))

    def cleanup():
        try:
            if Path(PIDFILE).read_text().strip() == str(current_pid):
                os.remove(PIDFILE)
        except OSError:
            pass

    atexit.register(cleanup)


def ensure_layer_shell_preload():
    if not os.environ.get("WAYLAND_DISPLAY") or "gtk-layer-shell" in os.environ.get("LD_PRELOAD", ""):
        return
    for pattern in ("/usr/lib/libgtk-layer-shell.so*", "/usr/lib64/libgtk-layer-shell.so*",
                    "/usr/lib/*/libgtk-layer-shell.so*", "/usr/local/lib*/libgtk-layer-shell.so*"):
        matches = sorted(glob.glob(pattern))
        if matches:
            env = dict(os.environ)
            env["LD_PRELOAD"] = (matches[0] + " " + env.get("LD_PRELOAD", "")).strip()
            os.execve(sys.executable, [sys.executable] + sys.argv, env)


toggle_existing()
ensure_layer_shell_preload()

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell
except (ImportError, ValueError) as error:
    print(f"Language popup needs GTK 3 and gtk-layer-shell: {error}", file=sys.stderr)
    sys.exit(1)


CSS = f"""
* {{ font-family: \"JetBrainsMono Nerd Font Propo\", monospace; font-size: 14px; }}
window.lang-window {{ background-color: transparent; }}
window.lang-backdrop {{ background-color: rgba(0, 0, 0, 0.01); }}
.lang-panel {{ background-color: {COLORS['bg']}; color: {COLORS['fg']};
               border: 1px solid rgba(224, 224, 224, 0.12); border-radius: 16px; padding: 8px; }}
.lang-title {{ color: {COLORS['blue']}; font-weight: bold; padding: 5px 10px; }}
button {{ background-image: none; background-color: transparent; border: none; box-shadow: none;
          text-shadow: none; color: {COLORS['fg']}; padding: 9px 10px; border-radius: 10px; }}
button:hover {{ background-color: rgba(255, 255, 255, 0.10); }}
.lang-current {{ color: {COLORS['green']}; }}
.lang-error {{ color: {COLORS['red']}; padding: 4px 10px; }}
"""


def get_layouts():
    output = subprocess.check_output(["niri", "msg", "keyboard-layouts"], text=True)
    layouts = []
    for line in output.splitlines():
        match = re.match(r"^\s*(\*)?\s*(\d+)\s+(.+)$", line)
        if match:
            layouts.append((int(match.group(2)), match.group(3).strip(), bool(match.group(1))))
    if not layouts:
        raise RuntimeError("Niri did not report any keyboard layouts")
    return layouts


class LanguagePopup:
    def __init__(self):
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Keyboard layout")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.get_style_context().add_class("lang-window")
        self.window.connect("key-press-event", self.on_key)
        self.window.connect("destroy", lambda *_args: Gtk.main_quit())

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        panel.set_size_request(WIDTH, -1)
        panel.get_style_context().add_class("lang-panel")
        title = Gtk.Label(label="Keyboard layout")
        title.set_xalign(0)
        title.get_style_context().add_class("lang-title")
        panel.pack_start(title, False, False, 0)

        self.error = Gtk.Label(label="")
        self.error.set_xalign(0)
        self.error.get_style_context().add_class("lang-error")
        self.error.set_no_show_all(True)
        panel.pack_start(self.error, False, False, 0)

        for index, name, active in get_layouts():
            label = f"{name}    {'✓' if active else ''}"
            button = Gtk.Button(label=label)
            button.set_relief(Gtk.ReliefStyle.NONE)
            if active:
                button.get_style_context().add_class("lang-current")
            button.connect("clicked", self.select_layout, index)
            panel.pack_start(button, False, False, 0)

        self.window.add(panel)
        self.backdrop = None
        self.setup_layer()

    def setup_layer(self):
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()
        _screen, pointer_x, pointer_y = pointer.get_position()
        monitor = display.get_monitor_at_point(pointer_x, pointer_y)
        if monitor is None:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        if pointer_x == 0 and pointer_y == 0:
            pointer_x = geometry.x + geometry.width // 2 + MODULE_OFFSET

        self.layer = GtkLayerShell.is_supported()
        if self.layer:
            GtkLayerShell.init_for_window(self.window)
            GtkLayerShell.set_namespace(self.window, "lang-menu")
            GtkLayerShell.set_monitor(self.window, monitor)
            GtkLayerShell.set_layer(self.window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.LEFT, True)
            GtkLayerShell.set_anchor(self.window, GtkLayerShell.Edge.BOTTOM, True)
            left = max(0, min(pointer_x - geometry.x - WIDTH // 2, geometry.width - WIDTH))
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.LEFT, left)
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.BOTTOM, BAR_GAP)
            GtkLayerShell.set_keyboard_mode(self.window, GtkLayerShell.KeyboardMode.EXCLUSIVE)

            self.backdrop = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.backdrop.set_decorated(False)
            self.backdrop.get_style_context().add_class("lang-backdrop")
            self.backdrop.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.backdrop.connect("button-press-event", lambda *_args: Gtk.main_quit())
            GtkLayerShell.init_for_window(self.backdrop)
            GtkLayerShell.set_namespace(self.backdrop, "lang-menu-backdrop")
            GtkLayerShell.set_monitor(self.backdrop, monitor)
            GtkLayerShell.set_layer(self.backdrop, GtkLayerShell.Layer.TOP)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self.backdrop, edge, True)
            GtkLayerShell.set_exclusive_zone(self.backdrop, -1)
            GtkLayerShell.set_keyboard_mode(self.backdrop, GtkLayerShell.KeyboardMode.NONE)
        else:
            self.fallback_position = (pointer_x - geometry.x, pointer_y - geometry.y)

    def show(self):
        if self.backdrop:
            self.backdrop.show_all()
        self.window.show_all()
        self.window.present()
        if not self.layer:
            x, y = self.fallback_position
            self.window.move(x - WIDTH // 2, y - BAR_GAP - 180)

    def select_layout(self, _button, index):
        try:
            subprocess.run(["niri", "msg", "action", "switch-layout", str(index)], check=True)
            Gtk.main_quit()
        except subprocess.CalledProcessError as error:
            self.error.set_text(f"Could not switch layout (exit {error.returncode})")
            self.error.show()

    @staticmethod
    def on_key(_window, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False


def main():
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    signal.signal(signal.SIGUSR1, lambda *_args: GLib.idle_add(Gtk.main_quit))
    popup = LanguagePopup()
    popup.show()
    Gtk.main()


if __name__ == "__main__":
    main()