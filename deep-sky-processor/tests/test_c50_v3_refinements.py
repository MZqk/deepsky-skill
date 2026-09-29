import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gradient_removal import (
    normalize_background_subtracted,
    estimate_background_polynomial,
)
from enhance import apply_clahe
from style_tools import apply_professional_style


class TestC50V3Refinements:
    def test_normalize_background_subtracted_per_channel_alignment(self):
        """测试 3 通道图像各通道黑点基线对齐至相同 pedestal。"""
        rng = np.random.default_rng(42)
        h, w = 100, 100
        # 构造通道背景中位数有微小偏差的图像
        img = np.zeros((h, w, 3), dtype=np.float32)
        img[..., 0] = rng.normal(loc=0.0003, scale=0.0001, size=(h, w))
        img[..., 1] = rng.normal(loc=-0.0001, scale=0.0001, size=(h, w))
        img[..., 2] = rng.normal(loc=-0.0002, scale=0.0001, size=(h, w))
        # 加上一个明亮星
        img[50, 50, :] = 0.5

        norm = normalize_background_subtracted(img)
        # 每个通道的背景中位数应当严格对齐至 pedestal（约 0.002 ~ 0.05）
        med0 = float(np.median(norm[..., 0]))
        med1 = float(np.median(norm[..., 1]))
        med2 = float(np.median(norm[..., 2]))
        assert abs(med0 - med1) < 1e-4
        assert abs(med1 - med2) < 1e-4

    def test_estimate_background_polynomial_center_guard(self):
        """测试中心缺乏有效样本时自动降阶至 degree=1，防止形成假穹顶。"""
        h, w = 120, 120
        # 创建一个带有线性梯度的背景
        yy, xx = np.mgrid[:h, :w]
        plane = 0.05 + 0.0002 * xx + 0.0001 * yy
        # 中心大范围排除蒙版
        exclusion_mask = np.zeros((h, w), dtype=bool)
        exclusion_mask[20:100, 20:100] = True  # 占满中心

        bg = estimate_background_polynomial(plane, degree=2, sample_spacing=10, exclusion_mask=exclusion_mask)
        # 拟合结果应该平整接近原平面，中心不应该有非线性的巨大穹顶/凹坑
        assert bg.shape == (h, w)
        assert abs(bg[60, 60] - plane[60, 60]) < 0.01

    def test_apply_clahe_shadow_rolloff(self):
        """测试 CLAHE 的 shadow_rolloff 能有效保护暗背景不被无差别拉伸。"""
        h, w = 80, 80
        img = np.zeros((h, w, 3), dtype=np.float32)
        # 暗背景区域 (0.02)
        img[:40, :] = 0.02
        # 星云主体区域 (0.35) 带有细微纹理
        yy, xx = np.mgrid[:40, :w]
        img[40:, :, :] = (0.35 + 0.05 * np.sin(xx * 0.5))[..., None]

        res = apply_clahe(img, clip_limit=0.02, kernel_size=16, highlight_rolloff=True, shadow_rolloff=True)
        # 暗背景区域增益应当完全衰减为 1.0 (增量为 0)
        assert np.allclose(res[:40, :], img[:40, :], atol=1e-4)
        # 星云主体区域对比度应当被增强
        assert not np.allclose(res[40:, :], img[40:, :], atol=1e-3)

    def test_style_tools_background_threshold_cap_and_oiii_preservation(self):
        """测试风格定调中的背景阈值上限约束与 OIII 电离区防去饱和。"""
        h, w = 60, 60
        img = np.zeros((h, w, 3), dtype=np.float32)
        # 边缘被拉高到 0.40 (模拟边缘光害或假性亮边)
        img[:10, :] = 0.40
        img[-10:, :] = 0.40
        img[:, :10] = 0.40
        img[:, -10:] = 0.40
        # 中央区域是 OIII 结构：G 和 B 相对活跃，如 R=0.18, G=0.15, B=0.15
        img[20:40, 20:40] = [0.18, 0.15, 0.15]

        graded, selected, reasoning = apply_professional_style(
            img,
            style="dramatic_nebula",
            target_type="emission_nebula",
            color_mode="emission",
            strength=1.0,
        )
        # 中央 OIII 区域 G/B 信号不应该被 desaturate 漂白成灰色 (G 与 B 依然维持显著色彩)
        oiii_crop = graded[25:35, 25:35]
        # (G+B)/(2R) 应该依然健康
        ratio = (oiii_crop[..., 1] + oiii_crop[..., 2]) / (2.0 * np.maximum(oiii_crop[..., 0], 1e-6))
        assert np.mean(ratio) > 0.65
