#!/usr/bin/env python3
"""winmenu - a small resident start menu for Wayland (GTK3 + gtk-layer-shell).

Toggle it by sending SIGUSR1:  kill -USR1 $(cat $XDG_RUNTIME_DIR/winmenu.pid)
Running the script a second time also toggles the running instance.
Pins:   ~/.config/winmenu/pins.json   (list of .desktop ids, right-click to pin/unpin)
Usage:  ~/.cache/winmenu/usage.json   (launch counts, drives "Most used")
"""
import json
import os
import re
import signal
import subprocess
import sys
import time

PIDFILE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "winmenu.pid")


def running_pid():
    try:
        pid = int(open(PIDFILE).read().strip())
        os.kill(pid, 0)
        if "winmenu" in open(f"/proc/{pid}/cmdline").read():
            return pid
    except Exception:
        pass
    return None


_pid = running_pid()
if _pid:  # already running: just toggle it
    os.kill(_pid, signal.SIGUSR1)
    sys.exit(0)

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, Gio, GLib, Gtk, GtkLayerShell, Pango  # noqa: E402

CONF_DIR = os.path.expanduser("~/.config/winmenu")
CACHE_DIR = os.path.expanduser("~/.cache/winmenu")
PINS_FILE = os.path.join(CONF_DIR, "pins.json")
USAGE_FILE = os.path.join(CACHE_DIR, "usage.json")
SEEN_FILE = os.path.join(CACHE_DIR, "seen.json")
CONFIG_FILE = os.path.join(CONF_DIR, "config.json")

# x = distance (px) from the left screen edge to the left edge of the menu, i.e. where
# your Win button's island starts. bottom = gap above the bar. Edit config.json to tune.
DEFAULT_CONFIG = {"x": 180, "bottom": 8, "terminal": ["alacritty", "-e"], "new_days": 7,
                  "most_used": 8}

CATEGORY_MAP = [("Game", "Games"), ("Development", "Development"), ("Graphics", "Graphics"),
                ("AudioVideo", "Media"), ("Audio", "Media"), ("Video", "Media"),
                ("Network", "Internet"), ("WebBrowser", "Internet"), ("Office", "Office"),
                ("Education", "Education"), ("Science", "Education"),
                ("Settings", "System"), ("System", "System"), ("Utility", "Utilities")]
ARROW_OPEN = "\u25be"
ARROW_CLOSED = "\u25b8"
PINNED_SHOWN = 7  # pinned apps shown on the home screen (plus an "All pinned" tile)
GROUP_ORDER = ["Pinned", "Games", "Internet", "Media", "Graphics", "Office", "Development",
               "Education", "Utilities", "System", "Other"]

LOCK_CMD = "swaylock -c 000000"
DEFAULT_PINS = [
    "firefox.desktop", "org.mozilla.firefox.desktop", "Alacritty.desktop",
    "thunar.desktop", "org.gnome.Nautilus.desktop", "code.desktop",
    "discord.desktop", "steam.desktop", "spotify.desktop",
    "org.telegram.desktop.desktop", "vlc.desktop", "gimp.desktop",
]

CSS = b"""
window#winmenu { background: transparent; }
#backdrop { background-color: rgba(0, 0, 0, 0); }
#panel {
    background-color: #212121;
    border: 1px solid rgba(224, 224, 224, 0.12);
    border-radius: 16px;
}
#panel * { font-family: "JetBrainsMono Nerd Font Propo"; font-size: 13px; color: #e0e0e0; }
#panel searchentry, #panel entry {
    background-color: #3a3a3a; background-image: none; border: none; box-shadow: none;
    border-radius: 12px; padding: 8px 12px; min-height: 0;
}
#panel .section { color: #a3a3a3; font-weight: bold; }
#panel button {
    background-color: transparent; background-image: none; border: none;
    box-shadow: none; text-shadow: none; border-radius: 10px; padding: 8px 4px;
}
#panel button:hover { background-color: rgba(255, 255, 255, 0.10); }
#panel button.link { color: #63a4ff; padding: 4px 10px; }
#panel list, #panel row { background: transparent; }
#panel row { border-radius: 10px; padding: 2px 4px; }
#panel row:hover, #panel row:focus, #panel row:selected { background-color: rgba(255, 255, 255, 0.10); }
#panel .footer { border-top: 1px solid rgba(224, 224, 224, 0.12); padding-top: 8px; }
#panel scrolledwindow, #panel viewport { background: transparent; }
#panel .badge-new { background-color: rgba(181, 232, 169, 0.20); color: #b5e8a9;
    border-radius: 6px; padding: 1px 6px; font-size: 10px; font-weight: bold; }
#panel .badge-term { background-color: rgba(255, 230, 167, 0.18); color: #ffe6a7;
    border-radius: 6px; padding: 1px 6px; font-size: 10px; }
#panel button.chip-active { background-color: rgba(99, 164, 255, 0.22); }
#panel button.cat-header { padding: 6px 8px; }
#panel .toast { background-color: #3a3a3a; border: 1px solid rgba(224, 224, 224, 0.15);
    border-radius: 10px; padding: 6px 14px; }
#panel button.cat-header label { color: #a3a3a3; font-weight: bold; }
"""


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print("winmenu: could not save", path, e, file=sys.stderr)


def icon_image(app, size):
    gicon = app.get_icon()
    if gicon:
        img = Gtk.Image.new_from_gicon(gicon, Gtk.IconSize.DND)
    else:
        img = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DND)
    img.set_pixel_size(size)
    return img


def is_terminal(app):
    """True if the .desktop file says Terminal=true (a CLI/TUI program, not a GUI app)."""
    try:
        return bool(app.get_boolean("Terminal"))
    except Exception:
        return False


def group_of(app):
    try:
        cats = set((app.get_categories() or "").split(";"))
    except Exception:
        cats = set()
    for key, name in CATEGORY_MAP:
        if key in cats:
            return name
    return "Other"


class WinMenu:
    def __init__(self):
        os.makedirs(CONF_DIR, exist_ok=True)
        os.makedirs(CACHE_DIR, exist_ok=True)
        self.cfg = self.read_config()
        self.usage = load_json(USAGE_FILE, {})
        self.seen = load_json(SEEN_FILE, None)
        self.load_apps()
        self.pins = load_json(PINS_FILE, None)
        if self.pins is None:
            self.pins = [i for i in DEFAULT_PINS if i in self.by_id]
            save_json(PINS_FILE, self.pins)
        self.mode = "home"
        self.sort = "az"
        self.hide_term = False
        self.home_open = set()        # categories expanded on the home page
        self.list_collapsed = set()   # categories collapsed on the All apps page
        self.group_rows = {}
        self._bodies = []
        self.dirty = False
        self.build_window()
        self._mark_chips()
        self.build_home()
        Gio.AppInfoMonitor.get().connect("changed", self.on_apps_changed)

    # ---------- data ----------
    def read_config(self):
        cfg = dict(DEFAULT_CONFIG)
        stored = load_json(CONFIG_FILE, None)
        if stored is None:
            save_json(CONFIG_FILE, cfg)
        else:
            cfg.update(stored)
        return cfg

    def load_apps(self):
        apps = [a for a in Gio.AppInfo.get_all() if a.should_show() and a.get_id()]
        apps.sort(key=lambda a: a.get_name().lower())
        self.apps = apps
        self.by_id = {a.get_id(): a for a in apps}
        self.track_new()

    def track_new(self):
        now = time.time()
        if self.seen is None:  # first run: everything installed now counts as old
            self.seen = {a.get_id(): 0 for a in self.apps}
            save_json(SEEN_FILE, self.seen)
            return
        changed = False
        for a in self.apps:
            if a.get_id() not in self.seen:
                self.seen[a.get_id()] = now
                changed = True
        if changed:
            save_json(SEEN_FILE, self.seen)

    def is_new(self, app):
        t = self.seen.get(app.get_id(), 0)
        days = float(self.cfg.get("new_days", 7))
        return t > time.time() - days * 86400 and self.usage.get(app.get_id(), 0) == 0

    def on_apps_changed(self, *_):
        self.load_apps()
        self.dirty = True

    def search(self, q):
        scored = []
        for a in self.apps:
            name = a.get_name().lower()
            exe = os.path.basename(a.get_executable() or "").lower()
            if q in name:
                score = 0 if name.startswith(q) else (1 if (" " + q) in name else 2)
            elif q in exe:
                score = 3
            else:
                continue
            scored.append((score, -self.usage.get(a.get_id(), 0), name, a))
        scored.sort(key=lambda t: t[:3])
        return [t[3] for t in scored]

    # ---------- window ----------
    def build_window(self):
        win = Gtk.Window(title="winmenu")
        win.set_name("winmenu")
        win.set_app_paintable(True)
        visual = Gdk.Screen.get_default().get_rgba_visual()
        if visual:
            win.set_visual(visual)

        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_namespace(win, "winmenu")
        GtkLayerShell.set_layer(win, GtkLayerShell.Layer.OVERLAY)
        for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                     GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
            GtkLayerShell.set_anchor(win, edge, True)
        try:
            GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.EXCLUSIVE)
        except AttributeError:
            GtkLayerShell.set_keyboard_interactivity(win, True)

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Full-screen transparent backdrop: clicking it closes the menu.
        backdrop = Gtk.EventBox()
        backdrop.set_name("backdrop")
        backdrop.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        backdrop.connect("button-press-event", lambda *_: self.hide() or True)

        panel = Gtk.EventBox()
        panel.set_name("panel")
        panel.set_halign(Gtk.Align.START)
        panel.set_valign(Gtk.Align.END)
        panel.set_margin_start(8)
        panel.set_margin_bottom(8)
        panel.set_size_request(540, 640)
        panel.connect("button-press-event", lambda *_: True)  # don't let clicks reach backdrop
        backdrop.add(panel)
        self.panel = panel

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for side in ("top", "bottom", "start", "end"):
            getattr(outer, f"set_margin_{side}")(16)
        panel.add(outer)

        self.entry = Gtk.SearchEntry()
        self.entry.set_placeholder_text("Search apps")
        self.entry.connect("search-changed", self.on_changed)
        self.entry.connect("activate", self.on_activate)
        self.entry.connect("key-press-event", self.on_entry_key)
        outer.pack_start(self.entry, False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        overlay = Gtk.Overlay()
        overlay.add(self.stack)
        self.toast_label = Gtk.Label()
        self.toast_label.get_style_context().add_class("toast")
        self.toast_label.set_halign(Gtk.Align.CENTER)
        self.toast_label.set_valign(Gtk.Align.END)
        self.toast_label.set_margin_bottom(10)
        self.toast_label.set_no_show_all(True)  # hidden until a toast is shown
        overlay.add_overlay(self.toast_label)
        overlay.set_overlay_pass_through(self.toast_label, True)
        self._toast_id = None
        outer.pack_start(overlay, True, True, 0)

        # home page
        self.home_scroll = Gtk.ScrolledWindow()
        self.home_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.home_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.home_scroll.add(self.home_box)
        self.stack.add_named(self.home_scroll, "home")

        # list page (search results / all apps)
        list_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        back = Gtk.Button(label="\u2039  Back")
        back.get_style_context().add_class("link")
        back.set_halign(Gtk.Align.START)
        back.connect("clicked", lambda *_: self.go_home())
        list_page.pack_start(back, False, False, 0)
        self.chips = Gtk.Box(spacing=4)
        self.chip_btns = {}
        for key, label in (("az", "A\u2013Z"), ("cat", "Category"), ("new", "New first")):
            b = Gtk.Button(label=label)
            b.get_style_context().add_class("link")
            b.connect("clicked", lambda _b, k=key: self.set_sort(k))
            self.chips.pack_start(b, False, False, 0)
            self.chip_btns[key] = b
        chk = Gtk.CheckButton(label="Hide terminal apps")
        chk.connect("toggled", self.on_hide_term)
        self.chips.pack_end(chk, False, False, 6)
        list_page.pack_start(self.chips, False, False, 0)
        sw = Gtk.ScrolledWindow()
        self.list_scroll = sw
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-activated", lambda lb, row: row.app and self.launch(row.app))
        sw.add(self.listbox)
        list_page.pack_start(sw, True, True, 0)
        self.stack.add_named(list_page, "list")

        # footer
        footer = Gtk.Box(spacing=4)
        footer.get_style_context().add_class("footer")
        user = Gtk.Label(label=GLib.get_real_name() if GLib.get_real_name() != "Unknown"
                         else GLib.get_user_name())
        user.set_halign(Gtk.Align.START)
        footer.pack_start(user, True, True, 8)
        for icon, cmd, tip in (("system-lock-screen-symbolic", LOCK_CMD, "Lock"),
                               ("system-reboot-symbolic", "systemctl reboot", "Reboot"),
                               ("system-shutdown-symbolic", "systemctl poweroff", "Power off")):
            b = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.BUTTON)
            b.set_tooltip_text(tip)
            b.connect("clicked", self.on_power, cmd)
            footer.pack_end(b, False, False, 0)
        outer.pack_end(footer, False, False, 0)

        win.connect("key-press-event", self.on_key)
        win.add(backdrop)
        backdrop.show_all()
        self.win = win

    # ---------- home ----------
    def tile(self, app):
        btn = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.pack_start(icon_image(app, 32), False, False, 0)
        lbl = Gtk.Label(label=app.get_name())
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(9)
        box.pack_start(lbl, False, False, 0)
        btn.add(box)
        btn.set_tooltip_text(app.get_name() + ("  (runs in terminal)" if is_terminal(app) else ""))
        btn.connect("clicked", lambda *_: self.launch(app))
        btn.connect("button-press-event", self.on_right_click, app)
        return btn

    def wide(self, app):
        btn = Gtk.Button()
        box = Gtk.Box(spacing=10)
        box.pack_start(icon_image(app, 24), False, False, 0)
        lbl = Gtk.Label(label=app.get_name(), xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(16)
        box.pack_start(lbl, True, True, 0)
        btn.add(box)
        btn.connect("clicked", lambda *_: self.launch(app))
        btn.connect("button-press-event", self.on_right_click, app)
        return btn

    def clear(self, container):
        for c in container.get_children():
            container.remove(c)

    def section_label(self, text):
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("section")
        return lbl

    def build_home(self):
        self.clear(self.home_box)
        self._bodies = []

        head = Gtk.Box()
        head.pack_start(self.section_label("Pinned"), True, True, 4)
        all_btn = Gtk.Button(label="All apps  \u203a")
        all_btn.get_style_context().add_class("link")
        all_btn.connect("clicked", lambda *_: self.show_all_apps())
        head.pack_end(all_btn, False, False, 0)
        self.home_box.pack_start(head, False, False, 0)

        pinned = [self.by_id[i] for i in self.pins if i in self.by_id]
        grid = Gtk.FlowBox()
        grid.set_selection_mode(Gtk.SelectionMode.NONE)
        grid.set_homogeneous(True)
        grid.set_min_children_per_line(4)
        grid.set_max_children_per_line(4)
        grid.set_valign(Gtk.Align.START)
        for a in pinned[:PINNED_SHOWN]:
            grid.add(self.tile(a))
        if pinned:
            grid.add(self.pinned_shortcut())
        if not pinned:
            self.home_box.pack_start(Gtk.Label(label="Right-click any app to pin it here."),
                                     False, False, 8)
        self.home_box.pack_start(grid, False, False, 0)

        fresh = [a for a in self.apps if self.is_new(a)]
        fresh.sort(key=lambda a: -self.seen.get(a.get_id(), 0))
        if fresh:
            self.home_box.pack_start(self.section_label("New"), False, False, 4)
            nbox = Gtk.FlowBox()
            nbox.set_selection_mode(Gtk.SelectionMode.NONE)
            nbox.set_homogeneous(True)
            nbox.set_min_children_per_line(2)
            nbox.set_max_children_per_line(2)
            nbox.set_valign(Gtk.Align.START)
            for a in fresh[:4]:
                nbox.add(self.wide(a))
            self.home_box.pack_start(nbox, False, False, 0)

        self.home_box.pack_start(self.section_label("Most used"), False, False, 4)
        top = [self.by_id[i] for i, _ in sorted(self.usage.items(), key=lambda kv: -kv[1])
               if i in self.by_id][:int(self.cfg.get("most_used", 8))]
        if top:
            rec = Gtk.FlowBox()
            rec.set_selection_mode(Gtk.SelectionMode.NONE)
            rec.set_homogeneous(True)
            rec.set_min_children_per_line(2)
            rec.set_max_children_per_line(2)
            rec.set_valign(Gtk.Align.START)
            for a in top:
                rec.add(self.wide(a))
            self.home_box.pack_start(rec, False, False, 0)
        else:
            self.home_box.pack_start(
                Gtk.Label(label="Apps you launch will show up here.", xalign=0),
                False, False, 4)
        pool = [a for a in self.apps if not (self.hide_term and is_terminal(a))]
        groups = {}
        for a in pool:
            groups.setdefault(group_of(a), []).append(a)
        pinned_pool = [a for a in pinned if not (self.hide_term and is_terminal(a))]
        if pinned_pool:
            groups["Pinned"] = pinned_pool
        if groups:
            self.home_box.pack_start(self.section_label("Categories"), False, False, 4)
            for g in GROUP_ORDER:
                if g in groups:
                    self.home_box.pack_start(self.category_block(g, groups[g]), False, False, 0)
        self.home_box.show_all()
        for body, g in self._bodies:  # show_all() above would reveal collapsed bodies
            body.set_visible(g in self.home_open)

    # ---------- list ----------
    def badge_labels(self, a):
        out = []
        if is_terminal(a):
            t = Gtk.Label(label="\uf120 terminal")
            t.get_style_context().add_class("badge-term")
            out.append(t)
        if self.is_new(a):
            n = Gtk.Label(label="NEW")
            n.get_style_context().add_class("badge-new")
            out.append(n)
        return out

    def row_button(self, app):
        btn = Gtk.Button()
        box = Gtk.Box(spacing=10)
        box.pack_start(icon_image(app, 24), False, False, 4)
        lbl = Gtk.Label(label=app.get_name(), xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        box.pack_start(lbl, True, True, 0)
        for b in self.badge_labels(app):
            box.pack_end(b, False, False, 4)
        btn.add(box)
        btn.connect("clicked", lambda *_: self.launch(app))
        btn.connect("button-press-event", self.on_right_click, app)
        return btn

    def category_block(self, group, apps):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for a in apps:
            body.pack_start(self.row_button(a), False, False, 0)
        hdr = Gtk.Button()
        hdr.get_style_context().add_class("cat-header")
        lbl = Gtk.Label(xalign=0)
        hdr.add(lbl)

        def refresh():
            opened = group in self.home_open
            lbl.set_text(f"{ARROW_OPEN if opened else ARROW_CLOSED}  {group}  ({len(apps)})")
            body.set_visible(opened)

        def toggle(_b):
            if group in self.home_open:
                self.home_open.discard(group)
            else:
                self.home_open.add(group)
            refresh()

        hdr.connect("clicked", toggle)
        outer.pack_start(hdr, False, False, 0)
        outer.pack_start(body, False, False, 0)
        self._bodies.append((body, group))
        refresh()
        return outer

    def add_header(self, text):
        row = Gtk.ListBoxRow()
        row.app = None
        row.set_activatable(False)
        row.set_selectable(False)
        lbl = Gtk.Label(label=text, xalign=0)
        lbl.get_style_context().add_class("section")
        lbl.set_margin_top(8)
        lbl.set_margin_start(6)
        row.add(lbl)
        self.listbox.add(row)

    def add_cat_header(self, group, count):
        row = Gtk.ListBoxRow()
        row.app = None
        row.set_activatable(False)
        row.set_selectable(False)
        btn = Gtk.Button()
        btn.get_style_context().add_class("cat-header")
        lbl = Gtk.Label(xalign=0)
        btn.add(lbl)

        def refresh():
            closed = group in self.list_collapsed
            lbl.set_text(f"{ARROW_CLOSED if closed else ARROW_OPEN}  {group}  ({count})")

        def toggle(_b):
            if group in self.list_collapsed:
                self.list_collapsed.discard(group)
            else:
                self.list_collapsed.add(group)
            for r in self.group_rows.get(group, []):
                r.set_visible(group not in self.list_collapsed)
            refresh()

        btn.connect("clicked", toggle)
        refresh()
        row.add(btn)
        self.listbox.add(row)

    def add_app_row(self, a):
        row = Gtk.ListBoxRow()
        row.app = a
        eb = Gtk.EventBox()
        hb = Gtk.Box(spacing=12)
        hb.set_margin_top(4)
        hb.set_margin_bottom(4)
        hb.pack_start(icon_image(a, 24), False, False, 4)
        lbl = Gtk.Label(label=a.get_name(), xalign=0)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        hb.pack_start(lbl, True, True, 0)
        for b in self.badge_labels(a):
            hb.pack_end(b, False, False, 4)
        eb.add(hb)
        eb.connect("button-press-event", self.on_right_click, a)
        row.add(eb)
        self.listbox.add(row)
        return row

    def populate_list(self, query):
        self.clear(self.listbox)
        self.group_rows = {}
        base = self.search(query)[:80] if query else self.apps
        pool = [a for a in base if not (self.hide_term and is_terminal(a))]
        self.chips.set_visible(not query)
        if query:
            for a in pool:
                self.add_app_row(a)
        elif self.sort == "cat":
            groups = {}
            for a in pool:
                groups.setdefault(group_of(a), []).append(a)
            pool_ids = {a.get_id() for a in pool}
            pinned_pool = [self.by_id[i] for i in self.pins if i in pool_ids]
            if pinned_pool:
                groups["Pinned"] = pinned_pool
            for g in GROUP_ORDER:
                if g in groups:
                    self.add_cat_header(g, len(groups[g]))
                    for a in groups[g]:
                        self.group_rows.setdefault(g, []).append(self.add_app_row(a))
        elif self.sort == "new":
            fresh = sorted([a for a in pool if self.is_new(a)],
                           key=lambda a: -self.seen.get(a.get_id(), 0))
            ids = {a.get_id() for a in fresh}
            if fresh:
                self.add_header("New")
                for a in fresh:
                    self.add_app_row(a)
            self.add_header("Everything else" if fresh else "All apps")
            for a in pool:
                if a.get_id() not in ids:
                    self.add_app_row(a)
        else:
            last = None
            for a in pool:
                ch = a.get_name()[:1].upper()
                if not ch.isalpha():
                    ch = "#"
                if ch != last:
                    self.add_header(ch)
                    last = ch
                self.add_app_row(a)
        self.listbox.show_all()
        for g in self.list_collapsed:
            for r in self.group_rows.get(g, []):
                r.set_visible(False)

    def _mark_chips(self):
        for k, b in self.chip_btns.items():
            ctx = b.get_style_context()
            (ctx.add_class if k == self.sort else ctx.remove_class)("chip-active")

    def set_sort(self, key):
        self.sort = key
        self._mark_chips()
        self.populate_list(self.entry.get_text().strip().lower())

    def on_hide_term(self, chk):
        self.hide_term = chk.get_active()
        self.build_home()
        self.populate_list(self.entry.get_text().strip().lower())

    def first_app_row(self):
        i = 0
        while True:
            row = self.listbox.get_row_at_index(i)
            if row is None:
                return None
            if getattr(row, "app", None) and row.get_visible():
                return row
            i += 1

    # ---------- behaviour ----------
    def on_changed(self, entry):
        q = entry.get_text().strip().lower()
        if q:
            self.populate_list(q)
            self.stack.set_visible_child_name("list")
        elif self.mode == "all":
            self.populate_list("")
            self.stack.set_visible_child_name("list")
        else:
            self.stack.set_visible_child_name("home")

    def on_activate(self, entry):
        if self.stack.get_visible_child_name() == "list":
            row = self.first_app_row()
            if row:
                self.launch(row.app)

    def on_entry_key(self, entry, ev):
        if ev.keyval == Gdk.KEY_Down and self.stack.get_visible_child_name() == "list":
            row = self.first_app_row()
            if row:
                self.listbox.select_row(row)
                row.grab_focus()
                return True
        return False

    def on_key(self, win, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def on_right_click(self, widget, ev, app):
        if ev.type == Gdk.EventType.BUTTON_PRESS and ev.button == 3:
            aid = app.get_id()
            if aid in self.pins:
                self.pins.remove(aid)
                self.toast(f"\uf08d  Unpinned {app.get_name()}")
            else:
                self.pins.append(aid)
                self.toast(f"\uf08d  Pinned {app.get_name()}")
            save_json(PINS_FILE, self.pins)
            # rebuild after this handler returns: the widget that was clicked gets destroyed
            GLib.idle_add(self._refresh_after_pin)
            return True
        return False

    def _refresh_after_pin(self):
        self.build_home()
        if self.stack.get_visible_child_name() == "list":
            self.populate_list(self.entry.get_text().strip().lower())
        return False

    def toast(self, text):
        self.toast_label.set_text(text)
        self.toast_label.set_visible(True)
        if self._toast_id:
            GLib.source_remove(self._toast_id)
        self._toast_id = GLib.timeout_add(1800, self._hide_toast)

    def _hide_toast(self):
        self.toast_label.set_visible(False)
        self._toast_id = None
        return False

    def pinned_shortcut(self):
        btn = Gtk.Button()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        img = Gtk.Image.new_from_icon_name("go-next-symbolic", Gtk.IconSize.DND)
        img.set_pixel_size(32)
        box.pack_start(img, False, False, 0)
        box.pack_start(Gtk.Label(label="All pinned"), False, False, 0)
        btn.add(box)
        btn.set_tooltip_text("Open the Pinned category")
        btn.connect("clicked", lambda *_: self.show_pinned_category())
        return btn

    def show_pinned_category(self):
        """Jump to All apps, sorted by category, with only Pinned expanded."""
        self.sort = "cat"
        self._mark_chips()
        self.list_collapsed = set(GROUP_ORDER) - {"Pinned"}
        self.entry.set_text("")
        self.show_all_apps()
        self.list_scroll.get_vadjustment().set_value(0)

    def on_power(self, _btn, cmd):
        self.hide()
        GLib.spawn_command_line_async(cmd)

    def show_all_apps(self):
        self.mode = "all"
        self.populate_list("")
        self.stack.set_visible_child_name("list")

    def go_home(self):
        self.mode = "home"
        self.entry.set_text("")
        self.stack.set_visible_child_name("home")
        self.entry.grab_focus()

    def launch(self, app):
        self.hide()
        aid = app.get_id()
        self.usage[aid] = self.usage.get(aid, 0) + 1
        save_json(USAGE_FILE, self.usage)
        self.dirty = True
        GLib.idle_add(self._launch, app)

    def _launch(self, app):
        try:
            if is_terminal(app):
                # Terminal=true apps are CLI/TUI programs: run them inside the terminal.
                cmd = re.sub(r"%[a-zA-Z%]", "", app.get_commandline() or "").strip()
                subprocess.Popen(list(self.cfg["terminal"]) + ["sh", "-c", cmd],
                                 start_new_session=True)
            else:
                ctx = Gdk.Display.get_default().get_app_launch_context()
                app.launch([], ctx)
        except Exception as e:
            print("winmenu: launch failed:", e, file=sys.stderr)
        return False

    # ---------- show / hide ----------
    def show(self):
        self.cfg = self.read_config()  # re-read so edits to config.json apply instantly
        self.list_collapsed.clear()
        self.panel.set_margin_start(int(self.cfg["x"]))
        self.panel.set_margin_bottom(int(self.cfg["bottom"]))
        if self.dirty:
            self.build_home()
            self.dirty = False
        self.mode = "home"
        self.entry.set_text("")
        self.stack.set_visible_child_name("home")
        self.home_scroll.get_vadjustment().set_value(0)
        self.win.show()
        self.entry.grab_focus()

    def hide(self):
        self.win.hide()

    def toggle(self, *_):
        if self.win.get_visible():
            self.hide()
        else:
            self.show()
        return True  # keep the signal handler installed


def main():
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    menu = WinMenu()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGUSR1, menu.toggle)
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, Gtk.main_quit)
    if "--show" in sys.argv:  # started by a click: open right away
        GLib.idle_add(lambda: menu.show() or False)
    try:
        Gtk.main()
    finally:
        try:
            os.remove(PIDFILE)
        except OSError:
            pass


if __name__ == "__main__":
    main()
