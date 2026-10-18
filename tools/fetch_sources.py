"""Fetch and checksum immutable public-data snapshots for the gazetteer build."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


KATO_URL = (
    "https://stat.gov.kz/upload/iblock/a7d/eqogm92xc0udumlzfzffhcrutk0fjhb4/"
    "%D0%9A%D0%90%D0%A2%D0%9E_18.09.2026.xlsx"
)
OSM_URL = "https://download.geofabrik.de/asia/kazakhstan-latest.osm.pbf"


def _write_manifest_entry(manifest_path: str | Path, entry: dict[str, str]) -> None:
    manifest = Path(manifest_path)
    current = {"sources": []}
    if manifest.exists():
        current = json.loads(manifest.read_text(encoding="utf-8"))
    current["sources"] = [
        item for item in current.get("sources", [])
        if item.get("source_name") != entry["source_name"]
    ] + [entry]
    current["sources"].sort(key=lambda item: item["source_name"])
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def record_existing_snapshot(
    path: str | Path,
    manifest_path: str | Path,
    *,
    source_name: str,
    url: str,
    version: str,
    license_name: str,
    license_url: str,
) -> dict[str, str]:
    """Checksum and register an already completed/resumed download."""

    snapshot = Path(path)
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    digest = hashlib.sha256()
    with snapshot.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    entry = {
        "source_name": source_name,
        "url": url,
        "filename": snapshot.name,
        "sha256": digest.hexdigest(),
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "version": version,
        "license_name": license_name,
        "license_url": license_url,
    }
    _write_manifest_entry(manifest_path, entry)
    return entry


def snapshot_file(
    url: str,
    destination: str | Path,
    manifest_path: str | Path,
    *,
    source_name: str,
    version: str,
    license_name: str,
    license_url: str,
) -> dict[str, str]:
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    handle, temp_name = tempfile.mkstemp(prefix=f".{destination_path.name}.", dir=destination_path.parent)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with urllib.request.urlopen(url, timeout=120) as response, temp_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
        os.replace(temp_path, destination_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    entry = {
        "source_name": source_name,
        "url": url,
        "filename": destination_path.name,
        "sha256": digest.hexdigest(),
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "version": version,
        "license_name": license_name,
        "license_url": license_url,
    }
    _write_manifest_entry(manifest_path, entry)
    return entry


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/public/raw"))
    parser.add_argument("--manifest", type=Path, default=Path("data/public/source-manifest.json"))
    args = parser.parse_args(argv)
    snapshot_file(
        KATO_URL,
        args.output_dir / "KATO_18.09.2026.xlsx",
        args.manifest,
        source_name="kato-nk-rk-11-2025",
        version="2026-09-18",
        license_name="Bureau of National Statistics reuse terms",
        license_url="https://stat.gov.kz/ru/description/",
    )
    snapshot_file(
        OSM_URL,
        args.output_dir / "kazakhstan-2026-10-15.osm.pbf",
        args.manifest,
        source_name="openstreetmap-kazakhstan-geofabrik",
        version="2026-10-15T20:21:00Z",
        license_name="Open Database License 1.0",
        license_url="https://www.openstreetmap.org/copyright",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
