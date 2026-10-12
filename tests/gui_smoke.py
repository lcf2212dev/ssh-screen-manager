"""Exercise the native GTK client over SSH against a computer you can access."""
import argparse
import json
from pathlib import Path
import shlex
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
from gi.repository import GLib, Gtk
from ssh_screen_manager import client
from ssh_screen_manager.protocol import ssh_command
from ssh_screen_manager.transport import Connection

root = Path(__file__).resolve().parents[1]
cli = argparse.ArgumentParser(description="Exercise the GTK client over SSH against a computer you can access.")
cli.add_argument("--via", required=True, help="SSH host that runs this two-hop check")
cli.add_argument("--host", required=True, help="Computer to view")
cli.add_argument("--screen", required=True, help="Connector to open on its own, for example HDMI-1")
cli.add_argument("--screens", type=int, required=True, help="Number of screens on that computer")
cli = cli.parse_args()
command = ["ssh", "-T", "-o", "BatchMode=yes", cli.via, shlex.join(ssh_command(cli.host, "low-bandwidth"))]
client.Connection = lambda host, profile: Connection(host, profile, command=command)
args = argparse.Namespace(host=cli.host, connect=True)
app = client.Application(args)
start = time.monotonic()
stage = 0
report = []
errors = []


def screenshot(name):
    w = app.window
    paintable = Gtk.WidgetPaintable.new(w)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, w.get_width(), w.get_height())
    node = snapshot.to_node()
    texture = w.get_renderer().render_texture(node, None)
    texture.save_to_png(str(root / "evidence" / name))


def step():
    global stage, start
    try:
        w = app.window
        elapsed = time.monotonic() - start
        if elapsed > 35:
            raise RuntimeError("GUI timed out: " + w.status.get_label())
        if stage == 0 and len(w.views) == cli.screens and all(v.picture.get_paintable() for v in w.views.values()):
            assert w.stack.get_visible_child_name() == "monitors"
            report.append("Real GTK monitor textures received over SSH")
            screenshot("client-overview.png")
            w.select(cli.screen)
            stage = 1
            start = time.monotonic()
        elif stage == 1 and elapsed > 2:
            assert w.views[cli.screen].get_parent() is w.grid
            assert all(not view.get_visible() for name, view in w.views.items() if name != cli.screen)
            screenshot("client-single.png")
            report.append("Single ultrawide selected and rendered")
            w.fullscreen()
            stage = 2
            start = time.monotonic()
        elif stage == 2 and elapsed > 1:
            assert w.is_fullscreen()
            w.release_control()
            assert not w.control.get_active()
            w.unfullscreen()
            w.select(None)
            report.append("Fullscreen, release control and return to overview passed")
            w.disconnect()
            stage = 3
            start = time.monotonic()
        elif stage == 3 and elapsed > 1:
            assert w.stack.get_visible_child_name() == "welcome"
            w.toggle_connection()
            stage = 4
            start = time.monotonic()
        elif (stage == 4 and len(w.views) == cli.screens and w.stack.get_visible_child_name() == "monitors"
              and all(v.picture.get_paintable() for v in w.views.values())):
            report.append("Disconnect and reconnect passed")
            w.disconnect()
            app.quit()
            return False
    except Exception as exc:
        errors.append(f"Stage {stage}: {type(exc).__name__}: {exc}")
        if hasattr(app, "window"):
            app.window.disconnect()
        app.quit()
        return False
    return True


GLib.timeout_add(300, step)
app.run(None)
result = {"checks": report, "errors": errors}
root.joinpath("evidence/gui-report.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2))
raise SystemExit(1 if errors else 0)
