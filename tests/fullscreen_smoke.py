"""Real GTK bounds, streamed textures and native Ctrl+Alt+Enter on a computer you can access."""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path.home() / '.local/share/ssh-screen-manager'))
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Gdk, Gio, GLib, Gtk
from ssh_screen_manager.client import Application
parser = argparse.ArgumentParser()
parser.add_argument('--host', required=True, help='Computer to view')
parser.add_argument('--monitors', nargs='+', required=True, help='Connector names to exercise, in order')
args = parser.parse_args()
app = Application(argparse.Namespace(host=args.host, connect=True))
stage = 'wait'
stamp = time.monotonic()
report = {'checks': [], 'errors': []}
connectors = args.monitors
index = 0
sent = []
RD = 'org.gnome.Mutter.RemoteDesktop'
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
def call(path, method, signature=None, args=()):
    interface = RD + '.Session' if path != '/org/gnome/Mutter/RemoteDesktop' else RD
    return bus.call_sync(RD, path, interface, method, GLib.Variant(signature, args) if signature else None,
                         None, Gio.DBusCallFlags.NONE, 5000, None).unpack()
session = call('/org/gnome/Mutter/RemoteDesktop', 'CreateSession')[0]
call(session, 'Start')
def exit_chord():
    for key in (Gdk.KEY_Control_L, Gdk.KEY_Alt_L, Gdk.KEY_Return):
        call(session, 'NotifyKeyboardKeysym', '(ub)', (key, True))
    for key in (Gdk.KEY_Return, Gdk.KEY_Alt_L, Gdk.KEY_Control_L):
        call(session, 'NotifyKeyboardKeysym', '(ub)', (key, False))
def screenshot(w):
    paintable = Gtk.WidgetPaintable.new(w)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, w.get_width(), w.get_height())
    w.get_renderer().render_texture(snapshot.to_node(), None).save_to_png(
        str(Path.home() / '.cache/ssh-screen-manager-fullscreen-total.png'))
def tick():
    global stage, stamp, index
    w = app.window
    try:
        if time.monotonic() - stamp > 25:
            raise RuntimeError(f'Timeout {stage}: {w.status.get_label()}')
        if stage == 'wait' and len(w.views) == len(connectors) and all(v.picture.get_paintable() for v in w.views.values()):
            original_send = w.send
            def traced_send(message):
                sent.append(message)
                original_send(message)
            w.send = traced_send
            report['checks'].append({'overview': list(w.views), 'textures': len(connectors)})
            w.views[connectors[index]].expand_button.emit('clicked')
            stage, stamp = 'fullscreen', time.monotonic()
        elif stage == 'fullscreen' and time.monotonic() - stamp > 1:
            assert w.is_fullscreen()
            assert w.selected == connectors[index]
            view = w.views[w.selected]
            assert not any(widget.get_visible() for widget in
                           (w.header, w.connection_bar, w.tabs, w.status, view.title_bar))
            assert view.picture.get_content_fit() == Gtk.ContentFit.FILL
            ok, bounds = view.picture.compute_bounds(w)
            assert ok
            actual = [bounds.get_x(), bounds.get_y(), bounds.get_width(), bounds.get_height()]
            expected = [0, 0, w.get_width(), w.get_height()]
            assert actual == expected, (actual, expected)
            assert view.point(0, 0) == (0, 0)
            assert view.point(w.get_width(), w.get_height()) == (1, 1)
            report['checks'].append({'monitor': w.selected, 'picture_bounds': actual, 'chrome_visible': False})
            if w.selected == connectors[0]:
                screenshot(w)
            sent.clear()
            w.present()
            view.picture.grab_focus()
            exit_chord()
            stage, stamp = 'exit', time.monotonic()
        elif stage == 'exit' and time.monotonic() - stamp > 1:
            assert not w.is_fullscreen(), 'Ctrl+Alt+Enter did not exit fullscreen'
            assert w.header.get_visible() and w.connection_bar.get_visible()
            assert w.control.get_active(), 'Exit should preserve remote control'
            assert w.views[w.selected].title_bar.get_visible()
            assert w.views[w.selected].picture.get_content_fit() == Gtk.ContentFit.CONTAIN
            assert any(m['type'] == 'release' for m in sent), 'Remote modifiers must be released'
            assert not any(m['type'] == 'key' and m['key'] == Gdk.KEY_Return for m in sent), 'Exit Enter leaked remotely'
            report['checks'].append({'monitor': w.selected, 'native_exit_chord': 'passed'})
            index += 1
            if index < len(connectors):
                w.views[connectors[index]].expand_button.emit('clicked')
                stage, stamp = 'fullscreen', time.monotonic()
            else:
                w.select(None)
                stage, stamp = 'overview', time.monotonic()
        elif stage == 'overview' and time.monotonic() - stamp > 1:
            assert all(v.get_visible() and v.picture.get_paintable() for v in w.views.values())
            assert w.tabs.get_visible() == (len(connectors) > 1)
            report['checks'].append({'overview_restored': len(connectors)})
            w.disconnect()
            app.quit()
            return False
    except Exception as exc:
        report['errors'].append(f'{stage}: {exc!r}')
        w.disconnect()
        app.quit()
        return False
    return True
GLib.timeout_add(250, tick)
app.run(None)
call(session, 'Stop')
print(json.dumps(report), flush=True)
Path.home().joinpath('.cache/ssh-screen-manager-fullscreen-report.json').write_text(json.dumps(report, indent=2))
raise SystemExit(bool(report['errors']))
