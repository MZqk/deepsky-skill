"""色彩空间转换的数值精度。

旧实现用 `cv2.cvtColor(..., COLOR_RGB2Lab)`。OpenCV 对 float32 输入会把
sRGB→线性 一步按 8 位量化，暗值直接归零 —— 实测 RGB < 0.001 时 L = 0，
一幅线性深空图的 Lab 往返会让 **23.6% 的像素变成纯 0**。
深空线性数据的背景常落在 1e-3 量级，这个精度损失是致命的。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from color_conv import safe_lab2rgb, safe_rgb2lab, safe_hsv2rgb, safe_rgb2hsv
from color_tools import remove_green_noise


class LabPrecisionTests(unittest.TestCase):
    def test_round_trip_is_exact_at_dark_values(self):
        for value in (0.0002, 0.0005, 0.001, 0.002, 0.005, 0.05, 0.5, 1.0):
            with self.subTest(value=value):
                rgb = np.full((1, 1, 3), value, dtype=np.float32)
                back = safe_lab2rgb(safe_rgb2lab(rgb))
                self.assertAlmostEqual(float(back[0, 0, 0]), value, places=5)

    def test_dark_values_do_not_collapse_to_zero(self):
        """核心回归：0.001 在旧实现里会变成 0（L 被量化掉）。"""
        rgb = np.full((1, 1, 3), 0.001, dtype=np.float32)
        lab = safe_rgb2lab(rgb)
        self.assertGreater(float(lab[0, 0, 0]), 0.0)
        self.assertGreater(float(safe_lab2rgb(lab)[0, 0, 0]), 0.0)

    def test_matches_skimage_reference(self):
        from skimage.color import rgb2lab

        rng = np.random.default_rng(4)
        rgb = rng.random((16, 16, 3)).astype(np.float32)
        ours = safe_rgb2lab(rgb)
        reference = rgb2lab(rgb)

        # L 在 [0,100]，a/b 可到 ±100；float32 下 ~5e-3 的绝对差
        # 对应约 5e-5 的相对误差，属正常范围。
        self.assertLess(float(np.max(np.abs(ours - reference))), 0.01)
        self.assertLess(float(np.max(np.abs(ours[..., 0] - reference[..., 0]))), 0.01)

    def test_round_trip_on_a_whole_dark_image(self):
        """模拟 DBE 之后的线性图：背景在 pedestal 上、噪声对称。"""
        rng = np.random.default_rng(6)
        image = np.full((64, 64, 3), 0.0012, dtype=np.float32)
        image += rng.normal(0, 2e-4, image.shape).astype(np.float32)
        image[28:36, 28:36] = 0.8
        image = np.clip(image, 0, 1)

        back = safe_lab2rgb(safe_rgb2lab(image))

        self.assertLess(float(np.max(np.abs(back - image))), 1e-4)
        self.assertLess(float(np.mean(back.mean(axis=2) <= 0)), 0.01)

    def test_known_colour_round_trips(self):
        rgb = np.array([[[0.8, 0.2, 0.1], [0.1, 0.6, 0.3]]], dtype=np.float32)
        back = safe_lab2rgb(safe_rgb2lab(rgb))
        self.assertTrue(np.allclose(back, rgb, atol=1e-4))

    def test_handles_black_and_white(self):
        for value in (0.0, 1.0):
            rgb = np.full((1, 1, 3), value, dtype=np.float32)
            back = safe_lab2rgb(safe_rgb2lab(rgb))
            self.assertAlmostEqual(float(back[0, 0, 0]), value, places=4)

    def test_hsv_helpers_still_work(self):
        rgb = np.array([[[0.9, 0.1, 0.1], [0.2, 0.7, 0.4]]], dtype=np.float32)
        self.assertTrue(
            np.allclose(safe_hsv2rgb(safe_rgb2hsv(rgb)), rgb, atol=1e-4)
        )


class RemoveGreenNoiseTests(unittest.TestCase):
    def test_does_not_zero_a_dark_background(self):
        """SCNR 在近零亮度处不该产生纯黑像素。"""
        rng = np.random.default_rng(8)
        image = np.full((64, 64, 3), 0.0015, dtype=np.float32)
        image += rng.normal(0, 2e-4, image.shape).astype(np.float32)
        image[..., 1] += 0.0004          # 人为的绿色偏置
        image[28:36, 28:36] = 0.6
        image = np.clip(image, 0, 1).astype(np.float32)

        result = remove_green_noise(image, strength=0.25)

        self.assertEqual(result.shape, image.shape)
        self.assertTrue(np.isfinite(result).all())
        self.assertLess(float(np.mean(result.mean(axis=2) <= 0)), 0.01)

    def test_reduces_a_green_cast_in_the_midtones(self):
        image = np.full((64, 64, 3), [0.30, 0.45, 0.30], dtype=np.float32)
        result = remove_green_noise(image, strength=0.5)

        before = float(image[..., 1].mean() / image[..., 0].mean())
        after = float(result[..., 1].mean() / result[..., 0].mean())
        self.assertLess(after, before)

    def test_all_zero_input_is_returned_unchanged(self):
        image = np.zeros((16, 16, 3), dtype=np.float32)
        self.assertTrue(np.array_equal(remove_green_noise(image), image))


if __name__ == "__main__":
    unittest.main()
