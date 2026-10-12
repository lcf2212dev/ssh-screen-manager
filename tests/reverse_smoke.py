"""Verify capture and input on one screen of a computer you can reach over SSH."""
import argparse
import json
from pathlib import Path
import select
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssh_screen_manager.transport import Connection

root = Path(__file__).resolve().parents[1]
cli = argparse.ArgumentParser(description="Verify capture and input on one remote screen.")
cli.add_argument("--host", required=True, help="Computer to view")
cli.add_argument("--screen", required=True, help="Connector on that computer, for example eDP-1")
cli.add_argument("--width", type=int, required=True, help="Logical width of that screen")
cli.add_argument("--height", type=int, required=True, help="Logical height of that screen")
cli = cli.parse_args()
remote_script = "python ~/.cache/ssh-screen-manager-input-target.py"
subprocess.run(["scp", str(root / "tests/input_target.py"), f"{cli.host}:.cache/ssh-screen-manager-input-target.py"], check=True)
remote = " ".join(["WAYLAND_DISPLAY=wayland-0", remote_script, shlex.quote(cli.screen),
                   "~/.cache/ssh-screen-manager-reverse-input.jsonl", "--timeout", "20"])
target = subprocess.Popen([
    "ssh", "-T", "-o", "BatchMode=yes", cli.host, remote
], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
c = None
try:
    assert select.select([target.stdout], [], [], 8)[0], "Test window not ready"
    assert target.stdout.readline().strip() == b"READY"
    c = Connection(cli.host, "balanced")
    frames = 0
    monitors = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not frames:
        events, images = c.drain()
        for event in events:
            if event["type"] == "hello":
                monitors = event["monitors"]
            elif event["type"] in ("error", "transport-error"):
                raise RuntimeError(event)
        frames += len(images)
        time.sleep(0.05)
    assert frames and len(monitors) == 1 and monitors[0]["id"] == cli.screen
    c.send({"type": "select", "monitors": [cli.screen]})
    for fraction in (0.2, 0.5, 0.8):
        c.send({"type": "move", "monitor": cli.screen, "x": fraction, "y": fraction})
        c.send({"type": "button", "button": 272, "down": True})
        c.send({"type": "button", "button": 272, "down": False})
        time.sleep(0.2)
    # A physical ABNT keyboard sends dead_acute followed by a, not an
    # already-composed á keysym (which is absent from this host's keymap).
    expected_keys = [ord(k) for k in "SSH Screen Manager"] + [0xFE51, ord("a"), ord("ç")]
    for key in expected_keys:
        c.send({"type": "key", "key": key, "down": True})
        c.send({"type": "key", "key": key, "down": False})
    c.send({"type": "scroll", "dx": 0, "dy": 30})
    c.send({"type": "key", "key": 0xFFE3, "down": True})
    c.send({"type": "release"})
    time.sleep(0.8)
    content = subprocess.check_output(["ssh", "-o", "BatchMode=yes", cli.host, "cat ~/.cache/ssh-screen-manager-reverse-input.jsonl"], text=True)
    records = [json.loads(line) for line in content.splitlines()]
    presses = [r for r in records if r["type"] == "press"]
    assert len(presses) == 3, presses
    for press, fraction in zip(presses, (0.2, 0.5, 0.8)):
        assert abs(press["x"] - fraction * (cli.width - 1)) < 3, press
        assert abs(press["y"] - fraction * (cli.height - 1)) < 3, press
    keys = [r["key"] for r in records if r["type"] == "key-down"]
    assert all(key in keys for key in expected_keys), keys
    assert any(r["type"] == "scroll" for r in records)
    assert any(r["type"] == "key-up" and r["key"] == 0xFFE3 for r in records)
    report = {"direction": cli.host, "monitors": monitors, "clicks": presses,
              "keyboard_and_dead_key_sequence": True, "scroll": True, "modifier_release": True, "errors": []}
    root.joinpath("evidence/reverse-input-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
finally:
    if c:
        c.close()
    # The dedicated test window closes by itself after 20 seconds.
    try:
        target.wait(timeout=22)
    except subprocess.TimeoutExpired:
        target.terminate()
        target.wait(timeout=5)
