#!/usr/bin/env python3
"""
Stacking Border Trimming & Dither Edge Correction (抖动叠加多通道边缘色差裁切)

原理：
  在经纬仪场旋、抖动跟踪 (Dithering) 或多次实况叠加 (Live Stacking) 中，
  感光芯片边缘的行和列由于只有部分子帧参与叠加，信噪比剧烈下降、背景均值出现断崖式跌落，
  并常常伴随红蓝单通道未对齐的色差边缘条纹 (Fringe / Stacking Artifact)。
  这些边缘如果直接进入 DBE / PCC，会严重带偏多项式背景拟合和颜色校准。
  本模块通过按行/列扫描各通道的统计一致性，自动切除未完全叠加的低质量边缘。
"""

from typing import Tuple, Optional
import numpy as np


def detect_stacking_borders(
    image: np.ndarray,
    max_crop_pct: float = 0.04,
    deviation_sigma: float = 2.5,
) -> Tuple[int, int, int, int]:
    """
    自动检测图像四边的抖动叠加无效边缘厚度。

    参数:
      image: (H, W) 或 (H, W, C) 图像
      max_crop_pct: 单边最大允许裁切比例 (默认最大 4%，防止误切有效天体)
      deviation_sigma: 偏离中心统计特性的标准差阈值
    返回:
      (top, bottom, left, right) 各边裁切像素数
    """
    source = np.asarray(image, dtype=np.float32)
    h, w = source.shape[:2]
    max_y = int(h * max_crop_pct)
    max_x = int(w * max_crop_pct)

    # 取画面中间安全区域计算参考背景统计
    safe_y0, safe_y1 = int(h * 0.2), int(h * 0.8)
    safe_x0, safe_x1 = int(w * 0.2), int(w * 0.8)
    center_roi = source[safe_y0:safe_y1, safe_x0:safe_x1]

    ref_median = np.median(center_roi, axis=(0, 1))
    ref_std = np.std(center_roi, axis=(0, 1)) + 1e-6

    def is_row_bad(row_idx: int) -> bool:
        row = source[row_idx, safe_x0:safe_x1]
        m = np.median(row, axis=0)
        # 如果均值严重跌落，或离参考中值超出阈值
        dev = np.abs(m - ref_median) / ref_std
        return bool(np.any(dev > deviation_sigma) or np.any(m < ref_median * 0.70))

    def is_col_bad(col_idx: int) -> bool:
        col = source[safe_y0:safe_y1, col_idx]
        m = np.median(col, axis=0)
        dev = np.abs(m - ref_median) / ref_std
        return bool(np.any(dev > deviation_sigma) or np.any(m < ref_median * 0.70))

    top = 0
    for y in range(max_y):
        if is_row_bad(y):
            top = y + 1
        else:
            break

    bottom = 0
    for y in range(max_y):
        if is_row_bad(h - 1 - y):
            bottom = y + 1
        else:
            break

    left = 0
    for x in range(max_x):
        if is_col_bad(x):
            left = x + 1
        else:
            break

    right = 0
    for x in range(max_x):
        if is_col_bad(w - 1 - x):
            right = x + 1
        else:
            break

    return top, bottom, left, right


def apply_stacking_border_trim(
    image: np.ndarray,
    max_crop_pct: float = 0.04,
) -> Tuple[np.ndarray, Tuple[int, int, int, int]]:
    """
    自动检测并裁切抖动边缘。
    返回: (裁切后图像, (top, bottom, left, right))
    """
    top, bottom, left, right = detect_stacking_borders(image, max_crop_pct=max_crop_pct)
    h, w = image.shape[:2]
    y1 = h - bottom if bottom > 0 else h
    x1 = w - right if right > 0 else w
    y0 = top
    x0 = left

    if y0 >= y1 or x0 >= x1:
        return image, (0, 0, 0, 0)

    cropped = image[y0:y1, x0:x1]
    return cropped, (top, bottom, left, right)
