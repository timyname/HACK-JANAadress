"""Reconstruct test strings from licensed public OSM address fields.

These are NOT customer-entered addresses, independent geographic ground truth,
or a held-out organizer dataset. Every error is an artificial transformation.
No production parser is used to produce the expected fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path


FIELDS = (
    "province", "city", "district", "living_area", "street_type", "street_name",
    "house", "apartment", "entrance", "floor", "landmark",
)
MARKERS = {
    "улица": "ул.", "проспект": "пр.", "переулок": "пер.",
    "бульвар": "бул.", "шоссе": "шоссе", "проезд": "проезд",
    "площадь": "пл.", "набережная": "наб.", "микрорайон": "мкр.",
}


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _typo(name: str) -> str:
    """One deterministic, visible duplication; preserve it in the oracle."""
    for index, char in enumerate(name):
        if char.isalpha():
            return name[:index] + char + name[index:]
    return name


def _public_district(canonical: str | None) -> str | None:
    """Organizer DOCX table 1: district name excludes the word 'район'.

    This oracle formatting rule is independent of the production parser.
    Preserve the complete source label in the reconstructed input string.
    """
    if canonical is None:
        return None
    return re.sub(r"^район(?:\s+|$)|(?:^|\s+)район$", "", canonical.strip(),
                  flags=re.IGNORECASE).strip() or None


def make_cases(connection: sqlite3.Connection, *, count: int, seed: str) -> list[dict]:
    if count < 0:
        raise ValueError("count must not be negative")
    columns = ("source_id", "city", "province", "name", "street_type", "house", "district")
    rows = connection.execute("""
        SELECT a.source_id, s.city, s.province, s.name, s.street_type, a.house, a.district
        FROM address_points a JOIN streets s ON a.street_id = s.id
        WHERE s.city IS NOT NULL AND s.name IS NOT NULL AND a.house IS NOT NULL
        ORDER BY a.source_id, s.id, a.house
    """).fetchall()
    records = [dict(zip(columns, row)) for row in rows]
    records.sort(key=lambda row: _digest(seed + "\0" + json.dumps(row, ensure_ascii=False, sort_keys=True)))
    cases: list[dict] = []
    seen: set[str] = set()
    # Distinct points first, but rotate the four variants before reaching a cap.
    for round_index in range(4):
        for source_index, row in enumerate(records):
            variant = (round_index + source_index) % 4
            if len(cases) >= count:
                return cases
            street_type = row["street_type"]
            if street_type not in MARKERS:
                # Do not invent a street type or reinterpret an unknown label.
                continue
            street_name = _typo(row["name"]) if variant == 3 else row["name"]
            admin = []
            if row["province"] and row["province"] != row["city"]:
                province = row["province"]
                admin.append(province if re.search(r"\bобласть\b", province, re.I) else f"{province} область")
            admin.append(f"г. {row['city']}")
            if row["district"]:
                district = row["district"]
                admin.append(district if re.search(r"\bрайон\b", district, re.I) else f"район {district}")
            street = f"{MARKERS[street_type]} {street_name}"
            house = f"дом {row['house']}"
            parts = [*admin, street, house]
            if variant == 1:
                parts[-2] = f"{MARKERS[street_type]}{street_name}"
                parts[-1] = f"д{row['house']}"
            elif variant == 2:
                parts = [house, street, *admin]
            raw = ", ".join(parts)
            if raw in seen:
                continue
            seen.add(raw)
            identifier = "osm-" + _digest(seed + "\0" + str(row["source_id"]) + "\0" + raw)[:20]
            item = {"id": identifier, "raw_address": raw}
            expected = {**item, **dict.fromkeys(FIELDS), "confidence": 1.0}
            expected.update(
                province=row["province"], city=row["city"], district=_public_district(row["district"]),
                street_type=street_type, street_name=street_name, house=str(row["house"]),
            )
            source_id = str(row["source_id"])
            source_url = "https://www.openstreetmap.org/" + source_id
            cases.append({
                "input": item, "expected": expected,
                "kind": "reconstructed_from_public_osm", "source_url": source_url,
                "source_id": source_id, "variant": variant,
                "tier": "clean" if variant == 0 else "corrupted",
                "locale": "source_surface",
                "operations": ["none", "marker_fusion", "reorder", "street_character_duplication"][variant:variant + 1],
                "metric_applicability": {"district": row["district"] is not None, "living_area": False},
                "context_caveat": "District/province may come from spatial joins in the same public catalog; not independent geographic truth.",
            })
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/public/gazetteer.sqlite"))
    parser.add_argument("--out", type=Path, default=Path("output/public-corpus"))
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", default="jana-public-v1")
    args = parser.parse_args()
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        cases = make_cases(connection, count=args.count, seed=args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for filename, key in (("input.json", "input"), ("expected.json", "expected")):
        content = json.dumps([row[key] for row in cases], ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        (args.out / filename).write_text(content, encoding="utf-8", newline="\n")
        hashes[filename] = _digest(content)
    oracle = [{key: value for key, value in row.items() if key not in ("input", "expected")} for row in cases]
    oracle_text = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in oracle)
    (args.out / "oracle.jsonl").write_text(oracle_text, encoding="utf-8", newline="\n")
    hashes["oracle.jsonl"] = _digest(oracle_text)
    with args.db.open("rb") as handle:
        database_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    manifest = {
        "kind": "reconstructed_from_public_osm", "seed": args.seed,
        "requested_count": args.count, "actual_count": len(cases),
        "distinct_source_points": len({row["source_id"] for row in cases}),
        "database_sha256": database_hash,
        "artifacts": hashes,
        "attribution": "© OpenStreetMap contributors, ODbL 1.0; administrative labels: Bureau of National Statistics of Kazakhstan (KATO).",
        "license_url": "https://www.openstreetmap.org/copyright",
        "limitations": [
            "Reconstructed strings, not real customer typos.",
            "Oracle generated from public structured fields independently of parser.",
            "Spatial district/province labels reuse the runtime source and do not validate geographic correctness independently.",
            "Unobserved fields are null, not evidence of their absence; full-record accuracy is diagnostic only.",
            "Confidence is a placeholder ignored by field accuracy, not an organizer label.",
        ],
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"out": str(args.out), "count": len(cases), "distinct_source_points": manifest["distinct_source_points"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
