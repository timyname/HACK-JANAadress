"""Context-aware OSM/KATO joins for city, house, and district enrichment."""

import json

from test_gazetteer_parent_identity import OsmFixture
from jana.gazetteer import Gazetteer
from tools.build_gazetteer import AdminRecord, create_database, load_verified_contexts


def test_parent_region_disambiguates_same_named_city_boundary(tmp_path):
    osm = OsmFixture()
    osm.area(1, "Region A", "4", 0, .010)
    osm.area(2, "Region B", "4", .011, .021)
    osm.area(3, "Twin", "8", .011, .021)
    osm._tags(osm.ways[-1], {"addr:region": "Region B"})
    osm.road(101, .020)
    osm.address(.019, "Twin")
    admins = [
        AdminRecord("province", "Region A", kato_code="110000000", province="Region A"),
        AdminRecord("province", "Region B", kato_code="220000000", province="Region B"),
        AdminRecord("city", "Twin", kato_code="111000001", province="Region A", city="Twin"),
        AdminRecord("city", "Twin", kato_code="222000002", province="Region B", city="Twin"),
    ]

    streets, points, report = osm.extract(tmp_path, admins)

    assert [(street.city, street.province) for street in streets] == [("Twin", "Region B")]
    assert len(points) == 1
    assert report["unresolved_address_points"] == 0


def test_address_point_creates_street_and_keeps_unique_district_without_highway(tmp_path):
    osm = OsmFixture()
    osm.area(1, "Region", "4", 0, .02)
    osm.area(2, "Test City", "8", 0, .02)
    osm.area(3, "Қазыбек би", "10", 0, .02)
    osm.address(.01, "Test City")
    osm.nodes[-1].find("tag[@k='addr:street']").set("v", "улица Саттара Ерубаева")
    admins = [
        AdminRecord("province", "Region", kato_code="350000000", province="Region"),
        AdminRecord("city", "Test City", kato_code="351010000", province="Region", city="Test City"),
        AdminRecord(
            "district",
            "район им.Казыбек би",
            aliases=("Қазыбек би атын.ауданы",),
            kato_code="351013100",
            province="Region",
            district="район им.Казыбек би",
        ),
    ]

    streets, points, report = osm.extract(tmp_path, admins)

    derived = [street for street in streets if street.source_id.startswith("derived/addr-street/")]
    assert len(derived) == 1
    assert derived[0].city == "Test City"
    assert derived[0].street_type == "улица"
    assert derived[0].name == "Саттара Ерубаева"
    assert derived[0].districts == ("район им.Казыбек би",)
    assert len(points) == 1
    assert points[0].house == "7"
    assert points[0].district == "район им.Казыбек би"
    assert report["derived_address_streets"] == 1
    assert report["unresolved_address_points"] == 0


def test_verified_context_assigns_district_only_to_exact_house(tmp_path):
    source = tmp_path / "verified.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "contexts": [{
            "source_id": "official/street/1",
            "province": "Region",
            "city": "Test City",
            "district": "Central District",
            "street_type": "улица",
            "street_name": "Main",
            "aliases": ["Main Street"],
            "houses": [{"source_id": "official/house/50", "house": "50"}],
        }],
    }), encoding="utf-8")
    streets, points = load_verified_contexts(source)
    database = tmp_path / "gazetteer.sqlite"
    create_database(database, [], streets, points, [])

    with Gazetteer(database) as gazetteer:
        exact = gazetteer.lookup_street("Test City", "Main Street", "50")
        other = gazetteer.lookup_street("Test City", "Main Street", "52")

    assert exact["district"] == "Central District"
    assert "district" not in other


def test_province_disambiguates_same_named_city_for_street_lookup(tmp_path):
    database = tmp_path / "gazetteer.sqlite"
    admins = [
        AdminRecord("city", "Twin", kato_code="111000001", province="Region A", city="Twin"),
        AdminRecord("city", "Twin", kato_code="222000002", province="Region B", city="Twin"),
    ]
    source = tmp_path / "verified.json"
    source.write_text(json.dumps({
        "schema_version": 1,
        "contexts": [{
            "source_id": "official/street/1",
            "province": "Region B",
            "city": "Twin",
            "district": "Central District",
            "street_type": "улица",
            "street_name": "Main",
            "houses": [{"source_id": "official/house/50", "house": "50"}],
        }],
    }), encoding="utf-8")
    streets, points = load_verified_contexts(source)
    create_database(database, admins, streets, points, [])

    with Gazetteer(database) as gazetteer:
        without_province = gazetteer.lookup_street("Twin", "Main", "50")
        scoped = gazetteer.lookup_street(
            "Twin", "Main", "50", province="Region B"
        )

    assert without_province["ambiguous"] is True
    assert scoped["district"] == "Central District"
