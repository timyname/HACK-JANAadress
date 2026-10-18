"""Read-only catalog acceptance checks using small, real SQLite artifacts."""

from contextlib import closing
from dataclasses import asdict
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tools.build_gazetteer import (
    AddressPointRecord,
    AdminRecord,
    SourceRecord,
    StreetRecord,
    create_database,
)


def test_verifier_module_is_available():
    assert importlib.util.find_spec("tools.verify_gazetteer") is not None, (
        "Read-only gazetteer verifier is not implemented"
    )


@pytest.fixture
def verifier():
    assert importlib.util.find_spec("tools.verify_gazetteer") is not None, (
        "Read-only gazetteer verifier is not implemented"
    )
    return importlib.import_module("tools.verify_gazetteer")


@pytest.fixture
def catalog(tmp_path):
    db = tmp_path / "каталог with spaces.sqlite"
    manifest = tmp_path / "manifest.json"
    source = SourceRecord(
        source_name="fixture",
        url="https://example.invalid/source",
        filename="fixture.xlsx",
        sha256="a" * 64,
        fetched_at="2026-10-18T00:00:00Z",
        version="test-v1",
        license_name="Fixture license",
        license_url="https://example.invalid/license",
    )
    admins = [AdminRecord(
        "city", "PRIVATE_TEST_LOCALITY", kato_code="710000000",
        province="PRIVATE_TEST_REGION", city="PRIVATE_TEST_LOCALITY",
    )]
    streets = [
        StreetRecord("way/1", "PRIVATE_TEST_STREET_A", city="PRIVATE_TEST_LOCALITY",
                     province="PRIVATE_TEST_REGION", districts=("PRIVATE_TEST_DISTRICT_A",)),
        StreetRecord("way/2", "PRIVATE_TEST_STREET_B", city="PRIVATE_TEST_LOCALITY",
                     districts=("PRIVATE_TEST_DISTRICT_A", "PRIVATE_TEST_DISTRICT_B")),
        StreetRecord("way/3", "PRIVATE_TEST_STREET_C"),
    ]
    points = [
        AddressPointRecord("node/1", "way/1", "99", district="PRIVATE_TEST_DISTRICT_A",
                           living_area="PRIVATE_TEST_AREA"),
        AddressPointRecord("node/2", "way/2", "88"),
        AddressPointRecord("node/3", "way/3", "77", district="PRIVATE_TEST_DISTRICT_B"),
    ]
    create_database(db, admins, streets, points, [source])
    manifest.write_text(json.dumps({"sources": [asdict(source)]}), encoding="utf-8")
    return db, manifest


def test_reports_real_counts_coverage_hash_and_scoped_initialization(verifier, catalog):
    db, manifest = catalog
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is True
    assert report["errors"] == []
    assert all(report["checks"].values())
    assert report["sqlite_version"] == sqlite3.sqlite_version
    assert "not prove application-domain validity" in report["integrity_scope"]
    assert report["database"] == {
        "size_bytes": db.stat().st_size,
        "sha256": hashlib.sha256(db.read_bytes()).hexdigest(),
    }
    assert report["counts"] == {
        "source_snapshots": 1, "admin_records": 1, "admin_aliases": 1,
        "streets": 3, "street_aliases": 3, "street_districts": 3,
        "address_points": 3,
    }
    assert report["coverage"]["streets"] == {
        "with_city": 2, "without_city": 1,
        "with_province": 1, "without_province": 2,
        "with_district": 2, "without_district": 1,
        "with_multiple_districts": 1,
    }
    assert report["coverage"]["address_points"] == {
        "with_district": 2, "without_district": 1,
        "with_living_area": 1, "without_living_area": 2,
    }
    assert report["initialization_seconds_after_validation"] >= 0
    assert "not cold start" in report["measurement_scope"]
    assert "Docker" in report["measurement_scope"]
    assert "HTTP" in report["measurement_scope"]
    assert "consistency only" in report["provenance_scope"]
    assert "empty catalogs may pass" in report["acceptance_scope"]
    assert "Not evidence of completeness or geographic accuracy" in report["acceptance_scope"]
    assert "PRIVATE_TEST_" not in json.dumps(report)


def test_empty_catalog_is_valid_and_missing_membership_is_not_a_failure(verifier, tmp_path):
    db, manifest = tmp_path / "empty.sqlite", tmp_path / "manifest.json"
    create_database(db, [], [], [], [])
    manifest.write_text('{"sources": []}', encoding="utf-8")
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is True
    assert all(value == 0 for value in report["counts"].values())
    assert all(value == 0 for values in report["coverage"].values() for value in values.values())


@pytest.mark.parametrize("field", [
    "source_name", "url", "filename", "sha256", "fetched_at", "version",
    "license_name", "license_url",
])
def test_every_source_metadata_field_must_match_exactly(verifier, catalog, field):
    db, manifest = catalog
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["sources"][0][field] = "PRIVATE_TEST_CHANGED_METADATA"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "SOURCE_METADATA_MISMATCH"
    assert "PRIVATE_TEST_" not in json.dumps(report)


def test_extra_catalog_source_is_rejected(verifier, catalog):
    db, manifest = catalog
    manifest.write_text('{"sources": []}', encoding="utf-8")
    report = verifier.verify_gazetteer(db, manifest)
    assert report["errors"][0]["code"] == "SOURCE_METADATA_MISMATCH"


@pytest.mark.parametrize("table", [
    "source_snapshots", "admin_records", "admin_aliases", "streets",
    "street_aliases", "street_districts", "address_points",
])
def test_required_tables_cannot_be_missing(verifier, catalog, table):
    db, manifest = catalog
    with closing(sqlite3.connect(db)) as connection:
        connection.execute(f'DROP TABLE "{table}"')
        connection.commit()
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "INVALID_SCHEMA"


def test_required_columns_cannot_be_missing(verifier, catalog):
    db, manifest = catalog
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("ALTER TABLE admin_aliases DROP COLUMN canonical")
        connection.commit()
    assert verifier.verify_gazetteer(db, manifest)["errors"][0]["code"] == "INVALID_SCHEMA"


def test_integrity_check_failure_is_safe(verifier, catalog):
    db, manifest = catalog
    with closing(sqlite3.connect(db)) as connection:
        # Deliberately mismatch an index definition and its stored keys.
        # SQLite 3.53.1's read-only integrity_check does not detect the alternate
        # fixture of CHECK-violating rows written with ignore_check_constraints.
        connection.execute("PRAGMA writable_schema=ON")
        connection.execute(
            "UPDATE sqlite_master SET sql="
            "'CREATE INDEX street_aliases_normalized_idx ON street_aliases(alias)' "
            "WHERE name='street_aliases_normalized_idx'"
        )
        connection.commit()
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "INTEGRITY_CHECK_FAILED"
    assert "PRIVATE_TEST_" not in json.dumps(report)


def test_orphan_foreign_key_is_rejected(verifier, catalog):
    db, manifest = catalog
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE address_points SET street_id=999999 WHERE id=1")
        connection.commit()
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "FOREIGN_KEY_CHECK_FAILED"


@pytest.mark.parametrize("content", ["not sqlite PRIVATE_TEST_SECRET", ""])
def test_corrupt_or_empty_database_fails_cleanly(verifier, catalog, content):
    db, manifest = catalog
    db.write_bytes(content.encode())
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] in {"DATABASE_ERROR", "INVALID_SCHEMA"}
    assert "PRIVATE_TEST_" not in json.dumps(report)


def test_missing_database_is_not_created(verifier, catalog, tmp_path):
    _, manifest = catalog
    missing = tmp_path / "missing.sqlite"
    report = verifier.verify_gazetteer(missing, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "DATABASE_NOT_FOUND"
    assert not missing.exists()


@pytest.mark.parametrize("payload", [
    "not json PRIVATE_TEST_SECRET", "[]", '{"sources": [{}]}',
    '{"sources": "PRIVATE_TEST_SECRET"}',
])
def test_invalid_manifest_fails_without_echoing_content(verifier, catalog, payload):
    db, manifest = catalog
    manifest.write_text(payload, encoding="utf-8")
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "INVALID_MANIFEST"
    assert "PRIVATE_TEST_" not in json.dumps(report)


def test_connections_are_read_only_closed_and_database_unchanged(verifier, catalog, monkeypatch):
    db, manifest = catalog
    original = db.read_bytes()
    connect = sqlite3.connect
    opened = []

    def observed_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden_write (id INTEGER)")
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    assert verifier.verify_gazetteer(db, manifest)["success"] is True
    assert len(opened) >= 2  # SQLite checks and real Gazetteer initialization.
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    assert db.read_bytes() == original
    assert not list(db.parent.glob("*.sqlite-*"))


def test_runtime_initialization_failure_is_safe_and_closes_connection(verifier, catalog, monkeypatch):
    db, manifest = catalog
    with closing(sqlite3.connect(db)) as connection:
        connection.execute("UPDATE admin_aliases SET province = X'0102'")
        connection.commit()
    connect, opened = sqlite3.connect, []

    def observed_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed_connect)
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "RUNTIME_INITIALIZATION_FAILED"
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("phase", ["_manifest_rows", "_initialization_seconds"])
def test_database_replacement_between_phases_invalidates_verification(
    verifier, catalog, tmp_path, monkeypatch, phase
):
    db, manifest = catalog
    sources = [SourceRecord(**row) for row in json.loads(manifest.read_text())["sources"]]
    replacement = tmp_path / "replacement.sqlite"
    create_database(replacement, [], [], [], sources)
    original_phase = getattr(verifier, phase)

    def replace_then_continue(*args, **kwargs):
        os.replace(replacement, db)
        return original_phase(*args, **kwargs)

    monkeypatch.setattr(verifier, phase, replace_then_continue)
    report = verifier.verify_gazetteer(db, manifest)
    assert report["success"] is False
    assert report["errors"][0]["code"] == "ARTIFACT_CHANGED_DURING_VERIFICATION"


def test_cli_reports_safe_json_and_optional_report(verifier, catalog, tmp_path, capsys):
    db, manifest = catalog
    output = tmp_path / "reports" / "verification.json"
    assert verifier.main(["--db", str(db), "--manifest", str(manifest), "--report", str(output)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    report = json.loads(captured.out)
    assert report == json.loads(output.read_text(encoding="utf-8"))
    assert report["success"] is True
    assert "PRIVATE_TEST_" not in captured.out


@pytest.mark.parametrize("target_index", [0, 1])
def test_report_cannot_overwrite_database_or_manifest(verifier, catalog, capsys, target_index):
    db, manifest = catalog
    originals = [path.read_bytes() for path in catalog]
    assert verifier.main(["--db", str(db), "--manifest", str(manifest),
                          "--report", str(catalog[target_index])]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["errors"][0]["code"] == "REPORT_PATH_CONFLICT"
    assert [path.read_bytes() for path in catalog] == originals


@pytest.mark.parametrize("target_index", [0, 1])
def test_report_cannot_overwrite_input_through_hardlink(verifier, catalog, tmp_path, capsys, target_index):
    db, manifest = catalog
    original = catalog[target_index].read_bytes()
    linked_report = tmp_path / "linked-report.json"
    linked_report.hardlink_to(catalog[target_index])
    assert verifier.main(["--db", str(db), "--manifest", str(manifest),
                          "--report", str(linked_report)]) == 1
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == "REPORT_PATH_CONFLICT"
    assert catalog[target_index].read_bytes() == original


def test_report_hardlink_created_after_preflight_does_not_change_database(
    verifier, catalog, tmp_path, capsys, monkeypatch
):
    db, manifest = catalog
    original = db.read_bytes()
    output = tmp_path / "report.json"
    original_verify = verifier.verify_gazetteer

    def verify_then_add_hardlink(*args, **kwargs):
        report = original_verify(*args, **kwargs)
        output.hardlink_to(db)
        return report

    monkeypatch.setattr(verifier, "verify_gazetteer", verify_then_add_hardlink)
    assert verifier.main(["--db", str(db), "--manifest", str(manifest), "--report", str(output)]) == 0
    assert db.read_bytes() == original
    assert not output.samefile(db)
    assert json.loads(output.read_text()) == json.loads(capsys.readouterr().out)


def test_failed_report_replace_preserves_old_report_and_cleans_temporary_file(
    verifier, catalog, tmp_path, capsys, monkeypatch
):
    db, manifest = catalog
    output = tmp_path / "report.json"
    output.write_text("previous report", encoding="utf-8")
    before = {path.name for path in tmp_path.iterdir()}

    def fail_replace(*args, **kwargs):
        raise PermissionError("PRIVATE_TEST_REPLACE_ERROR")

    monkeypatch.setattr(os, "replace", fail_replace)
    assert verifier.main(["--db", str(db), "--manifest", str(manifest), "--report", str(output)]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["errors"][-1]["code"] == "REPORT_WRITE_FAILED"
    assert "PRIVATE_TEST_" not in captured.out
    assert output.read_text() == "previous report"
    assert {path.name for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("failure", ["missing", "corrupt", "metadata"])
def test_cli_process_exits_nonzero_for_bad_artifact(verifier, catalog, failure):
    db, manifest = catalog
    if failure == "missing":
        db = db.with_name("missing.sqlite")
    elif failure == "corrupt":
        db.write_bytes(b"not sqlite PRIVATE_TEST_SECRET")
    else:
        manifest.write_text('{"sources": []}', encoding="utf-8")
    command = [sys.executable, str(Path(verifier.__file__)), "--db", str(db), "--manifest", str(manifest)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert json.loads(result.stdout)["success"] is False
    assert result.stderr == ""
    assert "PRIVATE_TEST_" not in result.stdout
