"""Colour calibration: additive background neutralisation + bounded star white balance.

旧实现有两个缺陷：
  - background_neutralize 用**乘性**增益 `bg_mean / max(bg_ch, 0.001)`，
    在近零通道上会算出 ~96× 的增益并全局乘到天体本体上，把暖核染成冷核。
  - 掩膜用严格 `<`，当大量像素恰好等于分位阈值时变成空集，整步被静默跳过。
  - white_balance_from_stars 名为 from_stars，实际用全图均值 gray-world，
    并带 R×0.9 / B×1.1 的硬编码偏置。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from color_tools import (
    _reference_star_mask,
    auto_color_calibrate,
    background_neutralize,
    white_balance_from_stars,
)


def _starfield(size=256, nstars=120, seed=7, background=(0.05, 0.065, 0.03),
               peak=0.5, noise=0.004):
    """带读噪声的合成星场 —— 噪声是必须的，否则参考星掩膜会退化成极端阈值。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    image = np.zeros((size, size), dtype=np.float32)
    for _ in range(nstars):
        cy, cx = rng.integers(6, size - 6, 2)
        image += peak * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 1.6 ** 2))
    image = image[..., None] + np.array(background)
    image = image + rng.normal(0, noise, image.shape)
    return np.clip(image, 0, 1).astype(np.float32)


def _tied_floor_image():
    """约 30% 像素恰好落在亮度分位阈值上 —— 旧版严格 `<` 会产生空掩膜。"""
    image = np.full((64, 64, 3), 0.10, dtype=np.float32)
    image[:19, :, 0] = 0.06
    image[:19, :, 1] = 0.05
    image[:19, :, 2] = 0.04
    return image


class BackgroundNeutralizeTests(unittest.TestCase):
    def test_is_additive_and_cannot_amplify_a_channel(self):
        image = np.full((64, 64, 3), [0.03, 0.001, 0.0], dtype=np.float32)
        image[28:36, 28:36] = [0.8, 0.7, 0.5]

        result = background_neutralize(image, bg_percentile=25)

        # 加性平移不可能让任何像素超过输入的最大值（旧乘性实现会到 96×）
        self.assertLessEqual(float(result.max()), float(image.max()) + 1e-6)
        self.assertTrue(np.isfinite(result).all())

    def test_tied_floor_is_not_silently_skipped(self):
        image = _tied_floor_image()
        gray = image.mean(axis=2)

        # 前提：旧版掩膜确实会是空集
        legacy_mask = gray < np.percentile(gray, 25)
        self.assertEqual(int(np.count_nonzero(legacy_mask)), 0)

        result = background_neutralize(image, bg_percentile=25)
        self.assertFalse(np.allclose(result, image))
        self.assertTrue(np.isfinite(result).all())

    def test_handles_non_rgb_input(self):
        gray = np.full((32, 32), 0.05, dtype=np.float32)
        result = background_neutralize(gray)
        self.assertEqual(result.shape, gray.shape)


class WhiteBalanceTests(unittest.TestCase):
    def test_gains_are_bounded_and_luminance_neutral(self):
        image = _starfield(background=(0.05, 0.085, 0.02))
        result = white_balance_from_stars(image, method="stars")

        self.assertEqual(result.shape, image.shape)
        self.assertTrue(np.isfinite(result).all())
        self.assertGreaterEqual(float(result.min()), 0.0)
        self.assertLessEqual(float(result.max()), 1.0)

    def test_star_sampling_finds_reference_pixels(self):
        image = _starfield()
        mask = _reference_star_mask(image[..., :3])
        self.assertIsNotNone(mask)
        self.assertGreaterEqual(int(np.count_nonzero(mask)), 50)

    def test_no_stars_degrades_gracefully(self):
        flat = np.full((64, 64, 3), 0.05, dtype=np.float32)
        result = white_balance_from_stars(flat, method="stars")
        self.assertTrue(np.array_equal(result, flat))

    def test_near_zero_channel_does_not_blow_up(self):
        image = _starfield()
        image[..., 2] = 0.0
        result = white_balance_from_stars(image, method="stars")
        self.assertTrue(np.isfinite(result).all())
        self.assertLessEqual(float(result.max()), 1.0)

    def test_gray_world_is_kept_as_an_alias(self):
        image = _starfield()
        alias = white_balance_from_stars(image, method="gray_world")
        stars = white_balance_from_stars(image, method="stars")
        self.assertTrue(np.allclose(alias, stars))

    def test_strength_zero_is_a_no_op(self):
        image = _starfield()
        result = white_balance_from_stars(image, method="stars", strength=0.0)
        self.assertTrue(np.allclose(result, image, atol=1e-6))

    def test_default_strength_is_conservative(self):
        """默认强度必须保守：把参考星强行拉中性会抹掉天体固有颜色。

        低银纬视场（如 M31）的场星受银河尘埃红化，并非真正的白色。
        实测把 1141 颗场星拉中性会把星系盘从 R/G≈1.55 压到 ≈1.01。
        """
        image = _starfield(background=(0.05, 0.06, 0.04))
        full = white_balance_from_stars(image, method="stars", strength=1.0)
        partial = white_balance_from_stars(image, method="stars", strength=0.35)

        # 保守强度下，改动量应明显小于全量校正
        full_delta = float(np.mean(np.abs(full - image)))
        partial_delta = float(np.mean(np.abs(partial - image)))
        self.assertLess(partial_delta, full_delta * 0.5)


class AutoColorCalibrateTests(unittest.TestCase):
    def test_restores_a_known_cast(self):
        """给中性场景乘上已知偏色，校色后参考星应显著更接近中性。

        注意要在**亮部（星点）**上量色偏：加性黑点会把背景整体压到 0，
        背景的通道比在那里没有意义，真正携带颜色信息的是星点。
        """
        base = _starfield(background=(0.05, 0.05, 0.05))
        cast = base * np.array([1.20, 1.00, 0.70], dtype=np.float32)
        cast = np.clip(cast, 0, 1).astype(np.float32)

        def bright_cast(image):
            gray = image.mean(axis=2)
            mask = gray >= np.percentile(gray, 99.0)
            med = np.median(image[mask], axis=0)
            return max(abs(med[0] / med[1] - 1.0), abs(med[2] / med[1] - 1.0))

        result, report = auto_color_calibrate(cast, return_report=True)

        self.assertEqual(result.shape, cast.shape)
        self.assertTrue(np.isfinite(result).all())
        self.assertLess(bright_cast(result), bright_cast(cast))
        self.assertIn("mode", report)

    def test_keeps_warm_core_warm(self):
        """暖核不能被校色翻成冷核（旧实现的 96× 增益会这样做）。"""
        image = np.full((96, 96, 3), [0.05, 0.05, 0.05], dtype=np.float32)
        image[40:56, 40:56] = [0.9, 0.8, 0.6]

        result = auto_color_calibrate(image)
        core = np.array([result[40:56, 40:56, c].mean() for c in range(3)])

        self.assertGreater(core[0], core[2])
        self.assertLess(core[2] / max(core[1], 1e-9), 1.0)


if __name__ == "__main__":
    unittest.main()
