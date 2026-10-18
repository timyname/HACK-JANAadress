"""Strict transport contracts, separate from address recognition."""

import math

PUBLIC_FIELDS = (
    "id", "raw_address", "province", "city", "district", "living_area",
    "street_type", "street_name", "house", "apartment", "entrance", "floor",
    "landmark", "confidence",
)
MAX_RECORDS = 10_000
MAX_ADDRESS_CHARS = 8192
MAX_BODY_BYTES = 10 * 1024 * 1024


class InputError(ValueError):
    def __init__(self, code, index=None):
        self.code, self.index = code, index
        super().__init__(code)

    def detail(self):
        return {"code": self.code, **({"index": self.index} if self.index is not None else {})}


def validate_batch(value):
    if not isinstance(value, list):
        raise InputError("EXPECTED_ARRAY")
    if len(value) > MAX_RECORDS:
        raise InputError("TOO_MANY_RECORDS")
    records = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not isinstance(item.get("raw_address"), str):
            raise InputError("INVALID_RECORD", index)
        if len(item["raw_address"]) > MAX_ADDRESS_CHARS or len(item["id"]) > 1024:
            raise InputError("RECORD_TOO_LONG", index)
        try:
            item["id"].encode("utf-8")
            item["raw_address"].encode("utf-8")
        except UnicodeEncodeError:
            raise InputError("INVALID_UNICODE", index) from None
        records.append({"id": item["id"], "raw_address": item["raw_address"]})
    return records


def validate_output(inputs, outputs):
    if not isinstance(outputs, list) or len(inputs) != len(outputs):
        raise ValueError("OUTPUT_COUNT")
    canonical = []
    for original, result in zip(inputs, outputs):
        if not isinstance(result, dict) or set(result) != set(PUBLIC_FIELDS):
            raise ValueError("OUTPUT_FIELDS")
        if any(result[key] != original[key] for key in ("id", "raw_address")):
            raise ValueError("OUTPUT_IDENTITY")
        if any(result[key] is not None and not isinstance(result[key], str) for key in PUBLIC_FIELDS[2:-1]):
            raise ValueError("OUTPUT_TYPE")
        for key in PUBLIC_FIELDS[:-1]:
            if isinstance(result[key], str):
                result[key].encode("utf-8")
        score = result["confidence"]
        if type(score) not in (float, int) or not math.isfinite(score) or not 0 <= score <= 1:
            raise ValueError("OUTPUT_CONFIDENCE")
        canonical.append({key: result[key] for key in PUBLIC_FIELDS})
    return canonical
