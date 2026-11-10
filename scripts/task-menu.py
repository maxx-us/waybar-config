#!/usr/bin/env python3
"""Waybar popup for creating Taskwarrior tasks."""
import atexit
import locale
locale.setlocale(locale.LC_ALL, "C")

import calendar as month_calendar
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
from datetime import date, datetime

RUNTIME = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
PIDFILE = os.path.join(RUNTIME, "waybar-task-menu.pid")
WIDTH = 500
YEAR_WIDTH = 920
MARGIN_BOTTOM = 8
WINDOW_SIDE_MARGIN = 12
MONTH_NAMES_EN = ("", "January", "February", "March", "April", "May", "June",
                  "July", "August", "September", "October", "November", "December")

CSS = b"""
window#task-window { background: transparent; }
#task-panel {
    background-color: #212121;
    border: 1px solid rgba(224, 224, 224, 0.12);
    border-radius: 14px;
}
#task-panel * { font-family: "JetBrainsMono Nerd Font Propo"; font-size: 13px; color: #e0e0e0; }
#task-panel entry, #task-panel textview, #task-panel combobox button {
    background-color: #3a3a3a; background-image: none; border: 1px solid rgba(224, 224, 224, 0.12);
    box-shadow: none; border-radius: 7px; padding: 7px 9px; min-height: 0;
}
#task-panel button {
    background-color: transparent; background-image: none; border: none;
    box-shadow: none; text-shadow: none; border-radius: 7px; padding: 7px 10px;
}
#task-panel button:hover { background-color: rgba(255, 255, 255, 0.10); }
#task-panel row, #task-panel row:hover, #task-panel row:selected, #task-panel row:focus {
    background-color: transparent; background-image: none; box-shadow: none;
}
#task-panel button.complete-action:hover { background-color: #b5e8a9; color: #18251b; }
#task-panel button.complete-action:hover label { color: #18251b; }
#task-panel button.suggested-action { background-color: #b5e8a9; color: #18251b; font-weight: bold; }
#task-panel button.suggested-action label { color: #18251b; }
#task-panel label.muted { color: #a3a3a3; }
#task-panel label.error { color: #ff9a9e; }
#task-panel label.section { color: #b5e8a9; }
#task-panel button.urgency-low, #task-panel label.urgency-low {
    background-color: #347b4b; color: #ffffff;
}
#task-panel button.urgency-medium, #task-panel label.urgency-medium {
    background-color: #f2c14e; color: #212121;
}
#task-panel button.urgency-high, #task-panel label.urgency-high {
    background-color: #bd3948; color: #ffffff;
}
#task-panel button.urgency-low label, #task-panel button.urgency-high label { color: #ffffff; }
#task-panel button.urgency-medium label { color: #212121; }
#task-panel button.selected-day { border: 2px solid #ffffff; }
#task-panel button.day-cell { padding: 1px; min-width: 24px; min-height: 23px; }
#task-panel label.weekday { font-size: 10px; color: #a3a3a3; }
#task-panel label.month-title { font-weight: bold; color: #e0e0e0; }
#task-panel label.urgency-legend { padding: 2px 6px; border-radius: 4px; }
#task-panel label.task-row { padding: 7px 8px; }
#task-panel label.task-meta { padding: 0 8px 7px; color: #a3a3a3; font-size: 11px; }
#task-panel button.form-tab:checked { background-color: rgba(181, 232, 169, 0.18); color: #b5e8a9; }
#task-panel button.form-tab:checked label { color: #b5e8a9; }
#task-panel scrolledwindow, #task-panel viewport { background: transparent; }
#task-backdrop { background: rgba(0, 0, 0, 0); }
"""


def toggle_running_instance():
    try:
        old_pid = int(open(PIDFILE, encoding="ascii").read().strip())
        command = open("/proc/%d/cmdline" % old_pid, "rb").read()
        if b"task-menu.py" in command:
            os.kill(old_pid, signal.SIGUSR1)
            return True
    except (OSError, ValueError):
        pass

    pid = os.getpid()
    with open(PIDFILE, "w", encoding="ascii") as handle:
        handle.write(str(pid))

    def cleanup():
        try:
            if open(PIDFILE, encoding="ascii").read().strip() == str(pid):
                os.remove(PIDFILE)
        except OSError:
            pass

    atexit.register(cleanup)
    return False


def parse_custom_modifiers(text):
    modifiers = []
    for raw_line in text.splitlines():
        item = raw_line.strip()
        if not item:
            continue
        if item.startswith(("+", "-")):
            tag = item[1:]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tag):
                raise ValueError("Invalid tag modifier: %s" % item)
        else:
            key, separator, value = item.partition(":")
            if not separator or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", key.strip()):
                raise ValueError("Use key:value or +tag, one modifier per line")
            if not value.strip():
                raise ValueError("Missing value for %s" % key.strip())
            item = "%s:%s" % (key.strip(), value.strip())
        modifiers.append(item)
    return modifiers


def parse_options(text):
    options = shlex.split(text)
    for option in options:
        if option == "--" or option in ("add", "--help", "--version"):
            raise ValueError("Extra options cannot include a command, --, --help, or --version")
        if option.startswith("-") and option != "-":
            continue
        if option.startswith("rc.") and "=" in option:
            continue
        raise ValueError("Extra options must be Taskwarrior flags or rc.name=value")
    return options


try:
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    try:
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
    except (ValueError, ImportError):
        GtkLayerShell = None
except (ValueError, ImportError) as error:
    print("Task popup needs GTK 3 PyGObject: %s" % error, file=sys.stderr)
    sys.exit(1)

locale.setlocale(locale.LC_ALL, "C")


class MaskedDateEntry(Gtk.Entry):
    def __init__(self):
        super().__init__()
        self.digits = ""
        self.set_placeholder_text("yyyy-mm-dd")
        self.set_width_chars(10)
        self.connect("key-press-event", self.on_key_press)

    def set_date(self, value):
        self.digits = value.strftime("%Y%m%d") if value else ""
        self.render_mask()

    def render_mask(self):
        digits = self.digits
        text = (digits[:4].ljust(4, "y") + "-" +
            digits[4:6].ljust(2, "m") + "-" +
            digits[6:8].ljust(2, "d") if digits else "")
        self.set_text(text)
        cursor = len(digits) + (1 if len(digits) >= 4 else 0) + (1 if len(digits) >= 6 else 0)
        self.set_position(min(cursor, len(text)))

    def get_iso_date(self):
        if not self.digits:
            return ""
        if len(self.digits) != 8:
            raise ValueError("Enter all digits in yyyy-mm-dd.")
        try:
            return date(int(self.digits[:4]), int(self.digits[4:6]), int(self.digits[6:8])).isoformat()
        except ValueError:
            raise ValueError("Enter a valid date in yyyy-mm-dd format.") from None

    def on_key_press(self, _entry, event):
        control = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if control and event.keyval == Gdk.KEY_a:
            self.digits = ""
            self.render_mask()
            return True
        if event.keyval in (Gdk.KEY_BackSpace, Gdk.KEY_Delete):
            if self.digits:
                self.digits = self.digits[:-1]
                self.render_mask()
            return True
        character = Gdk.keyval_to_unicode(event.keyval)
        if character and chr(character).isdigit():
            if len(self.digits) < 8:
                self.digits += chr(character)
                self.render_mask()
            return True
        if event.keyval in (Gdk.KEY_Left, Gdk.KEY_Right, Gdk.KEY_Tab,
                            Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return False
        return True


class TaskPopup:
    def __init__(self):
        self.layer = bool(GtkLayerShell) and GtkLayerShell.is_supported()
        self.current_year = date.today().year
        self.selected_date = date.today()
        self.due_urgencies = {}
        self.task_export_error = None
        self.task_return_view = "calendar"
        self.monitor = self.find_monitor()
        monitor_width = self.monitor.get_geometry().width if self.monitor else WIDTH + 2 * WINDOW_SIDE_MARGIN
        self.margin_right = max(0, (monitor_width - WIDTH - 2 * WINDOW_SIDE_MARGIN) // 2)
        self.window = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        self.window.set_title("Calendar")
        self.window.set_decorated(False)
        self.window.set_resizable(False)
        self.window.set_name("task-window")
        self.window.connect("key-press-event", self.on_key)
        self.window.connect("destroy", lambda *_args: Gtk.main_quit())

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.panel.set_name("task-panel")
        self.panel.set_size_request(WIDTH, -1)
        self.panel.set_margin_top(10)
        self.panel.set_margin_bottom(10)
        self.panel.set_margin_start(WINDOW_SIDE_MARGIN)
        self.panel.set_margin_end(WINDOW_SIDE_MARGIN)
        self.window.add(self.panel)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.title = Gtk.Label(label="Calendar")
        self.title.set_xalign(0)
        self.title.set_markup("<b>Calendar</b>")
        header.pack_start(self.title, True, True, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_args: self.close())
        header.pack_end(close, False, False, 0)
        self.panel.pack_start(header, False, False, 8)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(120)
        self.stack.set_homogeneous(False)
        self.stack.add_named(self.make_calendar_page(), "calendar")
        self.stack.add_named(self.make_year_page(), "year")
        self.stack.add_named(self.make_day_page(), "day")
        self.stack.add_named(self.make_undated_page(), "undated")
        self.stack.add_named(self.make_task_page(), "task")
        self.panel.pack_start(self.stack, False, False, 4)

        css = Gtk.CssProvider()
        css.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.backdrop = None
        if self.layer:
            self.setup_layer(self.window, GtkLayerShell.Layer.OVERLAY,
                             (GtkLayerShell.Edge.BOTTOM, GtkLayerShell.Edge.RIGHT),
                             ((GtkLayerShell.Edge.BOTTOM, MARGIN_BOTTOM),
                              (GtkLayerShell.Edge.RIGHT, self.margin_right)), "EXCLUSIVE")
            if self.monitor and hasattr(GtkLayerShell, "set_monitor"):
                GtkLayerShell.set_monitor(self.window, self.monitor)
            self.backdrop = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
            self.backdrop.set_decorated(False)
            self.backdrop.set_name("task-backdrop")
            self.backdrop.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            self.backdrop.connect("button-press-event", lambda *_args: self.close())
            self.setup_layer(self.backdrop, GtkLayerShell.Layer.TOP,
                             (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                              GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT), (), "NONE")

    def make_calendar_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        page.set_margin_top(8)
        page.set_margin_bottom(8)
        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", self.on_calendar_date_selected)
        page.pack_start(self.calendar, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        year_button = Gtk.Button(label="Year")
        year_button.connect("clicked", self.show_year)
        actions.pack_start(year_button, False, False, 0)
        undated_button = Gtk.Button(label="No date")
        undated_button.connect("clicked", self.show_undated)
        actions.pack_start(undated_button, False, False, 0)
        add_task = Gtk.Button(label="Add task")
        add_task.get_style_context().add_class("suggested-action")
        add_task.connect("clicked", self.show_task_form)
        actions.pack_end(add_task, False, False, 0)
        page.pack_start(actions, False, False, 0)
        return page

    def make_year_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.set_size_request(YEAR_WIDTH, -1)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        month_button = Gtk.Button(label="Month")
        month_button.connect("clicked", self.show_calendar)
        toolbar.pack_start(month_button, False, False, 0)
        previous = Gtk.Button(label="Previous year")
        previous.connect("clicked", lambda *_args: self.shift_year(-1))
        toolbar.pack_start(previous, False, False, 0)
        self.year_label = Gtk.Label(label=str(self.current_year))
        self.year_label.get_style_context().add_class("month-title")
        toolbar.pack_start(self.year_label, True, True, 0)
        following = Gtk.Button(label="Next year")
        following.connect("clicked", lambda *_args: self.shift_year(1))
        toolbar.pack_start(following, False, False, 0)
        add_task = Gtk.Button(label="Add task")
        add_task.get_style_context().add_class("suggested-action")
        add_task.connect("clicked", self.show_task_form)
        toolbar.pack_end(add_task, False, False, 0)
        page.pack_start(toolbar, False, False, 0)

        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        legend.pack_start(self.legend_label("Low < 5", "urgency-low"), False, False, 0)
        legend.pack_start(self.legend_label("Medium 5-9.9", "urgency-medium"), False, False, 0)
        legend.pack_start(self.legend_label("High >= 10", "urgency-high"), False, False, 0)
        self.year_status = Gtk.Label(label="Colors show the highest urgency of tasks due that day.")
        self.year_status.set_xalign(1)
        self.year_status.get_style_context().add_class("muted")
        legend.pack_end(self.year_status, True, True, 0)
        page.pack_start(legend, False, False, 0)

        self.year_grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        page.pack_start(self.year_grid, False, False, 0)
        self.refresh_year_grid(load_tasks=False)
        return page

    def make_undated_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back = Gtk.Button(label="Calendar")
        back.connect("clicked", self.show_calendar)
        toolbar.pack_start(back, False, False, 0)
        title = Gtk.Label(label="Tasks without a due date")
        title.get_style_context().add_class("month-title")
        toolbar.pack_start(title, True, True, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self.refresh_undated)
        toolbar.pack_end(refresh, False, False, 0)
        add_task = Gtk.Button(label="Add task")
        add_task.get_style_context().add_class("suggested-action")
        add_task.connect("clicked", lambda *_args: self.show_task_form(with_due=False))
        toolbar.pack_end(add_task, False, False, 0)
        page.pack_start(toolbar, False, False, 0)

        self.undated_list = Gtk.ListBox()
        self.undated_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.undated_list.set_placeholder(Gtk.Label(label="No open tasks without due dates."))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(420)
        scroll.add(self.undated_list)
        page.pack_start(scroll, False, False, 0)
        return page

    def make_day_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back = Gtk.Button(label="← Year")
        back.set_tooltip_text("Back to year overview")
        back.connect("clicked", self.show_year)
        toolbar.pack_start(back, False, False, 0)
        self.day_title = Gtk.Label(label="Tasks due this day")
        self.day_title.get_style_context().add_class("month-title")
        toolbar.pack_start(self.day_title, True, True, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self.refresh_day_tasks)
        toolbar.pack_end(refresh, False, False, 0)
        add_task = Gtk.Button(label="Add task")
        add_task.get_style_context().add_class("suggested-action")
        add_task.connect("clicked", self.show_task_form)
        toolbar.pack_end(add_task, False, False, 0)
        page.pack_start(toolbar, False, False, 0)

        self.day_task_list = Gtk.ListBox()
        self.day_task_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.day_task_list.set_placeholder(Gtk.Label(label="No tasks due on this date."))
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_max_content_height(420)
        scroll.add(self.day_task_list)
        page.pack_start(scroll, False, False, 0)
        self.day_status = Gtk.Label(label="")
        self.day_status.set_xalign(0)
        self.day_status.set_line_wrap(True)
        self.day_status.get_style_context().add_class("muted")
        self.day_status.set_no_show_all(True)
        page.pack_start(self.day_status, False, False, 0)
        return page

    @staticmethod
    def legend_label(text, style_class):
        label = Gtk.Label(label=text)
        label.get_style_context().add_class("urgency-legend")
        label.get_style_context().add_class(style_class)
        return label

    def make_month_table(self, year, month):
        month_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        month_box.set_size_request(205, -1)
        title = Gtk.Label(label=MONTH_NAMES_EN[month])
        title.set_xalign(0)
        title.get_style_context().add_class("month-title")
        month_box.pack_start(title, False, False, 0)

        grid = Gtk.Grid(column_spacing=1, row_spacing=1)
        weekdays = ("M", "T", "W", "T", "F", "S", "S")
        for column, weekday in enumerate(weekdays):
            label = Gtk.Label(label=weekday)
            label.set_size_request(24, 18)
            label.get_style_context().add_class("weekday")
            grid.attach(label, column, 0, 1, 1)

        self.year_day_buttons = getattr(self, "year_day_buttons", {})
        for row, week in enumerate(month_calendar.Calendar(firstweekday=0).monthdayscalendar(year, month), 1):
            for column, day in enumerate(week):
                if not day:
                    empty = Gtk.Label(label="")
                    empty.set_size_request(24, 23)
                    grid.attach(empty, column, row, 1, 1)
                    continue
                selected = date(year, month, day)
                button = Gtk.Button(label=str(day))
                button.set_relief(Gtk.ReliefStyle.NONE)
                button.get_style_context().add_class("day-cell")
                info = self.due_urgencies.get(selected)
                if info:
                    button.get_style_context().add_class(self.urgency_class(info["urgency"]))
                    button.set_tooltip_text("%d due task(s), highest urgency %.1f" %
                                            (info["count"], info["urgency"]))
                if selected == self.selected_date:
                    button.get_style_context().add_class("selected-day")
                button.connect("clicked", self.select_year_date, selected)
                self.year_day_buttons[selected] = button
                grid.attach(button, column, row, 1, 1)
        month_box.pack_start(grid, False, False, 0)
        return month_box

    def refresh_year_grid(self, load_tasks=True):
        if load_tasks:
            tasks = self.load_task_records()
            self.due_urgencies = self.collect_due_urgencies(tasks)
            self.year_status.set_text(self.task_export_error or
                                      "Colors show the highest urgency of tasks due that day.")
        self.year_label.set_text(str(self.current_year))
        self.year_day_buttons = {}
        for child in self.year_grid.get_children():
            self.year_grid.remove(child)
        for month in range(1, 13):
            self.year_grid.attach(self.make_month_table(self.current_year, month),
                                  (month - 1) % 4, (month - 1) // 4, 1, 1)
        self.year_grid.show_all()

    def shift_year(self, amount):
        self.current_year += amount
        self.refresh_year_grid()

    def select_year_date(self, _button, selected):
        old_button = self.year_day_buttons.get(self.selected_date)
        if old_button:
            old_button.get_style_context().remove_class("selected-day")
        self.selected_date = selected
        self.calendar.select_month(selected.month - 1, selected.year)
        self.calendar.select_day(selected.day)
        self.year_day_buttons[selected].get_style_context().add_class("selected-day")
        self.show_day()

    def on_calendar_date_selected(self, calendar_widget):
        year, month, day = calendar_widget.get_date()
        self.selected_date = date(year, month + 1, day)

    def show_task_form(self, *_args, with_due=True):
        self.task_return_view = self.stack.get_visible_child_name() or "calendar"
        selected = self.selected_date if with_due else None
        self.due.set_date(selected)
        self.title.set_text("New task")
        self.stack.set_visible_child_name("task")
        self.set_popup_width(WIDTH)
        self.description.grab_focus()

    def show_calendar(self, *_args):
        self.title.set_text("Calendar")
        self.stack.set_visible_child_name("calendar")
        self.set_popup_width(WIDTH)
        self.calendar.grab_focus()

    def show_year(self, *_args):
        self.current_year = self.selected_date.year
        self.refresh_year_grid()
        self.title.set_text("Year overview")
        self.stack.set_visible_child_name("year")
        self.set_popup_width(YEAR_WIDTH)

    def show_day(self, *_args):
        self.day_title.set_text(self.selected_date.strftime("%A, %B %d, %Y"))
        self.title.set_text("Tasks for selected date")
        self.stack.set_visible_child_name("day")
        self.set_popup_width(WIDTH)
        self.refresh_day_tasks()

    def show_undated(self, *_args):
        self.title.set_text("Tasks without a due date")
        self.stack.set_visible_child_name("undated")
        self.set_popup_width(WIDTH)
        self.refresh_undated()

    def return_to_calendar_view(self, *_args):
        view = self.task_return_view
        if view == "year":
            self.show_year()
        elif view == "day":
            self.show_day()
        elif view == "undated":
            self.show_undated()
        else:
            self.show_calendar()

    def set_popup_width(self, content_width):
        self.panel.set_size_request(content_width, -1)
        if self.monitor:
            monitor_width = self.monitor.get_geometry().width
            self.margin_right = max(
                0, (monitor_width - content_width - 2 * WINDOW_SIDE_MARGIN) // 2)
        if self.layer:
            GtkLayerShell.set_margin(self.window, GtkLayerShell.Edge.RIGHT, self.margin_right)
        elif self.window.get_visible():
            GLib.idle_add(self.position_fallback)

    def position_fallback(self):
        if self.monitor:
            geometry = self.monitor.get_geometry()
            width, height = self.window.get_size()
            self.window.move(geometry.x + (geometry.width - width) // 2,
                             geometry.y + geometry.height - height - 48)
        return GLib.SOURCE_REMOVE

    def load_task_records(self):
        self.task_export_error = None
        if not shutil.which("task"):
            self.task_export_error = "Taskwarrior was not found in PATH."
            return []
        try:
            result = subprocess.run(["task", "export"], text=True, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.task_export_error = "Could not read Taskwarrior tasks: %s" % error
            return []
        if result.returncode:
            self.task_export_error = result.stderr.strip() or "Taskwarrior export failed."
            return []
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.task_export_error = "Taskwarrior returned invalid export data."
            return []
        return [task for task in records if task.get("status") in ("pending", "waiting")]

    @staticmethod
    def parse_due_date(raw_value):
        if not raw_value:
            return None
        try:
            if re.match(r"^\d{8}T", raw_value):
                timestamp = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
                return timestamp.astimezone().date()
            return date.fromisoformat(raw_value[:10])
        except (TypeError, ValueError):
            return None

    @staticmethod
    def urgency_class(urgency):
        if urgency >= 10:
            return "urgency-high"
        if urgency >= 5:
            return "urgency-medium"
        return "urgency-low"

    def collect_due_urgencies(self, tasks):
        result = {}
        for task in tasks:
            due_date = self.parse_due_date(task.get("due"))
            if due_date is None:
                continue
            try:
                urgency = float(task.get("urgency", 0))
            except (TypeError, ValueError):
                urgency = 0.0
            current = result.setdefault(due_date, {"urgency": urgency, "count": 0})
            current["urgency"] = max(current["urgency"], urgency)
            current["count"] += 1
        return result

    def refresh_undated(self, *_args):
        tasks = [task for task in self.load_task_records() if not self.parse_due_date(task.get("due"))]
        for row in self.undated_list.get_children():
            self.undated_list.remove(row)
        if self.task_export_error:
            placeholder = Gtk.Label(label=self.task_export_error)
            placeholder.set_line_wrap(True)
            self.undated_list.set_placeholder(placeholder)
            self.undated_list.show_all()
            return
        self.undated_list.set_placeholder(Gtk.Label(label="No open tasks without due dates."))
        tasks.sort(key=lambda task: (-self.task_urgency(task),
                                     str(task.get("description", "")).casefold()))
        for task in tasks:
            row = Gtk.ListBoxRow()
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            description = Gtk.Label(label="#%s  %s" % (task.get("id", "?"),
                                                         task.get("description", "(no description)")))
            description.set_xalign(0)
            description.set_line_wrap(True)
            description.get_style_context().add_class("task-row")
            content.pack_start(description, False, False, 0)
            details = []
            if task.get("project"):
                details.append("Project: " + str(task["project"]))
            if task.get("priority"):
                details.append("Priority: " + str(task["priority"]))
            if task.get("tags"):
                details.append("Tags: " + ", ".join(task["tags"]))
            if details:
                metadata = Gtk.Label(label=" | ".join(details))
                metadata.set_xalign(0)
                metadata.set_line_wrap(True)
                metadata.get_style_context().add_class("task-meta")
                content.pack_start(metadata, False, False, 0)
            row.add(content)
            self.undated_list.add(row)
        self.undated_list.show_all()

    def refresh_day_tasks(self, *_args):
        tasks = [task for task in self.load_task_records()
                 if self.parse_due_date(task.get("due")) == self.selected_date]
        for row in self.day_task_list.get_children():
            self.day_task_list.remove(row)
        if self.task_export_error:
            placeholder = Gtk.Label(label=self.task_export_error)
            placeholder.set_line_wrap(True)
            self.day_task_list.set_placeholder(placeholder)
            self.day_task_list.show_all()
            return
        self.day_task_list.set_placeholder(Gtk.Label(label="No tasks due on this date."))
        tasks.sort(key=lambda task: (-self.task_urgency(task),
                                     str(task.get("description", "")).casefold()))
        for task in tasks:
            row = self.make_task_row(task)
            self.day_task_list.add(row)
        self.day_task_list.show_all()

    def make_task_row(self, task):
        row = Gtk.ListBoxRow()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        description = Gtk.Label(label="#%s  %s" % (task.get("id", "?"),
                                                     task.get("description", "(no description)")))
        description.set_xalign(0)
        description.set_line_wrap(True)
        description.set_hexpand(True)
        description.get_style_context().add_class("task-row")
        description.get_style_context().add_class(self.urgency_class(self.task_urgency(task)))
        heading.pack_start(description, True, True, 0)
        complete = Gtk.Button(label="Complete")
        complete.get_style_context().add_class("complete-action")
        complete.set_tooltip_text("Mark this task completed in Taskwarrior")
        complete.connect("clicked", self.complete_task, task.get("id"))
        heading.pack_end(complete, False, False, 0)
        content.pack_start(heading, False, False, 0)
        details = []
        if task.get("project"):
            details.append("Project: " + str(task["project"]))
        if task.get("priority"):
            details.append("Priority: " + str(task["priority"]))
        if task.get("tags"):
            details.append("Tags: " + ", ".join(task["tags"]))
        if details:
            metadata = Gtk.Label(label=" | ".join(details))
            metadata.set_xalign(0)
            metadata.set_line_wrap(True)
            metadata.get_style_context().add_class("task-meta")
            content.pack_start(metadata, False, False, 0)
        row.add(content)
        return row

    def complete_task(self, _button, task_id):
        task_id = str(task_id or "")
        if not task_id.isdigit():
            self.day_status.set_text("Taskwarrior returned an invalid task ID.")
            self.day_status.get_style_context().add_class("error")
            self.day_status.show()
            return
        try:
            result = subprocess.run(["task", task_id, "done"], text=True,
                                    capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            result = None
            message = "Could not complete task #%s: %s" % (task_id, error)
        if result is None:
            success = False
        else:
            success = result.returncode == 0
            message = (result.stdout + "\n" + result.stderr).strip()
            if not success and not message:
                message = "Taskwarrior could not complete task #%s." % task_id
            elif success:
                message = "Completed task #%s." % task_id
        self.day_status.set_text(message)
        context = self.day_status.get_style_context()
        context.remove_class("error")
        if not success:
            context.add_class("error")
        self.day_status.show()
        if success:
            self.refresh_day_tasks()
            self.refresh_year_grid()

    def make_task_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tabs = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.basic_tab_button = Gtk.RadioButton.new_with_label_from_widget(None, "Task")
        self.basic_tab_button.get_style_context().add_class("form-tab")
        self.advanced_tab_button = Gtk.RadioButton.new_with_label_from_widget(
            self.basic_tab_button, "Advanced")
        self.advanced_tab_button.get_style_context().add_class("form-tab")
        self.basic_tab_button.connect("toggled", self.switch_form_tab, "basic")
        self.advanced_tab_button.connect("toggled", self.switch_form_tab, "advanced")
        tabs.pack_start(self.basic_tab_button, False, False, 0)
        tabs.pack_start(self.advanced_tab_button, False, False, 0)
        page.pack_start(tabs, False, False, 0)

        self.form_tabs = Gtk.Stack()
        self.form_tabs.set_homogeneous(False)
        self.form_tabs.add_named(self.make_general_tab(), "basic")
        self.form_tabs.add_named(self.make_advanced_tab(), "advanced")
        self.basic_tab_button.set_active(True)
        form_scroll = Gtk.ScrolledWindow()
        form_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        form_scroll.set_propagate_natural_height(True)
        form_scroll.set_max_content_height(320)
        form_scroll.add(self.form_tabs)
        page.pack_start(form_scroll, False, False, 4)

        self.status = Gtk.Label(label="")
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        self.status.set_margin_top(8)
        self.status.set_no_show_all(True)
        page.pack_start(self.status, False, False, 0)

        footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        footer.set_margin_top(10)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_args: self.close())
        back = Gtk.Button(label="Calendar")
        back.connect("clicked", self.return_to_calendar_view)
        self.add_button = Gtk.Button(label="Add task")
        self.add_button.get_style_context().add_class("suggested-action")
        self.add_button.connect("clicked", self.submit)
        footer.pack_end(self.add_button, False, False, 0)
        footer.pack_end(cancel, False, False, 0)
        footer.pack_start(back, False, False, 0)
        page.pack_start(footer, False, False, 0)
        return page

    def switch_form_tab(self, button, name):
        if button.get_active():
            self.form_tabs.set_visible_child_name(name)

    def make_general_tab(self):
        grid = self.form_grid()
        self.description = self.entry("What needs doing?")
        self.project = self.entry("Project.Subproject")
        self.priority = Gtk.ComboBoxText()
        for value, label in (("", "None"), ("H", "High"), ("M", "Medium"), ("L", "Low")):
            self.priority.append(value, label)
        self.priority.set_active_id("")
        self.due = MaskedDateEntry()
        self.tags = self.entry("comma or space separated")
        self.add_row(grid, 0, "Description", self.description)
        self.add_row(grid, 1, "Project", self.project)
        self.add_row(grid, 2, "Priority", self.priority)
        self.add_row(grid, 3, "Due", self.due)
        self.add_row(grid, 4, "Tags", self.tags)
        self.description.connect("activate", lambda *_args: self.submit())
        return self.wrap_tab(grid)

    def make_advanced_tab(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.pack_start(self.section_label("Scheduling"), False, False, 0)
        grid = self.form_grid()
        self.scheduled = self.entry("date expression")
        self.wait = self.entry("date expression")
        self.until = self.entry("date expression")
        self.recur = self.entry("daily, weekly, monthly, ...")
        for row, label, widget in (
            (0, "Scheduled", self.scheduled), (1, "Wait until", self.wait),
            (2, "Until", self.until), (3, "Recurrence", self.recur),
        ):
            self.add_row(grid, row, label, widget)
        box.pack_start(grid, False, False, 0)

        box.pack_start(self.section_label("Dependencies and effort"), False, False, 0)
        relation_grid = self.form_grid()
        self.depends = self.entry("task IDs separated by commas")
        self.estimate = self.entry("e.g. 30min, 2h")
        self.add_row(relation_grid, 0, "Depends on", self.depends)
        self.add_row(relation_grid, 1, "Estimate", self.estimate)
        box.pack_start(relation_grid, False, False, 0)

        box.pack_start(self.section_label("Additional task fields"), False, False, 0)
        custom_grid = self.form_grid()
        self.annotation = self.entry("Added after the task is created")
        self.add_row(custom_grid, 0, "Annotation", self.annotation)
        box.pack_start(custom_grid, False, False, 0)

        custom_label = Gtk.Label(label="Additional attributes or tags, one per line (key:value or +tag)")
        custom_label.set_xalign(0)
        custom_label.get_style_context().add_class("muted")
        box.pack_start(custom_label, False, False, 2)
        self.custom = Gtk.TextView()
        self.custom.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.custom.set_size_request(-1, 76)
        custom_scroll = Gtk.ScrolledWindow()
        custom_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        custom_scroll.add(self.custom)
        box.pack_start(custom_scroll, False, False, 0)

        box.pack_start(self.section_label("Taskwarrior options"), False, False, 0)
        self.options = self.entry("--flag or --flag=value")
        box.pack_start(self.options, False, False, 0)
        return self.wrap_tab(box)

    @staticmethod
    def section_label(text):
        label = Gtk.Label()
        label.set_markup("<b>%s</b>" % text)
        label.set_xalign(0)
        label.get_style_context().add_class("section")
        return label

    @staticmethod
    def form_grid():
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        grid.set_margin_top(12)
        grid.set_margin_bottom(10)
        grid.set_margin_start(4)
        grid.set_margin_end(4)
        return grid

    @staticmethod
    def entry(placeholder):
        widget = Gtk.Entry()
        widget.set_placeholder_text(placeholder)
        return widget

    @staticmethod
    def add_row(grid, row, label, widget):
        caption = Gtk.Label(label=label)
        caption.set_xalign(0)
        caption.set_size_request(100, -1)
        grid.attach(caption, 0, row, 1, 1)
        widget.set_hexpand(True)
        grid.attach(widget, 1, row, 1, 1)

    @staticmethod
    def wrap_tab(content):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_start(8)
        outer.set_margin_end(8)
        outer.pack_start(content, False, False, 0)
        return outer

    @staticmethod
    def setup_layer(window, layer, anchors, margins, keyboard):
        GtkLayerShell.init_for_window(window)
        GtkLayerShell.set_namespace(window, "task-menu")
        GtkLayerShell.set_layer(window, layer)
        for edge in anchors:
            GtkLayerShell.set_anchor(window, edge, True)
        for edge, margin in margins:
            GtkLayerShell.set_margin(window, edge, margin)
        if hasattr(GtkLayerShell, "set_keyboard_mode"):
            GtkLayerShell.set_keyboard_mode(window, getattr(GtkLayerShell.KeyboardMode, keyboard))
        else:
            GtkLayerShell.set_keyboard_interactivity(window, keyboard == "EXCLUSIVE")

    def show(self):
        if self.backdrop:
            self.backdrop.show_all()
        self.window.show_all()
        self.window.present()
        if not self.layer:
            self.position_fallback()
        self.calendar.grab_focus()

    @staticmethod
    def find_monitor():
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor()
        if monitor is None and display.get_n_monitors():
            monitor = display.get_monitor(0)
        return monitor

    def set_status(self, message, is_error=False):
        self.status.set_text(message)
        self.status.get_style_context().remove_class("error")
        if is_error:
            self.status.get_style_context().add_class("error")
        self.status.show()

    @staticmethod
    def task_urgency(task):
        try:
            return float(task.get("urgency", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    def submit(self, *_args):
        description = self.description.get_text().strip()
        if not description:
            self.basic_tab_button.set_active(True)
            self.set_status("Enter a task description.", True)
            self.description.grab_focus()
            return
        if not shutil.which("task"):
            self.set_status("Taskwarrior was not found in PATH.", True)
            return

        modifiers = []
        try:
            due_value = self.due.get_iso_date()
        except ValueError as error:
            self.basic_tab_button.set_active(True)
            self.set_status(str(error), True)
            self.due.grab_focus()
            return
        for key, value in (("project", self.project.get_text()),
                           ("priority", self.priority.get_active_id()),
                           ("due", due_value),
                           ("scheduled", self.scheduled.get_text()),
                           ("wait", self.wait.get_text()),
                           ("until", self.until.get_text()),
                           ("recur", self.recur.get_text()),
                           ("depends", self.depends.get_text()),
                           ("estimate", self.estimate.get_text())):
            value = (value or "").strip()
            if value:
                modifiers.append("%s:%s" % (key, value))
        tags = re.split(r"[,\s]+", self.tags.get_text().strip())
        for tag in filter(None, tags):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tag):
                self.set_status("Tags must be single words using letters, numbers, _ or -.", True)
                return
            modifiers.append("+" + tag)

        try:
            modifiers.extend(parse_custom_modifiers(self.custom.get_buffer().get_text(
                self.custom.get_buffer().get_start_iter(),
                self.custom.get_buffer().get_end_iter(), True)))
            options = parse_options(self.options.get_text())
        except ValueError as error:
            self.advanced_tab_button.set_active(True)
            self.set_status(str(error), True)
            return

        command = ["task", *options, "add", *modifiers, "--", description]
        self.add_button.set_sensitive(False)
        self.set_status("Adding task...")
        while Gtk.events_pending():
            Gtk.main_iteration()
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired) as error:
            self.add_button.set_sensitive(True)
            self.set_status("Could not run Taskwarrior: %s" % error, True)
            return
        self.add_button.set_sensitive(True)
        output = (result.stdout + "\n" + result.stderr).strip()
        if result.returncode:
            self.set_status(output or "Taskwarrior could not add this task.", True)
            return

        annotation = self.annotation.get_text().strip()
        created = re.search(r"Created task\s+(\d+)", output)
        if annotation and created:
            annotated = subprocess.run(["task", *options, created.group(1), "annotate", annotation],
                                       text=True, capture_output=True, timeout=20)
            if annotated.returncode:
                self.set_status("Task added, but annotation failed: %s" %
                                (annotated.stderr.strip() or annotated.stdout.strip()), True)
                return
        self.set_status("Task added successfully.")
        self.description.set_text("")
        self.description.grab_focus()

    def on_key(self, _window, event):
        if event.keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def close(self):
        if self.backdrop:
            self.backdrop.destroy()
            self.backdrop = None
        self.window.destroy()


if __name__ == "__main__":
    if toggle_running_instance():
        sys.exit(0)
    popup = TaskPopup()
    signal.signal(signal.SIGUSR1, lambda *_args: GLib.idle_add(popup.close))
    popup.show()
    Gtk.main()