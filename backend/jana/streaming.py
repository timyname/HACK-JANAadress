"""Bounded local NDJSON processing into the standard public JSON array."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
from itertools import islice

from .contracts import InputError, validate_batch, validate_output
from .parser import parse_batch

MAX_NDJSON_LINE_BYTES = 256 * 1024


class StreamError(ValueError):
    """A payload-free diagnostic with an optional physical input line."""

    def __init__(self, code, line=None):
        self.code = code
        self.line = line
        super().__init__(code)

    def detail(self):
        return {"code": self.code, **({"line": self.line} if self.line is not None else {})}


def _reject_constant(value):
    raise ValueError("NONFINITE_JSON")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("NONFINITE_JSON")
    return number


def iter_ndjson(source):
    """Read strictly UTF-8 physical lines without any unbounded reads."""
    line = 0
    while True:
        payload = source.readline(MAX_NDJSON_LINE_BYTES + 1)
        if not payload:
            return
        line += 1
        if len(payload) > MAX_NDJSON_LINE_BYTES:
            raise StreamError("LINE_TOO_LONG", line)
        try:
            value = json.loads(payload.decode("utf-8", errors="strict"),
                               parse_constant=_reject_constant, parse_float=_finite_float)
        except (ValueError, UnicodeError, RecursionError):
            raise StreamError("INVALID_JSON", line) from None
        try:
            yield validate_batch([value])[0]
        except InputError as error:
            raise StreamError(error.code, line) from None


def _validate_batch_size(batch_size):
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise StreamError("INVALID_BATCH_SIZE")


def iter_batches(records, batch_size=250):
    """Pull one bounded chunk at a time, without prefetching the next."""
    _validate_batch_size(batch_size)
    records = iter(records)
    while chunk := list(islice(records, batch_size)):
        yield chunk


def write_standard_stream(source, target, gazetteer, *, batch_size=250, progress=None, parser=None):
    """Write only public fields and return partitioned quality counters."""
    _validate_batch_size(batch_size)
    parser = parse_batch if parser is None else parser
    counts = {"processed": 0, "no_flags": 0, "attention": 0}
    first = True
    target.write("[")
    for chunk in iter_batches(iter_ndjson(source), batch_size):
        try:
            explained = parser(chunk, gazetteer=gazetteer, explain=True)
            if not isinstance(explained, list) or len(explained) != len(chunk):
                raise ValueError("EXPLANATION_COUNT")
            for entry in explained:
                if (not isinstance(entry, dict) or not isinstance(entry["fields"], dict)
                        or not isinstance(entry["warnings"], list)
                        or any(not isinstance(warning, str) for warning in entry["warnings"])):
                    raise ValueError("EXPLANATION_SHAPE")
            outputs = validate_output(chunk, [entry["result"] for entry in explained])
            attention = [
                any(row[field] is None for field in ("province", "city", "street_name", "house"))
                or row["confidence"] < 0.75 or bool(entry["warnings"])
                for row, entry in zip(outputs, explained)
            ]
        except Exception:
            raise StreamError("PARSER_FAILURE", counts["processed"] + 1) from None
        for row, flagged in zip(outputs, attention):
            if not first:
                target.write(",")
            json.dump(row, target, ensure_ascii=False, allow_nan=False)
            first = False
            counts["processed"] += 1
            counts["attention" if flagged else "no_flags"] += 1
        if progress is not None:
            progress(counts.copy())
    target.write("]\n")
    return counts


def _same_path(left, right):
    if left.resolve() == right.resolve():
        return True
    try:
        return left.samefile(right)
    except FileNotFoundError:
        return False


def process_file(input_path, output_path, gazetteer, *, batch_size=250, overwrite=False,
                 progress=None, parser=None, protected_paths=()):
    """Publish a complete result beside its temporary file, preserving old data on failure.

    Path checks protect ordinary local use, including existing hard links and
    symlinks. They do not defend against hostile concurrent parent replacement.
    """
    _validate_batch_size(batch_size)
    temporary = None
    try:
        source_path, target_path = Path(input_path), Path(output_path)
        if "://" in str(input_path) or not source_path.is_file():
            raise StreamError("INPUT_NOT_FILE")
        if any(_same_path(target_path, guard)
               for guard in (source_path, *(Path(path) for path in protected_paths))):
            raise StreamError("OUTPUT_PATH_CONFLICT")
        if not overwrite and (target_path.exists() or target_path.is_symlink()):
            raise StreamError("OUTPUT_EXISTS")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with source_path.open("rb") as source:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", delete=False,
                dir=target_path.parent, prefix=f".{target_path.name}.jana-", suffix=".tmp",
            ) as target:
                temporary = Path(target.name)
                counts = write_standard_stream(source, target, gazetteer,
                                               batch_size=batch_size, progress=progress, parser=parser)
                target.flush()
                os.fsync(target.fileno())
        if overwrite:
            os.replace(temporary, target_path)
        else:
            try:
                os.link(temporary, target_path)
            except FileExistsError:
                raise StreamError("OUTPUT_EXISTS") from None
            except NotImplementedError:
                raise StreamError("IO_ERROR") from None
        temporary.unlink(missing_ok=True)
        temporary = None
        return counts
    except OSError:
        raise StreamError("IO_ERROR") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # Cleanup must not replace an input/parser failure or interrupt.
                pass
