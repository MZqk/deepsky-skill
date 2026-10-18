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
from fits_io import write_image
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


class ExternalStarlessFwhmTests(unittest.TestCase):
    """external-starless 路径没有 star_report，FWHM 必须实测而非退回 4.0。

    该值同时喂给 detect_stars、apply_star_halo_guard 与 INPAINT_FOOTPRINT
    的采样环带；硬编码在采样不足（真实 FWHM ≲ 3px）的数据上会把蒙版面积
    放大 3 倍（实测同一张图 fwhm 4→8 时 area 0.187→0.566）。
    """

    @staticmethod
    def _starfield(size=96, background=0.02):
        """含可测星点的高斯星场，真实 FWHM 约 3px（低于硬编码的 4.0）。"""
        image = np.full((size, size, 3), background, dtype=np.float32)
        for cy, cx in ((20, 20), (50, 70), (72, 40), (40, 55), (65, 25)):
            yy, xx = np.mgrid[cy - 3:cy + 4, cx - 3:cx + 4]
            patch = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / 4.0)
            image[cy - 3:cy + 4, cx - 3:cx + 4] += patch[..., None] * 0.5
        return image

    def _run(self, td, image, external):
        captured = {}

        def fake_detect_stars(gray, star_threshold=0.85, fwhm=None):
            captured["fwhm"] = fwhm
            return np.zeros(gray.shape, dtype=np.float32)

        source = Path(td) / "source.png"
        imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))
        external_path = Path(td) / "external.tif"
        write_image(external, str(external_path))

        with patch("pipeline.detect_stars", side_effect=fake_detect_stars):
            result = pipeline.run_pipeline(
                str(source), str(Path(td) / "output.tif"),
                steps="star_remove,stretch", preset="light",
                external_starless=str(external_path),
                auto_crop_edges=False, recognize=False,
            )
        return captured, result

    def test_measures_fwhm_instead_of_hardcoding(self):
        image = self._starfield()
        with tempfile.TemporaryDirectory() as td:
            captured, result = self._run(td, image, np.clip(image * 0.6, 0, 1))
        # 硬编码值是 4.0；实测值必须显著不同，否则说明分支没走到
        self.assertIsNotNone(captured.get("fwhm"))
        self.assertNotEqual(captured["fwhm"], 4.0)
        self.assertLess(captured["fwhm"], 4.0)
        self.assertEqual(
            result["star_layers"]["linear_star_metrics"]["fwhm_source"],
            "measured",
        )

    def test_starless_input_is_never_used_as_fwhm_subject(self):
        """测量对象必须含星：无星层上 estimate_fwhm 拟合的是星云纹理。

        同一份数据在无星层上给 9.909（实测），若误用会把尺度推到 3 倍。
        """
        from star_tools import estimate_fwhm

        image = self._starfield()
        starless = np.clip(image * 0.6, 0, 1)
        measured_on_stars = estimate_fwhm(np.mean(image, axis=2))[0]
        measured_on_starless = estimate_fwhm(np.mean(starless, axis=2))[0]
        self.assertLess(measured_on_stars, 4.0)
        self.assertGreater(measured_on_starless, measured_on_stars)

        with tempfile.TemporaryDirectory() as td:
            captured, _ = self._run(td, image, starless)
        # 源图写成 8bit PNG 再读回，FWHM 会有 ~0.5% 的量化偏移，因此按
        # 「量级正确」而非精确相等断言。
        self.assertAlmostEqual(
            captured["fwhm"] / measured_on_stars, 1.0, delta=0.02
        )

    def test_flat_image_falls_back_and_reports_source(self):
        """无可测星点时保留 4.0 兜底，并如实上报来源。"""
        flat = np.full((96, 96, 3), 0.02, dtype=np.float32)
        with tempfile.TemporaryDirectory() as td:
            _, result = self._run(td, flat, np.clip(flat * 0.6, 0, 1))
        metrics = result["star_layers"]["linear_star_metrics"]
        self.assertEqual(metrics["fwhm_source"], "fallback_constant")
        self.assertEqual(metrics["estimated_fwhm"], 4.0)



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
