import sys
from pathlib import Path
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from star_tools import inpaint_chroma_guard, detect_stars_multiscale, separate_stars


def test_inpaint_chroma_guard_restores_neutral_color():
    """验证 Chroma Guard 在保持亮度的同时，将星坑色彩约束至周围平滑背景。"""
    h, w = 100, 100
    # 模拟中性均匀背景 (R=G=B=0.01)
    img = np.full((h, w, 3), 0.01, dtype=np.float32)

    # 模拟在 (50, 50) 处星坑 Inpaint 残留的严重红橙偏色
    # 比如真实星坑被红色 PSF 扩散翼填满: R=0.025, G=0.010, B=0.005
    yy, xx = np.ogrid[:h, :w]
    star_hole = ((yy - 50) ** 2 + (xx - 50) ** 2) <= 16
    img[star_hole, 0] = 0.025
    img[star_hole, 1] = 0.010
    img[star_hole, 2] = 0.005

    mask = star_hole.astype(np.float32)

    # 未处理前，星坑中心 R/G 为 2.5
    pre_ratio = img[50, 50, 0] / img[50, 50, 1]
    assert pre_ratio > 2.0

    # 应用 Chroma Guard
    guarded = inpaint_chroma_guard(img, mask, blur_sigma=10.0)

    # 处理后，星坑中心 R/G 应当收敛到周围平滑背景的 ~1.0
    post_ratio = guarded[50, 50, 0] / guarded[50, 50, 1]
    assert abs(post_ratio - 1.0) < 0.15

    # 且亮度与原有 Inpaint 相当（不超过 10% 偏差）
    pre_lum = 0.2126 * img[50, 50, 0] + 0.7152 * img[50, 50, 1] + 0.0722 * img[50, 50, 2]
    post_lum = 0.2126 * guarded[50, 50, 0] + 0.7152 * guarded[50, 50, 1] + 0.0722 * guarded[50, 50, 2]
    assert abs(post_lum - pre_lum) / pre_lum < 0.1


def test_galaxy_nuclear_core_component_exclusion():
    """验证传入 galaxy_center 时，中心连通域核球被自然排除，周围星点正常检测。"""
    h, w = 150, 150
    img = np.full((h, w), 0.002, dtype=np.float32)

    # 1. 在中心 (75, 75) 放置一个面积显著的星系核球 (高光且等效直径 ~16px)
    yy, xx = np.ogrid[:h, :w]
    core_mask = ((yy - 75) ** 2 + (xx - 75) ** 2) <= (8 ** 2)
    img[core_mask] = 0.08  # 显著高光

    # 2. 在外围放置几个正常点源恒星 (半径 2-3px)
    star1 = ((yy - 30) ** 2 + (xx - 30) ** 2) <= (2 ** 2)
    star2 = ((yy - 120) ** 2 + (xx - 110) ** 2) <= (2 ** 2)
    img[star1] = 0.06
    img[star2] = 0.05

    # 未指定 galaxy_center 时检测
    mask_no_gc, conf_no_gc, details_no_gc = detect_stars_multiscale(
        img, fwhm=3.0, return_details=True, galaxy_center=None
    )

    # 指定 galaxy_center 时检测
    mask_gc, conf_gc, details_gc = detect_stars_multiscale(
        img, fwhm=3.0, return_details=True, galaxy_center=(75.0, 75.0)
    )

    # 检查 details 中是否有组件被标记为 galaxy_nuclear_core
    core_rejected = [
        c for c in details_gc['components']
        if c.get('reject_reason') == 'galaxy_nuclear_core'
    ]
    assert len(core_rejected) >= 1

    # 核球中心位置在 mask_gc 中应为 0 (被排除保护，未打入星点掩膜)
    assert mask_gc[75, 75] == 0.0

    # 周围星点应仍然被保留
    assert mask_gc[30, 30] > 0.0
    assert mask_gc[120, 110] > 0.0
