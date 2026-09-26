"""FWHM must survive the star-removal fallback path.

去星回退时报告里没有 estimated_fwhm，导致 pipeline 退回硬编码的 4.0px，
而实测值约 7.5px。半径差近一半会让噪声峰被当成星点 —— 同一张图上
star_area_ratio 从 0.0014 涨到 0.15。
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
from star_tools import _safe_star_removal_fallback


class SafeFallbackFwhmTests(unittest.TestCase):
    def setUp(self):
        self.image = np.full((32, 32, 3), 0.05, dtype=np.float32)

    def test_keeps_estimated_fwhm_from_report(self):
        starless, stars, mask, report = _safe_star_removal_fallback(
            self.image, "test_reason", {"estimated_fwhm": 7.5}
        )
        self.assertEqual(report["estimated_fwhm"], 7.5)
        self.assertTrue(report["fallback_applied"])
        self.assertEqual(int(np.max(stars)), 0)
        self.assertEqual(starless.shape, self.image.shape)

    def test_recovers_fwhm_from_detection_details(self):
        _s, _st, _m, report = _safe_star_removal_fallback(
            self.image, "test_reason",
            {"detection_details": {"fwhm": 6.0}, "detection_confidence": 0.9},
        )
        self.assertEqual(report["estimated_fwhm"], 6.0)

    def test_tolerates_a_report_without_any_fwhm(self):
        _s, _st, _m, report = _safe_star_removal_fallback(
            self.image, "test_reason", {"detection_confidence": 0.4}
        )
        self.assertIsNone(report.get("estimated_fwhm"))


class PipelineFwhmPropagationTests(unittest.TestCase):
    def test_pipeline_uses_measured_fwhm_not_hardcoded_default(self):
        """去星回退时 pipeline 必须把实测 FWHM 传给 detect_stars。"""
        image = np.full((48, 64, 3), 0.03, dtype=np.float32)
        image[18:31, 24:41, 0] += 0.12
        report = {
            "fallback_applied": True,
            "fallback_reason": "star_removal_quality_below_threshold",
            "repair_quality_score": 0.2,
            "estimated_fwhm": 7.5,
        }
        captured = {}

        def fake_detect_stars(gray, star_threshold=0.85, fwhm=None):
            captured["fwhm"] = fwhm
            return np.zeros(gray.shape, dtype=np.float32)

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))

            with (
                patch(
                    "pipeline.separate_stars",
                    return_value=(
                        image,
                        np.zeros_like(image),
                        np.zeros(image.shape[:2], dtype=np.float32),
                        report,
                    ),
                ),
                patch("pipeline.detect_stars", side_effect=fake_detect_stars),
            ):
                result = pipeline.run_pipeline(
                    str(source),
                    str(output),
                    steps="star_remove,stretch",
                    preset="light",
                    cleanup=True,
                )

        self.assertEqual(captured.get("fwhm"), 7.5)
        self.assertEqual(
            result["metrics"].get("linear_estimated_fwhm_px"), 7.5
        )


class SeparateStarsReportTests(unittest.TestCase):
    """直接打真实的 separate_stars —— 上面那组只测了辅助函数，会漏掉源头没赋值的情况。"""

    def _image(self):
        image = np.full((64, 64, 3), 0.03, dtype=np.float32)
        image[20:23, 20:23] = 0.8
        image[40:43, 44:47] = 0.9
        return image

    def test_quality_fallback_reports_measured_fwhm(self):
        import star_tools

        image = self._image()
        details = {"fwhm": 6.4, "n_components_total": 2, "n_components_kept": 2}
        mask = np.zeros(image.shape[:2], dtype=np.float32)
        mask[20:23, 20:23] = 1.0
        mask[40:43, 44:47] = 1.0
        poor = {
            "repair_quality_score": 0.1,
            "needs_starnet_plus": True,
            "quality": "poor",
        }

        with (
            patch(
                "star_tools.detect_stars_multiscale",
                return_value=(mask, 0.9, details),
            ),
            patch("star_tools.estimate_star_removal_quality", return_value=dict(poor)),
        ):
            _s, _st, _m, report = star_tools.separate_stars(
                image, method="inpaint", return_report=True
            )

        self.assertTrue(report.get("fallback_applied"))
        self.assertEqual(report.get("estimated_fwhm"), 6.4)

    def test_success_path_reports_measured_fwhm(self):
        import star_tools

        image = self._image()
        details = {"fwhm": 5.2, "n_components_total": 2, "n_components_kept": 2}
        mask = np.zeros(image.shape[:2], dtype=np.float32)
        mask[20:23, 20:23] = 1.0
        mask[40:43, 44:47] = 1.0
        good = {
            "repair_quality_score": 0.9,
            "needs_starnet_plus": False,
            "quality": "good",
        }

        with (
            patch(
                "star_tools.detect_stars_multiscale",
                return_value=(mask, 0.9, details),
            ),
            patch("star_tools.estimate_star_removal_quality", return_value=dict(good)),
        ):
            _s, _st, _m, report = star_tools.separate_stars(
                image, method="inpaint", return_report=True
            )

        self.assertEqual(report.get("estimated_fwhm"), 5.2)


if __name__ == "__main__":
    unittest.main()
