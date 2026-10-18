import json
import tempfile
import unittest
from pathlib import Path

from tools.synthetic_corpus import (
    PUBLIC_FIELDS,
    generate_cases,
    load_config,
    verify_bundle,
    write_bundle,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs" / "corpus" / "base.json"


class SyntheticCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(CONFIG)

    def test_same_seed_produces_identical_cases(self):
        left = generate_cases(self.config, count=48, split="dev", master_seed="determinism")
        right = generate_cases(self.config, count=48, split="dev", master_seed="determinism")

        self.assertEqual(left, right)

    def test_dev_and_test_split_semantic_families_before_mutation(self):
        dev = generate_cases(self.config, count=128, split="dev", master_seed="split-check")
        test = generate_cases(self.config, count=128, split="test", master_seed="split-check")

        dev_families = {case.oracle["family_id"] for case in dev}
        test_families = {case.oracle["family_id"] for case in test}
        self.assertTrue(dev_families)
        self.assertTrue(test_families)
        self.assertTrue(dev_families.isdisjoint(test_families))

    def test_release_splits_have_no_rendered_raw_or_lower_entity_overlap(self):
        dev = generate_cases(
            self.config,
            count=10000,
            split="dev",
            master_seed="jana-address-dev-v1-2026-10-16",
        )
        test = generate_cases(
            self.config,
            count=1000,
            split="test",
            master_seed="jana-address-test-v1-2026-10-16",
        )

        self.assertTrue(
            {case.input_record["raw_address"] for case in dev}.isdisjoint(
                {case.input_record["raw_address"] for case in test}
            )
        )
        self.assertTrue(
            {case.oracle["semantic_family_key"] for case in dev}.isdisjoint(
                {case.oracle["semantic_family_key"] for case in test}
            )
        )

    def test_test_split_has_enough_families_for_a_meaningful_holdout(self):
        test = generate_cases(self.config, count=1000, split="test", master_seed="holdout")

        self.assertGreaterEqual(len({case.oracle["family_id"] for case in test}), 8)

    def test_street_typo_changes_expected_surface_not_canonical_truth(self):
        cases = generate_cases(
            self.config,
            count=400,
            split="dev",
            master_seed="street-surface",
        )
        mutated = next(
            case for case in cases if "street_typo" in case.oracle["operations"]
        )

        self.assertNotEqual(
            mutated.expected_record["street_name"],
            mutated.oracle["canonical_fields"]["street_name"],
        )
        self.assertIn(mutated.expected_record["street_name"], mutated.input_record["raw_address"])

    def test_generated_cases_cover_locales_markers_order_and_irrecoverable_nulls(self):
        cases = generate_cases(self.config, count=1200, split="dev", master_seed="coverage")

        self.assertEqual({case.oracle["locale"] for case in cases}, {"ru", "kk", "mixed"})
        operations = {op for case in cases for op in case.oracle["operations"]}
        self.assertTrue(
            {"spacing", "marker_glue", "marker_suffix", "reorder", "mixed_script"}
            <= operations
        )
        ambiguous = [case for case in cases if case.oracle["tier"] == "irrecoverable"]
        self.assertTrue(ambiguous)
        self.assertTrue(any(case.expected_record["house"] is None for case in ambiguous))

    def test_microdistrict_has_dual_public_role(self):
        cases = generate_cases(self.config, count=1200, split="dev", master_seed="microdistrict")

        sole = next(case for case in cases if case.oracle["shape"] == "microdistrict_only")
        with_street = next(case for case in cases if case.oracle["shape"] == "microdistrict_street")
        self.assertEqual(sole.expected_record["street_type"], "микрорайон")
        self.assertIsNone(sole.expected_record["living_area"])
        self.assertIsNotNone(with_street.expected_record["living_area"])
        self.assertNotEqual(with_street.expected_record["street_type"], "микрорайон")

    def test_unknown_kazakh_living_area_uses_source_surface_not_translation(self):
        cases = generate_cases(self.config, count=240, split="dev", master_seed="living-surface")
        case = next(
            item
            for item in cases
            if item.oracle["locale"] == "kk" and item.oracle["shape"] == "microdistrict_street"
        )

        self.assertIn(case.expected_record["living_area"], case.input_record["raw_address"])
        self.assertEqual(
            case.expected_record["living_area"],
            case.oracle["source_surfaces"]["living_area"],
        )

    def test_regional_province_oracle_uses_authoritative_canonical_with_type(self):
        cases = generate_cases(self.config, count=240, split="dev", master_seed="province-canonical")
        expected_by_city = {
            "Караганда": "Карагандинская область",
            "Актобе": "Актюбинская область",
            "Усть-Каменогорск": "Восточно-Казахстанская область",
        }

        seen = set()
        for case in cases:
            city = case.expected_record["city"]
            if city in expected_by_city:
                seen.add(city)
                self.assertEqual(case.expected_record["province"], expected_by_city[city])
        self.assertEqual(seen, set(expected_by_city))

    def test_district_outputs_use_only_audited_kato_surface_mappings(self):
        # Independently transcribed from KATO_18.09.2026.xlsx, not parser output.
        # The old adjective/truncated names are not aliases in that source.
        expected = {
            "Алматы": {"ru": "Ауэзовский", "kk": "Ауэзовский"},
            "Астана": {"ru": "Есильский", "kk": "Есиль"},
            "Шымкент": {"ru": "Абайский", "kk": "Абайский"},
            "Караганда": {"ru": "Казыбекбийский", "kk": "Қазыбек би"},
            "Актобе": {"ru": "Алматинский", "kk": "Алматы"},
            "Усть-Каменогорск": {"ru": "Ульбинский", "kk": "Үлбі"},
        }
        cases = generate_cases(self.config, count=600, split="dev", master_seed="kato-audit")
        seen = set()
        for case in cases:
            if case.expected_record["district"] is None:
                continue
            city = case.expected_record["city"]
            locale = case.oracle["locale"]
            source_locale = "ru" if locale == "ru" else "kk"
            self.assertEqual(case.expected_record["district"], expected[city][source_locale], (city, locale))
            seen.add((city, locale))
        self.assertEqual(seen, {(city, locale) for city in expected for locale in ("ru", "kk", "mixed")})

    def test_district_raw_hooks_preserve_legacy_input_spellings(self):
        raw_by_city = {
            "Алматы": {"ru": "Ауэзовский", "kk": "Әуезов"},
            "Астана": {"ru": "Есильский", "kk": "Есіл"},
            "Шымкент": {"ru": "Абайский", "kk": "Абай"},
            "Караганда": {"ru": "Казыбекбийский", "kk": "Қазыбек би"},
            "Актобе": {"ru": "Алматинский", "kk": "Алматы"},
            "Усть-Каменогорск": {"ru": "Ульбинский", "kk": "Үлбі"},
        }
        cases = generate_cases(self.config, count=240, split="dev", master_seed="district-surfaces")
        for case in cases:
            source_locale = "ru" if case.oracle["locale"] == "ru" else "kk"
            raw = raw_by_city[case.expected_record["city"]][source_locale]
            surface = case.oracle["source_surfaces"].get("district")
            if case.expected_record["district"] is not None:
                self.assertEqual(surface, raw)
                self.assertIn(raw, case.input_record["raw_address"])
            else:
                self.assertIsNone(surface)

    def test_district_oracle_distinguishes_catalog_matches_unknown_names_and_absence(self):
        audited = {
            "Алматы": ("751310000", {"ru", "kk"}),
            "Астана": ("711210000", {"kk"}),
            "Шымкент": ("791110000", {"ru", "kk"}),
            "Караганда": ("351013100", set()),
            "Актобе": ("151013100", {"kk"}),
            "Усть-Каменогорск": (None, set()),
        }
        cases = generate_cases(self.config, count=240, split="dev", master_seed="district-provenance")
        for case in cases:
            resolution = case.oracle.get("district_resolution", {})
            code, verified_locales = audited[case.expected_record["city"]]
            source_locale = "ru" if case.oracle["locale"] == "ru" else "kk"
            if case.expected_record["district"] is None:
                self.assertEqual(resolution.get("status"), "absent")
                self.assertIsNone(resolution["source"])
                self.assertIsNone(resolution["kato_code"])
                self.assertIsNone(case.oracle["canonical_fields"]["district"])
            elif source_locale in verified_locales:
                self.assertEqual(resolution.get("status"), "kato_type_stripped")
                self.assertEqual(resolution["source"], "kato-nk-rk-11-2025")
                self.assertEqual(resolution["kato_code"], code)
                self.assertEqual(case.oracle["canonical_fields"]["district"], case.expected_record["district"])
            else:
                self.assertEqual(resolution.get("status"), "unverified_surface")
                self.assertEqual(resolution["source"], "explicit_synthetic_surface")
                self.assertIsNone(resolution["kato_code"])
                self.assertIsNone(case.oracle["canonical_fields"]["district"])

    def test_kazakh_unknown_street_and_living_area_keep_source_surfaces(self):
        cases = generate_cases(self.config, count=240, split="dev", master_seed="kk-surfaces")
        candidates = [case for case in cases if case.oracle["locale"] == "kk"]

        self.assertTrue(candidates)
        self.assertTrue(
            any(
                case.expected_record["street_name"]
                != case.oracle["canonical_fields"]["street_name"]
                for case in candidates
            )
        )
        living_candidates = [
            case for case in candidates if case.expected_record["living_area"] is not None
        ]
        self.assertTrue(living_candidates)
        self.assertTrue(
            any(
                case.expected_record["living_area"]
                != case.oracle["canonical_fields"]["living_area"]
                for case in living_candidates
            )
        )
        for case in candidates:
            self.assertEqual(
                case.expected_record["street_name"],
                case.oracle["source_surfaces"]["street_name"],
            )
            self.assertIn(case.expected_record["street_name"], case.input_record["raw_address"])
            if case.expected_record["living_area"] is not None:
                self.assertEqual(
                    case.expected_record["living_area"],
                    case.oracle["source_surfaces"]["living_area"],
                )
                self.assertIn(case.expected_record["living_area"], case.input_record["raw_address"])

    def test_duplicate_ids_do_not_change_count_or_position(self):
        cases = generate_cases(self.config, count=500, split="dev", master_seed="duplicates")
        ids = [case.input_record["id"] for case in cases]

        self.assertLess(len(set(ids)), len(ids))
        self.assertEqual(
            [(case.input_record["id"], case.input_record["raw_address"]) for case in cases],
            [(case.expected_record["id"], case.expected_record["raw_address"]) for case in cases],
        )

    def test_bundle_has_exact_public_shape_and_replayable_hashes(self):
        cases = generate_cases(self.config, count=64, split="test", master_seed="bundle")
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "bundle"
            manifest = write_bundle(
                target,
                cases,
                config=self.config,
                master_seed="bundle",
                split="test",
            )
            report = verify_bundle(target)

            self.assertTrue(report["valid"])
            self.assertEqual(manifest["record_count"], 64)
            self.assertRegex(manifest["generator_source_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(manifest["config_sha256"], r"^[0-9a-f]{64}$")
            self.assertFalse(manifest["privacy"].get("organizer_rows_used", True))
            self.assertEqual(manifest["privacy"].get("public_administrative_source"), "kato-nk-rk-11-2025")
            self.assertNotIn("official_rows_used", manifest["privacy"])
            expected = json.loads((target / "expected.json").read_text(encoding="utf-8"))
            self.assertEqual(list(expected[0]), list(PUBLIC_FIELDS))
            manual = json.loads((target / "manual-review.json").read_text(encoding="utf-8"))
            self.assertGreater(len(manual), 0)
            self.assertLessEqual(len(manual), 64)

    def test_boundary_counts_generate_without_special_cases(self):
        for count in (32, 999, 1001):
            with self.subTest(count=count):
                cases = generate_cases(
                    self.config,
                    count=count,
                    split="dev",
                    master_seed=f"boundary-{count}",
                )
                self.assertEqual(len(cases), count)


if __name__ == "__main__":
    unittest.main()
