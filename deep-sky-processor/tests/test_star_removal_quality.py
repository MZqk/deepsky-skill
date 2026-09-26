"""去星质量评估 —— 三个子指标都必须是有绝对锚点的测量值。

旧实现三处退化（全部表现为"恒定值"而非测量值）：
  - nebula_damage_ratio 除以最暗 30% 像素的均值，而该值等于噪声底（实测 ~1e-4），
    任何微小扰动都报 ≈1.0；且掩膜按原始亮度取，把"星点被去掉"本身算成误伤。
  - residual_star_fraction 用 `max(阈值, 0.1)` 绝对下限，星系本体被当成残留星。
  - high_grad_ratio 判据 `梯度 > p95(梯度)*0.5` 用图像自身分布定阈值，
    对任何图都必然命中 10~20%，等于恒定扣满该项。
"""

import unittest
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from star_tools import _robust_noise_scale, estimate_star_removal_quality


def _scene(with_stars=True, size=256, seed=3, background=0.01, noise=0.002):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    image = np.full((size, size), background, dtype=np.float32)
    image += rng.normal(0, noise, (size, size)).astype(np.float32)
    image += 0.35 * np.exp(
        -(((xx - size * 0.5) / 55.0) ** 2 + ((yy - size * 0.47) / 38.0) ** 2)
    ).astype(np.float32)
    if with_stars:
        for cy, cx in rng.integers(10, size - 10, (60, 2)):
            image += 0.7 * np.exp(
                -((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.5 ** 2)
            ).astype(np.float32)
    return np.clip(image, 0, 1).astype(np.float32)


class RobustNoiseScaleTests(unittest.TestCase):
    def test_ignores_clipped_background(self):
        """DBE 之后大片背景为 0；直接取 MAD 会退化成 0，必须排除零值像素。"""
        rng = np.random.default_rng(11)
        image = np.zeros((128, 128), dtype=np.float32)
        image[:40] = rng.normal(0, 0.003, (40, 128)).astype(np.float32)
        image[40:80] = 0.0
        image[80:] = np.abs(rng.normal(0, 0.003, (48, 128))).astype(np.float32)

        self.assertGreater(_robust_noise_scale(image), 0.0)

    def test_returns_zero_for_a_flat_image(self):
        self.assertEqual(_robust_noise_scale(np.zeros((64, 64), dtype=np.float32)), 0.0)


class StarRemovalQualityTests(unittest.TestCase):
    def setUp(self):
        self.original = _scene(with_stars=True)
        self.perfect = _scene(with_stars=False)

    def test_perfect_removal_scores_high_and_is_accepted(self):
        report = estimate_star_removal_quality(self.original, self.perfect)

        self.assertGreater(report["repair_quality_score"], 0.9)
        self.assertEqual(report["quality"], "good")
        self.assertFalse(report["needs_starnet_plus"])

    def test_identity_reports_residual_stars(self):
        """完全没去星时，星点仍在 —— residual 必须能识别出来。"""
        report = estimate_star_removal_quality(self.original, self.original.copy())

        self.assertGreater(report["residual_star_fraction"], 0.02)
        self.assertTrue(report["needs_starnet_plus"])

    def test_dimming_the_nebula_is_flagged_as_damage(self):
        dimmed = (self.original * 0.4).astype(np.float32)
        report = estimate_star_removal_quality(self.original, dimmed)

        self.assertGreater(report["nebula_damage_ratio"], 0.15)
        self.assertTrue(report["needs_starnet_plus"])
        self.assertLess(report["repair_quality_score"], 0.4)

    def test_over_smoothing_is_flagged(self):
        blurred = gaussian_filter(self.original, 3.0).astype(np.float32)
        report = estimate_star_removal_quality(self.original, blurred)

        self.assertGreater(report["high_gradient_ratio"], 0.02)
        self.assertLess(report["repair_quality_score"], 0.6)

    def test_all_black_output_is_flagged(self):
        report = estimate_star_removal_quality(
            self.original, np.zeros_like(self.original)
        )

        self.assertGreater(report["nebula_damage_ratio"], 0.5)
        self.assertTrue(report["needs_starnet_plus"])

    def test_damage_does_not_explode_on_a_near_zero_background(self):
        """核心回归：背景被 clip 到接近 0 时，完美去星仍应报 damage≈0。

        旧实现除以最暗 30% 像素的均值（≈噪声底），此场景下会报 ≈1.0。
        """
        clipped = self.original.copy()
        clipped[clipped < 0.02] = 0.0
        clipped_perfect = self.perfect.copy()
        clipped_perfect[clipped_perfect < 0.02] = 0.0

        zero_ratio = float(np.mean(clipped <= 0))
        self.assertGreater(zero_ratio, 0.1)  # 前提：这个场景确实有近零背景
        dark = clipped[clipped <= np.percentile(clipped, 30)]
        self.assertLess(float(np.mean(dark)), 1e-2)

        report = estimate_star_removal_quality(clipped, clipped_perfect)

        self.assertLess(report["nebula_damage_ratio"], 0.15)
        self.assertGreater(report["repair_quality_score"], 0.9)

    def test_star_removal_alone_is_not_counted_as_damage(self):
        """把星点去掉不应被判成"误伤星云" —— 误伤掩膜必须排除星点本身。"""
        report = estimate_star_removal_quality(self.original, self.perfect)
        self.assertLess(report["nebula_damage_ratio"], 0.15)

    def test_high_grad_ratio_is_not_a_constant(self):
        """旧判据对任何图都命中 10~20%，是常数；新判据应能区分好坏。"""
        good = estimate_star_removal_quality(self.original, self.perfect)
        bad = estimate_star_removal_quality(
            self.original, gaussian_filter(self.original, 3.0).astype(np.float32)
        )

        self.assertLess(good["high_gradient_ratio"], 0.02)
        self.assertGreater(bad["high_gradient_ratio"], good["high_gradient_ratio"] * 10)

    def test_report_shape_is_stable(self):
        report = estimate_star_removal_quality(self.original, self.perfect)
        for key in (
            "residual_star_fraction",
            "nebula_damage_ratio",
            "high_gradient_ratio",
            "repair_quality_score",
            "needs_starnet_plus",
            "quality",
        ):
            self.assertIn(key, report)
        self.assertGreaterEqual(report["repair_quality_score"], 0.0)
        self.assertLessEqual(report["repair_quality_score"], 1.0)

    def test_accepts_gray_input(self):
        report = estimate_star_removal_quality(
            self.original, self.perfect
        )
        gray_report = estimate_star_removal_quality(
            self.original[..., None].repeat(3, axis=2),
            self.perfect[..., None].repeat(3, axis=2),
        )
        self.assertAlmostEqual(
            report["repair_quality_score"], gray_report["repair_quality_score"], places=2
        )


if __name__ == "__main__":
    unittest.main()
