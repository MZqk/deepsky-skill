"""拉伸必须逐像素保留 RGB 通道比例。

旧实现把图像转到 Lab、只替换 L 通道、再转回 RGB。Lab 的 a/b 是**绝对**色度，
亮度抬高后色度不变等于稀释饱和度 —— 实测核心像素 (0.295, 0.260, 0.242) 的 L
从 28.9 拉到 86.5、a/b 原样保留后 R/G 由 1.137 掉到 1.055；整幅 M31 的核心
R/G 从 1.49 塌到 1.02，画面变成灰调。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stretch
from stretch import apply_luminance_stretch


def _warm_scene(height=64, width=64):
    """暖色场景，核心/背景比约 11:1 —— 刻意不做到 M31 那种数百倍反差，
    否则 target_bg 归一化后核心必然过曝，被 clip 的那部分比例本来就会变，
    测不出拉伸本身是否保色。"""
    yy, xx = np.mgrid[0:height, 0:width]
    glow = np.exp(-(((xx - width * 0.5) / 10.0) ** 2
                    + ((yy - height * 0.5) / 10.0) ** 2))
    base = 0.01 + 0.10 * glow
    image = np.stack([base, base * 0.67, base * 0.52], axis=-1)
    return image.astype(np.float32)


class StretchColourPreservationTests(unittest.TestCase):
    def _ratio(self, image, mask):
        med = np.median(image[mask], axis=0)
        return float(med[0] / max(med[1], 1e-9)), float(med[2] / max(med[1], 1e-9))

    def test_keeps_channel_ratios_through_the_stretch(self):
        image = _warm_scene()
        mask = image.mean(axis=2) > np.percentile(image.mean(axis=2), 80)

        before_rg, before_bg = self._ratio(image, mask)
        stretched = apply_luminance_stretch(
            image, method="masked_ghs", sp=-1, b=8.34,
            protect_strength=0.65, target_bg=0.08,
            shadow_pctl=0.0, highlight_pctl=99.9, gamma=0.45,
        )
        after_rg, after_bg = self._ratio(stretched, mask)

        self.assertAlmostEqual(after_rg, before_rg, places=2)
        self.assertAlmostEqual(after_bg, before_bg, places=2)

    def test_warm_core_stays_warm(self):
        image = _warm_scene()
        stretched = apply_luminance_stretch(
            image, method="masked_ghs", sp=-1, b=8.34,
            protect_strength=0.65, target_bg=0.08,
            shadow_pctl=0.0, highlight_pctl=99.9, gamma=0.45,
        )
        core = stretched[28:36, 28:36]
        median = np.median(core.reshape(-1, 3), axis=0)

        self.assertGreater(median[0], median[1])
        self.assertGreater(median[1], median[2])

    def test_still_brightens_and_stays_in_range(self):
        image = _warm_scene()
        stretched = apply_luminance_stretch(
            image, method="masked_ghs", sp=-1, b=8.34,
            protect_strength=0.65, target_bg=0.08,
            shadow_pctl=0.0, highlight_pctl=99.9, gamma=0.45,
        )

        self.assertGreater(
            float(np.median(stretched.mean(axis=2))),
            float(np.median(image.mean(axis=2))),
        )
        self.assertGreaterEqual(float(stretched.min()), 0.0)
        self.assertLessEqual(float(stretched.max()), 1.0)
        self.assertTrue(np.isfinite(stretched).all())

    def test_channel_order_is_preserved(self):
        """既有测试依赖的通道次序关系必须仍然成立。"""
        image = np.zeros((16, 16, 3), dtype=np.float32)
        image[..., 0] = 0.01
        image[..., 1] = 0.005
        image[..., 2] = 0.02

        stretched = apply_luminance_stretch(
            image, method="masked_ghs", sp=0.01, b=8.0, target_bg=0.08
        )

        self.assertTrue(np.all(stretched[..., 2] > stretched[..., 0]))
        self.assertTrue(np.all(stretched[..., 0] > stretched[..., 1]))

    def test_gray_input_is_unaffected(self):
        image = _warm_scene().mean(axis=2)
        stretched = apply_luminance_stretch(
            image, method="arcsinh", factor=12
        )
        self.assertEqual(stretched.ndim, 2)
        self.assertTrue(np.isfinite(stretched).all())

    def test_zero_pixels_do_not_blow_up(self):
        image = _warm_scene()
        image[:8, :8] = 0.0
        stretched = apply_luminance_stretch(
            image, method="masked_ghs", sp=-1, b=8.34,
            protect_strength=0.65, target_bg=0.08,
            shadow_pctl=0.0, highlight_pctl=99.9, gamma=0.45,
        )
        self.assertTrue(np.isfinite(stretched).all())
        self.assertLessEqual(float(stretched.max()), 1.0)

    def test_all_stretch_methods_preserve_ratios(self):
        """覆盖走 Lab 通路的各拉伸方法。

        不含 `masked`：它在 p99 ≤ 0.3 时会走"极暗数据回退"分支，
        该分支做百分位归一化 + 背景缩放，本来就会裁掉亮部 ——
        被裁掉的部分比例必然改变，与拉伸是否保色无关。
        """
        image = _warm_scene()
        mask = image.mean(axis=2) > np.percentile(image.mean(axis=2), 80)
        before_rg, before_bg = self._ratio(image, mask)

        for method, kwargs in (
            ("arcsinh", {"factor": 12}),
            ("ghs", {"sp": 0.01, "b": 8.0}),
            ("masked_ghs", {"sp": -1, "b": 8.34, "target_bg": 0.08}),
        ):
            with self.subTest(method=method):
                stretched = apply_luminance_stretch(image, method=method, **kwargs)
                after_rg, after_bg = self._ratio(stretched, mask)
                self.assertAlmostEqual(after_rg, before_rg, places=2)
                self.assertAlmostEqual(after_bg, before_bg, places=2)

    def test_masked_stretch_also_keeps_ratios_on_bright_input(self):
        """给足亮度的输入时，masked 走正常蒙版分支，同样应保色。"""
        yy, xx = np.mgrid[0:64, 0:64]
        glow = np.exp(-(((xx - 32) / 10.0) ** 2 + ((yy - 32) / 10.0) ** 2))
        base = 0.25 + 0.45 * glow
        image = np.stack([base, base * 0.67, base * 0.52], axis=-1).astype(np.float32)
        mask = image.mean(axis=2) > np.percentile(image.mean(axis=2), 80)
        before_rg, before_bg = self._ratio(image, mask)

        stretched = apply_luminance_stretch(
            image, method="masked", factor=2.0, target_bg=0.10
        )
        after_rg, after_bg = self._ratio(stretched, mask)

        self.assertAlmostEqual(after_rg, before_rg, places=2)
        self.assertAlmostEqual(after_bg, before_bg, places=2)


if __name__ == "__main__":
    unittest.main()
