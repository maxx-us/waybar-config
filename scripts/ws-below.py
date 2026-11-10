#!/usr/bin/env python3
"""Waybar module: shows a dot when windows exist on workspaces below the focused one (niri).

Event driven (niri event-stream), so no polling. Hidden (empty text) when there is nothing below.
Set DIRECTION = "above" to watch the workspaces above instead.
"""
import json
import os
import socket
import subprocess
import time

DIRECTION = "below"  # "below" or "above"
DOT = "\u25cf"
RELEVANT = {"WorkspacesChanged", "WorkspaceActivated", "WorkspaceUrgencyChanged",
            "WindowsChanged", "WindowOpenedOrChanged", "WindowClosed", "WindowUrgencyChanged"}


def niri(what):
    return json.loads(subprocess.check_output(["niri", "msg", "--json", what], text=True))


def compute(workspaces, windows):
    focused = next((w for w in workspaces if w.get("is_focused")), None)
    if focused is None:
        return {"text": "", "tooltip": "", "class": ""}
    idx = focused["idx"]
    other = {w["id"] for w in workspaces
             if w["output"] == focused["output"]
             and (w["idx"] > idx if DIRECTION == "below" else w["idx"] < idx)}
    hits = [x for x in windows if x.get("workspace_id") in other]
    if not hits:
        return {"text": "", "tooltip": "", "class": ""}
    names = [f'{x.get("app_id") or "?"}: {x.get("title") or ""}'.replace("&", "&amp;")
             .replace("<", "&lt;") for x in hits[:10]]
    more = f"\n+{len(hits) - 10} more" if len(hits) > 10 else ""
    cls = "urgent" if any(x.get("is_urgent") for x in hits) else "has-windows"
    return {"text": DOT, "class": cls,
            "tooltip": f"{len(hits)} window(s) {DIRECTION}\n" + "\n".join(names) + more}


def event_lines():
    """Raw event stream over niri's socket. Unlike `niri msg event-stream`, this does not
    choke on events from a newer/older niri than the CLI binary."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(os.environ["NIRI_SOCKET"])
    f = sock.makefile("rw")
    f.write('"EventStream"\n')
    f.flush()
    f.readline()  # {"Ok":"Handled"}
    yield from f


def main():
    last = None

    def render():
        nonlocal last
        try:
            out = json.dumps(compute(niri("workspaces"), niri("windows")))
        except Exception:
            return
        if out != last:
            print(out, flush=True)
            last = out

    render()
    try:
        for line in event_lines():
            try:
                ev = next(iter(json.loads(line)))
            except Exception:
                continue
            if ev in RELEVANT:
                render()
    except Exception:
        pass
    while True:  # fallback (no socket, or niri restarted): poll once a second
        render()
        time.sleep(1)


if __name__ == "__main__":
    main()
