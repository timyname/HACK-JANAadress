"""Read-only runtime access to the Jana Address gazetteer."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz, process

from .street_types import canonical_street_type


_WORD = re.compile(r"\w", re.UNICODE)
_TOKEN = re.compile(r"\w+", re.UNICODE)
_DISTRICT_TYPE_PREFIX = re.compile(r"^(?:район|р\s+н|аудан|ауданы|ауд)\s+", re.UNICODE)
_DISTRICT_TYPE_SUFFIX = re.compile(r"\s+(?:район|р\s+н|аудан|ауданы|ауд)$", re.UNICODE)
_FUZZY_STREET_SCORE_CUTOFF = 80.0
_FUZZY_STREET_SCORE_MARGIN = 5.0


def normalize(value: str) -> str:
    """Return the conservative search form used by the build and runtime."""

    normalized, _ = _normalize_with_offsets(value)
    return normalized


def normalize_house(value: str) -> str:
    """Normalize a house key while preserving meaningful slash/dash syntax."""

    chars: list[str] = []
    pending_space = False
    for source_char in unicodedata.normalize("NFKC", value).casefold().replace("ё", "е"):
        char = "-" if source_char in "‐‑‒–—−" else source_char
        if _WORD.fullmatch(char):
            if pending_space and chars and chars[-1] not in "/-":
                chars.append(" ")
            pending_space = False
            chars.append(char)
        elif char in "/-":
            while chars and chars[-1] == " ":
                chars.pop()
            if chars and chars[-1] not in "/-":
                chars.append(char)
            pending_space = False
        else:
            pending_space = bool(chars)
    return "".join(chars).strip()


def _district_name_key(value: str) -> str:
    """Normalize a district's own name while discarding only its type marker."""

    key = normalize(value)
    key = _DISTRICT_TYPE_PREFIX.sub("", key)
    key = _DISTRICT_TYPE_SUFFIX.sub("", key)
    return key.strip()


def _admin_identity(row: sqlite3.Row) -> tuple[Any, ...]:
    """Aliases/languages do not change an entity's role, code or parents."""

    return tuple(row[field] for field in (
        "field", "kato_code", "canonical", "province", "city", "district", "living_area"
    ))


def _normalize_with_offsets(value: str) -> tuple[str, list[int]]:
    chars: list[str] = []
    offsets: list[int] = []
    pending_space: int | None = None
    for index, source_char in enumerate(value):
        expanded = unicodedata.normalize("NFKC", source_char).casefold().replace("ё", "е")
        for char in expanded:
            if _WORD.fullmatch(char):
                if pending_space is not None and chars:
                    chars.append(" ")
                    offsets.append(pending_space)
                pending_space = None
                chars.append(char)
                offsets.append(index)
            else:
                if chars and pending_space is None:
                    pending_space = index
    return "".join(chars), offsets


class Gazetteer:
    """Small, read-only SQLite lookup facade used by the parser."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Gazetteer artifact is missing: {self.path}")
        uri = self.path.as_uri().replace("file://", "file:") + "?mode=ro&immutable=1"
        self._connection = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        admin_aliases = tuple(self._connection.execute(
            """
            SELECT field, canonical, alias, normalized, language, kato_code,
                   province, city, district, living_area
              FROM admin_aliases
             ORDER BY length(normalized) DESC, normalized, canonical
            """
        ))
        self._admin_by_alias: dict[str, tuple[sqlite3.Row, ...]] = {}
        grouped_admins: dict[str, list[sqlite3.Row]] = defaultdict(list)
        city_identities: dict[str, set[tuple[Any, ...]]] = defaultdict(set)
        for row in admin_aliases:
            grouped_admins[row["normalized"]].append(row)
            if row["field"] == "city":
                city_identities[normalize(row["canonical"])].add(_admin_identity(row))
        self._admin_by_alias = {
            alias: tuple(rows) for alias, rows in grouped_admins.items()
        }
        # Streets expose a city name, so they cannot select among same-named
        # administrative entities even when only one has an indexed street.
        self._ambiguous_city_names = {
            name for name, identities in city_identities.items() if len(identities) > 1
        }
        city_rows_by_province: dict[str, dict[tuple[Any, ...], sqlite3.Row]] = defaultdict(dict)
        for row in admin_aliases:
            if row["field"] != "city" or not row["province"] or not row["kato_code"]:
                continue
            identity = (
                row["canonical"],
                row["kato_code"],
                row["province"],
                row["city"],
            )
            city_rows_by_province[normalize(row["province"])][identity] = row

        district_rows: dict[
            str, dict[tuple[Any, ...], dict[str, Any]]
        ] = defaultdict(dict)
        for row in admin_aliases:
            if row["field"] != "district":
                continue
            key = _district_name_key(row["alias"])
            if not key:
                continue
            item = dict(row)
            if not item["city"] and item["province"] and item["kato_code"]:
                district_code = str(item["kato_code"])
                parent_candidates = []
                for city_row in city_rows_by_province.get(
                    normalize(str(item["province"])), {}
                ).values():
                    city_prefix = str(city_row["kato_code"]).rstrip("0")
                    if city_prefix and district_code.startswith(city_prefix):
                        parent_candidates.append((len(city_prefix), city_row))
                if parent_candidates:
                    longest_prefix = max(length for length, _ in parent_candidates)
                    best = [
                        city_row
                        for length, city_row in parent_candidates
                        if length == longest_prefix
                    ]
                    if len(best) == 1:
                        item["city"] = best[0]["city"] or best[0]["canonical"]
            identity = (
                item["canonical"],
                item["kato_code"],
                item["province"],
                item["city"],
                item["district"],
            )
            district_rows[key][identity] = item
        self._district_by_name = {
            key: tuple(rows.values()) for key, rows in district_rows.items()
        }
        self._max_admin_tokens = max(
            (len(alias.split()) for alias in self._admin_by_alias), default=0
        )

        street_rows = tuple(self._connection.execute(
            """
            SELECT id, source_id, name, street_type, city, normalized_city,
                   province, living_area
              FROM streets
             ORDER BY source_id
            """
        ))
        self._streets = {int(row["id"]): dict(row) for row in street_rows}
        self._street_types = {
            street_id: canonical_street_type(street["street_type"])
            for street_id, street in self._streets.items()
        }
        street_districts: dict[int, list[str]] = defaultdict(list)
        for row in self._connection.execute(
            "SELECT street_id, district FROM street_districts ORDER BY district"
        ):
            street_districts[int(row["street_id"])].append(row["district"])
        self._street_districts = {
            street_id: tuple(districts)
            for street_id, districts in street_districts.items()
        }
        global_aliases: dict[str, set[int]] = defaultdict(set)
        city_aliases: dict[tuple[str, str], set[int]] = defaultdict(set)
        global_alias_types: dict[str, set[str]] = defaultdict(set)
        city_alias_types: dict[tuple[str, str], set[str]] = defaultdict(set)
        global_typed_aliases: dict[tuple[str, str], set[int]] = defaultdict(set)
        city_typed_aliases: dict[tuple[str, str, str], set[int]] = defaultdict(set)
        global_typeless_aliases: dict[str, set[int]] = defaultdict(set)
        city_typeless_aliases: dict[tuple[str, str], set[int]] = defaultdict(set)
        for row in self._connection.execute(
            "SELECT street_id, normalized FROM street_aliases"
        ):
            street_id = int(row["street_id"])
            alias = row["normalized"]
            global_aliases[alias].add(street_id)
            normalized_city = self._streets[street_id]["normalized_city"]
            street_type = self._street_types[street_id]
            if street_type:
                global_alias_types[alias].add(street_type)
                global_typed_aliases[(alias, street_type)].add(street_id)
            else:
                global_typeless_aliases[alias].add(street_id)
            if normalized_city:
                city_key = (normalized_city, alias)
                city_aliases[city_key].add(street_id)
                if street_type:
                    city_alias_types[city_key].add(street_type)
                    city_typed_aliases[(*city_key, street_type)].add(street_id)
                else:
                    city_typeless_aliases[city_key].add(street_id)
        self._streets_by_alias = {
            alias: tuple(sorted(ids)) for alias, ids in global_aliases.items()
        }
        self._streets_by_city_alias = {
            key: tuple(sorted(ids)) for key, ids in city_aliases.items()
        }
        fuzzy_aliases_by_city: dict[str, set[str]] = defaultdict(set)
        for normalized_city, alias in self._streets_by_city_alias:
            fuzzy_aliases_by_city[normalized_city].add(alias)
        self._fuzzy_street_aliases_by_city = {
            city: tuple(sorted(aliases))
            for city, aliases in fuzzy_aliases_by_city.items()
        }
        self._street_types_by_alias = {
            alias: frozenset(types) for alias, types in global_alias_types.items()
        }
        self._street_types_by_city_alias = {
            key: frozenset(types) for key, types in city_alias_types.items()
        }
        self._streets_by_typed_alias = {
            key: tuple(sorted(ids)) for key, ids in global_typed_aliases.items()
        }
        self._streets_by_city_typed_alias = {
            key: tuple(sorted(ids)) for key, ids in city_typed_aliases.items()
        }
        self._typeless_streets_by_alias = {
            alias: tuple(sorted(ids)) for alias, ids in global_typeless_aliases.items()
        }
        self._typeless_streets_by_city_alias = {
            key: tuple(sorted(ids)) for key, ids in city_typeless_aliases.items()
        }
        point_districts: dict[tuple[int, str], set[str]] = defaultdict(set)
        for row in self._connection.execute(
            """
            SELECT street_id, house, district
              FROM address_points
             WHERE district IS NOT NULL
            """
        ):
            point_districts[(int(row["street_id"]), normalize_house(row["house"]))].add(
                row["district"]
            )
        self._point_districts = {
            key: tuple(sorted(values)) for key, values in point_districts.items()
        }

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "Gazetteer":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def find_admin(self, text: str) -> dict[str, Any]:
        return self._find_admin(text)

    def find_admin_contextual(
        self,
        text: str,
        *,
        province: str | None = None,
        district: str | None = None,
    ) -> dict[str, Any]:
        """Resolve administrative names with already parsed parent context.

        Kazakhstan contains many same-named settlements.  A province and an
        explicit district can safely narrow those candidates without turning
        a successful contextual match into a permanent ambiguity warning.
        """

        return self._find_admin(text, province=province, district=district)

    def resolve_city(
        self,
        fragment: str,
        *,
        province: str | None = None,
        district: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one explicit settlement component inside known parents."""

        rows = tuple(
            row for row in self._admin_by_alias.get(normalize(fragment), ())
            if row["field"] == "city"
        )
        if not rows:
            return {"source": "gazetteer", "matched": False, "ambiguous": False, "score": 0.0}

        matches = rows
        for field, value, key in (
            ("province", province, normalize),
            ("district", district, _district_name_key),
        ):
            if not value:
                continue
            expected = key(value)
            scoped = tuple(
                row for row in matches
                if row[field] and key(str(row[field])) == expected
            )
            if not scoped:
                return {
                    "source": "gazetteer", "matched": True, "ambiguous": True,
                    "parent_conflict": True, "score": 1.0,
                    "candidate_count": len(matches),
                }
            matches = scoped

        identities = {_admin_identity(row): row for row in matches}
        if len(identities) != 1:
            return {
                "source": "gazetteer", "matched": True, "ambiguous": True,
                "score": 1.0, "candidate_count": len(identities),
            }
        row = next(iter(identities.values()))
        return {
            "value": row["canonical"], "source": "gazetteer", "span": None,
            "matched": True, "ambiguous": False, "parent_match": bool(province or district),
            "score": 1.0, "language": row["language"], "kato_code": row["kato_code"],
        }

    def _find_admin(
        self,
        text: str,
        *,
        province: str | None = None,
        district: str | None = None,
    ) -> dict[str, Any]:
        normalized_text, offsets = _normalize_with_offsets(text)
        candidates: dict[str, list[dict[str, Any]]] = {}
        tokens = tuple(_TOKEN.finditer(normalized_text))
        for token_index, token in enumerate(tokens):
            for width in range(1, min(self._max_admin_tokens, len(tokens) - token_index) + 1):
                start = token.start()
                end = tokens[token_index + width - 1].end()
                needle = normalized_text[start:end]
                rows = self._admin_by_alias.get(needle)
                if not rows:
                    continue
                for row in rows:
                    if not offsets or end <= start:
                        continue
                    item = {
                        "value": row["canonical"],
                        "source": "gazetteer",
                        "span": [offsets[start], offsets[end - 1] + 1],
                        "matched": text[offsets[start] : offsets[end - 1] + 1],
                        "score": 1.0,
                        "language": row["language"],
                        "kato_code": row["kato_code"],
                        "_identity": _admin_identity(row),
                        "_province": row["province"],
                        "_district": row["district"],
                    }
                    candidates.setdefault(row["field"], []).append(item)

        result: dict[str, Any] = {}
        ambiguous_fields: list[str] = []
        for field, matches in candidates.items():
            longest = max(len(item["matched"]) for item in matches)
            best = [item for item in matches if len(item["matched"]) == longest]
            if field == "city":
                contextual = best
                if province:
                    province_key = normalize(province)
                    scoped = [
                        item for item in contextual
                        if item["_province"]
                        and normalize(str(item["_province"])) == province_key
                    ]
                    if scoped:
                        contextual = scoped
                if district:
                    district_key = _district_name_key(district)
                    scoped = [
                        item for item in contextual
                        if item["_district"]
                        and _district_name_key(str(item["_district"])) == district_key
                    ]
                    if scoped:
                        contextual = scoped
                best = contextual
            identities = {item["_identity"] for item in best}
            if len(identities) != 1:
                ambiguous_fields.append(field)
                continue
            result[field] = best[0]
            for private_key in ("_identity", "_province", "_district"):
                result[field].pop(private_key)
        if ambiguous_fields:
            result["ambiguous_fields"] = sorted(ambiguous_fields)
        return result

    def resolve_district(
        self,
        fragment: str,
        *,
        city: str | None = None,
        province: str | None = None,
    ) -> dict[str, Any]:
        """Resolve an explicit district fragment only inside a confirmed parent.

        District names are not globally unique in Kazakhstan.  The runtime
        therefore uses the district's own source fragment to select candidates,
        then requires an exact normalized city (preferred) or province match.
        All candidates are preloaded during initialization; this method performs
        no SQLite reads.
        """

        candidates = self._district_by_name.get(_district_name_key(fragment), ())
        if not candidates:
            return {
                "source": "gazetteer",
                "matched": False,
                "ambiguous": False,
                "score": 0.0,
            }

        supplied_parents = tuple(
            (field, value)
            for field, value in (("city", city), ("province", province))
            if value
        )
        if not supplied_parents:
            return {
                "source": "gazetteer",
                "matched": True,
                "ambiguous": True,
                "parent_required": True,
                "score": 1.0,
                "candidate_count": len(candidates),
            }

        matches = candidates
        for parent_field, parent_value in supplied_parents:
            normalized_parent = normalize(parent_value)
            matches = tuple(
                row
                for row in matches
                if row[parent_field]
                and normalize(str(row[parent_field])) == normalized_parent
            )
        if not matches:
            return {
                "source": "gazetteer",
                "matched": True,
                "ambiguous": True,
                "parent_conflict": True,
                "score": 1.0,
                "candidate_count": len(candidates),
            }
        if len(matches) != 1:
            return {
                "source": "gazetteer",
                "matched": True,
                "ambiguous": True,
                "score": 1.0,
                "candidate_count": len(matches),
            }

        row = matches[0]
        return {
            "value": row["canonical"],
            "source": "gazetteer",
            "span": None,
            "matched": True,
            "ambiguous": False,
            "parent_match": True,
            "score": 1.0,
            "language": row["language"],
            "kato_code": row["kato_code"],
        }

    def lookup_street(
        self,
        city: str | None,
        name: str,
        house: str | None = None,
        street_type: str | None = None,
        province: str | None = None,
    ) -> dict[str, Any]:
        normalized_name = normalize(name)
        normalized_city = normalize(city) if city else None
        if normalized_city:
            street_ids = self._streets_by_city_alias.get((normalized_city, normalized_name), ())
        else:
            street_ids = self._streets_by_alias.get(normalized_name, ())
        street_ids = tuple(
            street_id
            for street_id in street_ids
            if not str(self._streets[street_id]["source_id"]).startswith("osm-area/")
        )
        if not street_ids:
            return {"source": "gazetteer", "matched": False, "ambiguous": False, "score": 0.0}
        province_scoped = False
        if province:
            normalized_province = normalize(province)
            province_ids = tuple(
                street_id
                for street_id in street_ids
                if self._streets[street_id]["province"]
                and normalize(str(self._streets[street_id]["province"]))
                == normalized_province
            )
            if province_ids:
                street_ids = province_ids
                province_scoped = True
            elif any(self._streets[street_id]["province"] for street_id in street_ids):
                return {
                    "source": "gazetteer",
                    "matched": True,
                    "ambiguous": True,
                    "parent_conflict": True,
                    "score": 1.0,
                    "candidate_count": len(street_ids),
                }
        if street_type:
            normalized_type = canonical_street_type(street_type)
            if normalized_city:
                exact_ids = self._streets_by_city_typed_alias.get(
                    (normalized_city, normalized_name, normalized_type), ()
                )
                unknown_ids = self._typeless_streets_by_city_alias.get(
                    (normalized_city, normalized_name), ()
                )
            else:
                exact_ids = self._streets_by_typed_alias.get(
                    (normalized_name, normalized_type), ()
                )
                unknown_ids = self._typeless_streets_by_alias.get(normalized_name, ())
            street_scope = frozenset(street_ids)
            street_ids = tuple(
                street_id
                for street_id in (*exact_ids, *unknown_ids)
                if street_id in street_scope
            )
            if not street_ids:
                return {
                    "source": "gazetteer",
                    "matched": True,
                    "ambiguous": True,
                    "type_conflict": True,
                    "score": 1.0,
                    "candidate_count": len(street_scope),
                }
        else:
            known_types = {
                self._street_types[street_id]
                for street_id in street_ids
                if self._street_types[street_id]
            }
        if not street_type and len(known_types) > 1:
            return {
                "source": "gazetteer",
                "matched": True,
                "ambiguous": True,
                "score": 1.0,
                "candidate_count": len(street_ids),
            }
        include_source_id = len(street_ids) == 1
        conflicting_parent_metadata = any(
            len({
                self._streets[street_id][field]
                for street_id in street_ids
                if self._streets[street_id][field]
            }) > 1
            for field in ("province", "living_area")
        )
        if house and (normalized_city or province_scoped) and not conflicting_parent_metadata:
            normalized_house = normalize_house(house)
            house_ids = tuple(
                street_id
                for street_id in street_ids
                if (street_id, normalized_house) in self._point_districts
            )
            if house_ids:
                street_ids = house_ids
        return self._result_for_street_ids(
            normalized_city,
            street_ids,
            house=house,
            include_source_id=include_source_id,
            parent_scoped=province_scoped,
        )

    def fuzzy_lookup_street(
        self,
        city: str | None,
        name: str,
        house: str | None = None,
        *,
        province: str | None = None,
        min_score: float = _FUZZY_STREET_SCORE_CUTOFF,
        score_margin: float = _FUZZY_STREET_SCORE_MARGIN,
    ) -> dict[str, Any]:
        """Find a street with a missing marker or a spelling mistake.

        The search uses streets from the confirmed city only. The normal
        lookup still checks the street type, parent area, and house number.
        """

        normalized_city = normalize(city) if city else ""
        normalized_name = normalize(name)
        if not normalized_city or not normalized_name:
            return {
                "source": "gazetteer",
                "matched": False,
                "ambiguous": False,
                "score": 0.0,
            }

        choices = self._fuzzy_street_aliases_by_city.get(normalized_city, ())
        if not choices:
            return {
                "source": "gazetteer",
                "matched": False,
                "ambiguous": False,
                "score": 0.0,
            }

        matches = process.extract(
            normalized_name,
            choices,
            scorer=fuzz.ratio,
            score_cutoff=min_score,
            limit=10,
        )
        if not matches:
            return {
                "source": "gazetteer",
                "matched": False,
                "ambiguous": False,
                "score": 0.0,
            }

        best_alias, best_score, _ = matches[0]
        best_ids = frozenset(
            self._streets_by_city_alias.get((normalized_city, best_alias), ())
        )
        competing_aliases = {
            alias
            for alias, score, _ in matches[1:]
            if score >= best_score - score_margin
            and frozenset(
                self._streets_by_city_alias.get((normalized_city, alias), ())
            )
            != best_ids
        }
        if competing_aliases:
            return {
                "source": "gazetteer",
                "matched": True,
                "ambiguous": True,
                "score": round(best_score / 100.0, 4),
                "matched_alias": best_alias,
                "candidate_count": 1 + len(competing_aliases),
            }

        result = self.lookup_street(
            city,
            best_alias,
            house,
            province=province,
        )
        if not result.get("matched"):
            return result
        result["score"] = round(best_score / 100.0, 4)
        result["matched_alias"] = best_alias
        result["match_kind"] = "exact" if best_score == 100.0 else "fuzzy"
        return result

    def _result_for_street_ids(
        self,
        normalized_city: str | None,
        street_ids: tuple[int, ...],
        *,
        house: str | None = None,
        include_source_id: bool = True,
        parent_scoped: bool = False,
    ) -> dict[str, Any]:
        """Build one conservative result from an already scoped candidate set."""

        normalized_cities = {
            self._streets[street_id]["normalized_city"]
            for street_id in street_ids
            if self._streets[street_id]["normalized_city"]
        }
        if (
            (not normalized_city and len(normalized_cities) > 1)
            or (not parent_scoped and normalized_cities & self._ambiguous_city_names)
        ):
            return {
                "source": "gazetteer",
                "matched": True,
                "ambiguous": True,
                "score": 1.0,
                "candidate_count": len(street_ids),
            }

        street_id = street_ids[0]
        street = self._streets[street_id]
        conflicting_metadata = False
        result: dict[str, Any] = {
            "source": "gazetteer",
            "matched": True,
            "ambiguous": False,
            "score": 1.0,
            "street_name": street["name"],
        }
        if len(street_ids) == 1 and include_source_id:
            result["source_id"] = street["source_id"]
        for field in ("street_type", "city", "province", "living_area"):
            values = {
                self._streets[candidate_id][field]
                for candidate_id in street_ids
                if self._streets[candidate_id][field]
            }
            if len(values) == 1:
                result[field] = next(iter(values))
            elif len(values) > 1:
                conflicting_metadata = True

        districts = tuple(sorted({
            district
            for candidate_id in street_ids
            for district in self._street_districts.get(candidate_id, ())
        }))
        if house:
            point_districts = tuple(sorted({
                district
                for candidate_id in street_ids
                for district in self._point_districts.get(
                    (candidate_id, normalize_house(house)), ()
                )
            }))
            if len(point_districts) == 1:
                result["district"] = point_districts[0]
                result["ambiguous"] = conflicting_metadata
                return result
            if len(point_districts) > 1:
                result["ambiguous"] = True
                return result
        if len(districts) == 1:
            result["district"] = districts[0]
        if len(districts) > 1 or conflicting_metadata:
            result["ambiguous"] = True
        return result

    def lookup_area(
        self,
        city: str | None,
        name: str,
        area_type: str | None = None,
    ) -> dict[str, Any]:
        """Resolve only records created by the residential-area enrichment."""

        normalized_name = normalize(name)
        normalized_city = normalize(city) if city else None
        all_ids = (
            self._streets_by_city_alias.get((normalized_city, normalized_name), ())
            if normalized_city
            else self._streets_by_alias.get(normalized_name, ())
        )
        area_ids = tuple(
            street_id
            for street_id in all_ids
            if str(self._streets[street_id]["source_id"]).startswith("osm-area/")
        )
        if not area_ids:
            return {
                "source": "gazetteer",
                "matched": False,
                "ambiguous": False,
                "score": 0.0,
            }

        if area_type:
            normalized_type = canonical_street_type(area_type)
            candidates = tuple(
                street_id
                for street_id in area_ids
                if self._street_types[street_id] in {normalized_type, None}
            )
            if not candidates:
                return {
                    "source": "gazetteer",
                    "matched": True,
                    "ambiguous": True,
                    "type_conflict": True,
                    "score": 1.0,
                    "candidate_count": len(area_ids),
                }
        else:
            known_types = {
                self._street_types[street_id]
                for street_id in area_ids
                if self._street_types[street_id]
            }
            if len(known_types) > 1:
                return {
                    "source": "gazetteer",
                    "matched": True,
                    "ambiguous": True,
                    "score": 1.0,
                    "candidate_count": len(area_ids),
                }
            candidates = area_ids

        return self._result_for_street_ids(normalized_city, candidates)

    @property
    def stats(self) -> dict[str, Any]:
        counts = {}
        for key, table in (
            ("admin_records", "admin_records"),
            ("streets", "streets"),
            ("address_points", "address_points"),
        ):
            counts[key] = self._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        counts["sources"] = [dict(row) for row in self._connection.execute(
            "SELECT * FROM source_snapshots ORDER BY source_name"
        )]
        return counts
