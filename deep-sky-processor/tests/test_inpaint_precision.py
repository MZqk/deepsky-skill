"""OpenCV 修复（inpaint）必须用 float32，不能量化到 uint8。

旧实现先把图像按自身峰值缩放到 [0,1]、再乘 255 取整成 uint8。对线性深空
数据是灾难性的：背景 0.0017 相对峰值 0.97 只占 **0.45/255**，四舍五入后
整片背景直接归零 —— 实测修复后 **93.7%** 的像素变成纯 0，
去星质量评分从 0.72 掉到 0.001，每次必然回退。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from star_tools import HAS_OPENCV, inpaint_ns, inpaint_telea


@unittest.skipUnless(HAS_OPENCV, "需要 OpenCV")
class InpaintPrecisionTests(unittest.TestCase):
    def _dark_frame(self, height=64, width=64):
        """线性深空特征：背景在很小的 pedestal 上，峰值高出两个数量级。"""
        rng = np.random.default_rng(12)
        image = np.full((height, width), 0.0017, dtype=np.float32)
        image += rng.normal(0, 2e-4, (height, width)).astype(np.float32)
        image[30:34, 30:34] = 0.95
        return np.clip(image, 0, 1).astype(np.float32)

    def _mask(self, shape):
        mask = np.zeros(shape, dtype=np.float32)
        mask[30:34, 30:34] = 1.0
        return mask

    def _assert_preserves_background(self, result, source):
        background = source < 0.01
        self.assertGreater(float(np.median(result[background])), 0.0)
        self.assertLess(
            float(np.mean(result <= 0)),
            0.01,
            "背景被量化归零 —— inpaint 又走回了 uint8 路径",
        )

    def test_telea_does_not_zero_the_background(self):
        image = self._dark_frame()
        result = inpaint_telea(image, self._mask(image.shape), radius=5)

        self.assertEqual(result.shape, image.shape)
        self._assert_preserves_background(result, image)

    def test_navier_stokes_does_not_zero_the_background(self):
        image = self._dark_frame()
        result = inpaint_ns(image, self._mask(image.shape), radius=5)

        self.assertEqual(result.shape, image.shape)
        self._assert_preserves_background(result, image)

    def test_star_is_actually_removed(self):
        image = self._dark_frame()
        result = inpaint_telea(image, self._mask(image.shape), radius=5)

        self.assertLess(float(result[30:34, 30:34].max()), 0.5)

    def test_unmasked_pixels_are_essentially_untouched(self):
        image = self._dark_frame()
        mask = self._mask(image.shape)
        result = inpaint_telea(image, mask, radius=5)

        untouched = mask == 0
        self.assertLess(float(np.max(np.abs(result[untouched] - image[untouched]))), 1e-3)

    def test_handles_rgb_input(self):
        gray = self._dark_frame()
        rgb = np.stack([gray, gray * 0.8, gray * 0.6], axis=-1).astype(np.float32)
        mask = self._mask(gray.shape)

        result = inpaint_telea(rgb, mask, radius=5)

        self.assertEqual(result.shape, rgb.shape)
        self.assertLess(float(np.mean(result.mean(axis=2) <= 0)), 0.01)

    def test_all_zero_input_is_returned_unchanged(self):
        image = np.zeros((32, 32), dtype=np.float32)
        mask = np.zeros((32, 32), dtype=np.float32)
        mask[10:14, 10:14] = 1.0
        result = inpaint_telea(image, mask, radius=3)
        self.assertTrue(np.all(result == 0))


if __name__ == "__main__":
    unittest.main()
