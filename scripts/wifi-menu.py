#!/usr/bin/env python3
"""Wi-Fi popup for Waybar: a GTK3 window that talks to NetworkManager directly over D-Bus.

  click the module  -> popup opens (networks appear instantly, live-updating, rescans on open)
  click it again    -> popup closes   (also: Esc, or click anywhere outside it)

Needs: python-gobject, gtk3, gtk-layer-shell (Arch: pacman -S python-gobject gtk3 gtk-layer-shell)
If something goes wrong, the reason is in ~/.cache/wifi-menu.log
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
MARGIN_RIGHT = 140     # distance from the right screen edge (raise it to slide the popup left)
FONT = "JetBrainsMono Nerd Font Propo"
COLORS = dict(         # same palette as your Waybar style.css
    bg="#212121", bg_light="#3a3a3a", fg="#e0e0e0", dim="#8a8a8a", off="#5a5a5a",
    red="#ff9a9e", green="#b5e8a9", yellow="#ffe6a7", blue="#63a4ff", magenta="#dda0dd",
)
# --------------------------------------------------------------------

RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
PIDFILE = os.path.join(RUNTIME, "wifi-menu.pid")
LOGFILE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                       "wifi-menu.log")


# ---- start-up work that must happen BEFORE the heavy GTK import (keeps the toggle instant) ----

def _is_ours(pid):
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return b"wifi-menu" in f.read()
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
            os.kill(old, signal.SIGUSR1)     # "toggle": the running popup closes itself
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

NM = "org.freedesktop.NetworkManager"
NM_PATH = "/org/freedesktop/NetworkManager"
SETTINGS_PATH = NM_PATH + "/Settings"
IF_DEV = NM + ".Device"
IF_WIFI = NM + ".Device.Wireless"
IF_AP = NM + ".AccessPoint"
IF_ACTIVE = NM + ".Connection.Active"
IF_SETTINGS = NM + ".Settings"
IF_CONN = NM + ".Settings.Connection"
IF_PROPS = "org.freedesktop.DBus.Properties"

# NetworkManager enums we care about
DEV_WIFI = 2
ST_ACTIVATED, ST_CONNECTING = 100, range(40, 91)
AP_PRIVACY = 0x1
KM_PSK, KM_8021X, KM_SAE, KM_OWE = 0x100, 0x200, 0x400, 0x800
FAIL_REASONS = {
    2: "disconnected", 5: "couldn't get an IP address", 6: "connection timed out",
    7: "connection timed out", 9: "wrong password?", 10: "wrong password?", 11: "profile was removed",
}

# Nerd Font glyphs (Material Design set), written as codepoints so no locale can mangle them
G_WIFI, G_WIFI_OFF = "\U000F05A9", "\U000F05AA"
G_POWER, G_REFRESH, G_FORGET, G_LOCK = "\U000F0425", "\U000F0450", "\U000F01B4", "\U000F033E"
G_SIGNAL = ("\U000F092F", "\U000F091F", "\U000F0922", "\U000F0925", "\U000F0928")   # none, 1, 2, 3, 4 bars

# property changes that fire constantly and never change what we draw
DEV_DRAW_KEYS = {"State", "ActiveAccessPoint", "ActiveConnection"}


def clean_err(err):
    msg = getattr(err, "message", None) or str(err)
    if "ServiceUnknown" in msg or "NameHasNoOwner" in msg:
        return "the NetworkManager service isn't running"
    msg = re.sub(r"^GDBus\.Error:[\w.]+:\s*", "", msg).strip()
    return msg or "unknown error"


def bars(strength):
    return 4 if strength >= 75 else 3 if strength >= 50 else 2 if strength >= 25 else 1


def ap_ssid(ap):
    return bytes(ap.get("Ssid", b"")).decode("utf-8", "replace")


def security_of(ap):
    flags, wpa, rsn = ap.get("Flags", 0), ap.get("WpaFlags", 0), ap.get("RsnFlags", 0)
    if rsn & KM_PSK:
        return "WPA2/WPA3" if rsn & KM_SAE else "WPA2"
    if rsn & KM_SAE:
        return "WPA3"
    if (rsn | wpa) & KM_8021X:
        return "Enterprise"
    if rsn & KM_OWE:
        return "OWE"
    if wpa & KM_PSK:
        return "WPA"
    if flags & AP_PRIVACY:
        return "WEP"
    return "Open"


# ================================ NetworkManager client (no GTK) ================================

class NetMan:
    """Keeps a live copy of the Wi-Fi device, its access points and saved profiles."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.mgr = {}                  # NetworkManager's own properties
        self.dev = None                # object path of the Wi-Fi device
        self.dev_props = {}
        self.aps = {}                  # access point path -> properties
        self.saved = {}                # ssid -> [profile paths]
        self.busy = {}                 # ssid -> "Connecting…"
        self.service_error = None
        self._timer = 0
        kind = Gio.BusType.SESSION if os.environ.get("WIFI_MENU_BUS") == "session" else Gio.BusType.SYSTEM
        self.bus = Gio.bus_get_sync(kind, None)
        self.reload()
        for iface, member in ((IF_WIFI, "AccessPointAdded"), (IF_WIFI, "AccessPointRemoved"),
                              (IF_PROPS, "PropertiesChanged"),
                              (IF_SETTINGS, "NewConnection"), (IF_SETTINGS, "ConnectionRemoved")):
            self.bus.signal_subscribe(NM, iface, member, None, None,
                                      Gio.DBusSignalFlags.NONE, self._on_signal)

    # ---- state ----
    def _sync(self, path, iface, method, params, reply):
        return self.bus.call_sync(NM, path, iface, method, params, GLib.VariantType(reply),
                                  Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]

    def _get_all(self, path, iface):
        return self._sync(path, IF_PROPS, "GetAll", GLib.Variant("(s)", (iface,)), "(a{sv})")

    def reload(self):
        try:
            self.mgr = self._get_all(NM_PATH, NM)
            self.dev, self.dev_props, self.aps = None, {}, {}
            for path in self._sync(NM_PATH, NM, "GetDevices", None, "(ao)"):
                props = self._get_all(path, IF_DEV)
                if props.get("DeviceType") == DEV_WIFI:
                    self.dev, self.dev_props = path, props
                    break
            if self.dev:
                self.dev_props.update(self._get_all(self.dev, IF_WIFI))
                for ap in self.dev_props.get("AccessPoints", []):
                    try:
                        self.aps[ap] = self._get_all(ap, IF_AP)
                    except GLib.Error:
                        pass               # it vanished while we were looking
            self.load_saved()
            self.service_error = None
        except GLib.Error as e:
            self.service_error = clean_err(e)

    def load_saved(self):
        saved = {}
        try:
            for path in self._sync(SETTINGS_PATH, IF_SETTINGS, "ListConnections", None, "(ao)"):
                try:
                    st = self._sync(path, IF_CONN, "GetSettings", None, "(a{sa{sv}})")
                except GLib.Error:
                    continue
                wl = st.get("802-11-wireless")
                if st.get("connection", {}).get("type") == "802-11-wireless" and wl and "ssid" in wl:
                    saved.setdefault(bytes(wl["ssid"]).decode("utf-8", "replace"), []).append(path)
        except GLib.Error:
            pass
        self.saved = saved

    def _on_signal(self, _conn, _sender, path, _iface, signal_name, params, *_rest):
        args = params.unpack()
        if signal_name == "AccessPointAdded":
            try:
                self.aps[args[0]] = self._get_all(args[0], IF_AP)
                self._changed()
            except GLib.Error:
                pass
        elif signal_name == "AccessPointRemoved":
            self.aps.pop(args[0], None)
            self._changed()
        elif signal_name in ("NewConnection", "ConnectionRemoved"):
            self.load_saved()
            self._changed()
        elif signal_name == "PropertiesChanged":
            _ifname, changed, _invalid = args
            if path == NM_PATH:
                self.mgr.update(changed)
                self._changed()
            elif path == self.dev:
                self.dev_props.update(changed)
                if set(changed) & DEV_DRAW_KEYS:
                    self._changed()
            elif path in self.aps:
                old = self.aps[path].get("Strength", 0)
                self.aps[path].update(changed)
                if "Strength" not in changed or bars(old) != bars(changed["Strength"]) \
                        or set(changed) - {"Strength", "LastSeen"}:
                    self._changed()

    def _changed(self):
        if not self._timer:                    # coalesce bursts into one redraw
            self._timer = GLib.timeout_add(40, self._fire)

    def _fire(self):
        self._timer = 0
        self.on_change()
        return GLib.SOURCE_REMOVE

    def _set_busy(self, ssid, text):
        if text:
            self.busy[ssid] = text
        else:
            self.busy.pop(ssid, None)
        self._changed()

    @property
    def wifi_on(self):
        return bool(self.mgr.get("WirelessEnabled", False))

    @property
    def hw_blocked(self):
        return self.mgr.get("WirelessHardwareEnabled") is False

    def networks(self):
        active_ap = self.dev_props.get("ActiveAccessPoint")
        state = self.dev_props.get("State", 0)
        active_ssid = ap_ssid(self.aps[active_ap]) if active_ap in self.aps else None
        best = {}
        for path, ap in self.aps.items():
            ssid = ap_ssid(ap)
            if ssid and (ssid not in best or ap.get("Strength", 0) > best[ssid][1].get("Strength", 0)):
                best[ssid] = (path, ap)
        out = []
        for ssid, (path, ap) in best.items():
            is_active = ssid == active_ssid
            out.append(dict(ssid=ssid, ap=path, strength=ap.get("Strength", 0), sec=security_of(ap),
                            saved=ssid in self.saved, active=is_active and state == ST_ACTIVATED,
                            busy=self.busy.get(ssid) or ("Connecting…" if is_active and state in ST_CONNECTING else None)))
        out.sort(key=lambda n: (not n["active"], not n["saved"], -bars(n["strength"]), n["ssid"].lower()))
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
        self.bus.call(NM, path, iface, method, params, None,
                      Gio.DBusCallFlags.NONE, timeout, None, done)

    def set_wifi(self, on, cb):
        self._call(NM_PATH, IF_PROPS, "Set",
                   GLib.Variant("(ssv)", (NM, "WirelessEnabled", GLib.Variant("b", on))), cb)

    def rescan(self, cb=None):
        if self.dev:
            self._call(self.dev, IF_WIFI, "RequestScan", GLib.Variant("(a{sv})", ({},)), cb)

    def _watch(self, active, ssid, created, cb):
        """Wait for the activation to settle; on failure drop a profile we just made."""
        done = {"v": False}

        def finish(ok, msg):
            if done["v"]:
                return
            done["v"] = True
            self.bus.signal_unsubscribe(sub)
            self._set_busy(ssid, None)
            if not ok and created:
                self._call(created, IF_CONN, "Delete", None)   # so a bad password re-prompts next time
            cb(ok, msg)

        def settle(state, reason):
            if state == 2:
                finish(True, "")
            elif state == 4:
                finish(False, FAIL_REASONS.get(reason, "connection failed (code %d)" % reason))

        sub = self.bus.signal_subscribe(NM, IF_ACTIVE, "StateChanged", active, None,
                                        Gio.DBusSignalFlags.NONE,
                                        lambda _c, _s, _p, _i, _sig, params, *_r: settle(*params.unpack()))
        self._call(active, IF_PROPS, "Get", GLib.Variant("(ss)", (IF_ACTIVE, "State")),
                   lambda ok, v: settle(v.unpack()[0], 0) if ok else None)
        GLib.timeout_add_seconds(60, lambda: (finish(False, "connection timed out"), False)[1])

    def connect_saved(self, net, cb):
        self._set_busy(net["ssid"], "Connecting…")

        def done(ok, val):
            if not ok:
                self._set_busy(net["ssid"], None)
                cb(False, val)
            else:
                self._watch(val.unpack()[0], net["ssid"], None, cb)
        self._call(NM_PATH, NM, "ActivateConnection",
                   GLib.Variant("(ooo)", (self.saved[net["ssid"]][0], self.dev, net["ap"])), done)

    def connect_new(self, ssid, ap_path, sec, password, hidden, cb):
        V = GLib.Variant
        wireless = {"ssid": V("ay", ssid.encode()), "mode": V("s", "infrastructure")}
        if hidden:
            wireless["hidden"] = V("b", True)
        settings = {"connection": {"id": V("s", ssid), "type": V("s", "802-11-wireless"),
                                   "autoconnect": V("b", True)},
                    "802-11-wireless": wireless}
        if sec == "WPA3":
            settings["802-11-wireless-security"] = {"key-mgmt": V("s", "sae"), "psk": V("s", password)}
        elif sec == "OWE":
            settings["802-11-wireless-security"] = {"key-mgmt": V("s", "owe")}
        elif password:
            settings["802-11-wireless-security"] = {"key-mgmt": V("s", "wpa-psk"), "psk": V("s", password)}
        self._set_busy(ssid, "Connecting…")

        def done(ok, val):
            if not ok:
                self._set_busy(ssid, None)
                cb(False, val)
            else:
                created, active = val.unpack()
                self._watch(active, ssid, created, cb)
        self._call(NM_PATH, NM, "AddAndActivateConnection",
                   V("(a{sa{sv}}oo)", (settings, self.dev, ap_path or "/")), done)

    def disconnect(self, cb):
        active = self.dev_props.get("ActiveConnection")
        if not active or active == "/":
            cb(False, "not connected")
            return
        self._call(NM_PATH, NM, "DeactivateConnection", GLib.Variant("(o)", (active,)),
                   lambda ok, val: cb(ok, "" if ok else val))

    def forget(self, ssid, cb):
        paths = list(self.saved.get(ssid, []))
        if not paths:
            cb(False, "no saved profile")
            return
        left = {"n": len(paths), "err": ""}

        def one(ok, val):
            if not ok:
                left["err"] = val
            left["n"] -= 1
            if left["n"] == 0:
                cb(not left["err"], left["err"])
        for path in paths:
            self._call(path, IF_CONN, "Delete", None, one)

    def shutdown(self):
        pass


# ========================================= the popup (GTK) =========================================

CSS = string.Template("""
* { font-family: "$font", monospace; font-size: 14px; }
window.wf-window { background-color: transparent; }
window.wf-backdrop { background-color: rgba(0, 0, 0, 0.01); }
.wf-panel { background-color: $bg; color: $fg; border: 1px solid rgba(224, 224, 224, 0.12);
            border-radius: 16px; padding: 8px; }
.wf-title { color: $blue; font-weight: bold; padding: 4px 8px; }
button { background-image: none; background-color: transparent; border: none; box-shadow: none;
         text-shadow: none; outline-style: none; color: $fg; padding: 6px 8px; border-radius: 10px; }
button:hover { background-color: rgba(255, 255, 255, 0.10); }
button:disabled { opacity: 0.6; }
.wf-power { font-size: 16px; padding: 4px 10px; }
.wf-power.on { color: $green; }
.wf-power.off { color: $off; }
.wf-icon { min-width: 26px; font-size: 18px; color: $blue; }
.state-active .wf-icon { color: $green; }
.state-new .wf-icon { color: $dim; }
.wf-sub { color: $dim; font-size: 12px; }
.state-active .wf-sub { color: $green; }
.wf-action { color: $dim; font-size: 12px; }
.wf-forget { color: $off; padding: 6px 10px; }
.wf-forget:hover { color: $red; }
.wf-empty { color: $dim; padding: 22px 12px; }
.wf-status { color: $dim; font-size: 12px; padding: 4px 10px 0 10px; }
.wf-status.error { color: $red; }
.wf-footer { color: $blue; }
.wf-prompt { background-color: $bg_light; border-radius: 12px; padding: 10px; margin: 6px 2px; }
.wf-prompt-text { color: $fg; }
entry { background-color: $bg; color: $fg; border: 1px solid rgba(224, 224, 224, 0.15);
        border-radius: 8px; padding: 4px 8px; min-height: 0; }
.wf-ok { color: $green; }
.wf-cancel { color: $dim; }
viewport, scrolledwindow { background: none; border: none; }
""")


class Popup:
    def __init__(self):
        self.status_token = 0
        self.quitting = False
        self.scanning = False
        self._load_css()
        self._build_window()
        self.client = NetMan(self.refresh)
        self.refresh()
        self.rescan()

    # ---- window ----
    def _load_css(self):
        css = Gtk.CssProvider()
        try:
            css.load_from_data(CSS.substitute(font=FONT, **COLORS).encode())
        except GLib.Error as e:
            print("CSS error:", e, file=sys.stderr)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _flat(self, label=None, css=None):
        btn = Gtk.Button(label=label) if label is not None else Gtk.Button()
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_can_focus(False)
        if css:
            btn.get_style_context().add_class(css)
        return btn

    def _build_window(self):
        self.layer = bool(GtkLayerShell) and hasattr(GtkLayerShell, "is_supported") \
            and GtkLayerShell.is_supported()

        self.win = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.win.set_title("Wi-Fi")
        self.win.set_decorated(False)
        self.win.set_resizable(False)
        self.win.get_style_context().add_class("wf-window")
        visual = self.win.get_screen().get_rgba_visual()
        if visual:
            self.win.set_visual(visual)
        self.win.connect("key-press-event", self.on_key)
        self.win.connect("destroy", self.quit)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        panel.get_style_context().add_class("wf-panel")
        panel.set_size_request(WIDTH, -1)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        title = Gtk.Label(label=G_WIFI + "  Wi-Fi")
        title.set_xalign(0.0)
        title.get_style_context().add_class("wf-title")
        self.power_btn = self._flat(G_POWER, "wf-power")
        self.power_btn.set_tooltip_text("Turn Wi-Fi on / off")
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
        self.prompt.get_style_context().add_class("wf-prompt")
        self.prompt.set_no_show_all(True)

        self.status = Gtk.Label(label="")
        self.status.set_xalign(0.0)
        self.status.set_line_wrap(True)
        self.status.set_max_width_chars(40)
        self.status.get_style_context().add_class("wf-status")
        self.status.set_no_show_all(True)

        self.scan_btn = self._flat(G_REFRESH + "  Rescan networks", "wf-footer")
        self.scan_btn.connect("clicked", lambda _b: self.rescan())
        self.hidden_btn = self._flat(G_WIFI + "  Connect to a hidden network", "wf-footer")
        self.hidden_btn.connect("clicked", self.on_hidden)

        panel.pack_start(header, False, False, 0)
        panel.pack_start(self.scroll, False, False, 0)
        panel.pack_start(self.prompt, False, False, 0)
        panel.pack_start(self.status, False, False, 0)
        panel.pack_start(self.scan_btn, False, False, 2)
        panel.pack_start(self.hidden_btn, False, False, 0)
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
            self.backdrop.get_style_context().add_class("wf-backdrop")
            bvisual = self.backdrop.get_screen().get_rgba_visual()
            if bvisual:
                self.backdrop.set_visual(bvisual)
            self.backdrop.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.backdrop.connect("button-press-event", lambda *_a: self.quit())
            self._setup_layer(self.backdrop, GtkLayerShell.Layer.TOP,
                              (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                               GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT),
                              (), zone=-1, keyboard="NONE")

    @staticmethod
    def _setup_layer(win, layer, anchors, margins, zone=None, keyboard=None):
        GtkLayerShell.init_for_window(win)
        GtkLayerShell.set_namespace(win, "wifi-menu")
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

    def toggle(self):
        self.quit()
        return GLib.SOURCE_REMOVE

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
            if self.prompt.get_visible():
                self._clear_prompt()
            else:
                self.quit()
            return True
        return False

    # ---- drawing ----
    def refresh(self):
        c = self.client
        for child in self.body.get_children():
            self.body.remove(child)
            child.destroy()

        usable = False
        if c.service_error:
            self._empty("Wi-Fi isn't available\n%s\n\nTry: sudo systemctl enable --now NetworkManager"
                        % c.service_error)
        elif not c.dev:
            self._empty("No Wi-Fi adapter found")
        elif c.hw_blocked:
            self._empty("Wi-Fi is blocked by a hardware switch")
        elif not c.wifi_on:
            self._empty("Wi-Fi is off\nUse the %s button to turn it on" % G_POWER)
        else:
            usable = True
            nets = c.networks()
            for n in nets:
                self.body.pack_start(self._row(n), False, False, 0)
            if not nets:
                self._empty("No networks found\nPress Rescan")
        self.body.show_all()

        ctx = self.power_btn.get_style_context()
        ctx.remove_class("on")
        ctx.remove_class("off")
        ctx.add_class("on" if c.wifi_on else "off")
        self.power_btn.set_sensitive(bool(c.dev) and not c.hw_blocked)
        self.scan_btn.set_sensitive(usable)
        self.hidden_btn.set_sensitive(usable)
        self.scan_btn.set_label(G_REFRESH + ("  Scanning…" if self.scanning else "  Rescan networks"))

    def _empty(self, text):
        label = Gtk.Label(label=text)
        label.set_justify(Gtk.Justification.CENTER)
        label.get_style_context().add_class("wf-empty")
        self.body.pack_start(label, False, False, 0)

    def _row(self, n):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        btn = self._flat()
        state = "active" if n["active"] else "saved" if n["saved"] else "new"
        btn.get_style_context().add_class("state-" + state)

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        icon = Gtk.Label(label=G_SIGNAL[bars(n["strength"])])
        icon.get_style_context().add_class("wf-icon")
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        name = Gtk.Label(label=n["ssid"])
        name.set_xalign(0.0)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(22)
        if n["busy"]:
            sub_text = n["busy"]
        else:
            sub_text = "%s%s  %d%%" % ("Connected  " if n["active"] else "Saved  " if n["saved"] else "",
                                       n["sec"] if n["sec"] == "Open" else G_LOCK + " " + n["sec"],
                                       n["strength"])
        sub = Gtk.Label(label=sub_text)
        sub.set_xalign(0.0)
        sub.get_style_context().add_class("wf-sub")
        text.pack_start(name, False, False, 0)
        text.pack_start(sub, False, False, 0)
        action = Gtk.Label(label="" if n["busy"] else "Disconnect" if n["active"] else "Connect")
        action.get_style_context().add_class("wf-action")
        inner.pack_start(icon, False, False, 0)
        inner.pack_start(text, True, True, 0)
        inner.pack_end(action, False, False, 0)
        btn.add(inner)
        btn.set_sensitive(not n["busy"])
        btn.connect("clicked", self.on_row, n["ssid"])
        row.pack_start(btn, True, True, 0)

        if n["saved"]:
            forget = self._flat(G_FORGET, "wf-forget")
            forget.set_tooltip_text("Forget this network")
            forget.connect("clicked", self.on_forget, n["ssid"])
            row.pack_end(forget, False, False, 0)
        return row

    def set_status(self, text, error=False):
        self.status_token += 1
        token = self.status_token
        ctx = self.status.get_style_context()
        ctx.remove_class("error")
        if error:
            ctx.add_class("error")
        self.status.set_text(text)
        self.status.show()

        def clear():
            if token == self.status_token:
                self.status.hide()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(8, clear)

    # ---- actions ----
    def on_power(self, _b):
        want = not self.client.wifi_on
        self.client.set_wifi(want, lambda ok, msg: None if ok else
                             self.set_status("Couldn't change Wi-Fi: %s" % msg, True))

    def rescan(self):
        if self.scanning or not self.client.dev or not self.client.wifi_on:
            return
        self.scanning = True
        self.refresh()
        self.client.rescan()                     # errors ("scanned too recently") are harmless

        def done():
            self.scanning = False
            if not self.quitting:
                self.refresh()
            return GLib.SOURCE_REMOVE
        GLib.timeout_add_seconds(4, done)

    def on_row(self, _b, ssid):
        net = next((n for n in self.client.networks() if n["ssid"] == ssid), None)
        if not net:
            return
        if net["active"]:
            self.client.disconnect(lambda ok, msg: self.set_status(
                "Disconnected from %s" % ssid if ok else "Couldn't disconnect: %s" % msg, not ok))
        elif net["saved"]:
            self.client.connect_saved(net, lambda ok, msg: self._connected(net, ok, msg))
        elif net["sec"] in ("Open", "OWE"):
            self.client.connect_new(ssid, net["ap"], net["sec"], "", False,
                                    lambda ok, msg: self._connected(net, ok, msg))
        elif net["sec"] in ("WEP", "Enterprise"):
            self.set_status("%s networks can't be set up from here; use nm-connection-editor once, "
                            "then they show up as Saved" % net["sec"], True)
        else:
            self.show_form("Password for %s" % ssid, [("Password", True)], "Connect",
                           lambda vals: self._submit_password(net, vals[0]))

    def _submit_password(self, net, password):
        if not (8 <= len(password) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
            self.set_status("A Wi-Fi password is 8 to 63 characters", True)
            return False                         # keep the form open
        self.client.connect_new(net["ssid"], net["ap"], net["sec"], password, False,
                                lambda ok, msg: self._connected(net, ok, msg))
        return True

    def _connected(self, net, ok, msg):
        if ok:
            self.set_status("Connected to %s" % net["ssid"])
        elif net["saved"] and "password" in msg:
            self.set_status("Couldn't connect to %s: %s Forget it (trash icon) and add it again."
                            % (net["ssid"], msg), True)
        else:
            self.set_status("Couldn't connect to %s: %s" % (net["ssid"], msg), True)

    def on_hidden(self, _b):
        self.show_form("Hidden network", [("Network name (SSID)", False), ("Password (empty = open)", True)],
                       "Connect", self._submit_hidden)

    def _submit_hidden(self, vals):
        ssid, password = vals[0].strip(), vals[1]
        if not ssid:
            self.set_status("Enter the network name", True)
            return False
        if password and not (8 <= len(password) <= 63 or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
            self.set_status("A Wi-Fi password is 8 to 63 characters", True)
            return False
        net = dict(ssid=ssid, saved=False)
        self.client.connect_new(ssid, None, "WPA2" if password else "Open", password, True,
                                lambda ok, msg: self._connected(net, ok, msg))
        return True

    def on_forget(self, _b, ssid):
        self.client.forget(ssid, lambda ok, msg: self.set_status(
            "Forgot %s" % ssid if ok else "Couldn't forget %s: %s" % (ssid, msg), not ok))

    # ---- inline form (password / hidden network) ----
    def _clear_prompt(self):
        for child in self.prompt.get_children():
            self.prompt.remove(child)
            child.destroy()
        self.prompt.hide()

    def show_form(self, title, fields, ok_text, on_submit):
        """on_submit(values) returns False to keep the form open (bad input)."""
        self._clear_prompt()
        lab = Gtk.Label(label=title)
        lab.set_xalign(0.0)
        lab.set_line_wrap(True)
        lab.set_max_width_chars(36)
        lab.get_style_context().add_class("wf-prompt-text")
        self.prompt.pack_start(lab, False, False, 0)

        entries = []
        for placeholder, secret in fields:
            entry = Gtk.Entry()
            entry.set_placeholder_text(placeholder)
            if secret:
                entry.set_visibility(False)
                entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
            self.prompt.pack_start(entry, False, False, 0)
            entries.append(entry)

        def submit(*_a):
            if on_submit([e.get_text() for e in entries]) is not False:
                self._clear_prompt()
        entries[-1].connect("activate", submit)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel = Gtk.Button(label="Cancel")
        cancel.get_style_context().add_class("wf-cancel")
        cancel.connect("clicked", lambda *_a: self._clear_prompt())
        ok = Gtk.Button(label=ok_text)
        ok.get_style_context().add_class("wf-ok")
        ok.connect("clicked", submit)
        box.pack_end(ok, False, False, 0)
        box.pack_end(cancel, False, False, 0)
        self.prompt.pack_start(box, False, False, 0)
        self.prompt.show_all()
        entries[0].grab_focus()


# =============================================== main ===============================================

def fatal(text):
    print("wifi-menu:", text, file=sys.stderr)
    try:
        subprocess.run(["notify-send", "-a", "Wi-Fi", "-u", "critical", "Wi-Fi menu", text], timeout=3)
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
      
