"""Evaluate Jana Address predictions against an exact synthetic field oracle."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


FIELDS = (
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
)
PUBLIC_FIELDS = ("id", "raw_address", *FIELDS, "confidence")
GROUPS = {
    "primary": (0.60, ("province", "city", "street_name", "house")),
    "secondary": (0.25, ("apartment", "entrance", "floor")),
    "context": (0.15, ("district", "living_area")),
}
NUMERIC_STRING_FIELDS = {"house", "apartment", "entrance", "floor"}


def _normalized(value: Any, field: str) -> Any:
    if value is None:
        return None
    text = str(value).casefold()
    if field not in NUMERIC_STRING_FIELDS:
        text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _field_metrics(expected: list[dict[str, Any]], predicted: list[dict[str, Any]], field: str) -> dict[str, Any]:
    exact = sum(left.get(field) == right.get(field) for left, right in zip(expected, predicted))
    normalized = sum(
        _normalized(left.get(field), field) == _normalized(right.get(field), field)
        for left, right in zip(expected, predicted)
    )
    expected_null = sum(record.get(field) is None for record in expected)
    predicted_null = sum(record.get(field) is None for record in predicted)
    correct_null = sum(
        left.get(field) is None and right.get(field) is None
        for left, right in zip(expected, predicted)
    )
    return {
        "count": len(expected),
        "exact_accuracy": _ratio(exact, len(expected)),
        "normalized_accuracy": _ratio(normalized, len(expected)),
        "expected_null": expected_null,
        "predicted_null": predicted_null,
        "null_precision": _ratio(correct_null, predicted_null),
        "null_recall": _ratio(correct_null, expected_null),
    }


def _record_exact(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return all(left.get(field) == right.get(field) for field in FIELDS)


def _validate_public_record(record: dict[str, Any], index: int, role: str) -> None:
    if not isinstance(record, dict):
        raise ValueError(f"{role} at position {index} must be an object")
    missing = [field for field in PUBLIC_FIELDS if field not in record]
    unexpected = sorted(set(record) - set(PUBLIC_FIELDS))
    if missing:
        raise ValueError(f"{role} at position {index} missing fields: {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"{role} at position {index} unexpected fields: {', '.join(unexpected)}")
    for field in ("id", "raw_address"):
        if type(record[field]) is not str:
            raise ValueError(f"{role} at position {index} {field} must be a string")
    for field in FIELDS:
        if record[field] is not None and type(record[field]) is not str:
            raise ValueError(f"{role} at position {index} {field} must be string or null")
    confidence = record["confidence"]
    if type(confidence) not in (int, float):
        raise ValueError(f"{role} at position {index} confidence must be an int or float")
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError(f"{role} at position {index} confidence must be finite and in [0,1]")


def _summary_for_indices(
    expected: list[dict[str, Any]], predicted: list[dict[str, Any]], indices: list[int]
) -> dict[str, Any]:
    if not indices:
        return {"count": 0, "record_exact_accuracy": 1.0, "field_exact_accuracy": 1.0}
    record_exact = sum(_record_exact(expected[index], predicted[index]) for index in indices)
    field_exact = sum(
        expected[index].get(field) == predicted[index].get(field)
        for index in indices
        for field in FIELDS
    )
    return {
        "count": len(indices),
        "record_exact_accuracy": record_exact / len(indices),
        "field_exact_accuracy": field_exact / (len(indices) * len(FIELDS)),
    }


def _calibration(expected: list[dict[str, Any]], predicted: list[dict[str, Any]]) -> dict[str, Any]:
    if not expected:
        return {"count": 0, "brier": 0.0, "ece_10": 0.0, "mean_confidence": 0.0}
    points = []
    for left, right in zip(expected, predicted):
        confidence = right.get("confidence")
        points.append((float(confidence), 1.0 if _record_exact(left, right) else 0.0))
    brier = sum((confidence - truth) ** 2 for confidence, truth in points) / len(points)
    ece = 0.0
    for bucket in range(10):
        lower = bucket / 10
        upper = (bucket + 1) / 10
        members = [point for point in points if lower <= point[0] < upper or bucket == 9 and point[0] == 1]
        if members:
            mean_confidence = sum(point[0] for point in members) / len(members)
            mean_accuracy = sum(point[1] for point in members) / len(members)
            ece += len(members) / len(points) * abs(mean_confidence - mean_accuracy)
    return {
        "count": len(points),
        "brier": brier,
        "ece_10": ece,
        "mean_confidence": sum(point[0] for point in points) / len(points),
    }


def evaluate_records(
    expected: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
    *,
    oracle: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if len(expected) != len(predicted):
        raise ValueError(f"record count mismatch: expected {len(expected)}, got {len(predicted)}")
    if oracle is not None and len(oracle) != len(expected):
        raise ValueError("oracle metadata count does not match expected records")
    for index, (left, right) in enumerate(zip(expected, predicted)):
        _validate_public_record(left, index, "expected record")
        _validate_public_record(right, index, "prediction")
        if left.get("id") != right.get("id") or left.get("raw_address") != right.get("raw_address"):
            raise ValueError(f"id/raw mismatch at position {index}")

    per_field = {field: _field_metrics(expected, predicted, field) for field in FIELDS}
    all_exact = sum(
        left.get(field) == right.get(field)
        for left, right in zip(expected, predicted)
        for field in FIELDS
    )
    all_normalized = sum(
        _normalized(left.get(field), field) == _normalized(right.get(field), field)
        for left, right in zip(expected, predicted)
        for field in FIELDS
    )
    expected_null = sum(
        left.get(field) is None for left in expected for field in FIELDS
    )
    predicted_null = sum(
        right.get(field) is None for right in predicted for field in FIELDS
    )
    correct_null = sum(
        left.get(field) is None and right.get(field) is None
        for left, right in zip(expected, predicted)
        for field in FIELDS
    )

    group_scores: dict[str, Any] = {}
    weighted = 0.0
    for name in ("primary", "secondary"):
        weight, fields = GROUPS[name]
        eligible_count = len(expected) * len(fields)
        correct = sum(
            left.get(field) == right.get(field)
            for left, right in zip(expected, predicted)
            for field in fields
        )
        field_details = {
            field: {
                "status": "AVAILABLE",
                "eligible_count": len(expected),
                "exact_accuracy": per_field[field]["exact_accuracy"],
            }
            for field in fields
        }
        score = _ratio(correct, eligible_count)
        group_scores[name] = {
            "weight": weight,
            "status": "AVAILABLE",
            "eligible_count": eligible_count,
            "exact_accuracy": score,
            "fields": field_details,
        }
        weighted += weight * score

    context_weight, context_fields = GROUPS["context"]
    applicability_available = oracle is not None and all(
        isinstance(metadata.get("metric_applicability"), dict) for metadata in oracle
    )
    if not applicability_available:
        context = {
            "weight": context_weight,
            "status": "UNAVAILABLE_APPLICABILITY_REQUIRED",
            "eligible_count": 0,
            "exact_accuracy": None,
            "fields": {
                field: {
                    "status": "UNAVAILABLE_APPLICABILITY_REQUIRED",
                    "eligible_count": 0,
                    "exact_accuracy": None,
                }
                for field in context_fields
            },
        }
        approximate_score = None
        proxy_status = "UNAVAILABLE_CONTEXT_APPLICABILITY_REQUIRED"
    else:
        context_correct = 0
        context_eligible = 0
        context_field_details = {}
        for field in context_fields:
            indices = [
                index
                for index, metadata in enumerate(oracle or [])
                if metadata["metric_applicability"].get(field) is True
            ]
            correct = sum(expected[index][field] == predicted[index][field] for index in indices)
            context_correct += correct
            context_eligible += len(indices)
            context_field_details[field] = {
                "status": "AVAILABLE" if indices else "UNAVAILABLE_NO_ELIGIBLE_LABELS",
                "eligible_count": len(indices),
                "exact_accuracy": _ratio(correct, len(indices)) if indices else None,
            }
        context_score = _ratio(context_correct, context_eligible) if context_eligible else None
        context = {
            "weight": context_weight,
            "status": "AVAILABLE" if context_eligible else "UNAVAILABLE_NO_ELIGIBLE_LABELS",
            "eligible_count": context_eligible,
            "exact_accuracy": context_score,
            "fields": context_field_details,
        }
        if context_score is None:
            approximate_score = None
            proxy_status = "UNAVAILABLE_NO_CONTEXT_LABELS"
        else:
            approximate_score = weighted + context_weight * context_score
            proxy_status = "APPROXIMATE_NOT_ORGANIZER_EVALUATOR"
    group_scores["context"] = context

    all_instances = len(expected) * len(FIELDS)
    report: dict[str, Any] = {
        "record_count": len(expected),
        "record_exact_accuracy": _ratio(
            sum(_record_exact(left, right) for left, right in zip(expected, predicted)),
            len(expected),
        ),
        "per_field": per_field,
        "all_fields": {
            "eligible_count": all_instances,
            "exact_accuracy": _ratio(all_exact, all_instances),
            "normalized_accuracy": _ratio(all_normalized, all_instances),
        },
        "null_metrics": {
            "expected_null": expected_null,
            "predicted_null": predicted_null,
            "correct_null": correct_null,
            "null_precision": _ratio(correct_null, predicted_null),
            "null_recall": _ratio(correct_null, expected_null),
        },
        "group_proxy": {
            "status": proxy_status,
            "weights": {name: weight for name, (weight, _) in GROUPS.items()},
            "groups": group_scores,
            "approximate_score": approximate_score,
        },
        "calibration": _calibration(expected, predicted),
    }

    if oracle is not None:
        slice_indices: dict[str, dict[str, list[int]]] = {
            "tier": defaultdict(list),
            "locale": defaultdict(list),
            "operation": defaultdict(list),
        }
        for index, metadata in enumerate(oracle):
            slice_indices["tier"][metadata.get("tier", "unknown")].append(index)
            slice_indices["locale"][metadata.get("locale", "unknown")].append(index)
            for operation in metadata.get("operations", []):
                slice_indices["operation"][operation].append(index)
        report["slices"] = {
            dimension: {
                key: _summary_for_indices(expected, predicted, indices)
                for key, indices in sorted(values.items())
            }
            for dimension, values in slice_indices.items()
        }
    return report


def _read_oracle(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--predicted", required=True)
    parser.add_argument("--oracle")
    parser.add_argument("--out")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = json.loads(Path(args.expected).read_text(encoding="utf-8"))
    predicted = json.loads(Path(args.predicted).read_text(encoding="utf-8"))
    oracle = _read_oracle(args.oracle) if args.oracle else None
    report = evaluate_records(expected, predicted, oracle=oracle)
    text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8", newline="\n")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
