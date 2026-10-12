"""Length-prefixed JSON and optional JPEG payload. No pickle, shell or socket."""

import json
import math
import re
import struct

VERSION = 1
MAX_HEADER = 65536
MAX_PAYLOAD = 16 * 1024 * 1024
PROFILE_ALIASES = {"leve": "low-bandwidth", "equilibrada": "balanced", "detalhada": "sharp"}
PROFILES = {
    "low-bandwidth": (1024, 12, 65),
    "balanced": (1600, 20, 82),
    "sharp": (3840, 15, 92),
}


def read_exact(stream, size):
    parts = bytearray()
    while len(parts) < size:
        part = stream.read(size - len(parts))
        if not part:
            raise EOFError("Connection closed")
        parts.extend(part)
    return bytes(parts)


def read_packet(stream):
    length = struct.unpack("!I", read_exact(stream, 4))[0]
    if not 0 < length <= MAX_HEADER:
        raise ValueError("Invalid header")
    header = json.loads(read_exact(stream, length))
    if not isinstance(header, dict):
        raise ValueError("Invalid message")
    size = header.get("bytes", 0)
    if type(size) is not int or not 0 <= size <= MAX_PAYLOAD:
        raise ValueError("Invalid image size")
    return header, read_exact(stream, size) if size else b""


def write_packet(stream, header, payload=b""):
    header = dict(header, bytes=len(payload))
    data = json.dumps(header, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > MAX_HEADER or len(payload) > MAX_PAYLOAD:
        raise ValueError("Message too large")
    view = memoryview(struct.pack("!I", len(data)) + data + payload)
    while view:
        written = stream.write(view)
        if not written:
            raise OSError("Connection closed while sending")
        view = view[written:]
    stream.flush()


def validate_host(host):
    # A host/SSH alias, never options or a remote shell command.
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.:@%\[\]-]{0,252}", host):
        raise ValueError("Enter a valid SSH alias, IP address, or user@host.")
    return host


def ssh_command(host, profile):
    validate_host(host)
    if profile not in PROFILES:
        raise ValueError("Invalid quality profile")
    return [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=yes", "-o", "Compression=no",
        host, f"exec ~/.local/bin/ssh-screen-manager --serve --profile {profile}",
    ]


def normalized_point(x, y, area_width, area_height, image_width, image_height, clamp=False, fill=False):
    """Map contain/fill image coordinates using exactly the GTK picture's fit."""
    if min(area_width, area_height, image_width, image_height) <= 0:
        return None
    scale = min(area_width / image_width, area_height / image_height)
    width, height = (area_width, area_height) if fill else (image_width * scale, image_height * scale)
    nx = (x - (area_width - width) / 2) / width
    ny = (y - (area_height - height) / 2) / height
    if not clamp and not (0 <= nx <= 1 and 0 <= ny <= 1):
        return None
    return max(0, min(nx, 1)), max(0, min(ny, 1))


def unit(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Invalid coordinate")
    return float(value)
