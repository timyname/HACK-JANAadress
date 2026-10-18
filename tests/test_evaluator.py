import unittest

from tools.evaluate import evaluate_records


def expected_record(**overrides):
    record = {
        "id": "case-1",
        "raw_address": "г. Үлгіқала, ул. Тестовая, д. 10",
        "province": "Сынақ",
        "city": "Үлгіқала",
        "district": None,
        "living_area": None,
        "street_type": "улица",
        "street_name": "Тестовая",
        "house": "10",
        "apartment": None,
        "entrance": None,
        "floor": None,
        "landmark": None,
        "confidence": 0.42,
    }
    record.update(overrides)
    return record


class EvaluatorTests(unittest.TestCase):
    def test_confidence_is_not_part_of_exact_oracle_equality(self):
        expected = [expected_record(confidence=0.20)]
        predicted = [expected_record(confidence=0.99)]

        report = evaluate_records(expected, predicted)

        self.assertEqual(report["record_exact_accuracy"], 1.0)
        self.assertNotIn("confidence", report["per_field"])
        self.assertIn("calibration", report)

    def test_reports_field_group_proxy_and_null_precision(self):
        expected = [expected_record(), expected_record(id="case-2", city=None, house=None)]
        predicted = [
            expected_record(city="Другая", district="Лишний"),
            expected_record(id="case-2", city=None, house=None),
        ]

        oracle = [
            {"metric_applicability": {"district": True, "living_area": False}},
            {"metric_applicability": {"district": False, "living_area": False}},
        ]
        report = evaluate_records(expected, predicted, oracle=oracle)

        self.assertEqual(report["per_field"]["city"]["exact_accuracy"], 0.5)
        self.assertEqual(report["per_field"]["district"]["null_recall"], 0.5)
        self.assertEqual(report["per_field"]["district"]["null_precision"], 1.0)
        self.assertAlmostEqual(report["group_proxy"]["weights"]["primary"], 0.60)
        self.assertLess(report["group_proxy"]["approximate_score"], 1.0)
        self.assertIn("all_fields", report)
        self.assertIn("null_metrics", report)

    def test_context_proxy_uses_only_explicitly_present_fields(self):
        expected = [expected_record(), expected_record(id="case-2", district="Есильский")]
        predicted = [expected_record(), expected_record(id="case-2", district="Другой")]
        oracle = [
            {"metric_applicability": {"district": False, "living_area": False}},
            {"metric_applicability": {"district": True, "living_area": False}},
        ]

        report = evaluate_records(expected, predicted, oracle=oracle)
        context = report["group_proxy"]["groups"]["context"]

        self.assertEqual(context["eligible_count"], 1)
        self.assertEqual(context["exact_accuracy"], 0.0)
        self.assertEqual(context["fields"]["living_area"]["status"], "UNAVAILABLE_NO_ELIGIBLE_LABELS")

    def test_context_proxy_is_unavailable_without_applicability_labels(self):
        report = evaluate_records([expected_record()], [expected_record()])

        self.assertIsNone(report["group_proxy"]["approximate_score"])
        self.assertEqual(
            report["group_proxy"]["groups"]["context"]["status"],
            "UNAVAILABLE_APPLICABILITY_REQUIRED",
        )

    def test_slice_metrics_are_derived_from_oracle_metadata(self):
        expected = [expected_record(), expected_record(id="case-2", street_name="Көше")]
        predicted = [expected_record(), expected_record(id="case-2", street_name="Коше")]
        oracle = [
            {"tier": "clean", "locale": "ru", "operations": []},
            {"tier": "hard", "locale": "kk", "operations": ["kk_letter"]},
        ]

        report = evaluate_records(expected, predicted, oracle=oracle)

        self.assertEqual(report["slices"]["tier"]["clean"]["record_exact_accuracy"], 1.0)
        self.assertEqual(report["slices"]["tier"]["hard"]["record_exact_accuracy"], 0.0)
        self.assertEqual(report["slices"]["operation"]["kk_letter"]["count"], 1)

    def test_rejects_positional_id_or_raw_mismatch(self):
        with self.assertRaisesRegex(ValueError, "position 0"):
            evaluate_records([expected_record()], [expected_record(id="wrong")])

    def test_rejects_missing_public_prediction_field_instead_of_scoring_it_as_null(self):
        predicted = expected_record()
        del predicted["district"]

        with self.assertRaisesRegex(ValueError, "missing fields.*district"):
            evaluate_records([expected_record()], [predicted])

    def test_strict_public_shape_rejects_extra_keys_and_invalid_types(self):
        extra = expected_record(debug={"source": "internal"})
        numeric_house = expected_record(house=18)
        boolean_confidence = expected_record(confidence=True)

        with self.assertRaisesRegex(ValueError, "unexpected fields.*debug"):
            evaluate_records([expected_record()], [extra])
        with self.assertRaisesRegex(ValueError, "house must be string or null"):
            evaluate_records([expected_record()], [numeric_house])
        with self.assertRaisesRegex(ValueError, "confidence must be an int or float"):
            evaluate_records([expected_record()], [boolean_confidence])

    def test_expected_oracle_is_validated_independently(self):
        invalid_expected = expected_record()
        del invalid_expected["landmark"]

        with self.assertRaisesRegex(ValueError, "expected.*missing fields.*landmark"):
            evaluate_records([invalid_expected], [expected_record()])

    def test_numeric_normalization_preserves_fraction_separator(self):
        expected = [expected_record(house="18/2")]
        predicted = [expected_record(house="18-2")]

        report = evaluate_records(expected, predicted)

        self.assertEqual(report["per_field"]["house"]["normalized_accuracy"], 0.0)


if __name__ == "__main__":
    unittest.main()
