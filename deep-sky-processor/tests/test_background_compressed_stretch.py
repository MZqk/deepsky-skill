"""背景压缩拉伸回归测试（无星层专用）。

背景：含星图的 `target_bg` 是相对**全图量程**定义的。那里稀疏亮星把 max
抬到天空的 480 倍（实测 0.94 vs 0.002），背景落在量程 0.2%，0.035 是「比
背景高一档」的合理电平。去星后该前提失效：背景升到量程 24%，标准 GHS 的 SP
落在背景上，低背景目标不可达（实测 f(sky) 在 log_d 0→20 单调 0.067→0.950，
拟合撞上界后整幅被抬到 0.578 泛灰）。

本曲线绕开 SP：直接以实测天空为黑点、稳健高分位锚为白点，背景可以真正压黑。
仅在**拉伸源是无星层**时启用，含星路径标定保持原样。
"""

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from artifact_gates import check_channel_clipping  # noqa: E402
from stretch import background_compressed_stretch  # noqa: E402


def starless_galaxy(size=256, seed=991):
    """背景占量程约 1/4 的无星层，模拟 M33 去星后的分布。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size]
    sky = 0.002
    img = np.full((size, size), sky, dtype=np.float64)
    img += rng.normal(0, 1.2e-4, (size, size))
    r = np.sqrt((xx - size / 2.0) ** 2 + (yy - size / 2.0) ** 2)
    disk = 3.0 * np.exp(-r / 14.0) + 1.2 * np.exp(-r / 42.0)
    img += disk * (sky * 0.05)
    rgb = np.empty((size, size, 3), dtype=np.float64)
    rgb[..., 0] = img * 1.05
    rgb[..., 1] = img * 1.00
    rgb[..., 2] = img * 0.95
    return np.clip(rgb, 0, None).astype(np.float32)


class BackgroundCompressedStretchTests(unittest.TestCase):
    def test_places_background_at_requested_level(self):
        image = starless_galaxy()
        out, report = background_compressed_stretch(image, target_bg=0.03)
        self.assertAlmostEqual(report["actual_background"], 0.03, delta=0.01)
        self.assertLessEqual(float(np.median(out[..., 0])), 0.06)

    def test_separates_subject_from_background(self):
        image = starless_galaxy()
        out, _ = background_compressed_stretch(image, target_bg=0.03)
        lum_src = image.mean(axis=2)
        lum_out = out.mean(axis=2)
        size = lum_src.shape[0]
        yy, xx = np.mgrid[0:size, 0:size]
        r = np.hypot(yy - size / 2.0, xx - size / 2.0)
        disk = r <= size * 0.18
        bg = r > size * 0.40
        before = float(np.median(lum_src[disk])) / float(np.median(lum_src[bg]))
        after = float(np.median(lum_out[disk])) / float(np.median(lum_out[bg]))
        self.assertGreater(after, before * 1.5)

    def test_does_not_clip_channels(self):
        """保比例重建必须避开 CHANNEL_CLIPPING 门禁。

        共享增益会把相对亮度最高的通道顶出 1.0；按通道比值重建时，
        上限需为 1/max(通道比值)。
        """
        image = starless_galaxy()
        out, _ = background_compressed_stretch(image, target_bg=0.03)
        gate = check_channel_clipping(out)
        self.assertFalse(gate["escalate"], gate)
        self.assertLess(float((out >= 0.995).sum()), 25)

    def test_preserves_internal_structure(self):
        image = starless_galaxy()
        out, _ = background_compressed_stretch(image, target_bg=0.03)
        lum = out.mean(axis=2)
        size = lum.shape[0]
        yy, xx = np.mgrid[0:size, 0:size]
        r = np.hypot(yy - size / 2.0, xx - size / 2.0)
        disk = lum[r <= size * 0.18]
        iqr = float(np.percentile(disk, 75) - np.percentile(disk, 25))
        self.assertGreater(iqr, 0.01)

    def test_rejects_invalid_bounds(self):
        image = starless_galaxy()
        with self.assertRaises(ValueError):
            background_compressed_stretch(image, target_bg=0.0)
        with self.assertRaises(ValueError):
            background_compressed_stretch(image, target_bg=0.9, ceiling=0.5)
        with self.assertRaises(ValueError):
            background_compressed_stretch(image, target_bg=0.03, protect=1.5)

    def test_rejects_all_zero_input(self):
        with self.assertRaises(ValueError):
            background_compressed_stretch(np.zeros((32, 32, 3), np.float32))

    def test_accepts_grayscale(self):
        image = starless_galaxy().mean(axis=2)
        out, report = background_compressed_stretch(image, target_bg=0.03)
        self.assertEqual(out.shape, image.shape)
        self.assertIn("measured_sky", report)

    def test_reports_diagnostics(self):
        image = starless_galaxy()
        _out, report = background_compressed_stretch(image, target_bg=0.03)
        for key in ("measured_sky", "anchor", "span", "sky_over_anchor",
                    "shoulder_start", "actual_background"):
            self.assertIn(key, report)
        self.assertGreater(report["anchor"], report["measured_sky"])


if __name__ == "__main__":
    unittest.main()