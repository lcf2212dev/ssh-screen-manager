"""GNOME session agent. Lives only as long as the authenticated SSH stdio session."""

import collections
import json
import math
import os
import signal
import sys
import threading
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst

from .protocol import PROFILES, VERSION, unit, write_packet

RD = "org.gnome.Mutter.RemoteDesktop"
SC = "org.gnome.Mutter.ScreenCast"
DC = "org.gnome.Mutter.DisplayConfig"


class Outbox:
    """One latest frame per monitor; a slow network cannot accumulate video."""

    def __init__(self, stop):
        self.condition = threading.Condition()
        self.messages = collections.deque(maxlen=64)
        self.frames = {}
        self.closed = False
        self.stop = stop
        # Raw fd: a daemon thread blocked in buffered stdout would abort Python
        # during interpreter shutdown if the SSH consumer disappeared.
        self.output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
        threading.Thread(target=self.run, daemon=True).start()

    def send(self, header, payload=b""):
        with self.condition:
            if header["type"] == "frame":
                self.frames[header["monitor"]] = (header, payload)
            else:
                self.messages.append((header, payload))
            self.condition.notify()

    def run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.messages or self.frames)
                    if self.closed:
                        return
                    if self.messages:
                        packet = self.messages.popleft()
                    else:
                        key = next(iter(self.frames))
                        packet = self.frames.pop(key)
                write_packet(self.output, *packet)
        except (OSError, ValueError):
            GLib.idle_add(self.stop)


class Server:
    def __init__(self, profile):
        uid = os.getuid()
        os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
        os.environ.setdefault("GST_GL_PLATFORM", "egl")
        os.environ.setdefault("GST_GL_WINDOW", "surfaceless")
        Gst.init(None)
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.loop = GLib.MainLoop()
        self.out = Outbox(self.stop)
        self.profile = profile
        self.session = None
        self.streams = {}
        self.monitors = []
        self.pipelines = {}
        self.subscriptions = []
        self.keys = set()
        self.buttons = set()
        self.last_ping = time.monotonic()
        self.stopping = False
        self.started = False
        self.commands = collections.deque()
        self.command_lock = threading.Lock()
        self.error = None
        self.selected = None

    def call(self, service, path, interface, method, signature=None, args=()):
        params = GLib.Variant(signature, args) if signature else None
        return self.bus.call_sync(service, path, interface, method, params, None,
                                  Gio.DBusCallFlags.NONE, 5000, None).unpack()

    def rd(self, method, signature=None, args=()):
        return self.call(RD, self.session, RD + ".Session", method, signature, args)

    def subscribe(self, service, interface, signal_name, path, callback):
        self.subscriptions.append(self.bus.signal_subscribe(
            service, interface, signal_name, path, None, Gio.DBusSignalFlags.NONE, callback))

    def setup(self):
        state = self.call(DC, "/org/gnome/Mutter/DisplayConfig", DC, "GetCurrentState")
        active = {spec[0] for lm in state[2] for spec in lm[5]}
        physical = {spec[0]: (modes, props) for spec, modes, props in state[1]}
        if not active:
            raise RuntimeError("No active screens. Sign in to GNOME on the remote computer.")
        self.session = self.call(RD, "/org/gnome/Mutter/RemoteDesktop", RD, "CreateSession")[0]
        sid = self.call(RD, self.session, "org.freedesktop.DBus.Properties", "Get",
                        "(ss)", (RD + ".Session", "SessionId"))[0]
        self.cast = self.call(SC, "/org/gnome/Mutter/ScreenCast", SC, "CreateSession",
                              "(a{sv})", ({"remote-desktop-session-id": GLib.Variant("s", sid)},))[0]
        for connector in sorted(active):
            path = self.call(SC, self.cast, SC + ".Session", "RecordMonitor", "(sa{sv})",
                             (connector, {"cursor-mode": GLib.Variant("u", 1)}))[0]
            params = self.call(SC, path, "org.freedesktop.DBus.Properties", "Get",
                               "(ss)", (SC + ".Stream", "Parameters"))[0]
            modes, props = physical[connector]
            mode = next(m for m in modes if m[-1].get("is-current"))
            lm = next(l for l in state[2] if any(s[0] == connector for s in l[5]))
            monitor = {"id": connector, "name": props.get("display-name", connector),
                       "width": mode[1], "height": mode[2], "primary": lm[4],
                       "x": params["position"][0], "y": params["position"][1],
                       "logical_width": params["size"][0], "logical_height": params["size"][1]}
            rotated = lm[3] in (1, 3, 5, 7)
            monitor["input_width"] = mode[2] if rotated else mode[1]
            monitor["input_height"] = mode[1] if rotated else mode[2]
            self.monitors.append(monitor)
            self.streams[path] = monitor
            self.subscribe(SC, SC + ".Stream", "PipeWireStreamAdded", path, self.pipewire_added)
        self.monitors.sort(key=lambda m: (m["x"], m["y"]))
        self.subscribe(RD, RD + ".Session", "Closed", self.session,
                       lambda *a: self.fail("GNOME stopped screen sharing. Reconnect to continue."))
        self.subscribe(DC, DC, "MonitorsChanged", "/org/gnome/Mutter/DisplayConfig",
                       lambda *a: self.fail("The display layout changed. Reconnect to refresh it."))
        self.out.send({"type": "hello", "version": VERSION, "monitors": self.monitors,
                       "hostname": os.uname().nodename, "profile": self.profile})
        self.rd("Start")
        self.started = True
        threading.Thread(target=self.read_commands, daemon=True).start()
        GLib.timeout_add(8, self.drain_commands)
        GLib.timeout_add_seconds(5, self.watchdog)
        GLib.timeout_add_seconds(15, self.check_frames)

    def pipewire_added(self, bus, sender, path, interface, name, args):
        try:
            monitor = self.streams[path]
            width, fps, quality = PROFILES[self.profile]
            # Stream geometry respects monitor rotation and fractional scale.
            aspect = monitor["logical_height"] / monitor["logical_width"]
            width = min(width, max(monitor["width"], monitor["height"]))
            height = max(2, round(width * aspect / 2) * 2)
            monitor["frame_width"], monitor["frame_height"] = width, height
            pipeline = Gst.parse_launch(
                f'pipewiresrc name=source path={args.unpack()[0]} do-timestamp=true keepalive-time=1000 '
                'use-bufferpool=true min-buffers=4 max-buffers=16 '
                '! queue max-size-buffers=1 leaky=downstream '
                '! identity name=throttle '
                '! glupload ! glcolorconvert ! gldownload ! video/x-raw,format=RGBA '
                f'! videoconvert ! videoscale ! video/x-raw,format=I420,width={width},height={height} '
                f'! jpegenc quality={quality} '
                '! appsink name=frames emit-signals=true sync=false async=false max-buffers=1 drop=true')
            monitor["last_encode"] = 0
            monitor["fps"] = fps
            pipeline.get_by_name("throttle").get_static_pad("src").add_probe(
                Gst.PadProbeType.BUFFER, self.throttle, monitor)
            sink = pipeline.get_by_name("frames")
            sink.connect("new-sample", self.sample, monitor)
            gstbus = pipeline.get_bus()
            gstbus.add_signal_watch()
            gstbus.connect("message::error", self.pipeline_error, monitor["id"])
            self.pipelines[monitor["id"]] = pipeline
            pipeline.set_state(Gst.State.PLAYING)
        except Exception as exc:
            self.fail(f"Capture failed: {exc}")

    def throttle(self, pad, info, monitor):
        # Never hold a PipeWire buffer waiting for the next frame, nor stop an
        # inactive monitor's source. Static desktops have variable frame timing.
        monitor["input_frames"] = monitor.get("input_frames", 0) + 1
        if self.selected is not None and monitor["id"] not in self.selected:
            return Gst.PadProbeReturn.DROP
        now = time.monotonic()
        if now - monitor["last_encode"] < 1 / monitor["fps"]:
            return Gst.PadProbeReturn.DROP
        monitor["last_encode"] = now
        return Gst.PadProbeReturn.OK

    def sample(self, sink, monitor):
        sample = sink.emit("pull-sample")
        if sample:
            # Mutter absolute input uses original capture pixels, NOT logical
            # desktop coordinates and NOT the resized JPEG dimensions.
            caps = self.pipelines[monitor["id"]].get_by_name("source").get_static_pad("src").get_current_caps()
            if caps:
                structure = caps.get_structure(0)
                monitor["input_width"] = structure.get_value("width")
                monitor["input_height"] = structure.get_value("height")
            buffer = sample.get_buffer()
            data = buffer.extract_dup(0, buffer.get_size())
            monitor["received"] = True
            self.out.send({"type": "frame", "monitor": monitor["id"],
                           "width": monitor["frame_width"], "height": monitor["frame_height"]}, data)
        return Gst.FlowReturn.OK

    def pipeline_error(self, bus, message, connector):
        err, debug = message.parse_error()
        print(f"{connector}: {err}; {debug}", file=sys.stderr)
        self.fail(f"Could not capture {connector}: {err.message}")

    def check_frames(self):
        missing = [m["id"] for m in self.monitors if not m.get("received")
                   and (self.selected is None or m["id"] in self.selected)]
        if missing:
            for name in missing:
                pipeline = self.pipelines.get(name)
                monitor = next(m for m in self.monitors if m["id"] == name)
                if pipeline:
                    caps = pipeline.get_by_name("source").get_static_pad("src").get_current_caps()
                    print(f"{name}: state={pipeline.get_state(0)} input_frames={monitor.get('input_frames', 0)} caps={caps.to_string() if caps else None}", file=sys.stderr)
            self.fail("No image from " + ", ".join(missing) + ". Check the GNOME session and PipeWire.")
        return False

    def read_commands(self):
        try:
            pending = bytearray()
            while True:
                data = os.read(sys.stdin.fileno(), 8192)
                if not data:
                    break
                pending.extend(data)
                while b"\n" in pending:
                    line, _, rest = pending.partition(b"\n")
                    pending = bytearray(rest)
                    if len(line) > 8192:
                        raise ValueError("Command too large")
                    command = json.loads(line)
                    if not isinstance(command, dict):
                        raise ValueError("Invalid command")
                    with self.command_lock:
                        if len(self.commands) >= 512:
                            raise ValueError("Command queue limit exceeded")
                        # Coalesce only consecutive motion, preserving input order.
                        if self.commands and command.get("type") == "move" and self.commands[-1].get("type") == "move":
                            self.commands[-1] = command
                        else:
                            self.commands.append(command)
                if len(pending) > 8192:
                    raise ValueError("Command too large")
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
        finally:
            GLib.idle_add(self.stop)

    def drain_commands(self):
        with self.command_lock:
            commands = list(self.commands)
            self.commands.clear()
        for command in commands:
            try:
                self.handle(command)
            except Exception as exc:
                self.fail(f"Remote control failed: {exc}")
                return False
        return not self.stopping

    def handle(self, c):
        kind = c.get("type")
        self.last_ping = time.monotonic()
        if kind == "ping":
            self.out.send({"type": "pong", "time": c.get("time")})
        elif kind == "select":
            ids = c.get("monitors")
            if not isinstance(ids, list) or not set(ids) <= {m["id"] for m in self.monitors}:
                raise ValueError("Invalid screen")
            self.release()
            self.selected = ids
        elif kind == "move":
            path, m = next((p, m) for p, m in self.streams.items() if m["id"] == c["monitor"])
            self.rd("NotifyPointerMotionAbsolute", "(sdd)",
                    (path, unit(c["x"]) * (m["input_width"] - 1),
                     unit(c["y"]) * (m["input_height"] - 1)))
        elif kind == "button":
            button, down = c["button"], c["down"]
            if type(button) is not int or button not in (272, 273, 274, 275, 276) or type(down) is not bool:
                raise ValueError("Invalid button")
            self.rd("NotifyPointerButton", "(ib)", (button, down))
            (self.buttons.add if down else self.buttons.discard)(button)
        elif kind == "key":
            key, down = c["key"], c["down"]
            if type(key) is not int or not 1 <= key <= 0x1FFFFFFF or type(down) is not bool:
                raise ValueError("Invalid key")
            # Key repeat is generated by Mutter, not repeated press notifications.
            if down and key in self.keys:
                return
            self.rd("NotifyKeyboardKeysym", "(ub)", (key, down))
            (self.keys.add if down else self.keys.discard)(key)
        elif kind == "scroll":
            dx, dy = c["dx"], c["dy"]
            if any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 1000 for v in (dx, dy)):
                raise ValueError("Invalid scroll value")
            self.rd("NotifyPointerAxis", "(ddu)", (float(dx), float(dy), 2))
        elif kind == "release":
            self.release()
        elif kind == "bye":
            self.stop()
        else:
            raise ValueError("Unknown command")

    def release(self):
        for key in list(self.keys):
            self.rd("NotifyKeyboardKeysym", "(ub)", (key, False))
            self.keys.discard(key)
        for button in list(self.buttons):
            self.rd("NotifyPointerButton", "(ib)", (button, False))
            self.buttons.discard(button)

    def watchdog(self):
        if time.monotonic() - self.last_ping > 20:
            self.fail("No activity for 20 seconds; remote control stopped.")
        return not self.stopping

    def fail(self, message):
        if not self.stopping:
            self.error = message
            self.out.send({"type": "error", "message": message})
            print(message, file=sys.stderr)
            GLib.timeout_add(150, self.stop)
        return False

    def stop(self):
        if self.stopping:
            return False
        self.stopping = True
        if self.started:
            try:
                self.release()
            except GLib.Error:
                pass
        for pipeline in self.pipelines.values():
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_bus().remove_signal_watch()
        if self.session:
            try:
                self.rd("Stop")
            except GLib.Error:
                pass
        for sid in self.subscriptions:
            self.bus.signal_unsubscribe(sid)
        self.loop.quit()
        return False

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self.stop)
        try:
            self.setup()
            self.loop.run()
        except Exception as exc:
            self.error = str(exc)
            self.out.send({"type": "error", "message": f"Sign in to GNOME Wayland on the remote computer. Details: {exc}"})
            print(str(exc), file=sys.stderr)
            time.sleep(0.1)
        finally:
            self.stop()
        return 1 if self.error else 0
