"""风格定调的饱和度提升必须**保比例**，不得像 HSV 的 S 乘法那样压死弱通道。

缺陷（`style_tools.py:711-712` 的旧实现）：

    sat_factor = 1 + (profile["saturation"] - 1) * strength
    hsv[..., 1] *= sat_factor

HSV 的 S 乘法保持 max 通道不变、把 (max−min) 拉开。当 G≠B 时两个非最大通道
被**不等比例**压向 0 —— 实测纯公式（×1.32）：

| 输入 RGB | B/G 输入 | 旧 HSV 后 | 保比例后 |
|---|---|---|---|
| `[0.25,0.10,0.06]` | 0.600 | **0.000** | 0.402 |
| `[0.50,0.15,0.09]` | 0.600 | **0.000** | 0.335 |
| `[0.30,0.20,0.14]` | 0.700 | 0.529 | 0.595 |

经真实 `apply_professional_style`（dramatic_nebula + emission + strength=1.0），
B/G 保留率从 **0.0% / 29.6% / 66.6%** 提升到 **58.2% / 64.9% / 76.7%**。

对发射星云即「系统性破坏 OIII」—— 双窄带数据的青蓝电离区被抹成暗红。

修复改用与同函数 `:680` `color_separation` 相同的**均值轴保比例**公式：
`new = mean + (x - mean) * k`。未越界裁切时逐像素均值严格不变，弱通道只按
其偏离均值的比例缩放。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from style_tools import apply_professional_style  # noqa: E402

STYLE_KW = dict(style="dramatic_nebula", target_type="emission_nebula",
                color_mode="emission")


# ── 公式级参照实现（不依赖管线，纯 numpy，用于确定性对照） ──────────────

def _ratio_formula(rgb, k):
    """新实现：以像素均值为轴的保比例色度缩放。"""
    m = rgb.mean(axis=-1, keepdims=True)
    return np.clip(m + (rgb - m) * k, 0, 1)


def _hsv_formula(rgb, k):
    """旧实现：HSV 的 S 乘法。直接用 skimage，与生产代码同源。"""
    from skimage.color import hsv2rgb, rgb2hsv

    arr = np.asarray(rgb, dtype=np.float32)
    hsv = rgb2hsv(arr)
    hsv[..., 1] = np.clip(hsv[..., 1] * k, 0.0, 1.0)
    return hsv2rgb(hsv).astype(np.float32)


def _scene_with_patches(patches, h=128, w=128, bg=0.01):
    """深色中性背景 + 若干强饱和色块。返回 (image, {name: (y0, x0, size)})。"""
    image = np.full((h, w, 3), bg, dtype=np.float32)
    coords = {}
    for i, (name, value) in enumerate(patches.items()):
        y0, x0, size = 20 + i * 28, 40, 20
        image[y0:y0 + size, x0:x0 + size] = value
        coords[name] = (y0, x0, size)
    return image, coords


def _median_ratios(block):
    r, g, b = (float(np.median(block[..., i])) for i in range(3))
    return r / max(g, 1e-9), b / max(g, 1e-9)


class StyleSaturationRatioTests(unittest.TestCase):
    def test_red_dominant_signal_keeps_oiii_channels(self):
        """① 核心：强饱和红区的 B/G 必须保住 —— 旧实现会掉到 0。

        合成图必须用 **G≠B** 的强饱和红像素。若用 G=B（如 [0.50,0.15,0.15]），
        两个非最大通道等比收缩、B/G 恒为 1.0，**测不出缺陷**。
        """
        patches = {"red": (0.25, 0.10, 0.06), "red2": (0.50, 0.15, 0.09)}
        image, coords = _scene_with_patches(patches)
        styled, _, _ = apply_professional_style(image, strength=1.0, **STYLE_KW)

        for name, (y0, x0, size) in coords.items():
            with self.subTest(patch=name):
                before = image[y0 + 6:y0 + size - 6, x0 + 6:x0 + size - 6]
                after = styled[y0 + 6:y0 + size - 6, x0 + 6:x0 + size - 6]
                _, bg_before = _median_ratios(before)
                _, bg_after = _median_ratios(after)
                retention = bg_after / max(bg_before, 1e-9)

                # 旧实现给 0.0 / 0.30，新实现给 0.58 / 0.65
                self.assertGreater(
                    retention, 0.40,
                    f"{name}: B/G 保留率仅 {retention:.3f}（旧 HSV 实现会掉到 0）",
                )
                self.assertGreater(float(np.median(after[..., 2])), 0.0)

    def test_zero_strength_is_identity_for_saturation(self):
        """② strength=0 必须逐位恒等（两处守卫精确短路，不靠数值巧合）。"""
        image, _ = _scene_with_patches({"red": (0.25, 0.10, 0.06)})
        styled, _, _ = apply_professional_style(image, strength=0.0, **STYLE_KW)
        self.assertTrue(np.allclose(styled, image, atol=1e-6))
        self.assertLess(float(np.abs(styled - image).max()), 1e-7)

        # 反证：非零强度必须真的改变图像
        changed, _, _ = apply_professional_style(image, strength=1.0, **STYLE_KW)
        self.assertGreater(float(np.mean(np.abs(changed - image))), 1e-3)

    def test_high_saturation_keeps_dark_blocks_alive(self):
        """③ 偏红暗背景下，饱和度提升后仍不得出现归零区块。

        背景**偏红**是关键：旧 HSV 会把偏红背景的 B/G 压得更低。新实现以均值为轴，
        未越界裁切时逐像素均值严格不变 → `gray = graded.mean(axis=2)` 不变，
        `min_block_median_final` / `blocks_zeroed_final` 只会持平或改善。
        """
        h = w = 192
        yy, xx = np.mgrid[0:h, 0:w]
        bg = 0.06 - 0.05 * (yy / h) * (xx / w)
        image = np.stack([bg * 1.30, bg * 0.90, bg * 0.70], axis=-1)
        glow = np.exp(-(((xx - w * 0.5) / (w * 0.25)) ** 2
                        + ((yy - h * 0.5) / (h * 0.25)) ** 2))
        image = np.clip(image + (0.40 * glow)[..., None], 0, 1).astype(np.float32)

        graded, _, _, diag = apply_professional_style(
            image, strength=1.0, return_diagnostics=True, **STYLE_KW
        )
        self.assertEqual(diag["blocks_zeroed_final"], 0)
        self.assertGreater(diag["min_block_median_final"], 0.0)
        self.assertTrue(np.all(np.isfinite(graded)))
        self.assertGreaterEqual(float(graded.min()), 0.0)
        self.assertLessEqual(float(graded.max()), 1.0)
        # 注意：逐像素归零由 tone curve 的 black_floor 造成（既有行为，非饱和度步引入），
        # 本用例只关心「区块中位不归零」这一不变量。

    def test_ratio_formula_beats_hsv_on_weak_channel(self):
        """④ 纯公式级对照：同因子下保比例公式的弱通道保留率显著高于 HSV S 乘法。

        完全不依赖管线参数，确定性最强。
        """
        colors = np.array([
            [0.25, 0.10, 0.06],
            [0.50, 0.15, 0.09],
            [0.30, 0.20, 0.14],
            [0.40, 0.18, 0.10],
        ], dtype=np.float32)
        k = 1.32  # dramatic_nebula 的 saturation，strength=1.0

        old = _hsv_formula(colors, k)
        new = _ratio_formula(colors, k)
        # 用 B/G（非最大通道之比）作为判据：这正是被 HSV S 乘法不等比例压掉的量
        old_ret = (old[:, 2] / np.maximum(old[:, 1], 1e-9)) / (colors[:, 2] / colors[:, 1])
        new_ret = (new[:, 2] / np.maximum(new[:, 1], 1e-9)) / (colors[:, 2] / colors[:, 1])

        for i in range(len(colors)):
            with self.subTest(color=colors[i].tolist()):
                self.assertGreater(new_ret[i], old_ret[i])
                self.assertGreater(new_ret[i], 0.3)

        # 整体：新公式的平均 B/G 保留率显著更高
        self.assertGreater(float(new_ret.mean()), 1.5 * float(old_ret.mean()))
        # 旧公式在最极端的样例上会把 B 压到 0
        self.assertEqual(float(old[0, 2]), 0.0)

    def test_ratio_formula_preserves_pixel_mean(self):
        """⑤ 未越界裁切时逐像素均值严格守恒（这是 `blocks_zeroed_final` 不漂移的根据）。"""
        colors = np.array([
            [0.25, 0.10, 0.06],
            [0.50, 0.15, 0.09],
            [0.30, 0.20, 0.14],
            [0.22, 0.20, 0.18],
        ], dtype=np.float32)
        for k in (1.05, 1.32, 1.75):
            with self.subTest(k=k):
                out = _ratio_formula(colors, k)
                unclipped = np.all((out > 0) & (out < 1), axis=-1)
                self.assertTrue(unclipped.any(), "样例全部被裁切，无法验证均值守恒")
                self.assertTrue(np.allclose(
                    out[unclipped].mean(axis=-1),
                    colors[unclipped].mean(axis=-1),
                    atol=1e-6,
                ))


if __name__ == "__main__":
    unittest.main()
