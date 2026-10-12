import json
import os
from pathlib import Path
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .protocol import normalized_point, validate_host
from .transport import Connection
from .discovery import Device, discover, display_name
from .device_picker import DevicePicker

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ssh-screen-manager/settings.json"

CSS = """
.canvas { background: @view_bg_color; }
.monitor { background: @window_bg_color; border: 1px solid alpha(@window_fg_color, .12); border-radius: 12px; }
.monitor:focus-within { border-color: @accent_color; }
.monitor-title { padding: 8px 12px; }
.monitor picture { background: #080c12; border-radius: 0 0 11px 11px; }
.monitor.immersive, .monitor.immersive:focus-within {
  border: none; border-radius: 0; padding: 0; margin: 0; box-shadow: none;
}
.monitor.immersive picture, .monitor.immersive picture:focus {
  border: none; border-radius: 0; padding: 0; margin: 0; outline: none; box-shadow: none;
}
.status-line { padding: 10px 18px; font-size: 12px; opacity: .7; }
.hero { font-size: 28px; font-weight: 700; }
.hero-subtitle { font-size: 15px; opacity: .65; }
.connection-bar { padding: 4px 16px 12px; }
.monitor-tabs { padding: 2px 16px 10px; }
.device-name { font-weight: 600; }
.welcome-icon { margin-bottom: 8px; }
.settings { padding: 16px; }
.monitor-tabs button { border-radius: 8px; }
"""


def build_help_dialog():
    """In-app guide. Keep it aligned with the usage section of README.md."""
    dialog = Adw.Dialog(title="Help", content_width=520, content_height=640)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    page = Adw.PreferencesPage()

    def chapter(title, description):
        page.add(Adw.PreferencesGroup(title=title, description=description))

    chapter("What this app does",
            "SSH Screen Manager shows the screens of another computer that is already signed in to GNOME, "
            "and lets you use its keyboard and mouse. The connection goes through SSH. "
            "The app does not open an extra network port or start a background service.")
    chapter("Before you connect",
            "Install this app on both computers, as the person who is signed in to GNOME. "
            "From this computer, SSH to the other one must succeed with a key and must not ask for a password "
            "or whether to trust a host key. On the other computer, an unlocked GNOME on Wayland session has to be open, "
            "with the SSH server running. Put a custom user, port, or address in ~/.ssh/config.")
    chapter("Connect",
            "Choose a computer in the list and click Connect. Then click a screen to control it. "
            "The list checks computers you saved, names from ~/.ssh/config, and known SSH hosts. "
            "It does not scan the network or accept a new host key. "
            "Refresh devices checks again. Add computer saves a name or address that is not listed yet.")
    chapter("Control and screens",
            "The Keyboard and mouse switch turns control on or off. Off is view only. "
            "In Connection options, Send system shortcuts forwards keys such as Alt+Tab while a screen is focused. "
            "GNOME may ask you to allow that. "
            "The expand button on a screen fills your display with that screen. "
            "The full-screen button in the header does the same when the other computer has one screen. "
            "Overview places the primary monitor above the others, and the tabs follow left-to-right order. "
            "That layout does not move windows on the other computer. "
            "Choose image quality before connecting: Save data for a slow network, Balanced for everyday use, "
            "or Sharper text for reading.")
    shortcuts = Adw.PreferencesGroup(title="Keyboard shortcuts")
    for keys, meaning in (
        ("F1", "Open this help"),
        ("Ctrl+Alt+Enter", "Leave full screen and keep control"),
        ("Ctrl+Alt+Esc", "Release the keyboard and mouse, and leave full screen"),
    ):
        shortcuts.add(Adw.ActionRow(title=keys, subtitle=meaning))
    page.add(shortcuts)
    chapter("If a computer is unavailable",
            "It needs to be on, signed in, and unlocked. It also needs to accept your SSH key and have this app installed. "
            "A locked screen is reported as unavailable. Install the app there, then refresh the list.")
    chapter("What this app does not do",
            "There is no sound, shared clipboard, file transfer, or touch input. Use SSH or SFTP to copy files. "
            "The physical screens stay on and show the same activity. There is no privacy or blank-screen mode. "
            "Reconnect after adding, removing, or rearranging monitors.")
    chapter("From a terminal",
            "ssh-screen-manager opens this window. "
            "ssh-screen-manager --host NAME --connect opens it and connects. "
            "ssh-screen-manager --check reports whether this computer can be viewed, without sharing its screen.")
    toolbar.set_content(page)
    dialog.set_child(toolbar)
    return dialog


class MonitorView(Gtk.Box):
    def __init__(self, window, monitor):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.window = window
        self.monitor = monitor
        self.image_width = monitor["width"]
        self.image_height = monitor["height"]
        self.held = set()
        self.keymap = {}
        self.last_point = None
        self.last_motion = 0
        self.add_css_class("monitor")
        title = Gtk.Box(spacing=8)
        self.title_bar = title
        title.add_css_class("monitor-title")
        title.append(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        label = Gtk.Label(label=window.screen_name(monitor["id"]), xalign=0, hexpand=True)
        label.add_css_class("heading")
        title.append(label)
        resolution = Gtk.Label(label=f'{monitor["width"]} × {monitor["height"]}')
        resolution.add_css_class("dim-label")
        resolution.add_css_class("caption")
        title.append(resolution)
        title.set_tooltip_text(f'{monitor["name"]} · {monitor["id"]}')
        expand = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        self.expand_button = expand
        expand.add_css_class("flat")
        expand.set_tooltip_text("Expand screen · Ctrl+Alt+Enter to exit")
        expand.connect("clicked", lambda b: window.monitor_fullscreen(monitor["id"]))
        title.append(expand)
        self.append(title)
        self.picture = Gtk.Picture(hexpand=True, vexpand=True, can_shrink=True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_focusable(True)
        self.picture.set_size_request(120, 90)
        self.append(self.picture)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self.motion)
        motion.connect("leave", self.leave)
        self.picture.add_controller(motion)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self.press)
        click.connect("released", self.release_button)
        click.connect("cancel", lambda *a: self.release_all())
        self.picture.add_controller(click)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.key_pressed)
        keys.connect("key-released", self.key_released)
        self.picture.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *a: self.release_all())
        self.picture.add_controller(focus)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self.scroll)
        self.picture.add_controller(scroll)

    def update(self, header, data):
        texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        self.image_width, self.image_height = texture.get_width(), texture.get_height()
        self.picture.set_paintable(texture)

    def point(self, x, y):
        return normalized_point(x, y, self.picture.get_width(), self.picture.get_height(),
                                self.image_width, self.image_height, bool(self.held),
                                fill=self.picture.get_content_fit() == Gtk.ContentFit.FILL)

    def move(self, point):
        if point:
            self.last_point = point
            self.window.send({"type": "move", "monitor": self.monitor["id"], "x": point[0], "y": point[1]})

    def motion(self, controller, x, y):
        if not self.window.control.get_active():
            return
        now = time.monotonic()
        if now - self.last_motion >= 1 / 60:
            self.move(self.point(x, y))
            self.last_motion = now

    def leave(self, controller):
        # Keyboard focus stays until click elsewhere; GTK holds pointer during drag.
        self.last_point = None

    def press(self, gesture, count, x, y):
        if not self.window.control.get_active() or not self.point(x, y):
            return
        self.picture.grab_focus()
        if self.window.capture.get_active():
            self.window.get_surface().inhibit_system_shortcuts(gesture.get_current_event())
        button = {1: 272, 2: 274, 3: 273, 8: 275, 9: 276}.get(gesture.get_current_button())
        if button:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.move(self.point(x, y))
            self.held.add(button)
            self.window.send({"type": "button", "button": button, "down": True})

    def release_button(self, gesture, count, x, y):
        button = {1: 272, 2: 274, 3: 273, 8: 275, 9: 276}.get(gesture.get_current_button())
        if button in self.held:
            self.move(self.point(x, y))
            self.window.send({"type": "button", "button": button, "down": False})
            self.held.discard(button)

    def key_pressed(self, controller, keyval, keycode, state):
        # This escape chord always remains local, including in fullscreen/capture.
        if keyval == Gdk.KEY_Escape and state & Gdk.ModifierType.CONTROL_MASK and state & Gdk.ModifierType.ALT_MASK:
            self.window.release_control()
            return True
        if not self.window.control.get_active():
            return False
        if keycode not in self.keymap:
            self.keymap[keycode] = keyval
            self.window.send({"type": "key", "key": keyval, "down": True})
        return True

    def key_released(self, controller, keyval, keycode, state):
        key = self.keymap.pop(keycode, None)
        if key:
            self.window.send({"type": "key", "key": key, "down": False})

    def release_all(self):
        self.held.clear()
        self.keymap.clear()
        self.window.send({"type": "release"})
        if self.window.get_surface():
            self.window.get_surface().restore_system_shortcuts()

    def scroll(self, controller, dx, dy):
        if self.window.control.get_active() and self.last_point:
            self.move(self.last_point)
            self.window.send({"type": "scroll", "dx": max(-1000, min(1000, dx * 10)),
                              "dy": max(-1000, min(1000, dy * 10))})
            return True
        return False


class Window(Adw.ApplicationWindow):
    def __init__(self, app, args):
        super().__init__(application=app, title="SSH Screen Manager", default_width=1080, default_height=760)
        self.connection = None
        self.views = {}
        self.monitors = []
        self.selected = None
        self.buttons = {}
        self.last_error = None
        self.started_at = 0
        self.last_stats = time.monotonic()
        self.previous_bytes = 0
        self.previous_frames = 0
        self.refreshing = False
        self.scanning = False
        self.closed = False
        self.discovery_done = False
        self.active_host = ""
        self.settings = {}
        try:
            self.settings = json.loads(CONFIG.read_text())
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            pass
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(content)
        header = Adw.HeaderBar()
        self.header = header
        self.window_title = Adw.WindowTitle(title="SSH Screen Manager")
        header.set_title_widget(self.window_title)
        self.control = Gtk.ToggleButton(label="Keyboard & mouse", active=True, valign=Gtk.Align.CENTER)
        self.control.set_tooltip_text("Enable to control, or disable to view only. Ctrl+Alt+Esc releases control.")
        self.control.set_sensitive(False)
        self.control.set_visible(False)
        self.control.connect("toggled", self.control_changed)
        self.capture = Gtk.CheckButton(label="Send system shortcuts")
        self.capture.set_tooltip_text("Send shortcuts such as Alt+Tab to the remote computer while its screen is focused.")
        self.capture.set_sensitive(False)
        self.capture.connect("toggled", self.capture_changed)
        self.fullscreen_button = Gtk.Button.new_from_icon_name("view-fullscreen-symbolic")
        self.fullscreen_button.set_tooltip_text("Full screen · Ctrl+Alt+Enter to exit")
        self.fullscreen_button.set_sensitive(False)
        self.fullscreen_button.set_visible(False)
        self.fullscreen_button.connect("clicked", lambda b: self.toggle_fullscreen())
        self.help_button = Gtk.Button.new_from_icon_name("help-browser-symbolic")
        self.help_button.set_tooltip_text("Help")
        self.help_button.update_property([Gtk.AccessibleProperty.LABEL], ["Help"])
        self.help_button.connect("clicked", lambda button: self.show_help())
        header.pack_end(self.help_button)
        header.pack_end(self.fullscreen_button)
        self.options_button = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Connection options")
        options = Gtk.Popover()
        settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        settings_box.add_css_class("settings")
        quality_label = Gtk.Label(label="Image quality", xalign=0)
        quality_label.add_css_class("heading")
        settings_box.append(quality_label)
        self.quality = Gtk.DropDown.new_from_strings(["Save data", "Balanced", "Sharper text"])
        self.quality.set_tooltip_text("Choose before connecting. Sharper text preserves screen resolution for reading.")
        self.quality.set_selected(min(max(int(self.settings.get("quality", 1)), 0), 2))
        settings_box.append(self.quality)
        settings_box.append(Gtk.Separator())
        settings_box.append(self.capture)
        help_text = Gtk.Label(label="F1  ·  Help\nCtrl+Alt+Enter  ·  Exit full screen\nCtrl+Alt+Esc  ·  Release keyboard and mouse",
                              xalign=0, justify=Gtk.Justification.LEFT)
        help_text.add_css_class("caption")
        help_text.add_css_class("dim-label")
        settings_box.append(help_text)
        settings_box.append(Gtk.Separator())
        self.details = Gtk.Label(label="No active connection", xalign=0)
        self.details.add_css_class("caption")
        self.details.add_css_class("dim-label")
        settings_box.append(self.details)
        options.set_child(settings_box)
        self.options_button.set_popover(options)
        header.pack_end(self.options_button)
        self.connect("notify::fullscreened", self.fullscreen_changed)
        content.append(header)
        bar = Gtk.Box(spacing=8)
        self.connection_bar = bar
        bar.add_css_class("connection-bar")
        self.host = DevicePicker(args.host or self.settings.get("host", ""))
        self.host.connect("notify::selected-item", self.device_changed)
        bar.append(self.host)
        self.refresh_button = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                         tooltip_text="Refresh devices")
        self.refresh_button.add_css_class("flat")
        self.refresh_button.connect("clicked", lambda b: self.scan_devices())
        bar.append(self.refresh_button)
        self.add_button = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER,
                                     tooltip_text="Add computer")
        self.add_button.add_css_class("flat")
        self.add_button.connect("clicked", self.add_device)
        bar.append(self.add_button)
        self.connect_button = Gtk.Button(label="Connect", valign=Gtk.Align.CENTER)
        self.connect_button.add_css_class("suggested-action")
        self.connect_button.set_sensitive(False)
        self.connect_button.connect("clicked", lambda b: self.toggle_connection())
        bar.append(self.connect_button)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self.control)
        content.append(bar)
        self.tabs = Gtk.Box(spacing=6)
        self.tabs.add_css_class("monitor-tabs")
        self.tabs.set_visible(False)
        content.append(self.tabs)
        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.stack.add_css_class("canvas")
        welcome = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                          halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                          margin_start=32, margin_end=32, margin_top=32, margin_bottom=32)
        illustration = Gtk.Image.new_from_icon_name("io.github.ssh_screen_manager.Remote")
        illustration.set_pixel_size(88)
        illustration.add_css_class("welcome-icon")
        welcome.append(illustration)
        self.welcome_title = Gtk.Label(label="Finding computers", wrap=True,
                                       justify=Gtk.Justification.CENTER)
        self.welcome_title.add_css_class("hero")
        welcome.append(self.welcome_title)
        self.welcome_text = Gtk.Label(label="Available devices will appear in the list above.",
                                      justify=Gtk.Justification.CENTER, wrap=True, max_width_chars=48)
        self.welcome_text.add_css_class("hero-subtitle")
        welcome.append(self.welcome_text)
        how_to = Gtk.Button(label="How to use")
        how_to.add_css_class("pill")
        how_to.set_tooltip_text("Open help")
        how_to.connect("clicked", lambda button: self.show_help())
        welcome.append(how_to)
        self.stack.add_named(welcome, "welcome")
        self.grid = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True, row_homogeneous=True,
                             margin_start=14, margin_end=14, margin_top=14, margin_bottom=14)
        self.stack.add_named(self.grid, "monitors")
        content.append(self.stack)
        self.status = Gtk.Label(label="Checking devices…", xalign=0,
                                ellipsize=3, selectable=True)
        self.status.add_css_class("status-line")
        content.append(self.status)
        self.connect("close-request", self.closing)
        self.connect("notify::is-active", self.active_changed)
        shortcuts = Gtk.EventControllerKey()
        shortcuts.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        shortcuts.connect("key-pressed", self.local_shortcut)
        self.add_controller(shortcuts)
        GLib.timeout_add(33, self.refresh)
        GLib.timeout_add_seconds(1, self.statistics)
        GLib.timeout_add_seconds(30, self.auto_scan)
        if args.connect:
            GLib.idle_add(self.toggle_connection)
        else:
            GLib.idle_add(self.scan_devices)

    def save_settings(self):
        try:
            CONFIG.parent.mkdir(parents=True, exist_ok=True)
            temporary = CONFIG.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.settings))
            temporary.replace(CONFIG)
        except OSError:
            pass

    def scan_devices(self):
        if self.closed or self.scanning or (self.connection and not self.connection.closed):
            return False
        self.scanning = True
        self.refresh_button.set_sensitive(False)
        self.status.set_label("Checking devices…")
        spinner = Gtk.Spinner(spinning=True)
        self.refresh_button.set_child(spinner)
        saved = list(self.settings.get("devices", []))
        saved[:0] = [self.host.get_text(), self.settings.get("host", "")]
        def work():
            try:
                devices = discover(saved)
                GLib.idle_add(self.devices_found, devices, None)
            except Exception:
                GLib.idle_add(self.devices_found, [], "Could not refresh devices. Please try again.")
        threading.Thread(target=work, daemon=True).start()
        return False

    def show_help(self):
        dialog = getattr(self, "help_dialog", None)
        if dialog is None:
            dialog = build_help_dialog()
            self.help_dialog = dialog
        dialog.present(self)

    def devices_found(self, devices, error):
        if self.closed:
            return False
        self.scanning = False
        self.discovery_done = True
        self.refresh_button.set_icon_name("view-refresh-symbolic")
        busy = self.connection and not self.connection.closed
        self.refresh_button.set_sensitive(not busy)
        if busy:
            return False
        self.host.set_devices(devices)
        self.device_changed()
        count = sum(device.ready for device in devices)
        self.status.set_label(error or (f"{count} " + ("device available" if count == 1 else "devices available")))
        return False

    def auto_scan(self):
        if self.closed:
            return False
        self.scan_devices()
        return True

    def device_changed(self, *args):
        if not hasattr(self, "welcome_title") or (self.connection and not self.connection.closed):
            return
        device = self.host.device()
        self.connect_button.set_sensitive(bool(device and device.ready))
        if device and device.ready:
            self.welcome_title.set_label(f"Connect to {device.name}")
            self.welcome_text.set_label("View and control this computer's screens.\nClick Connect to get started.")
        elif device:
            self.welcome_title.set_label(f"{device.name} is unavailable")
            self.welcome_text.set_label(f"{device.detail}.\nCheck the computer, then refresh the list.")
        elif self.discovery_done:
            self.welcome_title.set_label("No computers available")
            self.welcome_text.set_label("Turn on the other computer and sign in.\nUse + to add a device that is not listed.")

    def add_device(self, *args):
        dialog = Adw.AlertDialog(heading="Add computer",
                                body="Enter the name or address of a computer with SSH Screen Manager and SSH access set up.")
        entry = Gtk.Entry(placeholder_text="Name or address", activates_default=True)
        entry.update_property([Gtk.AccessibleProperty.LABEL], ["Computer name or address"])
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("add", "Check device")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("add", False)
        def changed(*args):
            try:
                validate_host(entry.get_text().strip())
                valid = True
            except ValueError:
                valid = False
            dialog.set_response_enabled("add", valid)
        entry.connect("changed", changed)
        def respond(dialog, response):
            if response != "add":
                return
            host = validate_host(entry.get_text().strip())
            saved = list(self.settings.get("devices", []))
            if host not in saved:
                saved.append(host)
            self.settings["devices"] = saved
            self.save_settings()
            self.host.set_text(host)
            # A running scan has an older candidate snapshot. Queue the new
            # scan immediately after it finishes rather than dropping this host.
            def retry():
                if self.closed:
                    return False
                if self.scanning:
                    return True
                self.host.set_text(host)
                self.scan_devices()
                return False
            GLib.timeout_add(200, retry)
        dialog.connect("response", respond)
        dialog.present(self)
        entry.grab_focus()

    def screen_name(self, connector):
        index = next((i for i, m in enumerate(self.monitors) if m["id"] == connector), 0)
        monitor = self.monitors[index]
        return f"Screen {index + 1}" + (" · Primary" if monitor["primary"] and len(self.monitors) > 1 else "")

    def send(self, message):
        if self.connection and self.connection.ready and not self.connection.closed:
            self.connection.send(message)

    def toggle_connection(self):
        if self.connection and not self.connection.closed:
            self.disconnect()
            return
        try:
            host = validate_host(self.host.get_text().strip())
        except ValueError as exc:
            self.status.set_label(str(exc))
            return
        quality = min(self.quality.get_selected(), 2)
        self.settings.update(host=host, quality=quality)
        self.save_settings()
        self.active_host = host
        self.last_error = None
        self.started_at = time.monotonic()
        self.previous_bytes = self.previous_frames = 0
        self.last_stats = self.started_at
        self.connection = Connection(host, ["low-bandwidth", "balanced", "sharp"][quality])
        self.connect_button.set_label("Cancel")
        self.connect_button.set_sensitive(True)
        self.host.set_sensitive(False)
        self.refresh_button.set_sensitive(False)
        self.add_button.set_sensitive(False)
        self.quality.set_sensitive(False)
        self.stack.set_visible_child_name("welcome")
        device = self.host.device()
        name = device.name if device else display_name(host)
        self.welcome_title.set_label(f"Connecting to {name}")
        self.welcome_text.set_label("Your screens will appear in a moment.")
        self.status.set_label("Establishing a secure connection…")

    def disconnect(self):
        self.release_control(disable=False)
        if self.connection:
            self.connection.close()
        self.reset_ui("Disconnected. You can reconnect at any time.")

    def reset_ui(self, message):
        if self.is_fullscreen():
            self.unfullscreen()
        self.connect_button.set_label("Connect")
        self.connect_button.add_css_class("suggested-action")
        self.host.set_sensitive(True)
        self.refresh_button.set_sensitive(not self.scanning)
        self.add_button.set_sensitive(True)
        self.quality.set_sensitive(True)
        self.control.set_sensitive(False)
        self.control.set_visible(False)
        self.capture.set_sensitive(False)
        self.fullscreen_button.set_sensitive(False)
        self.fullscreen_button.set_visible(False)
        self.window_title.set_subtitle("")
        self.details.set_label("No active connection")
        self.tabs.set_visible(False)
        self.stack.set_visible_child_name("welcome")
        self.welcome_title.set_label("Connection closed")
        self.welcome_text.set_label(message)
        self.status.set_label(message)
        self.connect_button.set_sensitive(bool(self.host.get_text()))

    def setup_monitors(self, monitors, hostname=None):
        for view in self.views.values():
            if view.get_parent():
                self.grid.remove(view)
        self.monitors = monitors
        self.views = {m["id"]: MonitorView(self, m) for m in monitors}
        for index, view in enumerate(self.views.values()):
            self.grid.attach(view, 0, index, 1, 1)
        while self.tabs.get_first_child():
            self.tabs.remove(self.tabs.get_first_child())
        self.buttons = {}
        for key, label in [(None, "Overview")] + [(m["id"], self.screen_name(m["id"])) for m in monitors]:
            button = Gtk.ToggleButton(label=label)
            button.connect("clicked", lambda b, k=key: self.select(k))
            self.tabs.append(button)
            self.buttons[key] = button
        self.tabs.set_visible(len(monitors) > 1)
        self.stack.set_visible_child_name("monitors")
        self.connect_button.set_label("Disconnect")
        self.connect_button.remove_css_class("suggested-action")
        self.control.set_sensitive(True)
        self.control.set_visible(True)
        self.capture.set_sensitive(True)
        self.fullscreen_button.set_sensitive(True)
        self.fullscreen_button.set_visible(True)
        name = display_name(hostname or self.active_host)
        device = Device(self.active_host, name, ready=True, screens=len(monitors),
                        detail=f"Available · {len(monitors)} " + ("screen" if len(monitors) == 1 else "screens"))
        self.host.set_devices([d for d in self.host.devices.values() if d.host != self.active_host] + [device])
        self.host.set_text(self.active_host)
        self.window_title.set_subtitle(name)
        self.status.set_label("Keyboard and mouse enabled" if self.control.get_active() else "View only")
        # Receive at least one frame from every monitor before applying gates.
        self.select(None)

    def select(self, connector):
        if self.refreshing:
            return
        self.refreshing = True
        self.release_control(disable=False)
        self.selected = connector
        for key, button in self.buttons.items():
            button.set_active(key == connector)
        for key, view in self.views.items():
            view.set_visible(connector is None or key == connector)
        if connector:
            self.place(self.views[connector], 0, 0, 1)
        else:
            # Primary across the top, the other monitors below: readable on a notebook.
            primary = next((m for m in self.monitors if m["primary"]), self.monitors[0])
            others = [m for m in self.monitors if m is not primary]
            columns = max(1, min(len(others), 3))
            self.place(self.views[primary["id"]], 0, 0, columns)
            for i, monitor in enumerate(others):
                self.place(self.views[monitor["id"]], i % columns, 1 + i // columns, 1)
        self.send({"type": "select", "monitors": [connector] if connector else list(self.views)})
        self.refreshing = False
        self.update_fullscreen_layout()
        if connector and self.control.get_active():
            GLib.idle_add(self.focus_selected)

    def focus_selected(self):
        if self.selected and self.control.get_active():
            self.views[self.selected].picture.grab_focus()
        return False

    def place(self, view, column, row, span):
        child = self.grid.get_layout_manager().get_layout_child(view)
        child.set_column(column)
        child.set_row(row)
        child.set_column_span(span)
        child.set_row_span(1)

    def toggle_fullscreen(self):
        if self.is_fullscreen():
            self.unfullscreen()
        else:
            if len(self.views) == 1:
                self.select(next(iter(self.views)))
            self.fullscreen()

    def fullscreen_changed(self, window, param):
        self.update_fullscreen_layout()

    def update_fullscreen_layout(self):
        fullscreen = self.is_fullscreen()
        immersive = fullscreen and self.selected is not None and self.stack.get_visible_child_name() == "monitors"
        self.fullscreen_button.set_icon_name("view-restore-symbolic" if fullscreen else "view-fullscreen-symbolic")
        self.fullscreen_button.set_tooltip_text("Exit full screen · Ctrl+Alt+Enter" if fullscreen else "Full screen · Ctrl+Alt+Enter to exit")
        self.header.set_visible(not immersive)
        self.connection_bar.set_visible(not fullscreen)
        self.tabs.set_visible(not fullscreen and len(self.views) > 1 and self.stack.get_visible_child_name() == "monitors")
        self.status.set_visible(not fullscreen)
        for method in (self.grid.set_margin_start, self.grid.set_margin_end,
                       self.grid.set_margin_top, self.grid.set_margin_bottom):
            method(0 if immersive else 14)
        self.grid.set_row_spacing(0 if immersive else 12)
        self.grid.set_column_spacing(0 if immersive else 12)
        for view in self.views.values():
            view.title_bar.set_visible(not immersive)
            view.picture.set_content_fit(Gtk.ContentFit.FILL if immersive else Gtk.ContentFit.CONTAIN)
            if immersive:
                view.add_css_class("immersive")
            else:
                view.remove_css_class("immersive")
        if immersive:
            GLib.idle_add(self.focus_selected)

    def local_shortcut(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_F1 and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
                                                 | Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.SUPER_MASK):
            self.show_help()
            return True
        modifiers = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
        if state & modifiers != modifiers:
            return False
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.is_fullscreen():
            self.exit_fullscreen()
            return True
        if keyval == Gdk.KEY_Escape:
            if self.is_fullscreen():
                self.exit_fullscreen()
            self.release_control()
            return True
        return False

    def exit_fullscreen(self):
        # Ctrl/Alt may already have reached the remote host; release them before
        # consuming Enter locally, so no modifier remains held in the PC session.
        self.release_control(disable=False)
        self.unfullscreen()

    def monitor_fullscreen(self, connector):
        if self.is_fullscreen() and self.selected == connector:
            self.unfullscreen()
            return
        self.select(connector)
        self.fullscreen()

    def release_control(self, disable=True):
        for view in self.views.values():
            view.release_all()
        self.capture.set_active(False)
        if disable:
            self.control.set_active(False)
            self.host.grab_focus()

    def control_changed(self, button):
        if not button.get_active():
            self.release_control(disable=False)

    def capture_changed(self, button):
        if not button.get_active() and self.get_surface():
            self.get_surface().restore_system_shortcuts()

    def active_changed(self, window, param):
        if not self.is_active():
            self.release_control(disable=False)

    def refresh(self):
        if self.closed:
            return False
        if not self.connection:
            return True
        events, frames = self.connection.drain()
        for event in events:
            kind = event.get("type")
            if kind == "hello":
                self.setup_monitors(event["monitors"], event.get("hostname"))
            elif kind == "error":
                self.last_error = event.get("message", "Remote error")
                self.status.set_label(self.last_error)
            elif kind == "disconnected":
                if event.get("intentional"):
                    message = "Disconnected. Your windows are still open on the remote computer."
                else:
                    details = event.get("details", "")
                    message = self.last_error or self.explain_ssh(details)
                self.reset_ui(message)
        for connector, (header, data) in frames.items():
            if connector in self.views:
                try:
                    self.views[connector].update(header, data)
                except GLib.Error as exc:
                    self.status.set_label(f"Invalid image: {exc.message}")
        if not self.connection.ready and not self.connection.closed and time.monotonic() - self.started_at > 20:
            self.last_error = "The computer did not respond. Make sure it is on and signed in."
            self.connection.close(intentional=False)
        return True

    @staticmethod
    def explain_ssh(details):
        if "Permission denied" in details:
            return "Access denied. Set up your SSH key for this computer."
        if "Host key verification failed" in details:
            return "This computer is not trusted yet. Verify its identity using SSH in a terminal."
        if "No such file" in details or "not found" in details:
            return "Install SSH Screen Manager on the computer you want to access."
        if "Connection refused" in details:
            return "The computer refused the connection. Make sure SSH access is enabled."
        if "resolve hostname" in details:
            return "Computer not found. Check its address and your network connection."
        return "The connection was lost. Check your network and try again."

    def statistics(self):
        if self.closed:
            return False
        c = self.connection
        if c and c.ready and not c.closed and not self.last_error:
            now = time.monotonic()
            elapsed = max(0.1, now - self.last_stats)
            mbps = (c.bytes_received - self.previous_bytes) * 8 / elapsed / 1_000_000
            fps = (c.frame_count - self.previous_frames) / elapsed
            self.previous_bytes, self.previous_frames, self.last_stats = c.bytes_received, c.frame_count, now
            c.send({"type": "ping", "time": now})
            label = self.screen_name(self.selected) if self.selected else f"{len(self.monitors)} " + ("screen" if len(self.monitors) == 1 else "screens")
            latency = f"  ·  {c.latency_ms} ms" if c.latency_ms is not None else ""
            state = "Keyboard and mouse enabled" if self.control.get_active() else "View only"
            if c.last_frame and now - c.last_frame > 8:
                state = "Waiting for video. Check whether the remote computer is locked."
            self.status.set_label(f"{state}  ·  {label}  ·  Ctrl+Alt+Enter exits full screen")
            self.details.set_label(f"{fps:.0f} fps  ·  {mbps:.1f} Mb/s{latency}")
        return True

    def closing(self, *args):
        self.closed = True
        self.disconnect()
        return False


class Application(Adw.Application):
    def __init__(self, args):
        super().__init__(application_id="io.github.ssh_screen_manager.Remote", flags=Gio.ApplicationFlags.NON_UNIQUE)
        GLib.set_application_name("SSH Screen Manager")
        self.args = args

    def do_activate(self):
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Window(self, self.args)
        self.window.present()
