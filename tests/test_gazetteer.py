from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from jana.gazetteer import Gazetteer  # noqa: E402
from jana.parser import parse_record  # noqa: E402
from tools.build_gazetteer import (  # noqa: E402
    AddressPointRecord,
    AdminRecord,
    SourceRecord,
    StreetRecord,
    create_database,
    extract_osm_records,
    parse_kato_workbook,
    split_street_surface,
    street_alias_forms,
)
from tools.fetch_sources import record_existing_snapshot, snapshot_file  # noqa: E402


def _fixture_database(path: Path) -> None:
    admins = [
        AdminRecord(
            field="province",
            canonical="Алматинская область",
            aliases=("Алматы облысы", "Алматинская обл."),
            language="ru",
            kato_code="190000000",
            province="Алматинская область",
        ),
        AdminRecord(
            field="city",
            canonical="Алматы",
            aliases=("Алматы қаласы", "г. Алматы"),
            language="ru",
            kato_code="750000000",
            city="Алматы",
        ),
        AdminRecord(
            field="city",
            canonical="Астана",
            aliases=("Астана қаласы", "г. Астана"),
            language="ru",
            kato_code="710000000",
            city="Астана",
        ),
        AdminRecord(
            field="district",
            canonical="Алмалинский район",
            aliases=("Алмалы ауданы", "Алмалинский р-н"),
            language="ru",
            kato_code="751110000",
            city="Алматы",
            district="Алмалинский район",
        ),
        AdminRecord(
            field="living_area",
            canonical="Самал-2",
            aliases=("мкр. Самал-2", "Самал 2 шағынауданы"),
            language="ru",
            city="Алматы",
            living_area="Самал-2",
        ),
    ]
    streets = [
        StreetRecord(
            source_id="way/101",
            name="Абая",
            aliases=("Абай көшесі",),
            street_type="проспект",
            city="Алматы",
            province="Алматинская область",
            districts=("Алмалинский район",),
        ),
        StreetRecord(
            source_id="way/202",
            name="Абая",
            aliases=("Абай көшесі",),
            street_type="проспект",
            city="Астана",
            province="Астана",
            districts=("район Алматы",),
        ),
        StreetRecord(
            source_id="way/303",
            name="Сейфуллина",
            aliases=("Сәкен Сейфуллин көшесі",),
            street_type="улица",
            city="Алматы",
            province="Алматинская область",
            districts=("Алмалинский район", "Жетысуский район"),
        ),
    ]
    address_points = [
        AddressPointRecord(
            source_id="node/3010",
            street_source_id="way/303",
            house="10",
            district="Алмалинский район",
        ),
        AddressPointRecord(
            source_id="node/3099",
            street_source_id="way/303",
            house="99",
            district="Жетысуский район",
        ),
    ]
    sources = [
        SourceRecord(
            source_name="fixture-kato",
            url="https://example.test/kato.xlsx",
            filename="kato.xlsx",
            sha256="0" * 64,
            fetched_at="2026-10-16T00:00:00Z",
            version="2026-09-18",
            license_name="BNS reuse terms",
            license_url="https://stat.gov.kz/ru/description/",
        )
    ]
    create_database(path, admins, streets, address_points, sources)


class GazetteerTests(unittest.TestCase):
    def test_missing_database_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                Gazetteer(Path(tmp) / "missing.sqlite3")

    def test_find_admin_uses_bilingual_aliases_and_unicode_spans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            text = "Алматы қаласы, Алмалы ауданы, мкр. Самал-2"
            result = gazetteer.find_admin(text)

            self.assertEqual(result["city"]["value"], "Алматы")
            self.assertEqual(result["district"]["value"], "Алмалинский район")
            self.assertEqual(result["living_area"]["value"], "Самал-2")
            start, end = result["district"]["span"]
            self.assertEqual(text[start:end], "Алмалы ауданы")
            self.assertEqual(result["district"]["source"], "gazetteer")
            gazetteer.close()

    def test_find_admin_does_not_choose_an_ambiguous_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [
                    AdminRecord(field="district", canonical="Первый район", aliases=("Центральный",)),
                    AdminRecord(field="district", canonical="Второй район", aliases=("Центральный",)),
                ],
                [],
                [],
                [],
            )
            gazetteer = Gazetteer(path)

            result = gazetteer.find_admin("Центральный, улица Абая")

            self.assertNotIn("district", result)
            self.assertIn("district", result["ambiguous_fields"])
            gazetteer.close()

    def test_resolve_district_requires_matching_parent_and_keeps_catalog_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [
                    AdminRecord(
                        field="district",
                        canonical="район Алматы",
                        aliases=("Алматы ауданы", "р-н Алматы"),
                        kato_code="711110000",
                        city="Астана",
                        district="район Алматы",
                    ),
                    AdminRecord(
                        field="district",
                        canonical="район Алматы",
                        aliases=("Алматы ауданы", "р-н Алматы"),
                        kato_code="151013100",
                        city="Актобе",
                        district="район Алматы",
                    ),
                ],
                [],
                [],
                [],
            )
            with Gazetteer(path) as gazetteer:
                astana = gazetteer.resolve_district("Алматы", city="Астана")
                aktobe = gazetteer.resolve_district("Алматы", city="Актобе")
                no_parent = gazetteer.resolve_district("Алматы")
                wrong_parent = gazetteer.resolve_district("Алматы", city="Шымкент")
                unknown = gazetteer.resolve_district("Сарыөзек", city="Астана")

        self.assertEqual(astana["value"], "район Алматы")
        self.assertEqual(astana["kato_code"], "711110000")
        self.assertEqual(aktobe["value"], "район Алматы")
        self.assertEqual(aktobe["kato_code"], "151013100")
        self.assertTrue(no_parent["ambiguous"])
        self.assertTrue(wrong_parent["ambiguous"])
        self.assertTrue(wrong_parent["parent_conflict"])
        self.assertFalse(unknown["matched"])

    def test_resolve_district_requires_all_supplied_parents_to_agree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [
                    AdminRecord(
                        field="district",
                        canonical="Первый район",
                        aliases=("Бірінші ауданы",),
                        kato_code="111111111",
                        province="Северная область",
                        city="Тестоград",
                        district="Первый район",
                    ),
                    AdminRecord(
                        field="district",
                        canonical="Второй район",
                        aliases=("Бірінші ауданы",),
                        kato_code="222222222",
                        province="Южная область",
                        city="Тестоград",
                        district="Второй район",
                    ),
                ],
                [],
                [],
                [],
            )
            with Gazetteer(path) as gazetteer:
                north = gazetteer.resolve_district(
                    "Бірінші", city="Тестоград", province="Северная область"
                )
                city_only = gazetteer.resolve_district("Бірінші", city="Тестоград")
                contradiction = gazetteer.resolve_district(
                    "Бірінші", city="Тестоград", province="Западная область"
                )

        self.assertEqual(north["kato_code"], "111111111")
        self.assertTrue(city_only["ambiguous"])
        self.assertTrue(contradiction["ambiguous"])
        self.assertTrue(contradiction["parent_conflict"])

    def test_runtime_hydrates_missing_district_city_from_kato_hierarchy(self) -> None:
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workbook_path = root / "kato-parent-fixture.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(("te", "ab", "cd", "ef", "hij", "k", "kaz_name", "rus_name", "nn"))
            sheet.append(("150000000", 15, "00", "00", "000", "0", "Ақтөбе облысы", "Актюбинская область", "00"))
            sheet.append(("151000000", 15, "10", "00", "000", "1", "Ақтөбе Қ.Ә.", "Актобе Г.А.", "01"))
            sheet.append(("151010000", 15, "10", "10", "000", "1", "Ақтөбе қ.", "г.Актобе", ""))
            sheet.append(("151013100", 15, "10", "13", "100", "1", "Алматы ауданы", "район Алматы", ""))
            sheet.append(("710000000", 71, "00", "00", "000", "0", "Астана қ.", "г.Астана", "00"))
            sheet.append(("711110000", 71, "11", "10", "000", "1", "Алматы ауданы", "район Алматы", ""))
            workbook.save(workbook_path)

            admins = parse_kato_workbook(workbook_path)
            source_districts = {
                item.kato_code: item
                for item in admins
                if item.kato_code in {"151013100", "711110000"}
            }
            self.assertIsNone(source_districts["151013100"].city)
            self.assertIsNone(source_districts["711110000"].city)

            database_path = root / "gazetteer.sqlite3"
            create_database(database_path, admins, [], [], [])
            with Gazetteer(database_path) as gazetteer:
                aktobe = gazetteer.resolve_district(
                    "Алматы", city="Актобе", province="Актюбинская область"
                )
                astana = gazetteer.resolve_district(
                    "Алматы", city="Астана", province="Астана"
                )

        self.assertFalse(aktobe["ambiguous"])
        self.assertEqual(aktobe["kato_code"], "151013100")
        self.assertFalse(astana["ambiguous"])
        self.assertEqual(astana["kato_code"], "711110000")

    def test_lookup_street_scopes_same_name_by_city(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            result = gazetteer.lookup_street("Алматы", "Абай көшесі")

            self.assertTrue(result["matched"])
            self.assertFalse(result["ambiguous"])
            self.assertEqual(result["city"], "Алматы")
            self.assertEqual(result["street_type"], "проспект")
            self.assertEqual(result["district"], "Алмалинский район")
            gazetteer.close()

    def test_lookup_street_accepts_source_name_without_type_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            result = gazetteer.lookup_street("Алматы", "Абай")

            self.assertTrue(result["matched"])
            self.assertEqual(result["city"], "Алматы")
            gazetteer.close()

    def test_lookup_street_without_city_preserves_same_name_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            result = gazetteer.lookup_street(None, "Абая")

            self.assertTrue(result["matched"])
            self.assertTrue(result["ambiguous"])
            self.assertNotIn("district", result)
            gazetteer.close()

    def test_province_and_exact_house_resolve_same_named_street_without_city(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(
                        source_id="way/west",
                        name="Главная",
                        street_type="улица",
                        city="Первоград",
                        province="Западная область",
                        districts=("Северный район",),
                    ),
                    StreetRecord(
                        source_id="way/east",
                        name="Главная",
                        street_type="улица",
                        city="Второград",
                        province="Восточная область",
                        districts=("Южный район",),
                    ),
                ],
                [
                    AddressPointRecord(
                        source_id="node/west-7",
                        street_source_id="way/west",
                        house="7",
                        district="Северный район",
                    ),
                    AddressPointRecord(
                        source_id="node/east-9",
                        street_source_id="way/east",
                        house="9",
                        district="Южный район",
                    ),
                ],
                [],
            )
            with Gazetteer(path) as gazetteer:
                unscoped = gazetteer.lookup_street(
                    None, "Главная", house="7", street_type="улица"
                )
                resolved = gazetteer.lookup_street(
                    None,
                    "Главная",
                    house="7",
                    street_type="улица",
                    province="Западная область",
                )

        self.assertTrue(unscoped["ambiguous"])
        self.assertFalse(resolved["ambiguous"])
        self.assertEqual(resolved["province"], "Западная область")
        self.assertEqual(resolved["city"], "Первоград")
        self.assertEqual(resolved["district"], "Северный район")

    def test_exact_house_selects_one_same_named_street_inside_confirmed_city(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(
                        source_id="way/north",
                        name="Главная",
                        street_type="улица",
                        city="Тестоград",
                        province="Тестовая область",
                        districts=("Северный район",),
                    ),
                    StreetRecord(
                        source_id="way/south",
                        name="Главная",
                        street_type="улица",
                        city="Тестоград",
                        province="Тестовая область",
                        districts=("Южный район",),
                    ),
                ],
                [AddressPointRecord(
                    source_id="node/south-44",
                    street_source_id="way/south",
                    house="44",
                    district="Южный район",
                )],
                [],
            )
            with Gazetteer(path) as gazetteer:
                resolved = gazetteer.lookup_street(
                    "Тестоград", "Главная", house="44", street_type="улица"
                )
                unknown_house = gazetteer.lookup_street(
                    "Тестоград", "Главная", house="999", street_type="улица"
                )

        self.assertFalse(resolved["ambiguous"])
        self.assertEqual(resolved["district"], "Южный район")
        self.assertTrue(unknown_house["ambiguous"])
        self.assertNotIn("district", unknown_house)

    def test_province_and_exact_house_can_resolve_city_inside_one_province(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(
                        source_id="way/first",
                        name="Центральная",
                        street_type="улица",
                        city="Первоград",
                        province="Общая область",
                        districts=("Первый район",),
                    ),
                    StreetRecord(
                        source_id="way/second",
                        name="Центральная",
                        street_type="улица",
                        city="Второград",
                        province="Общая область",
                        districts=("Второй район",),
                    ),
                ],
                [AddressPointRecord(
                    source_id="node/first-17",
                    street_source_id="way/first",
                    house="17",
                    district="Первый район",
                )],
                [],
            )
            with Gazetteer(path) as gazetteer:
                resolved = gazetteer.lookup_street(
                    None,
                    "Центральная",
                    house="17",
                    street_type="улица",
                    province="Общая область",
                )

        self.assertFalse(resolved["ambiguous"])
        self.assertEqual(resolved["city"], "Первоград")
        self.assertEqual(resolved["province"], "Общая область")
        self.assertEqual(resolved["district"], "Первый район")

    def test_wrong_province_is_a_parent_conflict_not_a_guess(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            with Gazetteer(path) as gazetteer:
                result = gazetteer.lookup_street(
                    None,
                    "Абая",
                    house="44",
                    street_type="проспект",
                    province="Несуществующая область",
                )

        self.assertTrue(result["ambiguous"])
        self.assertTrue(result["parent_conflict"])
        self.assertNotIn("province", result)
        self.assertNotIn("district", result)

    def test_parser_enriches_city_and_district_from_province_street_and_house(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(
                        source_id="way/west",
                        name="Главная",
                        street_type="улица",
                        city="Конаев",
                        province="Алматинская область",
                        districts=("Северный район",),
                    ),
                    StreetRecord(
                        source_id="way/east",
                        name="Главная",
                        street_type="улица",
                        city="Усть-Каменогорск",
                        province="Восточно-Казахстанская область",
                        districts=("Южный район",),
                    ),
                ],
                [AddressPointRecord(
                    source_id="node/west-7",
                    street_source_id="way/west",
                    house="7",
                    district="Северный район",
                )],
                [],
            )
            with Gazetteer(path) as gazetteer:
                explained = parse_record(
                    {
                        "id": "province-house",
                        "raw_address": "Алматинская область, улица Главная, дом 7",
                    },
                    gazetteer=gazetteer,
                    explain=True,
                )

        result = explained["result"]
        self.assertEqual(result["province"], "Алматинская область")
        self.assertEqual(result["city"], "Конаев")
        self.assertEqual(result["district"], "Северный")
        self.assertEqual(explained["fields"]["province"]["source"], "text")
        self.assertEqual(explained["fields"]["city"]["source"], "gazetteer")
        self.assertEqual(explained["fields"]["district"]["source"], "gazetteer")
        self.assertNotIn("gazetteer_street_ambiguous", explained["warnings"])

    def test_parser_fuzzy_matches_unmarked_street_and_enriches_house_district(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [AdminRecord(
                    field="city",
                    canonical="Астана",
                    aliases=("Астана",),
                    kato_code="710000000",
                    province="Астана",
                    city="Астана",
                )],
                [StreetRecord(
                    source_id="way/mangilik",
                    name="Мангилик Ел",
                    aliases=("Мәңгілік Ел",),
                    street_type="проспект",
                    city="Астана",
                    province="Астана",
                    districts=("Есиль район",),
                )],
                [AddressPointRecord(
                    source_id="node/mangilik-47",
                    street_source_id="way/mangilik",
                    house="47",
                    district="Есиль район",
                )],
                [],
            )
            with Gazetteer(path) as gazetteer:
                explained = parse_record(
                    {
                        "id": "unmarked-fuzzy",
                        "raw_address": "Астана манглик ел 47 квартира 57",
                    },
                    gazetteer=gazetteer,
                    explain=True,
                )
                unknown = parse_record(
                    {
                        "id": "unmarked-unknown",
                        "raw_address": "Астана совершенно другая 47 квартира 57",
                    },
                    gazetteer=gazetteer,
                )

        result = explained["result"]
        self.assertEqual(result["city"], "Астана")
        self.assertEqual(result["street_type"], "проспект")
        self.assertEqual(result["street_name"], "Мангилик Ел")
        self.assertEqual(result["house"], "47")
        self.assertEqual(result["apartment"], "57")
        self.assertEqual(result["district"], "Есиль")
        self.assertEqual(explained["fields"]["street_name"]["source"], "gazetteer")
        self.assertEqual(explained["fields"]["street_name"]["match_kind"], "fuzzy")
        self.assertGreaterEqual(explained["fields"]["street_name"]["score"], 0.8)
        self.assertIsNone(unknown["street_name"])
        self.assertIsNone(unknown["house"])
        self.assertIsNone(unknown["district"])

    def test_cross_district_street_needs_house_point_for_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            unresolved = gazetteer.lookup_street("Алматы", "Сейфуллина")
            resolved = gazetteer.lookup_street("Алматы", "Сейфуллина", house="99")

            self.assertTrue(unresolved["ambiguous"])
            self.assertNotIn("district", unresolved)
            self.assertFalse(resolved["ambiguous"])
            self.assertEqual(resolved["district"], "Жетысуский район")
            gazetteer.close()

    def test_unknown_house_does_not_invent_a_district(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            result = gazetteer.lookup_street("Алматы", "Сейфуллина", house="777")

            self.assertTrue(result["ambiguous"])
            self.assertNotIn("district", result)
            gazetteer.close()

    def test_same_street_osm_segments_are_one_logical_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(source_id="way/1", name="Абая", aliases=("Абай көшесі",), street_type="проспект", city="Алматы", districts=("Алмалинский район",)),
                    StreetRecord(source_id="way/2", name="Абая", aliases=("Абай көшесі",), street_type="проспект", city="Алматы", districts=("Алмалинский район",)),
                ],
                [AddressPointRecord(source_id="node/1", street_source_id="way/2", house="44", district="Алмалинский район")],
                [],
            )
            gazetteer = Gazetteer(path)

            result = gazetteer.lookup_street("Алматы", "Абай", house="44")

            self.assertTrue(result["matched"])
            self.assertFalse(result["ambiguous"])
            self.assertEqual(result["district"], "Алмалинский район")
            gazetteer.close()

    def test_explicit_street_type_separates_same_named_road_roles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(
                        source_id="way/street",
                        name="Абая",
                        street_type="улица",
                        city="Алматы",
                        districts=("Район улицы",),
                    ),
                    StreetRecord(
                        source_id="way/avenue",
                        name="Абая",
                        street_type="проспект",
                        city="Алматы",
                        districts=("Район проспекта",),
                    ),
                ],
                [
                    AddressPointRecord(
                        source_id="node/44",
                        street_source_id="way/avenue",
                        house="44",
                        district="Район проспекта",
                    )
                ],
                [],
            )
            gazetteer = Gazetteer(path)

            avenue = gazetteer.lookup_street(
                "Алматы", "Абая", house="44", street_type="проспект"
            )
            street = gazetteer.lookup_street(
                "Алматы", "Абая", house="44", street_type="улица"
            )
            untyped = gazetteer.lookup_street("Алматы", "Абая", house="44")

            self.assertFalse(avenue["ambiguous"])
            self.assertEqual(avenue["district"], "Район проспекта")
            self.assertFalse(street["ambiguous"])
            self.assertEqual(street["district"], "Район улицы")
            self.assertTrue(untyped["ambiguous"])
            self.assertNotIn("district", untyped)
            gazetteer.close()

    def test_explicit_type_keeps_typeless_catalog_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [StreetRecord(
                    source_id="way/unknown-type",
                    name="Главная",
                    city="Тестоград",
                    districts=("Центральный район",),
                )],
                [],
                [],
            )
            with Gazetteer(path) as gazetteer:
                result = gazetteer.lookup_street(
                    "Тестоград", "Главная", street_type="улица"
                )

        self.assertTrue(result["matched"])
        self.assertFalse(result["ambiguous"])
        self.assertEqual(result["district"], "Центральный район")

    def test_typeless_exact_house_point_participates_with_typed_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [
                    StreetRecord(
                        source_id="way/typed",
                        name="Главная",
                        street_type="улица",
                        city="Тестоград",
                        districts=("Северный район",),
                    ),
                    StreetRecord(
                        source_id="way/unknown-type",
                        name="Главная",
                        city="Тестоград",
                    ),
                ],
                [AddressPointRecord(
                    source_id="node/7",
                    street_source_id="way/unknown-type",
                    house="7",
                    district="Южный район",
                )],
                [],
            )
            with Gazetteer(path) as gazetteer:
                result = gazetteer.lookup_street(
                    "Тестоград", "Главная", house="7", street_type="улица"
                )

        self.assertFalse(result["ambiguous"])
        self.assertEqual(result["district"], "Южный район")
        self.assertNotIn("source_id", result)

    def test_incompatible_explicit_type_reports_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [StreetRecord(
                    source_id="way/avenue",
                    name="Главная",
                    street_type="проспект",
                    city="Тестоград",
                    districts=("Центральный район",),
                )],
                [],
                [],
            )
            with Gazetteer(path) as gazetteer:
                result = gazetteer.lookup_street(
                    "Тестоград", "Главная", street_type="улица"
                )

        self.assertTrue(result["ambiguous"])
        self.assertTrue(result["type_conflict"])
        self.assertEqual(result["candidate_count"], 1)
        self.assertNotIn("district", result)

    def test_shared_street_type_vocabulary_covers_kazakh_and_role_audit(self) -> None:
        self.assertEqual(split_street_surface("Главная тұйық көшесі"), ("Главная", "тупик"))
        self.assertEqual(split_street_surface("пл. Республика"), ("Республика", "площадь"))
        self.assertEqual(split_street_surface("проезд Северный"), ("Северный", "проезд"))
        self.assertEqual(split_street_surface("аллея Жастар"), ("Жастар", "аллея"))

    def test_street_alias_forms_prefer_longest_overlapping_marker(self) -> None:
        self.assertEqual(
            street_alias_forms("Главная тұйық көшесі"),
            ("Главная тұйық көшесі", "Главная"),
        )
        self.assertEqual(
            street_alias_forms("тұйық көшесі Главная"),
            ("тұйық көшесі Главная", "Главная"),
        )

    def test_kazakh_dead_end_type_resolves_against_shared_catalog_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [StreetRecord(
                    source_id="way/dead-end",
                    name="Басты",
                    street_type="тупик",
                    city="Алматы",
                    districts=("Тестовый район",),
                )],
                [],
                [],
            )
            with Gazetteer(path) as gazetteer:
                result = parse_record(
                    {"id": "dead-end", "raw_address": "Алматы, Басты тұйығы, 7 үй"},
                    gazetteer=gazetteer,
                    explain=True,
                )

        self.assertEqual(result["result"]["street_type"], "тупик")
        self.assertEqual(result["result"]["district"], "Тестовый")
        self.assertNotIn("gazetteer_street_ambiguous", result["warnings"])

    def test_house_separator_is_not_collapsed_for_district_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            create_database(
                path,
                [],
                [StreetRecord(
                    source_id="way/1",
                    name="Пограничная",
                    city="Алматы",
                    districts=("Первый район", "Второй район"),
                )],
                [
                    AddressPointRecord(source_id="node/1", street_source_id="way/1", house="12/3", district="Первый район"),
                    AddressPointRecord(source_id="node/2", street_source_id="way/1", house="12-3", district="Второй район"),
                ],
                [],
            )
            gazetteer = Gazetteer(path)

            slash = gazetteer.lookup_street("Алматы", "Пограничная", house="12 / 3")
            dash = gazetteer.lookup_street("Алматы", "Пограничная", house="12-3")

            self.assertEqual(slash["district"], "Первый район")
            self.assertEqual(dash["district"], "Второй район")
            gazetteer.close()

    def test_database_is_opened_read_only_and_reports_provenance_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)

            stats = gazetteer.stats

            self.assertEqual(stats["admin_records"], 5)
            self.assertEqual(stats["streets"], 3)
            self.assertEqual(stats["address_points"], 2)
            self.assertEqual(stats["sources"][0]["source_name"], "fixture-kato")
            with self.assertRaises(sqlite3.OperationalError):
                gazetteer._connection.execute("DELETE FROM streets")
            gazetteer.close()

    def test_runtime_lookups_use_memory_indexes_after_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            _fixture_database(path)
            gazetteer = Gazetteer(path)
            statements: list[str] = []
            gazetteer._connection.set_trace_callback(statements.append)

            gazetteer.find_admin("Алматы қаласы, Алмалы ауданы")
            gazetteer.lookup_street("Алматы", "Абая", house="10")

            self.assertEqual(statements, [])
            gazetteer.close()


class SourceSnapshotTests(unittest.TestCase):
    def test_snapshot_file_records_checksum_and_immutable_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.bin"
            source.write_bytes(b"jana-address-fixture")
            manifest = root / "manifest.json"

            entry = snapshot_file(
                source.as_uri(),
                root / "raw" / "snapshot.bin",
                manifest,
                source_name="fixture",
                version="2026-10-16",
                license_name="fixture licence",
                license_url="https://example.test/licence",
            )

            expected = hashlib.sha256(b"jana-address-fixture").hexdigest()
            self.assertEqual(entry["sha256"], expected)
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(saved["sources"][0]["sha256"], expected)
            self.assertEqual(saved["sources"][0]["version"], "2026-10-16")

    def test_existing_resumed_download_can_be_registered_without_refetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "snapshot.bin"
            source.write_bytes(b"resumed-download")
            manifest = root / "manifest.json"

            entry = record_existing_snapshot(
                source,
                manifest,
                source_name="fixture-resumed",
                url="https://example.test/source.bin",
                version="2026-10-16T00:00:00Z",
                license_name="fixture licence",
                license_url="https://example.test/licence",
            )

            self.assertEqual(entry["filename"], "snapshot.bin")
            self.assertEqual(entry["sha256"], hashlib.sha256(b"resumed-download").hexdigest())


class GazetteerBuildTests(unittest.TestCase):
    def test_parse_kato_workbook_builds_bilingual_hierarchy(self) -> None:
        from openpyxl import Workbook

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kato.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(("te", "ab", "cd", "ef", "hij", "k", "kaz_name", "rus_name", "nn"))
            sheet.append(("190000000", 19, "00", "00", "000", "0", "Алматы облысы", "Алматинская область", "00"))
            sheet.append(("191000000", 19, "10", "00", "000", "1", "Қонаев Қ.Ә.", "Конаев Г.А.", "01"))
            sheet.append(("191010000", 19, "10", "10", "000", "1", "Қонаев қ.", "г.Конаев", ""))
            sheet.append(("193600000", 19, "36", "00", "000", "2", "Еңбекшіқазақ ауданы", "Енбекшиказахский район", "02"))
            sheet.append(("193630000", 19, "36", "30", "000", "2", "Есік қ.ә.", "Иссыкская г.а.", ""))
            sheet.append(("193630100", 19, "36", "30", "100", "2", "Есік қ.", "г.Есик", ""))
            workbook.save(path)

            records = parse_kato_workbook(path)

            by_code = {record.kato_code: record for record in records}
            self.assertEqual(by_code["190000000"].field, "province")
            self.assertIn("Алматы облысы", by_code["190000000"].aliases)
            self.assertEqual(by_code["191010000"].canonical, "Конаев")
            self.assertEqual(by_code["191010000"].province, "Алматинская область")
            self.assertEqual(by_code["193600000"].field, "district")
            self.assertEqual(by_code["193630100"].city, "Есик")
            self.assertEqual(by_code["193630100"].district, "Енбекшиказахский район")
            self.assertNotIn("191000000", by_code)  # administrative container, not a locality

    def test_extract_osm_records_preserves_cross_district_ambiguity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            osm_path = Path(tmp) / "fixture.osm"
            osm_path.write_text(
                """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='jana-test'>
  <node id='1' lat='0' lon='0'/><node id='2' lat='0' lon='1'/>
  <node id='3' lat='1' lon='1'/><node id='4' lat='1' lon='0'/>
  <node id='5' lat='0' lon='2'/><node id='6' lat='1' lon='2'/>
  <node id='10' lat='0.5' lon='0.2'/><node id='11' lat='0.5' lon='1.8'/>
  <node id='20' lat='0.49' lon='0.4'>
    <tag k='addr:housenumber' v='10'/><tag k='addr:street' v='Тестовая улица'/>
    <tag k='addr:city' v='Тестоград'/>
  </node>
  <node id='21' lat='0.51' lon='1.6'>
    <tag k='addr:housenumber' v='99'/><tag k='addr:street' v='Тестовая улица'/>
    <tag k='addr:city' v='Тестоград'/>
  </node>
  <node id='22' lat='5' lon='5'>
    <tag k='addr:housenumber' v='500'/><tag k='addr:street' v='Тестовая улица'/>
    <tag k='addr:city' v='Тестоград'/>
  </node>
  <node id='30' lat='0.48' lon='0.2'/><node id='31' lat='0.48' lon='0.3'/>
  <node id='32' lat='0.49' lon='0.3'/><node id='33' lat='0.49' lon='0.2'/>
  <way id='100'><nd ref='1'/><nd ref='2'/><nd ref='3'/><nd ref='4'/><nd ref='1'/>
    <tag k='boundary' v='administrative'/><tag k='admin_level' v='6'/>
    <tag k='name:ru' v='Первый район'/><tag k='name' v='Бірінші аудан'/>
  </way>
  <way id='101'><nd ref='2'/><nd ref='5'/><nd ref='6'/><nd ref='3'/><nd ref='2'/>
    <tag k='boundary' v='administrative'/><tag k='admin_level' v='6'/>
    <tag k='name:ru' v='Второй район'/><tag k='name' v='Екінші аудан'/>
  </way>
  <way id='102'><nd ref='1'/><nd ref='5'/><nd ref='6'/><nd ref='4'/><nd ref='1'/>
    <tag k='boundary' v='administrative'/><tag k='admin_level' v='8'/>
    <tag k='name:ru' v='Тестоград'/>
  </way>
  <way id='200'><nd ref='10'/><nd ref='11'/>
    <tag k='highway' v='residential'/><tag k='name:ru' v='Тестовая улица'/>
    <tag k='name:kk' v='Сынақ көшесі'/>
  </way>
  <way id='300'><nd ref='30'/><nd ref='31'/><nd ref='32'/><nd ref='33'/><nd ref='30'/>
    <tag k='building' v='yes'/><tag k='addr:housenumber' v='12'/>
    <tag k='addr:street' v='Тестовая улица'/><tag k='addr:city' v='Тестоград'/>
  </way>
</osm>
""",
                encoding="utf-8",
            )
            admins = [
                AdminRecord(field="province", canonical="Тестовая область", aliases=("Сынақ облысы",)),
                AdminRecord(field="city", canonical="Тестоград", aliases=("Тест қаласы",), province="Тестовая область", city="Тестоград"),
                AdminRecord(field="district", canonical="Первый район", aliases=("Бірінші аудан",), province="Тестовая область", city="Тестоград", district="Первый район"),
                AdminRecord(field="district", canonical="Второй район", aliases=("Екінші аудан",), province="Тестовая область", city="Тестоград", district="Второй район"),
            ]

            streets, points, report = extract_osm_records(osm_path, admins)

            self.assertEqual(len(streets), 1)
            self.assertEqual(streets[0].name, "Тестовая")
            self.assertEqual(streets[0].street_type, "улица")
            self.assertEqual(streets[0].city, "Тестоград")
            self.assertEqual(set(streets[0].districts), {"Первый район", "Второй район"})
            self.assertIn("Сынақ", streets[0].aliases)
            self.assertEqual(
                {point.house: point.district for point in points},
                {"10": "Первый район", "12": "Первый район", "99": "Второй район"},
            )
            self.assertEqual(report["unresolved_address_points"], 1)

    def test_known_address_city_never_falls_back_to_incompatible_street(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            osm_path = Path(tmp) / "city-mismatch.osm"
            osm_path.write_text(
                """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' generator='jana-test'>
  <node id='1' lat='0' lon='0'/><node id='2' lat='0' lon='1'/>
  <node id='3' lat='1' lon='1'/><node id='4' lat='1' lon='0'/>
  <node id='10' lat='0.5' lon='0.2'/><node id='11' lat='0.5' lon='0.8'/>
  <node id='20' lat='0.49' lon='0.4'>
    <tag k='addr:housenumber' v='7'/><tag k='addr:street' v='Чужая улица'/>
    <tag k='addr:city' v='Тестоград'/>
  </node>
  <way id='100'><nd ref='1'/><nd ref='2'/><nd ref='3'/><nd ref='4'/><nd ref='1'/>
    <tag k='boundary' v='administrative'/><tag k='admin_level' v='8'/>
    <tag k='name:ru' v='Иноград'/>
  </way>
  <way id='200'><nd ref='10'/><nd ref='11'/>
    <tag k='highway' v='residential'/><tag k='name:ru' v='Чужая улица'/>
  </way>
</osm>
""",
                encoding="utf-8",
            )
            admins = [
                AdminRecord(field="city", canonical="Тестоград", city="Тестоград"),
                AdminRecord(field="city", canonical="Иноград", city="Иноград"),
            ]

            streets, points, report = extract_osm_records(osm_path, admins)

            self.assertEqual(streets[0].city, "Иноград")
            self.assertEqual(points, [])
            self.assertEqual(report["unresolved_address_points"], 1)


@unittest.skipUnless(
    (ROOT / "data/public/gazetteer.sqlite").is_file(),
    "accepted gazetteer is not available",
)
class AcceptedGazetteerTypedStreetTests(unittest.TestCase):
    def test_abai_street_and_avenue_do_not_share_house_district(self) -> None:
        with Gazetteer(ROOT / "data/public/gazetteer.sqlite") as gazetteer:
            street = parse_record(
                {
                    "id": "typed-street",
                    "raw_address": "Алматы, улица Абая, дом 44, квартира 7",
                },
                gazetteer=gazetteer,
                explain=True,
            )
            avenue = parse_record(
                {
                    "id": "typed-avenue",
                    "raw_address": "Алматы, проспект Абая, дом 44, квартира 7",
                },
                gazetteer=gazetteer,
                explain=True,
            )

        self.assertEqual(street["result"]["street_type"], "улица")
        self.assertEqual(street["result"]["district"], "Наурызбайский")
        self.assertNotIn("gazetteer_street_ambiguous", street["warnings"])
        self.assertEqual(avenue["result"]["street_type"], "проспект")
        self.assertEqual(avenue["result"]["district"], "Бостандыкский")
        self.assertNotIn("gazetteer_street_ambiguous", avenue["warnings"])


if __name__ == "__main__":
    unittest.main()
