"""Residential complexes are protected text, and living areas only with a street."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.jana.contracts import PUBLIC_FIELDS
from backend.jana.gazetteer import Gazetteer
from backend.jana.parser import parse_batch, parse_record
from tools.build_gazetteer import AdminRecord, StreetRecord, create_database


class ResidentialTextTests(unittest.TestCase):
    def assert_complex(self, raw: str, name: str, street: str = "Сынақ") -> None:
        explained = parse_record({"id": "same", "raw_address": raw}, explain=True)
        result = explained["result"]
        self.assertEqual(result["living_area"], name)
        self.assertEqual(result["street_name"], street)
        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["house"], "17")
        self.assertEqual(result["raw_address"], raw)
        self.assertEqual(list(result), list(PUBLIC_FIELDS))
        meta = explained["fields"]["living_area"]
        self.assertEqual(meta["source"], "text")
        self.assertEqual(raw[slice(*meta["span"])], name)
        self.assertEqual(explained["warnings"], [])

    def test_normative_examples_preserve_name_and_source_span(self) -> None:
        for raw, name, street in (
            ("Астана, ул. Сынақ, ЖК Альфа, дом 17", "Альфа", "Сынақ"),
            ("Астана, ул. Сынақ ЖК Альфа, дом 17", "Альфа", "Сынақ"),
            ("Астана, ЖК «Альфа 2», ул. Сынақ, дом 17", "Альфа 2", "Сынақ"),
            ("Astana, ulitsa Test, ZHK Jana Park, dom 17", "Jana Park", "Test"),
            ("Астана, тұрғын үй кешені «Сынақ», Тест көшесі, үй 17", "Сынақ", "Тест"),
        ):
            with self.subTest(raw=raw):
                self.assert_complex(raw, name, street)

    def test_marker_forms_and_source_spellings(self) -> None:
        for marker in ("жК", "ЖИЛОЙ комплекс", "тұрғын үй кешені", "zhk", "ZhK"):
            for name in ("Альфа", "Jana Park", "Жаңа Park", "O'Neil-Парк 2"):
                with self.subTest(marker=marker, name=name):
                    self.assert_complex(f"Астана, {marker} {name}, ул. Сынақ, дом 17", name)

    def test_before_and_after_street_and_component_separators(self) -> None:
        for separator in (", ", "; ", "\n", " "):
            for components in (("ЖК Престиж", "ул.Сынақ"), ("ул.Сынақ", "ЖК Престиж")):
                raw = "Астана" + separator + separator.join((*components, "дом17"))
                with self.subTest(raw=raw):
                    self.assert_complex(raw, "Престиж")

    def test_quotes_and_optional_marker_punctuation(self) -> None:
        for left, right in (("«", "»"), ('"', '"'), ("“", "”")):
            with self.subTest(left=left):
                self.assert_complex(f"Астана, ЖК.: {left}  Альфа 2  {right}, ул. Сынақ, дом 17", "Альфа 2")

    def test_glued_dotted_admin_marker_terminates_complex_name(self) -> None:
        for admin, field, expected in (
            ("г.Алматы", "city", "Алматы"),
            ("қ.Алматы", "city", "Алматы"),
            ("обл.Акмолинская область", "province", "Акмолинская область"),
        ):
            raw = f"ЖК Альфа {admin}, ул. Сынақ, дом 17"
            with self.subTest(raw=raw):
                self.assert_complex(raw, "Альфа")
                result = parse_record({"id": "admin-boundary", "raw_address": raw})
                self.assertEqual(result[field], expected)

    def test_quoted_role_names_do_not_leak_into_components_or_warnings(self) -> None:
        for name in ("Дом 2", "Квартира 4", "Подъезд 3", "Этаж 9", "город Алматы", "Школа 8"):
            with self.subTest(name=name):
                raw = f"Астана, ЖК «{name}», ул. Сынақ, дом 17"
                self.assert_complex(raw, name)
                result = parse_record({"id": "role", "raw_address": raw})
                self.assertEqual(result["city"], "Астана")
                for field in ("apartment", "entrance", "floor", "landmark"):
                    self.assertIsNone(result[field])

    def test_without_street_never_becomes_street_or_house(self) -> None:
        for suffix, house in (("", None), (", дом 17", "17")):
            with self.subTest(suffix=suffix):
                explained = parse_record({"id": "bare", "raw_address": "ЖК Альфа 2" + suffix}, explain=True)
                for field in ("living_area", "street_name", "street_type"):
                    self.assertIsNone(explained["result"][field])
                self.assertEqual(explained["result"]["house"], house)
                self.assertEqual(explained["warnings"], [])

    def test_landmark_context_remains_landmark_only(self) -> None:
        for marker in ("ориентир", "рядом с", "напротив", "около", "возле"):
            with self.subTest(marker=marker):
                raw = f"Астана, ул. Сынақ, дом 17, {marker} ЖК Альфа"
                result = parse_record({"id": "landmark", "raw_address": raw})
                self.assertEqual(result["landmark"], "ЖК Альфа")
                self.assertIsNone(result["living_area"])
                self.assertEqual(result["street_name"], "Сынақ")

    def test_landmark_start_delimits_preceding_unquoted_complex(self) -> None:
        for marker in ("ориентир", "бағдар", "бағдары", "рядом с", "напротив", "около", "возле"):
            raw = f"Астана, ЖК Альфа {marker} ЖК Бета, ул.Сынақ, дом17"
            with self.subTest(marker=marker):
                self.assert_complex(raw, "Альфа")
                explained = parse_record({"id": "complex-and-landmark", "raw_address": raw}, explain=True)
                self.assertEqual(explained["result"]["landmark"], "ЖК Бета")
                landmark_span = explained["fields"]["landmark"]["span"]
                self.assertEqual(raw[slice(*landmark_span)], "ЖК Бета")

    def test_landmark_probe_inside_quoted_complex_does_not_hide_second_complex(self) -> None:
        names = (
            "Дом 2", "Квартира 4", "Подъезд 3", "Этаж 9", "город Алматы", "Школа 8",
            "ориентир Бета", "бағдар Бета", "бағдары Бета", "рядом с Бета",
            "напротив Бета", "около Бета", "возле Бета",
        )
        for left, right in (("«", "»"), ('"', '"'), ("“", "”")):
            for name in names:
                raw = f"Астана, ЖК {left}{name}{right} ЖК Альфа, ул.Сынақ, дом17"
                with self.subTest(raw=raw):
                    explained = parse_record({"id": "quoted-role-repeat", "raw_address": raw}, explain=True)
                    result = explained["result"]
                    self.assertIsNone(result["living_area"])
                    self.assertEqual(explained["warnings"], ["ambiguous_residential_complex"])
                    self.assertEqual(result["city"], "Астана")
                    self.assertEqual(result["street_name"], "Сынақ")
                    self.assertEqual(result["house"], "17")
                    for field in ("landmark", "apartment", "entrance", "floor"):
                        self.assertIsNone(result[field])

    def test_quoted_landmark_like_name_does_not_hide_later_genuine_landmark(self) -> None:
        for name in ("ориентир Бета", "рядом с Бета"):
            for marker in ("ориентир", "бағдар", "бағдары", "рядом с", "напротив", "около", "возле"):
                for left, right in (("«", "»"), ('"', '"'), ("“", "”")):
                    raw = f"Астана, ЖК {left}{name}{right}, ул.Сынақ, {marker} ЖК Альфа, дом17"
                    with self.subTest(raw=raw):
                        self.assert_complex(raw, name)
                        explained = parse_record({"id": "quoted-role-and-landmark", "raw_address": raw}, explain=True)
                        self.assertEqual(explained["result"]["landmark"], "ЖК Альфа")
                        landmark_span = explained["fields"]["landmark"]["span"]
                        self.assertEqual(raw[slice(*landmark_span)], "ЖК Альфа")

    def test_all_explicit_landmark_contexts_protect_complex_markers(self) -> None:
        for marker in ("ориентир", "бағдар", "бағдары", "рядом с", "напротив", "около", "возле"):
            for left, right in (("", ""), ("«", "»"), ('"', '"'), ("“", "”")):
                first_name, second_name = f"{left}Альфа{right}", f"{left}Бета{right}"
                cases = (
                    (f"ориентир парк, {marker} ЖК {first_name}", None, "парк"),
                    (f"возле ЖК {first_name}, {marker} ЖК {second_name}", None, f"ЖК {first_name}"),
                    (f"ЖК {first_name}, около магазина, {marker} ЖК {second_name}", "Альфа", "магазина"),
                )
                for middle, expected_complex, expected_landmark in cases:
                    raw = f"Астана, ул. Сынақ, {middle}, дом 17"
                    with self.subTest(raw=raw):
                        explained = parse_record({"id": "multiple-landmarks", "raw_address": raw}, explain=True)
                        result = explained["result"]
                        self.assertEqual(result["living_area"], expected_complex)
                        self.assertEqual(result["landmark"], expected_landmark)
                        self.assertEqual(result["street_name"], "Сынақ")
                        self.assertEqual(result["house"], "17")
                        self.assertEqual(explained["warnings"], [])

    def test_landmark_mask_does_not_restore_complex_role_numbers(self) -> None:
        self.assert_complex("Астана, ЖК «Дом 2», ул. Сынақ, дом 17, ориентир парк", "Дом 2")

    def test_marker_inside_quoted_street_name_is_not_complex(self) -> None:
        for left, right in (("«", "»"), ('"', '"'), ("“", "”")):
            raw = f"Астана, ул. {left}ЖК Альфа{right}, дом 17"
            with self.subTest(raw=raw):
                result = parse_record({"id": "quoted-street", "raw_address": raw})
                self.assertIsNone(result["living_area"])
                self.assertEqual(result["street_name"], f"{left}ЖК Альфа{right}")

    def test_markers_are_bounded_words(self) -> None:
        for false_marker in ("ЖКХ", "zhkland", "xzhk", "предЖК"):
            raw = f"Астана, ул. {false_marker} Альфа, дом 17"
            with self.subTest(raw=raw):
                result = parse_record({"id": "bounded", "raw_address": raw})
                self.assertIsNone(result["living_area"])
                self.assertEqual(result["street_name"], f"{false_marker} Альфа")

    def test_invalid_names_do_not_swallow_following_street(self) -> None:
        for invalid in ("ЖК", "ЖК :", "ЖК «", "ЖК !!!", "ЖК «!!!»"):
            raw = f"Астана, {invalid}, ул. Сынақ, дом 17"
            with self.subTest(raw=raw):
                result = parse_record({"id": "invalid", "raw_address": raw})
                self.assertIsNone(result["living_area"])
                self.assertEqual(result["street_name"], "Сынақ")
                self.assertEqual(result["house"], "17")

    def test_empty_complex_immediately_before_street_preserves_street(self) -> None:
        raw = "Астана, ЖК ул.Сынақ, дом17"
        result = parse_record({"id": "empty-before-street", "raw_address": raw})
        self.assertIsNone(result["living_area"])
        self.assertEqual(result["street_name"], "Сынақ")
        self.assertEqual(result["house"], "17")

    def test_unmatched_quote_protects_embedded_marker_from_extraction(self) -> None:
        result = parse_record({"id": "unmatched", "raw_address": "Астана, ул. «Тест ЖК Альфа, дом 17"})
        self.assertIsNone(result["living_area"])

    def test_different_complexes_are_explicitly_ambiguous(self) -> None:
        for raw in ("Астана, ЖК Альфа, ЖК Бета, ул. Сынақ, дом 17", "ЖК Альфа; ЖК Бета"):
            with self.subTest(raw=raw):
                explained = parse_record({"id": "ambiguous", "raw_address": raw}, explain=True)
                self.assertIsNone(explained["result"]["living_area"])
                self.assertIn("ambiguous_residential_complex", explained["warnings"])

    def test_equivalent_repeats_keep_first_spelling_and_span(self) -> None:
        raw = "Астана, ЖК «ALPHA-Park», ЖК alpha–park, ул. Сынақ, дом 17"
        self.assert_complex(raw, "ALPHA-Park")
        explained = parse_record({"id": "repeat", "raw_address": raw}, explain=True)
        self.assertEqual(explained["fields"]["living_area"]["span"][0], raw.index("ALPHA-Park"))

    def test_explicit_microdistrict_keeps_priority_without_false_conflict(self) -> None:
        raw = "Астана, мкр. Самал, ЖК Альфа, ул. Сынақ, дом 17"
        explained = parse_record({"id": "microdistrict", "raw_address": raw}, explain=True)
        self.assertEqual(explained["result"]["living_area"], "Самал")
        self.assertEqual(explained["result"]["street_name"], "Сынақ")
        self.assertEqual(explained["warnings"], [])

    def test_microdistrict_and_complex_without_separate_street_are_not_ambiguous(self) -> None:
        raw = "Астана, мкр. Самал, ЖК Альфа, дом17"
        explained = parse_record({"id": "microdistrict-only", "raw_address": raw}, explain=True)
        self.assertEqual(explained["result"]["street_name"], "Самал")
        self.assertEqual(explained["result"]["street_type"], "микрорайон")
        self.assertIsNone(explained["result"]["living_area"])
        self.assertEqual(explained["warnings"], [])

    def test_batch_order_duplicate_ids_and_exact_public_shape(self) -> None:
        records = [{"id": "same", "raw_address": raw} for raw in (
            "Астана, ул. Сынақ, ЖК Альфа, дом 17", "Астана, ул. Тест, ЖК Бета, дом 17",
        )]
        results = parse_batch(records)
        self.assertEqual([result["id"] for result in results], ["same", "same"])
        self.assertEqual([result["raw_address"] for result in results], [record["raw_address"] for record in records])
        self.assertEqual([result["living_area"] for result in results], ["Альфа", "Бета"])
        for result in results:
            self.assertEqual(list(result), list(PUBLIC_FIELDS))

    def test_long_whitespace_and_many_markers_are_deterministic(self) -> None:
        raw = "Астана, " + ("ЖК Альфа; " * 24) + "ул. Сынақ" + (" " * 80) + ", дом17"
        first = parse_record({"id": "long", "raw_address": raw}, explain=True)
        self.assertEqual(first, parse_record({"id": "long", "raw_address": raw}, explain=True))
        self.assertEqual(first["result"]["living_area"], "Альфа")
        self.assertEqual(first["result"]["house"], "17")
        self.assertEqual(first["warnings"], [])

    def test_extractor_handles_many_markers_and_whitespace_without_suffix_rescans(self) -> None:
        from backend.jana.parser import _extract_residential_complexes

        raw = ("ЖК Альфа; " * 240) + (" " * 4000) + "ЖК Бета, ул. Сынақ, дом17"
        first = _extract_residential_complexes(raw)
        self.assertEqual(first, _extract_residential_complexes(raw))
        self.assertEqual([candidate.value for candidate in first], ["Альфа"] * 240 + ["Бета"])
        for candidate in first:
            self.assertEqual(raw[slice(*candidate.span)], candidate.value)


class ResidentialGazetteerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "tiny.sqlite"
        create_database(path, [
            AdminRecord(field="city", canonical="Секретоград", aliases=("ТайныйГород",), city="Секретоград"),
            AdminRecord(field="living_area", canonical="Каталог", aliases=("Каталог",), living_area="Каталог"),
        ], [
            StreetRecord(source_id="way/1", name="Сынақ", street_type="улица", city="Астана", living_area="Каталог"),
        ], [], [])
        self.gazetteer = Gazetteer(path)
        self.addCleanup(self.gazetteer.close)

    def test_city_alias_inside_complex_cannot_supply_city(self) -> None:
        for raw in ("ЖК ТайныйГород", "ЖК «ТайныйГород», ул. Незнакомая, дом 17"):
            with self.subTest(raw=raw):
                result = parse_record({"id": "protected", "raw_address": raw}, gazetteer=self.gazetteer)
                self.assertIsNone(result["city"])

    def test_catalog_does_not_replace_explicit_complex(self) -> None:
        raw = "Астана, ул. Сынақ, ЖК Альфа, дом 17"
        explained = parse_record({"id": "explicit", "raw_address": raw}, gazetteer=self.gazetteer, explain=True)
        self.assertEqual(explained["result"]["living_area"], "Альфа")
        self.assertEqual(explained["fields"]["living_area"]["source"], "text")

    def test_ambiguous_complex_prevents_catalog_street_and_admin_fill(self) -> None:
        for raw in (
            "Астана, ул. Сынақ, ЖК Альфа, ЖК Бета, дом 17",
            "ЖК Альфа, ЖК Бета, Каталог",
        ):
            with self.subTest(raw=raw):
                explained = parse_record({"id": "ambiguous", "raw_address": raw}, gazetteer=self.gazetteer, explain=True)
                self.assertIsNone(explained["result"]["living_area"])
                self.assertIn("ambiguous_residential_complex", explained["warnings"])


if __name__ == "__main__":
    unittest.main()
