"""Stretch collapse detection.

masked_ghs 在黑点估计被极亮核心带偏时会把几乎所有像素挤进一条很窄的亮度带
（实测 span 从 0.145 塌到 0.014），管线此前不做任何检查，直接交给下一阶段的
CLAHE 去救 —— 而 CLAHE 是局部对比算子，无法恢复全局色调映射。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from stretch import is_stretch_collapsed, luminance_range_health


def _linear_ish(height=128, width=128):
    """模拟线性深空图：背景被 clip 到 0，核心很亮。"""
    image = np.zeros((height, width, 3), dtype=np.float32)
    image[40:80, 40:80] = 0.55
    image[56:64, 56:64] = 0.95
    return image


class LuminanceRangeHealthTests(unittest.TestCase):
    def test_reports_span_and_core_ratio(self):
        health = luminance_range_health(_linear_ish())
        self.assertIn("span", health)
        self.assertIn("core_ratio", health)
        self.assertGreater(health["p99"], health["p50"])

    def test_accepts_gray_and_rgb(self):
        gray = np.full((32, 32), 0.2, dtype=np.float32)
        self.assertEqual(luminance_range_health(gray)["span"], 0.0)
        self.assertEqual(luminance_range_health(np.stack([gray] * 3, -1))["span"], 0.0)


def _ramp(low, high, height=128, width=128):
    """构造一个亮度从 low 平滑升到 high 的图，用于复现实测的分位数值。"""
    values = np.linspace(low, high, height * width, dtype=np.float32)
    gray = values.reshape(height, width)
    return np.stack([gray, gray, gray], axis=-1)


class StretchCollapseTests(unittest.TestCase):
    def test_detects_a_flattened_stretch(self):
        """复现旧 run 实测的塌缩：span 约 0.135 → 约 0.026。"""
        before = _ramp(0.0457, 0.3207)
        after = _ramp(0.0389, 0.0928)

        collapsed, before_health, after_health = is_stretch_collapsed(before, after)

        self.assertTrue(collapsed)
        self.assertLess(after_health["span"], before_health["span"] * 0.25)

    def test_does_not_flag_a_healthy_stretch(self):
        before = _linear_ish()
        after = np.clip(before ** 0.45, 0, 1)

        collapsed, _before, after_health = is_stretch_collapsed(before, after)

        self.assertFalse(collapsed)
        self.assertGreater(after_health["span"], 0.02)

    def test_ignores_core_ratio_when_reference_background_is_clipped(self):
        """背景被 clip 到 0 时 p999/p50 会被 epsilon 主导，不能据此判塌缩。"""
        before = _linear_ish()
        before_health = luminance_range_health(before)
        self.assertLess(before_health["p50"], 0.01)

        after = np.clip(before ** 0.45, 0, 1)
        collapsed, _b, _a = is_stretch_collapsed(before, after)
        self.assertFalse(collapsed)


if __name__ == "__main__":
    unittest.main()
