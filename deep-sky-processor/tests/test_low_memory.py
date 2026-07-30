import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imread, imsave


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from pipeline import make_decision_preview, resolve_memory_plan, run_pipeline


class LowMemoryTests(unittest.TestCase):
    def test_large_image_auto_enables_low_memory(self):
        steps, tile_size, report = resolve_memory_plan(
            (2160, 3840, 3),
            [
                "color", "star_remove", "stretch", "star_process",
                "star_combine", "star_reduce",
            ],
        )
        self.assertTrue(report["enabled"])
        self.assertTrue(report["auto_enabled"])
        self.assertEqual(tile_size, 768)
        self.assertNotIn("star_remove", steps)
        self.assertNotIn("star_process", steps)
        self.assertNotIn("star_combine", steps)
        self.assertNotIn("star_reduce", steps)
        self.assertTrue(report["full_resolution_output"])

    def test_external_starless_preserves_star_pipeline(self):
        steps, _tile_size, report = resolve_memory_plan(
            (2160, 3840, 3),
            ["star_remove", "stretch", "star_process", "star_combine"],
            external_starless=True,
        )
        self.assertEqual(report["skipped_steps"], [])
        self.assertIn("star_remove", steps)

    def test_small_image_does_not_auto_enable(self):
        steps, tile_size, report = resolve_memory_plan(
            (1080, 1920, 3),
            ["star_remove", "stretch"],
        )
        self.assertFalse(report["enabled"])
        self.assertIsNone(tile_size)
        self.assertIn("star_remove", steps)

    def test_decision_preview_is_bounded(self):
        image = np.zeros((1080, 2048, 3), dtype=np.float32)
        preview = make_decision_preview(image, max_dimension=1024)
        self.assertEqual(max(preview.shape[:2]), 1024)
        self.assertEqual(preview.dtype, np.float32)

    def test_forced_low_memory_pipeline_reports_and_preserves_size(self):
        image = np.full((48, 64, 3), 0.02, dtype=np.float32)
        image[16:32, 20:44, 0] += 0.1
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))

            result = run_pipeline(
                str(source),
                str(output),
                steps="star_remove,stretch,star_reduce",
                preset="light",
                low_memory=True,
                cleanup=True,
            )

            rendered = imread(output)
            self.assertEqual(rendered.shape[:2], image.shape[:2])
            self.assertTrue(result["memory"]["enabled"])
            self.assertIn("star_remove", result["memory"]["skipped_steps"])
            self.assertIn("star_reduce", result["memory"]["skipped_steps"])
            self.assertEqual(
                result["effective_config"]["steps"],
                ["stretch"],
            )


if __name__ == "__main__":
    unittest.main()
