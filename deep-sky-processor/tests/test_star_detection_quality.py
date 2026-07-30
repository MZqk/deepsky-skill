import sys
import unittest
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from star_tools import detect_stars_multiscale


class StarDetectionQualityTests(unittest.TestCase):
    def test_saturated_star_does_not_hide_normal_stars(self):
        image = np.full((128, 128), 0.01, dtype=np.float32)
        impulses = np.zeros_like(image)
        for y, x, peak in [
            (20, 20, 1.0),
            (35, 90, 0.35),
            (70, 45, 0.28),
            (95, 100, 0.22),
            (100, 25, 0.18),
        ]:
            impulses[y, x] = peak
        image += gaussian_filter(impulses, sigma=1.4)

        mask, confidence, details = detect_stars_multiscale(
            image,
            fwhm=3.3,
            star_threshold=0.82,
            return_details=True,
        )

        self.assertGreaterEqual(details["n_components_kept"], 3)
        self.assertGreater(confidence, 0.3)
        self.assertGreater(float(mask[35, 90]), 0.5)
        self.assertGreater(float(mask[70, 45]), 0.5)

    def test_elongated_filament_is_rejected(self):
        image = np.full((128, 128), 0.01, dtype=np.float32)
        image[62:66, 20:108] += 0.25
        impulses = np.zeros_like(image)
        impulses[30, 30] = 0.5
        impulses[90, 95] = 0.4
        image += gaussian_filter(impulses, sigma=1.3)

        mask, _confidence, details = detect_stars_multiscale(
            image,
            fwhm=3.0,
            star_threshold=0.78,
            return_details=True,
        )
        self.assertLess(float(np.mean(mask[62:66, 35:90])), 0.1)


if __name__ == "__main__":
    unittest.main()
