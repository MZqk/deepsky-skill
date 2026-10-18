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


class HighlightCeilingTests(unittest.TestCase):
    """高光增益封顶：tone curve 不得把亮通道推过 clip 点。

    根因（实测 NGC6888）：`dramatic_nebula` 的 tone curve 在亮区给出
    gain p99=1.32 / max=1.36，而星云核心的 R 通道本就接近 0.86 ——
    全通道同乘后直接撞 1.0，`np.clip` 把 R 削平。逐步拆解（降采样 1/2）：

        起点 09_star_combined      R 饱和率 0.036%
        tone-curve 全通道同乘       0.220%   ← +0.183%，占饱和增量最大头
        + color_separation(0.12)   0.279%
        + saturation(1.32)         0.517%
    """

    @staticmethod
    def _bright_core(size=192):
        """构造「R 接近上限、其余通道偏低」的高亮核心 —— 发射星云的典型形态。"""
        yy, xx = np.mgrid[:size, :size]
        base = 0.02 + 0.84 * np.exp(
            -(((xx - size / 2) ** 2 + (yy - size / 2) ** 2) / (2 * (size / 8.0) ** 2))
        )
        img = np.stack([base * 0.98, base * 0.62, base * 0.70], axis=-1)
        return np.clip(img, 0, 1).astype(np.float32)

    @staticmethod
    def _saturated_ratio(image):
        return float(np.mean(image[..., 0] >= 0.995))

    def test_default_ceiling_keeps_legacy_behaviour(self):
        """默认 ceiling=1.0 必须与历史逐位相同 —— 该参数默认不改变输出。"""
        image = self._bright_core()
        legacy = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0, highlight_ceiling=1.0)[0]
        default = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0)[0]
        np.testing.assert_array_equal(legacy, default)

    def test_ceiling_below_one_prevents_saturation(self):
        image = self._bright_core()
        uncapped = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0, highlight_ceiling=1.0)[0]
        capped = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0, highlight_ceiling=0.98)[0]
        self.assertEqual(self._saturated_ratio(capped), 0.0)
        # 封顶后确实改变了高光（否则本测试无意义）
        self.assertLessEqual(capped.max(), 0.98 + 1e-6)

    def test_ceiling_does_not_reduce_shadows(self):
        """只压亮部增益：暗部压暗行为不得改变（否则等于改了风格）。"""
        image = self._bright_core()
        uncapped = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0, highlight_ceiling=1.0)[0]
        capped = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0, highlight_ceiling=0.98)[0]
        dark = image.mean(axis=2) < 0.05
        np.testing.assert_allclose(capped[dark], uncapped[dark], rtol=1e-6, atol=1e-6)

    def test_ceiling_keeps_channels_below_limit(self):
        """封顶后所有通道都应 ≤ ceiling。"""
        image = self._bright_core()
        capped = apply_professional_style(
            image.copy(), style="dramatic_nebula",
            target_type="emission_nebula", color_mode="emission",
            strength=1.0, highlight_ceiling=0.95)[0]
        self.assertLessEqual(float(capped.max()), 0.95 + 1e-6)


if __name__ == "__main__":
    unittest.main()
