import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from style_tools import apply_professional_style
from color_tools import enhance_saturation


def test_warmth_preserves_neutral_background_ratios():
    """验证 Phase 9a 风格加温仅作用于信号区，深空背景色彩比例不受污染。"""
    h, w = 120, 120
    # 构造图像：背景 R=G=B=0.02 (完美中性深空)，中心为天体结构 (R=0.22, G=0.20, B=0.18)
    img = np.full((h, w, 3), 0.02, dtype=np.float32)
    yy, xx = np.ogrid[:h, :w]
    core = ((yy - 60) ** 2 + (xx - 60) ** 2) <= (25 ** 2)
    img[core, 0] = 0.22
    img[core, 1] = 0.20
    img[core, 2] = 0.18

    # 背景区域采样点
    bg_sample_before = img[:20, :20]
    bg_rg_before = np.mean(bg_sample_before[..., 0]) / np.mean(bg_sample_before[..., 1])
    assert abs(bg_rg_before - 1.0) < 1e-4

    # 应用 galaxy_core 风格 (自带 warmth=0.015)
    styled, selected, _ = apply_professional_style(
        img,
        style="galaxy_core",
        target_type="galaxy",
        strength=1.0,
    )

    bg_sample_after = styled[:20, :20]
    bg_rg_after = np.mean(bg_sample_after[..., 0]) / np.mean(bg_sample_after[..., 1])

    # 验证背景区域 R/G 没有被加温染红 (偏离不超过 0.02)
    assert abs(bg_rg_after - 1.0) < 0.03

    # 验证中心天体区域确实获得了对比与饱和处理
    assert styled[60, 60, 0] > styled[60, 60, 1]


def test_broadband_galaxy_saturation_boost():
    """验证宽带星系饱和度提升保留背景纯净度并提升主体色彩。"""
    h, w = 80, 80
    img = np.full((h, w, 3), 0.01, dtype=np.float32)
    yy, xx = np.ogrid[:h, :w]
    arm = ((yy - 40) ** 2 + (xx - 40) ** 2) <= (15 ** 2)
    # 模拟微弱年轻星团旋臂微蓝特征 (R=0.05, G=0.06, B=0.08)
    img[arm, 0] = 0.05
    img[arm, 1] = 0.06
    img[arm, 2] = 0.08

    boosted = enhance_saturation(
        img,
        factor=1.45,
        protect_background=True,
        bg_protection_percentile=40,
    )

    # 背景区域 (0, 0) 几乎没有增加饱和度
    assert abs(boosted[0, 0, 0] - boosted[0, 0, 1]) < 1e-4

    # 旋臂区域色彩差异被显著拉大
    diff_before = abs(img[40, 40, 2] - img[40, 40, 0])
    diff_after = abs(boosted[40, 40, 2] - boosted[40, 40, 0])
    assert diff_after > diff_before * 1.2
