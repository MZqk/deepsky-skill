#!/usr/bin/env python3
"""
Deep-Sky Star Tools v2 (多尺度星点处理引擎)

原理：
  星点极其明亮，在拉伸和增强过程中容易盖过星云的暗弱细节。
  将星点和星云分离处理是现代深空后期的核心技巧之一。

v2 升级：
  - 多尺度星点检测（基于 FWHM 自动估计）
  - 连通域特征分析（圆度、尺寸、峰值、PSF）
  - 热像素/星云亮核/细丝过滤
  - OpenCV Telea/Navier-Stokes 修复替代高斯模糊
  - 星云高梯度区域自适应阈值
  - 检测置信度评估，低置信度时智能降级

用法:
  python star_tools.py separate <input> <output_starless> [options]
  python star_tools.py reduce <input> <output> [options]
  python star_tools.py combine <starless> <stars> <output> [options]
"""

import argparse
import sys
import warnings
import numpy as np
from scipy.ndimage import (
    gaussian_filter, median_filter, binary_dilation, binary_erosion,
    label as ndi_label, find_objects, maximum_filter, sobel,
    distance_transform_edt
)
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from skimage.morphology import disk, white_tophat, erosion, dilation

# OpenCV 用于 Telea/Navier-Stokes 修复
try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    warnings.warn("OpenCV 不可用，星点修复将回退到高斯模糊", RuntimeWarning)


# ══════════════════════════════════════════════════════════════
# FWHM 估计
# ══════════════════════════════════════════════════════════════

def estimate_fwhm(image, n_brightest=50, max_candidates=200,
                   min_separation=5, fit_radius=8):
    """
    从图像中估计星点的 FWHM（全宽半高，像素）。

    方法：
      1. 提取图像中亮于 p99.5 的候选局部极大值
      2. 要求候选点之间有最小间距（避免重复计数同一星的 PSF 尾部）
      3. 对每个候选提取 fit_radius 邻域，拟合 2D 高斯
      4. 计算 FWHM = 2.355 * mean(sigma_x, sigma_y)
      5. 取中值，用 MAD 剔除异常值

    返回: (fwhm_median, fwhm_std, n_used, n_candidates)
        fwhm_median: 估计的 FWHM（像素），失败时返回 3.0
        fwhm_std: FWHM 的标准差
        n_used: 实际用于估计的星数
        n_candidates: 候选星数
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)
    img_gray = np.asarray(img_gray, dtype=np.float64)

    # 1. 局部极大值检测（排除边缘）
    margin = fit_radius + 2
    interior = img_gray[margin:-margin, margin:-margin]
    local_max = maximum_filter(interior, size=min_separation)
    peak_mask = (interior == local_max) & (interior > np.percentile(img_gray, 99.5))
    y_peaks, x_peaks = np.where(peak_mask)
    y_peaks += margin
    x_peaks += margin

    candidates = list(zip(y_peaks, x_peaks))
    if len(candidates) == 0:
        return 3.0, 0.0, 0, 0

    # 按亮度排序，取最亮的 max_candidates 个
    candidate_vals = img_gray[y_peaks, x_peaks]
    sorted_idx = np.argsort(candidate_vals)[::-1][:max_candidates]
    candidates = [(y_peaks[i], x_peaks[i]) for i in sorted_idx]

    # 2. NMS：确保最小间距
    kept = []
    for cy, cx in candidates:
        too_close = False
        for ky, kx in kept:
            if (cy - ky) ** 2 + (cx - kx) ** 2 < min_separation ** 2:
                too_close = True
                break
        if not too_close:
            kept.append((cy, cx))
    candidates = kept[:n_brightest]
    n_candidates = len(candidates)

    if n_candidates < 3:
        return 3.0, 0.0, 0, n_candidates

    # 3. 对每个候选拟合 2D 高斯
    fwhm_vals = []
    h, w = img_gray.shape
    for cy, cx in candidates:
        y0, y1 = max(0, cy - fit_radius), min(h, cy + fit_radius + 1)
        x0, x1 = max(0, cx - fit_radius), min(w, cx + fit_radius + 1)
        patch = img_gray[y0:y1, x0:x1]
        if patch.size < 9:
            continue

        # 减去局部背景
        local_bg = np.percentile(patch, 10)
        patch_bg = patch - local_bg
        patch_bg = np.clip(patch_bg, 0, None)
        if patch_bg.max() < 1e-6:
            continue

        # 用矩方法估计高斯参数（快速近似）
        patch_norm = patch_bg / patch_bg.sum()
        yy, xx = np.mgrid[y0:y1, x0:x1]
        cx_est = (xx * patch_norm).sum()
        cy_est = (yy * patch_norm).sum()
        var_x = (xx ** 2 * patch_norm).sum() - cx_est ** 2
        var_y = (yy ** 2 * patch_norm).sum() - cy_est ** 2
        var_x = max(var_x, 0.25)
        var_y = max(var_y, 0.25)
        sigma = np.sqrt((var_x + var_y) / 2.0)
        fwhm = 2.355 * sigma
        fwhm_vals.append(fwhm)

    if len(fwhm_vals) < 3:
        return 3.0, 0.0, 0, n_candidates

    fwhm_vals = np.array(fwhm_vals)
    # MAD 剔除异常值
    median_fwhm = np.median(fwhm_vals)
    mad = np.median(np.abs(fwhm_vals - median_fwhm))
    threshold = max(3.0 * 1.4826 * mad, 0.5)
    inliers = np.abs(fwhm_vals - median_fwhm) < threshold
    fwhm_clean = fwhm_vals[inliers]

    if len(fwhm_clean) < 3:
        fwhm_clean = fwhm_vals

    return float(np.median(fwhm_clean)), float(np.std(fwhm_clean)), len(fwhm_clean), n_candidates


# ══════════════════════════════════════════════════════════════
# 多尺度星点检测
# ══════════════════════════════════════════════════════════════

def _multiscale_tophat(image_gray, fwhm, n_scales=4):
    """
    使用多尺度 White Top-hat 检测不同大小的星点。

    尺度定义（基于 FWHM）：
      scale 0: 小星  disk(ceil(FWHM * 0.4))  → 约 0.8x FWHM
      scale 1: 中星  disk(ceil(FWHM * 0.7))  → 约 1.4x FWHM
      scale 2: 大星  disk(ceil(FWHM * 1.1))  → 约 2.2x FWHM
      scale 3: 星芒  disk(ceil(FWHM * 1.8))  → 约 3.6x FWHM

    返回: list of (scale_idx, tophat_response, disk_radius)
    """
    scale_factors = [0.4, 0.7, 1.1, 1.8]
    results = []
    for i, sf in enumerate(scale_factors[:n_scales]):
        radius = max(1, int(np.ceil(fwhm * sf)))
        selem = disk(radius)
        tophat = white_tophat(image_gray, selem)
        results.append((i, tophat, radius))
    return results


def _gradient_mask(image_gray, fwhm):
    """
    计算星云高梯度区域掩膜。

    返回: gradient_map (float32, 0-1), 其中 1 表示高梯度区域
    """
    grad_y = sobel(image_gray, axis=0)
    grad_x = sobel(image_gray, axis=1)
    grad_mag = np.sqrt(grad_y ** 2 + grad_x ** 2)
    # 平滑以减少噪声影响
    grad_mag = gaussian_filter(grad_mag, sigma=max(1.0, fwhm * 0.5))
    # 归一化到 0-1
    p95 = np.percentile(grad_mag, 95)
    if p95 > 0:
        grad_norm = np.clip(grad_mag / p95, 0, 1)
    else:
        grad_norm = np.zeros_like(grad_mag)
    return grad_norm.astype(np.float32)


def _analyze_connected_components(labeled, image_gray, fwhm, star_threshold):
    """
    对每个连通域计算特征并分类过滤。

    特征：
      - area: 像素面积
      - equivalent_diameter: 等效直径 = sqrt(4*area/π)
      - circularity: 圆度 = 4π*area/perimeter²（接近 1 为圆）
      - peak: 峰值亮度
      - mean: 平均亮度
      - bbox_area_ratio: 面积 / 包围盒面积
      - aspect_ratio: 长宽比

    过滤规则：
      1. 面积 < π*(0.3*FWHM)²       → 热像素（reject）
      2. 面积 > π*(4*FWHM)² 且 圆度 < 0.25 → 星云亮核（reject）
      3. 峰值 < star_threshold * 0.3 → 噪声（reject）
      4. 长宽比 > 5 且 圆度 < 0.3   → 星云细丝（reject）
      5. 其余 → 保留，按特征分配置信度

    返回: (kept_mask, component_info_list)
    """
    h, w = image_gray.shape
    num_features = int(labeled.max())
    if num_features == 0:
        return np.zeros((h, w), dtype=bool), []

    # 预计算常量
    min_area = max(1, int(np.pi * (0.3 * fwhm) ** 2))
    max_area = int(np.pi * (4.0 * fwhm) ** 2)
    background = float(np.percentile(image_gray, 50.0))
    bright_reference = float(np.percentile(image_gray, 99.9))
    abs_peak_thresh = background + (
        max(bright_reference - background, 1e-8)
        * float(star_threshold) * 0.18
    )

    kept_mask = np.zeros((h, w), dtype=bool)
    components = []

    slices = find_objects(labeled)
    for i, slc in enumerate(slices, start=1):
        if slc is None:
            continue
        region = (labeled[slc] == i)
        area = int(region.sum())
        if area == 0:
            continue

        y_slice, x_slice = slc
        region_img = image_gray[slc]
        region_values = region_img[region]

        peak = float(region_values.max())
        mean_val = float(region_values.mean())

        # 等效直径
        eq_diam = np.sqrt(4.0 * area / np.pi)

        # 周长近似（4-连通轮廓）
        from skimage.measure import perimeter
        try:
            peri = perimeter(region, neighborhood=4)
        except Exception:
            peri = 2 * (region.shape[0] + region.shape[1])
        circularity = (4.0 * np.pi * area) / max(peri ** 2, 1e-6)
        circularity = min(circularity, 1.0)

        # 包围盒
        by0, by1 = y_slice.start, y_slice.stop
        bx0, bx1 = x_slice.start, x_slice.stop
        bbox_h = by1 - by0
        bbox_w = bx1 - bx0
        bbox_area = bbox_h * bbox_w
        bbox_ratio = area / max(bbox_area, 1)
        aspect_ratio = max(bbox_h, bbox_w) / max(min(bbox_h, bbox_w), 1)

        # 过滤决策
        reject_reason = None
        if area < min_area:
            reject_reason = "hot_pixel"
        elif area > max_area:
            reject_reason = "nebula_bright_core"
        elif (
            area > np.pi * (0.85 * fwhm) ** 2
            and peak / max(mean_val, 1e-9) < 1.5
        ):
            reject_reason = "diffuse_bright_structure"
        elif peak < abs_peak_thresh:
            reject_reason = "noise"
        elif aspect_ratio > 5.0 and circularity < 0.3:
            reject_reason = "filament"

        comp = {
            'id': i,
            'area': area,
            'equivalent_diameter': float(eq_diam),
            'circularity': float(circularity),
            'peak': peak,
            'mean': mean_val,
            'aspect_ratio': float(aspect_ratio),
            'bbox_ratio': float(bbox_ratio),
            'fwhm': float(fwhm),
            'rejected': reject_reason is not None,
            'reject_reason': reject_reason,
        }
        components.append(comp)

        if reject_reason is None:
            kept_mask[slc] |= region

    return kept_mask, components


def _compute_detection_confidence(components, fwhm, image_shape):
    """
    基于连通域特征计算整体检测置信度。

    因素：
      1. 保留/拒绝比例（reject > 80% 可能阈值过松）
      2. 保留组件的圆度一致性（标准差大 = 检测不稳定）
      3. 保留组件的尺寸与 FWHM 一致性
      4. 组件数量密度（过密 = 可能过检）
    """
    if not components:
        return 0.1, {'reason': 'no_components'}

    kept = [c for c in components if not c['rejected']]
    rejected = [c for c in components if c['rejected']]
    total = len(components)

    if total == 0:
        return 0.1, {'reason': 'no_components'}

    scores = []
    info = {}

    # 1. 保留比例。干净候选全部保留是合理结果，只惩罚大量拒绝。
    keep_ratio = len(kept) / total
    score_keep = np.clip(keep_ratio / 0.55, 0.0, 1.0)
    scores.append(score_keep)
    info['keep_ratio'] = round(keep_ratio, 3)

    # 2. 圆度一致性
    if kept:
        circs = [c['circularity'] for c in kept]
        circ_mean = np.mean(circs)
        circ_std = np.std(circs)
        # 真实星点应有较高圆度（>0.5）且标准差小
        score_circ = (circ_mean - 0.3) / 0.7
        score_circ = max(0.0, min(1.0, score_circ))
        score_circ_consistency = max(0.0, 1.0 - circ_std * 3.0)
        scores.append(score_circ * 0.5 + score_circ_consistency * 0.5)
        info['circularity_mean'] = round(circ_mean, 3)
        info['circularity_std'] = round(circ_std, 3)
    else:
        scores.append(0.0)
        info['circularity_mean'] = 0.0

    # 3. 尺寸一致性（等效直径应在 0.5-3x FWHM 范围内）
    if kept:
        diams = [c['equivalent_diameter'] for c in kept]
        diam_mean = np.mean(diams)
        diam_ratio = diam_mean / max(fwhm, 1.0)
        score_diam = 1.0 - abs(diam_ratio - 1.5) / 1.5
        score_diam = max(0.0, min(1.0, score_diam))
        scores.append(score_diam)
        info['diameter_fwhm_ratio'] = round(diam_ratio, 3)
    else:
        scores.append(0.0)

    # 4. 密度检查（过密 = 可能有假阳性）
    img_pixels = image_shape[0] * image_shape[1]
    density = len(kept) / (img_pixels / 1e6)  # 每百万像素
    score_density = 1.0 if density < 5000 else max(0.0, 1.0 - (density - 5000) / 10000)
    scores.append(score_density)
    info['stars_per_megapixel'] = round(density, 1)

    # 5. 拒绝原因分布（大量 hot_pixel 说明阈值太低）
    hot_pixel_ratio = len([c for c in rejected if c['reject_reason'] == 'hot_pixel']) / max(total, 1)
    score_hot = max(0.0, 1.0 - hot_pixel_ratio * 2.0)
    scores.append(score_hot)
    info['hot_pixel_ratio'] = round(hot_pixel_ratio, 3)

    confidence = float(np.mean(scores))
    info['confidence'] = round(confidence, 3)

    # 低置信度原因
    if confidence < 0.4:
        if hot_pixel_ratio > 0.5:
            info['low_confidence_reason'] = 'too_many_hot_pixels_threshold_too_low'
        elif len(kept) == 0:
            info['low_confidence_reason'] = 'all_components_rejected'
        elif density > 10000:
            info['low_confidence_reason'] = 'overdense_detection'
        else:
            info['low_confidence_reason'] = 'inconsistent_star_features'

    return confidence, info


def detect_stars_multiscale(image, fwhm=None, star_threshold=0.85,
                            gradient_aware=True, n_scales=4,
                            return_details=False):
    """
    多尺度星点检测引擎（v2）。

    参数:
        image: 输入图像 (H,W) 或 (H,W,C)
        fwhm: FWHM 估计值（像素）。None 时自动估计。
        star_threshold: 检测阈值（相对于 tophat 最大值）
        gradient_aware: 是否在高梯度区域提高阈值
        n_scales: 尺度数量（默认 4：小/中/大/星芒）
        return_details: 是否返回详细检测信息

    返回:
        star_mask: float32, 0-1 置信度掩膜
        confidence: float, 0-1 整体检测置信度
        details: dict（仅当 return_details=True 时）
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)
    img_gray = np.asarray(img_gray, dtype=np.float32)
    h, w = img_gray.shape

    # 1. 估计 FWHM
    if fwhm is None:
        fwhm_est, fwhm_std, n_used, n_cand = estimate_fwhm(img_gray)
        if n_used >= 3:
            fwhm = fwhm_est
            print(f"  [FWHM] 估计={fwhm:.2f}px (std={fwhm_std:.2f}, n={n_used}/{n_cand})")
        else:
            fwhm = 3.0
            print(f"  [FWHM] 估计失败，使用默认值 {fwhm:.1f}px")
    else:
        print(f"  [FWHM] 用户指定={fwhm:.2f}px")

    fwhm = max(fwhm, 1.5)  # 最小 FWHM 限制

    # 2. 多尺度 Top-hat 检测
    scale_results = _multiscale_tophat(img_gray, fwhm, n_scales=n_scales)

    combined_response = np.zeros((h, w), dtype=np.float32)
    for scale_idx, tophat, radius in scale_results:
        # 阈值：每个尺度使用略微不同的阈值
        # 小星（scale 0）阈值更严格，大星/星芒（scale 3）更宽松
        scale_thresh_factor = max(0.62, 1.0 - scale_idx * 0.11)
        positive = tophat[tophat > 0]
        if positive.size:
            median = float(np.median(positive))
            mad = float(np.median(np.abs(positive - median))) * 1.4826
            robust_threshold = median + 4.0 * max(mad, 1e-9)
            percentile_threshold = float(np.percentile(positive, 97.0))
        else:
            robust_threshold = percentile_threshold = 0.0
        relative_threshold = (
            float(star_threshold) * scale_thresh_factor * float(tophat.max())
        )
        th_abs = max(
            robust_threshold,
            min(percentile_threshold, relative_threshold),
        )
        mask = tophat > th_abs
        combined_response = np.maximum(combined_response, mask.astype(np.float32) * tophat)

    # 3. 梯度感知：在高梯度区域抑制检测
    if gradient_aware:
        grad_map = _gradient_mask(img_gray, fwhm)
        # 高梯度区域降低响应
        grad_penalty = 1.0 - grad_map * 0.5
        combined_response *= grad_penalty

    # 4. 二值化 + 连通域分析
    if combined_response.max() < 1e-6:
        star_mask = np.zeros((h, w), dtype=np.float32)
        confidence = 0.0
        details = {
            'fwhm': fwhm, 'n_scales': n_scales,
            'confidence': 0.0, 'reason': 'no_response',
            'components': [], 'scale_info': []
        }
        if return_details:
            return star_mask, confidence, details
        return star_mask, confidence

    response_values = combined_response[combined_response > 0]
    response_floor = (
        float(np.percentile(response_values, 20.0))
        if response_values.size else 0.0
    )
    binary_mask = combined_response > max(
        response_floor,
        float(star_threshold) * float(combined_response.max()) * 0.12,
    )
    # Top-hat response often leaves only the PSF core. Expand by a small,
    # FWHM-bounded radius before component analysis so ordinary faint stars
    # are not misclassified as one-pixel hot pixels.
    core_radius = max(1, int(round(fwhm * 0.35)))
    binary_mask = binary_dilation(binary_mask, structure=disk(core_radius))
    labeled, n_features = ndi_label(binary_mask)

    # 5. 连通域特征过滤
    kept_mask, components = _analyze_connected_components(
        labeled, img_gray, fwhm, star_threshold
    )

    # 6. 轻微膨胀以覆盖星点 PSF 边缘
    dilation_radius = max(1, min(3, int(round(fwhm * 0.25))))
    star_mask = binary_dilation(kept_mask, structure=disk(dilation_radius))
    star_mask = star_mask.astype(np.float32)

    # 7. 置信度评估
    confidence, conf_info = _compute_detection_confidence(
        components, fwhm, (h, w)
    )

    details = {
        'fwhm': fwhm,
        'n_scales': n_scales,
        'scale_info': [
            {'scale': i, 'radius': r} for i, _, r in scale_results
        ],
        'n_components_total': len(components),
        'n_components_kept': len([c for c in components if not c['rejected']]),
        'components': components if return_details else None,
        'confidence': confidence,
        'confidence_info': conf_info,
    }

    n_kept = details['n_components_kept']
    n_total = details['n_components_total']
    print(f"  [检测] 多尺度({n_scales}) → 候选{n_total} → 保留{n_kept} "
          f"→ 置信度={confidence:.2f}")

    if return_details:
        return star_mask, confidence, details
    return star_mask, confidence


# ══════════════════════════════════════════════════════════════
# 星点修复（Inpainting）
# ══════════════════════════════════════════════════════════════

def _mask_to_uint8(mask):
    """将浮点掩膜转为 OpenCV 可用的 uint8。"""
    return (np.clip(mask, 0, 1) * 255).astype(np.uint8)


def _image_to_uint8(image):
    """将 float32 图像转为 uint8。"""
    return (np.clip(image, 0, 1) * 255).astype(np.uint8)


def _image_from_uint8(image_uint8):
    """将 uint8 图像转回 float32。"""
    return image_uint8.astype(np.float32) / 255.0


def inpaint_telea(image, star_mask, radius=None):
    """
    OpenCV Telea 快速行进法修复（FMM-based）。

    适合：小星点、孤立亮斑。速度快。
    """
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV 不可用，无法使用 Telea 修复")

    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c] = inpaint_telea(image[..., c], star_mask, radius)
        return result

    if radius is None:
        radius = max(3, int(np.ceil(star_mask.sum() ** 0.5 * 0.3)))
        radius = min(radius, 15)

    # 针对极暗线性天文数据自适应放大至 [0, 1] 范围以保留 uint8 精度
    img_max = float(image.max())
    scaled_img = image / img_max if img_max > 1e-9 else image.copy()

    img_u8 = (np.clip(scaled_img, 0, 1) * 255).astype(np.uint8)
    mask_u8 = _mask_to_uint8(star_mask)
    inpainted_u8 = cv2.inpaint(img_u8, mask_u8, radius, cv2.INPAINT_TELEA)
    inpainted_f = inpainted_u8.astype(np.float32) / 255.0
    
    # 逆缩放还原
    return inpainted_f * img_max


def inpaint_ns(image, star_mask, radius=None):
    """
    OpenCV Navier-Stokes 流体动力学修复。

    适合：大星点、星芒、与复杂纹理重叠的区域。
    保留纹理连续性更好，但速度较慢。
    """
    if not HAS_OPENCV:
        raise RuntimeError("OpenCV 不可用，无法使用 Navier-Stokes 修复")

    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c] = inpaint_ns(image[..., c], star_mask, radius)
        return result

    if radius is None:
        radius = max(3, int(np.ceil(star_mask.sum() ** 0.5 * 0.3)))
        radius = min(radius, 15)

    # 自适应范围放大
    img_max = float(image.max())
    scaled_img = image / img_max if img_max > 1e-9 else image.copy()

    img_u8 = (np.clip(scaled_img, 0, 1) * 255).astype(np.uint8)
    mask_u8 = _mask_to_uint8(star_mask)
    inpainted_u8 = cv2.inpaint(img_u8, mask_u8, radius, cv2.INPAINT_NS)
    inpainted_f = inpainted_u8.astype(np.float32) / 255.0
    
    # 逆缩放还原
    return inpainted_f * img_max


def _inpaint_fallback(image, star_mask, radius=5):
    """
    当 OpenCV 不可用时的高斯模糊回退修复。
    保留旧行为以确保向后兼容。
    """
    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c] = _inpaint_fallback(image[..., c], star_mask, radius)
        return result

    result = image.copy()
    mask_bool = star_mask > 0.5
    if not np.any(mask_bool):
        return result

    # 使用扩张的掩膜
    dilated = binary_dilation(mask_bool, structure=disk(radius))
    for _ in range(3):
        result[mask_bool] = gaussian_filter(result, sigma=radius)[mask_bool]

    return result


# ══════════════════════════════════════════════════════════════
# 旧版兼容接口（内部调用新版引擎）
# ══════════════════════════════════════════════════════════════

def detect_stars(image, star_threshold=0.85, min_size=3, fwhm=None):
    """
    星点检测（兼容接口，内部使用多尺度引擎）。

    返回: float32 掩膜（与旧版相同格式）。
    """
    star_mask, confidence = detect_stars_multiscale(
        image, fwhm=fwhm, star_threshold=star_threshold,
        gradient_aware=True, n_scales=4, return_details=False
    )
    return star_mask


def remove_stars_inpaint(image, star_mask, inpaint_radius=5):
    """
    星点修复（兼容接口，优先使用 Telea，OpenCV 不可用时回退）。
    """
    if HAS_OPENCV:
        # 根据掩膜大小智能选择修复方法
        mask_size = int(np.sum(star_mask > 0.5))
        if mask_size > 500:
            # 大区域用 NS，小区域用 Telea
            return inpaint_ns(image, star_mask, radius=inpaint_radius)
        else:
            return inpaint_telea(image, star_mask, radius=inpaint_radius)
    else:
        return _inpaint_fallback(image, star_mask, inpaint_radius)


def remove_stars_median(image, star_mask, filter_size=15):
    """
    中值滤波移除星点（保持不变，兼容接口）。
    """
    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            channel_filtered = median_filter(image[..., c], size=filter_size)
            result[..., c] = np.where(star_mask > 0.5,
                                      channel_filtered,
                                      image[..., c])
        return result
    else:
        filtered = median_filter(image, size=filter_size)
        return np.where(star_mask > 0.5, filtered, image)


def estimate_star_removal_quality(original, starless):
    """
    评估星点分离质量（扩展版）。

    新增：检测去星后残留的结构伪影（修复过度平滑、边界痕迹）。
    """
    original = np.asarray(original, dtype=np.float32)
    starless = np.asarray(starless, dtype=np.float32)

    if original.ndim == 3:
        orig_gray = 0.299 * original[..., 0] + 0.587 * original[..., 1] + 0.114 * original[..., 2]
        sl_gray = 0.299 * starless[..., 0] + 0.587 * starless[..., 1] + 0.114 * starless[..., 2]
    else:
        orig_gray = original
        sl_gray = starless

    bg_level = float(np.percentile(sl_gray, 10))

    # 残留星检测
    residual_thresh = bg_level + 3 * float(np.std(sl_gray[sl_gray < np.percentile(sl_gray, 50)]))
    residual_mask = sl_gray > max(residual_thresh, 0.1)
    residual_fraction = float(np.mean(residual_mask))

    # 星云误伤检测
    dark_orig = orig_gray < np.percentile(orig_gray, 30)
    if np.sum(dark_orig) > 100:
        orig_dark_mean = float(np.mean(orig_gray[dark_orig]))
        sl_dark_mean = float(np.mean(sl_gray[dark_orig]))
        damage_ratio = abs(orig_dark_mean - sl_dark_mean) / max(orig_dark_mean, 1e-8)
    else:
        damage_ratio = 0.0

    # 新增：修复伪影检测（边界处的梯度异常）
    # 去星后的梯度应在星点区域平滑，但如果修复质量差，边界会有硬边
    grad_y = sobel(sl_gray, axis=0)
    grad_x = sobel(sl_gray, axis=1)
    grad_mag = np.sqrt(grad_y ** 2 + grad_x ** 2)
    # 高梯度像素比例（去星后不应有过多的高梯度）
    high_grad_ratio = float(np.mean(grad_mag > np.percentile(grad_mag, 95) * 0.5))

    # 综合评分
    needs_starnet = residual_fraction > 0.05 or damage_ratio > 0.15

    # 修复质量评分（0-1，1 最好）
    repair_score = 1.0
    repair_score -= min(residual_fraction * 5.0, 0.5)
    repair_score -= min(damage_ratio * 2.0, 0.3)
    repair_score -= min(high_grad_ratio * 2.0, 0.2)
    repair_score = max(0.0, min(1.0, repair_score))

    report = {
        'residual_star_fraction': round(residual_fraction, 4),
        'nebula_damage_ratio': round(damage_ratio, 4),
        'high_gradient_ratio': round(high_grad_ratio, 4),
        'repair_quality_score': round(repair_score, 3),
        'needs_starnet_plus': needs_starnet,
        'quality': 'good' if repair_score > 0.7 else ('marginal' if repair_score > 0.4 else 'poor'),
    }

    if needs_starnet:
        report['suggestion'] = (
            '形态学去星质量不足 — 建议使用 StarNet++ v2 CLI 生成无星图，'
            '然后通过 --external-starless 接入管线'
        )

    return report


def _repair_star_mask(image, star_mask, method, inpaint_radius):
    """Apply one repair strategy and return the result plus actual method."""
    if method == 'telea':
        if HAS_OPENCV:
            return inpaint_telea(image, star_mask, radius=inpaint_radius), 'telea'
        return _inpaint_fallback(image, star_mask, inpaint_radius), 'gaussian_fallback'
    if method == 'ns':
        if HAS_OPENCV:
            return inpaint_ns(image, star_mask, radius=inpaint_radius), 'ns'
        return _inpaint_fallback(image, star_mask, inpaint_radius), 'gaussian_fallback'
    if method == 'median':
        return remove_stars_median(image, star_mask), 'median'

    if HAS_OPENCV:
        if int(np.sum(star_mask > 0.5)) > 500:
            return inpaint_ns(image, star_mask, radius=inpaint_radius), 'ns'
        return inpaint_telea(image, star_mask, radius=inpaint_radius), 'telea'
    return _inpaint_fallback(image, star_mask, inpaint_radius), 'gaussian_fallback'


def _safe_star_removal_fallback(image, reason, report=None):
    """Return an unchanged image when star removal is not trustworthy."""
    fallback_report = dict(report or {})
    fallback_report.update({
        'accepted': False,
        'fallback_applied': True,
        'fallback_reason': reason,
    })
    starless = np.asarray(image, dtype=np.float32).copy()
    stars = np.zeros_like(starless)
    star_mask = np.zeros(starless.shape[:2], dtype=np.float32)
    return starless, stars, star_mask, fallback_report


def _star_layer_mask(stars):
    """Build a scale-aware mask from a positive stellar residual layer."""
    signal = np.mean(stars, axis=2) if stars.ndim == 3 else stars
    positive = signal[signal > 0]
    if positive.size == 0:
        return np.zeros(signal.shape, dtype=np.float32)
    median = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median))) * 1.4826
    threshold = max(
        median + 3.0 * mad,
        float(np.percentile(positive, 99.0)) * 0.04,
        1e-7,
    )
    return (signal > threshold).astype(np.float32)


# ══════════════════════════════════════════════════════════════
# 星点分离 / 缩星 / 合成（兼容接口）
# ══════════════════════════════════════════════════════════════

def find_starnet_executable(user_path=None):
    """
    寻找 StarNet2 CLI 可执行文件。
    优先级：user_path -> 环境变量 STARNET_PATH -> 系统 PATH (shutil.which) -> 默认扫描路径。
    """
    import os
    import shutil
    import sys

    # 1. 显式配置优先
    if user_path:
        user_path = os.path.abspath(os.path.expanduser(user_path))
        if os.path.isfile(user_path):
            return user_path

    # 2. 环境变量检索
    env_path = os.environ.get("STARNET_PATH")
    if env_path:
        env_path = os.path.abspath(os.path.expanduser(env_path))
        if os.path.isdir(env_path):
            for name in ("starnet2", "starnet++", "starnet++.exe"):
                candidate = os.path.join(env_path, name)
                if os.path.isfile(candidate):
                    return candidate
        if os.path.isfile(env_path):
            return env_path

    # 3. 系统 PATH 检索
    which_path = shutil.which("starnet++") or shutil.which("starnet2")
    if which_path:
        return which_path

    # 4. 默认解压目录扫描
    home = os.path.expanduser("~")
    sys_platform = sys.platform.lower()
    
    default_paths = []
    if "darwin" in sys_platform:
        default_paths = [
            "/Applications/StarNet/starnet2",
            "/Applications/StarNet/starnet++",
            "/usr/local/bin/starnet2",
            "/opt/homebrew/bin/starnet2",
            "/opt/homebrew/bin/starnet++",
            "/Applications/StarNet2/starnet++",
            "/Applications/StarNet2/StarNet2.app/Contents/MacOS/starnet2",
            os.path.join(home, "Applications/StarNet2/starnet++"),
            os.path.join(home, "Applications/StarNet/starnet2"),
            os.path.join(os.path.dirname(__file__), "starnet++"),
            os.path.join(os.path.dirname(__file__), "StarNet2", "starnet++"),
        ]
    elif "linux" in sys_platform:
        default_paths = [
            "/usr/local/bin/starnet++",
            os.path.join(home, ".local/bin/starnet++"),
            os.path.join(os.path.dirname(__file__), "starnet++"),
            os.path.join(os.path.dirname(__file__), "StarNet2", "starnet++"),
        ]
    else:
        # 其他系统，如 windows
        default_paths = [
            os.path.join(os.path.dirname(__file__), "starnet++.exe"),
            os.path.join(os.path.dirname(__file__), "starnet++"),
        ]

    for p in default_paths:
        if os.path.exists(p) and os.path.isfile(p):
            return p

    return None


def run_starnet_cli(image, exe_path, stride=256, timeout=900,
                    return_report=False):
    """
    运行 StarNet2 CLI 去星。
    """
    import os
    import tempfile
    import subprocess
    import sys
    
    parent_dir = os.path.dirname(os.path.abspath(exe_path))
    execution_report = {
        "executable": os.path.abspath(exe_path),
        "stride": int(stride),
        "timeout_seconds": int(timeout),
        "attempts": [],
    }
    
    # 1. 针对 macOS 清除 Gatekeeper 隔离属性
    if sys.platform == 'darwin':
        try:
            subprocess.run(
                ["xattr", "-r", "-d", "com.apple.quarantine", parent_dir],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass

    # 2. 确保有可执行权限
    try:
        subprocess.run(
            ["chmod", "+x", exe_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

    # 3. 构造环境变量，注入动态库路径
    env = os.environ.copy()
    if sys.platform == 'darwin':
        dyld_path = env.get("DYLD_LIBRARY_PATH", "")
        env["DYLD_LIBRARY_PATH"] = parent_dir + (":" + dyld_path if dyld_path else "")
    else:
        ld_path = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = parent_dir + (":" + ld_path if ld_path else "")

    # 4. 在临时目录中执行
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_in = os.path.join(tmpdir, "input.tif")
        temp_out = os.path.join(tmpdir, "output.tif")
        
        # 确保输入图像是 RGB (3通道) 并转换为 16-bit uint16 (StarNet2 仅支持 8/16-bit 整数)
        img_to_save = np.clip(image, 0, 1)
        if img_to_save.ndim == 2:
            img_to_save = np.stack([img_to_save]*3, axis=-1)
        elif img_to_save.ndim == 3 and img_to_save.shape[2] == 1:
            img_to_save = np.concatenate([img_to_save]*3, axis=-1)
        
        img_to_save = (img_to_save * 65535.0).astype(np.uint16)
        
        try:
            # 写入临时文件
            import skimage.io
            skimage.io.imsave(temp_in, img_to_save, check_contrast=False)
        except Exception as e:
            print(f"  [ERROR] 写入 StarNet2 输入临时文件失败: {e}")
            result = (False, None, execution_report)
            return result if return_report else result[:2]
            
        # 智能检测 StarNet 命令行格式 (StarNet2 CLI 必须使用 -i -o -s 参数)
        use_new_format = "starnet2" in os.path.basename(exe_path).lower()
        if not use_new_format:
            try:
                help_res = subprocess.run([exe_path, "--help"], env=env, capture_output=True, text=True, timeout=3)
                if "-i" in help_res.stdout or "-i" in help_res.stderr:
                    use_new_format = True
            except Exception:
                pass

        new_cmd = [exe_path, "-i", temp_in, "-o", temp_out, "-s", str(stride)]
        legacy_cmd = [exe_path, temp_in, temp_out, str(stride)]
        commands = [new_cmd, legacy_cmd] if use_new_format else [legacy_cmd, new_cmd]
        succeeded = False
        for cmd in commands:
            try:
                print(f"  [StarNet2] 正在执行: {' '.join(cmd)}")
                res = subprocess.run(
                    cmd,
                    cwd=parent_dir,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                attempt = {
                    "command_format": "flags" if "-i" in cmd else "legacy",
                    "returncode": int(res.returncode),
                    "stderr": (res.stderr or "")[-2000:],
                    "stdout": (res.stdout or "")[-2000:],
                }
                execution_report["attempts"].append(attempt)
                if res.returncode == 0:
                    succeeded = True
                    break
            except subprocess.TimeoutExpired:
                execution_report["attempts"].append({
                    "command_format": "flags" if "-i" in cmd else "legacy",
                    "error": "timeout",
                })
            except Exception as exc:
                execution_report["attempts"].append({
                    "command_format": "flags" if "-i" in cmd else "legacy",
                    "error": str(exc),
                })
        if not succeeded:
            print("  [ERROR] StarNet2 所有命令格式均执行失败")
            result = (False, None, execution_report)
            return result if return_report else result[:2]
            
        # 读取输出 (使用 OpenCV 避免 imagecodecs 缺失导致无法读取 LZW 压缩 TIFF)
        try:
            import cv2
            starless_raw = cv2.imread(temp_out, cv2.IMREAD_UNCHANGED)
            if starless_raw is None:
                raise ValueError("cv2.imread 返回了 None")
            if starless_raw.ndim == 3 and starless_raw.shape[2] == 3:
                starless_raw = cv2.cvtColor(starless_raw, cv2.COLOR_BGR2RGB)
            if np.issubdtype(starless_raw.dtype, np.integer):
                dtype_max = float(np.iinfo(starless_raw.dtype).max)
                starless = starless_raw.astype(np.float32) / dtype_max
            else:
                starless = starless_raw.astype(np.float32)
            # 如果原图是单通道，我们也转回单通道
            if image.ndim == 2:
                starless = np.mean(starless, axis=2)
            elif image.ndim == 3 and image.shape[2] == 1:
                starless = np.mean(starless, axis=2, keepdims=True)
            if starless.shape != image.shape:
                raise ValueError(
                    f"输出形状 {starless.shape} 与输入 {image.shape} 不一致"
                )
            if not np.all(np.isfinite(starless)):
                raise ValueError("输出包含 NaN/Inf")
            starless = np.clip(starless, 0, 1)
            execution_report.update({
                "output_shape": list(starless.shape),
                "output_dtype": str(starless_raw.dtype),
                "success": True,
            })
            result = (True, starless, execution_report)
            return result if return_report else result[:2]
        except Exception as e:
            print(f"  [ERROR] 读取 StarNet2 输出图像失败: {e}")
            execution_report["output_error"] = str(e)
            result = (False, None, execution_report)
            return result if return_report else result[:2]


def separate_stars(image, method='inpaint', star_threshold=0.85, inpaint_radius=5,
                   external_starless=None, fwhm=None, use_multiscale=True,
                   min_confidence=0.3, min_quality_score=0.45,
                   auto_fallback=True, return_report=False,
                   starnet_path=None, starnet_stride=256,
                   starnet_timeout=900):
    """
    分离星点和星云（v2 增强版）。

    新增参数:
        fwhm: FWHM 估计值。None 时自动估计。
        use_multiscale: 是否使用多尺度检测引擎（True=新引擎，False=旧引擎）
        min_confidence: 检测置信度阈值，低于此值时安全回退
        min_quality_score: 去星质量最低可接受分数
        auto_fallback: 是否在低置信度或低质量时回退原图
        return_report: 是否额外返回检测、重试和回退报告
        starnet_path: StarNet2 可执行文件的绝对路径
        starnet_stride: StarNet2 步长
        starnet_timeout: StarNet2 最长执行秒数

    返回: (星云图像, 星点图像, 星点掩膜[, 报告])
    """
    image = np.asarray(image, dtype=np.float32)
    if external_starless is not None:
        starless = np.asarray(external_starless, dtype=np.float32)
        if starless.shape != image.shape:
            raise ValueError(
                f"external starless shape {starless.shape} != input shape {image.shape}"
            )
        stars = np.clip(image - starless, 0, 1)
        star_mask = _star_layer_mask(stars)
        report = estimate_star_removal_quality(image, starless)
        report.update({
            'accepted': True,
            'fallback_applied': False,
            'source': 'external_starless',
        })
        result = (np.clip(starless, 0, 1), stars, star_mask)
        return (*result, report) if return_report else result

    starnet_fallback_reason = None
    starnet_failure_report = None
    
    if method == 'starnet':
        exe_path = find_starnet_executable(starnet_path)
        if exe_path is None:
            print("  [WARN] StarNet2 可执行文件未找到，将回退至形态学去星。")
            starnet_fallback_reason = "starnet_executable_not_found"
            method = 'inpaint'
        else:
            try:
                success, starnet_starless, starnet_report = run_starnet_cli(
                    image,
                    exe_path,
                    stride=starnet_stride,
                    timeout=starnet_timeout,
                    return_report=True,
                )
                if success:
                    starless = starnet_starless
                    stars = np.clip(image - starless, 0, 1)
                    star_mask = _star_layer_mask(stars)
                    
                    report = estimate_star_removal_quality(image, starless)
                    starnet_quality_ok = (
                        report['repair_quality_score'] >= min_quality_score
                        and report['nebula_damage_ratio'] <= 0.20
                    )
                    report.update({
                        'accepted': starnet_quality_ok,
                        'fallback_applied': not starnet_quality_ok,
                        'source': 'starnet',
                        'starnet_execution': starnet_report,
                    })
                    if starnet_quality_ok:
                        stars = gaussian_filter(stars, sigma=0.8)
                        result = (starless, stars, star_mask)
                        return (*result, report) if return_report else result
                    print(
                        "  [WARN] StarNet2 输出质量未通过安全门禁，"
                        "将回退至形态学去星。"
                    )
                    starnet_fallback_reason = "starnet_quality_below_threshold"
                    starnet_failure_report = report
                    method = 'inpaint'
                else:
                    print("  [WARN] StarNet2 运行失败，将回退至形态学去星。")
                    starnet_fallback_reason = "starnet_execution_failed"
                    starnet_failure_report = {
                        "starnet_execution": starnet_report,
                    }
                    method = 'inpaint'
            except Exception as e:
                print(f"  [WARN] StarNet2 运行发生异常: {e}，将回退至形态学去星。")
                starnet_fallback_reason = "starnet_execution_failed"
                starnet_failure_report = {"exception": str(e)}
                method = 'inpaint'

    def _run_morphology_pipeline():
        if use_multiscale:
            star_mask_val, confidence, details = detect_stars_multiscale(
                image, fwhm=fwhm, star_threshold=star_threshold,
                gradient_aware=True, n_scales=4, return_details=True
            )

            if confidence < min_confidence:
                retry_threshold = max(0.55, float(star_threshold) * 0.85)
                retry_mask, retry_confidence, retry_details = (
                    detect_stars_multiscale(
                        image,
                        fwhm=details.get('fwhm', fwhm),
                        star_threshold=retry_threshold,
                        gradient_aware=True,
                        n_scales=4,
                        return_details=True,
                    )
                )
                if retry_confidence > confidence:
                    star_mask_val = retry_mask
                    confidence = retry_confidence
                    details = retry_details
                    details['detection_retry'] = {
                        'attempted': True,
                        'threshold': retry_threshold,
                        'improved': True,
                    }
                else:
                    details['detection_retry'] = {
                        'attempted': True,
                        'threshold': retry_threshold,
                        'improved': False,
                        'retry_confidence': retry_confidence,
                    }

            if confidence < min_confidence:
                n_kept = details.get('n_components_kept', 0)
                n_total = details.get('n_components_total', 0)
                print(f"  [WARN] 星点检测置信度过低 ({confidence:.2f} < {min_confidence})")
                print(f"         保留{n_kept}/{n_total}组件，原因: "
                      f"{details.get('confidence_info', {}).get('low_confidence_reason', 'unknown')}")
                if auto_fallback:
                    print("         已安全回退：跳过去星并保留原图")
                    print("         建议：使用 --external-starless 传入 StarNet++ 无星图")
                    fallback_res = _safe_star_removal_fallback(
                        image,
                        reason='low_detection_confidence',
                        report={
                            'detection_confidence': confidence,
                            'detection_details': details,
                        },
                    )
                    return fallback_res
        else:
            star_mask_val = detect_stars(image, star_threshold=star_threshold)
            confidence = None

        n_star_pixels = int(np.sum(star_mask_val > 0.5))
        print(f"[星点分离] 检测到星点像素: {n_star_pixels:,}"
              f"{f' (置信度={confidence:.2f})' if confidence is not None else ''}")

        starless_val, actual_method = _repair_star_mask(
            image, star_mask_val, method, inpaint_radius
        )
        quality = estimate_star_removal_quality(image, starless_val)
        quality.update({
            'detection_confidence': confidence,
            'repair_method': actual_method,
            'repair_radius': inpaint_radius,
            'retry_attempted': False,
        })
        quality_ok = (
            quality['repair_quality_score'] >= min_quality_score
            and not quality['needs_starnet_plus']
        )

        if not quality_ok and auto_fallback:
            retry_method = 'telea' if actual_method == 'ns' else 'ns'
            if not HAS_OPENCV or actual_method == 'gaussian_fallback':
                retry_method = 'median'
            retry_radius = max(2, inpaint_radius - 2)
            print(
                f"  [质量闭环] 首次去星质量={quality['repair_quality_score']:.3f} "
                f"({quality['quality']})，使用 {retry_method} 半径={retry_radius} 重试"
            )
            retry_starless, retry_actual_method = _repair_star_mask(
                image, star_mask_val, retry_method, retry_radius
            )
            retry_quality = estimate_star_removal_quality(image, retry_starless)
            retry_quality.update({
                'detection_confidence': confidence,
                'repair_method': retry_actual_method,
                'repair_radius': retry_radius,
                'retry_attempted': True,
                'initial_quality': quality,
            })
            retry_ok = (
                retry_quality['repair_quality_score'] >= min_quality_score
                and not retry_quality['needs_starnet_plus']
            )
            if retry_ok:
                print(
                    f"  [质量闭环] 重试通过，质量={retry_quality['repair_quality_score']:.3f}"
                )
                starless_val = retry_starless
                quality = retry_quality
                quality_ok = True
            else:
                print(
                    f"  [质量闭环] 重试仍不达标，质量="
                    f"{retry_quality['repair_quality_score']:.3f}，安全回退原图"
                )
                fallback_res = _safe_star_removal_fallback(
                    image,
                    reason='star_removal_quality_below_threshold',
                    report=retry_quality,
                )
                return fallback_res

        stars_val = image - starless_val
        stars_val = np.clip(stars_val, 0, 1)

        # 对星点图像轻微模糊，使边缘更自然
        stars_val = gaussian_filter(stars_val, sigma=0.8)

        quality.update({
            'accepted': quality_ok,
            'fallback_applied': False,
        })
        return (starless_val, stars_val, star_mask_val, quality)

    morph_res = _run_morphology_pipeline()
    starless, stars, star_mask, report = morph_res

    if starnet_fallback_reason is not None:
        report = dict(report)
        report.update({
            'fallback_applied': True,
            'fallback_reason': starnet_fallback_reason,
            'starnet_failure': starnet_failure_report,
        })

    result = (starless, stars, star_mask)
    return (*result, report) if return_report else result


def reduce_stars(image, reduction=0.5, iterations=1, fwhm=None):
    """
    缩小星点直径（v2 增强版，支持 FWHM 感知）。
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)

    if fwhm is None:
        fwhm, _, n_used, _ = estimate_fwhm(img_gray)
        if n_used < 3:
            fwhm = 3.0

    star_mask = detect_stars(img_gray, star_threshold=0.7, fwhm=fwhm)
    # 腐蚀半径基于 FWHM 和 reduction
    erosion_radius = max(1, int(fwhm * 0.4 * reduction))
    selem = disk(erosion_radius)

    result = image.copy()
    for _ in range(iterations):
        if image.ndim == 3:
            eroded = np.zeros_like(image)
            for c in range(image.shape[2]):
                eroded[..., c] = erosion(result[..., c], selem)
        else:
            eroded = erosion(result, selem)
        result = np.where(np.expand_dims(star_mask, -1) if image.ndim == 3 else star_mask,
                          eroded, result)

    return np.clip(result, 0, 1)


def combine_starless_stars(starless, stars, star_strength=1.0, star_softness=1.0):
    """
    重新合成星云和星点图像（保持不变）。
    """
    if star_softness != 1.0:
        stars = gaussian_filter(stars, sigma=star_softness)
    result = np.clip(starless + star_strength * stars, 0, 1)
    return result


def mild_star_reduce_full(image, reduction=0.3, color_restore=True,
                          star_mask=None):
    """
    无需星点分离的轻微缩星 + 蓝白星色恢复（保持不变）。
    """
    from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb

    if image.ndim < 3:
        return image

    # 优先复用线性阶段星点蒙版，避免把亮星云壳层误判为星点。
    gray = np.mean(image, axis=2)
    if star_mask is None:
        star_mask = detect_stars(gray, star_threshold=0.82)
    else:
        star_mask = np.asarray(star_mask, dtype=np.float32)
        if star_mask.ndim == 3:
            star_mask = np.max(star_mask, axis=2)
        if star_mask.shape != gray.shape:
            raise ValueError(
                f"star_mask shape {star_mask.shape} != image shape {gray.shape}"
            )
    star_mask = gaussian_filter(np.clip(star_mask, 0, 1), sigma=1.2)
    star_mask = np.clip(star_mask, 0, 1)

    lab = rgb2lab(image)
    L = lab[..., 0]

    # 形态学腐蚀缩小星点
    from skimage.morphology import disk, erosion
    selem = disk(max(1, int(reduction * 6)))
    L_eroded = erosion(L, selem)

    # 混合：星点区域使用腐蚀后的亮度
    L_reduced = L * (1 - star_mask * reduction) + L_eroded * (star_mask * reduction)
    lab[..., 0] = np.clip(L_reduced, 0, 100)

    result = lab2rgb(lab)

    # 星色恢复：降低星点区域的红色饱和度，使星点呈现蓝白色
    if color_restore:
        from color_conv import safe_rgb2hsv as rgb2hsv, safe_hsv2rgb as hsv2rgb
        hsv = rgb2hsv(result)
        # 降低星点区域的饱和度
        hsv[..., 1] = hsv[..., 1] * (1 - star_mask * 0.5)
        # 微调色调远离红色（红色 H≈0，向蓝色 H≈0.6 偏移）
        r_shift = np.zeros_like(hsv[..., 0])
        red_mask = (hsv[..., 0] < 0.08) | (hsv[..., 0] > 0.92)
        r_shift[red_mask] = 0.04
        hsv[..., 0] = np.clip((hsv[..., 0] + r_shift * star_mask) % 1.0, 0, 1)
        result = hsv2rgb(hsv)

    return np.clip(result, 0, 1)


# ══════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description='深空星点处理工具 v2')
    sub = p.add_subparsers(dest='command', required=True)

    # separate
    p_sep = sub.add_parser('separate', help='分离星点和星云')
    p_sep.add_argument('input', help='输入图像')
    p_sep.add_argument('output_starless', help='输出无星图像')
    p_sep.add_argument('--output-stars', default=None, help='输出星点图像')
    p_sep.add_argument('--method', default='telea',
                       choices=['telea', 'ns', 'median', 'inpaint', 'starnet'],
                       help='修复方法 (默认: telea)')
    p_sep.add_argument('--threshold', type=float, default=0.85)
    p_sep.add_argument('--radius', type=int, default=5)
    p_sep.add_argument('--fwhm', type=float, default=None,
                       help='FWHM 估计值（像素），不指定则自动估计')
    p_sep.add_argument('--legacy', action='store_true',
                       help='使用旧版单尺度检测（不使用多尺度引擎）')
    p_sep.add_argument('--external', default=None,
                       help='外部工具生成的无星图，替代内部修复')
    p_sep.add_argument('--starnet-path', default=None, help='StarNet2 可执行二进制文件的绝对路径')
    p_sep.add_argument('--starnet-stride', type=int, default=256, help='StarNet2 步长 (默认: 256)')

    # detect (新增)
    p_det = sub.add_parser('detect', help='仅检测星点，输出掩膜')
    p_det.add_argument('input', help='输入图像')
    p_det.add_argument('output_mask', help='输出星点掩膜')
    p_det.add_argument('--threshold', type=float, default=0.85)
    p_det.add_argument('--fwhm', type=float, default=None)
    p_det.add_argument('--details', action='store_true',
                       help='输出检测详情 JSON')

    # reduce
    p_red = sub.add_parser('reduce', help='缩小星点')
    p_red.add_argument('input', help='输入图像')
    p_red.add_argument('output', help='输出图像')
    p_red.add_argument('--reduction', type=float, default=0.5)
    p_red.add_argument('--iterations', type=int, default=1)
    p_red.add_argument('--fwhm', type=float, default=None)

    # combine
    p_com = sub.add_parser('combine', help='合成星云和星点')
    p_com.add_argument('starless', help='无星/星云图像')
    p_com.add_argument('stars', help='星点图像')
    p_com.add_argument('output', help='输出图像')
    p_com.add_argument('--strength', type=float, default=1.0, help='星点强度 (默认: 1.0)')
    p_com.add_argument('--softness', type=float, default=1.0, help='星点柔化 (默认: 1.0)')

    args = p.parse_args()

    if args.command == 'separate':
        img = img_as_float32(imread(args.input))
        print(f"[星点分离] 输入: {args.input}  形状: {img.shape}")
        external = img_as_float32(imread(args.external)) if args.external else None
        starless, stars, mask = separate_stars(
            img, method=args.method, star_threshold=args.threshold,
            inpaint_radius=args.radius, external_starless=external,
            fwhm=args.fwhm, use_multiscale=not args.legacy,
            starnet_path=getattr(args, 'starnet_path', None),
            starnet_stride=getattr(args, 'starnet_stride', 256)
        )
        imsave(args.output_starless, img_as_ubyte(starless))
        print(f"[星点分离] 无星图像: {args.output_starless}")
        if args.output_stars:
            imsave(args.output_stars, img_as_ubyte(stars))
            print(f"[星点分离] 星点图像: {args.output_stars}")

    elif args.command == 'detect':
        img = img_as_float32(imread(args.input))
        print(f"[星点检测] 输入: {args.input}  形状: {img.shape}")
        mask, confidence, details = detect_stars_multiscale(
            img, fwhm=args.fwhm, star_threshold=args.threshold,
            return_details=True
        )
        imsave(args.output_mask, img_as_ubyte(mask))
        print(f"[星点检测] 掩膜: {args.output_mask}  置信度: {confidence:.3f}")
        if args.details:
            import json
            details_path = args.output_mask.replace('.tif', '.json').replace('.png', '.json').replace('.jpg', '.json') + '.details.json'
            with open(details_path, 'w') as f:
                json.dump(details, f, indent=2, default=str)
            print(f"[星点检测] 详情: {details_path}")

    elif args.command == 'reduce':
        img = img_as_float32(imread(args.input))
        print(f"[缩星] 输入: {args.input}")
        result = reduce_stars(img, reduction=args.reduction, iterations=args.iterations, fwhm=args.fwhm)
        imsave(args.output, img_as_ubyte(result))
        print(f"[缩星] 输出: {args.output}")

    elif args.command == 'combine':
        starless = img_as_float32(imread(args.starless))
        stars = img_as_float32(imread(args.stars))
        print(f"[合成] 星云: {args.starless} + 星点: {args.stars}")
        result = combine_starless_stars(
            starless, stars, star_strength=args.strength,
            star_softness=args.softness
        )
        imsave(args.output, img_as_ubyte(result))
        print(f"[合成] 输出: {args.output}")


if __name__ == '__main__':
    main()
