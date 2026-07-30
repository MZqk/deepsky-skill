#!/usr/bin/env python3
"""
Tests for enhanced smart style selector.
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from style_tools import (
    STYLE_PROFILES,
    choose_style_profile,
    _select_base_profile,
    _adapt_profile_by_diagnostics,
    _apply_user_prefs,
)


class TestBaseProfileMapping(unittest.TestCase):
    """Test target_type -> base profile mapping."""

    def test_emission_nebula(self):
        r = []
        self.assertEqual(_select_base_profile("emission_nebula", "standard", r), "dramatic_nebula")
        self.assertIn("dramatic_nebula", r[0])

    def test_planetary_nebula(self):
        r = []
        self.assertEqual(_select_base_profile("planetary_nebula", "standard", r), "planetary_detail")

    def test_supernova_remnant(self):
        r = []
        self.assertEqual(_select_base_profile("supernova_remnant", "standard", r), "supernova_remnant")

    def test_galaxy(self):
        r = []
        self.assertEqual(_select_base_profile("galaxy", "standard", r), "galaxy_core")

    def test_globular_cluster(self):
        r = []
        self.assertEqual(_select_base_profile("globular_cluster", "standard", r), "star_cluster")

    def test_open_cluster(self):
        r = []
        self.assertEqual(_select_base_profile("open_cluster", "standard", r), "star_cluster")

    def test_reflection_nebula(self):
        r = []
        self.assertEqual(_select_base_profile("reflection_nebula", "standard", r), "soft_dust")

    def test_dark_nebula(self):
        r = []
        self.assertEqual(_select_base_profile("dark_nebula", "standard", r), "soft_dust")

    def test_wide_field(self):
        r = []
        self.assertEqual(_select_base_profile("wide_field", "standard", r), "widefield_punch")

    def test_comet(self):
        r = []
        self.assertEqual(_select_base_profile("comet", "standard", r), "natural")

    def test_unknown(self):
        r = []
        self.assertEqual(_select_base_profile("unknown_target", "standard", r), "deep_clean")

    def test_color_mode_emission(self):
        r = []
        self.assertEqual(_select_base_profile(None, "emission", r), "dramatic_nebula")

    def test_color_mode_narrowband(self):
        r = []
        self.assertEqual(_select_base_profile(None, "narrowband", r), "dramatic_nebula")


class TestDiagnosticAdaptation(unittest.TestCase):
    """Test diagnostic-driven parameter adaptation."""

    def test_extreme_dark_adaptation(self):
        diag = {
            "brightness": {
                "darkness_level": "extreme_dark",
                "is_practically_black": True,
                "dynamic_range_ratio": 15.0,
            },
            "noise": {"noise_level": "moderate"},
            "color": {"color_health": "good"},
            "gradient": {"gradient_pattern": "none"},
            "sharpness": {"sharpness_level": "moderate"},
        }
        adapted, reasons = _adapt_profile_by_diagnostics("dramatic_nebula", diag)
        self.assertIsNotNone(adapted)
        # black_floor 应提高
        self.assertGreater(adapted["black_floor"], STYLE_PROFILES["dramatic_nebula"]["black_floor"])
        # contrast 应降低
        self.assertLess(adapted["contrast"], STYLE_PROFILES["dramatic_nebula"]["contrast"])
        # micro_contrast 应降低
        self.assertLess(adapted["micro_contrast"], STYLE_PROFILES["dramatic_nebula"]["micro_contrast"])
        self.assertTrue(any("extreme_dark" in r for r in reasons))

    def test_high_noise_adaptation(self):
        diag = {
            "brightness": {"darkness_level": "moderate", "dynamic_range_ratio": 15.0},
            "noise": {"noise_level": "very_high"},
            "color": {"color_health": "good"},
            "gradient": {"gradient_pattern": "none"},
            "sharpness": {"sharpness_level": "moderate"},
        }
        adapted, reasons = _adapt_profile_by_diagnostics("dramatic_nebula", diag)
        self.assertIsNotNone(adapted)
        self.assertLess(adapted["micro_contrast"], STYLE_PROFILES["dramatic_nebula"]["micro_contrast"])
        self.assertGreater(adapted["background_desat"], STYLE_PROFILES["dramatic_nebula"]["background_desat"])
        self.assertTrue(any("very_high" in r for r in reasons))

    def test_high_dynamic_range_adaptation(self):
        diag = {
            "brightness": {"darkness_level": "moderate", "dynamic_range_ratio": 80.0},
            "noise": {"noise_level": "low"},
            "color": {"color_health": "good"},
            "gradient": {"gradient_pattern": "none"},
            "sharpness": {"sharpness_level": "moderate"},
        }
        adapted, reasons = _adapt_profile_by_diagnostics("dramatic_nebula", diag)
        self.assertIsNotNone(adapted)
        self.assertGreater(adapted["highlight_rolloff"], STYLE_PROFILES["dramatic_nebula"]["highlight_rolloff"])
        self.assertLess(adapted["contrast"], STYLE_PROFILES["dramatic_nebula"]["contrast"])
        self.assertTrue(any("80" in r for r in reasons))

    def test_poor_color_adaptation(self):
        diag = {
            "brightness": {"darkness_level": "moderate", "dynamic_range_ratio": 15.0},
            "noise": {"noise_level": "moderate"},
            "color": {"color_health": "poor"},
            "gradient": {"gradient_pattern": "none"},
            "sharpness": {"sharpness_level": "moderate"},
        }
        adapted, reasons = _adapt_profile_by_diagnostics("dramatic_nebula", diag)
        self.assertIsNotNone(adapted)
        self.assertLess(adapted["saturation"], STYLE_PROFILES["dramatic_nebula"]["saturation"])
        self.assertLess(adapted["color_separation"], STYLE_PROFILES["dramatic_nebula"]["color_separation"])

    def test_no_adaptation_needed(self):
        diag = {
            "brightness": {"darkness_level": "moderate", "dynamic_range_ratio": 15.0},
            "noise": {"noise_level": "moderate"},
            "color": {"color_health": "good"},
            "gradient": {"gradient_pattern": "none"},
            "sharpness": {"sharpness_level": "moderate"},
        }
        adapted, reasons = _adapt_profile_by_diagnostics("dramatic_nebula", diag)
        self.assertIsNone(adapted)
        self.assertTrue(any("正常范围" in r for r in reasons))


class TestUserPrefs(unittest.TestCase):
    """Test user preference application."""

    def test_prefer_natural(self):
        base = dict(STYLE_PROFILES["dramatic_nebula"])
        adapted, reasons = _apply_user_prefs(base, {"prefer_natural": True})
        self.assertLess(adapted["saturation"], base["saturation"])
        self.assertLess(adapted["contrast"], base["contrast"])
        self.assertTrue(any("自然" in r for r in reasons))

    def test_max_saturation_cap(self):
        base = dict(STYLE_PROFILES["dramatic_nebula"])
        adapted, reasons = _apply_user_prefs(base, {"max_saturation": 1.15})
        self.assertEqual(adapted["saturation"], 1.15)
        self.assertTrue(any("上限" in r for r in reasons))

    def test_deep_black(self):
        base = dict(STYLE_PROFILES["soft_dust"])
        adapted, reasons = _apply_user_prefs(base, {"deep_black": True})
        self.assertGreater(adapted["black_floor"], base["black_floor"])
        self.assertGreater(adapted["background_desat"], base["background_desat"])


class TestChooseStyleProfileIntegration(unittest.TestCase):
    """Integration tests for the full choose_style_profile function."""

    def test_user_forced(self):
        name, adapted, reasons = choose_style_profile(
            target_type="galaxy",
            user_style="natural",
        )
        self.assertEqual(name, "natural")
        self.assertIsNone(adapted)
        self.assertIn("用户强制", reasons[0])

    def test_full_auto_with_diagnostics(self):
        diag = {
            "brightness": {
                "darkness_level": "extreme_dark",
                "is_practically_black": True,
                "dynamic_range_ratio": 15.0,
            },
            "noise": {"noise_level": "high"},
            "color": {"color_health": "poor"},
            "gradient": {"gradient_pattern": "strong_vignette"},
            "sharpness": {"sharpness_level": "very_low"},
        }
        name, adapted, reasons = choose_style_profile(
            target_type="emission_nebula",
            color_mode="standard",
            diagnostic_report=diag,
            user_prefs={"prefer_natural": True, "max_saturation": 1.1},
        )
        self.assertEqual(name, "dramatic_nebula")
        self.assertIsNotNone(adapted)
        # 应有多条决策理由
        self.assertGreater(len(reasons), 3)
        # 参数应被多方因素调整
        self.assertLess(adapted["saturation"], STYLE_PROFILES["dramatic_nebula"]["saturation"])
        self.assertLess(adapted["micro_contrast"], STYLE_PROFILES["dramatic_nebula"]["micro_contrast"])
        self.assertGreater(adapted["black_floor"], STYLE_PROFILES["dramatic_nebula"]["black_floor"])

    def test_planetary_with_diagnostics(self):
        diag = {
            "brightness": {"darkness_level": "moderate", "dynamic_range_ratio": 25.0},
            "noise": {"noise_level": "low"},
            "color": {"color_health": "excellent"},
            "gradient": {"gradient_pattern": "none"},
            "sharpness": {"sharpness_level": "moderate"},
        }
        name, adapted, reasons = choose_style_profile(
            target_type="planetary_nebula",
            diagnostic_report=diag,
        )
        self.assertEqual(name, "planetary_detail")
        # 极佳色彩 → 提高饱和度
        self.assertIsNotNone(adapted)
        self.assertGreater(adapted["saturation"], STYLE_PROFILES["planetary_detail"]["saturation"])

    def test_backwards_compatible(self):
        """Old API should still work."""
        name, adapted, reasons = choose_style_profile(
            target_type="galaxy",
            color_mode="standard",
            user_style="auto",
        )
        self.assertEqual(name, "galaxy_core")
        # adapted could be None if no diagnostics


if __name__ == "__main__":
    unittest.main(verbosity=2)
