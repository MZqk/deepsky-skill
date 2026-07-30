import unittest
from pathlib import Path
import sys

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from analyze import _analyze_gradient


class GradientDiagnosticTests(unittest.TestCase):
    def test_channel_gradient_difference_requires_review(self):
        height, width = 120, 160
        _yy, xx = np.mgrid[:height, :width]
        rgb = np.full((height, width, 3), 0.04, dtype=np.float32)
        rgb[..., 0] += 0.025 * xx / width
        rgb[..., 1] += 0.025 * (1.0 - xx / width)
        gray = np.mean(rgb, axis=2)

        report = _analyze_gradient(gray, rgb)

        self.assertIsNotNone(report["channel_gradients"])
        self.assertGreater(report["chromatic_gradient_spread"], 0.08)
        self.assertEqual(report["dbe_decision"], "review_chromatic")

    def test_uniform_rgb_background_can_skip_dbe(self):
        rgb = np.full((120, 160, 3), 0.04, dtype=np.float32)
        report = _analyze_gradient(np.mean(rgb, axis=2), rgb)
        self.assertEqual(report["dbe_decision"], "skip")
        self.assertFalse(report["chromatic_gradient_review"])


if __name__ == "__main__":
    unittest.main()
