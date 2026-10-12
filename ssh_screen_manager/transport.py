"""Bounded, asynchronous SSH transport, shared by the GUI and integration checks."""

import collections
import json
import subprocess
import threading
import time

from .protocol import VERSION, read_packet, ssh_command


class Connection:
    def __init__(self, host, profile, command=None):
        self.command = command or ssh_command(host, profile)
        self.process = None
        self.frames = {}
        self.events = collections.deque(maxlen=64)
        self.lock = threading.Lock()
        self.condition = threading.Condition()
        self.commands = collections.deque()
        self.closed = False
        self.intentional = False
        self.stderr = collections.deque(maxlen=16)
        self.bytes_received = 0
        self.frame_count = 0
        self.last_frame = 0
        self.latency_ms = None
        self.ready = False
        threading.Thread(target=self.run, daemon=True).start()

    def event(self, event):
        with self.lock:
            self.events.append(event)

    def send(self, command):
        with self.condition:
            if self.closed:
                return
            if len(self.commands) >= 512:
                self.event({"type": "error", "message": "The connection is too slow to receive input. Please reconnect."})
                self.close()
                return
            if self.commands and command.get("type") == "move" and self.commands[-1].get("type") == "move":
                self.commands[-1] = command
            else:
                self.commands.append(command)
            self.condition.notify()

    def drain(self):
        with self.lock:
            events, frames = list(self.events), self.frames
            self.events.clear()
            self.frames = {}
        return events, frames

    def run(self):
        try:
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, bufsize=0)
            if self.closed:
                self.process.terminate()
                return
            threading.Thread(target=self.write, daemon=True).start()
            threading.Thread(target=self.read_stderr, daemon=True).start()
            while not self.closed:
                header, data = read_packet(self.process.stdout)
                kind = header.get("type")
                if kind == "hello":
                    if header.get("version") != VERSION:
                        raise ValueError("Incompatible versions. Update SSH Screen Manager on both computers.")
                    self.ready = True
                    self.event(header)
                elif kind == "frame":
                    with self.lock:
                        self.frames[header["monitor"]] = (header, data)
                        self.bytes_received += len(data)
                        self.frame_count += 1
                        self.last_frame = time.monotonic()
                elif kind == "pong":
                    stamp = header.get("time")
                    if isinstance(stamp, (int, float)):
                        self.latency_ms = round((time.monotonic() - stamp) * 1000)
                else:
                    self.event(header)
        except (OSError, EOFError, ValueError, KeyError) as exc:
            if not self.intentional:
                self.event({"type": "transport-error", "message": str(exc)})
        finally:
            self.closed = True
            with self.condition:
                self.condition.notify_all()
            if self.process:
                if self.process.stdin:
                    self.process.stdin.close()
                try:
                    self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
            self.event({"type": "disconnected", "details": "\n".join(self.stderr),
                        "intentional": self.intentional})

    def read_stderr(self):
        for line in iter(self.process.stderr.readline, b""):
            self.stderr.append(line.decode(errors="replace").strip()[:1000])

    def write(self):
        try:
            while not self.closed:
                with self.condition:
                    self.condition.wait_for(lambda: self.closed or self.commands, timeout=5)
                    if self.closed:
                        return
                    command = self.commands.popleft() if self.commands else {"type": "ping", "time": time.monotonic()}
                data = (json.dumps(command, allow_nan=False, separators=(",", ":")) + "\n").encode()
                # FileIO.write can be partial even on a pipe.
                view = memoryview(data)
                while view:
                    count = self.process.stdin.write(view)
                    if not count:
                        raise OSError("SSH connection closed")
                    view = view[count:]
        except (OSError, ValueError):
            self.close(intentional=False)

    def close(self, intentional=True):
        self.intentional = intentional
        self.closed = True
        with self.condition:
            self.condition.notify_all()
        if self.process and self.process.poll() is None:
            self.process.terminate()
