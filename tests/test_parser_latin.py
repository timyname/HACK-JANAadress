"""Bounded Latin RU/KZ marker support and source-preservation regressions."""

import re

import pytest

from backend.jana.contracts import PUBLIC_FIELDS
from backend.jana.parser import _ADMIN_MARKERS, parse_batch, parse_record


@pytest.mark.parametrize(
    ("raw", "name"),
    [
        ("Astana, ulitsa Testovaya, dom 17, kv 8", "Testovaya"),
        ("Astana qalasy, Test koshesi, 17 ui, 8 pater", "Test"),
        ("Astana qalasy, Test köşesi, 17 üy, 8 päter", "Test"),
    ],
)
def test_latin_marker_addresses_preserve_public_contract_and_source(raw, name):
    explained = parse_record({"id": "latin", "raw_address": raw}, explain=True)
    result = explained["result"]

    assert list(result) == list(PUBLIC_FIELDS)
    assert result["raw_address"] == raw
    assert result["city"] == "Астана"
    assert result["street_type"] == "улица"
    assert result["street_name"] == name
    assert result["house"] == "17"
    assert result["apartment"] == "8"
    start, end = explained["fields"]["street_name"]["span"]
    assert raw[start:end] == name


@pytest.mark.parametrize("marker", ["ul.", "ulitsa", "koshe", "köşe", "kóshe"])
def test_latin_street_prefix_preserves_accented_name_and_numeric_source(marker):
    raw = f"Astana, {marker} TeSt-Örnek, dom 017B/2"
    explained = parse_record({"id": "latin", "raw_address": raw}, explain=True)
    result = explained["result"]
    assert result["street_type"] == "улица"
    assert result["street_name"] == "TeSt-Örnek"
    assert result["house"] == "017B/2"
    assert result["raw_address"] == raw
    start, end = explained["fields"]["street_name"]["span"]
    assert raw[start:end] == "TeSt-Örnek"


@pytest.mark.parametrize("name", ["Ulpan", "koshesim", "ulitsaev"])
def test_latin_street_marker_substrings_are_not_types(name):
    assert parse_record({"id": "negative", "raw_address": f"{name} 17"})["street_type"] is None


@pytest.mark.parametrize(
    ("field", "marker"),
    [("house", marker) for marker in ["dom", "ui", "uy", "üi", "üy"]]
    + [("apartment", marker) for marker in ["kvartira", "kv", "kv.", "pater", "päter", "páter", "flat"]]
    + [("entrance", marker) for marker in ["podyezd", "pod'yezd", "pod’yezd", "podezd", "kireberis"]]
    + [("floor", marker) for marker in ["etazh", "qabat", "kabat"]],
)
@pytest.mark.parametrize("suffix", [False, True])
def test_numeric_latin_roles_support_prefix_and_suffix(field, marker, suffix):
    component = f"08 {marker}" if suffix else f"{marker} 08"
    result = parse_record({"id": "roles", "raw_address": component})
    assert result[field] == "08"


def test_glued_latin_roles_keep_numbers_separate():
    result = parse_record({"id": "glued", "raw_address": "Astana, ul.Test, dom17B/2, 8kv, 2podezd, 5etazh"})
    assert result["street_name"] == "Test"
    assert {field: result[field] for field in ["house", "apartment", "entrance", "floor"]} == {
        "house": "17B/2", "apartment": "8", "entrance": "2", "floor": "5",
    }


@pytest.mark.parametrize("suffix", [False, True])
@pytest.mark.parametrize(
    ("field", "marker", "name"),
    [("province", marker, "Örnek") for marker in ["oblast", "oblysy"]]
    + [("city", marker, "Ülgi") for marker in ["gorod", "qalasy", "qala"]]
    + [("district", marker, "Synaq") for marker in ["rayon", "raion", "audany", "audan"]],
)
def test_explicit_unknown_latin_admin_names_remain_source(field, marker, name, suffix):
    component = f"{name} {marker}" if suffix else f"{marker} {name}"
    result = parse_record({"id": "admin", "raw_address": component})
    assert result[field] == name


def test_multiple_explicit_latin_admin_components():
    raw = "Örnek oblysy, Ülgi qalasy, Synaq audany, Test koshesi, 17 ui"
    result = parse_record({"id": "admin", "raw_address": raw})
    assert result["province"] == "Örnek"
    assert result["city"] == "Ülgi"
    assert result["district"] == "Synaq"
    assert result["street_name"] == "Test"
    assert result["house"] == "17"


@pytest.mark.parametrize("raw", ["Qalam", "Gorodok", "Oblastina", "Audandy", "Rayonova"])
def test_latin_admin_prefix_substrings_are_not_explicit_components(raw):
    result = parse_record({"id": "negative-admin", "raw_address": raw})
    assert all(result[field] is None for field in ["province", "city", "district"])


@pytest.mark.parametrize("separator", [" ", ".", ":", "-"])
@pytest.mark.parametrize(
    ("marker", "field", "name"),
    [("gorod", "city", "Ülgi"), ("oblast", "province", "Örnek"), ("audan", "district", "Synaq")],
)
def test_latin_admin_prefix_requires_and_accepts_explicit_separator(marker, field, name, separator):
    result = parse_record({"id": "admin-separator", "raw_address": f"{marker}{separator}{name}"})
    assert result[field] == name


@pytest.mark.parametrize("separator", [" ", ".", ":", "-"])
@pytest.mark.parametrize("kind, marker", [("city", "gorod"), ("province", "oblast"), ("district", "audan")])
def test_latin_admin_prefix_leaves_whitespace_to_shared_template(kind, marker, separator):
    prefix = re.compile(_ADMIN_MARKERS[kind][0], re.IGNORECASE)
    match = prefix.match(marker + separator + " " * 128 + "?")
    assert match is not None
    assert match.end() == len(marker) + (separator != " ")


@pytest.mark.parametrize("number", ["017B/2", "017Б/2", "017/02", "017"])
def test_mixed_scripts_case_and_punctuation_preserve_numeric_literals(number):
    raw = f"Astana, ul. Test, DoM: {number}, ПӘТЕР №08, 2 KiReBeRiS, ЭТАЖ: 05"
    result = parse_record({"id": "mixed", "raw_address": raw})
    assert result["house"] == number
    assert result["apartment"] == "08"
    assert result["entrance"] == "2"
    assert result["floor"] == "05"
    assert result["raw_address"] == raw


@pytest.mark.parametrize("marker", ["domovoy", "kvartirant", "paternal", "podezdik", "etazhny", "qabatty"])
def test_numeric_marker_substrings_do_not_create_roles(marker):
    result = parse_record({"id": "negative", "raw_address": f"8{marker}"})
    assert all(result[field] is None for field in ["house", "apartment", "entrance", "floor"])


def test_conflicting_latin_numeric_roles_use_existing_first_explicit_policy():
    result = parse_record({"id": "conflict", "raw_address": "dom 17, dom 19, kv 8, kv 9, 2podezd, 3podezd, 5etazh, 6etazh"})
    assert {field: result[field] for field in ["house", "apartment", "entrance", "floor"]} == {
        "house": "17", "apartment": "8", "entrance": "2", "floor": "5",
    }


def test_latin_batch_preserves_duplicate_ids_order_count_and_raw_text():
    records = [
        {"id": "same", "raw_address": "Astana, ulitsa TeSt, dom 017B/2"},
        {"id": "same", "raw_address": "Astana qalasy, Örnek köşesi, 18 üy"},
    ]
    results = parse_batch(records)
    assert len(results) == 2
    assert [result["id"] for result in results] == ["same", "same"]
    assert [result["raw_address"] for result in results] == [record["raw_address"] for record in records]
    assert [result["street_name"] for result in results] == ["TeSt", "Örnek"]
    assert all(list(result) == list(PUBLIC_FIELDS) for result in results)


@pytest.mark.parametrize("raw", ["г. Астана, ул. Тестовая, дом 17, кв. 8", "Астана қаласы, Тест көшесі, 17 үй, 8 пәтер"])
def test_existing_cyrillic_marker_examples(raw):
    result = parse_record({"id": "cyrillic", "raw_address": raw})
    assert result["city"] == "Астана"
    assert result["street_type"] == "улица"
    assert result["house"] == "17"
    assert result["apartment"] == "8"
