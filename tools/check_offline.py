"""Run the real parser with all socket connections denied in this process."""

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import socket
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--gazetteer", type=Path, default=Path("data/public/gazetteer.sqlite"))
    cli.add_argument("--input", type=Path, required=True)
    args = cli.parse_args()
    records = json.loads(args.input.read_text(encoding="utf-8"))

    def denied(*_args, **_kwargs):
        raise AssertionError("NETWORK_ACCESS_ATTEMPT")

    # Imports and artifact initialization are also inside the no-network boundary.
    with ExitStack() as stack:
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection", "socket.getaddrinfo"):
            stack.enter_context(patch(target, side_effect=denied))
        from jana.gazetteer import Gazetteer
        from jana.parser import parse_batch
        from jana.contracts import validate_batch, validate_output
        with Gazetteer(args.gazetteer) as catalog:
            inputs = validate_batch(records)
            result = validate_output(inputs, parse_batch(inputs, gazetteer=catalog))
    print(json.dumps({"scope": "Python imports + gazetteer initialization + parser; not a Docker network namespace test", "network": "denied", "records": len(result), "status": "PASS"}))


if __name__ == "__main__":
    main()
