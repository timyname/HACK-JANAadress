"""Read-only structural/provenance checks for an already built SQLite catalog."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from jana.gazetteer import Gazetteer


SOURCE_FIELDS = (
    "source_name", "url", "filename", "sha256", "fetched_at", "version",
    "license_name", "license_url",
)
AREA_ENRICHMENT_VERSION = "3"
AREA_ENRICHMENT_STEP = "osm-residential-area-enrichment"
REQUIRED_COLUMNS = {
    "source_snapshots": SOURCE_FIELDS,
    "admin_records": (
        "id", "field", "canonical", "language", "kato_code", "province",
        "city", "district", "living_area",
    ),
    "admin_aliases": (
        "admin_id", "field", "canonical", "alias", "normalized", "language",
        "kato_code", "province", "city", "district", "living_area",
    ),
    "streets": (
        "id", "source_id", "name", "street_type", "city", "normalized_city",
        "province", "living_area", "geometry_wkb",
    ),
    "street_aliases": ("street_id", "alias", "normalized"),
    "street_districts": ("street_id", "district"),
    "address_points": (
        "id", "source_id", "street_id", "house", "normalized_house", "district",
        "living_area", "longitude", "latitude",
    ),
}


class VerificationError(Exception):
    """A fixed safe error, never an exception containing source records."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _new_report() -> dict:
    return {
        "success": False,
        "sqlite_version": sqlite3.sqlite_version,
        "integrity_scope": (
            "SQLite PRAGMA checks do not prove application-domain validity. "
            "Observed on SQLite 3.53.1: read-only integrity_check missed injected "
            "CHECK violations while detecting an inconsistent index."
        ),
        "acceptance_scope": (
            "Structural/provenance consistency only; empty catalogs may pass. "
            "Not evidence of completeness or geographic accuracy."
        ),
        "artifact_scope": (
            "Requires a completed immutable SQLite artifact. Stat guards detect "
            "ordinary replacement or mutation, not adversarial filesystem races."
        ),
        "provenance_scope": (
            "Manifest-to-database metadata consistency only; raw sources are not "
            "hashed and geospatial joins are not validated."
        ),
        "coverage_scope": "Stored non-null membership counts; missing values do not fail verification.",
        "measurement_scope": (
            "Python Gazetteer initialization after validation and hashing; "
            "not cold start, Docker startup, or HTTP batch timing."
        ),
        "database": None,
        "checks": dict.fromkeys(
            ("schema", "integrity", "foreign_keys", "source_metadata", "runtime_initialization"),
            False,
        ),
        "counts": {},
        "coverage": {},
        "initialization_seconds_after_validation": None,
        "errors": [],
    }


def _manifest_rows(path: Path) -> list[tuple[str, ...]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise VerificationError("MANIFEST_READ_FAILED", "Cannot read the source manifest.") from None
    except (ValueError, UnicodeError, RecursionError):
        raise VerificationError("INVALID_MANIFEST", "Source manifest is not valid JSON metadata.") from None
    rows = payload.get("sources") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or set(row) != set(SOURCE_FIELDS)
        or any(not isinstance(row[field], str) for field in SOURCE_FIELDS)
        for row in rows
    ):
        raise VerificationError("INVALID_MANIFEST", "Source manifest has an invalid metadata schema.")
    return sorted(tuple(row[field] for field in SOURCE_FIELDS) for row in rows)


def _coverage(connection: sqlite3.Connection, counts: dict[str, int]) -> dict:
    cities, provinces = connection.execute("SELECT count(city), count(province) FROM streets").fetchone()
    districts, multiple = connection.execute(
        "SELECT count(*), coalesce(sum(n > 1), 0) FROM "
        "(SELECT street_id, count(*) AS n FROM street_districts GROUP BY street_id)"
    ).fetchone()
    point_districts, point_areas = connection.execute(
        "SELECT count(district), count(living_area) FROM address_points"
    ).fetchone()
    return {
        "streets": {
            "with_city": cities,
            "without_city": counts["streets"] - cities,
            "with_province": provinces,
            "without_province": counts["streets"] - provinces,
            "with_district": districts,
            "without_district": counts["streets"] - districts,
            "with_multiple_districts": multiple,
        },
        "address_points": {
            "with_district": point_districts,
            "without_district": counts["address_points"] - point_districts,
            "with_living_area": point_areas,
            "without_living_area": counts["address_points"] - point_areas,
        },
    }


def _initialization_seconds(path: Path) -> float:
    # Keep the object available for cleanup if its existing constructor fails
    # after opening SQLite. This does not change the production constructor.
    catalog = Gazetteer.__new__(Gazetteer)
    started = time.perf_counter()
    try:
        Gazetteer.__init__(catalog, path)
        return time.perf_counter() - started
    except (sqlite3.Error, OSError, ValueError, TypeError, AttributeError):
        raise VerificationError(
            "RUNTIME_INITIALIZATION_FAILED", "Gazetteer initialization failed."
        ) from None
    finally:
        if getattr(catalog, "_connection", None) is not None:
            catalog.close()


def _artifact_state(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _require_unchanged(path: Path, original: tuple[int, ...]) -> None:
    try:
        unchanged = _artifact_state(path) == original
    except OSError:
        unchanged = False
    if not unchanged:
        raise VerificationError(
            "ARTIFACT_CHANGED_DURING_VERIFICATION",
            "Database artifact changed during verification; retry on a completed immutable artifact.",
        )


def verify_gazetteer(db: str | Path, manifest: str | Path) -> dict:
    """Inspect a completed artifact without creating or modifying the database."""
    report = _new_report()
    try:
        db_path = Path(db).resolve()
        if not db_path.is_file():
            raise VerificationError("DATABASE_NOT_FOUND", "Database file does not exist.")
        original_state = _artifact_state(db_path)
        with db_path.open("rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
        _require_unchanged(db_path, original_state)
        report["database"] = {"size_bytes": original_state[2], "sha256": digest}
        expected = _manifest_rows(Path(manifest))
        _require_unchanged(db_path, original_state)
        uri = db_path.as_uri().replace("file://", "file:") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            connection.execute("BEGIN")
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not tables.issuperset(REQUIRED_COLUMNS):
                raise VerificationError("INVALID_SCHEMA", "Required catalog tables are missing.")
            for table, required in REQUIRED_COLUMNS.items():
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                if not columns.issuperset(required):
                    raise VerificationError("INVALID_SCHEMA", "Required catalog columns are missing.")
            report["checks"]["schema"] = True
            if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                raise VerificationError("INTEGRITY_CHECK_FAILED", "SQLite integrity check failed.")
            report["checks"]["integrity"] = True
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise VerificationError("FOREIGN_KEY_CHECK_FAILED", "Catalog has invalid foreign-key references.")
            report["checks"]["foreign_keys"] = True
            actual = connection.execute(
                "SELECT " + ", ".join(SOURCE_FIELDS) + " FROM source_snapshots ORDER BY source_name"
            ).fetchall()
            if actual != expected:
                raise VerificationError("SOURCE_METADATA_MISMATCH", "Catalog source metadata differs from the manifest.")
            report["checks"]["source_metadata"] = True
            has_area_rows = connection.execute(
                "SELECT EXISTS(SELECT 1 FROM streets WHERE source_id LIKE 'osm-area/%')"
            ).fetchone()[0]
            if has_area_rows and "processing_steps" not in tables:
                raise VerificationError(
                    "INVALID_ENRICHMENT_PROVENANCE",
                    "Area records exist without enrichment provenance.",
                )
            if "processing_steps" in tables:
                required = {
                    "step_name", "source_name", "source_sha256", "tool",
                    "algorithm_version",
                }
                columns = {
                    row[1]
                    for row in connection.execute(
                        'PRAGMA table_info("processing_steps")'
                    )
                }
                if not columns.issuperset(required):
                    raise VerificationError(
                        "INVALID_ENRICHMENT_PROVENANCE",
                        "Area-enrichment provenance has an invalid schema.",
                    )
                step = connection.execute(
                    "SELECT source_name,source_sha256,tool,algorithm_version "
                    "FROM processing_steps WHERE step_name = ?",
                    (AREA_ENRICHMENT_STEP,),
                ).fetchone()
                expected_sources = {
                    (row[0], row[3]) for row in expected
                }
                if (
                    step is None
                    or (step[0], step[1]) not in expected_sources
                    or step[2] != "tools/enrich_gazetteer_areas.py"
                    or step[3] != AREA_ENRICHMENT_VERSION
                ):
                    raise VerificationError(
                        "INVALID_ENRICHMENT_PROVENANCE",
                        "Area-enrichment provenance does not match this verifier.",
                    )
                report["checks"]["area_enrichment"] = True
                report["area_enrichment"] = {
                    "step": AREA_ENRICHMENT_STEP,
                    "algorithm_version": AREA_ENRICHMENT_VERSION,
                }
            report["counts"] = {
                table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                for table in REQUIRED_COLUMNS
            }
            report["coverage"] = _coverage(connection, report["counts"])
        _require_unchanged(db_path, original_state)
        report["initialization_seconds_after_validation"] = _initialization_seconds(db_path)
        _require_unchanged(db_path, original_state)
        report["checks"]["runtime_initialization"] = True
        report["success"] = True
    except VerificationError as error:
        report["errors"].append({"code": error.code, "message": str(error)})
    except sqlite3.Error:
        report["errors"].append({"code": "DATABASE_ERROR", "message": "Cannot read a valid SQLite catalog."})
    except OSError:
        report["errors"].append({"code": "DATABASE_READ_FAILED", "message": "Cannot read the database artifact."})
    return report


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
        # Replace the directory entry; never follow a newly introduced alias.
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.report is not None and any(
            args.report.resolve() == source.resolve()
            or (args.report.exists() and source.exists() and args.report.samefile(source))
            for source in (args.db, args.manifest)
        ):
            raise VerificationError("REPORT_PATH_CONFLICT", "Report must not overwrite an input file.")
    except (VerificationError, OSError) as error:
        report = _new_report()
        code = error.code if isinstance(error, VerificationError) else "REPORT_PATH_ERROR"
        report["errors"].append({"code": code, "message": "Report path cannot safely be used."})
    else:
        report = verify_gazetteer(args.db, args.manifest)
        if args.report is not None:
            try:
                _write_report(args.report, report)
            except OSError:
                report["success"] = False
                report["errors"].append({"code": "REPORT_WRITE_FAILED", "message": "Cannot write the verification report."})
    print(json.dumps(report))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
