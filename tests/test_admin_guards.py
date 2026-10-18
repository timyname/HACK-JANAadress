"""Role and identity guards exercised through tiny real OSM/SQLite fixtures."""

from dataclasses import replace

import pytest

from jana.gazetteer import Gazetteer
from jana.parser import parse_record
from test_gazetteer_parent_identity import OsmFixture
from tools.build_gazetteer import (
    AddressPointRecord,
    AdminRecord,
    StreetRecord,
    create_database,
)


def locality_admin(field="city"):
    return AdminRecord(field, "Twin", kato_code="111000001", province="Region",
                       **{field: "Twin"})


def polygon_fixture(**tags):
    osm = OsmFixture()
    osm.way(1, [(0, 0), (.01, 0), (.01, .01), (0, .01), (0, 0)],
            name="Twin", **tags)
    osm.road(101, .009)
    osm.address(.008)
    return osm


@pytest.mark.parametrize("level", ["1", "2", "3", "02"])
@pytest.mark.parametrize("field", ["city", "district", "living_area"])
def test_top_level_boundary_cannot_supply_a_local_role(tmp_path, level, field):
    osm = polygon_fixture(boundary="administrative", admin_level=level)

    streets, points, report = osm.extract(tmp_path, [locality_admin(field)])

    assert report["admin_shapes"] == 0
    assert streets[0].city is None
    assert streets[0].districts == ()
    assert streets[0].living_area is None
    assert points == []
    assert report["unresolved_address_points"] == 1


@pytest.mark.parametrize("place", ["country", "continent", "state", "province", "region"])
@pytest.mark.parametrize("geometry", ["polygon", "node"])
@pytest.mark.parametrize("field", ["city", "living_area"])
def test_nonlocal_place_cannot_supply_a_locality_role(tmp_path, place, geometry, field):
    if geometry == "polygon":
        osm = polygon_fixture(place=place, boundary="administrative", admin_level="8")
    else:
        osm = OsmFixture()
        osm.node(.005, .005, name="Twin", place=place)
        osm.road(101, .009)
    streets, _, report = osm.extract(tmp_path, [locality_admin(field)])

    assert report["admin_shapes"] == 0
    assert report["place_nodes"] == 0
    assert streets[0].city is None
    assert streets[0].living_area is None


@pytest.mark.parametrize("level", ["", "two", "2;8", "0", "-1"])
def test_explicit_malformed_admin_level_is_not_a_locality(tmp_path, level):
    osm = polygon_fixture(boundary="administrative", admin_level=level, place="city")

    streets, _, report = osm.extract(tmp_path, [locality_admin()])

    assert report["admin_shapes"] == 0
    assert streets[0].city is None


def test_admin_boundary_without_level_or_local_place_stays_unresolved(tmp_path):
    osm = polygon_fixture(boundary="administrative")

    streets, points, report = osm.extract(tmp_path, [locality_admin()])

    assert report["admin_shapes"] == 0
    assert streets[0].city is None
    assert points == []


@pytest.mark.parametrize("place", ["city", "town", "village", "hamlet", "suburb", "neighbourhood"])
def test_recognized_local_place_polygon_without_level_keeps_locality(tmp_path, place):
    osm = polygon_fixture(place=place)

    streets, points, report = osm.extract(tmp_path, [locality_admin()])

    assert report["admin_shapes"] == 1
    assert streets[0].city == "Twin"
    assert len(points) == 1


@pytest.mark.parametrize("level", ["4", "5", "6", "7", "8", "9", "10", "11"])
def test_existing_local_admin_levels_keep_city_role(tmp_path, level):
    osm = polygon_fixture(boundary="administrative", admin_level=level)

    streets, points, report = osm.extract(tmp_path, [locality_admin()])

    assert report["admin_shapes"] == 1
    assert streets[0].city == "Twin"
    assert len(points) == 1


@pytest.mark.parametrize("place", ["state", "province", "region"])
@pytest.mark.parametrize("field,level", [("province", "4"), ("district", "6")])
def test_nonlocal_place_keeps_explicit_compatible_admin_role(tmp_path, place, field, level):
    osm = polygon_fixture(place=place, boundary="administrative", admin_level=level)
    record = AdminRecord(field, "Twin", kato_code="110000000", **{field: "Twin"})

    streets, _, report = osm.extract(tmp_path, [record])

    assert report["admin_shapes"] == 1
    assert streets[0].city is None
    if field == "province":
        assert streets[0].province == "Twin"
    else:
        assert streets[0].districts == ("Twin",)


def runtime_fixture(tmp_path, admins=(), streets=(), points=()):
    path = tmp_path / "admin-guards.sqlite"
    create_database(path, admins, streets, points, [])
    return Gazetteer(path)


@pytest.mark.parametrize("districts", [(), ("District B",), ("District A", "District B")])
@pytest.mark.parametrize("house", [None, "7", "8", "99"])
def test_metadata_conflict_is_ambiguous_for_any_district_count_or_house(tmp_path, districts, house):
    streets = [
        StreetRecord("way/1", "Main", city="Twin", province="Region A"),
        StreetRecord("way/2", "Main", city="Twin", province="Region B", districts=districts),
    ]
    points = [
        AddressPointRecord("node/7", "way/2", "7", district="District B"),
        AddressPointRecord("node/8a", "way/1", "8", district="District A"),
        AddressPointRecord("node/8b", "way/2", "8", district="District B"),
    ]
    with runtime_fixture(tmp_path, streets=streets, points=points) as gazetteer:
        result = gazetteer.lookup_street("Twin", "Main", house)

    assert result["matched"] is True
    assert result["ambiguous"] is True


def test_metadata_conflict_cannot_enrich_parser_from_singleton_district(tmp_path):
    streets = [
        StreetRecord("way/1", "Main", city="Twin", province="Region A"),
        StreetRecord("way/2", "Main", city="Twin", province="Region B", districts=("District B",)),
    ]
    raw = "г. Twin, ул. Main, дом 99"
    with runtime_fixture(tmp_path, streets=streets) as gazetteer:
        result = parse_record({"id": "conflict", "raw_address": raw},
                              gazetteer=gazetteer, explain=True)

    assert result["result"]["city"] == "Twin"
    assert result["result"]["street_name"] == "Main"
    assert result["result"]["district"] is None
    assert result["result"]["province"] is None
    assert "gazetteer_street_ambiguous" in result["warnings"]


@pytest.mark.parametrize("field", ["province", "city", "district", "living_area"])
@pytest.mark.parametrize("difference", ["kato_code", "province", "city", "district", "living_area"])
def test_admin_identity_conflict_is_not_collapsed_by_canonical_name(tmp_path, field, difference):
    original = AdminRecord(field, "Twin", kato_code="111000001", province="Region",
                           city="Twin", district="District", living_area="Area")
    other = replace(original, **{difference: "111000002" if difference == "kato_code" else "Other"})
    with runtime_fixture(tmp_path, [original, other]) as gazetteer:
        result = gazetteer.find_admin("Twin")

    assert field not in result
    assert result["ambiguous_fields"] == [field]


@pytest.mark.parametrize("difference", ["kato_code", "province", "city", "district", "living_area"])
@pytest.mark.parametrize("city", [None, "Twin"])
@pytest.mark.parametrize("house", [None, "7"])
def test_city_identity_conflict_blocks_one_known_street_segment(tmp_path, difference, city, house):
    original = locality_admin()
    other = replace(original, **{difference: "111000002" if difference == "kato_code" else "Other"})
    street = StreetRecord("way/1", "Main", city="Twin", province="Region", districts=("District",))
    point = AddressPointRecord("node/7", "way/1", "7", district="District")
    with runtime_fixture(tmp_path, [original, other], [street], [point]) as gazetteer:
        result = gazetteer.lookup_street(city, "Main", house)

    assert result["matched"] is True
    assert result["ambiguous"] is True
    assert "district" not in result
    assert "province" not in result


@pytest.mark.parametrize("alias", ["Twin", "TWIN", "Егіз", "Egiz"])
def test_duplicate_alias_language_and_spelling_keep_one_admin_identity(tmp_path, alias):
    original = replace(locality_admin(), aliases=("TWIN", "Егіз", "Egiz"))
    duplicate = replace(original, aliases=("twin", "Егіз", "Egiz"), language="kk")
    street = StreetRecord("way/1", "Main", city="Twin", province="Region", districts=("District",))
    with runtime_fixture(tmp_path, [original, duplicate], [street]) as gazetteer:
        result = gazetteer.find_admin(alias)
        lookup = gazetteer.lookup_street("Twin", "Main")

    assert "ambiguous_fields" not in result
    assert result["city"]["value"] == "Twin"
    assert result["city"]["kato_code"] == "111000001"
    assert result["city"]["matched"] == alias
    assert set(result["city"]) == {"value", "source", "span", "matched", "score", "language", "kato_code"}
    assert lookup["ambiguous"] is False
    assert lookup["district"] == "District"


@pytest.mark.parametrize("city", [None, "Twin"])
@pytest.mark.parametrize("with_admin", [False, True])
def test_single_city_or_missing_admins_preserve_street_lookup(tmp_path, city, with_admin):
    admins = [locality_admin()] if with_admin else []
    street = StreetRecord("way/1", "Main", city="Twin", province="Region", districts=("District",))
    with runtime_fixture(tmp_path, admins, [street]) as gazetteer:
        result = gazetteer.lookup_street(city, "Main")

    assert result["ambiguous"] is False
    assert result["city"] == "Twin"
    assert result["province"] == "Region"
    assert result["district"] == "District"


def test_same_entity_coextensive_roles_are_not_ambiguous_admin_matches(tmp_path):
    common = {"kato_code": "750000000", "province": "Twin"}
    admins = [AdminRecord("province", "Twin", **common),
              AdminRecord("city", "Twin", city="Twin", **common)]
    with runtime_fixture(tmp_path, admins) as gazetteer:
        result = gazetteer.find_admin("Twin")

    assert "ambiguous_fields" not in result
    assert result["province"]["kato_code"] == result["city"]["kato_code"] == "750000000"


@pytest.mark.parametrize("field", ["province", "city", "living_area"])
def test_parser_reports_admin_identity_ambiguity(tmp_path, field):
    original = AdminRecord(field, "Twin", kato_code="111000001", **{field: "Twin"})
    with runtime_fixture(tmp_path, [original, replace(original, kato_code="111000002")]) as gazetteer:
        result = parse_record({"id": "ambiguous-admin", "raw_address": "Twin"},
                              gazetteer=gazetteer, explain=True)

    assert f"gazetteer_{field}_ambiguous" in result["warnings"]
    assert result["fields"][field].get("kato_code") is None


def test_parser_keeps_explicit_city_text_when_catalog_identity_is_ambiguous(tmp_path):
    original = locality_admin()
    street = StreetRecord("way/1", "Main", city="Twin", province="Region", districts=("District",))
    point = AddressPointRecord("node/7", "way/1", "7", district="District")
    raw = "г. Twin, ул. Main, дом 7"
    with runtime_fixture(tmp_path, [original, replace(original, kato_code="111000002")],
                         [street], [point]) as gazetteer:
        result = parse_record({"id": "explicit-city", "raw_address": raw},
                              gazetteer=gazetteer, explain=True)

    assert result["result"]["city"] == "Twin"
    assert result["result"]["street_name"] == "Main"
    assert result["result"]["house"] == "7"
    assert result["result"]["province"] is None
    assert result["result"]["district"] is None
    assert result["fields"]["city"]["source"] == "text"
    assert raw[slice(*result["fields"]["city"]["span"])] == "Twin"
    assert result["fields"]["city"].get("kato_code") is None
    assert "gazetteer_city_ambiguous" in result["warnings"]
    assert "gazetteer_street_ambiguous" in result["warnings"]
