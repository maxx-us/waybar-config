#!/usr/bin/env python3
"""Bluetooth popup for Waybar: a GTK3 window that talks to BlueZ directly over D-Bus.

  click the module  -> popup opens (devices appear instantly, live-updating)
  click it again    -> popup closes   (also: Esc, or click anywhere outside it)

Needs: python-gobject, gtk3, gtk-layer-shell (Arch: pacman -S python-gobject gtk3 gtk-layer-shell)
If something goes wrong, the reason is in ~/.cache/bluetooth-menu.log
"""
import atexit
import glob
import os
import re
import signal
import string
import subprocess
import sys
import time

# ----------------------------- tweak me -----------------------------
WIDTH = 340            # popup width in px
MARGIN_BOTTOM = 8      # gap above the bar
MARGIN_RIGHT = 12      # distance from the right screen edge (raise it to slide the popup left)
SCAN_SECONDS = 20      # a scan stops by itself after this long
OPEN_MINUTES = 10      # "Let any device connect" switches itself off after this long (0 = until you stop it)
FONT = "JetBrainsMono Nerd Font Propo"
COLORS = dict(         # same palette as your Waybar style.css
    bg="#212121", bg_light="#3a3a3a", fg="#e0e0e0", dim="#8a8a8a", off="#5a5a5a",
    red="#ff9a9e", green="#b5e8a9", yellow="#ffe6a7", blue="#63a4ff", magenta="#dda0dd",
)
# --------------------------------------------------------------------

RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
PIDFILE = os.path.join(RUNTIME, "bluetooth-menu.pid")
LOGFILE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                       "bluetooth-menu.log")


# ---- start-up work that must happen BEFORE the heavy GTK import (keeps the toggle instant) ----

def _is_ours(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return b"bluetooth-menu" in f.read()
    except OSError:
        return False


def _toggle_or_claim():
    """If a popup process already exists, tell it to toggle and exit; otherwise claim the pid file."""
    me = os.getpid()
    try:
        with open(PIDFILE) as f:
            old = int(f.read().strip())
    except (OSError, ValueError):
        old = 0
    if old and old != me and _is_ours(old):
        try:
            os.kill(old, signal.SIGUSR1)     # "toggle": the running popup opens, closes or hides itself
        except OSError:
            pass
        sys.exit(0)
    with open(PIDFILE, "w") as f:
        f.write(str(me))

    def _cleanup():
        try:
            with open(PIDFILE) as f:
                if f.read().strip() == str(me):
                    os.remove(PIDFILE)
        except OSError:
            pass
    atexit.register(_cleanup)


def _ensure_layer_shell_preload():
    """gtk-layer-shell must be loaded before libwayland-client; re-exec once with LD_PRELOAD."""
    if not os.environ.get("WAYLAND_DISPLAY") or "gtk-layer-shell" in os.environ.get("LD_PRELOAD", ""):
        return
    for pattern in ("/usr/lib/libgtk-layer-shell.so*", "/usr/lib64/libgtk-layer-shell.so*",
                    "/usr/lib/*/libgtk-layer-shell.so*", "/usr/local/lib*/libgtk-layer-shell.so*"):
        hits = sorted(glob.glob(pattern))
        if hits:
            env = dict(os.environ)
            env["LD_PRELOAD"] = (hits[0] + " " + env.get("LD_PRELOAD", "")).strip()
            os.execve(sys.executable, [sys.executable] + sys.argv, env)


def _setup_log():
    try:
        os.makedirs(os.path.dirname(LOGFILE), exist_ok=True)
        if os.path.exists(LOGFILE) and os.path.getsize(LOGFILE) > 200000:
            os.remove(LOGFILE)
        log = open(LOGFILE, "a", buffering=1)
        os.dup2(log.fileno(), 2)             # GTK/GLib warnings too, not just Python errors
        sys.stderr = log
        print("--- %s start ---" % time.strftime("%F %T"), file=log)
    except OSError:
        pass


if __name__ == "__main__":
    _toggle_or_claim()
    _ensure_layer_shell_preload()
    _setup_log()

# ------------------------------------------ GTK / GLib ------------------------------------------
import gi  # noqa: E402

Gtk = Gdk = Pango = GtkLayerShell = None
try:
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gtk, Gdk, Pango  # noqa: E402
except (ValueError, ImportError):
    Gtk = Gdk = Pango = None
try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell  # noqa: E402
except (ValueError, ImportError):
    GtkLayerShell = None
from gi.repository import Gio, GLib  # noqa: E402

BLUEZ = "org.bluez"
IF_ADAPTER = "org.bluez.Adapter1"
IF_DEVICE = "org.bluez.Device1"
IF_BATTERY = "org.bluez.Battery1"
IF_AGENT_MGR = "org.bluez.AgentManager1"
IF_PROPS = "org.freedesktop.DBus.Properties"
IF_OM = "org.freedesktop.DBus.ObjectManager"
AGENT_PATH = "/org/sunskin/bluetooth_menu/agent"

AGENT_XML = """<node><interface name="org.bluez.Agent1">
  <method name="Release"/>
  <method name="RequestPinCode"><arg type="o" direction="in"/><arg type="s" direction="out"/></method>
  <method name="DisplayPinCode"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="RequestPasskey"><arg type="o" direction="in"/><arg type="u" direction="out"/></method>
  <method name="DisplayPasskey"><arg type="o" direction="in"/><arg type="u" direction="in"/><arg type="q" direction="in"/></method>
  <method name="RequestConfirmation"><arg type="o" direction="in"/><arg type="u" direction="in"/></method>
  <method name="RequestAuthorization"><arg type="o" direction="in"/></method>
  <method name="AuthorizeService"><arg type="o" direction="in"/><arg type="s" direction="in"/></method>
  <method name="Cancel"/>
</interface></node>"""

# Nerd Font glyphs (Material Design set), written as codepoints so no locale can mangle them
G_BT, G_BT_OFF, G_BT_CONN = "\U000F00AF", "\U000F00B2", "\U000F00B1"
G_POWER, G_SCAN, G_FORGET, G_BATT = "\U000F0425", "\U000F0450", "\U000F01B4", "\U000F0079"
G_OPEN = "\U000F00B3"
ICONS = {
    "audio-headset": "\U000F02CB", "audio-headphones": "\U000F02CB", "audio-card": "\U000F04C3",
    "input-keyboard": "\U000F030C", "input-mouse": "\U000F037D", "input-gaming": "\U000F02B4",
    "phone": "\U000F011C", "computer": "\U000F0322",
}

# property changes that fire constantly while scanning and never change what we draw
NOISY = {"RSSI", "TxPower", "ManufacturerData", "ServiceData", "AdvertisingData", "AdvertisingFlags"}


def clean_err(err):
    msg = getattr(err, "message", None) or str(err)
    if "ServiceUnknown" in msg or "NameHasNoOwner" in msg:
        return "the Bluetooth service (bluetoothd) isn't running"
    msg = re.sub(r"^GDBus\.Error:[\w.]+:\s*", "", msg).strip()
    return msg or "unknown error"


# ===================================== BlueZ client (no GTK) =====================================

class Bluez:
    """Keeps a live copy of BlueZ's object tree and wraps the few calls the popup needs."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.objs = {}                 # path -> {interface -> {property -> value}}
        self.busy = {}                 # device path -> "Connecting…" etc.
        self.service_error = None
        self.agent_handler = None
        self._timer = 0
        self._agent_reg = None
        self._scan_ours = False
        self.open_mode = False          # "any device may connect" is on
        self._open_saved = {}
        kind = Gio.BusType.SESSION if os.environ.get("BT_MENU_BUS") == "session" else Gio.BusType.SYSTEM
        self.bus = Gio.bus_get_sync(kind, None)
        self.reload()
        for iface, member, path in ((IF_OM, "InterfacesAdded", "/"),
                                    (IF_OM, "InterfacesRemoved", "/"),
                                    (IF_PROPS, "PropertiesChanged", None)):
            self.bus.signal_subscribe(BLUEZ, iface, member, path, None,
                                      Gio.DBusSignalFlags.NONE, self._on_signal)

    # ---- state ----
    def reload(self):
        try:
            ret = self.bus.call_sync(BLUEZ, "/", IF_OM, "GetManagedObjects", None,
                                     GLib.VariantType("(a{oa{sa{sv}}})"),
                                     Gio.DBusCallFlags.NONE, 3000, None)
            self.objs = ret.unpack()[0]
            self.service_error = None
        except GLib.Error as e:
            self.objs = {}
            self.service_error = clean_err(e)

    def _on_signal(self, _conn, _sender, path, _iface, signal_name, params, *_rest):
        args = params.unpack()
        if signal_name == "InterfacesAdded":
            self.objs.setdefault(args[0], {}).update(args[1])
            self._changed()
        elif signal_name == "InterfacesRemoved":
            for name in args[1]:
                self.objs.get(args[0], {}).pop(name, None)
            if not self.objs.get(args[0]):
                self.objs.pop(args[0], None)
            self.busy.pop(args[0], None)
            self._changed()
        elif signal_name == "PropertiesChanged":
            ifname, changed, invalid = args
            props = self.objs.setdefault(path, {}).setdefault(ifname, {})
            props.update(changed)
            for key in invalid:
                props.pop(key, None)
            if (self.open_mode and ifname == IF_DEVICE and changed.get("Paired") is True
                    and not props.get("Trusted")):
                self._set_prop(path, IF_DEVICE, "Trusted", GLib.Variant("b", True))
            if invalid or not set(changed) <= NOISY:
                self._changed()

    def _changed(self):
        if not self._timer:                    # coalesce bursts into one redraw
            self._timer = GLib.timeout_add(40, self._fire)

    def _fire(self):
        self._timer = 0
        self.on_change()
        return GLib.SOURCE_REMOVE

    def _set_busy(self, path, text):
        if text:
            self.busy[path] = text
        else:
            self.busy.pop(path, None)
        self._changed()

    @property
    def adapter(self):
        found = None
        for path, ifs in self.objs.items():
            if IF_ADAPTER in ifs:
                if path.endswith("/hci0"):
                    return path
                found = found or path
        return found

    def adapter_prop(self, key, default=None):
        return self.objs.get(self.adapter, {}).get(IF_ADAPTER, {}).get(key, default)

    @property
    def powered(self):
        return bool(self.adapter_prop("Powered", False))

    @property
    def discovering(self):
        return bool(self.adapter_prop("Discovering", False))

    def name_of(self, path):
        d = self.objs.get(path, {}).get(IF_DEVICE, {})
        return d.get("Alias") or d.get("Name") or d.get("Address") or "device"

    def devices(self):
        adapter, out = self.adapter, []
        for path, ifs in self.objs.items():
            d = ifs.get(IF_DEVICE)
            if not d or (adapter and d.get("Adapter") not in (None, adapter)):
                continue
            addr = d.get("Address", "")
            name = d.get("Alias") or d.get("Name") or addr
            connected = bool(d.get("Connected"))
            paired = bool(d.get("Paired")) or bool(d.get("Bonded"))
            state = "connected" if connected else "paired" if paired else "new"
            if state == "new" and name.replace("-", ":").upper() == addr.upper():
                continue                       # nameless BLE beacon: pure noise
            out.append(dict(path=path, name=name, address=addr, icon=d.get("Icon", ""),
                            state=state, trusted=bool(d.get("Trusted")),
                            battery=ifs.get(IF_BATTERY, {}).get("Percentage"),
                            busy=self.busy.get(path)))
        rank = {"connected": 0, "paired": 1, "new": 2}
        out.sort(key=lambda x: (rank[x["state"]], x["name"].lower()))
        return out

    # ---- calls ----
    def _call(self, path, iface, method, params, cb=None, timeout=30000):
        def done(src, res, *_rest):
            try:
                ret = src.call_finish(res)
                ok, val = True, ret
            except GLib.Error as e:
                ok, val = False, clean_err(e)
            if cb:
                cb(ok, val)
        self.bus.call(BLUEZ, path, iface, method, params, None,
                      Gio.DBusCallFlags.NONE, timeout, None, done)

    def _set_prop(self, path, iface, prop, variant, cb=None):
        self._call(path, IF_PROPS, "Set", GLib.Variant("(ssv)", (iface, prop, variant)), cb)

    def set_powered(self, on, cb):
        def attempt(retry):
            def after(ok, val):
                if not ok and on and retry and re.search(r"rfkill|[Bb]locked", str(val)):
                    try:
                        subprocess.run(["rfkill", "unblock", "bluetooth"], timeout=3,
                                       capture_output=True)
                    except (OSError, subprocess.SubprocessError):
                        pass
                    attempt(False)
                else:
                    cb(ok, val)
            self._set_prop(self.adapter, IF_ADAPTER, "Powered", GLib.Variant("b", on), after)
        attempt(True)

    def start_scan(self, cb=None):
        self._scan_ours = True
        self._call(self.adapter, IF_ADAPTER, "StartDiscovery", None, cb)

    def stop_scan(self, cb=None):
        self._scan_ours = False
        self._call(self.adapter, IF_ADAPTER, "StopDiscovery", None, cb)

    def _set_seq(self, steps, cb):
        if not steps:
            if cb:
                cb(True, "")
            return
        (prop, var), rest = steps[0], steps[1:]

        def after(ok, val):
            if not ok:
                if cb:
                    cb(False, val)
            else:
                self._set_seq(rest, cb)
        self._set_prop(self.adapter, IF_ADAPTER, prop, var, after)

    def set_open(self, on, cb=None):
        """Discoverable + pairable + accept every pairing request. BlueZ also reverts the
        discoverable part by itself after OPEN_MINUTES, even if this program dies."""
        if not self.adapter:
            if cb:
                cb(False, "no adapter")
            return
        U, B = (lambda v: GLib.Variant("u", v)), (lambda v: GLib.Variant("b", v))
        if on:
            self.ensure_agent()
            self._open_saved = {k: self.adapter_prop(k) for k in
                                ("Discoverable", "DiscoverableTimeout", "Pairable", "PairableTimeout")}
            self.open_mode = True
            steps = [("DiscoverableTimeout", U(OPEN_MINUTES * 60)), ("PairableTimeout", U(0)),
                     ("Pairable", B(True)), ("Discoverable", B(True))]
        else:
            saved = self._open_saved or {}
            self.open_mode = False
            steps = [("Discoverable", B(False)),
                     ("DiscoverableTimeout", U(saved.get("DiscoverableTimeout") or 180)),
                     ("PairableTimeout", U(saved.get("PairableTimeout") or 0)),
                     ("Pairable", B(saved.get("Pairable", True)))]
        self._changed()
        self._set_seq(steps, cb)

    def connect(self, path, cb):
        self._set_busy(path, "Connecting…")

        def done(ok, val):
            self._set_busy(path, None)
            if ok:
                self._set_prop(path, IF_DEVICE, "Trusted", GLib.Variant("b", True))
            cb(ok, val if not ok else "")
        self._call(path, IF_DEVICE, "Connect", None, done)

    def disconnect(self, path, cb):
        self._set_busy(path, "Disconnecting…")

        def done(ok, val):
            self._set_busy(path, None)
            cb(ok, val if not ok else "")
        self._call(path, IF_DEVICE, "Disconnect", None, done)

    def pair(self, path, cb):
        """pair -> trust -> connect.  cb(ok, msg): ok=False failed; ok=True with msg = paired only."""
        self.ensure_agent()
        self._set_busy(path, "Pairing…")

        def paired(ok, val):
            if not ok and "Already" in str(val):
                ok = True
            if not ok:
                self._set_busy(path, None)
                cb(False, val)
                return
            self._set_prop(path, IF_DEVICE, "Trusted", GLib.Variant("b", True))
            self._set_busy(path, "Connecting…")

            def connected(ok2, val2):
                self._set_busy(path, None)
                cb(True, "" if ok2 else val2)
            self._call(path, IF_DEVICE, "Connect", None, connected)
        self._call(path, IF_DEVICE, "Pair", None, paired, timeout=60000)

    def forget(self, path, cb):
        self._call(self.adapter, IF_ADAPTER, "RemoveDevice", GLib.Variant("(o)", (path,)),
                   lambda ok, val: cb(ok, "" if ok else val))

    # ---- pairing agent (answers PIN / passkey / confirmation questions through the UI) ----
    def ensure_agent(self):
        if self._agent_reg is not None:
            return
        node = Gio.DBusNodeInfo.new_for_xml(AGENT_XML)
        try:
            self._agent_reg = self.bus.register_object(AGENT_PATH, node.interfaces[0],
                                                       self._agent_call, None, None)
        except GLib.Error as e:
            print("agent registration failed:", e, file=sys.stderr)
            return

        def make_default(_ok, _val):
            self._call("/org/bluez", IF_AGENT_MGR, "RequestDefaultAgent",
                       GLib.Variant("(o)", (AGENT_PATH,)))
        self._call("/org/bluez", IF_AGENT_MGR, "RegisterAgent",
                   GLib.Variant("(os)", (AGENT_PATH, "KeyboardDisplay")), make_default)

    def _agent_call(self, _conn, _sender, _path, _iface, method, params, invocation, *_rest):
        def ok(value=None):
            invocation.return_value(value)

        def reject(*_a):
            invocation.return_dbus_error("org.bluez.Error.Rejected", "Rejected")
        try:
            args = params.unpack()
            handler = self.agent_handler
            if method == "Release":
                ok()
            elif method == "Cancel":
                if handler:
                    handler("cancel", None, None, None, None)
                ok()
            elif method == "AuthorizeService":
                d = self.objs.get(args[0], {}).get(IF_DEVICE, {})
                ok() if (self.open_mode or d.get("Paired") or d.get("Trusted")) else reject()
            elif self.open_mode and method in ("RequestConfirmation", "RequestAuthorization"):
                if handler:                       # lets the UI say what it just accepted
                    handler("auto", args[0], args[1] if method == "RequestConfirmation" else None, None, None)
                ok()
            elif not handler:
                reject()
            elif method == "RequestPinCode":
                handler("pin", args[0], None, lambda v: ok(GLib.Variant("(s)", (str(v),))), reject)
            elif method == "RequestPasskey":
                handler("passkey", args[0], None, lambda v: ok(GLib.Variant("(u)", (int(v),))), reject)
            elif method == "DisplayPinCode":
                handler("display_pin", args[0], args[1], None, None)
                ok()
            elif method == "DisplayPasskey":
                handler("display_passkey", args[0], args[1], None, None)
                ok()
            elif method == "RequestConfirmation":
                handler("confirm", args[0], args[1], lambda *_a: ok(), reject)
            elif method == "RequestAuthorization":
                handler("authorize", args[0], None, lambda *_a: ok(), reject)
            else:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
        except Exception as e:                  # never leave BlueZ hanging on a bug of ours
            print("agent error:", repr(e), file=sys.stderr)
            reject()

    def shutdown(self):
        """Stop our scan, close open mode, drop the agent; best effort, never raises."""
        if self.open_mode and self.adapter:
            saved = self._open_saved or {}
            for prop, var in (("Discoverable", GLib.Variant("b", False)),
                              ("DiscoverableTimeout", GLib.Variant("u", saved.get("DiscoverableTimeout") or 180))):
                try:
                    self.bus.call_sync(BLUEZ, self.adapter, IF_PROPS, "Set",
                                       GLib.Variant("(ssv)", (IF_ADAPTER, prop, var)), None,
                                       Gio.DBusCallFlags.NONE, 1500, None)
                except GLib.Error:
                    pass
            self.open_mode = False
        try:
            if self._scan_ours and self.adapter:
                self.bus.call_sync(BLUEZ, self.adapter, IF_ADAPTER, "StopDiscovery", None, None,
                                   Gio.DBusCallFlags.NONE, 1500, None)
        except GLib.Error:
            pass
        try:
            if self._agent_reg is not None:
                self.bus.call_sync(BLUEZ, "/org/bluez", IF_AGENT_MGR, "UnregisterAgent",
                                   GLib.Variant("(o)", (AGENT_PATH,)), None,
                                   Gio.DBusCallFlags.NONE, 1500, None)
                self.bus.unregister_object(self._agent_reg)
        except GLib.Error:
            pass


# ========================================= the popup (GTK) =========================================

CSS = string.Template("""
* { font-family: "$font", monospace; font-size: 14px; }
window.bt-window { background-color: transparent; }
window.bt-backdrop { background-color: rgba(0, 0, 0, 0.01); }
.bt-panel { background-color: $bg; color: $fg; border: 1px solid rgba(224, 224, 224, 0.12);
            border-radius: 16px; padding: 8px; }
.bt-title { color: $magenta; font-weight: bold; padding: 4px 8px; }
button { background-image: none; background-color: transparent; border: none; box-shadow: none;
         text-shadow: none; outline-style: none; color: $fg; padding: 6px 8px; border-radius: 10px; }
button:hover { background-color: rgba(255, 255, 255, 0.10); }
button:disabled { opacity: 0.6; }
.bt-power { font-size: 16px; padding: 4px 10px; }
.bt-power.on { color: $green; }
.bt-power.off { color: $off; }
.bt-icon { min-width: 26px; font-size: 18px; color: $magenta; }
.state-connected .bt-icon { color: $green; }
.state-new .bt-icon { color: $blue; }
.bt-sub { color: $dim; font-size: 12px; }
.state-connected .bt-sub { color: $green; }
.bt-action { color: $dim; font-size: 12px; }
.bt-section { color: $dim; font-size: 12px; padding: 10px 10px 2px 10px; }
.bt-forget { color: $off; padding: 6px 10px; }
.bt-forget:hover { color: $red; }
.bt-empty { color: $dim; padding: 22px 12px; }
.bt-status { color: $dim; font-size: 12px; padding: 4px 10px 0 10px; }
.bt-status.error { color: $red; }
.bt-status.warn { color: $yellow; }
.bt-open { color: $dim; }
.bt-open.active { color: $red; background-color: rgba(255, 154, 158, 0.12); }
.bt-scan { color: $blue; }
.bt-scan.scanning { color: $yellow; }
.bt-prompt { background-color: $bg_light; border-radius: 12px; padding: 10px; margin: 6px 2px; }
.bt-code { color: $yellow; font-size: 24px; font-weight: bold; padding: 4px 0; }
.bt-prompt-text { color: $fg; }
entry { background-color: $bg; color: $fg; border: 1px solid rgba(224, 224, 224, 0.15);
        border-radius: 8px; padding: 4px 8px; min-height: 0; }
.bt-ok { color: $green; }
.bt-cancel { color: $dim; }
viewport, scrolledwindow { background: none; border: none; }
""")


class Popup:
    def __init__(self):
        self.status_token = 0
        self.scan_timer = 0
        self.quitting = False
        self.visible = False
        self.open_deadline = 0
        self.prompt_kind = self.prompt_dev = None
        self._load_css()
        self._build_window()
        self.client = Bluez(self.refresh)
        self.client.agent_handler = self.on_agent
        self.refresh()

    # ---- window ----
    def _load_css(self):
        css = Gtk.CssProvider()
        try:
            css.load_from_data(CSS.substitute(font=FONT, **COLORS).encode())
        except GLib.Error as e:
            print("CSS error:", e, file=sys.stderr)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _build_window(self):
        self.layer = bool(GtkLayerShell) and hasattr(GtkLayerShell, "is_supported") \
            and GtkLayerShell.is_supported()

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title("Bluetooth")
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.get_style_context().add_class("bt-window")
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.connect("key-press-event", self.on_key)
        self.win.connect("destroy", self.quit)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.get_style_context().add_class("bt-panel")
        panel.set_size_request(WIDTH, -1)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title = Gtk.Label(label=G_BT + "  Bluetooth")
        title.set_xalign(0.0)
        title.get_style_context().add_class("bt-title")
        self.power_btn = Gtk.Button(label=G_POWER)
        self.power_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.power_btn.set_can_focus(False)
        self.power_btn.set_tooltip_text("Turn Bluetooth on / off")
        self.power_btn.get_style_context().add_class("bt-power")
        self.power_btn.connect("clicked", self.on_power)
        header.pack_start(title, True, True, 0)
        header.pack_end(self.power_btn, False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(380)
        self.scroll.add(self.body)

        self.prompt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.prompt.get_style_context().add_class("bt-prompt")
        self.prompt.set_no_show_all(True)

        self.status = Gtk.Label(label="")
        self.status.set_xalign(0.0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(40)
        self.status.get_style_context().add_class("bt-status")
        self.status.set_no_show_all(True)

        self.open_btn = Gtk.Button(label=G_OPEN + "  Let any device connect")
        self.open_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.open_btn.set_can_focus(False)
        self.open_btn.set_tooltip_text("Make this computer discoverable and accept every pairing request")
        self.open_btn.get_style_context().add_class("bt-open")
        self.open_btn.connect("clicked", self.on_open)

        self.scan_btn = Gtk.Button(label=G_SCAN + "  Scan for devices")
        self.scan_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.scan_btn.set_can_focus(False)
        self.scan_btn.get_style_context().add_class("bt-scan")
        self.scan_btn.connect("clicked", self.on_scan)

        panel.pack_start(header, False, False, 0)
        panel.pack_start(self.scroll, False, False, 0)
        panel.pack_start(self.prompt, False, False, 0)
        panel.pack_start(self.status, False, False, 0)
        panel.pack_start(self.scan_btn, False, False, 2)
        panel.pack_start(self.open_btn, False, False, 0)
        self.win.add(panel)

        self.backdrop = None
        if self.layer:
            self._setup_layer(self.win, GtkLayerShell.Layer.OVERLAY,
                              (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.RIGHT),
                              ((GtkLayerShell.Edge.BOTTOM, MARGIN_BOTTOM),
                               (GtkLayerShell.Edge.RIGHT, MARGIN_RIGHT)),
                              keyboard="EXCLUSIVE")
            # invisible full-screen sheet underneath: a click anywhere outside closes the popup
            self.backdrop = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.backdrop.set_decorated(False)
            self.backdrop.get_style_context().add_class("bt-backdrop")
            bvisual = self.backdrop.get_screen().get_rgba_visual()
            if bvisual:
                self.backdrop.set_visual(bvisual)
            self.backdrop.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.backdrop.connect("button-press-event", lambda *_a: self.close_window())
            self._setup_layer(self.backdrop, GtkLayerShell.Layer.TOP,
                              (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                               GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT),
                              (), zone=-1, keyboard="NONE")

    @staticmethod
    def _setup_layer(win, layer, anchors, margins, zone=None, keyboard=None):
        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_namespace(win, "bluetooth-menu")
        GtkLayerShell.set_layer(win, layer)
        for edge in anchors:
            GtkLayerShell.set_anchor(win, edge, True)
        for edge, margin in margins:
            GtkLayerShell.set_margin(win, edge, margin)
        if zone is not None:
            GtkLayerShell.set_exclusive_zone(win, zone)
        if keyboard and hasattr(GtkLayerShell, "set_keyboard_mode"):
            GtkLayerShell.set_keyboard_mode(win, getattr(GtkLayerShell.KeyboardMode, keyboard))
        elif keyboard:
            GtkLayerShell.set_keyboard_interactivity(win, keyboard != "NONE")

    def show(self):
        if self.backdrop:
            self.backdrop.show_all()
        self.win.show_all()
        self.win.present()
        if not self.layer:                      # plain X11 / no layer-shell: park bottom-right
            display = Gdk.Display.get_default()
            mon = display.get_primary_monitor() or display.get_monitor(0)
            geo = mon.get_geometry()
            w, h = self.win.get_size()
            self.win.move(geo.x + geo.width - w - MARGIN_RIGHT, geo.y + geo.height - h - 48)
        self.visible = True

    def close_window(self, *_a):
        """Esc / click outside / click on the module: leave, but stay alive while open mode is on
        (somebody may be pairing right now and the agent has to keep answering)."""
        if self.client.open_mode:
            self.hide_window()
        else:
            self.quit()

    def hide_window(self):
        if self.client.discovering:
            self._stop_scan()
        if self.backdrop:
            self.backdrop.hide()
        self.win.hide()
        self.visible = False

    def toggle(self):
        if self.visible:
            self.close_window()
        else:
            self.show()
        return GLib.SOURCE_CONTINUE

    def quit(self, *_a):
        if self.quitting:
            return GLib.SOURCE_REMOVE
        self.quitting = True
        try:
            self.client.shutdown()
        except Exception as e:
            print("shutdown error:", repr(e), file=sys.stderr)
        Gtk.main_quit()
        return GLib.SOURCE_REMOVE

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.close_window()
            return True
        return False

    # ---- drawing ----
    def refresh(self):
        c = self.client
        if (self.prompt_kind in ("display_pin", "display_passkey")
                and c.objs.get(self.prompt_dev, {}).get(IF_DEVICE, {}).get("Paired")):
            self._clear_prompt()               # the code was typed, pairing is done
        for child in self.body.get_children():
            self.body.remove(child)
            child.destroy()

        if c.service_error:
            self._empty("Bluetooth isn't available\n%s\n\nTry: sudo systemctl enable --now bluetooth"
                        % c.service_error)
        elif not c.adapter:
            self._empty("No Bluetooth adapter found")
        elif not c.powered:
            self._empty("Bluetooth is off\nUse the %s button to turn it on" % G_POWER)
        else:
            devs = c.devices()
            known = [d for d in devs if d["state"] != "new"]
            new = [d for d in devs if d["state"] == "new"]
            for d in known:
                self.body.pack_start(self._row(d), False, False, 0)
            if new:
                section = Gtk.Label(label="Available")
                section.set_xalign(0.0)
                section.get_style_context().add_class("bt-section")
                self.body.pack_start(section, False, False, 0)
                for d in new:
                    self.body.pack_start(self._row(d), False, False, 0)
            if not devs:
                self._empty("No devices yet\nPut yours in pairing mode and press Scan")
        self.body.show_all()

        ctx = self.power_btn.get_style_context()
        ctx.remove_class("on")
        ctx.remove_class("off")
        ctx.add_class("on" if c.powered else "off")

        usable = bool(c.adapter) and c.powered and not c.service_error
        self.scan_btn.set_sensitive(usable)
        sctx = self.scan_btn.get_style_context()
        if c.discovering and usable:
            sctx.add_class("scanning")
            self.scan_btn.set_label(G_SCAN + "  Scanning…  (click to stop)")
        else:
            sctx.remove_class("scanning")
            self.scan_btn.set_label(G_SCAN + "  Scan for devices")

        octx = self.open_btn.get_style_context()
        self.open_btn.set_sensitive(usable)
        if c.open_mode:
            octx.add_class("active")
            self.open_btn.set_label(G_OPEN + "  Open to ALL devices%s  (click to stop)" % self._left())
        else:
            octx.remove_class("active")
            self.open_btn.set_label(G_OPEN + "  Let any device connect")

    def _empty(self, text):
        label = Gtk.Label(label=text)
        label.set_justify(Gtk.Justification.CENTER)
        label.get_style_context().add_class("bt-empty")
        self.body.pack_start(label, False, False, 0)

    def _row(self, d):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        btn = Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_can_focus(False)
        btn.get_style_context().add_class("state-" + d["state"])

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Label(label=ICONS.get(d["icon"], G_BT))
        icon.get_style_context().add_class("bt-icon")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(label=d["name"])
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(22)
        sub_text = d["busy"] or {"connected": "Connected", "paired": "Paired", "new": "Not paired"}[d["state"]]
        if d["battery"] is not None and not d["busy"] and d["state"] == "connected":
            sub_text += "  %s %d%%" % (G_BATT, d["battery"])
        sub = Gtk.Label(label=sub_text)
        sub.set_xalign(0.0)
        sub.get_style_context().add_class("bt-sub")
        text.pack_start(name, False, False, 0)
        text.pack_start(sub, False, False, 0)
        action = Gtk.Label(label="" if d["busy"] else
                           {"connected": "Disconnect", "paired": "Connect", "new": "Pair"}[d["state"]])
        action.get_style_context().add_class("bt-action")
        inner.pack_start(icon, False, False, 0)
        inner.pack_start(text, True, True, 0)
        inner.pack_end(action, False, False, 0)
        btn.add(inner)
        btn.set_sensitive(not d["busy"])
        btn.connect("clicked", self.on_row, d["path"], d["state"])
        row.pack_start(btn, True, True, 0)

        if d["state"] != "new":
            forget = Gtk.Button(label=G_FORGET)
            forget.set_relief(Gtk.ReliefStyle.NONE)
            forget.set_can_focus(False)
            forget.set_tooltip_text("Forget this device")
            forget.get_style_context().add_class("bt-forget")
            forget.connect("clicked", self.on_forget, d["path"])
            row.pack_end(forget, False, False, 0)
        return row

    def set_status(self, text, error=False, warn=False):
        self.status_token += 1
        token = self.status_token
        ctx = self.status.get_style_context()
        ctx.remove_class("error")
        ctx.remove_class("warn")
        if error:
            ctx.add_class("error")
        if warn:
            ctx.add_class("warn")
        self.status.set_text(text)
        self.status.show()

        def clear():
            if token == self.status_token:
                self.status.hide()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(8, clear)

    # ---- actions ----
    def on_power(self, _b):
        want = not self.client.powered
        self.set_status("Turning Bluetooth %s…" % ("on" if want else "off"))
        self.client.set_powered(want, lambda ok, msg: None if ok else
                                self.set_status("Couldn't change power: %s" % msg, True))

    def _left(self):
        if not OPEN_MINUTES or not self.open_deadline:
            return ""
        left = max(0, int(self.open_deadline - time.monotonic()))
        return "  %d:%02d left" % (left // 60, left % 60)

    def on_open(self, _b):
        want = not self.client.open_mode

        def done(ok, msg):
            if not ok:
                self.set_status("Couldn't change open mode: %s" % msg, error=True)
        self.client.set_open(want, done)
        if want:
            self.open_deadline = time.monotonic() + OPEN_MINUTES * 60 if OPEN_MINUTES else 0
            GLib.timeout_add_seconds(1, self._tick)
            self.set_status("Anyone in range can pair with this computer. Turn it off when done.",
                            warn=True)
        else:
            self.open_deadline = 0
            self.set_status("Open mode off")
        self.refresh()

    def _tick(self):
        c = self.client
        if not c.open_mode or self.quitting:
            return GLib.SOURCE_REMOVE
        if OPEN_MINUTES and time.monotonic() >= self.open_deadline:
            c.set_open(False)
            self.open_deadline = 0
            self.set_status("Open mode switched off (time limit)")
            self.refresh()
            return GLib.SOURCE_REMOVE
        if self.visible:
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def on_scan(self, _b):
        if self.client.discovering:
            self._stop_scan()
        else:
            self.client.start_scan(lambda ok, msg: None if ok else
                                   self.set_status("Couldn't scan: %s" % msg, True))
            if self.scan_timer:
                GLib.source_remove(self.scan_timer)
            self.scan_timer = GLib.timeout_add_seconds(SCAN_SECONDS, self._stop_scan)

    def _stop_scan(self):
        self.scan_timer = 0
        if self.client.discovering:
            self.client.stop_scan()
        return GLib.SOURCE_REMOVE

    def on_row(self, _b, path, state):
        name = self.client.name_of(path)
        if state == "connected":
            self.client.disconnect(path, lambda ok, msg: self.set_status(
                "Disconnected from %s" % name if ok else "Couldn't disconnect: %s" % msg, not ok))
        elif state == "paired":
            self.client.connect(path, lambda ok, msg: self.set_status(
                "Connected to %s" % name if ok else "Couldn't connect to %s: %s" % (name, msg), not ok))
        else:
            self.client.pair(path, lambda ok, msg: self._paired(name, ok, msg))

    def _paired(self, name, ok, msg):
        self._clear_prompt()
        if not ok:
            self.set_status("Couldn't pair with %s: %s" % (name, msg), True)
        elif msg:
            self.set_status("Paired with %s, but it didn't connect: %s" % (name, msg), True)
        else:
            self.set_status("Paired and connected to %s" % name)

    def on_forget(self, _b, path):
        name = self.client.name_of(path)
        self.client.forget(path, lambda ok, msg: self.set_status(
            "Removed %s" % name if ok else "Couldn't remove %s: %s" % (name, msg), not ok))

    # ---- pairing questions from BlueZ ----
    def _clear_prompt(self):
        for child in self.prompt.get_children():
            self.prompt.remove(child)
            child.destroy()
        self.prompt.hide()
        self.prompt_kind = self.prompt_dev = None

    def on_agent(self, kind, dev, arg, respond, reject):
        self._clear_prompt()
        if kind == "cancel":
            return
        if not self.visible and not self.quitting:
            self.show()                         # a device is pairing while the popup was tucked away
        name = self.client.name_of(dev)
        if kind == "auto":
            self.set_status("Accepted %s%s" % (name, "  (code %06d)" % arg if arg else ""))
            return
        self.prompt_kind, self.prompt_dev = kind, dev

        def label(text, css="bt-prompt-text"):
            lab = Gtk.Label(label=text)
            lab.set_line_wrap(True)
            lab.set_max_width_chars(36)
            lab.get_style_context().add_class(css)
            self.prompt.pack_start(lab, False, False, 0)

        def buttons(ok_text, on_ok):
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            cancel = Gtk.Button(label="Cancel")
            cancel.get_style_context().add_class("bt-cancel")
            cancel.connect("clicked", lambda *_a: (self._clear_prompt(), reject and reject()))
            ok = Gtk.Button(label=ok_text)
            ok.get_style_context().add_class("bt-ok")
            ok.connect("clicked", lambda *_a: on_ok())
            box.pack_end(ok, False, False, 0)
            box.pack_end(cancel, False, False, 0)
            self.prompt.pack_start(box, False, False, 0)

        if kind == "confirm":
            label("Does %s show this code?" % name)
            label("%06d" % arg, "bt-code")
            buttons("Yes, pair", lambda: (self._clear_prompt(), respond()))
        elif kind == "authorize":
            label("%s wants to pair with this computer." % name)
            buttons("Allow", lambda: (self._clear_prompt(), respond()))
        elif kind in ("pin", "passkey"):
            label("Enter the %s for %s:" % ("PIN" if kind == "pin" else "passkey", name))
            entry = Gtk.Entry()
            if kind == "passkey":
                entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            self.prompt.pack_start(entry, False, False, 0)

            def submit(*_a):
                value = entry.get_text().strip()
                if value:
                    self._clear_prompt()
                    respond(value)
            entry.connect("activate", submit)
            buttons("Pair", submit)
            self.prompt.show_all()
            entry.grab_focus()
            return
        elif kind in ("display_pin", "display_passkey"):
            code = arg if kind == "display_pin" else "%06d" % arg
            label("Type this on %s, then press Enter:" % name)
            label(code, "bt-code")
        self.prompt.show_all()


# =============================================== main ===============================================

def fatal(text):
    print("bluetooth-menu:", text, file=sys.stderr)
    try:
        subprocess.run(["notify-send", "-a", "Bluetooth", "-u", "critical", "Bluetooth menu", text],
                       timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass
    sys.exit(1)


def main():
    if Gtk is None:
        fatal("GTK 3 for Python is missing (install python-gobject and gtk3)")
    if Gdk.Display.get_default() is None:
        fatal("no display available")
    if os.environ.get("WAYLAND_DISPLAY") and GtkLayerShell is None:
        print("warning: gtk-layer-shell not found; the popup will be an ordinary window. "
              "Install it (pacman -S gtk-layer-shell).", file=sys.stderr)
    popup = Popup()
    for sig in (signal.SIGTERM, signal.SIGINT):
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, popup.quit)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR1, popup.toggle)
    popup.show()
    Gtk.main()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        fatal("crashed, see %s" % LOGFILE)