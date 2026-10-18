"""Bounded mixed-script microdistrict marker regressions."""

import pytest

from backend.jana.contracts import PUBLIC_FIELDS
from backend.jana.parser import parse_batch, parse_record


@pytest.mark.parametrize(
    "marker",
    ["мkр.", "mkr.", "mkp.", "МKР.", "MKR-n", "мkр-н", "mkrn", "mkpн"],
)
def test_mixed_microdistrict_markers_preserve_raw_name_house_and_spans(marker):
    raw = f"Алматы, {marker} TeSt-Өрнек, дом 017Б/2"

    explained = parse_record({"id": "mixed-area", "raw_address": raw}, explain=True)
    result = explained["result"]

    assert list(result) == list(PUBLIC_FIELDS)
    assert result["raw_address"] == raw
    assert result["street_type"] == "микрорайон"
    assert result["street_name"] == "TeSt-Өрнек"
    assert result["living_area"] is None
    assert result["house"] == "017Б/2"
    name_start, name_end = explained["fields"]["street_name"]["span"]
    assert raw[name_start:name_end] == "TeSt-Өрнек"
    marker_start, marker_end = explained["fields"]["street_type"]["span"]
    assert raw[marker_start:marker_end] == marker


@pytest.mark.parametrize("marker", ["мkр", "mkr", "mkp"])
@pytest.mark.parametrize("delimiter", [" ", ".", ":", "-"])
def test_mixed_microdistrict_markers_accept_typed_prefix_delimiters(marker, delimiter):
    result = parse_record(
        {"id": "delimiter", "raw_address": f"Алматы, {marker}{delimiter}Тест, дом 1"}
    )

    assert result["street_type"] == "микрорайон"
    assert result["street_name"] == "Тест"
    assert result["house"] == "1"


def test_mixed_microdistrict_can_be_living_area_before_a_separate_street():
    result = parse_record(
        {
            "id": "area-street",
            "raw_address": "Алматы, mkr. TeSt-Өрнек, ул. Сынақ, дом 1",
        }
    )

    assert result["living_area"] == "TeSt-Өрнек"
    assert result["street_type"] == "улица"
    assert result["street_name"] == "Сынақ"


def test_mixed_microdistrict_is_not_misread_as_quartal():
    result = parse_record({"id": "area", "raw_address": "Алматы, мkр.Тест Орамы"})

    assert result["street_type"] == "микрорайон"
    assert result["street_name"] == "Тест Орамы"


@pytest.mark.parametrize("name", ["mkrPoint", "mkpHouse", "мkрТест", "mkrnName", "xmkr"])
def test_mixed_microdistrict_marker_substrings_are_not_components(name):
    result = parse_record({"id": "negative", "raw_address": name})

    assert result["street_type"] is None
    assert result["street_name"] is None
    assert result["living_area"] is None


@pytest.mark.parametrize("marker", ["мкр.", "мкр-н", "мкрн"])
def test_legacy_cyrillic_microdistrict_markers_remain_supported(marker):
    result = parse_record({"id": "legacy", "raw_address": f"Алматы, {marker} Самал"})

    assert result["street_type"] == "микрорайон"
    assert result["street_name"] == "Самал"


def test_duplicate_id_batch_preserves_order_raw_values_and_public_shape():
    records = [
        {"id": "same", "raw_address": "Алматы, mkr. Первый, дом 1"},
        {"id": "same", "raw_address": "Алматы, mkp. Второй, дом 2"},
    ]

    results = parse_batch(records)

    assert len(results) == 2
    assert [item["id"] for item in results] == ["same", "same"]
    assert [item["raw_address"] for item in results] == [r["raw_address"] for r in records]
    assert all(list(item) == list(PUBLIC_FIELDS) for item in results)
    assert [item["street_name"] for item in results] == ["Первый", "Второй"]
