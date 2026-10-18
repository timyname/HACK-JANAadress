from __future__ import annotations

import contextlib
import importlib
import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from shapely import LineString, MultiPolygon, Point, Polygon, box, is_prepared
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import build_gazetteer as builder


def prepared_index(geometries):
    assert hasattr(builder, "PreparedAdminIndex"), "Prepared static polygon index is missing"
    return builder.PreparedAdminIndex(geometries)


@pytest.mark.parametrize("predicate", ["within", "intersects"])
def test_prepared_index_matches_tree_on_boundary_holes_multipolygon(predicate):
    geometries = [
        Polygon([(0, 0), (5, 0), (5, 5), (0, 5)], holes=[[(1, 1), (2, 1), (2, 2), (1, 2)]]),
        MultiPolygon([box(6, 0, 7, 1), box(8, 0, 9, 1)]),
        box(4, 0, 6, 3),
        Polygon(),
    ]
    queries = [
        Point(0, 0), Point(1, 1.5), Point(1.5, 1.5), Point(3, 3),
        Point(4.5, 1), Point(6.5, 0.5), Point(8.5, 0.5), Point(7.5, 0.5),
        LineString([(-1, 3), (10, 3)]), LineString([(0, 0), (5, 0)]),
        LineString([(1.1, 1.1), (1.9, 1.9)]), Point(), None,
    ]
    baseline = STRtree(geometries)
    expected = [baseline.query(query, predicate=predicate).tolist() for query in queries]
    index = prepared_index(geometries)
    assert [index.query(query, predicate).tolist() for query in queries] == expected
    assert all(is_prepared(geometry) for geometry in geometries)


@pytest.mark.parametrize("predicate", ["within", "intersects"])
def test_invalid_polygons_use_original_predicate_without_repair(predicate):
    invalid = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    assert not invalid.is_valid
    geometries = [invalid, box(4, 4, 5, 5)]
    baseline = STRtree(geometries)
    queries = [Point(1, 1), Point(0.5, 0.25), Point(4.5, 4.5), LineString([(-1, 1), (3, 1)])]
    expected = [baseline.query(query, predicate=predicate).tolist() for query in queries]
    index = prepared_index(geometries)
    assert [index.query(query, predicate).tolist() for query in queries] == expected
    assert not is_prepared(invalid)
    assert invalid.wkt == geometries[0].wkt


def test_empty_index_and_other_predicates_keep_original_tree_semantics():
    assert prepared_index([]).query(Point(0, 0), "within").tolist() == []
    geometries = [box(0, 0, 1, 1)]
    query = Point(0, 0)
    assert prepared_index(geometries).query(query, "touches").tolist() == STRtree(geometries).query(query, predicate="touches").tolist()


def test_extraction_reports_phases_counts_and_times_to_stderr(tmp_path):
    source = tmp_path / "empty.osm"
    source.write_text("<osm version='0.6' generator='jana-test'><node id='1' lat='0' lon='0'/></osm>", encoding="utf-8")
    stderr, stdout = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
        streets, points, counts = builder.extract_osm_records(source, [])
    assert not streets and not points
    assert counts["address_points_seen"] == 0
    assert stdout.getvalue() == ""
    events = [json.loads(line) for line in stderr.getvalue().splitlines()]
    assert events, "Builder must expose phase progress while no SQLite exists yet"
    for phase in ("boundaries", "spatial_index", "features", "point_join", "finalize"):
        assert any(item["phase"] == phase and item["event"] == "start" for item in events)
        assert any(item["phase"] == phase and item["event"] == "done" for item in events)
    assert all(item["elapsed_seconds"] >= 0 and item["cpu_seconds"] >= 0 for item in events)
    assert all("raw_address" not in item for item in events)
    assert next(item for item in events if item["phase"] == "boundaries" and item["event"] == "done")["nodes_seen"] == 1


def test_cli_reports_input_and_database_phases_without_polluting_final_json(tmp_path, capsys):
    fixture = tmp_path / "fixture.json"
    manifest = tmp_path / "manifest.json"
    fixture.write_text(json.dumps({"admins": [], "streets": [], "address_points": []}), encoding="utf-8")
    manifest.write_text(json.dumps({"sources": []}), encoding="utf-8")
    assert builder.main(["--fixture-json", str(fixture), "--manifest", str(manifest),
                         "--output", str(tmp_path / "out.sqlite")]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["streets"] == 0
    events = [json.loads(line) for line in captured.err.splitlines()]
    for phase in ("input", "database"):
        assert any(item["phase"] == phase and item["event"] == "start" for item in events)
        assert any(item["phase"] == phase and item["event"] == "done" for item in events)


def test_reproducible_spatial_profile_checks_exact_hits():
    assert importlib.util.find_spec("tools.profile_spatial") is not None, "Reproducible profile is missing"
    profile = importlib.import_module("tools.profile_spatial")
    report = profile.run_profile(vertices=200, point_count=20, road_count=10)
    assert report["fixture_kind"] == "synthetic_irregular_regions_not_osm"
    assert report["vertices_per_polygon"] == 200
    assert report["all_hits_identical"] is True
    assert {item["predicate"] for item in report["measurements"]} == {"within", "intersects"}
    assert all(item["baseline_seconds"] > 0 and item["prepared_seconds"] > 0 for item in report["measurements"])
