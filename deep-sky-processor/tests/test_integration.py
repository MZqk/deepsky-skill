import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imread, imsave

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gradient_removal import remove_gradient

from pipeline import (apply_crop, detect_black_edge_crop, detect_target_crop,
                      parse_crop, run_pipeline)
from quality_metrics import calculate_metrics
from star_tools import mild_star_reduce_full, separate_stars
from stretch import (apply_luminance_stretch, arcsinh_stretch,
                     emission_stretch, very_dark_stretch)
from color_tools import emission_nebula_calibrate, stabilize_emission_channels
from enhance import local_nebula_enhance, positive_starless_detail_enhance
from style_tools import apply_professional_style, choose_style_profile


def synthetic_nebula(height=96, width=128, seed=20260606):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    gradient = 0.015 + 0.05 * xx / width + 0.025 * yy / height
    cloud = 0.18 * np.exp(-(((xx - width * 0.52) / 25) ** 2 +
                            ((yy - height * 0.48) / 17) ** 2))
    image = gradient + cloud + rng.normal(0, 0.002, (height, width))
    for y, x in [(15, 18), (25, 101), (45, 34), (64, 90), (80, 55)]:
        image[y - 1:y + 2, x - 1:x + 2] += 0.8
    return np.clip(image, 0, 1).astype(np.float32)


class IntegrationTests(unittest.TestCase):
    def test_crop_parser_and_bounds(self):
        image = np.zeros((40, 60, 3), dtype=np.float32)
        crop = parse_crop("10,5,30,20")
        result, bounds = apply_crop(image, crop)
        self.assertEqual(result.shape, (20, 30, 3))
        self.assertEqual(bounds, (10, 5, 30, 20))

    def test_auto_crop_locates_compact_shell(self):
        image = np.zeros((480, 320, 3), dtype=np.float32)
        yy, xx = np.mgrid[:480, :320]
        shell = np.exp(
            -((np.sqrt((xx - 180) ** 2 + (yy - 290) ** 2) - 38) / 5) ** 2
        )
        image[..., 0] = 0.002 + shell * 0.08
        image[..., 1] = 0.002 + shell * 0.02
        image[..., 2] = 0.002 + shell * 0.015

        crop, info = detect_target_crop(image, padding=2.0)
        x, y, width, height = crop

        self.assertLessEqual(x, 180)
        self.assertLessEqual(y, 290)
        self.assertGreaterEqual(x + width, 180)
        self.assertGreaterEqual(y + height, 290)
        self.assertEqual(width, height)
        self.assertAlmostEqual(info["center"][0], 180, delta=12)
        self.assertAlmostEqual(info["center"][1], 290, delta=12)

    def test_auto_edge_crop_preserves_valid_full_frame(self):
        image = np.full((120, 80, 3), 0.003, dtype=np.float32)
        crop, info = detect_black_edge_crop(image)
        self.assertEqual(crop, (0, 0, 80, 120))
        self.assertFalse(info["trimmed"])

    def test_auto_edge_crop_only_trims_black_registration_border(self):
        image = np.full((120, 80, 3), 0.003, dtype=np.float32)
        image[:4] = 0
        image[-3:] = 0
        image[:, :2] = 0
        image[:, -5:] = 0
        crop, info = detect_black_edge_crop(image, min_run=4)
        self.assertEqual(crop, (2, 4, 73, 113))
        self.assertTrue(info["trimmed"])

    def test_emission_processing_preserves_red_signal_and_neutralizes_blackpoint(self):
        image = np.full((64, 64, 3), [0.03, 0.01, 0.008], dtype=np.float32)
        yy, xx = np.mgrid[:64, :64]
        shell = np.exp(-((np.sqrt((xx - 32) ** 2 + (yy - 32) ** 2) - 14) / 2) ** 2)
        image[..., 0] += shell * 0.05
        image[..., 1] += shell * 0.012
        image[..., 2] += shell * 0.008
        image[8:11, 8:11] = [0.7, 0.68, 0.66]

        calibrated = emission_nebula_calibrate(image)
        stretched = emission_stretch(calibrated, gamma=3.0)

        background = np.median(calibrated[:8, :8], axis=(0, 1))
        shell_mask = shell > 0.7
        shell_rgb = np.mean(stretched[shell_mask], axis=0)
        self.assertLess(float(np.max(background)), 0.005)
        self.assertGreater(float(shell_rgb[0]), float(shell_rgb[1]) * 1.5)
        self.assertGreater(float(shell_rgb[0]), float(shell_rgb[2]) * 1.5)

    def test_emission_star_gains_are_reported_as_local_only(self):
        image = np.full((64, 64, 3), [0.01, 0.008, 0.006], dtype=np.float32)
        image[8:20, 8:20] = [0.7, 0.5, 0.3]

        calibrated, report = emission_nebula_calibrate(
            image,
            return_report=True,
        )

        self.assertEqual(report["star_gains_scope"], "star_mask_only")
        self.assertEqual(report["oiii_blue_injection"], 0.0)
        self.assertEqual(calibrated.shape, image.shape)

    def test_emission_channel_recovery_only_lifts_collapsed_signal(self):
        image = np.full((64, 64, 3), [0.01, 0.008, 0.006], dtype=np.float32)
        yy, xx = np.mgrid[:64, :64]
        signal = np.exp(-(((xx - 32) / 12) ** 2 + ((yy - 32) / 10) ** 2))
        image[..., 0] += signal * 0.4
        image[..., 1] += signal * 0.08
        image[..., 2] += signal * 0.0001

        corrected, report = stabilize_emission_channels(
            image,
            collapse_ratio=0.02,
            max_gain=1.35,
        )

        self.assertTrue(report["applied"])
        self.assertAlmostEqual(report["gains"][0], 1.0)
        self.assertAlmostEqual(report["gains"][1], 1.0)
        self.assertGreater(report["gains"][2], 1.0)
        self.assertLessEqual(report["gains"][2], 1.350001)
        self.assertGreater(float(corrected[32, 32, 2]), float(image[32, 32, 2]))

    def test_emission_channel_recovery_does_not_rebalance_valid_red_signal(self):
        image = np.full((64, 64, 3), [0.01, 0.008, 0.006], dtype=np.float32)
        image[16:48, 16:48] += [0.4, 0.04, 0.03]

        corrected, report = stabilize_emission_channels(image)

        self.assertFalse(report["applied"])
        np.testing.assert_allclose(corrected, image)

    def test_emission_stretch_preserves_pixel_channel_ratios(self):
        image = np.full((64, 64, 3), [0.0008, 0.0002, 0.00015], dtype=np.float32)
        yy, xx = np.mgrid[:64, :64]
        signal = np.exp(-(((xx - 32) / 12) ** 2 + ((yy - 32) / 10) ** 2))
        image[..., 0] += signal * 0.012
        image[..., 1] += signal * 0.003
        image[..., 2] += signal * 0.002

        stretched = emission_stretch(
            image,
            shadow_pctl=0.0,
            gamma=0.42,
            target_bg=0.10,
        )

        sample = (32, 32)
        source_ratio = image[sample][0] / image[sample][1]
        output_ratio = stretched[sample][0] / stretched[sample][1]
        self.assertAlmostEqual(float(output_ratio), float(source_ratio), delta=0.03)
        self.assertGreater(float(stretched[sample][1]), 0.01)
        self.assertGreater(float(stretched[sample][2]), 0.005)

    def test_emission_stretch_honors_target_background_and_fills_range(self):
        image = np.full((96, 96, 3), [0.0006, 0.00035, 0.00025], dtype=np.float32)
        yy, xx = np.mgrid[:96, :96]
        signal = np.exp(-(((xx - 48) / 18) ** 2 + ((yy - 48) / 14) ** 2))
        image += signal[..., None] * np.array(
            [0.015, 0.004, 0.003], dtype=np.float32
        )

        low = emission_stretch(image, shadow_pctl=0.0, target_bg=0.05)
        high = emission_stretch(image, shadow_pctl=0.0, target_bg=0.15)
        low_luma = np.sum(low * [0.2126, 0.7152, 0.0722], axis=2)
        high_luma = np.sum(high * [0.2126, 0.7152, 0.0722], axis=2)

        self.assertLess(float(np.median(low_luma)), float(np.median(high_luma)))
        # 强红主导像素在保持通道比例且禁止单通道裁切时，亮度上限
        # 低于中性 RGB；这里验证动态范围得到显著利用，而非强制破坏色相。
        self.assertGreaterEqual(float(np.percentile(low_luma, 99)), 0.40)
        self.assertGreaterEqual(float(np.percentile(high_luma, 99)), 0.40)

    def test_quality_metrics_report_dynamic_range_and_channel_collapse(self):
        image = np.full((48, 48, 3), [0.2, 0.02, 0.00001], dtype=np.float32)
        metrics = calculate_metrics(image)

        self.assertIn("p99", metrics)
        self.assertIn("p99_9", metrics)
        self.assertIn("highlight_clip_ratio", metrics)
        self.assertAlmostEqual(
            metrics["channel_signal_ratios"]["r_over_g"],
            10.0,
            delta=0.01,
        )
        self.assertIn("b", metrics["collapsed_channels"])

    def test_very_dark_stretch_recovers_signal_without_changing_color_ratio(self):
        image = np.full((80, 80, 3), [0.00008, 0.00004, 0.00003], dtype=np.float32)
        yy, xx = np.mgrid[:80, :80]
        signal = np.exp(-(((xx - 40) / 14) ** 2 + ((yy - 40) / 11) ** 2))
        image += signal[..., None] * np.array(
            [0.006, 0.0025, 0.0018],
            dtype=np.float32,
        )

        stretched = very_dark_stretch(image, factor=25, target_bg=0.12)
        sample = (40, 40)
        source_ratio = image[sample][0] / image[sample][1]
        output_ratio = stretched[sample][0] / stretched[sample][1]

        self.assertAlmostEqual(float(output_ratio), float(source_ratio), delta=0.05)
        self.assertGreater(float(np.percentile(stretched, 99)), 0.35)
        self.assertGreater(float(np.median(stretched)), 0.03)

    def test_luminance_dispatch_keeps_very_dark_rgb_path(self):
        image = np.full((32, 32, 3), [0.005, 0.003, 0.002], dtype=np.float32)
        image[8:24, 8:24] += [0.012, 0.005, 0.003]

        direct = very_dark_stretch(image, factor=20, target_bg=0.10)
        dispatched = apply_luminance_stretch(
            image,
            method="very_dark",
            factor=20,
            target_bg=0.10,
        )

        np.testing.assert_allclose(dispatched, direct, atol=1e-7)

    def test_positive_starless_detail_never_darkens_pixels(self):
        original = np.full((64, 64, 3), 0.02, dtype=np.float32)
        yy, xx = np.mgrid[:64, :64]
        shell = np.exp(-((np.sqrt((xx - 32) ** 2 + (yy - 32) ** 2) - 14) / 2) ** 2)
        original[..., 0] += shell * 0.05
        starless = original.copy()
        starless[20:23, 20:23] = 0.0
        current = np.power(np.clip(original / 0.08, 0, 1), 0.45)

        enhanced = positive_starless_detail_enhance(
            current,
            original_linear=original,
            starless_linear=starless,
            strength=0.8,
        )

        self.assertTrue(np.all(enhanced >= current - 1e-7))
        self.assertGreater(float(np.mean(enhanced[shell > 0.7])),
                           float(np.mean(current[shell > 0.7])))

    def test_local_enhance_suppresses_star_amplification(self):
        image = np.full((96, 96, 3), 0.04, dtype=np.float32)
        yy, xx = np.mgrid[:96, :96]
        shell = np.exp(
            -((np.sqrt((xx - 48) ** 2 + (yy - 48) ** 2) - 20) / 3) ** 2
        )
        image[..., 0] += shell * 0.16
        image[46:51, 46:51] = 0.9
        star_mask = np.zeros((96, 96), dtype=np.float32)
        star_mask[44:53, 44:53] = 1.0

        unprotected = local_nebula_enhance(
            image, 48, 48, radius=42, strength=0.5
        )
        protected = local_nebula_enhance(
            image, 48, 48, radius=42, strength=0.5, star_mask=star_mask
        )

        star_delta_unprotected = float(np.mean(unprotected[46:51, 46:51] - image[46:51, 46:51]))
        star_delta_protected = float(np.mean(protected[46:51, 46:51] - image[46:51, 46:51]))
        self.assertLess(abs(star_delta_protected), abs(star_delta_unprotected))
        self.assertGreater(
            float(np.mean(protected[shell > 0.7])),
            float(np.mean(image[shell > 0.7])),
        )

    def test_full_frame_star_reduction_respects_explicit_mask(self):
        image = np.full((64, 64, 3), 0.04, dtype=np.float32)
        image[12:17, 12:17] = 0.9
        image[40:48, 36:52, 0] = 0.55
        star_mask = np.zeros((64, 64), dtype=np.float32)
        star_mask[10:19, 10:19] = 1.0

        reduced = mild_star_reduce_full(
            image, reduction=0.4, color_restore=False, star_mask=star_mask
        )

        self.assertLess(float(np.mean(reduced[12:17, 12:17])),
                        float(np.mean(image[12:17, 12:17])))
        np.testing.assert_allclose(
            reduced[40:48, 36:52],
            image[40:48, 36:52],
            atol=2e-3,
        )

    def test_style_auto_selects_emission_and_changes_image(self):
        gray = synthetic_nebula(64, 80)
        rgb = np.stack([gray * 1.4, gray * 0.45, gray * 0.35], axis=-1)
        rgb = np.clip(rgb, 0, 1)

        profile_name, _, _ = choose_style_profile("emission_nebula", "emission", "auto")
        self.assertEqual(profile_name, "dramatic_nebula")
        styled, selected, _ = apply_professional_style(
            rgb,
            style="auto",
            target_type="emission_nebula",
            color_mode="emission",
            strength=1.0,
        )
        self.assertEqual(selected, "dramatic_nebula")
        self.assertEqual(styled.shape, rgb.shape)
        self.assertGreater(float(np.mean(np.abs(styled - rgb))), 0.001)

    def test_dbe_stretch_and_quality_metrics_are_in_expected_ranges(self):
        image = synthetic_nebula()
        corrected, _background = remove_gradient(image, method="polynomial", degree=2)
        corrected = np.clip(corrected, 0, None)
        corrected /= max(float(corrected.max()), 1e-8)
        corners = [
            corrected[:12, :12].mean(),
            corrected[:12, -12:].mean(),
            corrected[-12:, :12].mean(),
            corrected[-12:, -12:].mean(),
        ]
        self.assertLess(float(max(corners) - min(corners)), 0.08)

        corrected = np.clip(corrected + 0.01, 0, 1)
        stretched = arcsinh_stretch(corrected, factor=12)
        self.assertGreater(float(np.median(stretched)), 0.01)
        self.assertLess(float(np.median(stretched)), 0.35)
        metrics = calculate_metrics(stretched)
        self.assertIn("high_frequency_energy_ratio", metrics)
        self.assertIn("corner_uniformity_ratio", metrics)
        self.assertEqual(len(metrics["corner_means"]), 4)
        self.assertLessEqual(metrics["star_area_ratio"], 0.05)

    def test_override_params_drive_pipeline_output(self):
        gray = synthetic_nebula(48, 64)
        rgb = np.stack([gray, gray * 0.8, gray * 0.7], axis=-1)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            low_bg = Path(td) / "low_bg.tif"
            high_bg = Path(td) / "high_bg.tif"
            imsave(source, np.clip(rgb * 255, 0, 255).astype(np.uint8))

            run_pipeline(
                str(source),
                str(low_bg),
                steps="stretch",
                preset="light",
                override_params={"target_bg": 0.04},
                cleanup=True,
            )
            run_pipeline(
                str(source),
                str(high_bg),
                steps="stretch",
                preset="light",
                override_params={"target_bg": 0.16},
                cleanup=True,
            )

            low = imread(low_bg)
            high = imread(high_bg)
            self.assertLess(float(np.median(low)), float(np.median(high)) * 0.75)

    def test_external_starless_path_leaves_no_known_star_residual(self):
        base = synthetic_nebula()
        starless = base.copy()
        image = base.copy()
        known_star_mask = np.zeros_like(image, dtype=bool)
        for y, x in [(20, 20), (50, 70), (72, 105)]:
            image[y - 1:y + 2, x - 1:x + 2] = 1.0
            known_star_mask[y - 1:y + 2, x - 1:x + 2] = True
        result, stars, _mask = separate_stars(
            image, external_starless=starless
        )
        residual = np.mean(np.abs(result[known_star_mask] - starless[known_star_mask]) > 1e-6)
        self.assertLessEqual(float(residual), 0.05)
        self.assertGreater(float(stars[known_star_mask].mean()), 0.1)



    def test_rgba_pipeline_preserves_alpha(self):
        gray = synthetic_nebula(48, 64)
        rgb = np.stack([gray, gray * 0.8, gray * 0.7], axis=-1)
        alpha = np.linspace(0.2, 1.0, 64, dtype=np.float32)[None, :]
        alpha = np.repeat(alpha, 48, axis=0)
        rgba = np.dstack([rgb, alpha])
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.png"
            imsave(source, np.clip(rgba * 255, 0, 255).astype(np.uint8))
            run_pipeline(
                str(source),
                str(output),
                steps="stretch",
                preset="light",
                cleanup=True,
            )
            actual = imread(output)
            expected_alpha = imread(source)[..., 3]
            self.assertEqual(actual.shape[2], 4)
            np.testing.assert_array_equal(actual[..., 3], expected_alpha)

    def test_synthetic_fits_runs_pipeline(self):
        try:
            from astropy.io import fits
        except ImportError:
            self.skipTest("astropy is not installed")
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "synthetic.fit"
            output = Path(td) / "processed.tif"
            fits.PrimaryHDU(synthetic_nebula()).writeto(source)
            run_pipeline(
                str(source),
                str(output),
                steps="dbe,stretch",
                preset="light",
                cleanup=True,
                tile_size=64,
            )
            result = imread(output)
            self.assertEqual(result.shape[:2], (96, 128))
            self.assertGreater(float(np.max(result)), 0)
            self.assertGreater(float(np.mean(result > 0)), 0.01)


if __name__ == "__main__":
    unittest.main()
