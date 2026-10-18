from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3
import stat
import tempfile

from jana.gazetteer import Gazetteer, normalize
from jana.parser import parse_record
from tools.build_gazetteer import (
    AdminRecord,
    SourceRecord,
    StreetRecord,
    create_database,
)
from tools.enrich_gazetteer_areas import (
    _consistent_province,
    _paths_conflict,
    area_alias_forms,
    enrich_database,
    main as enrichment_main,
    split_area_surface,
)
from tools.verify_gazetteer import verify_gazetteer


def test_area_surface_helpers_use_longest_marker_and_shared_roles() -> None:
    assert area_alias_forms("12 шағын ауданы") == ("12 шағын ауданы", "12")
    assert split_area_surface("12 шағын ауданы", "suburb") == ("12", "микрорайон")
    assert split_area_surface("квартал 7", "quarter") == ("7", "квартал")
    assert split_area_surface("Первый", "suburb") == ("Первый", None)


def test_parent_province_requires_all_available_evidence_to_agree() -> None:
    consistent, value = _consistent_province(
        ({"canonical": "Область Б"},),
        {"province": "Область А"},
        {"province": "Область А"},
    )
    assert consistent is False
    assert value is None

    consistent, value = _consistent_province(
        ({"canonical": "Область А"},),
        {"province": "область а"},
        {"province": "Область А"},
    )
    assert consistent is True
    assert value == "Область А"


def test_report_path_conflicts_include_filesystem_aliases() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        alias = root / "alias"
        source.write_text("preserve", encoding="utf-8")
        alias.hardlink_to(source)
        assert _paths_conflict(source, (source,)) is True
        assert _paths_conflict(alias, (source,)) is True
        assert _paths_conflict(root / "other", (source,)) is False


def test_enrichment_adds_only_spatially_verified_area_mapping() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        base = root / "base.sqlite"
        enriched = root / "enriched.sqlite"
        osm = root / "fixture.osm"
        manifest = root / "source-manifest.json"
        source = SourceRecord(
            source_name="fixture-osm",
            url="https://example.invalid/fixture.osm",
            filename="fixture.osm",
            sha256="pending",
            fetched_at="2026-10-18T00:00:00Z",
            version="fixture-v1",
            license_name="fixture",
            license_url="https://example.invalid/license",
        )
        admins = [
                AdminRecord(
                    field="city",
                    canonical="Тестоград",
                    kato_code="120000000",
                    province="Тестовая область",
                    city="Тестоград",
                ),
                AdminRecord(
                    field="district",
                    canonical="Центральный район",
                    aliases=("Центральный",),
                    kato_code="121000000",
                    province="Тестовая область",
                    district="Центральный район",
                ),
            ]
        osm.write_text(
            """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='jana-test'>
  <node id='1' lat='0' lon='0'/><node id='2' lat='0' lon='10'/>
  <node id='3' lat='10' lon='10'/><node id='4' lat='10' lon='0'/>
  <node id='5' lat='0' lon='5'/><node id='6' lat='10' lon='5'/>
  <node id='7' lat='1' lon='1'/><node id='8' lat='1' lon='2'/>
  <node id='9' lat='2' lon='2'/><node id='10' lat='2' lon='1'/>
  <node id='11' lat='3' lon='4'/><node id='12' lat='3' lon='6'/>
  <node id='13' lat='4' lon='6'/><node id='14' lat='4' lon='4'/>
  <node id='15' lat='5' lon='0'/><node id='16' lat='5' lon='1'/>
  <node id='17' lat='6' lon='1'/><node id='18' lat='6' lon='0'/>
  <way id='101'><nd ref='1'/><nd ref='5'/><nd ref='6'/><nd ref='4'/><nd ref='1'/>
    <tag k='boundary' v='administrative'/><tag k='admin_level' v='6'/>
    <tag k='name' v='Центральный район'/>
  </way>
  <way id='200'><nd ref='7'/><nd ref='8'/><nd ref='9'/><nd ref='10'/><nd ref='7'/>
    <tag k='area' v='yes'/><tag k='place' v='suburb'/>
    <tag k='landuse' v='residential'/><tag k='name' v='Первый микрорайон'/>
  </way>
  <way id='201'><nd ref='11'/><nd ref='12'/><nd ref='13'/><nd ref='14'/><nd ref='11'/>
    <tag k='area' v='yes'/><tag k='place' v='suburb'/>
    <tag k='landuse' v='residential'/><tag k='name' v='Пограничный микрорайон'/>
  </way>
  <way id='202'><nd ref='15'/><nd ref='16'/><nd ref='17'/><nd ref='18'/><nd ref='15'/>
    <tag k='area' v='yes'/><tag k='place' v='suburb'/>
    <tag k='landuse' v='residential'/><tag k='name' v='Граничный микрорайон'/>
  </way>
</osm>
""",
            encoding="utf-8",
        )
        osm_sha256 = hashlib.sha256(osm.read_bytes()).hexdigest()
        source = SourceRecord(**{**source.__dict__, "sha256": osm_sha256})
        create_database(
            base,
            admins,
            [
                StreetRecord(
                    source_id="way/street",
                    name="Тестовая",
                    street_type="улица",
                    city="Тестоград",
                ),
                StreetRecord(
                    source_id="way/typeless-road",
                    name="Арман",
                    street_type=None,
                    city="Тестоград",
                    districts=("Дорожный район",),
                ),
            ],
            [],
            [source],
        )
        manifest.write_text(
            json.dumps({"sources": [source.__dict__]}), encoding="utf-8"
        )
        base_sha256 = hashlib.sha256(base.read_bytes()).hexdigest()
        manifest_sha256 = hashlib.sha256(manifest.read_bytes()).hexdigest()

        for conflicting_database in (manifest, root / "manifest-output-alias"):
            if conflicting_database != manifest:
                conflicting_database.hardlink_to(manifest)
            try:
                enrich_database(
                    base, osm, conflicting_database, manifest
                )
            except ValueError as error:
                assert "not alias any input" in str(error)
            else:
                raise AssertionError("an output/input path conflict must fail")
            assert hashlib.sha256(manifest.read_bytes()).hexdigest() == manifest_sha256

        conflicting_output = root / "conflicting-output.sqlite"
        try:
            enrichment_main([
                "--base", str(base),
                "--osm", str(osm),
                "--manifest", str(manifest),
                "--output", str(conflicting_output),
                "--report", str(base),
            ])
        except SystemExit as error:
            assert error.code == 2
        else:
            raise AssertionError("a report/input path conflict must fail preflight")
        assert hashlib.sha256(base.read_bytes()).hexdigest() == base_sha256
        assert not conflicting_output.exists()

        report = enrich_database(base, osm, enriched, manifest)
        assert enriched.stat().st_mode & stat.S_IROTH

        with sqlite3.connect(enriched) as connection:
            cursor = connection.execute(
                """
                INSERT INTO streets
                    (source_id,name,street_type,city,normalized_city,province,living_area,geometry_wkb)
                VALUES (?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    "osm-area/way/test-typeless",
                    "Арман-2",
                    None,
                    "Тестоград",
                    normalize("Тестоград"),
                    "Тестовая область",
                ),
            )
            street_id = int(cursor.lastrowid)
            connection.execute(
                "INSERT INTO street_aliases(street_id,alias,normalized) VALUES (?,?,?)",
                (street_id, "Арман-2", normalize("Арман-2")),
            )
            connection.execute(
                "INSERT INTO street_districts(street_id,district) VALUES (?,?)",
                (street_id, "Районный район"),
            )

        assert hashlib.sha256(base.read_bytes()).hexdigest() == base_sha256
        assert report["source_area_candidates"] == 3
        assert report["inserted_area_mappings"] == 1
        with Gazetteer(enriched) as gazetteer:
            result = gazetteer.lookup_area(
                "Тестоград", "Первый", area_type="микрорайон"
            )
        assert result["matched"] is True
        assert result["ambiguous"] is False
        assert result["district"] == "Центральный район"
        assert result["street_type"] == "микрорайон"
        with Gazetteer(enriched) as gazetteer:
            road_as_area = gazetteer.lookup_area(
                "Тестоград", "Арман", area_type="микрорайон"
            )
        assert road_as_area["matched"] is False
        with Gazetteer(enriched) as gazetteer:
            area_as_road = gazetteer.lookup_street(
                "Тестоград", "Арман-2", street_type="улица"
            )
        assert area_as_road["matched"] is False
        with Gazetteer(enriched) as gazetteer:
            combined = parse_record(
                {
                    "id": "combined",
                    "raw_address": (
                        "г. Тестоград, мкр. Первый, ул. Тестовая, дом 7"
                    ),
                },
                gazetteer=gazetteer,
                explain=True,
            )
        assert combined["result"]["living_area"] == "Первый"
        assert combined["result"]["street_name"] == "Тестовая"
        assert combined["result"]["district"] == "Центральный"
        assert "gazetteer_area_ambiguous" not in combined["warnings"]
        with Gazetteer(enriched) as gazetteer:
            crossing = gazetteer.lookup_street(
                "Тестоград", "Пограничный", street_type="микрорайон"
            )
        assert crossing["matched"] is False
        with Gazetteer(enriched) as gazetteer:
            touching = gazetteer.lookup_street(
                "Тестоград", "Граничный", street_type="микрорайон"
            )
        assert touching["matched"] is False
        with sqlite3.connect(enriched) as connection:
            step = connection.execute(
                "SELECT source_name,source_sha256,algorithm_version FROM processing_steps"
            ).fetchone()
        assert step == ("fixture-osm", osm_sha256, "3")
        verification = verify_gazetteer(enriched, manifest)
        assert verification["success"] is True
        assert verification["checks"]["area_enrichment"] is True
        assert verification["area_enrichment"]["algorithm_version"] == "3"

        missing_step = root / "missing-step.sqlite"
        with sqlite3.connect(enriched) as source_connection:
            with sqlite3.connect(missing_step) as target_connection:
                source_connection.backup(target_connection)
                target_connection.execute("DROP TABLE processing_steps")
        missing_step_report = verify_gazetteer(missing_step, manifest)
        assert missing_step_report["success"] is False
        assert missing_step_report["errors"][0]["code"] == (
            "INVALID_ENRICHMENT_PROVENANCE"
        )

        rerun_output = root / "rerun-must-not-exist.sqlite"
        try:
            enrich_database(enriched, osm, rerun_output, manifest)
        except ValueError as error:
            assert "already area-enriched" in str(error)
        else:
            raise AssertionError("an enriched base must fail closed")
        assert not rerun_output.exists()

        bad_manifest = root / "bad-manifest.json"
        bad_manifest.write_text(
            json.dumps({
                "sources": [{**source.__dict__, "sha256": "0" * 64}],
            }),
            encoding="utf-8",
        )
        try:
            enrich_database(base, osm, root / "must-not-exist.sqlite", bad_manifest)
        except ValueError as error:
            assert "does not match" in str(error)
        else:
            raise AssertionError("mismatched OSM provenance must fail closed")
        assert not (root / "must-not-exist.sqlite").exists()

        with sqlite3.connect(base) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_area_insert
                BEFORE INSERT ON streets
                WHEN NEW.source_id LIKE 'osm-area/%'
                BEGIN
                    SELECT RAISE(ABORT, 'forced area insert failure');
                END
                """
            )
        preserved_output = root / "preserved.sqlite"
        preserved_output.write_bytes(b"previous-output")
        try:
            enrich_database(base, osm, preserved_output, manifest)
        except sqlite3.IntegrityError as error:
            assert "forced area insert failure" in str(error)
        else:
            raise AssertionError("injected insert failure must abort enrichment")
        assert preserved_output.read_bytes() == b"previous-output"
        assert not list(root.glob(".preserved.sqlite.*"))
