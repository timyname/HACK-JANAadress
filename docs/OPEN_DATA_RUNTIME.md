# Offline public-data runtime

Documentation updated: 2026-10-18 (source snapshot unchanged). The runtime never downloads data. Network access is
used only by the reproducible build step; raw source files are excluded from
Git and the runtime image.

## Sources and decisions

| Source | Authority and snapshot | Fields used | Licence/status | Integration decision and red flags |
|---|---|---|---|---|
| [KATO NK RK 11-2025](https://stat.gov.kz/ru/classifiers/statistical/21/) | Bureau of National Statistics of Kazakhstan; workbook updated 2026-09-18 | `province`, `city`, `district`, `living_area`; Russian canonical labels, Kazakh aliases, KATO code and hierarchy | [BNS reuse terms](https://stat.gov.kz/ru/description/) allow free reuse, modification and combination with attribution | Primary administrative catalogue. It has no streets, house numbers or geometries. Administrative containers are not emitted as cities. |
| [Kazakhstan Geofabrik extract](https://download.geofabrik.de/asia/kazakhstan.html) | OpenStreetMap regional extract; snapshot through 2026-10-15T20:21:00Z | named roads, aliases, road geometry, administrative/place geometry, explicit `addr:*` nodes and building ways | [ODbL 1.0 and attribution](https://www.openstreetmap.org/copyright); Geofabrik updates daily | Primary geometry/street layer. Any distributed OSM-derived database needs OpenStreetMap attribution; ODbL share-alike obligations can apply to a public derivative database. OSM completeness and tagging are community supplied, not an official address register. Kazakhstan admin-level tagging is not treated as canonical: shapes are joined to KATO names/aliases. |
| [State Address Register description](https://www.gov.kz/org/adilet-akt/press/article/details/76721) and [public RKA lookup](https://data.egov.kz/services/rka) | Ministry/eGov; described as the official source of addresses and 16-digit RKA identifiers | Potential validation of settlements, geonyms, buildings and rooms | Public interactive/API lookup; no verified bulk download, bulk licence or update contract found | Do not scrape or ship. Reconsider only after obtaining documented bulk access and redistribution terms. |
| [QazPost address API](https://open.post.kz/services/details/26) | QazPost | postcode/address/RKA lookup | Bearer token, rate limits, no verified bulk export/open redistribution licence | Not suitable for an offline redistributable snapshot. Optional online validation would be a separate product mode. |
| [Postal index dataset](https://data.egov.kz/datasets/view?index=kazakstan_respublikasynyn_posh1) | Kazakhstan open-data portal | postcodes | Portal marks the dataset not current; annual cadence/API-key access and historic completeness complaints | Excluded from the core gazetteer. It cannot safely establish current address membership. |
| [National Spatial Data Infrastructure WFS](https://map.gov.kz/geoserver/ows?service=WFS&version=2.0.0&request=GetCapabilities) | Kazakhstan NSDI/Geoserver; public capabilities advertise no fees/access constraints | selected regional KATO boundaries, districts and roads | Per-layer redistribution licence and uniform national coverage were not verified; update cadence is not stated | Useful for manual comparison only. Do not redistribute layers until the responsible authority confirms layer-specific licence and cadence. |

The older KATO copy on data.egov.kz is archived and is not used as the
canonical classifier. Commercial map products such as 2GIS are deliberately
excluded without a compatible data licence.

## Reproducible build

The immutable source metadata and SHA-256 values are stored in
`data/public/source-manifest.json`. To fetch a new declared snapshot:

```powershell
.\.venv\Scripts\python.exe tools\fetch_sources.py
```

To build the local delivery artifact:

```powershell
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe tools\build_gazetteer.py `
  --kato data\public\raw\KATO_18.09.2026.xlsx `
  --osm data\public\raw\kazakhstan-2026-10-15.osm.pbf `
  --manifest data\public\source-manifest.json `
  --output data\public\gazetteer.sqlite `
  --report data\public\build-report.json

.\.venv\Scripts\python.exe tools\enrich_gazetteer_areas.py `
  --base data\public\gazetteer.sqlite `
  --osm data\public\raw\kazakhstan-2026-10-15.osm.pbf `
  --manifest data\public\source-manifest.json `
  --output data\public\gazetteer.enriched.sqlite `
  --report data\public\area-enrichment-report.json
```

After verification, atomically replace `gazetteer.sqlite` with the enriched
artifact. The enrichment output must differ from `--base`; the command never
mutates its input database.

The build streams OSM with pyosmium, converts only relevant geometries with
Shapely, joins them to KATO aliases, and writes SQLite atomically. A street
intersecting multiple district polygons retains every district and remains
ambiguous at runtime. Only an explicit OSM address point/building with the
requested house number may resolve that ambiguity. No address ranges or house
numbers are generated.

The second offline stage imports only named, non-administrative OSM polygons
tagged `place=suburb`, `place=quarter`, or `place=neighbourhood`. A mapping is
stored only when its complete valid geometry is properly contained by exactly one KATO-matched
district and its city is either spatially unique or uniquely inherited from
the district's KATO parent. Ambiguous city or district matches are skipped.
These records share the exact lookup engine with roads. Explicit lexical area
markers carry the roles `микрорайон` or `квартал`; an unmarked OSM
`place` value remains typeless evidence and can participate in an explicit
lookup without asserting a lexical role. An untyped same-name lookup remains
conservative. No official sample value is used by the build. The JSON report
also records process peak RSS for the full enrichment pass.

The builder emits JSON-lines progress to stderr for input, boundary extraction,
spatial indexing, feature extraction, address-point joins, finalization and
database writing; stdout remains the final JSON report. Wall and CPU timings
are local to each progress producer. Intermediate counters are not an ETA.
Static valid administrative polygons are prepared once without simplification;
queries preserve strict `within`/`intersects` results and use the original
STRtree predicate when invalid geometry is involved. `tools/profile_spatial.py`
reproduces a synthetic microbenchmark, not an end-to-end OSM performance claim.

SQLite alias insertion deduplicates normalized lookup keys per administrative
entity or street while retaining the first surface form. It never merges
different entities sharing a name. On failure, the connection is closed before
removing its temporary file, and any previous output database stays untouched.

Named OSM ways are segment-level provenance records. At runtime, segments with
the same exact alias and city are treated as one logical street: metadata and
district membership are unioned, while conflicts remain ambiguous. Address
points are attached only to an exact street alias in the same known locality
and within 0.05 degrees of a candidate segment.

## Runtime and update policy

### Verify a completed artifact

Run this only after the builder exits successfully, with no writer or update
process touching the database:

```powershell
.\.venv\Scripts\python.exe tools\verify_gazetteer.py `
  --db data\public\gazetteer.sqlite `
  --manifest data\public\source-manifest.json `
  --report output\gazetteer-verification.json
```

The verifier opens SQLite read-only, checks the required schema, integrity and
foreign keys, compares all eight source metadata fields to the manifest, and
records database SHA-256, size, table counts and non-null membership coverage.
It then initializes the actual `Gazetteer` and closes its connection. Exit code
zero means structural/provenance consistency, not geographic accuracy or
completeness: even an empty structurally valid catalog may pass. Source files
are not rehashed by this command. SQLite integrity checks also do not replace
application-domain validation; the report records the SQLite version and scope.

The initialization measurement follows hashing/validation and is neither a
cold-start measurement nor Docker startup or HTTP batch timing. Ordinary
database replacement or mutation during verification produces a failure; the
tool assumes a completed immutable artifact, not an adversarial filesystem.
The optional report is written by atomic replacement and must not alias either
input file. Review unresolved counts and run the separate runtime/accuracy
checks even when this verifier succeeds.

### Load and refresh

`Gazetteer` opens SQLite read-only, loads exact aliases, streets, districts and
house-point memberships into memory once, then performs `find_admin` and
`lookup_street` without SQL. Admin matching enumerates bounded token n-grams;
it does not run a regular expression for every KATO alias. Fuzzy matching is
not used to invent membership. The compact build report records observed
counts and elapsed time for this snapshot.

Some official KATO city-district rows have a null city field even though their
code is nested under a city record (for example, `751310000` under Almaty's
`750000000`). At runtime, and only for such null-city district rows, the parent
city is hydrated when there is one unique longest city KATO-code prefix within
the same province. The database row, canonical district name, province and
KATO code remain unchanged. A missing or non-unique code parent stays
unresolved; there is no weaker province-only fallback when an explicit city is
supplied.

For updates, download a new dated KATO workbook and Geofabrik extract, record
new hashes and versions, rebuild, rerun the fixture/integration tests, review
count deltas, and replace the SQLite artifact as a unit. Do not overwrite old
manifest metadata with an undated `latest` claim.

## Known limits

- KATO has administrative names but no boundary geometry; the spatial join is
  limited by OSM boundary coverage and exact bilingual alias agreement.
- OSM street/address coverage varies by settlement. An unmatched house stays
  unresolved; it does not inherit a guessed district.
- A locality name can legitimately occur in several provinces. Without an
  explicit contextual match the runtime preserves ambiguity.
- This build does not claim State Address Register completeness and does not
  measure hidden hackathon-evaluator accuracy.
- Longitude/latitude distance is used only as a conservative 0.05-degree
  association bound, not as a metric-distance or routing calculation.
