"""风格定调必须逐像素保留 RGB 通道比例。

旧实现把图像转到 Lab、替换 L 通道、再乘大 a/b。Lab 的 a/b 是**绝对**色度，
L 被 `black_floor` 压暗后色度不变等于相对放大 —— 实测暗背景的 B/G 由 1.29
一步跳到 **3.20**（整片背景发蓝，触发 BACKGROUND_COLOR_CAST 门）。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from style_tools import STYLE_PROFILES, apply_professional_style


def _scene(height=96, width=128, seed=21):
    """暖色星系 + 深色背景，模拟风格定调的真实输入。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    glow = np.exp(-(((xx - width * 0.5) / 18.0) ** 2
                    + ((yy - height * 0.5) / 14.0) ** 2))
    base = 0.05 + 0.55 * glow
    image = np.stack([base, base * 0.75, base * 0.62], axis=-1)
    image = image + rng.normal(0, 3e-3, image.shape)
    return np.clip(image, 0, 1).astype(np.float32)


class StyleColourPreservationTests(unittest.TestCase):
    def _ratio(self, image, mask):
        med = np.median(image[mask], axis=0)
        return float(med[0] / max(med[1], 1e-9)), float(med[2] / max(med[1], 1e-9))

    def test_dark_background_does_not_gain_a_blue_cast(self):
        image = _scene()
        styled, _selected, _reasoning = apply_professional_style(
            image, style="galaxy_core", target_type="galaxy", strength=1.0
        )

        dark = styled.mean(axis=2) <= np.percentile(styled.mean(axis=2), 25)
        if float(np.median(styled[dark].mean(axis=1))) <= 0:
            self.skipTest("暗区被压到纯黑，无从测色偏")
        _rg, bg = self._ratio(styled, dark)
        self.assertLess(bg, 1.6)

    def test_core_colour_ratio_is_preserved(self):
        image = _scene()
        core = image.mean(axis=2) > np.percentile(image.mean(axis=2), 90)
        before_rg, before_bg = self._ratio(image, core)

        styled, _selected, _reasoning = apply_professional_style(
            image, style="galaxy_core", target_type="galaxy", strength=1.0
        )
        after_rg, after_bg = self._ratio(styled, core)

        self.assertAlmostEqual(after_rg, before_rg, delta=0.25)
        self.assertAlmostEqual(after_bg, before_bg, delta=0.25)

    def test_still_changes_the_image(self):
        image = _scene()
        styled, _selected, _reasoning = apply_professional_style(
            image, style="galaxy_core", target_type="galaxy", strength=1.0
        )
        self.assertEqual(styled.shape, image.shape)
        self.assertGreater(float(np.mean(np.abs(styled - image))), 0.001)
        self.assertGreaterEqual(float(styled.min()), 0.0)
        self.assertLessEqual(float(styled.max()), 1.0)

    def test_zero_strength_is_a_no_op(self):
        image = _scene()
        styled, _selected, _reasoning = apply_professional_style(
            image, style="galaxy_core", target_type="galaxy", strength=0.0
        )
        self.assertTrue(np.allclose(styled, image, atol=1e-5))

    def test_all_profiles_stay_in_range(self):
        image = _scene()
        for name in STYLE_PROFILES:
            with self.subTest(style=name):
                styled, _s, _r = apply_professional_style(
                    image, style=name, strength=1.0
                )
                self.assertTrue(np.isfinite(styled).all())
                self.assertGreaterEqual(float(styled.min()), 0.0)
                self.assertLessEqual(float(styled.max()), 1.0)

    def test_gray_input_is_supported(self):
        image = _scene().mean(axis=2)
        styled, _s, _r = apply_professional_style(
            image, style="galaxy_core", strength=1.0
        )
        self.assertEqual(styled.shape[:2], image.shape)


if __name__ == "__main__":
    unittest.main()
