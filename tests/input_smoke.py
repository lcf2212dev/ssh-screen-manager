"""Send input over SSH to a dedicated window on each screen of a computer you can access."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssh_screen_manager.protocol import ssh_command
from ssh_screen_manager.transport import Connection

root = Path(__file__).resolve().parents[1]
cli = argparse.ArgumentParser(description="Send input over SSH to a dedicated window on each remote screen.")
cli.add_argument("--via", required=True, help="SSH host that runs this two-hop check")
cli.add_argument("--host", required=True, help="Computer to view")
cli = cli.parse_args()
command = ["ssh", "-T", "-o", "BatchMode=yes", cli.via, shlex.join(ssh_command(cli.host, "low-bandwidth"))]
c = Connection(cli.host, "low-bandwidth", command=command)
monitors = []
deadline = time.monotonic() + 15
while time.monotonic() < deadline and not monitors:
    events, _ = c.drain()
    for event in events:
        if event["type"] == "hello":
            monitors = event["monitors"]
        elif event["type"] == "error":
            raise RuntimeError(event)
    time.sleep(0.05)
assert monitors, "The computer did not report any screens"
results = []
try:
    for monitor in monitors:
        name = monitor["id"]
        report = root / "evidence" / f"input-{name}.jsonl"
        report.unlink(missing_ok=True)
        target = subprocess.Popen([sys.executable, str(root / "tests/input_target.py"), name, str(report)])
        try:
            deadline = time.monotonic() + 7
            while time.monotonic() < deadline:
                if report.exists() and '"ready"' in report.read_text():
                    break
                time.sleep(0.1)
            assert report.exists() and '"ready"' in report.read_text(), "Target not ready"
            c.send({"type": "select", "monitors": [name]})
            time.sleep(0.3)
            for fraction in (0.2, 0.5, 0.8):
                c.send({"type": "move", "monitor": name, "x": fraction, "y": fraction})
                c.send({"type": "button", "button": 272, "down": True})
                c.send({"type": "button", "button": 272, "down": False})
                time.sleep(0.2)
            for key in (ord("H"), ord("e"), ord("l"), ord("o"), 0xE1, 0xE7):
                c.send({"type": "key", "key": key, "down": True})
                c.send({"type": "key", "key": key, "down": False})
            c.send({"type": "scroll", "dx": 0, "dy": 30})
            # Hold a modifier then release all, as on loss of client focus.
            c.send({"type": "key", "key": 0xFFE3, "down": True})
            c.send({"type": "release"})
            time.sleep(0.8)
            records = [json.loads(line) for line in report.read_text().splitlines()]
            presses = [r for r in records if r["type"] == "press"]
            keys = [r["key"] for r in records if r["type"] == "key-down"]
            errors = []
            if len(presses) != 3:
                errors.append(f"Expected 3 clicks, received {len(presses)}")
            for press, fraction in zip(presses, (0.2, 0.5, 0.8)):
                for axis, size in (("x", "width"), ("y", "height")):
                    expected = (monitor["logical_" + size] - 1) * fraction
                    if abs(press[axis] - expected) > 3:
                        errors.append(f"{axis}: {press[axis]} != {expected}")
            for key in (ord("H"), ord("e"), ord("l"), ord("o"), 0xE1, 0xE7, 0xFFE3):
                if key not in keys:
                    errors.append(f"Missing key {key}")
            if not any(r["type"] == "scroll" for r in records):
                errors.append("Missing scroll")
            if not any(r["type"] == "key-up" and r["key"] == 0xFFE3 for r in records):
                errors.append("Modifier not released")
            result = {"monitor": name, "clicks": presses, "keys": keys, "errors": errors}
            results.append(result)
            print(json.dumps(result), flush=True)
        finally:
            target.terminate()
            target.wait(timeout=5)
finally:
    c.close()
    if c.process:
        c.process.wait(timeout=5)
root.joinpath("evidence/input-report.json").write_text(json.dumps(results, indent=2))
assert all(not r["errors"] for r in results), results
