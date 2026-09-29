"""Phase 8 `final_color` 的饱和度提升（`color_tools.enhance_saturation`）必须**保比例**。

缺陷（旧实现）：在 HSV 空间乘 S：

    hsv[..., 1] = np.clip(hsv[..., 1] * local_factor, 0.0, 0.98)

HSV 的 S 乘法保持 max 通道不变、把 (max−min) 拉开。当 G≠B 时两个非最大通道被
**不等比例**压向 0 —— 实测（factor=1.45）：

| 输入 RGB | B/G 输入 | 旧 HSV 后 | 保比例后 |
|---|---|---|---|
| `[0.25,0.10,0.06]` | 0.600 | **0.088** | 0.305 |
| `[0.50,0.15,0.09]` | 0.600 | **0.122** | 0.183 |
| `[0.30,0.20,0.14]` | 0.700 | 0.439 | 0.552 |
| `[0.50,0.15,0.15]`（G=B） | 1.000 | 1.000（但 R/G 冲到 50） | 1.000（R/G 6.21） |

对发射星云即「把 OIII 的青蓝电离区抹成暗红」—— 与 `style_tools` 的 P0-1 是同一类缺陷，
Phase 8 这一处因子更高（1.45），破坏更重。

修复改用**均值轴保比例**公式 `new = mean + (x - mean) * k`，并用**每像素色域上限**
（而非旧实现末尾的"底电平守护"均匀抬亮补丁）保证不越界、均值严格守恒。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from color_tools import enhance_saturation  # noqa: E402


def _ratio_formula(rgb, k):
    """参照实现：均值轴保比例缩放 + 每像素色域上限。"""
    arr = np.asarray(rgb, dtype=np.float32)
    neutral = arr.mean(axis=-1, keepdims=True)
    dev = arr - neutral
    safe = np.maximum(np.abs(dev), 1e-9)
    headroom = np.where(dev > 0, (1.0 - neutral) / safe, neutral / safe)
    k_eff = np.minimum(k, np.maximum(np.min(headroom, axis=-1), 0.0))
    return np.clip(neutral + dev * k_eff[..., None], 0, 1)


def _b_over_g(arr):
    return float(arr[..., 2]) / max(float(arr[..., 1]), 1e-9)


class EnhanceSaturationRatioTests(unittest.TestCase):
    def test_red_dominant_signal_keeps_oiii_channels(self):
        """① 核心：强饱和红区的 B/G 必须保住（旧 HSV 实现会压到 0.09）。

        合成色块必须 **G≠B**；若 G=B 则两个非最大通道等比收缩、B/G 恒为 1，
        测不出缺陷（详见 `test_style_saturation_ratio_preservation.py` 的同类说明）。
        """
        colors = {
            "ha1": (0.25, 0.10, 0.06),
            "ha2": (0.50, 0.15, 0.09),
            "ha3": (0.30, 0.20, 0.14),
        }
        for name, value in colors.items():
            with self.subTest(color=name):
                arr = np.array(value, dtype=np.float32).reshape(1, 1, 3)
                out = enhance_saturation(arr, factor=1.45, protect_background=False)
                retention = _b_over_g(out.ravel()) / _b_over_g(np.array(value, np.float32))
                # 旧实现给 0.15 / 0.20 / 0.63，新实现给 0.51 / 0.31 / 0.79
                self.assertGreater(retention, 0.25,
                                   f"{name}: B/G 保留率仅 {retention:.3f}")

    def test_preserves_pixel_mean_when_not_clipped(self):
        """② 未触及色域上限时，逐像素均值严格守恒。"""
        colors = np.array([
            [0.25, 0.10, 0.06],
            [0.30, 0.20, 0.14],
            [0.05, 0.06, 0.08],
            [0.22, 0.20, 0.18],
        ], dtype=np.float32).reshape(1, 4, 3)
        for factor in (1.05, 1.45, 1.80):
            with self.subTest(factor=factor):
                out = enhance_saturation(colors, factor=factor, protect_background=False)
                self.assertTrue(np.allclose(out.mean(axis=-1), colors.mean(axis=-1),
                                            atol=1e-6))

    def test_gamut_cap_prevents_out_of_range_and_keeps_mean(self):
        """③ 极端 factor 下也不得越出 [0,1]，且均值仍守恒（色域上限而非裁切）。"""
        arr = np.array([0.25, 0.10, 0.06], dtype=np.float32).reshape(1, 1, 3)
        for factor in (2.5, 4.0, 8.0):
            with self.subTest(factor=factor):
                out = enhance_saturation(arr, factor=factor, protect_background=False)
                self.assertGreaterEqual(float(out.min()), 0.0)
                self.assertLessEqual(float(out.max()), 1.0)
                self.assertAlmostEqual(float(out.mean()), float(arr.mean()), places=6)

    def test_weak_channel_never_collapses_on_nonuniform_image(self):
        """④ 非均匀图上弱通道不被压死为 0（旧实现的"底电平守护"场景）。"""
        h, w = 48, 48
        img = np.full((h, w, 3), 0.02, dtype=np.float32)
        img[..., 0] = 0.030
        img[..., 1] = 0.008
        img[..., 2] = 0.020
        # 制造 V 分位跨度，让背景保护真正生效（均匀图会退化为恒等）
        img[h // 3:, w // 3:] = np.array([0.30, 0.12, 0.18], np.float32)

        boosted = enhance_saturation(img, factor=1.85, protect_background=True,
                                     bg_protection_percentile=40)
        self.assertGreater(float(np.min(boosted[..., 1])), 1e-4)
        self.assertTrue(np.all(np.isfinite(boosted)))

    def test_background_protection_leaves_uniform_dark_background_untouched(self):
        """⑤ 背景保护仍然生效：均匀暗背景（V 分位退化）不应被着色。"""
        img = np.full((32, 32, 3), 0.01, dtype=np.float32)
        out = enhance_saturation(img, factor=1.45, protect_background=True,
                                 bg_protection_percentile=40)
        self.assertTrue(np.allclose(out, img, atol=1e-6))

    def test_bright_signal_is_actually_boosted(self):
        """⑥ 反证：亮区必须真的被提升（不能因为"保比例"而变成恒等）。"""
        h, w = 64, 64
        img = np.full((h, w, 3), 0.01, dtype=np.float32)
        img[24:40, 24:40] = np.array([0.05, 0.06, 0.08], np.float32)

        out = enhance_saturation(img, factor=1.45, protect_background=True,
                                 bg_protection_percentile=40)
        before = abs(float(img[32, 32, 2]) - float(img[32, 32, 0]))
        after = abs(float(out[32, 32, 2]) - float(out[32, 32, 0]))
        self.assertGreater(after, before * 1.2)

    def test_reference_formula_matches_implementation(self):
        """⑦ 实现与参照公式逐位一致（防止后续改动悄悄偏离）。"""
        rng = np.random.default_rng(7)
        arr = rng.uniform(0.0, 1.0, (16, 16, 3)).astype(np.float32)
        for factor in (1.0, 1.45, 2.2):
            with self.subTest(factor=factor):
                impl = enhance_saturation(arr, factor=factor, protect_background=False)
                ref = _ratio_formula(arr, factor)
                self.assertTrue(np.allclose(impl, ref, atol=1e-6))


if __name__ == "__main__":
    unittest.main()
