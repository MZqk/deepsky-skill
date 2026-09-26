"""DBE 输出归一化：黑点、白点与 pedestal。

旧实现（pipeline Phase 1）有两个独立缺陷：
  - 先 `clip(0)` 再减去"正值像素的 p0.3"，等于连续两次抬高黑位，
    把噪声底整个裁掉（实测 28~44% 的画面变成纯 0）。
  - 白点用 p99.7，而星系/星云亮度跨数量级，该分位落在很暗的外缘
    （实测比峰值低 36.6 倍），等于把整幅图放大 ~37 倍 ——
    噪声被放大到与星点可比，星点检测于是把噪声当成星（0.24% → 4.5%）。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gradient_removal import (
    estimate_background_noise,
    normalize_background_subtracted,
    remove_gradient,
)


def _background_subtracted_scene(height=192, width=256, seed=5,
                                 peak=0.83, noise=2e-4):
    """模拟 remove_gradient 的输出：背景以 0 为中心，亮核很小、暗晕很大。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    image = rng.normal(0, noise, (height, width))
    image += peak * np.exp(
        -(((xx - width * 0.5) / 4.0) ** 2 + ((yy - height * 0.5) / 4.0) ** 2)
    )
    image += 0.05 * np.exp(
        -(((xx - width * 0.5) / 60.0) ** 2 + ((yy - height * 0.5) / 40.0) ** 2)
    )
    return image.astype(np.float64)


class BackgroundNoiseTests(unittest.TestCase):
    def test_ignores_clipped_pixels(self):
        image = np.zeros((128, 128), dtype=np.float32)
        rng = np.random.default_rng(2)
        image[40:] = np.abs(rng.normal(0, 0.004, (88, 128))).astype(np.float32)
        self.assertGreater(estimate_background_noise(image), 0.0)

    def test_zero_for_flat_image(self):
        self.assertEqual(
            estimate_background_noise(np.zeros((64, 64), np.float32)), 0.0
        )


class NormalizeBackgroundSubtractedTests(unittest.TestCase):
    def setUp(self):
        self.scene = _background_subtracted_scene()

    def test_scene_really_has_a_high_percentile_far_below_peak(self):
        """前提校验：这个场景必须能复现"p99.7 远低于峰值"的特征。"""
        p997 = float(np.percentile(self.scene, 99.7))
        peak = float(np.max(self.scene))
        self.assertLess(p997 * 5.0, peak)

    def test_white_point_is_anchored_to_the_peak(self):
        out = normalize_background_subtracted(self.scene)

        # 峰值应落在 1.0 附近，而不是被裁掉一大片
        self.assertGreater(float(np.percentile(out, 99.99)), 0.5)
        self.assertLess(float(np.mean(out >= 0.995)), 0.01)

    def test_background_is_not_hard_clipped_to_zero(self):
        out = normalize_background_subtracted(self.scene)
        self.assertLess(float(np.mean(out <= 0)), 0.02)

    def test_background_noise_is_preserved(self):
        """pedestal 的作用就是让背景噪声完整通过，而不是被裁掉一半。"""
        out = normalize_background_subtracted(self.scene)
        sky = out[:40, :40]
        self.assertGreater(float(np.median(sky)), 0.0)
        self.assertGreater(float(sky.std()), 0.0)

    def test_noise_is_not_amplified_relative_to_the_input(self):
        """核心回归：归一化不应把噪声放大到与星点可比。"""
        out = normalize_background_subtracted(self.scene)

        sky_sigma = float(out[:40, :40].std())
        peak = float(np.percentile(out, 99.99))
        # 输入噪声/峰值 ≈ 2e-4/0.83；输出应保持同量级，而不是放大数十倍
        self.assertLess(sky_sigma / max(peak, 1e-9), 0.01)

    def test_black_point_uses_the_background_centre(self):
        """把整幅图抬高一个常数，归一化结果应基本不变（黑点跟随背景）。"""
        base = normalize_background_subtracted(self.scene)
        shifted = normalize_background_subtracted(self.scene + 0.05)
        self.assertAlmostEqual(
            float(np.median(base)), float(np.median(shifted)), places=3
        )

    def test_handles_an_all_zero_input(self):
        out = normalize_background_subtracted(np.zeros((32, 32)))
        self.assertEqual(out.shape, (32, 32))
        self.assertTrue(np.all(out == 0))

    def test_handles_a_flat_nonzero_input(self):
        out = normalize_background_subtracted(np.full((32, 32), 0.5))
        self.assertTrue(np.isfinite(out).all())
        self.assertLessEqual(float(out.max()), 1.0)

    def test_integration_with_remove_gradient(self):
        """端到端：remove_gradient 的 RGB 输出经归一化后背景不被裁死。"""
        rng = np.random.default_rng(9)
        yy, xx = np.mgrid[0:192, 0:256]
        rgb = np.stack([
            0.10 + 0.02 * xx / 256 + rng.normal(0, 2e-4, (192, 256)),
            0.09 + 0.02 * xx / 256 + rng.normal(0, 2e-4, (192, 256)),
            0.08 + 0.02 * xx / 256 + rng.normal(0, 2e-4, (192, 256)),
        ], axis=-1).astype(np.float32)
        rgb[90:100, 120:130] += 0.6

        corrected, _bg = remove_gradient(rgb, method="polynomial", degree=2)
        out = normalize_background_subtracted(corrected)

        self.assertEqual(out.shape, rgb.shape)
        self.assertTrue(np.isfinite(out).all())
        self.assertLess(float(np.mean(out <= 0)), 0.02)


if __name__ == "__main__":
    unittest.main()
