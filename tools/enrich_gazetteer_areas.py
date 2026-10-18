"""Add verified OSM residential-area-to-district mappings to a gazetteer."""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import time
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from jana.gazetteer import _district_name_key, normalize
from jana.street_types import canonical_street_type
from tools.build_gazetteer import PreparedAdminIndex, unique_normalized_aliases


_NAME_KEYS = ("name:ru", "official_name:ru", "name", "name:kk", "official_name")
_AREA_PLACES = {"suburb", "quarter", "neighbourhood"}
AREA_ENRICHMENT_VERSION = "3"
_AREA_PREFIXES = (
    "микрорайон ", "мкр-н ", "мкр. ", "мкр ",
    "шағын аудан ", "ықшам аудан ",
    "квартал ", "кв-л ", "орам ",
)
_AREA_SUFFIXES = (
    " микрорайон", " мкр-н", " мкр",
    " шағын ауданы", " ықшам ауданы",
    " квартал", " орамы",
)


def _peak_rss_mib() -> float:
    """Return the process peak resident set size on supported platforms."""

    if os.name == "nt":
        import psutil

        return float(psutil.Process().memory_info().peak_wset) / (1024 * 1024)

    import resource

    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak_rss / (1024 * 1024) if sys.platform == "darwin" else peak_rss / 1024


def _artifact_state(path: Path) -> tuple[int, ...]:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _consistent_province(
    spatial_provinces: Sequence[dict[str, Any]],
    district: dict[str, Any],
    spatial_city: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    """Select a province only when all available parent evidence agrees."""

    if len(spatial_provinces) > 1:
        return False, None
    values = [
        spatial_provinces[0].get("canonical") if spatial_provinces else None,
        district.get("province"),
        spatial_city.get("province") if spatial_city is not None else None,
    ]
    present = [str(value) for value in values if value]
    if len({normalize(value) for value in present}) > 1:
        return False, None
    return True, present[0] if present else None


def _paths_conflict(target: Path, sources: Sequence[Path]) -> bool:
    resolved_target = target.resolve()
    for source in sources:
        resolved_source = source.resolve()
        if resolved_target == resolved_source:
            return True
        if target.exists() and source.exists() and target.samefile(source):
            return True
    return False


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def area_alias_forms(value: str) -> tuple[str, ...]:
    """Return source-preserving and longest-marker-free area aliases."""

    source = " ".join(value.split()).strip(" ,")
    forms = [source]
    folded = source.casefold()
    for marker in sorted(_AREA_PREFIXES, key=len, reverse=True):
        if folded.startswith(marker):
            stripped = source[len(marker):].strip(" ,")
            if stripped:
                forms.append(stripped)
            break
    for marker in sorted(_AREA_SUFFIXES, key=len, reverse=True):
        if folded.endswith(marker):
            stripped = source[:-len(marker)].strip(" ,")
            if stripped:
                forms.append(stripped)
            break
    return tuple(dict.fromkeys(forms))


def split_area_surface(value: str, place: str) -> tuple[str, str | None]:
    """Return a marker-free name and canonical exact14 area role."""

    forms = area_alias_forms(value)
    name = forms[-1]
    folded = " ".join(value.split()).casefold()
    explicit_quarter = any(
        folded.startswith(marker) or folded.endswith(marker.strip())
        for marker in (*_AREA_PREFIXES[-3:], *_AREA_SUFFIXES[-2:])
    )
    explicit_microdistrict = any(
        folded.startswith(marker) or folded.endswith(marker.strip())
        for marker in (*_AREA_PREFIXES[:-3], *_AREA_SUFFIXES[:-2])
    )
    role = "квартал" if explicit_quarter else "микрорайон" if explicit_microdistrict else None
    return name, canonical_street_type(role)


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row.get(key) for key in (
        "field", "kato_code", "canonical", "province", "city", "district", "living_area"
    ))


def _load_admin_indexes(
    connection: sqlite3.Connection,
) -> tuple[dict[str, tuple[dict[str, Any], ...]], dict[str, tuple[dict[str, Any], ...]]]:
    connection.row_factory = sqlite3.Row
    rows = [dict(source) for source in connection.execute(
        """
        SELECT field, canonical, alias, normalized, kato_code,
               province, city, district, living_area
          FROM admin_aliases
        """
    )]
    cities_by_province: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        if row["field"] != "city" or not row["province"] or not row["kato_code"]:
            continue
        cities_by_province[normalize(row["province"])][_identity(row)] = row
    for row in rows:
        if (
            row["field"] != "district"
            or row["city"]
            or not row["province"]
            or not row["kato_code"]
        ):
            continue
        district_code = str(row["kato_code"])
        candidates = []
        for city in cities_by_province.get(normalize(row["province"]), {}).values():
            prefix = str(city["kato_code"]).rstrip("0")
            if prefix and district_code.startswith(prefix):
                candidates.append((len(prefix), city))
        if candidates:
            longest = max(length for length, _ in candidates)
            best = [city for length, city in candidates if length == longest]
            if len(best) == 1:
                row["city"] = best[0].get("city") or best[0]["canonical"]

    by_alias: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = defaultdict(dict)
    districts: dict[str, dict[tuple[Any, ...], dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        identity = _identity(row)
        by_alias[row["normalized"]][identity] = row
        if row["field"] == "district":
            key = _district_name_key(row["alias"])
            if key:
                districts[key][identity] = row
    return (
        {key: tuple(values.values()) for key, values in by_alias.items()},
        {key: tuple(values.values()) for key, values in districts.items()},
    )


def enrich_database(
    base_database: str | Path,
    osm_path: str | Path,
    output_database: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """Copy *base_database* and append unambiguous residential-area mappings."""

    import osmium
    from shapely import wkb

    started = time.perf_counter()
    base_path = Path(base_database).resolve()
    osm_file = Path(osm_path).resolve()
    output_path = Path(output_database).resolve()
    manifest_file = Path(manifest_path).resolve()
    if not base_path.is_file() or not osm_file.is_file() or not manifest_file.is_file():
        raise FileNotFoundError("base gazetteer, OSM snapshot and manifest are required")
    if _paths_conflict(output_path, (base_path, osm_file, manifest_file)):
        raise ValueError("output must differ from and not alias any input")
    base_state = _artifact_state(base_path)

    digest = hashlib.sha256()
    with osm_file.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    osm_sha256 = digest.hexdigest()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    manifest_matches = [
        row for row in manifest.get("sources", [])
        if row.get("filename") == osm_file.name
    ]
    if len(manifest_matches) != 1 or manifest_matches[0].get("sha256") != osm_sha256:
        raise ValueError("OSM snapshot does not match the declared manifest")
    osm_source = manifest_matches[0]

    def require_source_metadata(source: sqlite3.Connection) -> None:
        source.row_factory = sqlite3.Row
        stored = source.execute(
            "SELECT * FROM source_snapshots WHERE source_name = ?",
            (osm_source["source_name"],),
        ).fetchone()
        if stored is None or any(stored[key] != osm_source[key] for key in osm_source):
            raise ValueError("base gazetteer provenance does not match the manifest")

    with sqlite3.connect(f"file:{base_path}?mode=ro", uri=True) as source:
        require_source_metadata(source)
        already_enriched = source.execute(
            "SELECT EXISTS(SELECT 1 FROM streets WHERE source_id LIKE 'osm-area/%')"
        ).fetchone()[0]
        has_steps = source.execute(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='processing_steps')"
        ).fetchone()[0]
        if already_enriched or has_steps:
            raise ValueError("base gazetteer is already area-enriched")
        admin_by_alias, district_by_key = _load_admin_indexes(source)

    def resolve_admin(tags: Any, allowed: set[str]) -> tuple[dict[str, Any], ...]:
        for key in _NAME_KEYS:
            value = tags.get(key)
            if not value:
                continue
            matches = {
                _identity(row): row
                for row in admin_by_alias.get(normalize(value), ())
                if row["field"] in allowed
            }
            if "district" in allowed:
                matches.update({
                    _identity(row): row
                    for row in district_by_key.get(_district_name_key(value), ())
                })
            if len(matches) == 1:
                return tuple(matches.values())
        return ()

    shapes: dict[str, list[tuple[Any, dict[str, Any]]]] = defaultdict(list)
    named_areas: list[dict[str, Any]] = []
    factory = osmium.geom.WKBFactory()

    class AreaHandler(osmium.SimpleHandler):
        def area(self, area: Any) -> None:
            tags = area.tags
            place = str(tags.get("place") or "")
            is_candidate = (
                place in _AREA_PLACES
                and tags.get("boundary") != "administrative"
                and any(tags.get(key) for key in _NAME_KEYS)
            )
            is_admin = tags.get("boundary") == "administrative"
            if not is_candidate and not is_admin:
                return
            try:
                geometry = wkb.loads(factory.create_multipolygon(area), hex=True)
            except (RuntimeError, ValueError):
                return
            if geometry.is_empty:
                return

            if is_admin:
                level_text = tags.get("admin_level")
                if level_text and re.fullmatch(r"[0-9]+", level_text):
                    level = int(level_text)
                    if level in {4, 5}:
                        allowed = {"province", "city"}
                    elif level in {6, 7}:
                        allowed = {"district", "city"}
                    elif level >= 8:
                        allowed = {"city", "district", "living_area"}
                    else:
                        allowed = set()
                    for row in resolve_admin(tags, allowed):
                        shapes[row["field"]].append((geometry, row))

            if is_candidate:
                surfaces = tuple(dict.fromkeys(
                    tags.get(key) for key in _NAME_KEYS if tags.get(key)
                ))
                named_areas.append({
                    "source_id": (
                        f"osm-area/{'way' if area.from_way() else 'relation'}/{area.orig_id()}"
                    ),
                    "place": place,
                    "surfaces": surfaces,
                    "geometry": geometry,
                })

    handler = AreaHandler()
    handler.apply_file(str(osm_file), locations=True, idx="flex_mem")

    trees: dict[str, tuple[PreparedAdminIndex, list[dict[str, Any]]]] = {}
    for field, entries in shapes.items():
        trees[field] = (
            PreparedAdminIndex([geometry for geometry, _ in entries]),
            [row for _, row in entries],
        )

    def spatial_rows(geometry: Any, field: str) -> tuple[dict[str, Any], ...]:
        if field not in trees:
            return ()
        tree, rows = trees[field]
        distinct: dict[tuple[Any, ...], dict[str, Any]] = {}
        for index in tree.query(geometry, predicate="within"):
            position = int(index)
            if (
                not geometry.is_valid
                or not tree.valid[position]
                or not tree.geometries[position].contains_properly(geometry)
            ):
                continue
            row = rows[position]
            distinct[_identity(row)] = row
        return tuple(distinct.values())

    accepted: list[dict[str, Any]] = []
    ambiguous_city = 0
    ambiguous_district = 0
    for area in named_areas:
        geometry = area["geometry"]
        cities = spatial_rows(geometry, "city")
        districts = spatial_rows(geometry, "district")
        provinces = spatial_rows(geometry, "province")
        if len(cities) > 1:
            ambiguous_city += 1
            continue
        if len(districts) != 1:
            ambiguous_district += 1
            continue
        district = districts[0]
        spatial_city = cities[0] if cities else None
        inherited_city = district.get("city")
        if spatial_city is None and not inherited_city:
            ambiguous_city += 1
            continue
        if spatial_city is not None and inherited_city and inherited_city not in {
            spatial_city.get("canonical"), spatial_city.get("city")
        }:
            ambiguous_district += 1
            continue
        city_name = (
            spatial_city.get("city") or spatial_city["canonical"]
            if spatial_city is not None
            else inherited_city
        )
        province_consistent, province = _consistent_province(
            provinces, district, spatial_city
        )
        if not province_consistent:
            ambiguous_district += 1
            continue
        canonical, area_type = split_area_surface(area["surfaces"][0], area["place"])
        aliases = tuple(dict.fromkeys(
            form
            for surface in area["surfaces"]
            for form in area_alias_forms(surface)
        ))
        accepted.append({
            "source_id": area["source_id"],
            "name": canonical,
            "aliases": aliases,
            "street_type": area_type,
            "city": city_name,
            "province": province,
            "district": district.get("district") or district["canonical"],
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=f".{output_path.name}.", dir=output_path.parent)
    os.close(handle)
    temp_path = Path(temp_name)
    connection: sqlite3.Connection | None = None
    try:
        if _artifact_state(base_path) != base_state:
            raise ValueError("base gazetteer changed during enrichment")
        with sqlite3.connect(f"file:{base_path}?mode=ro", uri=True) as source:
            require_source_metadata(source)
            connection = sqlite3.connect(temp_path)
            source.backup(connection)
        if _artifact_state(base_path) != base_state:
            raise ValueError("base gazetteer changed during enrichment")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processing_steps (
                step_name TEXT PRIMARY KEY,
                source_name TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                tool TEXT NOT NULL,
                algorithm_version TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT OR REPLACE INTO processing_steps
                (step_name,source_name,source_sha256,tool,algorithm_version)
            VALUES (?,?,?,?,?)
            """,
            (
                "osm-residential-area-enrichment",
                osm_source["source_name"],
                osm_sha256,
                "tools/enrich_gazetteer_areas.py",
                AREA_ENRICHMENT_VERSION,
            ),
        )
        inserted = 0
        for area in accepted:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO streets
                    (source_id,name,street_type,city,normalized_city,province,living_area,geometry_wkb)
                VALUES (?,?,?,?,?,?,NULL,NULL)
                """,
                (
                    area["source_id"], area["name"], area["street_type"], area["city"],
                    normalize(area["city"]), area["province"],
                ),
            )
            if cursor.rowcount != 1:
                continue
            inserted += 1
            street_id = int(cursor.lastrowid)
            aliases = unique_normalized_aliases((area["name"], *area["aliases"]))
            connection.executemany(
                "INSERT INTO street_aliases(street_id,alias,normalized) VALUES (?,?,?)",
                [(street_id, alias, normalized) for alias, normalized in aliases],
            )
            connection.execute(
                "INSERT INTO street_districts(street_id,district) VALUES (?,?)",
                (street_id, area["district"]),
            )
        connection.execute("ANALYZE")
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("enriched database failed integrity_check")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.DatabaseError("enriched database failed foreign_key_check")
        connection.close()
        connection = None
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, output_path)
    finally:
        if connection is not None:
            try:
                connection.rollback()
            finally:
                connection.close()
        if temp_path.exists():
            temp_path.unlink()

    return {
        "source_area_candidates": len(named_areas),
        "inserted_area_mappings": inserted,
        "skipped_ambiguous_city": ambiguous_city,
        "skipped_ambiguous_district": ambiguous_district,
        "admin_shapes": sum(len(entries) for entries in shapes.values()),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "output_bytes": output_path.stat().st_size,
        "source_name": osm_source["source_name"],
        "source_sha256": osm_sha256,
        "algorithm_version": AREA_ENRICHMENT_VERSION,
        "peak_rss_mib": round(_peak_rss_mib(), 3),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--osm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if args.report:
        try:
            conflict = _paths_conflict(
                args.report, (args.base, args.osm, args.manifest, args.output)
            )
        except OSError:
            parser.error("--report path cannot be safely resolved")
        if conflict:
            parser.error("--report must not overwrite or alias an input/output")
    report = enrich_database(args.base, args.osm, args.output, args.manifest)
    if args.report:
        _write_json_atomic(args.report, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
