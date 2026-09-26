"""starless_profiles 目标判定测试。

核心回归点：星点即主体的目标（M45、球状/疏散星团）即使被标错 target_type，
也必须被拒绝进入无星层流程。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import starless_profiles  # noqa: E402


class StarlessTargetValidationTests(unittest.TestCase):
    def test_m45_rejected_even_when_type_is_wrong(self):
        with self.assertRaises(starless_profiles.RejectedStarlessTarget):
            starless_profiles.validate_starless_target("reflection_nebula", "M 45")

    def test_cluster_rejected_by_type(self):
        with self.assertRaises(starless_profiles.RejectedStarlessTarget):
            starless_profiles.validate_starless_target("globular_cluster", "M13")

    def test_emission_target_accepted(self):
        self.assertEqual(
            starless_profiles.validate_starless_target("emission_nebula", "M 42"),
            "emission_nebula",
        )

    def test_missing_type_still_raises_value_error(self):
        with self.assertRaises(ValueError):
            starless_profiles.validate_starless_target("")

    def test_get_target_profile_signature_still_works(self):
        profile = starless_profiles.get_target_profile("emission_nebula")
        self.assertEqual(profile.name, "emission_nebula")


if __name__ == "__main__":
    unittest.main()
