# Public data attribution

The Jana Address gazetteer combines these dated public-data snapshots:

- Administrative names and hierarchy: Kazakhstan Bureau of National
  Statistics, **KATO NK RK 11-2025**, workbook updated 2026-09-18.
  Source: <https://stat.gov.kz/ru/classifiers/statistical/21/>.
  Reuse terms: <https://stat.gov.kz/ru/description/>.
- Streets, geometries, and explicit address points: **© OpenStreetMap
  contributors**, Kazakhstan Geofabrik extract through
  2026-10-15T20:21:00Z. Source:
  <https://download.geofabrik.de/asia/kazakhstan.html>.
  Open Database License 1.0: <https://www.openstreetmap.org/copyright>.
- Exact, source-attributed Karaganda address contexts: the official
  [Karaganda Region geoportal](https://geo.qarobl.kz/) address register,
  cross-checked against the regional akimat's published
  [Kazybek Bi electoral boundaries](https://www.karaganda-region.kz/rus/vibory_1_1_1/).
  The shipped JSON contains only the listed verified facts and source IDs, not
  a copy of the regional register. The portal does not state bulk
  redistribution terms, so no bulk scrape is included.

The exact source URLs, versions, fetch timestamps, and SHA-256 checksums are in
`source-manifest.json`. Rebuild instructions and data limitations are in
`docs/research/OPEN_DATA_RUNTIME.md` in the source repository.

If `gazetteer.sqlite` is redistributed publicly, preserve this attribution and
review the ODbL requirements that apply to an OSM-derived database, including
the database share-alike provisions where applicable.
