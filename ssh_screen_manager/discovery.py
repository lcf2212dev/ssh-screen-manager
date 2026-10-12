"""Find trusted SSH peers and check readiness without starting screen capture."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import glob
import ipaddress
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess

from .protocol import VERSION, validate_host


@dataclass
class Device:
    host: str
    name: str
    ready: bool = False
    detail: str = "Checking…"
    screens: int = 0
    identity: str = ""


def display_name(host):
    name = host.rsplit("@", 1)[-1].split(".")[0]
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        return name.replace("-", " ").replace("_", " ").capitalize()


def command_output(command, timeout=4):
    result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=timeout, check=False)
    return result.stdout if result.returncode == 0 else ""


def ssh_aliases(path, visited=None):
    """Read literal Host entries and Include files; never expand wildcard hosts."""
    visited = set() if visited is None else visited
    path = Path(path).expanduser().resolve()
    if path in visited:
        return []
    visited.add(path)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    hosts = []
    for line in lines:
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            continue
        if not words:
            continue
        # OpenSSH accepts both "Host name" and "Host=name".
        if "=" in words[0]:
            key, value = words.pop(0).split("=", 1)
            words[:0] = [key, value]
        key, values = words[0].lower(), [w for w in words[1:] if w != "="]
        if key == "host":
            hosts.extend(h for h in values if not any(c in h for c in "*?!"))
        elif key == "include":
            for pattern in values:
                pattern = Path(pattern).expanduser()
                if not pattern.is_absolute():
                    base = Path("/etc/ssh") if str(path).startswith("/etc/ssh/") else Path.home() / ".ssh"
                    pattern = base / pattern
                for included in sorted(glob.glob(str(pattern))):
                    hosts.extend(ssh_aliases(included, visited))
    return hosts


def known_hosts(path):
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return []
    hosts = []
    for line in lines:
        words = line.split()
        if not words or words[0].startswith(("#", "@", "|")):
            continue
        hosts.extend(h for h in words[0].split(",") if not any(c in h for c in "*?!["))
    return hosts


def advertised_hosts():
    # Browsing advertisements is optional. Authentication still requires a
    # previously trusted SSH identity and a working key, just like connection.
    try:
        output = command_output(["avahi-browse", "-artp"], timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return []
    result = []
    for line in output.splitlines():
        fields = line.split(";")
        if len(fields) > 8 and fields[0] == "=" and fields[4] in (
                "_ssh-screen-manager._tcp", "_ssh._tcp", "SSH Remote Terminal", "_workstation._tcp", "Workstation"):
            result.append(fields[6])
    return result


def effective_target(host, explicit=False):
    """Resolve OpenSSH aliases, deduplicate them, and skip unrelated Internet hosts."""
    validate_host(host)
    config = {}
    for line in command_output(["ssh", "-G", host], timeout=3).splitlines():
        key, _, value = line.partition(" ")
        config.setdefault(key, value)
    hostname = config.get("hostname", host.rsplit("@", 1)[-1])
    local = socket.gethostname().lower()
    if hostname.lower() in (local, local.split(".")[0], local.split(".")[0] + ".home",
                            local.split(".")[0] + ".local", "localhost"):
        return None
    addresses = []
    for line in command_output(["getent", "ahosts", hostname], timeout=3).splitlines():
        try:
            addresses.append(ipaddress.ip_address(line.split()[0]))
        except (ValueError, IndexError):
            pass
    if any(a.is_loopback for a in addresses):
        return None
    if not explicit and not any(a.is_private and not a.is_link_local for a in addresses):
        return None
    # The probe below also drops our own machine if it was found by LAN IP.
    return (hostname.lower(), config.get("user", ""), config.get("port", "22"))


def probe(host):
    validate_host(host)
    device = Device(host, display_name(host), detail="Unavailable")
    command = ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=3",
               "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes",
               "-o", "ClearAllForwardings=yes", host, "exec ~/.local/bin/ssh-screen-manager --check"]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=8, check=False)
        if result.returncode:
            if "Permission denied" in result.stderr:
                device.detail = "Access not configured"
            elif "Host key verification failed" in result.stderr:
                device.detail = "Identity not verified"
            elif "not found" in result.stderr or "No such file" in result.stderr:
                device.detail = "SSH Screen Manager not installed"
            elif "unrecognized arguments" in result.stderr:
                device.detail = "Update SSH Screen Manager"
            return device
        payload = json.loads(result.stdout)
        if payload.get("app") != "ssh-screen-manager" or payload.get("protocol") != VERSION:
            device.detail = "Update SSH Screen Manager"
            return device
        device.name = display_name(str(payload.get("hostname", host)))
        device.ready = payload.get("ready") is True
        device.screens = max(0, int(payload.get("screens", 0)))
        device.identity = str(payload.get("identity", ""))
        device.detail = (f"Available · {device.screens} " + ("screen" if device.screens == 1 else "screens")) if device.ready else str(payload.get("reason", "Session unavailable"))
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        pass
    return device


def local_identity():
    try:
        return Path("/etc/machine-id").read_text().strip() + ":" + str(os.getuid())
    except OSError:
        return socket.gethostname() + ":" + str(os.getuid())


def discover(saved=()):
    saved = list(dict.fromkeys(h for h in saved if isinstance(h, str) and h))
    candidates = list(dict.fromkeys(saved + ssh_aliases(Path.home() / ".ssh/config")
                                    + ssh_aliases("/etc/ssh/ssh_config")
                                    + known_hosts(Path.home() / ".ssh/known_hosts") + advertised_hosts()))
    targets, results = {}, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        pending = {pool.submit(effective_target, h, h in saved): h for h in candidates}
        resolved = {}
        for future in as_completed(pending):
            try:
                resolved[pending[future]] = future.result()
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
        # Prefer saved aliases over IPs and preserve the configured SSH identity.
        for host in candidates:
            target = resolved.get(host)
            if target and target not in targets:
                targets[target] = host
        pending = {pool.submit(probe, host): host for host in targets.values()}
        for future in as_completed(pending):
            device = future.result()
            if device.identity == local_identity():
                continue
            if device.ready or device.host in saved:
                results.append(device)
    unique = {}
    for device in sorted(results, key=lambda d: (d.host not in saved, not d.ready, d.name)):
        unique.setdefault(device.identity or device.host, device)
    return sorted(unique.values(), key=lambda d: (not d.ready, d.name.casefold()))


def check_local():
    """Read session state only: no CreateSession, capture, or input injection."""
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gio, GLib, Gst
    os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{os.getuid()}/bus")
    result = dict(app="ssh-screen-manager", protocol=VERSION, hostname=socket.gethostname(),
                  identity=local_identity(), ready=False, screens=0, reason="Sign in to GNOME")
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        def call(name, path, interface, method, params=None):
            return bus.call_sync(name, path, interface, method, params, None,
                                 Gio.DBusCallFlags.NONE, 1500, None).unpack()
        for service in ("org.gnome.Mutter.RemoteDesktop", "org.gnome.Mutter.ScreenCast"):
            if not call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                        "NameHasOwner", GLib.Variant("(s)", (service,)))[0]:
                return result
        state = call("org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
                     "org.gnome.Mutter.DisplayConfig", "GetCurrentState")
        result["screens"] = len({spec[0] for monitor in state[2] for spec in monitor[5]})
        if not result["screens"]:
            result["reason"] = "No active screens"
            return result
        if call("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver", "GetActive")[0]:
            result["reason"] = "Unlock the computer"
            return result
        Gst.init(None)
        if not all(Gst.ElementFactory.find(name) for name in (
                "pipewiresrc", "glupload", "glcolorconvert", "gldownload", "jpegenc", "appsink")):
            result["reason"] = "Incomplete installation"
            return result
        result.update(ready=True, reason="Available")
    except (GLib.Error, OSError, ValueError):
        pass
    return result
