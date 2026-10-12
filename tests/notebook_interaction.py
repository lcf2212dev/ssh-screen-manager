"""Run on this computer. Mutter events go through the GTK client to a computer you can access."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path.home() / ".local/share/ssh-screen-manager"))
os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk
from ssh_screen_manager.client import Application

cli = argparse.ArgumentParser(description="Control another computer from this GNOME session.")
cli.add_argument("--host", required=True, help="Computer to view")
cli.add_argument("--screen", required=True, help="Connector to click, for example HDMI-1")
cli.add_argument("--screens", type=int, required=True, help="Number of screens on that computer")
cli = cli.parse_args()
app = Application(argparse.Namespace(host=cli.host, connect=True))
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
RD = "org.gnome.Mutter.RemoteDesktop"


def call(path, interface, method, sig=None, args=()):
    return bus.call_sync(RD, path, interface, method, GLib.Variant(sig,args) if sig else None,
                         None, Gio.DBusCallFlags.NONE, 5000, None).unpack()


session = call("/org/gnome/Mutter/RemoteDesktop", RD, "CreateSession")[0]
call(session, RD + ".Session", "Start")


def rd(method, sig, args):
    call(session, RD + ".Session", method, sig, args)


def click_at(x,y):
    rd("NotifyPointerMotionRelative", "(dd)", (-10000., -10000.))
    rd("NotifyPointerMotionRelative", "(dd)", (float(x), float(y)))
    rd("NotifyPointerButton", "(ib)", (272, True))
    rd("NotifyPointerButton", "(ib)", (272, False))


def click_widget(widget):
    success, bounds = widget.compute_bounds(app.window)
    assert success
    x, y = bounds.get_x() + bounds.get_width()/2, bounds.get_y() + bounds.get_height()/2
    print(json.dumps({"click": [x,y], "widget": type(widget).__name__}), flush=True)
    click_at(x, y)


stage = 0
stamp = time.monotonic()
events = []


def tick():
    global stage, stamp
    w = app.window
    try:
        if time.monotonic() - stamp > 30:
            raise RuntimeError(f"Timeout stage {stage}: {w.status.get_label()}")
        if stage == 0 and len(w.views) == cli.screens and all(v.picture.get_paintable() for v in w.views.values()):
            w.fullscreen()
            stage = 1
            stamp = time.monotonic()
        elif stage == 1 and time.monotonic() - stamp > 1:
            click_widget(w.views[cli.screen].picture)
            stage = 2
            stamp = time.monotonic()
        elif stage == 2 and time.monotonic() - stamp > 0.5:
            for key in (ord("a"),):
                rd("NotifyKeyboardKeysym", "(ub)", (key, True))
                rd("NotifyKeyboardKeysym", "(ub)", (key, False))
            click_widget(w.buttons[cli.screen])
            stage = 3
            stamp = time.monotonic()
        elif stage == 3 and time.monotonic() - stamp > 1:
            print(json.dumps({"selected":w.selected, "control": w.control.get_active(), "stage":stage}),flush=True)
            click_widget(w.views[cli.screen].picture)
            stage = 4
            stamp = time.monotonic()
        elif stage == 4 and time.monotonic() - stamp > 0.5:
            for key in (ord("b"),):
                rd("NotifyKeyboardKeysym", "(ub)", (key, True))
                rd("NotifyKeyboardKeysym", "(ub)", (key, False))
            stage = 5
            stamp = time.monotonic()
        elif stage == 5 and time.monotonic() - stamp > 1:
            paintable=Gtk.WidgetPaintable.new(w)
            snapshot=Gtk.Snapshot.new()
            paintable.snapshot(snapshot,w.get_width(),w.get_height())
            texture=w.get_renderer().render_texture(snapshot.to_node(),None)
            texture.save_to_png(str(Path.home()/".cache/ssh-screen-manager-native-test.png"))
            w.disconnect()
            app.quit()
            return False
    except Exception as exc:
        print(f"ERROR: {exc}",flush=True)
        w.disconnect()
        app.quit()
        return False
    return True


GLib.timeout_add(250,tick)
app.run(None)
call(session,RD+".Session","Stop")
