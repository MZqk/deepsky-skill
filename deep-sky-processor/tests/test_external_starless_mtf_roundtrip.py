"""「先 MTF 拉伸 → StarNet2 → 逆 MTF 回线性」配方的单元测试。

背景（实测）：极暗 Duo-Band 线性母版直接量化为 16-bit 时，星云区 G 通道只有
23 counts（占满量程 0.035%），量化跨度仅 70 级。喂给 StarNet2 后其输出在**所有
低于 200 counts 的亮度档**把 G 100% 归零，而 R 存活 —— 拉伸后整片星云变纯红。

实测修复效果（真实母版 + /usr/local/bin/starnet2 -s 128）：

| 阶段 | R/G | B/G | G 归零 |
|---|---|---|---|
| 源 | 3.272 | 0.830 | 0.08% |
| MTF 载荷 | 3.149 | 0.833 | 0.08% |
| StarNet2 输出（MTF 域） | 3.175 | 0.839 | 0.05% |
| 逆 MTF 回线性 | 3.294 | 0.837 | 0.05% |
| 线性对照（同 CLI） | ∞ | — | **99.4%** |

本文件锁住：逆变换的数学正确性、**m=0.5 的恒等陷阱**、通道比值守恒、
量化损失确实被降低、以及域契约（sidecar / stride / -m vs -n / resume_cmd）。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import tifffile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from stretch import (  # noqa: E402
    derive_mtf_midtones,
    inverse_mtf_stretch,
    mtf_stretch,
)
from neural_star_bridge import (  # noqa: E402
    STARNET_PAYLOAD_META,
    build_bridge_run_instructions,
    export_starless_payload,
    read_starnet_payload_meta,
    write_starnet_payload_meta,
)


def _dark_nebula(h=120, w=160, bg=0.0004, amp=(0.0030, 0.0011, 0.0009), seed=11):
    yy, xx = np.mgrid[:h, :w]
    neb = np.exp(-(((yy - h * 0.55) ** 2 + (xx - w * 0.5) ** 2)
                   / (2 * (min(h, w) * 0.22) ** 2))).astype(np.float32)
    img = np.full((h, w, 3), bg, np.float32)
    img += neb[..., None] * np.array(amp, np.float32)
    return np.clip(img, 0, 1)


class InverseMtfTests(unittest.TestCase):
    def test_round_trip_is_exact(self):
        x = np.linspace(0.0, 1.0, 257, dtype=np.float32)
        for m in (0.02, 0.1, 0.25, 0.4):
            back = inverse_mtf_stretch(mtf_stretch(x, midtones=m), midtones=m)
            self.assertTrue(np.allclose(back, x, atol=1e-5),
                            f"m={m} 往返不闭合，最大偏差 {np.abs(back - x).max()}")

    def test_half_midtones_is_identity_trap(self):
        """m=0.5 时 MTF 是恒等映射——必须显式锁住，否则「默认 0.5」会让修复静默失效。"""
        x = np.linspace(0.0, 1.0, 129, dtype=np.float32)
        self.assertTrue(np.allclose(mtf_stretch(x, midtones=0.5), x, atol=1e-6))
        self.assertTrue(np.allclose(inverse_mtf_stretch(x, midtones=0.5), x, atol=1e-6))

    def test_no_pole_on_unit_interval(self):
        """分母 D(y) 在 y∈[0,1]、m∈(0,1) 上恒正，输出必须有限且单调。"""
        y = np.linspace(0.0, 1.0, 501, dtype=np.float32)
        for m in (0.02, 0.1, 0.3, 0.5, 0.7, 0.9):
            out = inverse_mtf_stretch(y, midtones=m)
            self.assertTrue(np.all(np.isfinite(out)), f"m={m} 出现非有限值")
            self.assertGreaterEqual(float(out.min()), 0.0)
            self.assertLessEqual(float(out.max()), 1.0)
            self.assertTrue(np.all(np.diff(out) >= -1e-6), f"m={m} 非单调")

    def test_midtones_out_of_range_is_clamped(self):
        x = np.linspace(0, 1, 65, dtype=np.float32)
        for m in (0.0, -1.0, 1.0, 2.0):
            out = inverse_mtf_stretch(x, midtones=m)
            self.assertTrue(np.all(np.isfinite(out)))

    def test_channel_ratio_is_preserved(self):
        """正+逆往返后通道比值应基本不变（MTF 对三通道用同一标量 m）。"""
        img = _dark_nebula()
        m = derive_mtf_midtones(img)
        back = inverse_mtf_stretch(mtf_stretch(img, midtones=m), midtones=m)
        lum = back.mean(axis=2)
        mask = lum > np.percentile(lum, 60)
        ratio_in = float(np.median(img[..., 0][mask]) / np.median(img[..., 1][mask]))
        ratio_out = float(np.median(back[..., 0][mask]) / np.median(back[..., 1][mask]))
        self.assertLess(abs(ratio_out - ratio_in) / ratio_in, 0.01)


class DeriveMidtonesTests(unittest.TestCase):
    def test_returns_below_half_and_within_clamp(self):
        img = _dark_nebula()
        m = derive_mtf_midtones(img)
        self.assertLess(m, 0.5)
        self.assertGreaterEqual(m, 0.02)
        self.assertLessEqual(m, 0.45)

    def test_lifts_background(self):
        img = _dark_nebula()
        m = derive_mtf_midtones(img)
        before = float(np.median(img.mean(axis=2)))
        after = float(np.median(mtf_stretch(img, midtones=m).mean(axis=2)))
        self.assertGreater(after, before * 10.0)

    def test_blank_image_falls_back(self):
        self.assertLess(derive_mtf_midtones(np.zeros((16, 16, 3), np.float32)), 0.5)


class QuantizationLossTests(unittest.TestCase):
    def test_mtf_increases_retained_quantization_levels(self):
        """② 的核心依据：拉伸后弱通道在 16-bit 下保留的级数显著增多。"""
        img = _dark_nebula()
        lum = img.mean(axis=2)
        mask = lum > np.percentile(lum, 60)

        plain = (np.clip(img, 0, 1) * 65535.0).round().astype(np.uint16)
        m = derive_mtf_midtones(img)
        lifted = (mtf_stretch(img, midtones=m) * 65535.0).round().astype(np.uint16)

        def span(u16, c):
            reg = u16[..., c][mask].astype(np.int64)
            return int(np.percentile(reg, 99) - np.percentile(reg, 1))

        for c in (1, 2):  # G / B —— 弱通道才是被量化吃掉的那个
            self.assertGreater(span(lifted, c), span(plain, c) * 3,
                               f"通道 {c} 拉伸后量化级数未显著改善")


class PayloadExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_linear_domain_is_backward_compatible(self):
        img = _dark_nebula()
        path = str(Path(self.tmp) / "p_linear.tif")
        export_starless_payload(img, path)
        back = tifffile.imread(path)
        self.assertEqual(back.dtype, np.uint16)
        self.assertEqual(back.shape, img.shape)
        expect = (np.clip(img, 0, 1) * 65535.0).round().astype(np.uint16)
        self.assertTrue(np.array_equal(back, expect))

    def test_mtf_domain_lifts_weak_channel(self):
        img = _dark_nebula()
        path = str(Path(self.tmp) / "p_mtf.tif")
        m = derive_mtf_midtones(img)
        export_starless_payload(img, path, domain="mtf", midtones=m)
        back = tifffile.imread(path).astype(np.float64)
        plain = (np.clip(img, 0, 1) * 65535.0).round().astype(np.float64)
        # G 通道中位数应被显著抬高
        self.assertGreater(float(np.median(back[..., 1])),
                           float(np.median(plain[..., 1])) * 5.0)

    def test_mtf_domain_without_midtones_uses_default(self):
        img = _dark_nebula()
        path = str(Path(self.tmp) / "p_default.tif")
        export_starless_payload(img, path, domain="mtf")
        self.assertEqual(tifffile.imread(path).dtype, np.uint16)


class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_round_trip(self):
        write_starnet_payload_meta(self.tmp, domain="mtf", midtones=0.02,
                                   shadows=0.0, source_median=0.00027)
        meta = read_starnet_payload_meta(self.tmp)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["domain"], "mtf")
        self.assertAlmostEqual(meta["midtones"], 0.02)

    def test_accepts_explicit_json_path(self):
        write_starnet_payload_meta(self.tmp, domain="mtf", midtones=0.05)
        meta = read_starnet_payload_meta(str(Path(self.tmp) / STARNET_PAYLOAD_META))
        self.assertEqual(meta["domain"], "mtf")

    def test_missing_returns_none(self):
        self.assertIsNone(read_starnet_payload_meta(self.tmp, None, ""))

    def test_corrupt_returns_none(self):
        path = Path(self.tmp) / STARNET_PAYLOAD_META
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_starnet_payload_meta(self.tmp))


class BridgeInstructionsTests(unittest.TestCase):
    def _inst(self, **kwargs):
        env = {"available": True, "executable": "/usr/local/bin/starnet2",
               "supports_unscreen": True}
        base = dict(payload_path="/tmp/p.tif", output_starless_path="/tmp/s.tif",
                    input_fits_path="/tmp/i.fits", final_output_path="/tmp/o.jpg",
                    env_info=env, work_dir="/tmp/w")
        base.update(kwargs)
        return build_bridge_run_instructions(**base)

    def test_stride_defaults_to_128(self):
        inst = self._inst()
        self.assertIn("-s 128", inst["external_starnet_command"])
        self.assertNotIn("-s 256", inst["external_starnet_command"])
        self.assertEqual(inst["stride"], 128)

    def test_stride_can_be_overridden(self):
        inst = self._inst(stride=64)
        self.assertIn("-s 64", inst["external_starnet_command"])

    def test_mtf_domain_uses_unscreen(self):
        inst = self._inst(domain="mtf")
        self.assertIn("-n", inst["external_starnet_command"])
        self.assertNotIn(" -m ", inst["external_starnet_command"])

    def test_linear_domain_uses_additive_mask(self):
        inst = self._inst(domain="linear")
        self.assertIn(" -m ", inst["external_starnet_command"])
        self.assertNotIn(" -n ", inst["external_starnet_command"])

    def test_resume_cmd_carries_domain_and_midtones(self):
        inst = self._inst(domain="mtf", midtones=0.02)
        cmd = inst["resume_pipeline_command"]
        self.assertIn("--external-starless-domain mtf", cmd)
        self.assertIn("--external-starless-midtones 0.02", cmd)

    def test_resume_cmd_omits_midtones_when_unknown(self):
        inst = self._inst(domain="linear")
        self.assertIn("--external-starless-domain linear", inst["resume_pipeline_command"])
        self.assertNotIn("--external-starless-midtones", inst["resume_pipeline_command"])

    def test_without_unscreen_support_still_emits_valid_command(self):
        env = {"available": True, "executable": "/usr/local/bin/starnet2",
               "supports_unscreen": False}
        inst = build_bridge_run_instructions(
            payload_path="/tmp/p.tif", output_starless_path="/tmp/s.tif",
            input_fits_path="/tmp/i.fits", final_output_path="/tmp/o.jpg",
            env_info=env, work_dir="/tmp/w", domain="mtf")
        cmd = inst["external_starnet_command"]
        self.assertIn("-i", cmd)
        self.assertIn("-o", cmd)
        self.assertNotIn("-n", cmd)


if __name__ == "__main__":
    unittest.main()
