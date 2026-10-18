"""Deterministic, parser-independent synthetic corpus generator for Jana Address.

The generator owns labeled components and projects the expected public output from
those labels after corruption.  It deliberately does not import the production
parser or evaluator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PUBLIC_FIELDS = (
    "id",
    "raw_address",
    "province",
    "city",
    "district",
    "living_area",
    "street_type",
    "street_name",
    "house",
    "apartment",
    "entrance",
    "floor",
    "landmark",
    "confidence",
)

PARSED_FIELDS = PUBLIC_FIELDS[2:-1]
GENERATOR_VERSION = "jana-synthetic-corpus/1.2"


@dataclass(frozen=True)
class GeneratedCase:
    input_record: dict[str, Any]
    expected_record: dict[str, Any]
    oracle: dict[str, Any]


def load_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {"schema_version", "city_profiles", "street_names", "microdistricts"}
    missing = sorted(required - config.keys())
    if missing:
        raise ValueError(f"corpus config missing keys: {', '.join(missing)}")
    return config


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _decision(master_seed: str, *parts: Any, modulo: int) -> int:
    if modulo <= 0:
        raise ValueError("modulo must be positive")
    material = "\0".join((master_seed, *(str(part) for part in parts))).encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % modulo


def _family_id(shape: str, city_key: str, street_key: str = "-", microdistrict_key: str = "-") -> str:
    digest = _sha256_bytes(
        f"family-v2\0{shape}\0{city_key}\0{street_key}\0{microdistrict_key}".encode("utf-8")
    )
    return f"family_{digest[:12]}"


def _family_split(family_id: str) -> str:
    bucket = int(_sha256_bytes(f"split-v1\0{family_id}".encode("utf-8"))[:8], 16) % 10
    return "test" if bucket < 2 else "dev"


def _families(config: dict[str, Any], split: str, shape: str) -> list[dict[str, Any]]:
    if split not in {"dev", "test"}:
        raise ValueError("split must be 'dev' or 'test'")
    result = []
    for city in config["city_profiles"]:
        if shape == "street":
            candidates = [
                {"street": street, "microdistrict": None}
                for street in config["street_names"]
            ]
        elif shape == "microdistrict_street":
            candidates = [
                {"street": street, "microdistrict": microdistrict}
                for street in config["street_names"]
                for microdistrict in config["microdistricts"]
            ]
        elif shape == "microdistrict_only":
            candidates = [
                {"street": None, "microdistrict": microdistrict}
                for microdistrict in config["microdistricts"]
            ]
        else:
            raise ValueError(f"unknown address shape: {shape}")
        for candidate in candidates:
            street_key = candidate["street"]["key"] if candidate["street"] else "-"
            microdistrict_key = (
                candidate["microdistrict"]["key"] if candidate["microdistrict"] else "-"
            )
            family_id = _family_id(shape, city["key"], street_key, microdistrict_key)
            if _family_split(family_id) == split:
                semantic_family_key = "|".join(
                    (shape, city["key"], street_key, microdistrict_key)
                )
                result.append(
                    {
                        "family_id": family_id,
                        "semantic_family_key": semantic_family_key,
                        "shape": shape,
                        "city": city,
                        **candidate,
                    }
                )
    if not result:
        raise ValueError(f"config provides no semantic families for split {split!r}")
    return sorted(result, key=lambda item: item["family_id"])


def _tier(index: int) -> str:
    slot = index % 20
    if slot < 2:
        return "clean"
    if slot < 7:
        return "easy"
    if slot < 14:
        return "medium"
    if slot < 19:
        return "hard"
    return "irrecoverable"


def _locale(master_seed: str, split: str, index: int) -> str:
    slot = _decision(master_seed, split, index, "locale", modulo=20)
    if slot < 9:
        return "ru"
    if slot < 16:
        return "kk"
    return "mixed"


def _shape(master_seed: str, split: str, index: int) -> str:
    slot = _decision(master_seed, split, index, "shape", modulo=10)
    if slot < 6:
        return "street"
    if slot < 8:
        return "microdistrict_street"
    return "microdistrict_only"


def _markers(locale: str) -> dict[str, str]:
    if locale == "kk":
        return {
            "province": "обл.",
            "city": "қ.",
            "district": "ауд.",
            "living_area": "ш-а",
            "street": "к-сі",
            "avenue": "д-лы",
            "microdistrict": "ш-а",
            "house": "үй",
            "apartment": "пәт.",
            "entrance": "кіреберіс",
            "floor": "қабат",
            "landmark": "бағдар",
        }
    return {
        "province": "обл.",
        "city": "г.",
        "district": "р-н",
        "living_area": "мкр.",
        "street": "ул.",
        "avenue": "пр-т",
        "microdistrict": "мкр.",
        "house": "д.",
        "apartment": "кв.",
        "entrance": "подъезд",
        "floor": "этаж",
        "landmark": "ориентир",
    }


def _surface(profile: dict[str, Any], locale: str) -> str:
    if locale in {"kk", "mixed"}:
        return profile.get("raw_kk", profile.get("kk", profile["ru"]))
    return profile.get("raw_ru", profile["ru"])


def _canonical_ru(profile: dict[str, Any]) -> str:
    return profile.get("canonical_ru", profile["ru"])


def _component(field: str, marker: str, value: str, canonical: str | None = None) -> dict[str, Any]:
    return {
        "field": field,
        "marker": marker,
        "value": value,
        "canonical": canonical if canonical is not None else value,
        "glue": False,
        "suffix": False,
    }


def _find_component(components: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    return next((component for component in components if component["field"] == field), None)


def _typo(value: str, selector: int) -> str:
    if len(value) < 3:
        return value + "а"
    position = 1 + selector % (len(value) - 2)
    if selector % 2:
        return value[:position] + value[position + 1 :]
    return value[:position] + value[position + 1] + value[position] + value[position + 2 :]


def _kk_letter(value: str) -> str:
    replacements = {
        "Ә": "А", "ә": "а", "Ғ": "Г", "ғ": "г", "Қ": "К", "қ": "к",
        "Ң": "Н", "ң": "н", "Ө": "О", "ө": "о", "Ұ": "У", "ұ": "у",
        "Ү": "У", "ү": "у", "Һ": "Х", "һ": "х", "І": "И", "і": "и",
    }
    for source, target in replacements.items():
        if source in value:
            return value.replace(source, target, 1)
    return _typo(value, 1)


def _operation_plan(tier: str, index: int) -> list[str]:
    surface = [
        "street_typo",
        "spacing",
        "marker_glue",
        "marker_suffix",
        "reorder",
        "mixed_script",
        "kk_letter",
        "punctuation",
        "landmark_noise",
        "number_noise",
        "city_repeat",
    ]
    start = index % len(surface)
    if tier == "clean":
        return []
    count = {"easy": 1, "medium": 3, "hard": 5, "irrecoverable": 3}[tier]
    operations = [surface[(start + offset) % len(surface)] for offset in range(count)]
    if tier == "irrecoverable":
        operations.append("ambiguous_house")
    return operations


def _render_component(component: dict[str, Any]) -> str:
    marker = component["marker"]
    value = component["value"]
    if not marker:
        return value
    if component.get("suffix"):
        return f"{value} {marker}"
    separator = "" if component.get("glue") else " "
    return f"{marker}{separator}{value}"


def _reference_confidence(tier: str, operations: list[str], ambiguous_fields: list[str]) -> float:
    caps = {"clean": 0.99, "easy": 0.95, "medium": 0.85, "hard": 0.70, "irrecoverable": 0.25}
    score = 0.99 - 0.045 * len(operations) - 0.18 * len(ambiguous_fields)
    return round(max(0.02, min(caps[tier], score)), 2)


def _build_case(
    config: dict[str, Any],
    family: dict[str, Any],
    *,
    split: str,
    master_seed: str,
    index: int,
) -> GeneratedCase:
    locale = _locale(master_seed, split, index)
    shape = family["shape"]
    tier = _tier(index)
    markers = _markers(locale)
    city = family["city"]
    street = family["street"]
    microdistrict = family["microdistrict"]
    street_type = "проспект" if _decision(master_seed, split, index, "street-type", modulo=4) == 0 else "улица"
    street_marker = markers["avenue"] if street_type == "проспект" else markers["street"]
    house_values = config["number_values"]["house"]
    apartment_values = config["number_values"]["apartment"]
    entrance_values = config["number_values"]["entrance"]
    floor_values = config["number_values"]["floor"]
    house = house_values[_decision(master_seed, split, index, "house", modulo=len(house_values))]
    apartment = apartment_values[_decision(master_seed, split, index, "apartment", modulo=len(apartment_values))]
    entrance = entrance_values[_decision(master_seed, split, index, "entrance", modulo=len(entrance_values))]
    floor_value = floor_values[_decision(master_seed, split, index, "floor", modulo=len(floor_values))]

    components: list[dict[str, Any]] = [
        _component("province", markers["province"], _surface(city["province"], locale), _canonical_ru(city["province"])),
        _component("city", markers["city"], _surface(city["city"], locale), city["city"]["ru"]),
    ]
    include_district = index % 4 != 0
    district_profile = city["district"]
    district_surface = _surface(district_profile, locale) if include_district else None
    district_source_locale = "ru" if locale == "ru" else "kk"
    district_verified = include_district and district_source_locale in district_profile.get("canonical_locales", [])
    district_canonical = _canonical_ru(district_profile) if district_verified else None
    district_value = district_canonical if district_verified else district_surface
    if include_district:
        components.append(
            _component("district", markers["district"], district_surface, district_canonical)
        )

    if shape == "microdistrict_street":
        components.append(
            _component(
                "living_area",
                markers["living_area"],
                _surface(microdistrict, locale),
                microdistrict["ru"],
            )
        )
    if shape == "microdistrict_only":
        components.append(
            _component(
                "street_name",
                markers["microdistrict"],
                _surface(microdistrict, locale),
                microdistrict["ru"],
            )
        )
        street_type = "микрорайон"
    else:
        components.append(_component("street_name", street_marker, _surface(street, locale), street["ru"]))

    components.append(_component("house", markers["house"], house))
    if index % 3 != 0:
        components.append(_component("apartment", markers["apartment"], apartment))
    if index % 5 in {1, 2}:
        components.append(_component("entrance", markers["entrance"], entrance))
    if index % 5 == 2:
        components.append(_component("floor", markers["floor"], floor_value))
    if index % 6 == 0:
        landmark = config["landmarks"][
            _decision(master_seed, split, index, "landmark", modulo=len(config["landmarks"]))
        ]
        components.append(_component("landmark", markers["landmark"], _surface(landmark, locale)))

    operations = _operation_plan(tier, index)
    separator = ", "
    ambiguous_fields: list[str] = []
    reasons: list[str] = []

    for operation in operations:
        if operation == "street_typo":
            target = _find_component(components, "street_name")
            if target:
                target["value"] = _typo(target["value"], index)
        elif operation == "kk_letter":
            target = _find_component(components, "street_name")
            if target:
                target["value"] = _kk_letter(target["value"])
        elif operation == "spacing":
            separator = "   "
        elif operation == "punctuation":
            separator = "; "
        elif operation == "marker_glue":
            target = _find_component(components, "house") or _find_component(components, "apartment")
            if target:
                target["glue"] = True
        elif operation == "marker_suffix":
            target = _find_component(components, "apartment") or _find_component(components, "house")
            if target:
                target["suffix"] = True
        elif operation == "reorder":
            if len(components) > 4:
                components = components[2:4] + components[:2] + components[4:]
        elif operation == "mixed_script":
            target = _find_component(components, "street_name")
            if target and target["marker"]:
                target["marker"] = target["marker"].replace("у", "y", 1).replace("к", "k", 1)
        elif operation == "landmark_noise" and not _find_component(components, "landmark"):
            landmark = config["landmarks"][index % len(config["landmarks"])]
            components.append(_component("landmark", markers["landmark"], _surface(landmark, locale)))
        elif operation == "number_noise":
            components.append(_component("noise", "участок", str(700 + index % 200)))
        elif operation == "city_repeat":
            city_component = _find_component(components, "city")
            if city_component:
                components.append(dict(city_component))
        elif operation == "ambiguous_house":
            target = _find_component(components, "house")
            if target:
                components.remove(target)
                components.append(_component("noise", "", target["value"]))
                components.append(_component("noise", "", str(40 + index % 50)))
                ambiguous_fields.append("house")
                reasons.append("HOUSE_TWO_EQUAL_UNLABELED_CANDIDATES")

    raw_address = separator.join(_render_component(component) for component in components)
    public_id = f"synthetic-{split}-{index:06d}"
    if index % 25 == 24:
        public_id = f"synthetic-{split}-{index - 1:06d}"

    values: dict[str, Any] = {
        "province": _canonical_ru(city["province"]),
        "city": city["city"]["ru"],
        "district": district_value,
        "living_area": None,
        "street_type": street_type,
        "street_name": None,
        "house": None,
        "apartment": None,
        "entrance": None,
        "floor": None,
        "landmark": None,
    }
    for component in components:
        field = component["field"]
        if field in {"living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark"}:
            values[field] = component["value"]
    for field in ambiguous_fields:
        values[field] = None

    input_record = {"id": public_id, "raw_address": raw_address}
    expected_record = {
        "id": public_id,
        "raw_address": raw_address,
        **values,
        "confidence": _reference_confidence(tier, operations, ambiguous_fields),
    }
    expected_record = {field: expected_record[field] for field in PUBLIC_FIELDS}
    canonical_fields = {
        "province": _canonical_ru(city["province"]),
        "city": city["city"]["ru"],
        "district": district_canonical,
        "living_area": microdistrict["ru"] if shape == "microdistrict_street" else None,
        "street_type": street_type,
        "street_name": microdistrict["ru"] if shape == "microdistrict_only" else street["ru"],
        "house": house,
        "apartment": apartment if index % 3 != 0 else None,
        "entrance": entrance if index % 5 in {1, 2} else None,
        "floor": floor_value if index % 5 == 2 else None,
        "landmark": values["landmark"],
    }
    case_uid = "case_" + _sha256_bytes(
        f"{master_seed}\0{split}\0{index}\0{family['family_id']}".encode("utf-8")
    )[:16]
    oracle = {
        "case_uid": case_uid,
        "input_index": index,
        "family_id": family["family_id"],
        "semantic_family_key": family["semantic_family_key"],
        "synthetic": True,
        "privacy_profile": config.get("privacy_profile", "public_demo_v1"),
        "locale": locale,
        "tier": tier,
        "shape": shape,
        "operations": operations,
        "canonical_fields": canonical_fields,
        "source_surfaces": {
            "district": district_surface,
            **{
                field: values[field]
                for field in ("living_area", "street_name", "house", "apartment", "entrance", "floor", "landmark")
            },
        },
        "district_resolution": {
            "status": "absent" if not include_district else "kato_type_stripped" if district_verified else "unverified_surface",
            "source": None if not include_district else config["administrative_source"]["source_id"] if district_verified else "explicit_synthetic_surface",
            "kato_code": district_profile["kato"]["code"] if district_verified else None,
            "reason": district_profile.get("unverified_reason", "no_audited_mapping") if include_district and not district_verified else None,
        },
        "metric_applicability": {
            "district": include_district,
            "living_area": shape == "microdistrict_street",
        },
        "field_states": {
            field: ("ambiguous" if field in ambiguous_fields else "absent" if values[field] is None else "recoverable")
            for field in PARSED_FIELDS
        },
        "expected_null_fields": ambiguous_fields,
        "irrecoverable_reason_codes": reasons,
    }
    return GeneratedCase(input_record=input_record, expected_record=expected_record, oracle=oracle)


def generate_cases(
    config: dict[str, Any],
    *,
    count: int,
    split: str,
    master_seed: str,
) -> list[GeneratedCase]:
    if count < 0:
        raise ValueError("count must be non-negative")
    family_pools = {
        shape: _families(config, split, shape)
        for shape in ("street", "microdistrict_street", "microdistrict_only")
    }
    cases = []
    for index in range(count):
        shape = _shape(master_seed, split, index)
        families = family_pools[shape]
        family = families[_decision(master_seed, split, index, "family", modulo=len(families))]
        cases.append(
            _build_case(
                config,
                family,
                split=split,
                master_seed=master_seed,
                index=index,
            )
        )
    return cases


def _write_json(path: Path, value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _coverage(cases: list[GeneratedCase]) -> dict[str, Any]:
    return {
        "record_count": len(cases),
        "tiers": dict(sorted(Counter(case.oracle["tier"] for case in cases).items())),
        "locales": dict(sorted(Counter(case.oracle["locale"] for case in cases).items())),
        "shapes": dict(sorted(Counter(case.oracle["shape"] for case in cases).items())),
        "operations": dict(
            sorted(Counter(op for case in cases for op in case.oracle["operations"]).items())
        ),
        "families": len({case.oracle["family_id"] for case in cases}),
        "duplicate_id_records": len(cases)
        - len({case.input_record["id"] for case in cases}),
        "expected_nulls": {
            field: sum(case.expected_record[field] is None for case in cases)
            for field in PARSED_FIELDS
        },
    }


def _manual_review(cases: list[GeneratedCase], master_seed: str) -> list[dict[str, Any]]:
    if not cases:
        return []
    sample_size = min(len(cases), max(20, min(200, math.ceil(len(cases) * 0.10))))
    ranked = sorted(
        cases,
        key=lambda case: _sha256_bytes(
            f"manual-review-v1\0{master_seed}\0{case.oracle['case_uid']}".encode("utf-8")
        ),
    )
    selected = ranked[:sample_size]
    return [
        {
            "case_uid": case.oracle["case_uid"],
            "input": case.input_record,
            "expected": case.expected_record,
            "check": {
                "tier": case.oracle["tier"],
                "locale": case.oracle["locale"],
                "operations": case.oracle["operations"],
                "status": "UNREVIEWED",
                "notes": "",
            },
        }
        for case in selected
    ]


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_bundle(
    target: str | Path,
    cases: list[GeneratedCase],
    *,
    config: dict[str, Any],
    master_seed: str,
    split: str,
    allow_overwrite: bool = False,
) -> dict[str, Any]:
    target = Path(target)
    if target.exists():
        if not allow_overwrite:
            raise FileExistsError(f"bundle already exists: {target}")
        shutil.rmtree(target)
    target.mkdir(parents=True)

    _write_json(target / "input.json", [case.input_record for case in cases])
    _write_json(target / "expected.json", [case.expected_record for case in cases])
    oracle_text = "".join(
        json.dumps(case.oracle, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
        for case in cases
    )
    (target / "oracle.jsonl").write_text(oracle_text, encoding="utf-8", newline="\n")
    _write_json(target / "coverage.json", _coverage(cases))
    _write_json(target / "manual-review.json", _manual_review(cases, master_seed))

    artifact_names = ["input.json", "expected.json", "oracle.jsonl", "coverage.json", "manual-review.json"]
    artifacts = {
        name: {"sha256": _file_sha256(target / name), "bytes": (target / name).stat().st_size}
        for name in artifact_names
    }
    generator_source_sha256 = _file_sha256(Path(__file__))
    fingerprint_material = {
        "generator_version": GENERATOR_VERSION,
        "generator_source_sha256": generator_source_sha256,
        "schema_version": config["schema_version"],
        "oracle_policy_version": config.get("oracle_policy_version", "jana-oracle/v1"),
        "master_seed": master_seed,
        "split": split,
        "record_count": len(cases),
        "config_sha256": _sha256_bytes(_canonical_bytes(config)),
        "artifacts": {name: value["sha256"] for name, value in artifacts.items()},
    }
    manifest = {
        "manifest_version": "jana-corpus-manifest/v1",
        **fingerprint_material,
        "decision_algorithm": "sha256(seed + NUL-separated decision coordinates), first 64 bits modulo N",
        "split_algorithm": "rendered lower-entity family v2, then sha256('split-v1' + NUL + family_id) modulo 10; buckets 0-1=test",
        "privacy": {
            "profile": config.get("privacy_profile", "public_demo_v1"),
            "organizer_rows_used": False,
            "public_administrative_source": config.get("administrative_source", {}).get("source_id"),
            "synthetic_combinations": True,
        },
        "artifacts": artifacts,
        "corpus_fingerprint": _sha256_bytes(_canonical_bytes(fingerprint_material)),
    }
    _write_json(target / "manifest.json", manifest)
    return manifest


def verify_bundle(target: str | Path) -> dict[str, Any]:
    target = Path(target)
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    errors: list[str] = []
    for name, metadata in manifest["artifacts"].items():
        path = target / name
        if not path.exists():
            errors.append(f"missing artifact: {name}")
            continue
        if _file_sha256(path) != metadata["sha256"]:
            errors.append(f"sha256 mismatch: {name}")
    inputs = json.loads((target / "input.json").read_text(encoding="utf-8"))
    expected = json.loads((target / "expected.json").read_text(encoding="utf-8"))
    if len(inputs) != manifest["record_count"] or len(expected) != len(inputs):
        errors.append("record count mismatch")
    for index, (input_record, expected_record) in enumerate(zip(inputs, expected)):
        if list(expected_record) != list(PUBLIC_FIELDS):
            errors.append(f"public field order mismatch at position {index}")
            break
        if input_record.get("id") != expected_record.get("id") or input_record.get("raw_address") != expected_record.get("raw_address"):
            errors.append(f"id/raw preservation mismatch at position {index}")
            break
        confidence = expected_record.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            errors.append(f"confidence out of range at position {index}")
            break
    fingerprint_material = {
        key: manifest[key]
        for key in (
            "generator_version",
            "generator_source_sha256",
            "schema_version",
            "oracle_policy_version",
            "master_seed",
            "split",
            "record_count",
            "config_sha256",
        )
    }
    fingerprint_material["artifacts"] = {
        name: metadata["sha256"] for name, metadata in manifest["artifacts"].items()
    }
    if _sha256_bytes(_canonical_bytes(fingerprint_material)) != manifest["corpus_fingerprint"]:
        errors.append("corpus fingerprint mismatch")
    return {"valid": not errors, "errors": errors, "record_count": len(inputs)}


def _load_profile(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _command_generate(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    profile = _load_profile(args.profile) if args.profile else {}
    count = args.count if args.count is not None else profile.get("count")
    split = args.split or profile.get("split")
    seed = args.seed or profile.get("master_seed")
    if count is None or split is None or seed is None:
        raise ValueError("count, split and seed are required directly or through --profile")
    cases = generate_cases(config, count=int(count), split=split, master_seed=seed)
    manifest = write_bundle(
        args.out,
        cases,
        config=config,
        master_seed=seed,
        split=split,
        allow_overwrite=args.allow_overwrite,
    )
    print(json.dumps({"records": count, "fingerprint": manifest["corpus_fingerprint"]}))
    return 0


def _command_validate(args: argparse.Namespace) -> int:
    report = verify_bundle(args.bundle)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["valid"] else 3


def _command_replay(args: argparse.Namespace) -> int:
    source = Path(args.manifest)
    manifest = json.loads(source.read_text(encoding="utf-8"))
    config = load_config(args.config)
    if _sha256_bytes(_canonical_bytes(config)) != manifest["config_sha256"]:
        raise ValueError("config hash does not match manifest")
    cases = generate_cases(
        config,
        count=manifest["record_count"],
        split=manifest["split"],
        master_seed=manifest["master_seed"],
    )
    replay = write_bundle(
        args.out,
        cases,
        config=config,
        master_seed=manifest["master_seed"],
        split=manifest["split"],
        allow_overwrite=args.allow_overwrite,
    )
    matches = replay["corpus_fingerprint"] == manifest["corpus_fingerprint"]
    print(json.dumps({"matches": matches, "fingerprint": replay["corpus_fingerprint"]}))
    return 0 if matches else 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="generate a deterministic corpus bundle")
    generate.add_argument("--config", required=True)
    generate.add_argument("--profile")
    generate.add_argument("--count", type=int)
    generate.add_argument("--split", choices=("dev", "test"))
    generate.add_argument("--seed")
    generate.add_argument("--out", required=True)
    generate.add_argument("--allow-overwrite", action="store_true")
    generate.set_defaults(handler=_command_generate)
    validate = subparsers.add_parser("validate", help="verify artifact hashes and public invariants")
    validate.add_argument("--bundle", required=True)
    validate.set_defaults(handler=_command_validate)
    replay = subparsers.add_parser("replay", help="regenerate a bundle from its manifest")
    replay.add_argument("--manifest", required=True)
    replay.add_argument("--config", required=True)
    replay.add_argument("--out", required=True)
    replay.add_argument("--allow-overwrite", action="store_true")
    replay.set_defaults(handler=_command_replay)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ValueError, FileExistsError, json.JSONDecodeError) as error:
        parser.exit(5, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
