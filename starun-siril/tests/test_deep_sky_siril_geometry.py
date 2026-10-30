#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.deep_sky_siril_geometry import (
    detect_stacking_borders,
    generate_geometry_assets,
    MAX_SIDE_CROP_RATIO,
    MIN_PRESERVED_AREA,
)
from tests.test_native_processing import write_fits


class TestDeepSkySirilGeometry(unittest.TestCase):

    def test_clean_image_passes_through(self):
        """A full-frame clean image should return None to skip Stage 2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fits_path = Path(tmpdir) / "clean.fit"
            # 200x200 image with flat background 0.05 plus small noise
            np.random.seed(42)
            noise = np.random.normal(0.05, 0.002, (200, 200)).astype(np.float32)
            write_fits(fits_path, noise)

            res = detect_stacking_borders(fits_path)
            self.assertIsNone(res, "Clean image must return None and pass through")

    def test_dither_border_detection_and_quantization(self):
        """An image with near-black borders should be detected, quantized, and have even dimensions."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fits_path = Path(tmpdir) / "dither.fit"
            w, h = 400, 400
            # Base background 0.05
            arr = np.full((h, w), 0.05, dtype=np.float32)
            # Add black border on right (10 columns ~ 2.5% of 400)
            arr[:, -10:] = 0.0001
            # Add black border on left (4 columns ~ 1.0% of 400)
            arr[:, :4] = 0.0001
            write_fits(fits_path, arr)

            res = detect_stacking_borders(fits_path)
            self.assertIsNotNone(res)
            self.assertTrue(res["left_px"] > 0)
            self.assertTrue(res["right_px"] > 0)
            # Step size for 400 is 400 * 0.005 = 2px
            self.assertEqual(res["left_px"] % 2, 0)
            self.assertEqual(res["right_px"] % 2, 0)
            # Output width and height must be even
            self.assertEqual(res["width"] % 2, 0)
            self.assertEqual(res["height"] % 2, 0)
            # Width must be smaller than original
            self.assertLess(res["width"], w)
            self.assertGreaterEqual(res["area_retention"], MIN_PRESERVED_AREA)

    def test_bounds_clamp_to_protocol_limits(self):
        """Even if black border extends beyond 3.5%, crop is capped at 3.5%."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fits_path = Path(tmpdir) / "deep_border.fit"
            w, h = 400, 400
            arr = np.full((h, w), 0.05, dtype=np.float32)
            # Black border extends 30 columns (7.5%)
            arr[:, -30:] = 0.0
            write_fits(fits_path, arr)

            res = detect_stacking_borders(fits_path)
            self.assertIsNotNone(res)
            max_crop_px = int(w * MAX_SIDE_CROP_RATIO)
            # right_px can be max_crop_px + 1 at most for even parity adjustment
            self.assertLessEqual(res["right_px"], max_crop_px + 1)
            self.assertGreaterEqual(res["area_retention"], MIN_PRESERVED_AREA)

    def test_generate_geometry_assets(self):
        """Generated SSF script and provenance must match protocol contract."""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = Path(tmpdir)
            source_fits = session / "input.fit"
            write_fits(source_fits, np.full((100, 100), 0.05, dtype=np.float32))

            crop_box = {
                "x": 2,
                "y": 0,
                "width": 96,
                "height": 100,
                "left_px": 2,
                "right_px": 2,
                "top_px": 0,
                "bottom_px": 0,
                "area_retention": 0.96,
            }

            ssf_file, prov_file = generate_geometry_assets(session, source_fits, crop_box)
            self.assertTrue(ssf_file.exists())
            self.assertTrue(prov_file.exists())

            ssf_text = ssf_file.read_text()
            self.assertIn("crop 2 0 96 100", ssf_text)
            self.assertIn("save \"{}/artifacts/020-crop\" -chksum".format(session), ssf_text)
            self.assertIn("autostretch -linked -2.8 0.22", ssf_text)

            prov_data = json.loads(prov_file.read_text())
            self.assertEqual(prov_data["protocol"], "geometry.crop-near-black")
            self.assertEqual(prov_data["run_id"], "020-crop")
            self.assertEqual(prov_data["schema"], "starun-siril.ssf-provenance.v1")


if __name__ == "__main__":
    unittest.main()
