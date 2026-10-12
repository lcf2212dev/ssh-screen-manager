"""Compact native device selector with names and live availability."""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .discovery import Device, display_name


class DevicePicker(Gtk.DropDown):
    def __init__(self, preferred=""):
        super().__init__(enable_search=True, valign=Gtk.Align.CENTER)
        self.devices = {}
        self.preferred = preferred
        self.set_size_request(238, -1)
        self.set_tooltip_text("Computer you want to access")
        self.update_property([Gtk.AccessibleProperty.LABEL], ["Remote computer"])
        for setter in (self.set_factory, self.set_list_factory):
            factory = Gtk.SignalListItemFactory()
            factory.connect("setup", self.setup_item)
            factory.connect("bind", self.bind_item)
            setter(factory)
        self.set_devices([Device(preferred, display_name(preferred))] if preferred else [])

    def setup_item(self, factory, item):
        row = Gtk.Box(spacing=10, margin_top=3, margin_bottom=3)
        icon = Gtk.Image(icon_name="computer-symbolic", pixel_size=20)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        name = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=23)
        name.add_css_class("device-name")
        detail = Gtk.Label(xalign=0, ellipsize=3, max_width_chars=29)
        detail.add_css_class("caption")
        detail.add_css_class("dim-label")
        text.append(name)
        text.append(detail)
        row.append(icon)
        row.append(text)
        item.set_child(row)

    def bind_item(self, factory, item):
        host = item.get_item().get_string()
        device = self.devices.get(host, Device("", "No devices", detail="Refresh the list or add a computer"))
        row = item.get_child()
        icon, text = row.get_first_child(), row.get_last_child()
        icon.set_from_icon_name("computer-laptop-symbolic" if any(word in device.name.lower() for word in ("laptop", "notebook")) else "computer-symbolic")
        text.get_first_child().set_label(device.name)
        detail = text.get_last_child()
        detail.set_label(device.detail)
        if device.ready:
            detail.add_css_class("success")
        else:
            detail.remove_css_class("success")
        row.set_tooltip_text(device.host or None)

    def set_devices(self, devices):
        selected = self.get_text() if self.get_model() else self.preferred
        self.devices = {device.host: device for device in devices}
        hosts = list(self.devices)
        chosen = selected if selected in hosts else next((d.host for d in devices if d.ready), hosts[0] if hosts else "")
        self.set_model(Gtk.StringList.new(hosts or [""]))
        self.set_selected(hosts.index(chosen) if chosen in hosts else 0)

    def get_text(self):
        item = self.get_selected_item()
        return item.get_string() if item else ""

    def set_text(self, host):
        self.preferred = host
        if host not in self.devices:
            self.set_devices(list(self.devices.values()) + [Device(host, display_name(host))])
        self.set_selected(list(self.devices).index(host))

    def device(self):
        return self.devices.get(self.get_text())
