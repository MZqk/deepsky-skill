"""HDR 与 CLAHE 的二选一决策。

外部权威经验：「HDRMT 与 LHE 同时使用太过头，二选一」。本地 Phase 6 此前是
无条件串联。`decide_enhance_mode` 把它变成条件触发，但**保守默认仍是 both**
（= 历史行为），只在测到明确信号时才跳过一个。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from enhance import (  # noqa: E402
    CLAHE_MAX_HF_AMPLIFICATION,
    ENHANCE_MODES,
    decide_enhance_mode,
    measure_texture_snr,
    preview_clahe_hf_amplification,
)


class DecideEnhanceModeTests(unittest.TestCase):
    def test_modes_tuple_is_declared(self):
        self.assertEqual(
            set(ENHANCE_MODES), {"both", "hdr_only", "clahe_only", "none"}
        )

    def test_highlight_clip_with_dark_background_keeps_both(self):
        """高光过曝 + 暗背景：两者都需要。"""
        mode = decide_enhance_mode({
            "highlight_clip_ratio": 0.01, "p50": 0.08, "p99": 0.99, "span": 0.91,
        })
        self.assertEqual(mode, "both")

    def test_highlight_clip_with_bright_background_skips_clahe(self):
        """高光过曝 + 背景已亮：再叠 CLAHE 会放大噪声 → 只跑 HDR。"""
        mode = decide_enhance_mode({
            "highlight_clip_ratio": 0.01, "p50": 0.20, "p99": 0.99, "span": 0.79,
        })
        self.assertEqual(mode, "hdr_only")

    def test_no_highlight_risk_skips_hdr(self):
        """p99 < 0.80 → 没有可压的高光，HDR 近似空操作 → 只跑 CLAHE。"""
        mode = decide_enhance_mode({
            "highlight_clip_ratio": 0.0, "p50": 0.10, "p99": 0.60, "span": 0.50,
        })
        self.assertEqual(mode, "clahe_only")

    def test_low_global_span_prefers_clahe(self):
        """全局层次不足 → 优先局部对比。"""
        mode = decide_enhance_mode({
            "highlight_clip_ratio": 0.0, "p50": 0.45, "p99": 0.85, "span": 0.15,
        })
        self.assertEqual(mode, "clahe_only")

    def test_default_is_both(self):
        """无明确信号时保持历史行为（保守兜底）。"""
        mode = decide_enhance_mode({
            "highlight_clip_ratio": 0.001, "p50": 0.12, "p99": 0.90, "span": 0.78,
        })
        self.assertEqual(mode, "both")

    def test_missing_signals_default_to_both(self):
        """signals 缺失时不能抛异常，且回落到 both。"""
        self.assertEqual(decide_enhance_mode({}), "both")

    def test_span_is_derived_when_absent(self):
        mode = decide_enhance_mode({"p50": 0.45, "p99": 0.60})
        self.assertEqual(mode, "clahe_only")   # p99<0.80 先命中


class TextureNoiseGuardTests(unittest.TestCase):
    """噪声守卫：星云区纹理不比背景噪声多 → CLAHE 只会放大噪声成蜂窝斑块。

    实测分离度：有伪影的 Phase 6 输入 texture_snr = 1.24，干净的 ≥2.32。
    """

    def _base(self, **extra):
        signals = {"highlight_clip_ratio": 0.0, "p50": 0.0853,
                   "p99": 0.1756, "span": 0.0903}
        signals.update(extra)
        return signals

    def test_low_texture_snr_suppresses_clahe_only(self):
        """span<0.22 本会选 clahe_only；噪声主导时应降为 none。"""
        self.assertEqual(decide_enhance_mode(self._base()), "clahe_only")
        self.assertEqual(
            decide_enhance_mode(self._base(texture_snr=1.24)), "none"
        )

    def test_low_texture_snr_downgrades_both_to_hdr_only(self):
        signals = {"highlight_clip_ratio": 0.0, "p50": 0.12,
                   "p99": 0.90, "span": 0.78, "texture_snr": 1.2}
        self.assertEqual(decide_enhance_mode(signals), "hdr_only")

    def test_healthy_texture_snr_keeps_clahe(self):
        """负例：有结构（2.32~5.26）时必须保留 CLAHE。"""
        for snr in (2.32, 2.91, 5.26):
            self.assertEqual(
                decide_enhance_mode(self._base(texture_snr=snr)), "clahe_only"
            )

    def test_missing_texture_snr_does_not_trigger_guard(self):
        """负例：旧调用方不传 texture_snr → 行为与历史一致。"""
        self.assertEqual(decide_enhance_mode(self._base()), "clahe_only")
        self.assertEqual(
            decide_enhance_mode(self._base(texture_snr=None)), "clahe_only"
        )

    def test_non_finite_texture_snr_does_not_trigger_guard(self):
        for bad in (float("nan"), float("inf")):
            self.assertEqual(
                decide_enhance_mode(self._base(texture_snr=bad)), "clahe_only"
            )

    def test_guard_does_not_touch_hdr_only(self):
        """hdr_only 本来就不跑 CLAHE，守卫不该改动它。"""
        signals = {"highlight_clip_ratio": 0.01, "p50": 0.20,
                   "p99": 0.99, "span": 0.79, "texture_snr": 1.1}
        self.assertEqual(decide_enhance_mode(signals), "hdr_only")


class MeasureTextureSnrTests(unittest.TestCase):
    def test_noise_dominated_image_scores_low(self):
        """纯噪声图：星云区与背景都是噪声 → 比值 ≈1。"""
        rng = np.random.default_rng(5)
        flat = (0.05 + rng.normal(0, 0.002, (256, 256))).astype(np.float32)
        snr = measure_texture_snr(flat)
        self.assertIsNotNone(snr)
        self.assertLess(snr, 1.4)

    def test_structured_image_scores_high(self):
        """带真实结构的图：星云区高通能量明显高于背景。"""
        yy, xx = np.mgrid[:256, :256]
        base = np.full((256, 256), 0.02, np.float32)
        neb = 0.12 * np.exp(-(((yy - 128) ** 2 + (xx - 128) ** 2) / (2 * 60.0 ** 2)))
        texture = 0.01 * np.sin(xx * 0.35) * np.cos(yy * 0.30) * (neb > 0.01)
        img = (base + neb + texture).astype(np.float32)
        snr = measure_texture_snr(img)
        self.assertIsNotNone(snr)
        self.assertGreater(snr, 2.0)

    def test_uniform_image_returns_none(self):
        self.assertIsNone(measure_texture_snr(np.full((32, 32), 0.5, np.float32)))

    def test_empty_image_returns_none(self):
        self.assertIsNone(measure_texture_snr(np.zeros((0, 0), np.float32)))


class ClaheHfAmplificationGuardTests(unittest.TestCase):
    """CLAHE 的**实测**过度增强守卫。

    为什么需要实测判据：`equalize_adapthist` 的 `clip_limit` 在**窄直方图**上会饱和。
    实测某极暗发射星云（span=0.39）上 clip_limit 从 0.0003 到 0.003 给出**逐位相同**
    的输出（HF 放大恒为 1.85×、p75 恒为 0.440），只有到 0.006 才变化。
    **无法靠调小 clip_limit 减弱效果，只能二选一** —— 所以必须在预览上实测放大倍数。

    实测收益（真实母版）：跳过 CLAHE 后成片 p75 由 0.381 降到 0.173（−55%）、
    HF 比由 6.74 降到 3.62（−46%）。
    """

    def _nebula_scene(self, h=128, w=128, bg=0.02, peak=0.30, seed=5):
        """暗背景 + 中央弥散星云（带轻微纹理，避免常数图掩膜为空）。"""
        rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:h, 0:w]
        glow = np.exp(-(((yy - h / 2) ** 2 + (xx - w / 2) ** 2)
                        / (2 * (min(h, w) * 0.22) ** 2))).astype(np.float32)
        base = bg + peak * glow
        texture = 0.01 * np.sin(xx * 0.4) * np.cos(yy * 0.35)
        img = np.clip(base + texture, 0, 1)
        img = img + rng.normal(0, 2e-3, img.shape).astype(np.float32)
        return np.repeat(np.clip(img, 0, 1)[..., None], 3, axis=2).astype(np.float32)

    def test_returns_finite_amplification_above_one(self):
        """① 正常输入应给出 >1 的有限放大倍数。"""
        img = self._nebula_scene()
        amp = preview_clahe_hf_amplification(img, 0.003, 64,
                                             mask_gamma=2.0, mask_high_pctl=95.0)
        self.assertIsNotNone(amp)
        self.assertTrue(np.isfinite(amp))
        self.assertGreater(amp, 1.0)

    def test_constant_image_returns_none(self):
        """② 常数图无信号区/无高频 → None（调用方视为不触发守卫）。"""
        flat = np.full((64, 64, 3), 0.5, dtype=np.float32)
        self.assertIsNone(preview_clahe_hf_amplification(flat, 0.003, 64))

    def test_preview_downsampling_is_bounded(self):
        """③ 大图应在预览上评估（限幅 max_side），不因尺寸失败。"""
        img = self._nebula_scene(h=900, w=1200)
        amp = preview_clahe_hf_amplification(img, 0.003, 64, max_side=256)
        self.assertIsNotNone(amp)
        self.assertTrue(np.isfinite(amp))

    def test_threshold_is_above_one(self):
        """④ 阈值必须是"放大"语义（>1），否则任何 CLAHE 都会被误判。"""
        self.assertGreater(CLAHE_MAX_HF_AMPLIFICATION, 1.0)

    def test_gray_image_is_supported(self):
        """⑤ 灰度输入不应崩溃。"""
        gray = self._nebula_scene()[..., 0]
        amp = preview_clahe_hf_amplification(gray, 0.003, 64)
        self.assertTrue(amp is None or np.isfinite(amp))

    def test_amplification_is_monotonic_in_clip_limit(self):
        """⑥ 放大倍数随 clip_limit 单调不减（弱不变量，容忍饱和平台）。"""
        img = self._nebula_scene()
        lo = preview_clahe_hf_amplification(img, 0.0003, 64)
        hi = preview_clahe_hf_amplification(img, 0.02, 64)
        self.assertIsNotNone(lo)
        self.assertIsNotNone(hi)
        self.assertGreaterEqual(hi + 1e-6, lo)


if __name__ == "__main__":
    unittest.main()
