"""A top-level KATO city can supply both roles for one OSM boundary."""

from dataclasses import replace

import pytest

from test_gazetteer_parent_identity import OsmFixture
from tools.build_gazetteer import AdminRecord, parse_kato_workbook


CITY = "Republic City"
SOURCE_ALIAS = "Republic Qalasy"


def republican_admins():
    common = {
        "canonical": CITY,
        "aliases": (f"г.{CITY}", SOURCE_ALIAS),
        "kato_code": "750000000",
        "province": CITY,
    }
    return [AdminRecord("province", **common), AdminRecord("city", city=CITY, **common)]


def republican_fixture(boundary_name=CITY, explicit_city=None, level="4"):
    osm = OsmFixture()
    osm.area(1, boundary_name, level, 0, .01)
    osm.road(101, .009)
    osm.address(.008, explicit_city)
    return osm


@pytest.mark.parametrize("explicit_city", [None, CITY, SOURCE_ALIAS])
@pytest.mark.parametrize("boundary_name", [CITY, SOURCE_ALIAS, f"г.{CITY}"])
def test_one_republican_boundary_retains_both_roles(tmp_path, boundary_name, explicit_city):
    streets, points, report = republican_fixture(boundary_name, explicit_city).extract(
        tmp_path, republican_admins()
    )

    assert report["admin_shapes"] == 2
    assert [(street.city, street.province) for street in streets] == [(CITY, CITY)]
    assert [point.street_source_id for point in points] == ["way/101"]
    assert report["unresolved_address_points"] == 0


@pytest.mark.parametrize("level", ["4", "5"])
def test_dual_roles_are_independent_of_record_order(tmp_path, level):
    streets, points, report = republican_fixture(level=level).extract(
        tmp_path, list(reversed(republican_admins()))
    )

    assert report["admin_shapes"] == 2
    assert streets[0].city == streets[0].province == CITY
    assert len(points) == 1


@pytest.mark.parametrize("role", ["province", "city"])
def test_single_role_boundaries_and_city_place_nodes_keep_existing_behavior(tmp_path, role):
    admins = [record for record in republican_admins() if record.field == role]
    osm = republican_fixture()
    osm.node(.005, .005, place="city", name=SOURCE_ALIAS)

    streets, points, report = osm.extract(tmp_path, admins)

    assert report["admin_shapes"] == 1
    assert streets[0].province == CITY
    assert streets[0].city == (CITY if role == "city" else None)
    assert len(points) == (1 if role == "city" else 0)
    assert report["place_nodes"] == (1 if role == "city" else 0)


def test_republican_city_place_node_retains_only_its_allowed_city_role(tmp_path):
    osm = republican_fixture()
    osm.node(.005, .005, place="city", name=SOURCE_ALIAS)

    _, points, report = osm.extract(tmp_path, republican_admins())

    assert report["admin_shapes"] == 2
    assert report["place_nodes"] == 1
    assert len(points) == 1


@pytest.mark.parametrize(
    "name,code", [("Алматы", "750000000"), ("Астана", "710000000"), ("Шымкент", "790000000")]
)
def test_workbook_republican_city_roles_survive_osm_extraction(tmp_path, name, code):
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(("te", "kaz_name", "rus_name"))
    sheet.append((code, f"{name} қаласы", f"г.{name}"))
    path = tmp_path / "kato.xlsx"
    workbook.save(path)
    workbook.close()
    admins = parse_kato_workbook(path)

    assert {record.field for record in admins} == {"province", "city"}
    streets, points, report = republican_fixture(f"{name} қаласы").extract(tmp_path, admins)

    assert report["admin_shapes"] == 2
    assert streets[0].city == streets[0].province == name
    assert len(points) == 1


@pytest.mark.parametrize(
    "role,changes",
    [
        pytest.param(1, {"kato_code": "710000000"}, id="different-code"),
        pytest.param(1, {"canonical": "Other City"}, id="different-canonical"),
        pytest.param(0, {"province": "Other Region"}, id="different-province-parent"),
        pytest.param(1, {"province": "Other Region"}, id="different-city-parent"),
        pytest.param(0, {"province": None}, id="missing-province-parent"),
        pytest.param(1, {"province": None}, id="missing-city-province-parent"),
        pytest.param(0, {"city": CITY}, id="unexpected-province-city-parent"),
        pytest.param(1, {"city": None}, id="missing-city-self-parent"),
        pytest.param(1, {"city": "Other City"}, id="different-city-self-parent"),
        pytest.param(0, {"district": "District"}, id="province-has-district-parent"),
        pytest.param(1, {"district": "District"}, id="city-has-district-parent"),
        pytest.param(0, {"living_area": "Area"}, id="province-has-living-area-parent"),
        pytest.param(1, {"living_area": "Area"}, id="city-has-living-area-parent"),
        pytest.param(0, {"kato_code": None}, id="missing-province-code"),
        pytest.param(1, {"kato_code": None}, id="missing-city-code"),
    ],
)
def test_inconsistent_dual_roles_remain_unresolved(tmp_path, role, changes):
    admins = republican_admins()
    admins[role] = replace(admins[role], **changes)

    # Both identities still share this source alias even if their canonical names differ.
    streets, points, report = republican_fixture(SOURCE_ALIAS, SOURCE_ALIAS).extract(tmp_path, admins)

    assert report["admin_shapes"] == 0
    assert streets[0].city is None
    assert streets[0].province is None
    assert points == []
    assert report["unresolved_address_points"] == 1


@pytest.mark.parametrize("code", [None, "", "75000000", "751010000", "XX0000000"])
def test_shared_missing_or_non_top_level_code_is_not_a_dual_role_identity(tmp_path, code):
    admins = [replace(record, kato_code=code) for record in republican_admins()]

    streets, points, report = republican_fixture().extract(tmp_path, admins)

    assert report["admin_shapes"] == 0
    assert streets[0].city is None
    assert points == []


@pytest.mark.parametrize("extra_role", ["province", "city"])
def test_an_extra_candidate_cannot_be_discarded_to_form_a_dual_role_pair(tmp_path, extra_role):
    extra = AdminRecord(extra_role, "Other City", aliases=(CITY, SOURCE_ALIAS),
                        kato_code="710000000", province="Other City")
    osm = republican_fixture(explicit_city=CITY)
    osm._tags(osm.ways[0], {"name:ru": SOURCE_ALIAS})

    streets, points, report = osm.extract(tmp_path, [*republican_admins(), extra])

    assert report["admin_shapes"] == 0
    assert streets[0].city is None
    assert points == []


def test_duplicate_aliases_and_shapes_do_not_make_a_dual_role_pair_ambiguous(tmp_path):
    admins = republican_admins()
    admins += [replace(record, aliases=(CITY.upper(), SOURCE_ALIAS)) for record in admins]
    osm = republican_fixture(CITY.upper(), SOURCE_ALIAS)
    osm.area(2, SOURCE_ALIAS, "4", 0, .01)

    streets, points, report = osm.extract(tmp_path, admins)

    assert report["admin_shapes"] == 4
    assert streets[0].city == streets[0].province == CITY
    assert len(points) == 1


def test_dual_role_province_still_conflicts_with_another_overlapping_province(tmp_path):
    admins = [*republican_admins(), AdminRecord(
        "province", "Other Region", kato_code="110000000", province="Other Region"
    )]
    osm = republican_fixture(explicit_city=CITY)
    osm.area(2, "Other Region", "4", 0, .01)

    streets, points, report = osm.extract(tmp_path, admins)

    assert report["admin_shapes"] == 3
    assert streets[0].city is None
    assert points == []
    assert report["unresolved_address_points"] == 1
