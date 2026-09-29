"""`apply_clahe` 的自适应 gamma 压缩亮度掩版。

背景：原实现的"高光/暗部保护"是**硬编码分段线性斜坡**——高光 >0.65 渐弱、
暗部 <0.16 渐弱，满强度区间固定为 [0.16, 0.65]，与图像直方图/动态范围无关。
亮核在 0.3~0.65 区间仍是满强度，会被无差别局部均衡压平成无特征斑块。

修法：把固定斜坡与**自适应高光渐弱掩版相乘**（下降型 taper，p_low 以下权重恒为 1）。
taper ∈ [0,1]，故新权重恒 ≤ 旧权重——相对历史行为只会更保守。函数默认
`mask_gamma=None` 保持旧行为，新掩版只在调用方显式传参时启用
（`test_c50_v3_refinements.py` 直接调 `apply_clahe(img, clip_limit=0.02, ...)`，
默认值必须不变）。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from enhance import apply_clahe, _clahe_rolloff  # noqa: E402


def _nebula_scene(height=96, width=128, seed=9):
    """暗背景 + 带纹理的亮核 + 中间调星云。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    base = np.full((height, width), 0.03, dtype=np.float32)
    nebula = 0.35 * np.exp(-(((xx - width * 0.5) / (width * 0.30)) ** 2
                             + ((yy - height * 0.5) / (height * 0.30)) ** 2))
    core = 0.55 * np.exp(-(((xx - width * 0.5) / (width * 0.08)) ** 2
                           + ((yy - height * 0.5) / (height * 0.08)) ** 2))
    texture = 0.02 * np.sin(xx * 0.7) * np.cos(yy * 0.5)
    gray = np.clip(base + nebula + core + texture, 0, 1)
    return np.clip(np.stack([gray, gray * 0.85, gray * 0.75], axis=-1), 0, 1).astype(np.float32)


class ClaheAdaptiveMaskTests(unittest.TestCase):
    def test_function_default_is_legacy(self):
        """函数默认（mask_gamma=None）必须与显式 None 逐位相同。"""
        image = _nebula_scene()
        a = apply_clahe(image, clip_limit=0.02, kernel_size=16)
        b = apply_clahe(image, clip_limit=0.02, kernel_size=16, mask_gamma=None)
        self.assertTrue(np.array_equal(a, b))

    def test_adaptive_mask_is_never_more_aggressive(self):
        """幂版 ∈ [0,1] ⇒ 新权重恒 ≤ 旧权重 ⇒ 改动量不会更大。"""
        image = _nebula_scene()
        legacy = apply_clahe(image, clip_limit=0.02, kernel_size=16)
        masked = apply_clahe(image, clip_limit=0.02, kernel_size=16, mask_gamma=2.0)
        d_legacy = np.abs(legacy - image)
        d_masked = np.abs(masked - image)
        self.assertFalse(np.array_equal(legacy, masked))
        self.assertLessEqual(float(d_masked.mean()), float(d_legacy.mean()) + 1e-9)
        self.assertTrue(np.all(d_masked <= d_legacy + 1e-6))

    def test_rolloff_weight_is_bounded_by_legacy(self):
        """直接对权重函数断言：加了自适应掩版之后逐像素不增。"""
        L = np.linspace(0.0, 1.0, 257)
        legacy = _clahe_rolloff(L)
        masked = _clahe_rolloff(L, mask_gamma=2.0)
        self.assertTrue(np.all(masked <= legacy + 1e-12))
        self.assertTrue(np.all(masked >= 0.0))

    def test_taper_only_affects_the_bright_window(self):
        """下降型 taper 的语义：p_low 以下权重不变，只有亮端被逐步保护。

        这条锁住一个真实踩过的坑：最初实现写成**上升型**幂版
        `((L-lo)/(hi-lo))**g`，会在整个中低亮度区间给出接近 0 的权重，
        等于把 CLAHE 全图关掉——实测 median 掉了 25%。
        """
        L = np.linspace(0.0, 1.0, 2001)
        legacy = _clahe_rolloff(L)
        masked = _clahe_rolloff(
            L, mask_gamma=2.0, mask_low_pctl=90.0, mask_high_pctl=99.9
        )
        lo = float(np.percentile(L, 90.0))
        # p90 以下：权重与历史**完全相同**（背景/主体不受影响）
        below = L < lo - 1e-9
        self.assertTrue(np.array_equal(masked[below], legacy[below]))
        # 亮端：权重显著低于历史（亮核被保护）。
        # 注意窗口要选在「历史权重仍非零、而 taper 已开始生效」的区间——
        # L>=0.95 时历史 highlight_rolloff 已经归零，两者都是 0，断言会退化。
        top = (L >= lo) & (L < 0.94)
        self.assertTrue(top.any())
        self.assertLess(float(masked[top].mean()), float(legacy[top].mean()))

    def test_background_unchanged_and_core_protected(self):
        """端到端：暗背景改动量为 0，亮核改动量显著。"""
        image = _nebula_scene()
        legacy = apply_clahe(image, clip_limit=0.02, kernel_size=16)
        masked = apply_clahe(image, clip_limit=0.02, kernel_size=16, mask_gamma=2.0)
        L = image.mean(axis=2)
        d = np.abs(masked - legacy).mean(axis=2)
        bg = L <= np.percentile(L, 50)
        core = L >= np.percentile(L, 99.5)
        self.assertLess(float(d[bg].mean()), 1e-6)
        self.assertGreater(float(d[core].mean()), 1e-3)

    def test_median_is_not_shifted(self):
        """保守性判据：自适应掩版不得整体改变画面亮度。"""
        image = _nebula_scene()
        legacy = apply_clahe(image, clip_limit=0.02, kernel_size=16)
        masked = apply_clahe(image, clip_limit=0.02, kernel_size=16, mask_gamma=2.0)
        m_legacy = float(np.median(legacy.mean(axis=2)))
        m_masked = float(np.median(masked.mean(axis=2)))
        self.assertLess(abs(m_masked - m_legacy) / max(m_legacy, 1e-9), 0.01)

    def test_highlight_rolloff_switch_is_honoured(self):
        """补上此前完全缺失的 highlight_rolloff 测试。"""
        image = _nebula_scene()
        L = image.mean(axis=2)
        bright = L >= np.percentile(L, 90)
        on = apply_clahe(image, clip_limit=0.02, kernel_size=16,
                         highlight_rolloff=True, shadow_rolloff=True)
        off = apply_clahe(image, clip_limit=0.02, kernel_size=16,
                          highlight_rolloff=False, shadow_rolloff=True)
        d_on = np.abs(on - image).mean(axis=2)
        d_off = np.abs(off - image).mean(axis=2)
        # 关掉高光 rolloff 后，亮区被处理得更狠
        self.assertGreater(float(d_off[bright].mean()), float(d_on[bright].mean()))

    def test_grayscale_path_also_supports_mask(self):
        image = _nebula_scene()[..., 0]
        legacy = apply_clahe(image, clip_limit=0.02, kernel_size=16)
        masked = apply_clahe(image, clip_limit=0.02, kernel_size=16, mask_gamma=2.0)
        self.assertEqual(masked.shape, image.shape)
        self.assertLessEqual(
            float(np.abs(masked - image).mean()),
            float(np.abs(legacy - image).mean()) + 1e-9,
        )


if __name__ == "__main__":
    unittest.main()
