"""Parse a local UTF-8 NDJSON file using one existing offline gazetteer."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from jana.gazetteer import Gazetteer
from jana.streaming import StreamError, process_file


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise StreamError("INVALID_ARGUMENTS")


def emit(event, **details):
    print(json.dumps({"event": event, **details}, ensure_ascii=False, allow_nan=False),
          file=sys.stderr, flush=True)


def main(argv=None):
    arguments = _SafeArgumentParser(description=__doc__)
    arguments.add_argument("--input", type=Path, required=True, help="Local UTF-8 NDJSON file")
    arguments.add_argument("--output", type=Path, required=True, help="Standard JSON array output file")
    arguments.add_argument("--gazetteer", type=Path, required=True, help="Existing offline SQLite catalog")
    arguments.add_argument("--batch-size", type=int, default=250, help="Records per chunk (1..1000)")
    arguments.add_argument("--overwrite", action="store_true", help="Atomically replace an existing output")
    try:
        args = arguments.parse_args(argv)
        if not 1 <= args.batch_size <= 1000:
            raise StreamError("INVALID_BATCH_SIZE")
        try:
            gazetteer = Gazetteer(args.gazetteer)
        except Exception:
            raise StreamError("CATALOG_ERROR") from None
        with gazetteer:
            counts = process_file(
                args.input, args.output, gazetteer, batch_size=args.batch_size,
                overwrite=args.overwrite, protected_paths=(args.gazetteer,),
                progress=lambda counts: emit("progress", **counts),
            )
        emit("complete", **counts)
        return 0
    except StreamError as error:
        emit("error", **error.detail())
        return 1 if error.code in {"IO_ERROR", "PARSER_FAILURE", "CATALOG_ERROR"} else 2
    except KeyboardInterrupt:
        emit("error", code="CANCELLED")
        return 130
    except Exception:
        emit("error", code="RUNTIME_FAILURE")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
