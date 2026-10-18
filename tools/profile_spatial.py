"""Bounded, reproducible spatial microbenchmark; not a full OSM build estimate."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import shapely
from shapely import LineString, Point, Polygon
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_gazetteer import PreparedAdminIndex


def run_profile(vertices: int = 30000, point_count: int = 2000, road_count: int = 300) -> dict:
    """Compare identical ordered hits using detailed, irregular synthetic regions."""
    rng = random.Random(20261018)
    ring = []
    for index in range(vertices):
        angle = index * 2 * math.pi / vertices
        radius = 1 + 0.08 * math.sin(27 * angle)
        ring.append((radius * math.cos(angle), radius * math.sin(angle)))
    geometries = [Polygon(ring), Polygon([(x + 3, y) for x, y in ring])]
    points = [Point(rng.uniform(-1.2, 4.2), rng.uniform(-1.2, 1.2)) for _ in range(point_count)]
    roads = [LineString([(rng.uniform(-1.2, 4.2), rng.uniform(-1.2, 1.2)),
                         (rng.uniform(-1.2, 4.2), rng.uniform(-1.2, 1.2))]) for _ in range(road_count)]
    tree = STRtree(geometries)
    # Both baselines run before preparing the shared polygons.
    baselines = {}
    for predicate, queries in (("within", points), ("intersects", roads)):
        started = time.perf_counter()
        expected = [tree.query(query, predicate=predicate).tolist() for query in queries]
        baselines[predicate] = (expected, time.perf_counter() - started)
    prepare_started = time.perf_counter()
    prepared = PreparedAdminIndex(geometries)
    prepare_seconds = time.perf_counter() - prepare_started
    measurements = []
    for predicate, queries in (("within", points), ("intersects", roads)):
        started = time.perf_counter()
        actual = [prepared.query(query, predicate).tolist() for query in queries]
        prepared_seconds = time.perf_counter() - started
        expected, baseline_seconds = baselines[predicate]
        measurements.append({
            "predicate": predicate,
            "queries": len(queries),
            "baseline_seconds": baseline_seconds,
            "prepared_seconds": prepared_seconds,
            "speedup": baseline_seconds / prepared_seconds,
            "identical_ordered_hits": actual == expected,
        })
    return {
        "fixture_kind": "synthetic_irregular_regions_not_osm",
        "seed": 20261018,
        "polygons": len(geometries),
        "vertices_per_polygon": vertices,
        "shapely_version": shapely.__version__,
        "geos_version": shapely.geos_version_string,
        "prepare_seconds": prepare_seconds,
        "all_hits_identical": all(item["identical_ordered_hits"] for item in measurements),
        "measurements": measurements,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = run_profile()
    encoded = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["all_hits_identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
