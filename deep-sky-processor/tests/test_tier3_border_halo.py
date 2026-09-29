#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import numpy as np
import pytest
from border_tools import detect_stacking_borders, apply_stacking_border_trim
from star_tools import apply_star_halo_guard


def test_detect_and_trim_stacking_borders():
    h, w = 100, 100
    img = np.full((h, w, 3), 0.1, dtype=np.float32)

    # 1. 干净图像无黑边
    t, b, l, r = detect_stacking_borders(img)
    assert (t, b, l, r) == (0, 0, 0, 0)
    trimmed, bbox = apply_stacking_border_trim(img)
    assert trimmed.shape == img.shape

    # 2. 人工制造边缘断崖（例如顶部2行、左侧3列完全未曝光或叠加漂移）
    img_dither = img.copy()
    img_dither[:2, :, :] = 0.01
    img_dither[:, :3, :] = 0.01

    t, b, l, r = detect_stacking_borders(img_dither)
    assert t >= 2
    assert l >= 3
    trimmed, (t_res, b_res, l_res, r_res) = apply_stacking_border_trim(img_dither)
    assert trimmed.shape[0] == h - t_res - b_res
    assert trimmed.shape[1] == w - l_res - r_res
    assert np.all(trimmed >= 0.09)


def test_apply_star_halo_guard():
    # 构造一张带有中性背景 (0.1, 0.1, 0.1) 的无星图
    h, w = 80, 80
    starless = np.full((h, w, 3), 0.1, dtype=np.float32)

    # 在中心模拟亮星残留的强烈色差红色光晕 (Halo)
    cy, cx = 40, 40
    y, x = np.ogrid[:h, :w]
    r = np.sqrt((y - cy) ** 2 + (x - cx) ** 2)
    halo_zone = (r >= 2.0) & (r <= 8.0)
    starless[halo_zone, 0] = 0.35  # 红色通道异常突起

    # 对应的星点图
    stars = np.zeros((h, w, 3), dtype=np.float32)
    stars[r < 4.0, :] = 0.9  # 强亮星

    # 运行 star halo guard
    cleaned = apply_star_halo_guard(starless, stars=stars, fwhm=3.0, halo_threshold=0.5)

    assert cleaned.shape == starless.shape
    assert cleaned.min() >= 0.0
    assert cleaned.max() <= 1.0

    # 检验色差红色突起是否被抑制平滑至接近中性
    halo_red_ratio_before = np.mean(starless[halo_zone, 0] / np.maximum(starless[halo_zone, 1], 1e-6))
    halo_red_ratio_after = np.mean(cleaned[halo_zone, 0] / np.maximum(cleaned[halo_zone, 1], 1e-6))

    assert halo_red_ratio_before > 2.5
    assert halo_red_ratio_after < halo_red_ratio_before * 0.6  # 红色偏置大幅平整接近 1.0


def test_star_halo_guard_edge_cases():
    # 灰度图直接返回
    gray = np.full((50, 50), 0.1, dtype=np.float32)
    res_gray = apply_star_halo_guard(gray)
    assert np.array_equal(res_gray, gray)

    # 全纯平三通道图直接返回
    flat = np.full((50, 50, 3), 0.1, dtype=np.float32)
    res_flat = apply_star_halo_guard(flat)
    assert np.array_equal(res_flat, flat)
