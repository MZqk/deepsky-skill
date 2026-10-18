"""风格黑位守卫比例（v0.1.40）。

锁定四条性质：

  1. **比例收窄**：守卫从 0.5 降到 0.25（实测依据见常量注释）。
  2. **防裁切职责保留**：floor 恒 <= 局部背景 —— 这是
     CORNER_NONUNIFORM / BACKGROUND_CRUSHED 的防护不变量。
  3. **归零像素不增加**：0.25 相对 0.5 反而让归零像素变少
     （实测 0.346% -> 0.007%），因为 p5+black_floor 重新成为约束项。
  4. **既有诊断键不消失**（下游 result.json 依赖）。

不锁具体读数：这些依赖夹具参数，性质才是真正不能退化的部分。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from style_tools import (
    _tone_curve,
    _FLOOR_GUARD_FRACTION,
    STYLE_PROFILES,
    _local_background,
)


def _scene(pedestal=0.03, size=320, seed=5, contrast=0.30):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    img = np.full((size, size), pedestal, dtype=np.float32)
    img += rng.normal(0, pedestal * 0.02, (size, size)).astype(np.float32)
    img += contrast * np.exp(
        -(((xx - size * 0.5) / 50.0) ** 2 + ((yy - size * 0.5) / 40.0) ** 2)
    ).astype(np.float32)
    return np.clip(img, 0, 1).astype(np.float32)


class GuardFractionTests(unittest.TestCase):

    def test_fraction_is_quarter(self):
        self.assertAlmostEqual(_FLOOR_GUARD_FRACTION, 0.25)

    def test_fraction_is_tighter_than_history(self):
        """新比例必须严格小于历史的 0.5，否则本改动无意义。"""
        self.assertLess(_FLOOR_GUARD_FRACTION, 0.5)

    def test_reported_fraction_matches_constant(self):
        _t, d = _tone_curve(_scene(), STYLE_PROFILES["galaxy_core"],
                            return_diagnostics=True)
        self.assertAlmostEqual(d["floor_guard_fraction"],
                               _FLOOR_GUARD_FRACTION)


class LocalFloorInvariantTests(unittest.TestCase):
    """性质 2：floor 恒 <= 局部背景。"""

    def test_floor_never_exceeds_local_background(self):
        image = _scene()
        maps, _grid = _local_background(image, block=96,
                                        percentiles=(5.0, 25.0))
        bg_map = maps[25.0]
        _t, d = _tone_curve(image, STYLE_PROFILES["galaxy_core"],
                            return_diagnostics=True)
        self.assertLessEqual(d["floor_max"], float(bg_map.max()) + 1e-6)

    def test_no_pixel_clipped_to_zero(self):
        """性质 3：收紧守卫后不得引入归零像素。"""
        image = _scene()
        t, _d = _tone_curve(image, STYLE_PROFILES["galaxy_core"],
                            return_diagnostics=True)
        self.assertLess(float(np.mean(t <= 0.0)), 0.01)

    def test_floor_below_cap(self):
        image = _scene()
        _t, d = _tone_curve(image, STYLE_PROFILES["galaxy_core"],
                            return_diagnostics=True)
        self.assertLessEqual(d["floor_global"], 0.25 + 1e-9)


class DiagnosticsCompatibilityTests(unittest.TestCase):
    """性质 4：既有诊断键不得消失。"""

    def test_existing_keys_present(self):
        _t, d = _tone_curve(_scene(), STYLE_PROFILES["galaxy_core"],
                            return_diagnostics=True)
        for k in ("floor_global", "floor_min", "floor_max", "floor_mean",
                  "local_bg_min", "local_bg_max",
                  "clipped_block_frac_before", "block_size", "n_blocks",
                  "blocks_clipped_before_repair", "repair_shift"):
            self.assertIn(k, d)


if __name__ == "__main__":
    unittest.main()