"""Dedicated fullscreen window for safe end-to-end input verification."""
import argparse
import json
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

parser = argparse.ArgumentParser()
parser.add_argument("monitor")
parser.add_argument("report")
parser.add_argument("--timeout", type=int, default=25)
args = parser.parse_args()
report = open(args.report, "w", buffering=1)


def record(kind, **kw):
    report.write(json.dumps({"type": kind, **kw}, ensure_ascii=False) + "\n")
    if kind == "ready":
        print("READY", flush=True)


class Target(Gtk.Application):
    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self, title="SSH Screen Manager — input test")
        area = Gtk.DrawingArea(focusable=True, hexpand=True, vexpand=True)

        def draw(area, cr, width, height):
            cr.set_source_rgb(0.05, 0.12, 0.20)
            cr.paint()
            cr.set_source_rgb(0.5, 0.8, 1)
            cr.select_font_face("Sans", 0, 1)
            cr.set_font_size(36)
            cr.move_to(80, 100)
            cr.show_text("SSH Screen Manager — checking " + args.monitor)
            cr.set_font_size(22)
            cr.move_to(80, 145)
            cr.show_text("Temporary test window. It will close automatically.")
            for x, y in ((0.2, 0.2), (0.5, 0.5), (0.8, 0.8)):
                cr.arc(width*x, height*y, 12, 0, 6.284)
                cr.fill()

        area.set_draw_func(draw)
        window.set_child(area)
        click = Gtk.GestureClick(button=0)

        def pressed(g, n, x, y):
            area.grab_focus()
            record("press", x=x, y=y, button=g.get_current_button(), width=area.get_width(), height=area.get_height())

        click.connect("pressed", pressed)
        click.connect("released", lambda g, n, x, y: record("release", x=x, y=y, button=g.get_current_button()))
        area.add_controller(click)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda c, x, y: record("move", x=x, y=y))
        area.add_controller(motion)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda c, k, code, s: record("key-down", key=k, state=int(s)) or True)
        keys.connect("key-released", lambda c, k, code, s: record("key-up", key=k, state=int(s)))
        area.add_controller(keys)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", lambda c, dx, dy: record("scroll", dx=dx, dy=dy) or True)
        area.add_controller(scroll)
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        monitor = next(m for m in monitors if m.get_connector() == args.monitor)
        window.fullscreen_on_monitor(monitor)
        window.present()
        area.grab_focus()
        GLib.timeout_add(1500, lambda: record("ready", width=area.get_width(), height=area.get_height()) or False)
        GLib.timeout_add_seconds(args.timeout, lambda: self.quit() or False)


Target(application_id="io.github.ssh_screen_manager.InputTest").run(None)
