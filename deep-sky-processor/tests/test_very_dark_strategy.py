import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze import _analyze_brightness, _analyze_color, _generate_recommendations
from pipeline import build_config_from_analysis


class VeryDarkStrategyTests(unittest.TestCase):
    def test_joint_brightness_detection_rejects_bright_tail(self):
        gray = np.full((100, 100), 0.0005, dtype=np.float32)
        gray[:20, :] = 0.2
        result = _analyze_brightness(gray, gray)

        self.assertFalse(result["very_dark_eligible"])
        self.assertNotEqual(result["brightness_class"], "very_dark")

    def test_very_dark_recommendation_contains_method_gamma_and_background(self):
        gray = np.full((100, 100), 0.0004, dtype=np.float32)
        gray[40:60, 40:60] = 0.008
        brightness = _analyze_brightness(gray, gray)
        report = {
            "brightness": brightness,
            "noise": {"noise_level": "low"},
            "gradient": {
                "gradient_pattern": "none",
                "gradient_severity": "none",
                "dbe_method_recommendation": "polynomial",
                "has_vignetting": False,
            },
            "color": None,
            "starfield": {"star_density": "sparse"},
            "sharpness": {"sharpness_level": "moderate"},
            "is_linear": True,
        }

        recommendations = _generate_recommendations(report)
        stretch = recommendations["stretch"]
        self.assertEqual(stretch["method"], "very_dark")
        self.assertEqual(stretch["target_bg"], 0.12)
        self.assertIn("gamma", stretch)

        config = build_config_from_analysis(
            {"recommendations": recommendations, "starfield": report["starfield"]}
        )
        self.assertEqual(config["stretch_method"], "very_dark")
        self.assertEqual(config["target_bg"], 0.12)

    def test_color_diagnostics_include_signal_ratios(self):
        image = np.full((32, 32, 3), [0.001, 0.001, 0.001], dtype=np.float32)
        image[8:24, 8:24] += [0.03, 0.01, 0.005]
        result = _analyze_color(image)

        self.assertIn("channel_signal_ratios", result)
        self.assertGreater(result["channel_signal_ratios"]["r_over_g"], 2.0)
        self.assertGreater(result["channel_signal_ratios"]["r_over_b"], 4.0)


if __name__ == "__main__":
    unittest.main()
