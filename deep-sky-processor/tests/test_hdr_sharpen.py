import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from enhance import protected_hdr_compress
from sharpen import adaptive_signal_sharpen


class HdrSharpenTests(unittest.TestCase):
    def test_hdr_compresses_highlights_without_darkening_background(self):
        image = np.full((96, 96, 3), [0.08, 0.06, 0.04], dtype=np.float32)
        yy, xx = np.mgrid[:96, :96]
        core = np.exp(-(((xx - 48) / 8) ** 2 + ((yy - 48) / 8) ** 2))
        image += core[..., None] * [0.85, 0.55, 0.35]
        image = np.clip(image, 0, 1)

        result, report = protected_hdr_compress(image, strength=0.6)

        self.assertLess(report["p99_after"], report["p99_before"])
        self.assertAlmostEqual(
            report["median_after"],
            report["median_before"],
            delta=1e-5,
        )
        source_ratio = image[48, 48, 0] / image[48, 48, 1]
        result_ratio = result[48, 48, 0] / result[48, 48, 1]
        self.assertAlmostEqual(float(result_ratio), float(source_ratio), delta=0.02)

    def test_adaptive_sharpen_avoids_flat_background(self):
        rng = np.random.default_rng(42)
        image = np.full((96, 96, 3), 0.04, dtype=np.float32)
        image += rng.normal(0, 0.0005, image.shape).astype(np.float32)
        yy, xx = np.mgrid[:96, :96]
        shell = np.exp(
            -((np.sqrt((xx - 48) ** 2 + (yy - 48) ** 2) - 20) / 2.5) ** 2
        )
        image[..., 0] += shell * 0.2
        image = np.clip(image, 0, 1)

        result, report = adaptive_signal_sharpen(
            image,
            amount=0.8,
            fwhm=4.5,
        )
        background = np.s_[:20, :20]
        bg_change = np.mean(np.abs(result[background] - image[background]))
        shell_change = np.mean(np.abs(result[shell > 0.6] - image[shell > 0.6]))

        self.assertLess(float(bg_change), 0.0002)
        self.assertGreater(float(shell_change), float(bg_change) * 3)
        self.assertAlmostEqual(report["radius"], 1.5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
