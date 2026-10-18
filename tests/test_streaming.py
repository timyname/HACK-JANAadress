"""Bounded NDJSON parsing and atomic standard-export integration tests."""

import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from jana.contracts import PUBLIC_FIELDS
from jana.parser import parse_batch


def streaming():
    return importlib.import_module("jana.streaming")


def ndjson(*records):
    return b"".join((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8") for row in records)


def record(raw="Astana, ulitsa Test, dom 17", identity="same"):
    return {"id": identity, "raw_address": raw}


def test_batches_pull_only_current_chunk():
    api = streaming()
    pulled = []

    def source():
        for item in range(5):
            pulled.append(item)
            yield item

    chunks = api.iter_batches(source(), 2)
    assert pulled == []
    assert next(chunks) == [0, 1]
    assert pulled == [0, 1]
    assert next(chunks) == [2, 3]
    assert pulled == [0, 1, 2, 3]
    assert next(chunks) == [4]
    assert list(chunks) == []


@pytest.mark.parametrize("size", [True, False, 0, -1, 1001, 1.0, "2", None])
def test_batch_sizes_are_strict(size):
    api = streaming()
    with pytest.raises(api.StreamError) as error:
        list(api.iter_batches([], size))
    assert error.value.detail() == {"code": "INVALID_BATCH_SIZE"}


@pytest.mark.parametrize("size", [1, 250, 1000])
def test_valid_batch_sizes(size):
    assert list(streaming().iter_batches(iter([1]), size)) == [[1]]


def test_reader_uses_only_bounded_binary_readline():
    api = streaming()

    class Bounded(io.BytesIO):
        def read(self, *args):
            pytest.fail("read is forbidden")

        def readline(self, limit=-1):
            assert limit == api.MAX_NDJSON_LINE_BYTES + 1
            return super().readline(limit)

    rows = [record("  Ә\u200bғ  "), record("")]
    assert list(api.iter_ndjson(Bounded(ndjson(*rows)))) == rows


@pytest.mark.parametrize("ending", [b"\n", b"\r\n", b""])
def test_reader_line_byte_limit_includes_terminator(ending):
    api = streaming()
    row = json.dumps(record()).encode()
    exact = row + b" " * (api.MAX_NDJSON_LINE_BYTES - len(row) - len(ending)) + ending
    assert list(api.iter_ndjson(io.BytesIO(exact))) == [record()]
    with pytest.raises(api.StreamError) as error:
        list(api.iter_ndjson(io.BytesIO(b" " + exact)))
    assert error.value.detail() == {"code": "LINE_TOO_LONG", "line": 1}


@pytest.mark.parametrize("payload,code", [
    (b"\n", "INVALID_JSON"),
    (b" \r\n", "INVALID_JSON"),
    (b"\xff", "INVALID_JSON"),
    (b"\xef\xbb\xbf" + ndjson(record()), "INVALID_JSON"),
    (json.dumps(record()).encode("utf-16"), "INVALID_JSON"),
    (b'{"id":', "INVALID_JSON"),
    (b'{"id":"secret","raw_address":"secret","x":NaN}', "INVALID_JSON"),
    (b'{"id":"secret","raw_address":"secret","x":Infinity}', "INVALID_JSON"),
    (b'{"id":"secret","raw_address":"secret","x":-Infinity}', "INVALID_JSON"),
    (b"null", "INVALID_RECORD"),
    (b"[]", "INVALID_RECORD"),
    (b'{}', "INVALID_RECORD"),
    (b'{"id":1,"raw_address":"secret"}', "INVALID_RECORD"),
    (ndjson(record("x" * 8193)), "RECORD_TOO_LONG"),
    (ndjson(record(identity="x" * 1025)), "RECORD_TOO_LONG"),
    (b'{"id":"x","raw_address":"\\ud800"}', "INVALID_UNICODE"),
    (b'{"id":"\\udfff","raw_address":"secret"}', "INVALID_UNICODE"),
], ids=["blank", "spaces", "invalid-utf8", "bom", "utf16", "json", "nan", "infinity",
         "negative-infinity", "null", "array", "object", "id-type",
         "long-address", "long-id", "surrogate-address", "surrogate-id"])
def test_reader_rejects_invalid_input_safely_with_physical_line(payload, code):
    api = streaming()
    with pytest.raises(api.StreamError) as error:
        list(api.iter_ndjson(io.BytesIO(ndjson(record()) + payload)))
    assert error.value.detail() == {"code": code, "line": 2}
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("number", [b"1e999", b"-1e999"])
@pytest.mark.parametrize("location", ["value", "extra", "nested"])
def test_reader_rejects_float_overflow_anywhere_with_physical_line(number, location):
    api = streaming()
    if location == "value":
        payload = number
    elif location == "extra":
        payload = b'{"id":"secret","raw_address":"secret","extra":' + number + b'}'
    else:
        payload = b'{"id":"secret","raw_address":"secret","extra":{"nested":[' + number + b']}}'
    with pytest.raises(api.StreamError) as error:
        list(api.iter_ndjson(io.BytesIO(ndjson(record()) + payload)))
    assert error.value.detail() == {"code": "INVALID_JSON", "line": 2}
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("number", [b"1.25", b"-1.25", b"1e308", b"-1e308"])
def test_reader_ignores_finite_extra_floats(number):
    payload = json.dumps(record()).encode()[:-1] + b',"extra":' + number + b',"nested":[' + number + b']}'
    assert list(streaming().iter_ndjson(io.BytesIO(payload))) == [record()]


def test_reader_converts_deep_json_recursion_failure():
    api = streaming()
    depth = 10000  # Also exceeds CPython 3.12's separate C decoder recursion guard.
    with pytest.raises(api.StreamError) as error:
        list(api.iter_ndjson(io.BytesIO(b"[" * depth + b"0" + b"]" * depth)))
    assert error.value.detail() == {"code": "INVALID_JSON", "line": 1}


def test_reader_empty_final_line_and_extra_keys():
    api = streaming()
    assert list(api.iter_ndjson(io.BytesIO())) == []
    value = {**record(), "extra": {"ignored": 1}}
    assert list(api.iter_ndjson(io.BytesIO(json.dumps(value).encode()))) == [record()]


def test_stream_preserves_every_row_and_exact_public_contract():
    api = streaming()
    rows = [record("???"), record()]
    target = io.StringIO()
    events = []
    counts = api.write_standard_stream(io.BytesIO(ndjson(*rows)), target, None,
                                       batch_size=1, progress=events.append)
    result = json.loads(target.getvalue())
    assert len(result) == 2
    assert all(tuple(row) == PUBLIC_FIELDS for row in result)
    assert [row["id"] for row in result] == ["same", "same"]
    assert [row["raw_address"] for row in result] == [row["raw_address"] for row in rows]
    assert result[0]["city"] is None
    assert counts == {"processed": 2, "no_flags": 1, "attention": 1}
    assert events == [{"processed": 1, "no_flags": 0, "attention": 1}, counts]
    assert events[0] is not events[1] and events[1] is not counts
    assert target.getvalue().endswith("]\n")


def test_writer_emits_literal_unicode_and_preserves_source():
    value = record("  Ә\u200bғ, Астана, ул. Тест, д. 17  ", "Қ-1")
    target = io.StringIO()
    streaming().write_standard_stream(io.BytesIO(ndjson(value)), target, None)
    assert "Ә" in target.getvalue() and "Қ-1" in target.getvalue()
    assert "\\u" not in target.getvalue()
    output = json.loads(target.getvalue())[0]
    assert output["raw_address"] == value["raw_address"]
    assert output["id"] == value["id"]


def test_empty_stream_and_progress_copies():
    api = streaming()
    target = io.StringIO()
    assert api.write_standard_stream(io.BytesIO(), target, None) == {
        "processed": 0, "no_flags": 0, "attention": 0}
    assert target.getvalue() == "[]\n"

    def mutate(counts):
        counts["processed"] = -100

    assert api.write_standard_stream(io.BytesIO(ndjson(record())), io.StringIO(), None,
                                     progress=mutate)["processed"] == 1


@pytest.mark.parametrize("raw,attention", [
    ("Astana, ulitsa Test, dom 17", False),
    ("Astana, ulitsa Test, dom 17, kv 4, pod 2, et 3", False),
    ("Astana, ulitsa Test", True),
    ("Astana, dom 17", True),
    ("ulitsa Test, dom 17", True),
    ("Astana, ulitsa Test, dom 17, dom 18", True),
])
def test_quality_counts_use_actual_parser(raw, attention):
    counts = streaming().write_standard_stream(io.BytesIO(ndjson(record(raw))), io.StringIO(), None)
    assert counts == {"processed": 1, "attention": int(attention), "no_flags": int(not attention)}


@pytest.mark.parametrize("failure", ["exception", "output", "explain", "warnings"])
def test_parser_failures_have_safe_chunk_start_line(failure):
    api = streaming()
    calls = []

    def broken(rows, *, gazetteer, explain):
        calls.append(rows)
        assert explain is True
        output = parse_batch(rows, gazetteer=gazetteer, explain=explain)
        if len(calls) == 1:
            return output
        if failure == "exception":
            raise RuntimeError("secret-address")
        if failure == "output":
            output[0]["result"]["confidence"] = True
        elif failure == "explain":
            del output[0]["result"]
        else:
            del output[0]["warnings"]
        return output

    with pytest.raises(api.StreamError) as error:
        api.write_standard_stream(io.BytesIO(ndjson(*[record()] * 5)), io.StringIO(), None,
                                  batch_size=2, parser=broken)
    assert error.value.detail() == {"code": "PARSER_FAILURE", "line": 3}
    assert "secret" not in str(error.value)


def test_writer_does_not_convert_io_errors_or_interrupts_to_parser_failure():
    api = streaming()

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        api.write_standard_stream(io.BytesIO(ndjson(record())), io.StringIO(), None, parser=interrupted)

    class BrokenOutput(io.StringIO):
        def write(self, text):
            raise OSError("secret")

    with pytest.raises(OSError):
        api.write_standard_stream(io.BytesIO(), BrokenOutput(), None)


def test_process_file_publishes_complete_utf8_array_and_makes_explicit_parent(tmp_path):
    api = streaming()
    source = tmp_path / "source.jsonl"
    source.write_bytes(ndjson(record("  Астана, ул. Тест, д. 17  "), record("???")))
    target = tmp_path / "nested" / "out.json"
    counts = api.process_file(source, target, None, batch_size=1)
    outputs = json.loads(target.read_text(encoding="utf-8"))
    assert tuple(outputs[0]) == PUBLIC_FIELDS
    assert outputs[0]["raw_address"] == "  Астана, ул. Тест, д. 17  "
    assert b"\\u" not in target.read_bytes()
    assert counts == {"processed": 2, "no_flags": 1, "attention": 1}
    assert list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("overwrite", [False, True])
def test_existing_target_is_preserved_or_atomically_replaced(tmp_path, overwrite):
    api = streaming()
    source, target, sibling = (tmp_path / name for name in ("input", "output", "sibling"))
    source.write_bytes(ndjson(record()))
    target.write_bytes(b"old")
    sibling.write_bytes(b"untouched")
    if overwrite:
        api.process_file(source, target, None, overwrite=True)
        assert json.loads(target.read_text())[0]["id"] == "same"
    else:
        with pytest.raises(api.StreamError) as error:
            api.process_file(source, target, None)
        assert error.value.detail() == {"code": "OUTPUT_EXISTS"}
        assert target.read_bytes() == b"old"
    assert sibling.read_bytes() == b"untouched"
    assert source.read_bytes() == ndjson(record())
    assert len(list(tmp_path.iterdir())) == 3


@pytest.mark.parametrize("failure", ["invalid-last-line", "parser", "interrupt", "write", "fsync", "publish"])
@pytest.mark.parametrize("existing", [False, True])
def test_processing_failure_preserves_files_and_removes_only_own_temp(tmp_path, monkeypatch, failure, existing):
    api = streaming()
    source, target, unrelated = (tmp_path / name for name in ("input", "output", "keep.tmp"))
    source.write_bytes(ndjson(record()) + (b"secret-address" if failure == "invalid-last-line" else ndjson(record())))
    unrelated.write_bytes(b"untouched")
    if existing:
        target.write_bytes(b"old")

    def broken(*args, **kwargs):
        if failure == "interrupt":
            raise KeyboardInterrupt
        raise OSError("secret-address")

    kwargs = {"batch_size": 1, "overwrite": existing}
    if failure in {"parser", "interrupt"}:
        kwargs["parser"] = broken
    elif failure == "write":
        monkeypatch.setattr(api, "write_standard_stream", broken)
    elif failure == "fsync":
        monkeypatch.setattr(api.os, "fsync", broken)
    elif failure == "publish":
        monkeypatch.setattr(api.os, "replace" if existing else "link", broken)
    with pytest.raises(KeyboardInterrupt if failure == "interrupt" else api.StreamError) as error:
        api.process_file(source, target, None, **kwargs)
    if failure != "interrupt":
        expected = ("INVALID_JSON" if failure == "invalid-last-line" else
                    "PARSER_FAILURE" if failure == "parser" else "IO_ERROR")
        assert error.value.code == expected
        assert "secret-address" not in str(error.value)
    assert (target.read_bytes() == b"old") if existing else not target.exists()
    assert unrelated.read_bytes() == b"untouched"
    assert set(tmp_path.iterdir()) == {source, unrelated, *([target] if existing else [])}


@pytest.mark.parametrize("alias", ["self", "relative", "symlink", "hardlink"])
@pytest.mark.parametrize("protected", [False, True])
def test_output_alias_cannot_replace_input_or_catalog(tmp_path, monkeypatch, alias, protected):
    api = streaming()
    source = tmp_path / "input"
    source.write_bytes(ndjson(record()))
    guard = tmp_path / "catalog"
    guard.write_bytes(b"catalog")
    original = guard if protected else source
    if alias == "self":
        target = original
    elif alias == "relative":
        monkeypatch.chdir(tmp_path)
        target = Path(original.name)
    else:
        target = tmp_path / "alias"
        try:
            if alias == "symlink":
                target.symlink_to(original)
            else:
                os.link(original, target)
        except OSError as error:
            if alias == "symlink":
                pytest.skip(f"symlink unavailable: {type(error).__name__}")
            raise
    with pytest.raises(api.StreamError) as error:
        api.process_file(source, target, None, overwrite=True, protected_paths=(guard,))
    assert error.value.detail() == {"code": "OUTPUT_PATH_CONFLICT"}
    assert source.read_bytes() == ndjson(record())
    assert guard.read_bytes() == b"catalog"


def test_overwrite_replaces_directory_entry_without_following_other_links(tmp_path):
    api = streaming()
    source, target, other = (tmp_path / name for name in ("input", "output", "other"))
    source.write_bytes(ndjson(record()))
    other.write_bytes(b"other-data")
    os.link(other, target)
    api.process_file(source, target, None, overwrite=True)
    assert other.read_bytes() == b"other-data"
    assert json.loads(target.read_text())[0]["id"] == "same"


@pytest.mark.parametrize("kind", ["missing", "directory", "url"])
def test_input_must_be_a_local_file(tmp_path, kind):
    api = streaming()
    source = "https://example.test/private" if kind == "url" else tmp_path / "source"
    if kind == "directory":
        source.mkdir()
    with pytest.raises(api.StreamError) as error:
        api.process_file(source, tmp_path / "output", None)
    assert error.value.detail() == {"code": "INPUT_NOT_FILE"}


def test_publish_without_overwrite_has_no_clobber_race(tmp_path, monkeypatch):
    api = streaming()
    source, target = tmp_path / "input", tmp_path / "output"
    source.write_bytes(ndjson(record()))
    real_link = api.os.link

    def competing_link(temp, destination):
        target.write_bytes(b"competing-process")
        return real_link(temp, destination)

    monkeypatch.setattr(api.os, "link", competing_link)
    with pytest.raises(api.StreamError) as error:
        api.process_file(source, target, None)
    assert error.value.detail() == {"code": "OUTPUT_EXISTS"}
    assert target.read_bytes() == b"competing-process"
    assert set(tmp_path.iterdir()) == {source, target}


@pytest.mark.parametrize("failure", [OSError, NotImplementedError])
def test_unsupported_atomic_link_fails_safely_without_fallback(tmp_path, monkeypatch, failure):
    api = streaming()
    source, target = tmp_path / "input", tmp_path / "output"
    source.write_bytes(ndjson(record()))

    def unsupported(*args):
        raise failure("secret-address")

    monkeypatch.setattr(api.os, "link", unsupported)
    with pytest.raises(api.StreamError) as error:
        api.process_file(source, target, None)
    assert error.value.detail() == {"code": "IO_ERROR"}
    assert list(tmp_path.iterdir()) == [source]


@pytest.fixture
def tiny_catalog(tmp_path):
    from tools.build_gazetteer import create_database

    path = tmp_path / "catalog.sqlite"
    create_database(path, [], [], [], [])
    return path


def run_cli(source, target, catalog, *options):
    return subprocess.run([sys.executable, str(ROOT / "tools" / "parse_stream.py"),
                           "--input", str(source), "--output", str(target),
                           "--gazetteer", str(catalog), *options], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8", timeout=20)


def events(completed):
    assert completed.stdout == ""
    assert "Traceback" not in completed.stderr
    assert "secret-address" not in completed.stderr
    return [json.loads(line) for line in completed.stderr.splitlines()]


@pytest.mark.parametrize("empty", [False, True])
def test_genuine_cli_emits_safe_progress_then_complete_after_publication(tmp_path, tiny_catalog, empty):
    source, target = tmp_path / "input", tmp_path / "output"
    rows = [] if empty else [record("???"), record("  Астана, ул. Тест, д. 17  ")]
    source.write_bytes(ndjson(*rows))
    completed = run_cli(source, target, tiny_catalog, "--batch-size", "1")
    assert completed.returncode == 0
    emitted = events(completed)
    assert [event["event"] for event in emitted] == ([] if empty else ["progress", "progress"]) + ["complete"]
    assert emitted[-1] == {"event": "complete", "processed": len(rows), "no_flags": int(not empty), "attention": int(not empty)}
    outputs = json.loads(target.read_text(encoding="utf-8"))
    assert len(outputs) == len(rows)
    for original, output in zip(rows, outputs):
        assert tuple(output) == PUBLIC_FIELDS
        assert output["raw_address"] == original["raw_address"]
        assert output["id"] == original["id"]
        assert isinstance(output["confidence"], (int, float))
        assert all(value is None or isinstance(value, str) for value in list(output.values())[:-1])
    for event in emitted:
        assert set(event) == {"event", "processed", "attention", "no_flags"}


@pytest.mark.parametrize("kind,exit_code,code", [
    ("invalid-input", 2, "INVALID_JSON"),
    ("exists", 2, "OUTPUT_EXISTS"),
    ("self", 2, "OUTPUT_PATH_CONFLICT"),
    ("catalog-output", 2, "OUTPUT_PATH_CONFLICT"),
    ("missing-catalog", 1, "CATALOG_ERROR"),
    ("corrupt-catalog", 1, "CATALOG_ERROR"),
    ("bad-size", 2, "INVALID_BATCH_SIZE"),
    ("bad-int", 2, "INVALID_ARGUMENTS"),
    ("extra-arg", 2, "INVALID_ARGUMENTS"),
])
def test_cli_failures_are_safe_and_never_complete(tmp_path, tiny_catalog, kind, exit_code, code):
    source, target, catalog = tmp_path / "input", tmp_path / "output", tiny_catalog
    source.write_bytes(ndjson(record()) + (b"secret-address" if kind == "invalid-input" else b""))
    options = []
    if kind == "exists":
        target.write_bytes(b"old")
    elif kind == "self":
        target = source
        options = ["--overwrite"]
    elif kind == "catalog-output":
        target = catalog
        options = ["--overwrite"]
    elif kind == "missing-catalog":
        catalog = tmp_path / "missing"
    elif kind == "corrupt-catalog":
        catalog.write_bytes(b"secret-address")
    elif kind == "bad-size":
        options = ["--batch-size", "0"]
        catalog = tmp_path / "missing"  # Reject before catalog load.
    elif kind == "bad-int":
        options = ["--batch-size", "secret-address"]
    elif kind == "extra-arg":
        options = ["--secret-address"]
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    completed = run_cli(source, target, catalog, *options)
    assert completed.returncode == exit_code
    emitted = events(completed)
    assert emitted == [{"event": "error", "code": code, **({"line": 2} if kind == "invalid-input" else {})}]
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_cli_overwrite_is_explicit(tmp_path, tiny_catalog):
    source, target = tmp_path / "input", tmp_path / "output"
    source.write_bytes(ndjson(record()))
    target.write_bytes(b"old")
    completed = run_cli(source, target, tiny_catalog, "--overwrite")
    assert completed.returncode == 0
    assert events(completed)[-1]["event"] == "complete"
    assert json.loads(target.read_text())[0]["raw_address"] == record()["raw_address"]


def test_warnings_alone_require_attention_even_with_high_confidence():
    value = record("Астана, Есильский район, мкр. Test, ул. Test, д.17, д.18, кв.4, под.2, эт.3, ориентир школа")
    explanation = parse_batch([value], explain=True)[0]
    assert explanation["warnings"] == ["conflicting_house_values"]
    assert explanation["result"]["confidence"] >= 0.75
    assert all(explanation["result"][field] is not None
               for field in ("province", "city", "street_name", "house"))
    assert streaming().write_standard_stream(io.BytesIO(ndjson(value)), io.StringIO(), None) == {
        "processed": 1, "no_flags": 0, "attention": 1}


def test_unverified_explicit_district_from_real_catalog_requires_attention(tiny_catalog):
    from jana.gazetteer import Gazetteer

    value = record("Астана, Есильский район, ул. Тест, д.17, кв.4")
    with Gazetteer(tiny_catalog) as gazetteer:
        explanation = parse_batch([value], gazetteer=gazetteer, explain=True)[0]
        assert explanation["warnings"] == ["gazetteer_district_unverified"]
        assert explanation["result"]["confidence"] >= 0.75
        assert streaming().write_standard_stream(io.BytesIO(ndjson(value)), io.StringIO(), gazetteer) == {
            "processed": 1, "no_flags": 0, "attention": 1}


def test_cli_missing_required_arguments_is_safe_json():
    completed = subprocess.run([sys.executable, str(ROOT / "tools" / "parse_stream.py"),
                                "--input", "secret-address"], cwd=ROOT,
                               capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert completed.returncode == 2
    assert events(completed) == [{"event": "error", "code": "INVALID_ARGUMENTS"}]


@pytest.mark.parametrize("kind,code,status", [
    ("interrupt", "CANCELLED", 130), ("unexpected", "RUNTIME_FAILURE", 1),
    ("parser", "PARSER_FAILURE", 1), ("io", "IO_ERROR", 1),
])
def test_cli_runtime_failures_emit_only_safe_events(tmp_path, tiny_catalog, monkeypatch, capsys, kind, code, status):
    from tools import parse_stream

    def fail(*args, **kwargs):
        if kind == "interrupt":
            raise KeyboardInterrupt
        if kind == "unexpected":
            raise RuntimeError("secret-address")
        raise streaming().StreamError(code, 1 if kind == "parser" else None)

    monkeypatch.setattr(parse_stream, "process_file", fail)
    assert parse_stream.main(["--input", str(tmp_path / "input"), "--output", str(tmp_path / "output"),
                              "--gazetteer", str(tiny_catalog)]) == status
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "secret-address" not in captured.err
    assert [json.loads(line) for line in captured.err.splitlines()] == [
        {"event": "error", "code": code, **({"line": 1} if kind == "parser" else {})}]


@pytest.mark.parametrize("prior_error", [False, True])
def test_temp_cleanup_failure_is_safe_and_preserves_meaningful_error(tmp_path, monkeypatch, prior_error):
    api = streaming()
    source, target = tmp_path / "input", tmp_path / "output"
    source.write_bytes(b"secret-address" if prior_error else ndjson(record()))
    original_unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path.name.endswith(".tmp"):
            raise PermissionError("secret-address")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    with pytest.raises(api.StreamError) as error:
        api.process_file(source, target, None)
    assert error.value.detail() == ({"code": "INVALID_JSON", "line": 1} if prior_error else {"code": "IO_ERROR"})
    assert "secret-address" not in str(error.value)
    assert not target.exists() if prior_error else json.loads(target.read_text())[0]["id"] == "same"
    # The deliberately locked owned temp cannot be removed; release it for test cleanup.
    for path in tmp_path.glob("*.tmp"):
        original_unlink(path)
