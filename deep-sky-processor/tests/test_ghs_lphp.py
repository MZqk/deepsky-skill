import unittest
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stretch


def _legacy_ghs(x, sp=0.01, b=8.0, c=0.0):
    """LP/HP 之前的裸 GHS 实现，用于验证默认窗口的向后兼容性。"""
    x = np.clip(x, 0, 1)
    b = max(float(b), 1e-5)
    sp = float(sp)

    denom = np.sinh(b * (1.0 - sp)) - np.sinh(-b * sp)
    if abs(denom) < 1e-9:
        denom = 1e-9

    num = np.sinh(b * (x - sp)) - np.sinh(-b * sp)
    stretched = num / denom

    if c > 0:
        c = float(c)
        stretched = np.power(stretched, 1.0 + c * (1.0 - stretched))

    return np.clip(stretched, 0, 1)


def _ramp(samples=4096):
    return np.linspace(0.0, 1.0, samples)


def _dark_scene(size=256, seed=0):
    """极暗线性图：背景 0.002 + 噪声，暗壳层 0.03，外围 0.006，中心亮核 0.5。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size, :size]
    center = (size - 1) / 2.0
    radius = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)

    image = 0.002 + rng.normal(0, 0.0004, (size, size))
    image += 0.030 * np.exp(-((radius - size * 0.234) / (size * 0.070)) ** 2)
    image += 0.006 * np.exp(-((radius - size * 0.371) / (size * 0.086)) ** 2)
    image[size // 2, size // 2] = 0.5
    return np.clip(image, 0, 1).astype(np.float32)


class TestGhsLpHp(unittest.TestCase):
    def test_default_window_matches_legacy(self):
        # lp=0 / hp=1 时窗口覆盖全域，必须与旧实现逐位一致
        ramp = _ramp(5001)
        for sp in (0.001, 0.01, 0.05, 0.25, 0.5):
            for b in (2.0, 4.0, 8.0, 12.0):
                for c in (0.0, 0.2, 0.4):
                    with self.subTest(sp=sp, b=b, c=c):
                        new = stretch.ghs_stretch(ramp, sp=sp, b=b, c=c, lp=0.0, hp=1.0)
                        old = _legacy_ghs(ramp, sp=sp, b=b, c=c)
                        self.assertLess(
                            float(np.max(np.abs(new - old))), 1e-12
                        )

    def test_endpoints_pinned(self):
        probe = np.array([0.0, 1.0])
        for lp, hp in ((0.0, 1.0), (0.001, 0.05), (0.01, 0.9), (0.2, 0.8)):
            with self.subTest(lp=lp, hp=hp):
                out = stretch.ghs_stretch(probe, sp=0.005, b=8.0, lp=lp, hp=hp)
                self.assertAlmostEqual(float(out[0]), 0.0, places=9)
                self.assertAlmostEqual(float(out[1]), 1.0, places=9)

    def test_monotonic_bounded_finite(self):
        ramp = _ramp(4096)
        for lp, hp, sp, b in (
            (0.0, 1.0, 0.01, 8.0),
            (0.001, 0.05, 0.005, 8.0),
            (0.02, 0.3, 0.05, 4.0),
            (0.1, 0.6, 0.2, 12.0),
        ):
            with self.subTest(lp=lp, hp=hp):
                out = stretch.ghs_stretch(ramp, sp=sp, b=b, lp=lp, hp=hp)
                self.assertTrue(np.isfinite(out).all())
                self.assertGreaterEqual(float(out.min()), 0.0)
                self.assertLessEqual(float(out.max()), 1.0)
                self.assertTrue(np.all(np.diff(out) >= -1e-12))

    def test_protected_zones_are_affine(self):
        # 窗口外必须是直线（只做等比缩放），窗口内必须有真实曲率
        lp, hp = 0.2, 0.8
        kwargs = dict(sp=0.05, b=8.0, lp=lp, hp=hp)

        below = np.linspace(0.0, lp * 0.9, 64)
        d2_below = np.diff(stretch.ghs_stretch(below, **kwargs), n=2)
        self.assertLess(float(np.max(np.abs(d2_below))), 1e-9)

        above = np.linspace(hp + 0.02, 1.0, 64)
        d2_above = np.diff(stretch.ghs_stretch(above, **kwargs), n=2)
        self.assertLess(float(np.max(np.abs(d2_above))), 1e-9)

        middle = np.linspace(0.35, 0.55, 64)
        d2_middle = np.diff(stretch.ghs_stretch(middle, **kwargs), n=2)
        self.assertGreater(float(np.max(np.abs(d2_middle))), 1e-6)

    def test_lp_hp_prevents_dark_collapse(self):
        # 痛点回归：默认窗口会把极暗数据压成一条窄带，LP/HP 重新锚定后不坍缩
        scene = _dark_scene()

        legacy = stretch.ghs_stretch(scene, sp=0.01, b=8.0)
        anchored = stretch.ghs_stretch(scene, sp=0.005, b=8.0, lp=0.001, hp=0.05)

        collapsed_legacy, _, health_legacy = stretch.is_stretch_collapsed(scene, legacy)
        collapsed_anchored, _, health_anchored = stretch.is_stretch_collapsed(scene, anchored)

        self.assertTrue(collapsed_legacy)
        self.assertFalse(collapsed_anchored)
        self.assertGreater(health_anchored["span"], health_legacy["span"] * 10)

    def test_hp_controls_core_landing(self):
        # 默认窗口把亮核压死；显式 hp 让它落到预期位置，且不产生饱和
        scene = _dark_scene()
        core_y = scene.shape[0] // 2

        crushed = stretch.ghs_stretch(scene, sp=0.005, b=8.0)
        preserved = stretch.ghs_stretch(scene, sp=0.005, b=8.0, lp=0.001, hp=0.05)

        self.assertLess(float(crushed[core_y, core_y]), 0.05)
        self.assertGreater(float(preserved[core_y, core_y]), 0.3)
        self.assertEqual(int(np.sum(crushed >= 0.999)), 0)
        self.assertEqual(int(np.sum(preserved >= 0.999)), 0)

        # hp 越大，核心落点越低（单调）
        cores = [
            float(stretch.ghs_stretch(scene, sp=0.005, b=8.0, lp=0.001, hp=hp)[core_y, core_y])
            for hp in (0.05, 0.2, 0.5, 1.0)
        ]
        self.assertTrue(all(cores[i] > cores[i + 1] for i in range(len(cores) - 1)))

    def test_degenerate_window_falls_back(self):
        ramp = _ramp(1024)
        expected = stretch.ghs_stretch(ramp, sp=0.01, b=8.0)
        for lp, hp in ((0.5, 0.2), (0.3, 0.3), (1.0, 1.0), (0.9, 0.1), (0.0, 0.0)):
            with self.subTest(lp=lp, hp=hp):
                out = stretch.ghs_stretch(ramp, sp=0.01, b=8.0, lp=lp, hp=hp)
                self.assertTrue(np.isfinite(out).all())
                np.testing.assert_allclose(out, expected, atol=1e-12)

    def test_masked_ghs_none_is_unchanged(self):
        scene = _dark_scene(size=96)
        base = stretch.masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06)
        same = stretch.masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06, lp=None, hp=None
        )
        np.testing.assert_array_equal(base, same)

    def test_masked_ghs_explicit_anchors(self):
        scene = _dark_scene(size=96)
        core_y = scene.shape[0] // 2

        base = stretch.masked_ghs_stretch(scene, sp=-1, b=8.0, target_bg=0.06)
        anchored = stretch.masked_ghs_stretch(
            scene, sp=-1, b=8.0, target_bg=0.06, hp=0.05
        )

        self.assertGreater(float(np.max(np.abs(anchored - base))), 1e-3)
        self.assertEqual(int(np.sum(anchored >= 0.999)), 0)

        # 该路径下 hp 是归一化除数：hp 越大，核心落点越高（与裸 ghs 方向相反）
        cores = [
            float(
                stretch.masked_ghs_stretch(
                    scene, sp=-1, b=8.0, target_bg=0.06, hp=hp
                )[core_y, core_y]
            )
            for hp in (0.05, 0.2, 0.5)
        ]
        self.assertTrue(all(cores[i] < cores[i + 1] for i in range(len(cores) - 1)))


if __name__ == "__main__":
    unittest.main()
