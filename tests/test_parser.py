from __future__ import annotations

import tempfile
import unittest
from pathlib import Path


PUBLIC_FIELDS = [
    "id",
    "raw_address",
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
    "confidence",
]


def parser_api():
    try:
        from backend.jana.parser import parse_batch, parse_record
    except ModuleNotFoundError as error:
        raise AssertionError(f"parser module is missing: {error}") from error
    return parse_record, parse_batch


class NormalizationContractTests(unittest.TestCase):
    def test_normalization_keeps_unicode_source_offsets(self) -> None:
        try:
            from backend.jana.normalization import normalize_with_spans
        except ModuleNotFoundError as error:
            self.fail(f"normalization module is missing: {error}")

        raw = "  ҚАБАНБАЙ\u00a0  Батыр көшесі  "
        normalized = normalize_with_spans(raw)

        self.assertEqual(normalized.text, "қабанбай батыр көшесі")
        start = normalized.text.index("батыр")
        end = start + len("батыр")
        source_start, source_end = normalized.source_span(start, end)
        self.assertEqual(raw[source_start:source_end], "Батыр")

    def test_normalization_does_not_rewrite_the_original_value(self) -> None:
        try:
            from backend.jana.normalization import normalize_with_spans
        except ModuleNotFoundError as error:
            self.fail(f"normalization module is missing: {error}")

        raw = "Ақжол–12, үй 7Б"
        normalized = normalize_with_spans(raw)

        self.assertEqual(normalized.original, raw)
        self.assertIn("ақжол-12", normalized.text)


class ParserContractTests(unittest.TestCase):
    def test_batch_preserves_order_duplicates_and_public_shape(self) -> None:
        _, parse_batch = parser_api()
        records = [
            {"id": "same", "raw_address": "г. Астана, ул. Сарайшык, дом 8"},
            {"id": "same", "raw_address": "г. Шымкент, ул. Кунаева, дом 4"},
        ]

        result = parse_batch(records)

        self.assertEqual([item["id"] for item in result], ["same", "same"])
        self.assertEqual([item["raw_address"] for item in result], [item["raw_address"] for item in records])
        self.assertEqual(list(result[0]), PUBLIC_FIELDS)
        self.assertEqual(list(result[1]), PUBLIC_FIELDS)

    def test_invalid_batch_reports_only_the_record_index(self) -> None:
        _, parse_batch = parser_api()
        secret_value = "do-not-echo-this-synthetic-value"

        with self.assertRaisesRegex(ValueError, r"record\[1\]") as caught:
            parse_batch([
                {"id": "ok", "raw_address": "synthetic"},
                {"id": "bad", "raw_address": 42, "extra": secret_value},
            ])

        self.assertNotIn(secret_value, str(caught.exception))

    def test_unknown_address_returns_nulls_and_a_bounded_score(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record({"id": "unknown", "raw_address": "???"})

        self.assertTrue(all(result[field] is None for field in PUBLIC_FIELDS[2:-1]))
        self.assertGreaterEqual(result["confidence"], 0.0)
        self.assertLessEqual(result["confidence"], 0.25)

    def test_id_does_not_change_the_parsed_answer(self) -> None:
        parse_record, _ = parser_api()
        raw = "Астана, проспект Кабанбай батыра, дом 17"

        first = parse_record({"id": "alpha", "raw_address": raw})
        second = parse_record({"id": "omega", "raw_address": raw})

        self.assertEqual(
            {key: value for key, value in first.items() if key != "id"},
            {key: value for key, value in second.items() if key != "id"},
        )


class ParserExtractionTests(unittest.TestCase):
    def test_glued_and_suffix_markers_keep_fraction_and_litera(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "glued",
                "raw_address": "г.Астана, ул. Сығанақ, д.55/6А, 12кв, 3подъезд, 8этаж",
            }
        )

        self.assertEqual(result["city"], "Астана")
        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["street_name"], "Сығанақ")
        self.assertEqual(result["house"], "55/6А")
        self.assertEqual(result["apartment"], "12")
        self.assertEqual(result["entrance"], "3")
        self.assertEqual(result["floor"], "8")

    def test_kazakh_suffix_markers_use_russian_type_and_admin_catalog(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "kazakh",
                "raw_address": (
                    "Қарағанды қаласы, Бұқар жырау даңғылы, "
                    "18 үй, 45-пәтер, 2-кіреберіс, 7-қабат"
                ),
            }
        )

        self.assertEqual(result["city"], "Караганда")
        self.assertEqual(result["street_type"], "проспект")
        self.assertEqual(result["street_name"], "Бұқар жырау")
        self.assertEqual(result["house"], "18")
        self.assertEqual(result["apartment"], "45")
        self.assertEqual(result["entrance"], "2")
        self.assertEqual(result["floor"], "7")

    def test_bilingual_city_resolves_to_one_canonical_russian_value(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "bilingual",
                "raw_address": "Алматы қаласы / город Алматы, Абай даңғылы, 25 үй",
            }
        )

        self.assertEqual(result["city"], "Алматы")
        self.assertEqual(result["province"], "Алматы")
        self.assertEqual(result["street_type"], "проспект")

    def test_microdistrict_without_street_is_the_street(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "micro-only", "raw_address": "Алматы, мкр. Самал-2, дом 11"}
        )

        self.assertIsNone(result["living_area"])
        self.assertEqual(result["street_type"], "микрорайон")
        self.assertEqual(result["street_name"], "Самал-2")
        self.assertEqual(result["house"], "11")

    def test_microdistrict_with_street_becomes_living_area(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "micro-street",
                "raw_address": "Алматы, мкр. Самал-2, ул. Жандосова, дом 11",
            }
        )

        self.assertEqual(result["living_area"], "Самал-2")
        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["street_name"], "Жандосова")

    def test_street_surface_spelling_is_not_catalog_rewritten(self) -> None:
        parse_record, _ = parser_api()
        raw = "Астана қаласы, ҚаБанбай Батыр көшесі, 27 үй"

        result = parse_record({"id": "surface", "raw_address": raw})

        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["street_name"], "ҚаБанбай Батыр")

    def test_house_is_inferred_only_from_a_street_segment(self) -> None:
        parse_record, _ = parser_api()

        safe = parse_record({"id": "safe", "raw_address": "Астана, ул. Сығанақ 55/6А, кв. 12"})
        ambiguous = parse_record({"id": "ambiguous", "raw_address": "Астана, 12, 34"})

        self.assertEqual(safe["street_name"], "Сығанақ")
        self.assertEqual(safe["house"], "55/6А")
        self.assertIsNone(ambiguous["house"])

    def test_landmark_stops_before_entrance_and_floor(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "landmark",
                "raw_address": (
                    "Алматы, ул. Төле би, дом 10, ориентир: магазин Береке, "
                    "2 подъезд, 5 этаж"
                ),
            }
        )

        self.assertEqual(result["landmark"], "магазин Береке")
        self.assertEqual(result["entrance"], "2")
        self.assertEqual(result["floor"], "5")

    def test_landmark_without_commas_stops_at_following_detail_markers(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "landmark-glued-flow",
                "raw_address": (
                    "Алматы, ул. Төле би, дом 10, "
                    "ориентир магазин Береке 2 подъезд 5 этаж"
                ),
            }
        )

        self.assertEqual(result["landmark"], "магазин Береке")
        self.assertEqual(result["entrance"], "2")
        self.assertEqual(result["floor"], "5")

    def test_landmark_word_inside_typed_street_name_is_not_a_landmark(self) -> None:
        parse_record, _ = parser_api()
        cases = (
            ("г. Астана, ул. Бағдар, д. 12, кв. 3", "Бағдар"),
            ("Астана қ., Бағдар көшесі, 12 үй, 3 пәтер", "Бағдар"),
            ("г. Астана, улица Бағдар жолы, дом 12", "Бағдар жолы"),
        )

        for raw, expected_name in cases:
            with self.subTest(raw=raw):
                explained = parse_record(
                    {"id": "street-named-bagdar", "raw_address": raw},
                    explain=True,
                )
                result = explained["result"]
                self.assertEqual(result["street_type"], "улица")
                self.assertEqual(result["street_name"], expected_name)
                self.assertIsNone(result["landmark"])
                span = explained["fields"]["street_name"]["span"]
                self.assertEqual(raw[slice(*span)], expected_name)

    def test_street_named_bagdar_keeps_a_later_explicit_landmark(self) -> None:
        parse_record, _ = parser_api()
        raw = "г. Астана, ул. Бағдар, д. 12, бағдар дүкен Береке"

        explained = parse_record(
            {"id": "street-and-landmark", "raw_address": raw}, explain=True
        )
        result = explained["result"]

        self.assertEqual(result["street_name"], "Бағдар")
        self.assertEqual(result["house"], "12")
        self.assertEqual(result["landmark"], "дүкен Береке")
        street_span = explained["fields"]["street_name"]["span"]
        landmark_span = explained["fields"]["landmark"]["span"]
        self.assertEqual(raw[slice(*street_span)], "Бағдар")
        self.assertEqual(raw[slice(*landmark_span)], "дүкен Береке")

    def test_unseparated_bagdar_after_street_name_remains_a_landmark(self) -> None:
        parse_record, _ = parser_api()
        raw = "Астана ул. Сынақ бағдар дүкен Береке дом 12"

        result = parse_record({"id": "unseparated-landmark", "raw_address": raw})

        self.assertEqual(result["street_name"], "Сынақ")
        self.assertEqual(result["house"], "12")
        self.assertEqual(result["landmark"], "дүкен Береке")

    def test_explicit_details_are_peeled_from_the_street_segment(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "one-segment",
                "raw_address": "Астана, ул. Достык дом 7 квартира 3",
            }
        )

        self.assertEqual(result["street_name"], "Достык")
        self.assertEqual(result["house"], "7")
        self.assertEqual(result["apartment"], "3")

    def test_compact_kazakh_markers_preserve_spans_and_canonicalize_admin(self) -> None:
        parse_record, _ = parser_api()
        raw = (
            "обл. Шығыс Қазақстан   қ. Өскемен   ауд. Үлбі   "
            "к-сі Көк Із   үй 003   пәт. 4   қабат -1"
        )

        explained = parse_record({"id": "compact-kz", "raw_address": raw}, explain=True)
        result = explained["result"]

        self.assertEqual(result["province"], "Восточно-Казахстанская область")
        self.assertEqual(result["city"], "Усть-Каменогорск")
        # No catalog-backed district for Өскемен is present here, so the
        # source spelling is preserved instead of inventing a translation.
        self.assertEqual(result["district"], "Үлбі")
        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["street_name"], "Көк Із")
        self.assertEqual(result["house"], "003")
        self.assertEqual(result["apartment"], "4")
        self.assertEqual(result["floor"], "-1")
        for field in ("province", "city", "district", "street_type", "street_name", "floor"):
            span = explained["fields"][field]["span"]
            self.assertIsNotNone(span, field)
            self.assertTrue(raw[slice(*span)], field)

    def test_hyphenated_avenue_marker_is_not_part_of_the_name(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "avenue", "raw_address": "г. Караганда, пр-т Золотое Направление, д. 9"}
        )

        self.assertEqual(result["street_type"], "проспект")
        self.assertEqual(result["street_name"], "Золотое Направление")

    def test_compact_microdistrict_marker_keeps_dual_role(self) -> None:
        parse_record, _ = parser_api()

        only_area = parse_record(
            {"id": "area-only", "raw_address": "қ. Алматы, ш-а Көк Арка, үй 8"}
        )
        with_street = parse_record(
            {
                "id": "area-street",
                "raw_address": "қ. Алматы, ш-а Көк Арка, к-сі Тыныш Белгі, үй 8",
            }
        )

        self.assertEqual(only_area["street_type"], "микрорайон")
        self.assertEqual(only_area["street_name"], "Көк Арка")
        self.assertIsNone(only_area["living_area"])
        self.assertEqual(with_street["living_area"], "Көк Арка")
        self.assertEqual(with_street["street_type"], "улица")
        self.assertEqual(with_street["street_name"], "Тыныш Белгі")

    def test_mixed_script_street_marker_is_recognized(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "mixed-script", "raw_address": "г. Караганда, yл. Северный Узел, д. 4"}
        )

        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["street_name"], "Северный Узел")

    def test_admin_aliases_are_not_inferred_from_other_components(self) -> None:
        parse_record, _ = parser_api()

        city_as_street = parse_record(
            {"id": "city-street", "raw_address": "ул. Алматы, дом 5"}, explain=True
        )
        district_as_city = parse_record(
            {"id": "district-city", "raw_address": "г. Алматы, ул. Тестовая, дом 5"}
        )
        district_as_street = parse_record(
            {"id": "district-street", "raw_address": "г. Астана, ул. Абай, дом 7"}
        )

        self.assertIsNone(city_as_street["result"]["city"])
        self.assertEqual(city_as_street["fields"]["city"]["source"], "unknown")
        self.assertIsNone(district_as_city["district"])
        self.assertIsNone(district_as_street["district"])

    def test_explicit_house_preserves_numeric_street_name_suffix(self) -> None:
        parse_record, _ = parser_api()

        microdistrict = parse_record(
            {"id": "micro-number", "raw_address": "Алматы, мкр. Восток 3, дом 9"}
        )
        street = parse_record(
            {"id": "street-number", "raw_address": "Алматы, ул. Линия 7, дом 12"}
        )

        self.assertEqual(microdistrict["street_name"], "Восток 3")
        self.assertEqual(microdistrict["house"], "9")
        self.assertEqual(street["street_name"], "Линия 7")
        self.assertEqual(street["house"], "12")

    def test_bare_house_after_numbered_microdistrict_preserves_name(self) -> None:
        parse_record, _ = parser_api()
        cases = (
            ("г. Алматы, мкр. Арман 2, 17, кв. 8", "Арман 2", "17"),
            ("Алматы қаласы, ш-а Жаңа 2, 17, 8 пәтер", "Жаңа 2", "17"),
        )

        for raw, expected_name, expected_house in cases:
            with self.subTest(raw=raw):
                explained = parse_record(
                    {"id": "bare-house-after-area", "raw_address": raw}, explain=True
                )
                result = explained["result"]
                self.assertEqual(result["street_type"], "микрорайон")
                self.assertEqual(result["street_name"], expected_name)
                self.assertEqual(result["house"], expected_house)
                self.assertIsNone(result["district"])
                self.assertEqual(raw[slice(*explained["fields"]["street_name"]["span"])], expected_name)
                self.assertEqual(raw[slice(*explained["fields"]["house"]["span"])], expected_house)

    def test_hyphenated_numbered_microdistrict_accepts_following_bare_house(self) -> None:
        parse_record, _ = parser_api()
        cases = (
            ("г. Алматы, мкр. Самал-2, 17, кв. 8", "Самал-2"),
            ("г. Алматы, мкр. Самал №2, 17, кв. 8", "Самал №2"),
            ("г. Алматы, мкр. Самал #2, 17, кв. 8", "Самал #2"),
            ("г. Алматы, мкр. 5-й, 17, кв. 8", "5-й"),
            ("Алматы қаласы, ш-а 5-ші, 17, 8 пәтер", "5-ші"),
        )

        for raw, expected_name in cases:
            with self.subTest(raw=raw):
                result = parse_record({"id": "numbered-area-form", "raw_address": raw})
                self.assertEqual(result["street_name"], expected_name)
                self.assertEqual(result["house"], "17")

    def test_postal_index_after_numbered_microdistrict_is_not_a_house(self) -> None:
        parse_record, _ = parser_api()

        postal_only = parse_record({
            "id": "postal-only",
            "raw_address": "г. Алматы, мкр. Арман 2, 050000",
        })
        house_then_postal = parse_record({
            "id": "house-postal",
            "raw_address": "г. Алматы, мкр. Арман 2, 17, 050000, кв. 8",
        })

        self.assertEqual(postal_only["street_name"], "Арман 2")
        self.assertIsNone(postal_only["house"])
        self.assertEqual(house_then_postal["street_name"], "Арман 2")
        self.assertEqual(house_then_postal["house"], "17")

    def test_two_bare_numbers_do_not_steal_numbered_microdistrict_suffix(self) -> None:
        parse_record, _ = parser_api()
        raw = "г. Алматы, мкр. Арман 2, 17, 19, кв. 8"

        explained = parse_record(
            {"id": "ambiguous-bare-house", "raw_address": raw}, explain=True
        )

        self.assertEqual(explained["result"]["street_name"], "Арман 2")
        self.assertIsNone(explained["result"]["house"])
        self.assertIn("ambiguous_bare_house", explained["warnings"])

    def test_bare_house_inference_ignores_numbers_before_numbered_microdistrict(self) -> None:
        parse_record, _ = parser_api()

        before_only = parse_record({
            "id": "before-only",
            "raw_address": "17, г. Алматы, мкр. Арман 2, кв. 8",
        })
        before_and_after = parse_record({
            "id": "before-and-after",
            "raw_address": "99, г. Алматы, мкр. Арман 2, 17, кв. 8",
        })
        ordinary_street = parse_record({
            "id": "ordinary-street",
            "raw_address": "050000, г. Алматы, ул. Абая 17",
        })

        self.assertEqual(before_only["street_name"], "Арман 2")
        self.assertIsNone(before_only["house"])
        self.assertEqual(before_and_after["street_name"], "Арман 2")
        self.assertEqual(before_and_after["house"], "17")
        self.assertEqual(ordinary_street["street_name"], "Абая")
        self.assertEqual(ordinary_street["house"], "17")

    def test_numbered_living_area_does_not_disable_street_house_inference(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record({
            "id": "numbered-area-and-street",
            "raw_address": "г. Алматы, мкр. Арман 2, ул. Сынақ 17, кв. 8",
        })

        self.assertEqual(result["living_area"], "Арман 2")
        self.assertEqual(result["street_name"], "Сынақ")
        self.assertEqual(result["house"], "17")

    def test_ambiguous_and_conflicting_numbers_warn_and_reduce_confidence(self) -> None:
        parse_record, _ = parser_api()

        baseline = parse_record(
            {"id": "base", "raw_address": "г. Астана, ул. Учебная, дом 12"},
            explain=True,
        )
        ambiguous = parse_record(
            {"id": "amb", "raw_address": "г. Астана, ул. Учебная12 34"},
            explain=True,
        )
        conflicting = parse_record(
            {"id": "conflict", "raw_address": "г. Астана, ул. Учебная, дом12, дом34"},
            explain=True,
        )

        self.assertTrue(ambiguous["warnings"])
        self.assertTrue(any("house" in warning for warning in ambiguous["warnings"]))
        self.assertTrue(any("conflict" in warning for warning in conflicting["warnings"]))
        self.assertLess(ambiguous["result"]["confidence"], baseline["result"]["confidence"])
        self.assertLess(conflicting["result"]["confidence"], baseline["result"]["confidence"])

    def test_glued_role_markers_do_not_become_number_litera(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "glued-roles",
                "raw_address": "г.Астана, ул.Учебная, д12кв5под2эт5",
            }
        )

        self.assertEqual(result["house"], "12")
        self.assertEqual(result["apartment"], "5")
        self.assertEqual(result["entrance"], "2")
        self.assertEqual(result["floor"], "5")

    def test_landmark_numbers_are_masked_from_address_roles(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "landmark-number", "raw_address": "г. Астана, ориентир дом34"}
        )

        self.assertEqual(result["landmark"], "дом34")
        self.assertIsNone(result["house"])

    def test_unknown_rural_locality_is_preserved_as_city(self) -> None:
        parse_record, _ = parser_api()

        russian = parse_record(
            {"id": "rural-ru", "raw_address": "село Жанаозек, ул. Центральная, дом 1"}
        )
        kazakh = parse_record(
            {"id": "rural-kk", "raw_address": "Қарасу ауылы, Бейбіт к-сі, үй 2"}
        )

        self.assertEqual(russian["city"], "Жанаозек")
        self.assertEqual(kazakh["city"], "Қарасу")
        self.assertEqual(kazakh["street_type"], "улица")
        self.assertEqual(kazakh["street_name"], "Бейбіт")

    def test_compact_city_street_and_detail_markers_are_peeled(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "compact-all", "raw_address": "г.Алматы ул.Абай дом12кв5"}
        )

        self.assertEqual(result["city"], "Алматы")
        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["street_name"], "Абай")
        self.assertEqual(result["house"], "12")
        self.assertEqual(result["apartment"], "5")

    def test_full_number_markers_beat_letters_inside_other_markers(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "marker-priority",
                "raw_address": "г. Сағымкент, ул. Учебная, кв3под2эт5",
            }
        )

        self.assertIsNone(result["house"])
        self.assertEqual(result["apartment"], "3")
        self.assertEqual(result["entrance"], "2")
        self.assertEqual(result["floor"], "5")

    def test_noun_landmark_stops_before_suffix_details(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {
                "id": "noun-landmark-details",
                "raw_address": "г. Астана, магазин Башня 2 подъезд 5 этаж",
            }
        )

        self.assertEqual(result["landmark"], "магазин Башня")
        self.assertEqual(result["entrance"], "2")
        self.assertEqual(result["floor"], "5")

    def test_unmarked_city_before_typed_street_is_contextual_not_global(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "compact-context", "raw_address": "Астана ул. Учебная дом10"}
        )

        self.assertEqual(result["city"], "Астана")
        self.assertEqual(result["street_name"], "Учебная")
        self.assertEqual(result["house"], "10")

    def test_mixed_kazakh_street_marker_and_house_surfaces_are_preserved(self) -> None:
        parse_record, _ = parser_api()

        mixed = parse_record(
            {"id": "mixed-k", "raw_address": "қ. Алматы, k-сі Бейбіт, үй 12–3"}
        )
        litera = parse_record(
            {"id": "litera", "raw_address": "қ. Алматы, к-сі Бейбіт, үй 12А"}
        )
        simple = parse_record(
            {"id": "simple", "raw_address": "қ. Алматы, к-сі Бейбіт, үй 1"}
        )
        compound = parse_record(
            {"id": "compound", "raw_address": "Астана, ул. Учебная, дом 12А/1"}
        )
        fractional_litera = parse_record(
            {"id": "fractional-litera", "raw_address": "Астана, ул. Учебная, дом 12/3Б"}
        )

        self.assertEqual(mixed["street_type"], "улица")
        self.assertEqual(mixed["street_name"], "Бейбіт")
        self.assertEqual(mixed["house"], "12–3")
        self.assertEqual(litera["house"], "12А")
        self.assertEqual(simple["house"], "1")
        self.assertEqual(compound["house"], "12А/1")
        self.assertEqual(fractional_litera["house"], "12/3Б")


class ParserRealGazetteerIntegrationTests(unittest.TestCase):
    def _catalog(self, path: Path):
        from jana.gazetteer import Gazetteer
        from tools.build_gazetteer import AdminRecord, create_database

        create_database(
            path,
            [
                AdminRecord(
                    field="city",
                    canonical="Алматы",
                    aliases=("Алматы қаласы", "г. Алматы"),
                    city="Алматы",
                ),
                AdminRecord(
                    field="city",
                    canonical="Астана",
                    aliases=("Астана қаласы", "г. Астана"),
                    city="Астана",
                ),
                AdminRecord(
                    field="city",
                    canonical="Актобе",
                    aliases=("Ақтөбе қаласы", "г. Актобе"),
                    city="Актобе",
                ),
                AdminRecord(
                    field="city",
                    canonical="Шымкент",
                    aliases=("Шымкент қаласы", "г. Шымкент"),
                    city="Шымкент",
                ),
                AdminRecord(
                    field="district",
                    canonical="район Алматы",
                    aliases=("Алматы ауданы", "р-н Алматы"),
                    kato_code="711110000",
                    province="Астана",
                    city="Астана",
                    district="район Алматы",
                ),
                AdminRecord(
                    field="district",
                    canonical="район Алматы",
                    aliases=("Алматы ауданы", "р-н Алматы"),
                    kato_code="151013100",
                    province="Актюбинская область",
                    city="Актобе",
                    district="район Алматы",
                ),
            ],
            [],
            [],
            [],
        )
        return Gazetteer(path)

    def test_gazetteer_admin_lookup_does_not_read_street_or_landmark_spans(self) -> None:
        parse_record, _ = parser_api()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            with self._catalog(path) as gazetteer:
                street = parse_record(
                    {"id": "street-city-name", "raw_address": "ул. Алматы, дом10"},
                    gazetteer=gazetteer,
                )
                landmark = parse_record(
                    {
                        "id": "landmark-city-name",
                        "raw_address": "ориентир Алматы, ул. Учебная, дом10",
                    },
                    gazetteer=gazetteer,
                )
                positive = parse_record(
                    {"id": "real-city", "raw_address": "Алматы, ул. Учебная, дом10"},
                    gazetteer=gazetteer,
                )

        self.assertIsNone(street["city"])
        self.assertIsNone(landmark["city"])
        self.assertEqual(positive["city"], "Алматы")

    def test_explicit_district_is_resolved_only_with_matching_parent(self) -> None:
        parse_record, _ = parser_api()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gazetteer.sqlite3"
            with self._catalog(path) as gazetteer:
                astana = parse_record(
                    {"id": "astana-district", "raw_address": "г. Астана, р-н Алматы"},
                    gazetteer=gazetteer,
                    explain=True,
                )
                aktobe = parse_record(
                    {"id": "aktobe-district", "raw_address": "қ. Ақтөбе, Алматы ауданы"},
                    gazetteer=gazetteer,
                    explain=True,
                )
                no_parent = parse_record(
                    {"id": "no-parent", "raw_address": "р-н Алматы"},
                    gazetteer=gazetteer,
                    explain=True,
                )
                conflict = parse_record(
                    {"id": "wrong-parent", "raw_address": "г. Шымкент, р-н Алматы"},
                    gazetteer=gazetteer,
                    explain=True,
                )
                unknown = parse_record(
                    {"id": "unknown", "raw_address": "г. Астана, р-н Сарыөзек"},
                    gazetteer=gazetteer,
                    explain=True,
                )
                city_only = parse_record(
                    {"id": "city-only", "raw_address": "Алматы"},
                    gazetteer=gazetteer,
                )

        self.assertEqual(astana["result"]["district"], "Алматы")
        self.assertEqual(astana["fields"]["district"]["source"], "gazetteer")
        self.assertEqual(astana["fields"]["district"]["kato_code"], "711110000")
        astana_span = astana["fields"]["district"]["span"]
        self.assertEqual("г. Астана, р-н Алматы"[slice(*astana_span)], "Алматы")
        self.assertEqual(aktobe["result"]["district"], "Алматы")
        self.assertEqual(aktobe["fields"]["district"]["kato_code"], "151013100")
        self.assertEqual(no_parent["result"]["district"], "Алматы")
        self.assertIsNone(no_parent["result"]["city"])
        self.assertIsNone(no_parent["result"]["province"])
        self.assertTrue(any("district" in warning for warning in no_parent["warnings"]))
        self.assertEqual(conflict["result"]["district"], "Алматы")
        self.assertTrue(any("district" in warning for warning in conflict["warnings"]))
        self.assertEqual(unknown["result"]["district"], "Сарыөзек")
        self.assertIn("gazetteer_district_unverified", unknown["warnings"])
        self.assertIsNone(city_only["district"])


class ParserExplainAndGazetteerTests(unittest.TestCase):
    class FakeGazetteer:
        last_street_type = None
        last_province = None

        def find_admin(self, text: str) -> dict:
            return {
                "province": {"value": "Карагандинская область", "source": "gazetteer", "span": None},
                "city": {"value": "Астана", "source": "gazetteer", "span": None},
            }

        def lookup_street(
            self,
            city: str | None,
            name: str,
            house: str | None = None,
            street_type: str | None = None,
            province: str | None = None,
        ) -> dict:
            self.last_street_type = street_type
            self.last_province = province
            return {
                "matched": True,
                "ambiguous": False,
                "source": "gazetteer",
                "score": 1.0,
                "street_type": "проспект",
                "district": "Октябрьский район",
            }

    def test_explicit_text_wins_over_conflicting_gazetteer_enrichment(self) -> None:
        parse_record, _ = parser_api()

        gazetteer = self.FakeGazetteer()
        result = parse_record(
            {"id": "precedence", "raw_address": "г. Сарань, ул. Тестовая, дом 5"},
            gazetteer=gazetteer,
        )

        self.assertEqual(result["city"], "Сарань")
        self.assertEqual(result["province"], "Карагандинская область")
        self.assertEqual(result["street_type"], "улица")
        self.assertEqual(result["district"], "Октябрьский")
        self.assertEqual(gazetteer.last_street_type, "улица")
        self.assertEqual(gazetteer.last_province, "Карагандинская область")

    def test_explain_returns_source_spans_and_unknown_provenance(self) -> None:
        parse_record, _ = parser_api()
        raw = "Астана, ҚаБанбай Батыр көшесі, 27 үй"

        explained = parse_record({"id": "explain", "raw_address": raw}, explain=True)

        self.assertEqual(set(explained), {"result", "fields", "warnings"})
        result = explained["result"]
        street_meta = explained["fields"]["street_name"]
        self.assertEqual(street_meta["source"], "text")
        self.assertEqual(raw[slice(*street_meta["span"])], result["street_name"])
        self.assertEqual(explained["fields"]["district"]["source"], "unknown")
        self.assertEqual(explained["fields"]["confidence"]["source"], "heuristic")

    def test_explain_street_type_span_points_to_the_original_marker(self) -> None:
        parse_record, _ = parser_api()
        raw = "Астана, Қабанбай Батыр көшесі, 27 үй"

        explained = parse_record({"id": "type-span", "raw_address": raw}, explain=True)

        type_span = explained["fields"]["street_type"]["span"]
        self.assertEqual(raw[slice(*type_span)], "көшесі")

    def test_kazakh_dead_end_uses_shared_canonical_street_type(self) -> None:
        parse_record, _ = parser_api()

        result = parse_record(
            {"id": "dead-end", "raw_address": "Алматы, Басты тұйығы, 7 үй"}
        )

        self.assertEqual(result["street_type"], "тупик")
        self.assertEqual(result["street_name"], "Басты")
        self.assertEqual(result["house"], "7")

    def test_confidence_counts_present_fields_without_provenance_discount(self) -> None:
        from jana.parser import _confidence

        values = {
            "province": "P",
            "city": "C",
            "district": "D",
            "living_area": None,
            "street_type": "улица",
            "street_name": "S",
            "house": "7",
            "apartment": "2",
            "entrance": None,
            "floor": None,
            "landmark": None,
        }
        text_metadata = {key: {"source": "text"} for key in values}
        mixed_metadata = {
            key: {"source": "gazetteer" if key == "district" else "heuristic"}
            for key in values
        }

        self.assertEqual(
            _confidence(values, text_metadata, []),
            _confidence(values, mixed_metadata, []),
        )

    def test_conflicting_area_and_street_parents_do_not_create_hybrid(self) -> None:
        parse_record, _ = parser_api()

        class ConflictingGazetteer:
            def find_admin(self, text: str) -> dict:
                return {}

            def lookup_area(
                self, city: str | None, name: str, area_type: str | None = None
            ) -> dict:
                return {
                    "matched": True,
                    "ambiguous": False,
                    "source": "gazetteer",
                    "province": "Область Б",
                    "city": "Город Б",
                    "district": "Район Б",
                }

            def lookup_street(
                self,
                city: str | None,
                name: str,
                house: str | None = None,
                street_type: str | None = None,
                province: str | None = None,
            ) -> dict:
                return {
                    "matched": True,
                    "ambiguous": False,
                    "source": "gazetteer",
                    "province": "Область А",
                    "city": "Город А",
                    "district": "Район А",
                }

        explained = parse_record(
            {
                "id": "cross-parent",
                "raw_address": "мкр. Арман, ул. Тестовая, дом 1",
            },
            gazetteer=ConflictingGazetteer(),
            explain=True,
        )

        self.assertIsNone(explained["result"]["province"])
        self.assertIsNone(explained["result"]["city"])
        self.assertIsNone(explained["result"]["district"])
        self.assertIn("gazetteer_area_street_conflict", explained["warnings"])

    def test_explicit_parent_blocks_incompatible_lookup_enrichment(self) -> None:
        parse_record, _ = parser_api()

        class CrossProvinceGazetteer:
            def find_admin(self, text: str) -> dict:
                return {
                    "province": {
                        "value": "Карагандинская область",
                        "source": "gazetteer",
                        "span": [0, len("Карагандинская область")],
                    }
                }

            def lookup_street(
                self,
                city: str | None,
                name: str,
                house: str | None = None,
                street_type: str | None = None,
                province: str | None = None,
            ) -> dict:
                return {
                    "matched": True,
                    "ambiguous": False,
                    "source": "gazetteer",
                    "province": "Алматинская область",
                    "city": "Алматы",
                    "district": "Наурызбайский район",
                }

        explained = parse_record(
            {
                "id": "explicit-parent",
                "raw_address": (
                    "Карагандинская область, ул. 75 лет Победы, дом 1"
                ),
            },
            gazetteer=CrossProvinceGazetteer(),
            explain=True,
        )

        self.assertEqual(
            explained["result"]["province"], "Карагандинская область"
        )
        self.assertIsNone(explained["result"]["city"])
        self.assertIsNone(explained["result"]["district"])
        self.assertIn("gazetteer_street_parent_conflict", explained["warnings"])

    def test_district_marker_variants_are_parent_equivalent(self) -> None:
        parse_record, _ = parser_api()

        class CompatibleDistrictGazetteer:
            def find_admin(self, text: str) -> dict:
                return {
                    "district": {
                        "value": "Жетысуский",
                        "source": "gazetteer",
                        "span": [0, len("Жетысуский район")],
                    }
                }

            def lookup_street(
                self,
                city: str | None,
                name: str,
                house: str | None = None,
                street_type: str | None = None,
                province: str | None = None,
            ) -> dict:
                return {
                    "matched": True,
                    "ambiguous": False,
                    "source": "gazetteer",
                    "province": "Алматинская область",
                    "city": "Алматы",
                    "district": "Жетысуский район",
                }

        explained = parse_record(
            {
                "id": "district-form",
                "raw_address": "Жетысуский район, ул. Венецианова, дом 1",
            },
            gazetteer=CompatibleDistrictGazetteer(),
            explain=True,
        )

        self.assertEqual(explained["result"]["district"], "Жетысуский")
        self.assertEqual(explained["result"]["city"], "Алматы")
        self.assertEqual(explained["result"]["province"], "Алматинская область")
        self.assertNotIn(
            "gazetteer_street_parent_conflict", explained["warnings"]
        )

    def test_ambiguous_gazetteer_result_is_reported_not_silently_used(self) -> None:
        parse_record, _ = parser_api()

        class AmbiguousGazetteer(self.FakeGazetteer):
            def lookup_street(
                self,
                city: str | None,
                name: str,
                house: str | None = None,
                street_type: str | None = None,
                province: str | None = None,
            ) -> dict:
                return {"matched": True, "ambiguous": True, "source": "gazetteer", "district": "Wrong"}

        explained = parse_record(
            {"id": "ambiguous-gazetteer", "raw_address": "ул. Тестовая, дом 5"},
            gazetteer=AmbiguousGazetteer(),
            explain=True,
        )

        self.assertIsNone(explained["result"]["district"])
        self.assertTrue(any("ambiguous" in warning for warning in explained["warnings"]))


if __name__ == "__main__":
    unittest.main()
