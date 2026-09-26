"""target_rules 单一事实源的单元测试。

重点覆盖两类回归风险：
1. 旧版散落在 pipeline.py 里的硬编码名称清单，删除后必须仍然命中；
2. 名称匹配改为精确匹配后，子串误判（如 'M8' in 'M81'）必须消失。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fits_io import (  # noqa: E402
    LOCAL_CELESTIAL_DB,
    TARGET_NAME_SYNONYMS,
    normalize_target_name,
)
from target_rules import (  # noqa: E402
    STAR_DOMINANT_TYPES,
    is_emission_nebula_target,
    is_m42_target,
    is_reflection_nebula_target,
    is_star_poi_target,
    resolve_catalog_id,
    resolve_target_type,
)


# pipeline.py 重构前使用的三张硬编码清单，作为回归护栏逐条保留。
LEGACY_EMISSION_NAMES = [
    "M42", "ORION NEBULA", "NGC7000", "NORTH AMERICA NEBULA",
    "M16", "EAGLE NEBULA", "M8", "LAGOON NEBULA", "NGC2237",
    "ROSETTE NEBULA", "NGC6888", "CRESCENT NEBULA",
]
LEGACY_REFLECTION_NAMES = [
    "M45", "PLEIADES", "NGC7023", "IRIS NEBULA",
    "IC2118", "WITCH HEAD NEBULA",
]
LEGACY_M42_NAMES = ["M42", "ORION NEBULA", "GREAT ORION NEBULA"]


class TargetRulesTests(unittest.TestCase):
    def test_legacy_emission_names_still_match(self):
        for name in LEGACY_EMISSION_NAMES:
            with self.subTest(name=name):
                self.assertTrue(is_emission_nebula_target(None, name))

    def test_legacy_reflection_names_still_match(self):
        for name in LEGACY_REFLECTION_NAMES:
            with self.subTest(name=name):
                self.assertTrue(is_reflection_nebula_target(None, name))

    def test_legacy_m42_names_still_match(self):
        for name in LEGACY_M42_NAMES:
            with self.subTest(name=name):
                self.assertTrue(is_m42_target(name))

    def test_m45_is_star_dominant(self):
        for name in ("M45", "M 45", "PLEIADES", "PLEIADES CLUSTER"):
            with self.subTest(name=name):
                self.assertTrue(is_star_poi_target(None, name))

    def test_m45_keeps_dual_nature(self):
        """M45 既是星点即主体，也保留反射星云调校（target_awareness.md 的 M45 例外）。"""
        self.assertTrue(is_star_poi_target(None, "M45"))
        self.assertTrue(is_reflection_nebula_target(None, "M45"))
        self.assertTrue(is_reflection_nebula_target(None, "PLEIADES"))

    def test_substring_false_positive_is_gone(self):
        """'M8' 曾是 'M81' 的子串，导致星系被当成发射星云。"""
        self.assertFalse(is_emission_nebula_target(None, "M81"))
        self.assertEqual(resolve_target_type(None, "M81"), "galaxy")
        self.assertTrue(is_emission_nebula_target(None, "M8"))

    def test_separator_variants_normalize(self):
        for name in ("M 42", "m-42", "m42"):
            with self.subTest(name=name):
                self.assertEqual(resolve_catalog_id(name), "M42")
        for name in ("NGC 6888", "ngc-6888", "NGC6888"):
            with self.subTest(name=name):
                self.assertEqual(resolve_catalog_id(name), "NGC6888")
        self.assertTrue(is_emission_nebula_target(None, "NGC 6888"))

    def test_explicit_type_takes_precedence(self):
        """显式 target_type 权威，优先于名称查表。"""
        self.assertTrue(is_emission_nebula_target("emission_nebula", "M81"))
        self.assertEqual(resolve_target_type("galaxy", "M42"), "galaxy")

    def test_unknown_target_triggers_nothing(self):
        self.assertEqual(resolve_target_type(None, "NOT A REAL TARGET"), "")
        self.assertFalse(is_emission_nebula_target(None, "NOT A REAL TARGET"))
        self.assertFalse(is_reflection_nebula_target(None, "NOT A REAL TARGET"))
        self.assertFalse(is_m42_target("NOT A REAL TARGET"))
        self.assertFalse(is_star_poi_target(None, "NOT A REAL TARGET"))

    def test_empty_and_none_inputs_are_safe(self):
        self.assertEqual(resolve_catalog_id(None), "")
        self.assertEqual(resolve_catalog_id(""), "")
        self.assertFalse(is_star_poi_target(None, None))
        self.assertFalse(is_m42_target(None))

    def test_cluster_types_are_star_dominant(self):
        for target_type in sorted(STAR_DOMINANT_TYPES):
            with self.subTest(target_type=target_type):
                self.assertTrue(is_star_poi_target(target_type, None))


class SynonymCoverageTests(unittest.TestCase):
    def test_every_db_standard_name_resolves_back(self):
        """LOCAL_CELESTIAL_DB 中每个标准名都应解析回自己的星表编号。"""
        skipped = []
        for catalog_id, (_target_type, standard_name) in LOCAL_CELESTIAL_DB.items():
            if standard_name.upper() in TARGET_NAME_SYNONYMS:
                # 显式表优先，允许覆盖（IC434 与 B33 共用 "Horsehead Nebula"）
                skipped.append(catalog_id)
                continue
            with self.subTest(catalog_id=catalog_id, standard_name=standard_name):
                self.assertEqual(normalize_target_name(standard_name), catalog_id)
        self.assertIn("B33", skipped)

    def test_explicit_synonym_wins_on_conflict(self):
        self.assertEqual(normalize_target_name("Horsehead Nebula"), "IC434")

    def test_derived_synonyms_are_reachable(self):
        """派生表覆盖的俗名（旧清单未收录）也能解析。"""
        for name, expected in (
            ("Rosette Nebula", "NGC2237"),
            ("Witch Head Nebula", "IC2118"),
            ("Heart Nebula", "IC1805"),
            ("Bubble Nebula", "NGC7635"),
            ("Bode's Galaxy", "M81"),
        ):
            with self.subTest(name=name):
                self.assertEqual(normalize_target_name(name), expected)


if __name__ == "__main__":
    unittest.main()
