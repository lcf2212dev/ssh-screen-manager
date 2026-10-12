"""Real discovery -> dropdown selection -> connection -> reconnect, with renders."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk
from ssh_screen_manager import client

parser = argparse.ArgumentParser()
parser.add_argument("--host", required=True, help="SSH name of a computer that is ready")
parser.add_argument("--output", default="evidence/ui-pc")
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
# Isolate test preferences so validation does not replace the user's defaults.
client.CONFIG = output / "settings.json"
app = client.Application(argparse.Namespace(host=args.host, connect=False))
stage, stamp = "discover", time.monotonic()
checks, errors = [], []


class FrameNotReady(Exception):
    pass


def screenshot(w, name):
    paintable = Gtk.WidgetPaintable.new(w)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, w.get_width(), w.get_height())
    node = snapshot.to_node()
    if node is None:
        # A newly received frame can invalidate GTK's snapshot between ticks.
        # Let the next paint complete, then retry this stage.
        raise FrameNotReady()
    w.get_native().get_renderer().render_texture(node, None).save_to_png(str(output / name))


def tick():
    global stage, stamp
    w = app.window
    elapsed = time.monotonic() - stamp
    try:
        if elapsed > 30:
            raise RuntimeError(f"Timeout {stage}: {w.status.get_label()}")
        if stage == "discover" and w.discovery_done and not w.scanning:
            device = w.host.device()
            assert device and device.ready, w.welcome_text.get_label()
            assert w.host.get_text() == args.host
            assert w.connect_button.get_sensitive()
            assert w.host.get_width() < 300, w.host.get_width()
            assert not w.control.get_sensitive()
            checks.append({"discovered": device.name, "screens": device.screens,
                           "selector_width": w.host.get_width(), "aliases": list(w.host.devices)})
            # Let GTK lay out the new selection before rendering.
            stage, stamp = "welcome", time.monotonic()
        elif stage == "welcome" and elapsed > .5:
            screenshot(w, "welcome.png")
            w.host.get_first_child().set_active(True)
            stage, stamp = "dropdown", time.monotonic()
        elif stage == "dropdown" and elapsed > .5:
            assert w.host.get_first_child().get_active()
            checks.append("Native device dropdown opens")
            w.host.get_first_child().set_active(False)
            w.options_button.popup()
            stage, stamp = "options", time.monotonic()
        elif stage == "options" and elapsed > .5:
            screenshot(w.options_button.get_popover(), "options.png")
            w.options_button.popdown()
            w.add_device()
            stage, stamp = "add", time.monotonic()
        elif stage == "add" and elapsed > .5:
            dialog = w.get_visible_dialog()
            assert dialog
            assert not dialog.get_response_enabled("add")
            entry = dialog.get_extra_child()
            entry.set_text("-oProxyCommand=unsafe")
            assert not dialog.get_response_enabled("add")
            entry.set_text(args.host)
            assert dialog.get_response_enabled("add")
            stage, stamp = "add-render", time.monotonic()
        elif stage == "add-render" and elapsed > .5:
            dialog = w.get_visible_dialog()
            screenshot(w, "add-device.png")
            dialog.close()
            checks.append("Manual address validation and cancel passed")
            w.connect_button.emit("clicked")
            stage, stamp = "connect", time.monotonic()
        elif stage == "connect" and w.views and all(v.picture.get_paintable() for v in w.views.values()):
            assert w.stack.get_visible_child_name() == "monitors"
            assert not w.host.get_sensitive()
            assert not w.quality.get_sensitive()
            assert w.control.get_sensitive()
            assert w.tabs.get_visible() == (len(w.views) > 1)
            screenshot(w, "connected.png")
            checks.append({"connected": list(w.views), "textures": len(w.views)})
            w.disconnect()
            stage, stamp = "disconnect", time.monotonic()
        elif stage == "disconnect" and elapsed > .5:
            assert w.stack.get_visible_child_name() == "welcome"
            assert w.host.get_sensitive() and w.connect_button.get_sensitive()
            w.toggle_connection()
            stage, stamp = "reconnect", time.monotonic()
        elif stage == "reconnect" and not w.connection.closed and w.connection.frame_count > 0:
            checks.append("Dropdown selection, connect, disconnect and reconnect passed")
            w.close()
            app.quit()
            return False
    except FrameNotReady:
        return True
    except Exception as exc:
        import traceback
        traceback.print_exc()
        errors.append(f"{stage}: {type(exc).__name__}: {exc}")
        w.close()
        app.quit()
        return False
    return True


GLib.timeout_add(200, tick)
app.run(None)
result = {"checks": checks, "errors": errors}
(output / "report.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result, indent=2), flush=True)
raise SystemExit(bool(errors))
