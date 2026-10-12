"""Capture through the actual agent, with no input injected into the desktop."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ssh_screen_manager.transport import Connection

parser = argparse.ArgumentParser()
parser.add_argument("--host")
parser.add_argument("--via", help="SSH host that should open the connection, to test access from another computer")
parser.add_argument("--output", default="evidence")
parser.add_argument("--profile", default="low-bandwidth")
args = parser.parse_args()
output = Path(args.output)
output.mkdir(parents=True, exist_ok=True)
command = None if args.host else [sys.executable, "-m", "ssh_screen_manager", "--serve", "--profile", args.profile]
if args.via:
    from ssh_screen_manager.protocol import ssh_command
    import shlex
    command = ["ssh", "-T", "-o", "BatchMode=yes", args.via, shlex.join(ssh_command(args.host, args.profile))]
c = Connection(args.host or "local", args.profile, command=command)
received = {}
monitors = []
errors = []
start = time.monotonic()
try:
    while time.monotonic() - start < 18 and not c.closed:
        events, frames = c.drain()
        for e in events:
            if e["type"] == "hello":
                monitors = e["monitors"]
                print(json.dumps(e), flush=True)
            elif e["type"] in ("error", "transport-error"):
                errors.append(e)
                print(e, flush=True)
        for name, (header, data) in frames.items():
            if name not in received:
                output.joinpath(f"{name}.jpg").write_bytes(data)
            received[name] = received.get(name, 0) + 1
        if monitors and len(received) == len(monitors) and time.monotonic() - start > 5:
            break
        time.sleep(0.05)
finally:
    c.close()
    if c.process:
        c.process.wait(timeout=5)
report = {"monitors": monitors, "frames": received, "bytes": c.bytes_received,
          "elapsed": round(time.monotonic()-start, 2), "errors": errors, "stderr": list(c.stderr)}
output.joinpath("capture.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
raise SystemExit(0 if len(received) == len(monitors) > 0 and not errors else 1)
