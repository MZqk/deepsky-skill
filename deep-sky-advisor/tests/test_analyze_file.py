import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np
from astropy.io import fits


SCRIPT = Path(__file__).parents[1] / "scripts" / "analyze_file.py"
SPEC = importlib.util.spec_from_file_location("advisor_analyze_file", SCRIPT)
analyzer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analyzer)


def synthetic_star_field(height=192, width=256, seed=7):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    image = 0.05 + 0.10 * xx / width + 0.04 * yy / height
    image += rng.normal(0, 0.003, size=image.shape)
    for y, x, sx, sy, amp in (
        (35, 40, 1.4, 1.4, 0.55),
        (60, 120, 1.8, 1.3, 0.65),
        (90, 200, 1.5, 2.0, 0.60),
        (130, 70, 1.7, 1.5, 0.70),
        (155, 160, 2.0, 1.4, 0.62),
        (45, 220, 1.6, 1.6, 0.58),
        (110, 145, 1.9, 1.5, 0.64),
        (165, 225, 1.5, 1.8, 0.60),
    ):
        image += amp * np.exp(-0.5 * (((xx - x) / sx) ** 2 + ((yy - y) / sy) ** 2))
    return image.astype(np.float32)


class AnalyzeFileTests(unittest.TestCase):
    def test_extension_hdu_and_quantitative_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "stacked_light.fits"
            primary = fits.PrimaryHDU()
            image_hdu = fits.ImageHDU(synthetic_star_field())
            image_hdu.header["OBJECT"] = "TEST NEBULA"
            image_hdu.header["FILTER"] = "L"
            image_hdu.header["NCOMBINE"] = 24
            fits.HDUList([primary, image_hdu]).writeto(input_path)

            report = analyzer.analyze_image_file(input_path, root / "out")

            self.assertEqual(report["schema_version"], "2.0")
            self.assertEqual(report["file"]["selected_hdu"], 1)
            self.assertEqual(report["classification"]["processing_stage"], "stacked_or_integrated")
            self.assertGreater(report["background"]["plane"]["x_change_across_frame"], 0.05)
            self.assertGreater(report["noise"]["background_sample_pixels"], 100)
            self.assertEqual(report["stars"]["evidence"], "measured")
            self.assertGreaterEqual(report["stars"]["usable_star_count"], 5)
            self.assertGreater(report["stars"]["fwhm_major_median_px"], 1.0)
            self.assertTrue(Path(report["analysis_json"]).exists())
            saved = Path(report["analysis_json"]).read_text(encoding="utf-8")
            self.assertIn('"analysis_json"', saved)
            for preview in report["previews"].values():
                self.assertTrue(Path(preview).exists())

    def test_rgb_channel_diagnostics_and_channel_preview(self):
        mono = synthetic_star_field()
        rgb = np.stack([mono * 1.2, mono, mono * 0.7], axis=0).astype(np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "rgb.fits"
            fits.PrimaryHDU(rgb).writeto(input_path)
            report = analyzer.analyze_image_file(input_path, root / "out")

            self.assertEqual(report["classification"]["channel_model"], "rgb")
            self.assertEqual(report["color"]["evidence"], "measured")
            self.assertGreater(
                report["color"]["background_ratios_to_mean"]["r"],
                report["color"]["background_ratios_to_mean"]["b"],
            )
            self.assertIn("channels_rgb_order", report["previews"])

    def test_no_stars_degrades_to_unavailable(self):
        yy, xx = np.mgrid[0:128, 0:128]
        image = (0.1 + 0.03 * xx / 128 + 0.01 * yy / 128).astype(np.float32)
        normalized, _ = analyzer._normalize_for_analysis(image)
        noise = analyzer.analyze_noise(normalized)
        stars = analyzer.analyze_stars(normalized, noise)
        self.assertEqual(stars["evidence"], "unavailable")


if __name__ == "__main__":
    unittest.main()
