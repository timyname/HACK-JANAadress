"""Audit every address point and export lazy browser district indexes.

Only cities that have child urban districts in KATO receive an index. Cities
without that administrative level correctly keep ``district = null``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Sequence


def normalize(value: str) -> str:
    chars: list[str] = []
    pending = False
    for source in unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"):
        if source.isalnum() or source == "_":
            if pending and chars:
                chars.append(" ")
            pending = False
            chars.append(source)
        else:
            pending = bool(chars)
    return "".join(chars).strip()


def normalize_house(value: str) -> str:
    chars: list[str] = []
    for source in unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"):
        char = "-" if source in "‐‑‒–—−" else source
        if char.isalnum() or char == "_":
            chars.append(char)
        elif char in "/-" and chars and chars[-1] not in "/-":
            chars.append(char)
    return "".join(chars).strip("/-")


def slug(value: str) -> str:
    transliteration = str.maketrans({
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l",
        "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
        "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch",
        "ш": "sh", "щ": "sh", "ы": "y", "э": "e", "ю": "yu", "я": "ya",
        "ә": "a", "ғ": "g", "қ": "q", "ң": "n", "ө": "o", "ұ": "u",
        "ү": "u", "һ": "h", "і": "i", "ь": "", "ъ": "",
    })
    result = normalize(value).translate(transliteration)
    return re.sub(r"[^a-z0-9]+", "-", result).strip("-")


def kato_district_cities(connection: sqlite3.Connection) -> dict[tuple[str, str], set[str]]:
    connection.row_factory = sqlite3.Row
    cities = connection.execute(
        "SELECT DISTINCT canonical,kato_code,province FROM admin_records "
        "WHERE field='city' AND kato_code IS NOT NULL"
    ).fetchall()
    districts = connection.execute(
        "SELECT DISTINCT canonical,kato_code,province,city FROM admin_records "
        "WHERE field='district' AND kato_code IS NOT NULL"
    ).fetchall()
    result: dict[tuple[str, str], set[str]] = defaultdict(set)
    for district in districts:
        if district["city"]:
            result[(district["province"], district["city"])].add(district["canonical"])
            continue
        candidates: list[tuple[int, str]] = []
        for city in cities:
            if city["province"] != district["province"]:
                continue
            prefix = city["kato_code"].rstrip("0")
            if prefix and district["kato_code"].startswith(prefix):
                candidates.append((len(prefix), city["canonical"]))
        if not candidates:
            continue
        longest = max(length for length, _ in candidates)
        best = {city for length, city in candidates if length == longest}
        if len(best) == 1:
            result[(district["province"], best.pop())].add(district["canonical"])
    return result


def export(db: Path, output: Path, report_path: Path, unresolved_path: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    applicable = kato_district_cities(connection)
    aliases: dict[int, tuple[str, ...]] = defaultdict(tuple)
    grouped_aliases: dict[int, list[str]] = defaultdict(list)
    for row in connection.execute("SELECT street_id,normalized FROM street_aliases"):
        grouped_aliases[int(row["street_id"])].append(row["normalized"])
    aliases = {key: tuple(dict.fromkeys(values)) for key, values in grouped_aliases.items()}
    street_districts: dict[int, tuple[str, ...]] = defaultdict(tuple)
    grouped_districts: dict[int, list[str]] = defaultdict(list)
    for row in connection.execute("SELECT street_id,district FROM street_districts"):
        grouped_districts[int(row["street_id"])].append(row["district"])
    street_districts = {
        key: tuple(dict.fromkeys(values)) for key, values in grouped_districts.items()
    }

    candidates: dict[tuple[str, str], dict[tuple[str, str], set[str]]] = {
        key: defaultdict(set) for key in applicable
    }
    coverage = {
        key: {
            "total": 0,
            "exact": 0,
            "street_fallback": 0,
            "ambiguous": 0,
            "parent_conflict": 0,
            "missing": 0,
        }
        for key in applicable
    }
    unresolved: list[tuple[str, str, str, str, str]] = []
    rows = connection.execute(
        "SELECT ap.street_id,ap.house,ap.district,s.province,s.city,s.name "
        "FROM address_points ap JOIN streets s ON s.id=ap.street_id "
        "ORDER BY s.province,s.city,s.name,ap.house"
    )
    total_points = 0
    not_applicable = 0
    for row in rows:
        total_points += 1
        key = (row["province"], row["city"])
        if key not in applicable:
            not_applicable += 1
            continue
        stats = coverage[key]
        stats["total"] += 1
        allowed = applicable[key]
        all_street_districts = street_districts.get(int(row["street_id"]), ())
        districts = tuple(value for value in all_street_districts if value in allowed)
        district = row["district"]
        if district and district in allowed:
            stats["exact"] += 1
        elif district and district not in allowed:
            stats["parent_conflict"] += 1
            unresolved.append((*key, row["name"], row["house"], "district_parent_conflict"))
            continue
        elif len(districts) == 1:
            district = districts[0]
            stats["street_fallback"] += 1
        elif len(districts) > 1 or len(all_street_districts) > 1:
            stats["ambiguous"] += 1
            unresolved.append((*key, row["name"], row["house"], "ambiguous_street"))
            continue
        else:
            stats["missing"] += 1
            unresolved.append((*key, row["name"], row["house"], "no_district_evidence"))
            continue
        house = normalize_house(row["house"])
        if not house:
            continue
        for alias in aliases.get(int(row["street_id"]), (normalize(row["name"]),)):
            candidates[key][(alias, house)].add(str(district))

    manifest_cities: dict[str, dict[str, object]] = {}
    conflicts = 0
    exported = 0
    for key in sorted(applicable):
        province, city = key
        nested: dict[str, dict[str, str]] = defaultdict(dict)
        for (alias, house), districts in candidates[key].items():
            if len(districts) != 1:
                conflicts += 1
                continue
            nested[alias][house] = next(iter(districts))
            exported += 1
        filename = f"{slug(province)}--{slug(city)}.json"
        payload = {
            "schema_version": 1,
            "province": province,
            "city": city,
            "districts": sorted(applicable[key]),
            "addresses": {alias: dict(sorted(houses.items())) for alias, houses in sorted(nested.items())},
        }
        (output / filename).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        manifest_cities[normalize(city)] = {
            "province": province,
            "city": city,
            "file": filename,
            "district_count": len(applicable[key]),
            **coverage[key],
        }

    manifest = {"schema_version": 1, "cities": manifest_cities}
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    with unresolved_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("province", "city", "street", "house", "reason"))
        writer.writerows(unresolved)
    report = {
        "database": str(db),
        "total_address_points_scanned": total_points,
        "cities_with_kato_urban_districts": len(applicable),
        "points_in_cities_without_urban_district_level": not_applicable,
        "exported_alias_house_mappings": exported,
        "conflicting_alias_house_mappings_skipped": conflicts,
        "unresolved_applicable_address_points": len(unresolved),
        "cities": manifest_cities,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    connection.close()
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--unresolved", type=Path, required=True)
    args = parser.parse_args(argv)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.unresolved.parent.mkdir(parents=True, exist_ok=True)
    print(json.dumps(export(args.db, args.output, args.report, args.unresolved), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
