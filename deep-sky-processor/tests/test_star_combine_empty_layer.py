"""An all-zero star layer must be reported, not silently "combined".

去星失败回退时管线会写出全零的 04_stars_linear.tif。该层仍能通过
validate_stars_layer（background=0、nonzero=0），于是 combine_starless_stars
变成静默空操作 —— 日志和 manifest 里看不出任何异常。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from skimage.io import imsave

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pipeline
from stellar_recompose import is_stars_layer_empty


class StarsLayerEmptyHelperTests(unittest.TestCase):
    def test_all_zero_layer_is_empty(self):
        self.assertTrue(is_stars_layer_empty(np.zeros((16, 16, 3), dtype=np.float32)))

    def test_layer_with_signal_is_not_empty(self):
        layer = np.zeros((16, 16, 3), dtype=np.float32)
        layer[8, 8] = 0.5
        self.assertFalse(is_stars_layer_empty(layer))

    def test_gray_layer_is_supported(self):
        self.assertTrue(is_stars_layer_empty(np.zeros((16, 16), dtype=np.float32)))


class StarCombineSkipTests(unittest.TestCase):
    def _run(self, stars):
        image = np.full((48, 64, 3), 0.03, dtype=np.float32)
        image[18:31, 24:41, 0] += 0.12
        report = {
            "fallback_applied": True,
            "fallback_reason": "star_removal_quality_below_threshold",
            "repair_quality_score": 0.2,
            "estimated_fwhm": 4.0,
        }
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            work_dir = Path(td) / "work"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))

            with (
                patch(
                    "pipeline.separate_stars",
                    return_value=(
                        image,
                        stars,
                        np.zeros(image.shape[:2], dtype=np.float32),
                        report,
                    ),
                ),
                patch(
                    "pipeline.detect_stars",
                    return_value=np.zeros(image.shape[:2], dtype=np.float32),
                ),
            ):
                return pipeline.run_pipeline(
                    str(source),
                    str(output),
                    steps="star_remove,stretch,star_process,star_combine",
                    preset="light",
                    keep_all=True,
                    work_dir=str(work_dir),
                )

    def test_empty_star_layer_skips_combine_and_records_reason(self):
        result = self._run(np.zeros((48, 64, 3), dtype=np.float32))

        self.assertNotIn("09_star_combined.tif", result["artifacts"])
        self.assertEqual(
            result["star_combine_skipped"]["reason"], "empty_stars_layer"
        )
        self.assertIn(
            "STAR_COMBINE_SKIPPED",
            {warning["code"] for warning in result["warnings"]},
        )

    def test_non_empty_star_layer_still_combines(self):
        stars = np.zeros((48, 64, 3), dtype=np.float32)
        stars[10:13, 10:13] = 0.8
        stars[34:37, 50:53] = 0.9

        result = self._run(stars)

        self.assertIn("09_star_combined.tif", result["artifacts"])
        self.assertIsNone(result["star_combine_skipped"])


if __name__ == "__main__":
    unittest.main()
