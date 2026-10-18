"""Verify preloaded Docker image startup and a 1000-record HTTP benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import socket
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen


def read_health() -> dict | None:
    """Return readiness JSON, treating cold-start disconnects as retryable."""

    try:
        with urlopen("http://127.0.0.1:8080/health/ready", timeout=2) as response:
            return json.load(response)
    except (URLError, TimeoutError, ConnectionError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("output/container-verification.json"))
    args = parser.parse_args()
    subprocess.run(["docker", "version"], check=True, stdout=subprocess.DEVNULL)
    # Never call an already running service a cold start or stop someone else's app.
    running = subprocess.run(["docker", "compose", "ps", "--status", "running", "--quiet"],
                             check=True, capture_output=True, text=True).stdout.strip()
    if running:
        raise SystemExit("Stop this project's running container before measuring a cold start.")
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8080)) == 0:
            raise SystemExit("Port 8080 is occupied; will not benchmark an unrelated or warm service.")
    started = time.perf_counter()
    subprocess.run(["docker", "compose", "up", "-d", "--no-build"], check=True, timeout=180)
    health = None
    while time.perf_counter() - started <= 180:
        health = read_health()
        if health and health.get("service") == "jana-address" and health.get("status") == "ready":
            break
        time.sleep(0.5)
    elapsed = time.perf_counter() - started
    if not health or health.get("status") != "ready" or elapsed > 180:
        raise SystemExit("Docker readiness limit exceeded")
    benchmark = args.report.with_name("container-batch.json")
    subprocess.run([sys.executable, str(Path(__file__).with_name("benchmark.py")), "--input", str(args.input), "--report", str(benchmark)], check=True)
    result = json.loads(benchmark.read_text(encoding="utf-8"))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"startup_seconds": elapsed, "startup_limit_pass": elapsed <= 180, "precondition": "preloaded image, no build or pull", "batch": result}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"startup_seconds": elapsed, "batch_pass": result["batch_1000_under_300s"]}))
    return 0 if result["batch_1000_under_300s"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
