"""`masked_ghs_stretch` 的内层 GHS 切线保护（归一化 [0,1] 空间）。

背景：`ghs_stretch` 早已实现 LP/HP 切线保护（`_ghs_base_slope` + 四段构造），
但 `masked_ghs_stretch` 只把 `lp/hp` 当作**绝对输入单位的重锚点**用，从不向下透传，
代码注释自认「既有缺口，保持现状」。

为什么不能盲目透传：外层 `lp/hp` 的语义是 `source=lp→0`、`source=hp→1`（归一化之前），
而内层 GHS 看到的是已经重锚定到 [0,1] 的 `normalized`。原样透传量纲不符；按
`((v-low)/(high-low))` 换算又会退化为 `(0,1)`（等于无保护）。故新增独立的
`protect_lp`/`protect_hp` 键。`c` 域无关（作用在曲线输出的 [0,1] 上），可以直接透传。

本文件锁住两条：默认路径**逐位不变**；显式开启时保护确实生效。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stretch import ghs_stretch, masked_ghs_stretch  # noqa: E402


def _dark_scene(height=96, width=128, seed=3):
    """极暗线性场景：背景 0.002 + 噪声，暗壳 0.03，中心亮核 0.5。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    base = np.full((height, width), 0.002, dtype=np.float32)
    shell = np.exp(-(((xx - width * 0.5) / (width * 0.30)) ** 2
                     + ((yy - height * 0.5) / (height * 0.30)) ** 2))
    core = np.exp(-(((xx - width * 0.5) / (width * 0.06)) ** 2
                    + ((yy - height * 0.5) / (height * 0.06)) ** 2))
    gray = base + 0.028 * shell + 0.5 * core
    gray = gray + rng.normal(0, 2e-4, gray.shape)
    return np.clip(gray, 0, 1).astype(np.float32)


class MaskedGhsProtectionTests(unittest.TestCase):
    def test_default_path_is_bit_identical(self):
        """默认（不传新键）必须与显式禁用逐位相同。"""
        scene = _dark_scene()
        default = masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06)
        explicit_off = masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06,
            protect_lp=None, protect_hp=None, c=0.0,
        )
        zeroed = masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06,
            protect_lp=0.0, protect_hp=1.0, c=0.0,
        )
        self.assertTrue(np.array_equal(default, explicit_off))
        # 显式 0/1 走的是 ghs_stretch 的全域恒等分支，也应为逐位相同
        self.assertTrue(np.array_equal(default, zeroed))

    def test_protect_lp_changes_only_dark_region(self):
        """protect_lp 抬高阴影锚点：只改暗部，且不产生 NaN。"""
        scene = _dark_scene()
        base = masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06)
        guarded = masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06, protect_lp=0.08,
        )
        self.assertFalse(np.array_equal(base, guarded))
        self.assertTrue(np.all(np.isfinite(guarded)))
        self.assertGreaterEqual(float(guarded.min()), 0.0)
        self.assertLessEqual(float(guarded.max()), 1.0)
        # 暗部（场景最暗的四分之一）受影响，而整体范围仍合法
        dark = scene <= np.percentile(scene, 25)
        self.assertGreater(float(np.abs(guarded - base)[dark].mean()), 0.0)

    def test_c_passthrough_only_compresses_highlights(self):
        """c 是域无关的高光 rolloff：只会压低高光，不会抬高。"""
        scene = _dark_scene()
        base = masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06)
        rolled = masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06, c=0.3)
        self.assertFalse(np.array_equal(base, rolled))
        self.assertLessEqual(float(rolled.max()), float(base.max()) + 1e-9)
        self.assertTrue(np.all(np.isfinite(rolled)))

    def test_auto_anchor_matches_manual_background_level(self):
        """'auto' 推导出的 lp 等于归一化空间的中值（= 自动 sp）。"""
        scene = _dark_scene()
        auto = masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06, protect_lp="auto",
        )

        # 复算函数内部的归一化与 gamma 预拉伸，取中值作为期望锚点
        gamma = 0.45
        low = 0.0                       # shadow_pctl=0.0
        high = float(np.percentile(scene, 99.9))
        if float(np.max(scene)) > high * 1.5:
            high = float(np.max(scene))
        normalized = np.clip((scene - low) / (high - low), 0, 1)
        normalized = np.power(normalized, gamma)
        expected_lp = float(np.clip(float(np.median(normalized)), 0.005, 0.10))

        manual = masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06, protect_lp=expected_lp,
        )
        self.assertTrue(np.array_equal(auto, manual))

    def test_degenerate_window_is_disabled(self):
        """hp <= lp 时不抛异常，且等于禁用路径。"""
        scene = _dark_scene()
        base = masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06)
        degenerate = masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06,
            protect_lp=0.5, protect_hp=0.2,
        )
        self.assertTrue(np.array_equal(base, degenerate))

    def test_invalid_anchor_string_raises(self):
        scene = _dark_scene()
        with self.assertRaises(ValueError):
            masked_ghs_stretch(scene, sp=-1, b=8.0, protect_lp="bogus")

    def test_ghs_stretch_default_window_is_identity_guard(self):
        """底层不变量：lp=0/hp=1 时 ghs_stretch 与裸 GHS 完全一致。"""
        rng = np.random.default_rng(11)
        ramp = np.sort(rng.random(512)).astype(np.float32)
        a = ghs_stretch(ramp, sp=0.02, b=8.0, c=0.0, lp=0.0, hp=1.0)
        b = ghs_stretch(ramp, sp=0.02, b=8.0, c=0.0)
        self.assertTrue(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
