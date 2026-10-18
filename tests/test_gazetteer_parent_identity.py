"""Small real OSM fixtures for administrative identity during the offline build."""

from dataclasses import replace
from xml.etree.ElementTree import Element, SubElement, tostring

import pytest

from tools.build_gazetteer import AdminRecord, extract_osm_records


class OsmFixture:
    def __init__(self):
        self.nodes = []
        self.ways = []

    def node(self, longitude, latitude, **tags):
        node_id = len(self.nodes) + 1
        node = Element("node", id=str(node_id), lon=str(longitude), lat=str(latitude))
        self._tags(node, tags)
        self.nodes.append(node)
        return node_id

    @staticmethod
    def _tags(element, tags):
        for key, value in tags.items():
            SubElement(element, "tag", k=key, v=value)

    def way(self, way_id, coordinates, **tags):
        references = [self.node(*coordinate) for coordinate in coordinates]
        if coordinates[0] == coordinates[-1]:
            references[-1] = references[0]
        way = Element("way", id=str(way_id))
        for node_id in references:
            SubElement(way, "nd", ref=str(node_id))
        self._tags(way, tags)
        self.ways.append(way)

    def area(self, way_id, name, level, left, right):
        self.way(
            way_id,
            [(left, 0), (right, 0), (right, .01), (left, .01), (left, 0)],
            boundary="administrative", admin_level=level, name=name,
        )

    def road(self, way_id, longitude):
        self.way(way_id, [(longitude, .004), (longitude, .006)],
                 highway="residential", name="Main")

    def address(self, longitude, city=None):
        tags = {"addr:street": "Main", "addr:housenumber": "7"}
        if city:
            tags["addr:city"] = city
        self.node(longitude, .005, **tags)

    def extract(self, tmp_path, admins):
        osm = Element("osm", version="0.6", generator="jana-parent-identity-test")
        osm.extend(self.nodes)
        osm.extend(sorted(self.ways, key=lambda way: int(way.attrib["id"])))
        path = tmp_path / "parent-identity.osm"
        path.write_bytes(tostring(osm, encoding="utf-8", xml_declaration=True))
        return extract_osm_records(path, admins)


def twin_admins():
    return [
        AdminRecord("province", "Region A", kato_code="110000000", province="Region A"),
        AdminRecord("province", "Region B", kato_code="220000000", province="Region B"),
        AdminRecord("city", "Twin", aliases=("West Twin",), kato_code="111000001",
                    province="Region A", city="Twin"),
        AdminRecord("city", "Twin", aliases=("East Twin",), kato_code="222000002",
                    province="Region B", city="Twin"),
        AdminRecord("district", "District B", kato_code="222000000",
                    province="Region B", district="District B"),
    ]


def twin_fixture(*, unique_names=True):
    osm = OsmFixture()
    osm.area(1, "Region A", "4", 0, .010)
    osm.area(2, "Region B", "4", .011, .021)
    osm.area(3, "West Twin" if unique_names else "Twin", "8", 0, .010)
    osm.area(4, "East Twin" if unique_names else "Twin", "8", .011, .021)
    osm.area(5, "District B", "6", .011, .021)
    osm.road(101, .009)
    osm.road(202, .020)
    return osm


def test_same_name_shapes_without_unique_identity_stay_unresolved(tmp_path):
    osm = twin_fixture(unique_names=False)
    osm.address(.012, "Twin")

    streets, points, report = osm.extract(tmp_path, twin_admins())

    assert {street.city for street in streets} == {None}
    assert points == []
    assert report["unresolved_address_points"] == 1


@pytest.mark.parametrize("explicit_city", [None, "Twin", "East Twin"])
def test_nearest_road_must_share_the_points_full_locality_identity(tmp_path, explicit_city):
    osm = twin_fixture()
    osm.address(.012, explicit_city)

    streets, points, report = osm.extract(tmp_path, twin_admins())

    assert {street.source_id: street.province for street in streets} == {
        "way/101": "Region A", "way/202": "Region B",
    }
    assert [point.street_source_id for point in points] == ["way/202"]
    assert points[0].district == "District B"
    assert report["unresolved_address_points"] == 0


def test_explicit_city_conflicting_with_spatial_city_stays_unresolved(tmp_path):
    osm = twin_fixture()
    osm.address(.012, "West Twin")

    _, points, report = osm.extract(tmp_path, twin_admins())

    assert points == []
    assert report["unresolved_address_points"] == 1


def test_explicit_city_conflicting_with_spatial_province_stays_unresolved(tmp_path):
    osm = OsmFixture()
    osm.area(1, "Region A", "4", 0, .010)
    osm.area(2, "Region B", "4", .011, .021)
    osm.area(3, "West Twin", "8", 0, .010)
    osm.road(101, .009)
    osm.address(.012, "West Twin")

    _, points, report = osm.extract(tmp_path, twin_admins())

    assert points == []
    assert report["unresolved_address_points"] == 1


def test_same_name_provinces_keep_distinct_identity_during_road_join(tmp_path):
    osm = OsmFixture()
    osm.area(1, "Western Region", "4", 0, .010)
    osm.area(2, "Eastern Region", "4", .011, .021)
    osm.area(3, "Unique Town", "8", 0, .021)
    osm.road(101, .009)
    osm.road(202, .020)
    osm.address(.012, "Unique Town")
    admins = [
        AdminRecord("province", "Region", aliases=("Western Region",), kato_code="110000000"),
        AdminRecord("province", "Region", aliases=("Eastern Region",), kato_code="220000000"),
        AdminRecord("city", "Unique Town", city="Unique Town"),
    ]

    _, points, report = osm.extract(tmp_path, admins)

    assert [point.street_source_id for point in points] == ["way/202"]
    assert report["unresolved_address_points"] == 0


def test_distinct_point_provinces_do_not_become_one_inferred_road_province(tmp_path):
    osm = OsmFixture()
    osm.area(1, "Western Region", "4", 0, .010)
    osm.area(2, "Eastern Region", "4", .011, .021)
    osm.area(3, "Unique Town", "8", 0, .021)
    osm.road(101, .0105)  # No province shape covers this road.
    osm.address(.009, "Unique Town")
    osm.address(.012, "Unique Town")
    admins = [
        AdminRecord("province", "Region", aliases=("Western Region",), kato_code="110000000"),
        AdminRecord("province", "Region", aliases=("Eastern Region",), kato_code="220000000"),
        AdminRecord("city", "Unique Town", city="Unique Town"),
    ]

    streets, points, _ = osm.extract(tmp_path, admins)

    assert len(points) == 2
    assert streets[0].province is None


def test_known_city_parent_survives_finalization_without_province_shape(tmp_path):
    osm = OsmFixture()
    osm.area(1, "East Twin", "8", .011, .021)
    osm.road(202, .020)
    osm.address(.012, "East Twin")

    streets, points, report = osm.extract(tmp_path, twin_admins())

    assert streets[0].province == "Region B"
    assert [point.street_source_id for point in points] == ["way/202"]
    assert report["unresolved_address_points"] == 0


def test_same_name_without_spatial_parent_stays_ambiguous(tmp_path):
    osm = twin_fixture()
    osm.address(.0105, "Twin")  # Gap between both known localities and provinces.

    _, points, report = osm.extract(tmp_path, twin_admins())

    assert points == []
    assert report["unresolved_address_points"] == 1


def test_spatial_hits_for_distinct_kato_codes_are_not_deduplicated(tmp_path):
    osm = OsmFixture()
    osm.area(1, "First Twin", "8", 0, .01)
    osm.area(2, "Second Twin", "8", 0, .01)
    osm.road(101, .009)
    osm.address(.008)
    admins = [
        AdminRecord("city", "Twin", aliases=("First Twin",), kato_code="111000001",
                    province="Region A", city="Twin"),
        AdminRecord("city", "Twin", aliases=("Second Twin",), kato_code="111000002",
                    province="Region A", city="Twin"),
    ]

    streets, points, report = osm.extract(tmp_path, admins)

    assert streets[0].city is None
    assert points == []
    assert report["unresolved_address_points"] == 1


@pytest.mark.parametrize("difference", ["kato_code", "province", "district", "living_area", "field"])
def test_shape_name_does_not_collapse_different_administrative_identities(tmp_path, difference):
    osm = OsmFixture()
    osm.area(1, "Twin", "8", 0, .01)
    osm.road(101, .009)
    original = AdminRecord("city", "Twin", kato_code="111000001", province="Region A", city="Twin")
    value = "living_area" if difference == "field" else "other identity"

    streets, _, report = osm.extract(tmp_path, [original, replace(original, **{difference: value})])

    assert streets[0].city is None
    assert report["admin_shapes"] == 0


def test_duplicate_aliases_and_shapes_of_one_entity_keep_unique_match(tmp_path):
    osm = OsmFixture()
    osm.area(1, "Twin", "8", 0, .01)
    osm.area(2, "TWIN", "8", 0, .01)
    osm.road(101, .009)
    osm.address(.008, "twin")
    original = AdminRecord("city", "Twin", aliases=("TWIN", "twin", "Twin"),
                           kato_code="111000001", province="Region A", city="Twin")

    streets, points, report = osm.extract(tmp_path, [original, replace(original, aliases=("twin",))])

    assert streets[0].city == "Twin"
    assert streets[0].province == "Region A"
    assert [point.street_source_id for point in points] == ["way/101"]
    assert report["unresolved_address_points"] == 0
