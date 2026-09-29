"""Stretch collapse detection.

masked_ghs 在黑点估计被极亮核心带偏时会把几乎所有像素挤进一条很窄的亮度带
（实测 span 从 0.145 塌到 0.014），管线此前不做任何检查，直接交给下一阶段的
CLAHE 去救 —— 而 CLAHE 是局部对比算子，无法恢复全局色调映射。

判据只用绝对跨度 span = p99 - p50。历史上曾有一条 p999/p50（core_ratio）比值
判据，因任何抬升背景的拉伸都会让该比值下降 —— 无法区分"正常的背景抬升"与
"病态的对比度压塌"，实测误报率 40.3% 且零额外检出能力，已移除。本文件保留
对应的回归测试，防止该判据被重新引入。
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


def _two_level(bg, peak, bg_frac=0.9, size=128):
    """双峰图：背景 bg 占 bg_frac，其余为峰值 peak（近似真实深空直方图）。"""
    total = size * size
    k = int(total * bg_frac)
    flat = np.concatenate(
        [np.full(total - k, peak), np.full(k, bg)]
    ).astype(np.float32)
    return np.stack([flat.reshape(size, size)] * 3, axis=-1)


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

    def test_background_lift_alone_is_not_collapse(self):
        """抬升背景会让 p999/p50 必然下降，这本身不是塌缩。

        旧实现要求该比值不低于拉伸前的一半（min_core_frac=0.5），于是把正常的
        背景抬升误判为塌缩 —— 实测正常拉伸样本的误报率高达 40.3%。这里用一个
        "背景抬到 target_bg、峰值与跨度都健康"的双峰场景锁定该行为。
        """
        before = _two_level(bg=0.012, peak=0.500)
        after = _two_level(bg=0.150, peak=0.500)

        before_health = luminance_range_health(before)
        after_health = luminance_range_health(after)

        # 前提：比值确实大幅下降（旧判据会在此触发），但跨度是健康的
        self.assertLess(
            after_health["core_ratio"], before_health["core_ratio"] * 0.5
        )
        self.assertGreater(after_health["span"], before_health["span"] * 0.25)

        collapsed, _, _ = is_stretch_collapsed(before, after)
        self.assertFalse(collapsed)


if __name__ == "__main__":
    unittest.main()
