"""Measure a real HTTP batch through complete response, without printing addresses."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from jana.contracts import validate_batch, validate_output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--report", type=Path, default=Path("output/benchmark.json"))
    parser.add_argument("--predicted", type=Path)
    args = parser.parse_args()
    if not 1 <= args.runs <= 20:
        parser.error("--runs must be 1..20")
    payload = args.input.read_bytes()
    inputs = validate_batch(json.loads(payload))
    with urlopen(args.url.rstrip("/") + "/health/ready", timeout=10) as response:
        health = json.load(response)
    if health.get("status") != "ready" or health.get("service") != "jana-address":
        raise SystemExit("Jana Address is not ready")
    timings = []
    for _ in range(args.runs):
        request = Request(args.url.rstrip("/") + "/parse", data=payload, headers={"Content-Type": "application/json"})
        started = time.perf_counter()
        with urlopen(request, timeout=310) as response:
            result_bytes = response.read()
        timings.append(time.perf_counter() - started)
        outputs = validate_output(inputs, json.loads(result_bytes))
    revision = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
    report = {
        "kind": "real_http_full_response", "input_count": len(inputs), "output_count": len(outputs),
        "input_sha256": hashlib.sha256(payload).hexdigest(), "response_bytes": len(result_bytes),
        "runs_seconds": timings, "median_seconds": statistics.median(timings), "max_seconds": max(timings),
        "records_per_second": len(inputs) / statistics.median(timings),
        "batch_1000_under_300s": len(inputs) == 1000 and max(timings) <= 300,
        "environment": {"os": platform.platform(), "python": platform.python_version(), "cpu_count": os.cpu_count()},
        "git_commit": revision.stdout.strip(), "dirty_worktree": bool(dirty.stdout.strip()),
        "code_sha256": {
            name: hashlib.sha256((Path(__file__).resolve().parents[1] / "backend/jana" / name).read_bytes()).hexdigest()
            for name in ("parser.py", "normalization.py", "gazetteer.py", "contracts.py", "api.py")
        },
        "health": health, "startup_measurement": "health startup_seconds measures process initialization only, not Docker compose startup",
        "quality_claim": "No accuracy claim; evaluate predictions independently against an oracle.",
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.predicted:
        args.predicted.parent.mkdir(parents=True, exist_ok=True)
        args.predicted.write_text(json.dumps(outputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("input_count", "output_count", "median_seconds", "max_seconds", "batch_1000_under_300s")}, indent=2))
    return 0 if max(timings) <= 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
