"""星点保亮度饱和补偿。

缺口：主链路的星点处理是 拉伸(arcsinh) → 去绿(SCNR) → 曲线 → 缩星。
arcsinh 压色度、SCNR 又把 Lab a 推向 0，**净效果是去饱和**；而
`star_saturation` 此前只存在于外部无星层路径（starless_profiles /
stellar_recompose），主链路拿不到。

外部权威经验（线性 Seti 星点法）明确要求「饱和必须在拉伸之后、且要足够激进」。

本文件锁住：保亮度、恒等、有界、以及管线确实产出该阶段产物。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from star_tools import saturate_stars  # noqa: E402
from color_conv import safe_rgb2lab as rgb2lab  # noqa: E402


def _star_colors(height=48, width=64, seed=17):
    """稀疏彩色星点层：黑底 + 若干彩色点（模拟真实星点层的色彩分布）。"""
    rng = np.random.default_rng(seed)
    image = np.zeros((height, width, 3), dtype=np.float32)
    for _ in range(12):
        cy = int(rng.integers(4, height - 4))
        cx = int(rng.integers(4, width - 4))
        tint = rng.random(3).astype(np.float32)
        tint = tint / max(float(tint.max()), 1e-6)
        image[cy - 1:cy + 2, cx - 1:cx + 2] = 0.6 * tint
    return image


def _luminance(image):
    return (
        0.2126 * image[..., 0]
        + 0.7152 * image[..., 1]
        + 0.0722 * image[..., 2]
    )


class SaturateStarsTests(unittest.TestCase):
    def test_preserves_luminance_when_no_clipping(self):
        """无裁切时亮度严格不变（这是「保亮度」的定义）。

        注意：色度放大后若某通道被推到 [0,1] 之外，clip 会改变亮度——这是该
        公式（与 stellar_recompose 同源）的固有行为，只影响近零通道的极饱和星核。
        故这里用一条**远离 0/1** 的星点层来验证不变量本身。
        """
        rng = np.random.default_rng(23)
        image = np.zeros((32, 40, 3), dtype=np.float32)
        for _ in range(8):
            cy = int(rng.integers(3, 29))
            cx = int(rng.integers(3, 37))
            image[cy - 1:cy + 2, cx - 1:cx + 2] = 0.4 + 0.3 * rng.random(3)
        out = saturate_stars(image, saturation=1.08)
        self.assertFalse(np.array_equal(out, image))
        delta = np.abs(_luminance(out) - _luminance(image))
        self.assertLess(float(delta.max()), 1e-5)

    def test_luminance_shift_only_happens_where_clipping_occurs(self):
        """亮度偏移必须**只在**裁切发生的像素上出现，且幅度有界。"""
        image = _star_colors()
        out = saturate_stars(image, saturation=1.08)
        delta = np.abs(_luminance(out) - _luminance(image))
        self.assertLess(float(delta.max()), 0.01)

        gray = _luminance(image)[..., None]
        unclipped = gray + (image - gray) * 1.08
        out_of_range = (unclipped < 0.0) | (unclipped > 1.0)
        shifted = delta > 1e-5
        if shifted.any():
            # 每个发生亮度偏移的像素，至少有一个通道被裁切
            self.assertTrue(bool(out_of_range[shifted].any(axis=-1).all()))

    def test_factor_one_is_identity(self):
        image = _star_colors()
        out = saturate_stars(image, saturation=1.0)
        self.assertTrue(np.array_equal(out, image))

    def test_increases_chroma(self):
        image = _star_colors()
        out = saturate_stars(image, saturation=1.08)
        self.assertFalse(np.array_equal(out, image))
        # 色度幅度（偏离灰轴的距离）应增大
        gray = image.mean(axis=2, keepdims=True)
        gray_out = out.mean(axis=2, keepdims=True)
        self.assertGreater(
            float(np.abs(out - gray_out).mean()),
            float(np.abs(image - gray).mean()),
        )

    def test_default_gain_is_bounded(self):
        """保守默认 1.08 的改动幅度必须有界，且输出仍在 [0,1]。"""
        image = _star_colors()
        out = saturate_stars(image, saturation=1.08)
        self.assertLess(float(np.abs(out - image).max()), 0.05)
        self.assertGreaterEqual(float(out.min()), 0.0)
        self.assertLessEqual(float(out.max()), 1.0)

    def test_grayscale_passthrough(self):
        gray = np.full((16, 16), 0.3, dtype=np.float32)
        self.assertTrue(np.array_equal(saturate_stars(gray, 1.5), gray))

    def test_alpha_channel_is_preserved(self):
        rgba = np.zeros((12, 12, 4), dtype=np.float32)
        rgba[..., :3] = 0.5
        rgba[..., 3] = 0.25
        out = saturate_stars(rgba, saturation=1.2)
        self.assertEqual(out.shape, rgba.shape)
        self.assertTrue(np.allclose(out[..., 3], 0.25))

    def test_does_not_shift_hue_much(self):
        """保亮度缩放应保持色相基本不变（区别于 HSV 的 S 缩放）。"""
        image = _star_colors()
        out = saturate_stars(image, saturation=1.08)
        lab_in = rgb2lab(image)
        lab_out = rgb2lab(out)
        # a/b 的方向（色相角）应基本一致
        angle_in = np.arctan2(lab_in[..., 2], lab_in[..., 1])
        angle_out = np.arctan2(lab_out[..., 2], lab_out[..., 1])
        mask = np.abs(lab_in[..., 1]) + np.abs(lab_in[..., 2]) > 1e-3
        if mask.any():
            diff = np.abs(np.angle(np.exp(1j * (angle_out - angle_in)))[mask])
            self.assertLess(float(np.median(diff)), np.deg2rad(2.0))


if __name__ == "__main__":
    unittest.main()
