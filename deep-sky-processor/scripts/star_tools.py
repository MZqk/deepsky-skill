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

# 去星门禁阈值档（按 target_type 分档）。导入放在 OpenCV 探测之后以保持
# 原有 import 顺序；模块本身只依赖 dataclasses/target_rules，无重依赖。
from star_removal_profiles import DEFAULTS as _REMOVAL_DEFAULTS
from star_removal_profiles import get_profile as _removal_profile
from star_removal_profiles import profile_report as _removal_profile_report

# OpenCV 用于 Telea/Navier-Stokes 修复
try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False
    warnings.warn("OpenCV 不可用，星点修复将回退到高斯模糊", RuntimeWarning)


# ── 星点检测阈值常量 ──
# 全部锚定"背景噪声"，**绝不锚定画面里最亮的星**：
# 一旦锚定最亮星，检测结果就取决于画面里最亮的那个天体（如 M31 的核），
# 暗星会被整体滤掉。详见 detect_stars_multiscale 内的注释。
_DETECT_TOPHAT_SIGMA_MULTIPLIER = 7.0  # × 背景 tophat 正值部分的稳健噪声水平
_DETECT_PEAK_SIGMAS = 5.0              # 峰值下限 = background + 5σ

# 星系核保护的兜底半径（占画幅短边）。显式传入 galaxy_core_radius 优先。
# 固定 15px 只挡得住核球最中心，内盘恒星仍被剥离；按画幅比例取值才能覆盖真正的核区。
GALAXY_CORE_PROTECTION_MIN_RADIUS_FRACTION = 0.02


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


def _profile_shape_at(image_gray, center_rc, fit_radius=8):
    """Measure a local stellar profile at a fixed reference coordinate."""
    cy, cx = (int(center_rc[0]), int(center_rc[1]))
    h, w = image_gray.shape
    y0, y1 = cy - fit_radius, cy + fit_radius + 1
    x0, x1 = cx - fit_radius, cx + fit_radius + 1
    if y0 < 0 or x0 < 0 or y1 > h or x1 > w:
        return None
    patch = np.asarray(image_gray[y0:y1, x0:x1], dtype=np.float64)
    background = float(np.percentile(patch, 20.0))
    signal = np.clip(patch - background, 0.0, None)
    peak = float(np.max(signal))
    if not np.isfinite(peak) or peak <= 1e-7:
        return None
    # Suppress faint wings/noise so profile widths remain comparable after
    # nonlinear star-layer curves.
    weights = np.where(signal >= peak * 0.05, signal, 0.0)
    total = float(np.sum(weights))
    if total <= 1e-8:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    cx_fit = float(np.sum(xx * weights) / total)
    cy_fit = float(np.sum(yy * weights) / total)
    if (cx_fit - cx) ** 2 + (cy_fit - cy) ** 2 > 2.5 ** 2:
        return None
    dx = xx - cx_fit
    dy = yy - cy_fit
    cov_xx = float(np.sum(weights * dx * dx) / total)
    cov_yy = float(np.sum(weights * dy * dy) / total)
    cov_xy = float(np.sum(weights * dx * dy) / total)
    eigenvalues = np.linalg.eigvalsh(np.array([
        [cov_xx, cov_xy],
        [cov_xy, cov_yy],
    ], dtype=np.float64))
    eigenvalues = np.maximum(eigenvalues, 0.04)
    sigma_minor, sigma_major = np.sqrt(eigenvalues)
    fwhm_minor = float(2.355 * sigma_minor)
    fwhm_major = float(2.355 * sigma_major)
    return {
        "center_rc": [cy, cx],
        "fitted_centroid_rc": [cy_fit, cx_fit],
        "fwhm_major_px": fwhm_major,
        "fwhm_minor_px": fwhm_minor,
        "axis_ratio": float(fwhm_minor / max(fwhm_major, 1e-8)),
        "peak_above_local_background": peak,
    }


def measure_paired_star_profiles(reference, candidate, max_stars=80,
                                 min_stars=3, min_separation=7,
                                 fit_radius=8):
    """Compare the same unsaturated stellar coordinates before and after.

    The report is a processing/aesthetic diagnostic.  It does not claim
    physical acquisition resolution when either input has been stretched.
    """
    reference = np.asarray(reference, dtype=np.float32)
    candidate = np.asarray(candidate, dtype=np.float32)
    if reference.shape != candidate.shape:
        raise ValueError("reference and candidate must have identical shapes")
    if not np.all(np.isfinite(reference)) or not np.all(np.isfinite(candidate)):
        raise ValueError("reference and candidate must contain finite values")

    def luminance(value):
        if value.ndim == 2:
            return value
        if value.ndim != 3 or value.shape[2] < 3:
            raise ValueError("paired star profiles require gray or RGB images")
        return (
            0.2126 * value[..., 0]
            + 0.7152 * value[..., 1]
            + 0.0722 * value[..., 2]
        )

    ref_gray = luminance(reference)
    candidate_gray = luminance(candidate)
    margin = int(fit_radius) + 2
    if min(ref_gray.shape) <= margin * 2:
        return {
            "status": "insufficient_samples",
            "reason": "image_too_small",
            "n_paired_stars": 0,
            "data_domain": "star_layer_processing_diagnostic",
        }
    interior = ref_gray[margin:-margin, margin:-margin]
    threshold = float(np.percentile(interior, 99.5))
    local_max = maximum_filter(interior, size=max(3, int(min_separation)))
    peak_mask = (interior == local_max) & (interior > threshold)
    yy, xx = np.where(peak_mask)
    yy += margin
    xx += margin
    order = np.argsort(ref_gray[yy, xx])[::-1]
    coordinates = []
    for index in order:
        cy, cx = int(yy[index]), int(xx[index])
        peak = float(ref_gray[cy, cx])
        if peak >= 0.995:
            continue
        if any(
            (cy - ky) ** 2 + (cx - kx) ** 2 < min_separation ** 2
            for ky, kx in coordinates
        ):
            continue
        coordinates.append((cy, cx))
        if len(coordinates) >= int(max_stars):
            break

    pairs = []
    for coordinate in coordinates:
        before = _profile_shape_at(ref_gray, coordinate, fit_radius=fit_radius)
        after = _profile_shape_at(
            candidate_gray,
            coordinate,
            fit_radius=fit_radius,
        )
        if before is None or after is None:
            continue
        pairs.append((before, after))

    if len(pairs) < int(min_stars):
        return {
            "status": "insufficient_samples",
            "reason": "too_few_valid_unsaturated_isolated_stars",
            "n_reference_candidates": len(coordinates),
            "n_paired_stars": len(pairs),
            "data_domain": "star_layer_processing_diagnostic",
            "coordinate_convention": "row_col",
        }

    before_major = np.array(
        [before["fwhm_major_px"] for before, _after in pairs],
        dtype=np.float64,
    )
    after_major = np.array(
        [after["fwhm_major_px"] for _before, after in pairs],
        dtype=np.float64,
    )
    before_axis = np.array(
        [before["axis_ratio"] for before, _after in pairs],
        dtype=np.float64,
    )
    after_axis = np.array(
        [after["axis_ratio"] for _before, after in pairs],
        dtype=np.float64,
    )
    ratios = after_major / np.maximum(before_major, 1e-8)
    centroid_shifts = np.asarray(
        [
            np.hypot(
                after["fitted_centroid_rc"][0]
                - before["fitted_centroid_rc"][0],
                after["fitted_centroid_rc"][1]
                - before["fitted_centroid_rc"][1],
            )
            for before, after in pairs
        ],
        dtype=np.float64,
    )
    peak_ratios = np.asarray(
        [
            after["peak_above_local_background"]
            / max(before["peak_above_local_background"], 1e-8)
            for before, after in pairs
        ],
        dtype=np.float64,
    )
    median_ratio = float(np.median(ratios))
    review_required = bool(
        len(pairs) >= max(int(min_stars), 5)
        and (
            median_ratio > 1.50
            or float(np.percentile(ratios, 90.0)) > 1.90
        )
    )
    return {
        "status": "review_required" if review_required else "ok",
        "n_reference_candidates": len(coordinates),
        "n_paired_stars": len(pairs),
        "data_domain": "star_layer_processing_diagnostic",
        "physical_resolution_claim": False,
        "coordinate_convention": "row_col",
        "reference_fwhm_major_median_px": float(np.median(before_major)),
        "candidate_fwhm_major_median_px": float(np.median(after_major)),
        "candidate_to_reference_fwhm_ratio_median": median_ratio,
        "candidate_to_reference_fwhm_ratio_p90": float(
            np.percentile(ratios, 90.0)
        ),
        "reference_axis_ratio_median": float(np.median(before_axis)),
        "candidate_axis_ratio_median": float(np.median(after_axis)),
        "candidate_to_reference_centroid_shift_median_px": float(
            np.median(centroid_shifts)
        ),
        "candidate_to_reference_centroid_shift_p95_px": float(
            np.percentile(centroid_shifts, 95.0)
        ),
        "candidate_to_reference_peak_ratio_median": float(
            np.median(peak_ratios)
        ),
        "candidate_to_reference_peak_ratio_p10": float(
            np.percentile(peak_ratios, 10.0)
        ),
        "candidate_to_reference_peak_ratio_p90": float(
            np.percentile(peak_ratios, 90.0)
        ),
        "review_required": review_required,
        "engineering_thresholds": {
            "median_fwhm_ratio_max": 1.50,
            "p90_fwhm_ratio_max": 1.90,
        },
        "sample_centers_rc": [
            before["center_rc"] for before, _after in pairs[:64]
        ],
    }


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


def _analyze_connected_components(labeled, image_gray, fwhm, star_threshold, galaxy_center=None,
                                   galaxy_core_radius=None):
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
      5. 质心重合于星系中心且尺寸显著 → 星系核球（reject，保留在无星底层）
      6. 其余 → 保留，按特征分配置信度

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
    # 峰值阈值同样必须锚定噪声，不能锚定画面里最亮的东西。
    # 旧式 `background + (p99.9 - background) * star_threshold * 0.18` 在 M31 上
    # 算出 0.0114，而全图 p99 只有 0.0111 —— 等于只放行最亮 ~1% 的像素，
    # 暗星全被判成 noise。
    noise = _robust_noise_scale(image_gray)
    abs_peak_thresh = background + _DETECT_PEAK_SIGMAS * max(noise, 1e-9)

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
        if galaxy_center is not None:
            g_cy, g_cx = galaxy_center
            cy_comp = (by0 + by1) / 2.0
            cx_comp = (bx0 + bx1) / 2.0
            dist_to_center = np.sqrt((cy_comp - g_cy)**2 + (cx_comp - g_cx)**2)
            core_radius = float(galaxy_core_radius) if galaxy_core_radius else max(
                15.0, GALAXY_CORE_PROTECTION_MIN_RADIUS_FRACTION * min(h, w)
            )
            if dist_to_center <= core_radius and area >= min(50, 2 * min_area):
                reject_reason = "galaxy_nuclear_core"

        if reject_reason is None:
            if area < min_area:
                reject_reason = "hot_pixel"
            elif area > max_area:
                peak_prom = peak / max(mean_val, 1e-9)
                # 只有当区域既显著超过星点尺寸，又缺乏恒星特有的集中尖锐高光峰值时，才判定为星云亮核
                # 真实亮星（包含饱和星与亮星光晕）具有极高的峰值和中心集中度
                if (
                    peak >= abs_peak_thresh * 2.0
                    and peak_prom >= 2.0
                    and area <= min(int(0.05 * h * w), int(max_area * 1.5))
                    and aspect_ratio < 2.5
                ):
                    reject_reason = None
                else:
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
                            return_details=False, galaxy_center=None,
                            galaxy_core_radius=None):
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

    # 用图像自身的暗区估计各尺度 tophat 的**噪声水平**，作为唯一阈值基准。
    #
    # 旧实现用 `max(median + 4·MAD of positive, min(p97 of positive, 0.85·factor·max))`：
    # 三项全是**相对画面内容**的统计量 ——
    #   · `p97 of positive` 在稀疏星场里几乎等于最大值（合成测试里只剩 1 颗星被检出）
    #   · `0.85·factor·max` 直接锚定最亮星，M31 上算出 0.0985，
    #     把 3.12% 的候选集压到 0.044%（检出 115 个，真实约 1985 个）
    # 只要阈值锚定"画面里有什么"，检测结果就取决于最亮的那个天体。
    # 改为在背景掩膜内取 median + 4·MAD：无噪声的合成图会得到 ≈0（全部星点通过），
    # 真实星场则得到与噪声成正比的稳健阈值。
    background_mask = img_gray <= np.percentile(img_gray, 30.0)
    if int(np.count_nonzero(background_mask)) < 100:
        background_mask = np.ones_like(img_gray, dtype=bool)

    combined_response = np.zeros((h, w), dtype=np.float32)
    for scale_idx, tophat, radius in scale_results:
        # 大尺度（星芒）略放宽，小尺度（暗星）略收紧
        scale_thresh_factor = max(0.62, 1.0 - scale_idx * 0.11)
        background_values = tophat[background_mask]
        # 只用正值部分估噪声：white_tophat 恒非负，且对纯噪声**大多数像素恰好为 0**
        # （opening 取局部极大，必然 ≥ 原值），因此对含零的整段取 median/MAD 会
        # 直接塌成 0 —— 合成测试图上阈值归零，整幅图连成一个连通域。
        positive = background_values[background_values > 0]
        if positive.size >= 50:
            median = float(np.median(positive))
            mad = float(np.median(np.abs(positive - median))) * 1.4826
            noise_level = median + 4.0 * max(mad, 1e-9)
        else:
            noise_level = 0.0
        th_abs = noise_level * _DETECT_TOPHAT_SIGMA_MULTIPLIER * scale_thresh_factor
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
            'n_components_total': 0, 'n_components_kept': 0,
            'components': [], 'scale_info': []
        }
        if return_details:
            return star_mask, confidence, details
        return star_mask, confidence

    # 各尺度的阈值已在上面按"背景噪声"锚定，这里不再叠加第二道相对阈值。
    # 旧实现还有一道 `star_threshold * combined_response.max() * 0.12`，
    # 是相对最亮星的：M31 上它把 3.12% 的候选集压到 0.044%（检出 115 个，
    # 而局部极大法在同一张图上有约 1985 个）。
    binary_mask = combined_response > 0
    # Top-hat response often leaves only the PSF core. Expand by a small,
    # FWHM-bounded radius before component analysis so ordinary faint stars
    # are not misclassified as one-pixel hot pixels.
    core_radius = max(1, int(round(fwhm * 0.35)))
    binary_mask = binary_dilation(binary_mask, structure=disk(core_radius))
    labeled, n_features = ndi_label(binary_mask)

    # 5. 连通域特征过滤
    kept_mask, components = _analyze_connected_components(
        labeled, img_gray, fwhm, star_threshold, galaxy_center=galaxy_center,
        galaxy_core_radius=galaxy_core_radius
    )

    # 6. 膨胀以覆盖星点 PSF 边缘与扩散翼（防止去星后残留亮环或在拉伸中激化为白斑）
    dilation_radius = max(2, min(5, int(round(fwhm * 0.6))))
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
    快速行进法修复（FMM-based）。

    适合：小星点、孤立亮斑。速度快。

    注意：OpenCV 的 INPAINT_TELEA 在 32F 下数值不稳定（实测会输出
    [-1.41, 1.37] 的越界值），因此本函数在 float 通路上实际调用
    INPAINT_NS。保留函数名是为了不破坏既有调用点与报告字段。
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

    # cv2.inpaint 原生支持 32F 输入，直接用 float 即可。
    #
    # 旧实现先把图像按自身峰值缩放到 [0,1] 再量化成 uint8，对线性深空数据
    # 是灾难性的：背景 0.0017 相对峰值 0.97 只占 0.45/255，四舍五入后整片
    # 背景直接归零 —— 实测修复后 93.7% 的像素变成纯 0，去星必然失败。
    #
    # 但 OpenCV 的 INPAINT_TELEA 在 32F 下数值不稳定：同样一张图会输出
    # [-1.41, 1.37] 的越界值（背景 0.0017、星点 0.95 的对比下），
    # 而 INPAINT_NS 在 32F 下能精确插值回背景电平。因此 float 通路统一走 NS。
    mask_u8 = _mask_to_uint8(star_mask)
    source = np.clip(image, 0, 1).astype(np.float32)
    return np.clip(cv2.inpaint(source, mask_u8, radius, cv2.INPAINT_NS), 0, 1)


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

    # 同 inpaint_telea：直接用 32F，不要量化到 uint8
    mask_u8 = _mask_to_uint8(star_mask)
    source = np.clip(image, 0, 1).astype(np.float32)
    return np.clip(cv2.inpaint(source, mask_u8, radius, cv2.INPAINT_NS), 0, 1)


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


# 去星会把星点自身的光通量从平滑图中移除，导致延展结构亮度出现约 8% 的
# 系统性下降。这是去星的预期结果而非误伤，衡量误伤时需扣除该基线。
#
# 以下常量全部**从分档默认值派生**，不再独立写死：v0.1.33 把阈值散落成
# 模块级常量，新增分档时容易只改一处而漏掉另一处。现在唯一事实源是
# star_removal_profiles.DEFAULTS，这里的名字保留是为了不破坏既有引用
# （CLI / 测试 / 文档都直接读它们）。
_STAR_REMOVAL_BASELINE_CHANGE = _REMOVAL_DEFAULTS.baseline_change

# ── 星云误伤（nebula_damage_ratio）的可测性边界 ──
# 主体掩膜由 ``orig_smooth >= p75`` 圈定，是**相对**分位。实测该定义下
# 掩膜占比**恒为 0.25**（4 份母版全部 ``0.250000``）⇒ 「掩膜占比过高」
# 这个失败形态在数学上不可能发生，``_NEBULA_MASK_MAX_COVERAGE``
# 因此已从判定链移除（v0.1.34，标 deprecated，见
# star_removal_profiles.DEPRECATED_FIELDS）。常量保留仅为 schema
# 兼容与人工观测，**不得据它分档或判定**。
_NEBULA_MASK_MAX_COVERAGE = _REMOVAL_DEFAULTS.mask_max_coverage
# 主体（p75）与安静区（p25）的最小**相对**亮度比 —— 现在是误伤可测性的
# **唯一**判据。必须用比值而非绝对落差：quality 评估跑在**拉伸前的
# 线性域**，绝对亮度极小（NGC6888 实测 p75-p25 仅 0.00019），任何绝对
# 阈值都会误判。比值在纯暗场 + 小星点时为 1.0（p25=p75=背景）⇒ 判为
# 「无主体、不可测」，这正是我们要的：那类数据里「主体是否被损伤」
# 无从谈起。
_NEBULA_MIN_SUBJECT_SEPARATION = _REMOVAL_DEFAULTS.min_subject_separation



def _robust_noise_scale(gray):
    """稳健背景噪声尺度（MAD × 1.4826），用于给各判据提供绝对锚点。

    必须排除恰好为 0 的像素：DBE 之后大片背景会被 clip 到 0，
    直接对整幅图取 MAD 会退化成 0，使所有"相对噪声"阈值失效。
    """
    positive = gray[gray > 0]
    if positive.size < 100:
        return 0.0
    lower = positive[positive <= float(np.percentile(positive, 50))]
    if lower.size < 100:
        return 0.0
    median = float(np.median(lower))
    return float(np.median(np.abs(lower - median))) * 1.4826


def estimate_star_removal_quality(original, starless, target_type=None,
                                  profile=None, stars=None):
    """
    评估星点分离质量。

    四个子指标都必须是有**绝对锚点**的测量值，否则会退化成常数或恒等于满分/零分：

      - residual_star_fraction：残留星点应当是"亮且紧凑"的点状结构。
        旧版用 `max(阈值, 0.1)` 这个绝对亮度下限，任何亮于 0.1 的像素都算残留星，
        于是星系/星云本体被当成残留星 —— 恒等操作（完全没去星）也会报出残留。
      - nebula_damage_ratio：误伤应在**有真实信号的星云主体**上衡量。
        旧版除以最暗 30% 像素的均值，而该值等于噪声底（实测约 1e-4），
        任何微小扰动都会让比值冲到 ≈1.0，与真实误伤程度无关。
      - high_grad_ratio：应衡量去星**新引入**的硬边。
        旧版判据是 `梯度 > p95(梯度) * 0.5`，用图像自身的分布定阈值，
        对任何图像都必然命中约 10~20% 的像素，等于恒定扣满该项惩罚。
        新版改为"去星后梯度显著超过原图同一位置"，并用绝对噪声尺度锚定：
        原图在星点处本就有大梯度，好的修复只会让它变小。

    target_type / profile：门禁阈值按目标类型分档，见
    :mod:`star_removal_profiles`。两者都缺省时用默认档，行为与分档前逐位一致。

    stars：星点层（可选）。**星层归一化读数必须显式传入**，不能由
    orig - starless 派生 —— 见下方「为什么不能派生」。

    v0.1.39 新增**星层归一化**读数（nebula_damage_ratio_star_flux）
    并与原读数**并存**。原读数把「被移走的星光」计入误伤，实测
    corr(星层占主体比, nebula_damage_ratio) = +0.9417，健康组读数因此
    跨 10 倍（0.0135~0.3529）⇒ 双向夹逼必然为空、阈值无法标定。
    **原字段语义与取值完全不变**；新读数缺省不启用，避免一次性改两处
    导致读数无法归因。

    为什么星层不能由 orig - starless 派生
    -----------------------------------------
    实测（M8 注入 beta 递减的合成损伤）：若星层按 orig - starless
    派生，注入的损伤会**同时**被算进「星层」，分母被补偿掉，读数变成
    **非单调**（beta 0→0.30：0.0942→0.0948→0.0917→0.0773，越损伤越小）。
    派生量与被测量共用同一个减法 ⇒ 修正项与信号项自相消。
    星层必须是**独立观测量**（去星时的产物），才不含待测的损伤。
    """
    original = np.asarray(original, dtype=np.float32)
    starless = np.asarray(starless, dtype=np.float32)
    prof = _removal_profile(target_type, profile)

    if original.ndim == 3:
        orig_gray = 0.299 * original[..., 0] + 0.587 * original[..., 1] + 0.114 * original[..., 2]
        sl_gray = 0.299 * starless[..., 0] + 0.587 * starless[..., 1] + 0.114 * starless[..., 2]
    else:
        orig_gray = original
        sl_gray = starless

    bg_level = float(np.percentile(sl_gray, 10))
    noise_scale = _robust_noise_scale(sl_gray)
    # 噪声为 0 时（例如全黑输入）给一个极小下限，避免零阈值把整幅图判为残留
    gate = max(noise_scale, 1e-6)

    # ── 残留星点：亮且紧凑 ──
    # 高通响应只保留点状结构；同时要求像素显著高于背景，延展的星系/星云被排除。
    local_bg = gaussian_filter(sl_gray, sigma=4.0)
    highpass = sl_gray - local_bg
    residual_mask = (highpass > 5.0 * gate) & (sl_gray > bg_level + 10.0 * gate)
    residual_fraction = float(np.mean(residual_mask))

    # ── 星云误伤：只在延展结构上比较，且必须排除星点 ──
    # 关键：星点本身就是画面最亮的部分，若直接按原始亮度取"主体"掩膜，
    # 掩膜会包含星点，于是"把星去掉"这件事本身就被算成误伤（实测报 0.17）。
    # 用重平滑（sigma=8）压掉点状结构后再取掩膜与比较，剩下的才是星系/星云本体。
    #
    # **可测性前置判据**（借鉴 aiseestart 把「真实低覆盖」与「测量不可用」
    # 分成两个码的做法）：主体掩膜靠 ``orig_smooth >= p75`` 圈定，p75 是
    # **相对**分位。NGC6888 实测 damage=0.516 无法区分是**真实误伤**还是
    # **口径偏严**，因此在算比值之前先判可测性，不满足就报 unavailable ——
    # 而不是给一个偏高但看似确定的 damage。
    orig_smooth = gaussian_filter(orig_gray, sigma=8.0)
    sl_smooth = gaussian_filter(sl_gray, sigma=8.0)
    signal_level = float(np.percentile(orig_smooth, 75))
    # 主体与安静区的分离度。**注意它衡量的是「延展结构对比度」**，
    # 不是「有没有主体」：纯暗场 + 小星点时 p25 与 p75 都落在背景上，
    # separation≈1.0，但那类数据根本没有星云主体可言，damage 无从谈起。
    quiet_level = float(np.percentile(orig_smooth, 25))
    subject_separation = (signal_level / max(quiet_level, 1e-8)
                         if quiet_level > 1e-8 else 0.0)
    nebula_pixels = int(np.count_nonzero(orig_smooth >= signal_level)) \
        if signal_level > 1e-6 else 0
    nebula_coverage = nebula_pixels / float(orig_smooth.size) \
        if orig_smooth.size else 0.0
    # 可测性 = 画面里确实有**可比的延展主体**。
    # 判据只有一条：主体/背景亮度比超过下限。掩膜内原图与无星图一样亮时，
    # 差值落在噪声内，比值是放大噪声（纯暗场 + 小星点，没有星云主体）。
    #
    # v0.1.34 移除了原先的 ``nebula_coverage <= mask_max_coverage`` 一条：
    # 掩膜由**相对**分位 p75 圈定，占比数学上恒等于 0.25，判据从不触发
    # （4 份母版实测全部 0.250000）。保留一个死判据会让后续维护者误以为
    # 「掩膜退化为全图」场景已受控。coverage 仍作为**观测量**输出。
    damage_measurable = (
        signal_level > 1e-6
        and nebula_pixels > 100
        and subject_separation >= prof.min_subject_separation
    )
    if damage_measurable:
        nebula_mask = orig_smooth >= signal_level
        orig_ref = float(np.median(orig_smooth[nebula_mask]))
        sl_ref = float(np.median(sl_smooth[nebula_mask]))
        raw_change = abs(orig_ref - sl_ref) / max(orig_ref, 1e-8)
    else:
        raw_change = 0.0

    # ── 星层归一化判据（v0.1.39，与上面的 raw_change 并存）──
    #
    # 上面那个比值把「被移走的星光」算成了「误伤」：分母是**原图**主体
    # 亮度，而分母里含星点通量；分子只剩无星层。StarNet 取走星点后分子
    # 掉、分母不动，读数虚高多少**取决于画面里有多少星** —— 与目标类型
    # 和主体形态无关。
    #
    # 实测（7 份真实 StarNet2 产物，跨机型/跨分辨率）：
    #   corr(星层占主体比, raw_change) = +0.9417
    #   健康组 raw_change 极差 0.3393（跨 10 倍 ⇒ 夹逼必然为空）
    # 星层通量在掩膜内占主体 0.024~0.447（跨 18 倍），正是漂移来源。
    #
    # 处置：把**同一掩膜内**的星层通量从分母里减掉，让分母只含延展结构。
    # 依据是加法恒等式 ``orig - starless - stars`` 残差 ≤ 0.0008（7 份
    # 全部），即「原图 − 无星图 = 星光 + 结构损伤」在数据上成立。
    star_free_ref = 0.0
    star_flux_fraction = 0.0
    star_flux_damage_ratio = 0.0
    star_flux_measurable = False
    star_free_snr = 0.0
    star_free_snr_available = False
    # 归一化只在**星层可用且显式开启**时计算。两者缺一不可：
    #   - 没有 stars 入参 ⇒ 只能退回 orig-starless 派生，而派生量会
    #     把待测损伤一并算成星光（见 docstring），使判据非单调；
    #   - star_flux_normalize=False ⇒ 只出旧读数，不出归一化读数。
    if (prof.star_flux_normalize and damage_measurable and stars is not None):
        stars_arr = np.asarray(stars, dtype=np.float32)
        if stars_arr.ndim == 3:
            stars_gray = (0.299 * stars_arr[..., 0] + 0.587 * stars_arr[..., 1]
                          + 0.114 * stars_arr[..., 2])
        else:
            stars_gray = stars_arr
        if stars_gray.shape == orig_gray.shape:
            stars_smooth = gaussian_filter(stars_gray, sigma=8.0)
            stars_ref = float(np.median(stars_smooth[nebula_mask]))
            star_free_ref = orig_ref - stars_ref
            star_flux_fraction = stars_ref / max(orig_ref, 1e-8)
            # 扣除后主体可能趋近 0（高星密度 + 小目标），比值失去意义。
            star_flux_measurable = (
                star_free_ref > prof.min_star_free_subject_level)
            if star_flux_measurable:
                raw_star_free = (abs(star_free_ref - sl_ref)
                                 / max(star_free_ref, 1e-8))
                star_flux_damage_ratio = max(
                    0.0, raw_star_free - prof.star_flux_baseline_change)
            # ── 观测量：star_free_snr（**不参与任何判定**）──
            # v0.1.39 实测**否决**了「用 SNR 当置信下界」的想法：
            #   corr(star_free_snr, 归一化 damage) = -0.4464（方向相反），
            #   且 7 份健康样本的 SNR 跨 1.6~385.4。
            # M31 的读数为 0 是因为 starfrac 仅 0.0026（星光不在主体
            # 掩膜内），**不是**噪声淹没 —— 它的 SNR 恰恰是全组最高的。
            # 深层原因：掩膜 = 平滑图 p75 = 最亮 25%，装的是真实结构
            # 而非噪声。实测超额峰度 105~504、偏度 -9~-21 ⇒ MAD 在
            # 这种重尾分布上没有统计意义（截尾均值与中位数差 24%~779%）。
            # 保留它只为报告可查；**任何判定都不得引用它**。
            _diff = sl_smooth[nebula_mask] - orig_smooth[nebula_mask]
            _mad = (1.4826 * float(np.median(np.abs(_diff
                                                 - np.median(_diff)))))
            # orig/sl 各自含独立噪声 ⇒ 差值的噪声是单侧的 √2 倍
            _sigma_diff = _mad / float(np.sqrt(2.0))
            if _sigma_diff > 0.0:
                star_free_snr = star_free_ref / _sigma_diff
                star_free_snr_available = True

    # 去星必然会把星点自身的光通量从平滑图里拿掉（实测占延展结构亮度的 ~8%），
    # 这部分是去星的**预期结果**，不是误伤。只统计超出该基线的结构性改变。
    damage_ratio = max(0.0, raw_change - prof.baseline_change)

    # ── 修复伪影：去星新引入的硬边 ──
    grad_orig = np.sqrt(sobel(orig_gray, axis=0) ** 2 + sobel(orig_gray, axis=1) ** 2)
    grad_sl = np.sqrt(sobel(sl_gray, axis=0) ** 2 + sobel(sl_gray, axis=1) ** 2)
    edge_floor = max(6.0 * gate, 1e-6)
    high_grad_ratio = float(np.mean(grad_sl > np.maximum(grad_orig * 2.0, edge_floor)))

    # 综合评分
    # residual 阈值取 2%：星点掩膜通常只占画面 1~5%，若去星完全没生效，
    # 残留率就是掩膜本身的占比，因此 5% 的老阈值过宽，恒等操作都能蒙混过关。
    needs_starnet = (residual_fraction > prof.needs_starnet_residual
                     or damage_ratio > prof.needs_starnet_damage)

    # 修复质量评分（0-1，1 最好）。
    # 权重按"可接受上限"标定：残留 2.5%、误伤 22%、新硬边 6.7% 分别扣满该项。
    repair_score = 1.0
    repair_score -= min(residual_fraction * prof.repair_weight_residual,
                        prof.repair_cap_residual)
    repair_score -= min(damage_ratio * prof.repair_weight_damage,
                        prof.repair_cap_damage)
    repair_score -= min(high_grad_ratio * prof.repair_weight_gradient,
                        prof.repair_cap_gradient)
    repair_score = max(0.0, min(1.0, repair_score))

    report = {
        'residual_star_fraction': round(residual_fraction, 4),
        'nebula_damage_ratio': round(damage_ratio, 4),
        'high_gradient_ratio': round(high_grad_ratio, 4),
        'repair_quality_score': round(repair_score, 3),
        'needs_starnet_plus': needs_starnet,
        # 误伤指标是否可测。False 时 nebula_damage_ratio 不可作为拒绝依据
        # （主体对比度不足），门禁应走 unavailable。
        'nebula_damage_measurable': bool(damage_measurable),
        # coverage 保留为**观测量**，不再参与判定：p75 相对分位下它恒为
        # 0.25（star_removal_profiles.DEPRECATED_FIELDS 记录了否决理由）。
        'nebula_mask_coverage': round(nebula_coverage, 4),
        'subject_separation': round(subject_separation, 3),
        # ── 星层归一化族（v0.1.39）：与 nebula_damage_ratio 并存的观测量 ──
        # 旧字段一律不动：一次改两处会让「哪个改动带来了读数变化」无法
        # 归因。三个新字段的 0/False 语义是**「没算」**，不是「算出来是 0」
        # —— 所以下面显式带一个 available 标志。
        'nebula_damage_ratio_star_flux': round(star_flux_damage_ratio, 4),
        'nebula_damage_star_flux_measurable': bool(star_flux_measurable),
        'star_flux_metric_available': bool(
            prof.star_flux_normalize and damage_measurable
            and stars is not None),
        # 掩膜内星层通量占原图主体的比值。**这是旧判据漂移的根因量**：
        # 实测 corr(该值, nebula_damage_ratio) = +0.9417。
        'star_flux_fraction': round(star_flux_fraction, 4),
        # 扣除星通量后的主体基准，供人工核对是否趋近 0（趋近即不可测）。
        'star_free_subject_level': float(f'{star_free_ref:.6g}'),
        # ── 相对判据基线（v0.1.44）──
        # 本值**就是**基线：它本身就是「该图健康态下的 |1-q_sf|」。
        # 写进 result.json 后，后续同图运行读回作对照，即可度量
        # 「这次比上次多损坏了多少」——绝对判据做不到这一点。
        'star_flux_baseline': (
            round(abs(1.0 - (sl_ref / max(star_free_ref, 1e-8))), 4)
            if star_flux_measurable else None),
        # ⚠ **纯观测量，任何判定都不得引用**（v0.1.39 实测否决了
        # 「SNR 当置信下界」：corr(SNR, damage) = -0.4464，方向相反）。
        'star_free_snr': (round(star_free_snr, 2)
                         if star_free_snr_available else None),
        'star_free_snr_available': bool(star_free_snr_available),
        'quality': ('good' if repair_score > prof.quality_band_good
                    else ('marginal' if repair_score > prof.quality_band_marginal
                          else 'poor')),
    }

    if not damage_measurable:
        report['quality'] = 'unmeasurable'
        report['suggestion'] = (
            '星云误伤指标在当前数据上不可测：'
            f'主体/背景亮度比 {subject_separation:.2f}'
            f'（下限 {prof.min_subject_separation:.2f}）—— '
            '无星云主体的纯暗场，掩膜内原图与无星图一样亮，'
            '差值落在噪声内。此时残留星与硬边指标仍有效，'
            '但不能用 damage 判定去星是否伤到星云。'
        )
    elif needs_starnet:
        report['suggestion'] = (
            '形态学去星质量不足 — 建议使用 StarNet++ v2 CLI 生成无星图，'
            '然后通过 --external-starless 接入管线'
        )

    # 归一化判据自身不可测时必须单独说明。它与 nebula_damage_measurable
    # **不是同一个条件**：前者测的是「主体对比度够不够」，后者测的是
    # 「扣掉星光后主体还剩不剩」。混用会让「这张图测不了」的原因被吞掉。
    if damage_measurable and prof.star_flux_normalize and stars is not None \
            and not star_flux_measurable:
        report['star_flux_note'] = (
            f'星层归一化判据不可测：扣除掩膜内星通量后主体基准仅 '
            f'{star_free_ref:.3g}（下限 {prof.min_star_free_subject_level:g}）—— '
            '该目标的星光占比过高，扣掉后主体已无法与噪声区分。'
            '此时以 nebula_damage_ratio 为准，但它含星通量成分。'
        )

    return report


def inpaint_chroma_guard(image_inpainted, star_mask, blur_sigma=16.0):
    """
    对 Inpaint 修复后的无星图应用色度保护。
    原理：在保持 Inpaint 亮度不变的前提下，将星坑内部的色彩通道比例对齐至周围低频背景场，
    彻底消除折射镜红光色差光晕向内扩散造成的刺眼红橙圆斑。
    """
    image_inpainted = np.asarray(image_inpainted, dtype=np.float32)
    if image_inpainted.ndim != 3 or image_inpainted.shape[2] < 3:
        return image_inpainted

    mask_bool = np.asarray(star_mask > 0.5, dtype=bool)
    if not np.any(mask_bool):
        return image_inpainted

    # 计算低频平滑背景色彩场
    bg_smooth = gaussian_filter(image_inpainted, sigma=blur_sigma)
    bg_lum = (
        0.2126 * bg_smooth[..., 0]
        + 0.7152 * bg_smooth[..., 1]
        + 0.0722 * bg_smooth[..., 2]
    )
    bg_ratio_r = bg_smooth[..., 0] / np.maximum(bg_lum, 1e-6)
    bg_ratio_g = bg_smooth[..., 1] / np.maximum(bg_lum, 1e-6)
    bg_ratio_b = bg_smooth[..., 2] / np.maximum(bg_lum, 1e-6)

    # 软羽化蒙版 (稍向外扩张 2px 并平滑羽化，确保接缝自然过渡)
    feathered = gaussian_filter(
        binary_dilation(mask_bool, structure=disk(2)).astype(np.float32),
        sigma=2.0
    )
    feathered = np.clip(feathered * 1.5, 0.0, 1.0)[..., None]

    # Inpaint 结果的感官亮度
    lum = (
        0.2126 * image_inpainted[..., 0]
        + 0.7152 * image_inpainted[..., 1]
        + 0.0722 * image_inpainted[..., 2]
    )
    chroma_clean = np.stack(
        [lum * bg_ratio_r, lum * bg_ratio_g, lum * bg_ratio_b],
        axis=-1
    )

    result = image_inpainted * (1.0 - feathered) + chroma_clean * feathered
    return np.clip(result, 0.0, 1.0).astype(np.float32)


def apply_star_halo_guard(starless, stars=None, fwhm=3.0, halo_threshold=0.6, radius_factor=3.5):
    """
    亮星星晕守卫（Star Halo Guard）。
    
    去星算法（无论是形态学还是 StarNet2）往往能去除亮星核，但由于光学系统（特别是双窄带滤镜/折射镜）
    的色差或衍射光晕，亮星外周 2~4 倍 FWHM 区域往往残留一圈发红或发青的假彩色星晕（Halo）。
    在非线性强拉伸后，这些暗弱星晕会被成百倍放大为显眼的彩色圆环。
    
    本函数在大亮星周围的星晕过渡带中，保留背景感官亮度 L 绝对不变，
    平滑过渡其色度比例至周围背景场，彻底消除亮星外围残余晕轮。
    
    参数:
        starless: (H, W, 3) 浮点无星图
        stars: (H, W, 3) 或 (H, W) 浮点星点图，若为 None 则从 starless 局部残差分析
        fwhm: 恒星 FWHM（像素）
        halo_threshold: 亮星判定阈值 (默认 0.6)
        radius_factor: 光晕影响半径倍数 (默认 3.5x FWHM)
    
    返回:
        平整星晕后的无星图 (H, W, 3)
    """
    starless = np.asarray(starless, dtype=np.float32)
    if starless.ndim != 3 or starless.shape[2] < 3:
        return starless

    fwhm = float(fwhm) if fwhm is not None else 3.0
    fwhm = max(1.5, fwhm)

    if stars is not None:
        stars_arr = np.asarray(stars, dtype=np.float32)
        star_signal = np.mean(stars_arr, axis=2) if stars_arr.ndim == 3 else stars_arr
    else:
        star_signal = np.mean(starless, axis=2)

    bright_star_mask = star_signal > halo_threshold
    if not np.any(bright_star_mask):
        p999 = float(np.percentile(star_signal, 99.9))
        if p999 > 0.3:
            bright_star_mask = star_signal >= p999
        else:
            return starless

    if not np.any(bright_star_mask):
        return starless

    outer_r = max(3, int(round(fwhm * radius_factor)))
    dilated_outer = binary_dilation(bright_star_mask, structure=disk(outer_r))
    if not np.any(dilated_outer):
        return starless

    halo_mask = gaussian_filter(dilated_outer.astype(np.float32), sigma=max(1.5, fwhm * 0.75))
    halo_mask = np.clip(halo_mask * 1.2, 0.0, 1.0)[..., None]

    lum = (
        0.2126 * starless[..., 0]
        + 0.7152 * starless[..., 1]
        + 0.0722 * starless[..., 2]
    )
    safe_lum = np.maximum(lum, 1e-6)

    smooth_sigma = max(4.0, fwhm * 3.0)
    ratio_r = starless[..., 0] / safe_lum
    ratio_g = starless[..., 1] / safe_lum
    ratio_b = starless[..., 2] / safe_lum

    smooth_r = gaussian_filter(ratio_r, sigma=smooth_sigma)
    smooth_g = gaussian_filter(ratio_g, sigma=smooth_sigma)
    smooth_b = gaussian_filter(ratio_b, sigma=smooth_sigma)

    chroma_cleaned = np.stack(
        [lum * smooth_r, lum * smooth_g, lum * smooth_b],
        axis=-1
    )

    result = starless * (1.0 - halo_mask) + chroma_cleaned * halo_mask
    return np.clip(result, 0.0, 1.0).astype(np.float32)



def _repair_star_mask(image, star_mask, method, inpaint_radius, apply_chroma_guard=True):
    """Apply one repair strategy and return the result plus actual method."""
    if method == 'telea':
        if HAS_OPENCV:
            repaired, actual = inpaint_telea(image, star_mask, radius=inpaint_radius), 'telea'
        else:
            repaired, actual = _inpaint_fallback(image, star_mask, inpaint_radius), 'gaussian_fallback'
    elif method == 'ns':
        if HAS_OPENCV:
            repaired, actual = inpaint_ns(image, star_mask, radius=inpaint_radius), 'ns'
        else:
            repaired, actual = _inpaint_fallback(image, star_mask, inpaint_radius), 'gaussian_fallback'
    elif method == 'median':
        repaired, actual = remove_stars_median(image, star_mask), 'median'
    else:
        if HAS_OPENCV:
            if int(np.sum(star_mask > 0.5)) > 500:
                repaired, actual = inpaint_ns(image, star_mask, radius=inpaint_radius), 'ns'
            else:
                repaired, actual = inpaint_telea(image, star_mask, radius=inpaint_radius), 'telea'
        else:
            repaired, actual = _inpaint_fallback(image, star_mask, inpaint_radius), 'gaussian_fallback'

    if apply_chroma_guard and image.ndim == 3:
        repaired = inpaint_chroma_guard(repaired, star_mask)

    return repaired, actual


def _safe_star_removal_fallback(image, reason, report=None):
    """Return an unchanged image when star removal is not trustworthy."""
    fallback_report = dict(report or {})
    # 尽量保留检测阶段实测到的 FWHM —— 调用方用它决定后续星点检测的尺度，
    # 丢掉它会退回硬编码默认值，把噪声峰当成星点。
    if fallback_report.get('estimated_fwhm') is None:
        details = fallback_report.get('detection_details') or {}
        if details.get('fwhm') is not None:
            fallback_report['estimated_fwhm'] = details['fwhm']
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
    """定位 StarNet2 / StarNet++ CLI（委托给通用探测器）。

    四级优先：显式 → ``STARNET_PATH`` → ``shutil.which`` → 默认安装目录。
    完整语义与跨平台路径表见 :mod:`external_tools`。

    本函数历史上是「有默认路径兜底」的一方，而 :func:`denoise.
    find_deepsnr_executable` 当时只有 ``shutil.which`` —— 于是同一台机器上
    StarNet 能被发现、DeepSNR 被报「未找到」并静默降级。两者现已统一到
    :mod:`external_tools`，不会再出现单边缺兜底的不对称失败。
    """
    from external_tools import find_executable
    return find_executable("starnet", user_path=user_path)


def run_starnet_cli(image, exe_path, stride=128, timeout=900,
                    return_report=False, domain='mtf', midtones=None):
    """
    运行 StarNet2 CLI 去星。

    domain: 送进 StarNet2 的载荷域。
      - "mtf"（默认）：先做 MTF 拉伸再量化为 16-bit，读回后做逆 MTF 还原到线性。
        **极暗线性数据必须用这个**——线性直接量化会让弱色通道被压成 0（实测星云区
        G 通道 93% 像素精确为 0），StarNet2 输出随之丢掉 OIII，拉伸后整片星云变纯红。
        实测先拉伸可把 G 的量化级数从 70 抬到 5800，通道比值全程保持。
      - "linear"：原样量化（旧行为）。极暗数据下会丢色，仅供向后兼容。

    midtones: MTF 中间调参数，None 则按图像背景自动推导。读回时用**同一个值**
      做逆变换，因此这里只推导一次。

    stride：StarNet2 的推理步长（质量/速度旋钮，官方范围 2~512 且必须为偶数）。
      代价按推理块数 ≈ (W/s)*(H/s) 增长 —— 实测 3839x2159 全幅：
      stride 64 → 2040 块，128 → 510 块，而 **stride 2 → 2073600 块**，
      实测 2400 s 仍未跑完。非法值会在这里**提前拒绝**，不启动子进程：
      StarNet2 自身也要等推理开始后才报错，代价是整帧白跑。
    """
    import os
    import tempfile
    import subprocess
    import sys

    # ── stride 前置校验（v0.1.43）──
    # 官方 CLI: "Stride value. Must be even and between 2 and 512."
    # 实测 StarNet2 2.6.2 的行为：
    #   -s 3    → rc=255 "Stride should be even!"
    #   -s 0    → rc=255 "positive stride no larger than the window"
    #   -s 513  → rc=255 同上
    #   -s 100  → rc=0  （偶数即可；官方**未**要求 2 的幂，故此处不擅自加）
    # 校验放在入口是因为上面的失败都发生在整帧推理**之后**。
    try:
        stride_value = int(stride)
    except (TypeError, ValueError):
        stride_value = None
    if stride_value is None:
        raise ValueError("StarNet2 stride must be an integer, got %r" % (stride,))
    if stride_value % 2 != 0:
        raise ValueError(
            "StarNet2 stride must be even, got %d "
            "（官方 CLI: 'Stride should be even!'）" % stride_value)
    if not (2 <= stride_value <= 512):
        raise ValueError(
            "StarNet2 stride must be between 2 and 512, got %d "
            "（官方 CLI: 'positive stride no larger than the window'）"
            % stride_value)

    parent_dir = os.path.dirname(os.path.abspath(exe_path))
    execution_report = {
        "executable": os.path.abspath(exe_path),
        "stride": int(stride_value),
        "timeout_seconds": int(timeout),
        "domain": str(domain),
        "attempts": [],
    }

    # 成本提示：合法但昂贵时提前告知，而不是让用户在超时后才发现。
    # 经验阈值：块数 > 8000 时按实测速率（stride 128 / 全幅 8MP ≈ 数分钟）已接近
    # 默认 timeout=900s。这里只**打印**，不阻断 —— 阻断合法值属于过度干预。
    try:
        _h, _w = image.shape[:2]
        _tiles = -(-int(_w) // stride_value) * (-(-int(_h) // stride_value))
        execution_report["inference_tiles"] = int(_tiles)
        if _tiles > 8000:
            print(
                "  [WARN] StarNet2 stride=%d 在 %dx%d 上约需 %d 个推理块，"
                "可能超出 timeout=%ds；如需加速请调高 stride（512 最快、2 最准）。"
                % (stride_value, _w, _h, _tiles, int(timeout))
            )
    except Exception:
        pass
    
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

        # 极暗线性数据必须先做 MTF 拉伸再量化：否则弱色通道会被 16-bit 量化压成 0
        # （实测星云区 G 有 93% 像素精确为 0），StarNet2 输出随之丢掉 OIII。
        applied_midtones = None
        if str(domain) == 'mtf':
            from stretch import derive_mtf_midtones, mtf_stretch

            applied_midtones = (float(midtones) if midtones is not None
                                else derive_mtf_midtones(img_to_save))
            img_to_save = np.clip(
                mtf_stretch(img_to_save, midtones=applied_midtones, shadows=0.0),
                0.0, 1.0,
            )
            execution_report["midtones"] = applied_midtones

        img_to_save = (img_to_save * 65535.0).astype(np.uint16)
        
        try:
            # 写入临时文件
            import skimage.io
            skimage.io.imsave(temp_in, img_to_save, check_contrast=False)
        except Exception as e:
            print(f"  [ERROR] 写入 StarNet2 输入临时文件失败: {e}")
            result = (False, None, execution_report)
            return result if return_report else result[:2]
            
        # ── 命令行格式（v0.1.43：删除 legacy 位置参数回退）──
        # 旧实现在新旧两种语法间来回重试。但实测 StarNet2 2.6.2 的位置参数
        # 语法**已不存在**：
        #     StarNet2 <in> <out> 128  →  PARSE ERROR: Couldn't find match
        # 保留它只会让每次失败**再白跑一遍整帧**，而且 legacy 的第 4 个位置
        # 参数在新 CLI 里语义完全不同（-n 是 unscreen 输出路径），一旦某个
        # 旧版二进制真的接受它，就会写出错误的产物。
        # 现在的契约：只发 -i/-o/-s；非 StarNet2 的同名工具请显式传正确的 exe。
        cmd = [exe_path, "-i", temp_in, "-o", temp_out, "-s", str(stride_value)]
        # StarNet2 失败时**可能已写出残缺文件**（实测 -s 513 → rc=255 且
        # out 存在）。先清掉，保证「读到文件」等价于「这次运行成功」。
        if os.path.exists(temp_out):
            try:
                os.remove(temp_out)
            except OSError:
                pass
        succeeded = False
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
                "command_format": "flags",
                "returncode": int(res.returncode),
                "stderr": (res.stderr or "")[-2000:],
                "stdout": (res.stdout or "")[-2000:],
            }
            execution_report["attempts"].append(attempt)
            if res.returncode == 0:
                succeeded = True
        except subprocess.TimeoutExpired:
            execution_report["attempts"].append({
                "command_format": "flags",
                "error": "timeout",
            })
        except Exception as exc:
            execution_report["attempts"].append({
                "command_format": "flags",
                "error": str(exc),
            })
        if not succeeded:
            print("  [ERROR] StarNet2 执行失败")
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
            # 逆 MTF 还原回线性域（用与正向拉伸同一个 midtones）
            if applied_midtones is not None:
                from stretch import inverse_mtf_stretch

                starless = inverse_mtf_stretch(
                    starless, midtones=applied_midtones, shadows=0.0
                )
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


def protect_subject_layers(image, starless, stars, star_mask, exclude_mask):
    """Keep protected subject signal out of the stellar processing layer."""
    if exclude_mask is None:
        return starless, stars, star_mask
    mask = np.asarray(exclude_mask, dtype=np.float32)
    if mask.shape != image.shape[:2] or not np.all(np.isfinite(mask)) or mask.min() < 0 or mask.max() > 1:
        raise ValueError('Subject mask must match image geometry and contain finite samples in [0, 1]')
    weight = mask[..., None] if image.ndim == 3 else mask
    starless = starless * (1.0 - weight) + image * weight
    stars = stars * (1.0 - weight)
    star_mask = star_mask * (1.0 - mask) if star_mask is not None else None
    return starless.astype(np.float32), stars.astype(np.float32), star_mask


# ── 去星接受判据的两级门禁与可测性常量 ──
# 误伤（nebula_damage_ratio）的接纳线。超过它但在 _GATE_ADVISORY_MULTIPLIER
# 倍以内 ⇒ advisory（带警告接受）；再超过 ⇒ hard_failed（回退）。
# 同上：数值从分档默认值派生，唯一事实源在 star_removal_profiles。
_STAR_REMOVAL_DAMAGE_LIMIT = _REMOVAL_DEFAULTS.damage_limit
_GATE_ADVISORY_MULTIPLIER = _REMOVAL_DEFAULTS.advisory_multiplier
# 质量分不达标时的容忍上限：0 表示重试/修复不得让质量分变差
# （借鉴 aiseestart 的 stage7_starless_repair_max_score_growth = 0.00）。
_STAR_REMOVAL_MAX_SCORE_GROWTH = _REMOVAL_DEFAULTS.max_score_growth


def _star_removal_gate(report, min_quality_score, target_type=None,
                       profile=None):
    """去星接受判据，返回 ``(accepted, gate_state)``。

    三态而非两态，借鉴 aiseestart 的「advisory ×2 → hard」两级门禁，以及
    「真实低覆盖拒绝 / 测量不可用保留」的区分：

    ``ok``
        质量分达标且误伤在接纳线以内。
    ``advisory``
        质量分达标，误伤落在 ``(limit, limit × multiplier]``。接受但记录
        警告 —— 轻微误伤就整图回退形态学，代价（残留星从 1/19 劣化到 1）
        远大于误伤的轻微增加。
    ``unavailable``
        误伤指标**不可测**（主体对比度不足）。不据此拒绝 —— 判定依据不
        存在，不能拿来否决一个已完成的推理。
    ``uncalibrated``
        误伤指标**能测**，但该档的 ``damage_limit`` **尚未标定**。
        同样只记录不判定 —— 阈值不存在，就无法判定读数好坏。
        ⚠ 与 ``unavailable`` 是**不同**状态，排查方向不同：
        前者「阈值没定」（换档/补标定可解），后者「这张图测不了」
        （数据事实）。混用会让报告里看不出是哪一种。
    ``hard_failed``
        质量分不达标，或误伤超过 ``limit × multiplier``。

    改前是一刀切 ``score >= X and damage <= 0.20``：NGC6888 上 StarNet2
    （damage 0.516 / 残留 0.0003，比形态学干净 19 倍）与形态学
    （damage 0.450）**双双被拒**，既没区分优劣也没表达「测不准」。

    ``target_type`` / ``profile`` 选阈值档（见 :mod:`star_removal_profiles`）；
    缺省时用默认档，与分档前逐位一致。
    """
    score = float(report.get('repair_quality_score', 0.0))
    prof = _removal_profile(target_type, profile)
    # 判据选择（v0.1.39）：damage_limit 绑定到**哪个**读数上必须是显式的，
    # 不能由「哪个数字先算出来」决定。默认走旧读数 ⇒ 行为逐位不变。
    if prof.star_flux_normalize:
        # 归一化读数不可用时**不静默回退**到旧读数：两个读数的绝对刻度
        # 不可比（corr=+0.94 但分布不同），拿旧阈值判新读数会得出无意义
        # 的结论。此时按「误伤不可测」处理 ⇒ 门禁走 unavailable。
        damage = float(report.get('nebula_damage_ratio_star_flux', 0.0))
        measurable = bool(report.get('nebula_damage_measurable', True)) and bool(
            report.get('nebula_damage_star_flux_measurable', False))
    else:
        damage = float(report.get('nebula_damage_ratio', 0.0))
        measurable = bool(report.get('nebula_damage_measurable', True))

    # ── 相对判据（v0.1.44）优先于绝对判据 ──
    # 三级语义，逐级降级，**任何一级都不得静默回退到绝对判据**：
    #   1) 有本次基线 + 有基线对照 ⇒ 用 delta（相对判据）
    #   2) 有本次基线 + 无对照     ⇒ 只出基线，判据记 unavailable
    #   3) 无本次基线              ⇒ 沿用既有绝对判据路径（逐位不变）
    # 之所以能静默回退，是因为绝对读数是**每次都算**的既有量，语义不变；
    # 而绝对阈值本身已标定为 None（走 uncalibrated），不会误判。
    baseline = report.get('star_flux_baseline')
    reference = report.get('star_flux_baseline_reference')
    if prof.star_flux_normalize and baseline is not None:
        if reference is None:
            accepted = score >= min_quality_score
            return accepted, 'advisory' if accepted else 'hard_failed'
        delta = max(0.0, float(baseline) - float(reference))
        report['star_flux_delta'] = round(delta, 4)
        if prof.damage_delta_limit is None:
            accepted = score >= min_quality_score
            return accepted, ('uncalibrated' if accepted else 'hard_failed')
        if not measurable:
            accepted = score >= min_quality_score
            return accepted, ('advisory' if accepted else 'hard_failed')
        if score < min_quality_score:
            return False, 'hard_failed'
        if delta <= prof.damage_delta_limit:
            return True, 'ok'
        return False, 'hard_failed'

    if prof.damage_limit is None:
        # 阈值未标定 ⇒ 误伤项**只记录不判定**。
        # 必须放在 measurable 判定**之前**：阈值缺失与读数缺失是两个
        # 独立问题，而「不能判定」的后果是同一个 —— 不用误伤项决策。
        # 质量分**仍参与判定**：它有实测依据（残留 2.5% / 误伤 22% /
        # 硬边 6.7% 三档扣满），应继续拦住「去星完全没生效」这类硬失败。
        # 误伤判据不存在 ⇒ 只用质量分，误伤项记为不可测。
        if not measurable:
            accepted = score >= min_quality_score
            return accepted, 'advisory' if accepted else 'hard_failed'
        accepted = score >= min_quality_score
        return accepted, 'uncalibrated' if accepted else 'hard_failed'
    if not measurable:
        # 误伤判据不存在 ⇒ 只用质量分，误伤项记为不可测。
        accepted = score >= min_quality_score
        return accepted, 'advisory' if accepted else 'hard_failed'
    if score < min_quality_score:
        return False, 'hard_failed'
    if damage <= prof.damage_limit:
        return True, 'ok'
    if damage <= prof.damage_limit * prof.advisory_multiplier:
        return True, 'advisory'
    return False, 'hard_failed'


def _record_gate_state(report, gate_state, min_quality_score=None,
                       profile=None):
    """把门禁状态写进报告（供验收与上游判定）。

    阈值一律取自 ``profile``。此前这里硬编码模块级常量 —— 分档上线后会
    打印**错误的**阈值数字，而验收者正是靠这些数字判断门禁口径是否正确。
    """
    prof = profile or _REMOVAL_DEFAULTS
    limit = prof.damage_limit
    report['gate_state'] = gate_state
    # 记录门禁**实际绑定的读数**。两个读数绝对刻度不同，验收者若不知道
    # 阈值比的是哪个，会把新读数拿去和旧阈值比 —— 那是无意义的比较。
    report['gate_metric'] = ('star_flux' if prof.star_flux_normalize
                             else 'legacy_damage')
    if limit is None:
        # 阈值未标定：**只报读数，不报阈值**。
        # 不能像原来那样写「接纳线 %.2f」—— 那个数字不存在，
        # 打印出来会被当成「阈值是 0」或「阈值很宽松」。
        if gate_state == 'uncalibrated':
            # 文案必须报**门禁实际读的那个数**，不是固定报旧读数。
            # 否则开启归一化后，验收者会看到「误伤 0.3529 未参与判定」
            # 而门禁其实比的是 0.0938 —— 数字与结论对不上。
            _metric_key = ('nebula_damage_ratio_star_flux'
                           if prof.star_flux_normalize
                           else 'nebula_damage_ratio')
            report['gate_note'] = (
                '误伤 %.4f 已记录但**不参与判定**：该目标类型的 damage '
                '阈值尚未标定（跨样本口径跨度达 10 倍，夹逼无解）—— 见'
                'references/star_damage_gate_calibration.md §3.12'
                % report.get(_metric_key, 0.0))
        elif gate_state == 'hard_failed':
            # 阈值缺失**不能吞掉失败原因**：质量分判定仍然生效，
            # 验收者必须看到「为什么被拒」，否则会以为「未标定 = 放行」。
            report['gate_note'] = (
                '未通过：误伤 %.4f（不参与判定，阈值未标定）；'
                '质量分 %.3f 低于最低 %.2f'
                % (report.get('nebula_damage_ratio', 0.0),
                   report.get('repair_quality_score', 0.0),
                   min_quality_score)
                if min_quality_score is not None else
                '未通过：误伤 %.4f（不参与判定，阈值未标定）'
                % report.get('nebula_damage_ratio', 0.0))
        return report
    hard = limit * prof.advisory_multiplier
    if gate_state == 'advisory':
        report['gate_note'] = (
            '误伤 %.4f 落在接纳线 %.2f 与硬线 %.2f 之间，已带警告接受'
            % (report.get('nebula_damage_ratio', 0.0), limit, hard))
    elif gate_state == 'hard_failed':
        # 分数与误伤是**不同量纲**，不可互相代入 —— 文案里必须分别给出各自的
        # 判据，否则验收者会读到自相矛盾的数字。
        report['gate_note'] = (
            '未通过：误伤 %.4f（接纳线 %.2f / 硬线 %.2f）'
            % (report.get('nebula_damage_ratio', 0.0), limit, hard)
            + ('；质量分 %.3f 低于最低 %.2f'
               % (report.get('repair_quality_score', 0.0),
                  min_quality_score)
               if min_quality_score is not None else ''))
    return report


def separate_stars(image, method='inpaint', star_threshold=0.85, inpaint_radius=5,
                   external_starless=None, fwhm=None, use_multiscale=True,
                   min_confidence=0.3, min_quality_score=0.45,
                   auto_fallback=True, return_report=False,
                   starnet_path=None, starnet_stride=128,
                   star_flux_baseline_reference=None, profile=None,
                   starnet_timeout=900, exclude_mask=None,
                   galaxy_center=None, starnet_domain='mtf', target_type=None,
                   galaxy_core_radius=None):
    """
    分离星点和星云（v2 增强版）。

    starnet_domain: 送进 StarNet2 的载荷域，'mtf'（默认，先 MTF 拉伸再量化，
        读回后逆变换回线性）或 'linear'（旧行为）。极暗线性数据用 'linear' 会
        让弱色通道被量化压成 0，StarNet2 输出丢掉 OIII。

    新增参数:
        exclude_mask: 排除蒙版（布尔或浮点）。蒙版内非零区域禁止标记为星点并免于 Inpaint，用于星系核心等致密高光天体保护。
        fwhm: FWHM 估计值。None 时自动估计。
        use_multiscale: 是否使用多尺度检测引擎（True=新引擎，False=旧引擎）
        min_confidence: 检测置信度阈值，低于此值时安全回退
        min_quality_score: 去星质量最低可接受分数
        auto_fallback: 是否在低置信度或低质量时回退原图
        return_report: 是否额外返回检测、重试和回退报告
        starnet_path: StarNet2 可执行文件的绝对路径
        starnet_stride: StarNet2 步长
        starnet_timeout: StarNet2 最长执行秒数
        target_type: 目标类型，仅用于**选门禁阈值档**（见
            :mod:`star_removal_profiles`）。None 时用默认档，行为与分档前一致。

    返回: (星云图像, 星点图像, 星点掩膜[, 报告])
    """
    image = np.asarray(image, dtype=np.float32)
    # 一次查表、全链复用：4 处 estimate_star_removal_quality 与 3 处
    # _star_removal_gate 共用同一个档位对象，避免两处口径漂移。
    # 显式传入的 profile 优先（v0.1.44：让 pipeline 能用 cfg 覆盖门禁档，
    # 逐字段走 apply_profile 因而仍受单向棘轮约束）。
    prof = profile if profile is not None else _removal_profile(target_type)
    if not prof.reachable:
        print(f"  [INFO] 目标类型 {prof.name} 不可到达去星门禁（星点即主体），使用默认档阈值")
    if external_starless is not None:
        starless = np.asarray(external_starless, dtype=np.float32)
        if starless.shape != image.shape:
            raise ValueError(
                f"external starless shape {starless.shape} != input shape {image.shape}"
            )
        stars = np.clip(image - starless, 0, 1)
        star_mask = _star_layer_mask(stars)
        report = estimate_star_removal_quality(
            image, starless, prof.name, prof, stars=stars)
        # 相对判据基线对照（v0.1.44）：在门禁读取之前写入，
        # 否则 _star_removal_gate 看不到它。
        if star_flux_baseline_reference is not None:
            report['star_flux_baseline_reference'] = float(
                star_flux_baseline_reference)
        report.update({
            'accepted': True,
            'fallback_applied': False,
            'source': 'external_starless',
            # 外部无星层不进本模块的质量门禁（用户自带产物，阈值口径
            # 属另一条链路）。不显式标注的话，「无 gate_state」会被读成
            # 「默认档通过」—— 那是两种完全不同的结论。
            'gate_evaluated': False,
            'gate_state': None,
            'star_removal_profile': _removal_profile_report(prof, target_type),
        })
        result = protect_subject_layers(image, np.clip(starless, 0, 1), stars, star_mask, exclude_mask)
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
                    domain=starnet_domain,
                )
                if success:
                    starless = starnet_starless
                    stars = np.clip(image - starless, 0, 1)
                    star_mask = _star_layer_mask(stars)

                    report = estimate_star_removal_quality(
                        image, starless, prof.name, prof, stars=stars)
                    # 相对判据基线对照必须在门禁**之前**写入。
                    if star_flux_baseline_reference is not None:
                        report['star_flux_baseline_reference'] = float(
                            star_flux_baseline_reference)
                    starnet_quality_ok, starnet_gate_state = _star_removal_gate(
                        report, min_quality_score, prof.name, prof)
                    _record_gate_state(report, starnet_gate_state,
                                       min_quality_score, prof)
                    report.update({
                        'accepted': starnet_quality_ok,
                        'fallback_applied': not starnet_quality_ok,
                        'source': 'starnet',
                        'gate_evaluated': True,
                        'star_removal_profile': _removal_profile_report(prof, target_type),
                        'starnet_execution': starnet_report,
                    })
                    if starnet_quality_ok:
                        stars = gaussian_filter(stars, sigma=0.8)
                        result = protect_subject_layers(image, starless, stars, star_mask, exclude_mask)
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
                gradient_aware=True, n_scales=4, return_details=True,
                galaxy_center=galaxy_center,
                galaxy_core_radius=galaxy_core_radius
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
                        galaxy_center=galaxy_center,
                        galaxy_core_radius=galaxy_core_radius,
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

        # 把检测阶段实测到的 FWHM 带进报告：调用方（pipeline）用它决定后续
        # 星点检测的尺度，丢失它会退回硬编码默认值并把噪声峰当成星点。
        detected_fwhm = details.get('fwhm') if use_multiscale else None

        if exclude_mask is not None:
            ex_arr = np.asarray(exclude_mask > 0, dtype=bool)
            if ex_arr.shape == star_mask_val.shape:
                excluded_pixels = int(np.sum((star_mask_val > 0) & ex_arr))
                if excluded_pixels > 0:
                    star_mask_val = star_mask_val * (~ex_arr).astype(np.float32)
                    print(f"  [核心保护] 排除天体核心防去星像素: {excluded_pixels:,}px")

        n_star_pixels = int(np.sum(star_mask_val > 0.5))
        print(f"[星点分离] 检测到星点像素: {n_star_pixels:,}"
              f"{f' (置信度={confidence:.2f})' if confidence is not None else ''}")

        starless_val, actual_method = _repair_star_mask(
            image, star_mask_val, method, inpaint_radius
        )
        # 形态学路径的星层由**检测掩膜**给出，而不是 orig - starless：
        # 后者含修复残差（=待测损伤），会让归一化分母自相消（见
        # estimate_star_removal_quality 的 docstring）。掩膜是去星前的
        # 独立决策，不含损伤。
        morph_stars = (np.asarray(star_mask_val, dtype=np.float32)[..., None]
                       * np.asarray(image, dtype=np.float32))
        quality = estimate_star_removal_quality(
            image, starless_val, prof.name, prof, stars=morph_stars)
        quality.update({
            'detection_confidence': confidence,
            'repair_method': actual_method,
            'repair_radius': inpaint_radius,
            'retry_attempted': False,
            'estimated_fwhm': detected_fwhm,
        })
        # 主判与 StarNet 走同一个门禁函数，避免两条路径口径不一致
        # （此前形态学用 `not needs_starnet_plus`（或），StarNet 用 `damage<=0.20`
        #（与），同模块两处对damage 的容忍度矛盾）。
        quality_ok, morph_gate_state = _star_removal_gate(
            quality, min_quality_score, prof.name, prof)
        # 首次质量分的独立快照：重试的非回归检查要比「首次 vs 重试」，
        # 而 quality 变量在重试路径上会被 retry_quality 覆盖。
        first_attempt_score = float(quality['repair_quality_score'])
        if not quality['needs_starnet_plus'] and quality_ok:
            # needs_starnet_plus 命中但门禁放行（advisory）：质量分够，
            # 残留或误伤略高 —— 接受并记录，不做无谓回退。
            morph_gate_state = 'advisory'
        _record_gate_state(quality, morph_gate_state, min_quality_score, prof)

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
            retry_quality = estimate_star_removal_quality(
                image, retry_starless, prof.name, prof, stars=morph_stars)
            retry_quality.update({
                'detection_confidence': confidence,
                'repair_method': retry_actual_method,
                'repair_radius': retry_radius,
                'retry_attempted': True,
                'initial_quality': quality,
                'estimated_fwhm': detected_fwhm,
            })
            retry_ok, retry_gate_state = _star_removal_gate(
                retry_quality, min_quality_score, prof.name, prof)
            #非回归：重试不得让质量分变差（借鉴 aiseestart 的
            # stage7_starless_repair_max_score_growth = 0.00零容忍）。
            # 此前只查「达标没达标」，一个更差的结果也能被接受 ——
            # NGC6888 实测重试质量分与首次完全相同（telea 半径 3 对强发射
            # 星云无效），即重试白跑一遍全分辨率检测 + 修复。
            score_before = first_attempt_score
            score_after = float(retry_quality['repair_quality_score'])
            # 质量分**越大越好**，所以「变差」= 分数下降。
            # _STAR_REMOVAL_MAX_SCORE_GROWTH = 0.00 零容忍：分数可以不变
            # 或变好，但绝不允许采纳一个更差的重试结果。
            score_growth = score_after - score_before
            no_regression = (
                score_growth >= -prof.max_score_growth - 1e-9)
            if retry_ok and no_regression:
                _record_gate_state(retry_quality, retry_gate_state,
                                   min_quality_score, prof)
                print(
                    f"  [质量闭环] 重试通过，质量={score_after:.3f}"
                    f"（{retry_gate_state}，Δ={score_growth:+.3f}）"
                )
                starless_val = retry_starless
                quality = retry_quality
                quality_ok = True
            else:
                if retry_ok and not no_regression:
                    # 达标但变差 ⇒ 拒绝采用，保留首次结果
                    print(
                        f"  [质量闭环] 重试虽达标但质量变差"
                        f"（{score_before:.3f} → {score_after:.3f}），"
                        "按非回归零容忍丢弃重试结果"
                    )
                else:
                    print(
                        f"  [质量闭环] 重试仍不达标，质量={score_after:.3f}"
                        f"（{retry_gate_state}），安全回退原图"
                    )
                _record_gate_state(retry_quality,
                                   'hard_failed' if not retry_ok
                                   else 'regressed',
                                   min_quality_score, prof)
                fallback_res = _safe_star_removal_fallback(
                    image,
                    reason=('star_removal_retry_regressed' if retry_ok
                            else 'star_removal_quality_below_threshold'),
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
            'gate_evaluated': True,
            'star_removal_profile': _removal_profile_report(prof, target_type),
        })
        return (starless_val, stars_val, star_mask_val, quality)

    morph_res = _run_morphology_pipeline()
    starless, stars, star_mask, report = morph_res

    # 回退路径（低检测置信度 / 重试仍不达标）返回的 report 不带档位信息 ——
    # 那些分支在 _run_morphology_pipeline 内部提前 return。统一在这里补齐，
    # 保证**任何**返回路径的 report 都有 star_removal_profile。
    if 'star_removal_profile' not in report:
        report['star_removal_profile'] = _removal_profile_report(prof, target_type)
    report.setdefault('gate_evaluated', True)

    if starnet_fallback_reason is not None:
        report = dict(report)
        report.update({
            'fallback_applied': True,
            'fallback_reason': starnet_fallback_reason,
            'starnet_failure': starnet_failure_report,
        })
        # 验收可见性：回退后顶层字段已被形态学结果覆盖，只看顶层会误以为
        # 形态学是唯一候选（NGC6888 实测顶层 residual 0.0057，而 StarNet2
        # 是 0.0003 —— 差 19 倍）。把被拒候选的**关键指标**与拒绝原因
        # 提升到顶层命名空间，验收者不必深挖 starnet_failure。
        failure = starnet_failure_report
        if isinstance(failure, dict):
            for key in ('repair_quality_score', 'residual_star_fraction',
                         'nebula_damage_ratio', 'nebula_damage_measurable',
                         'gate_state'):
                if key in failure:
                    report['starnet_candidate_' + key] = failure[key]
        if starnet_fallback_reason == 'starnet_quality_below_threshold':
            failed = (starnet_failure_report or {})
            # 阈值未标定时**不报数字** ——「接纳线 None」会被读成
            # 「阈值是 0」或「阈值不存在」，两者都会误导验收。
            limit_text = ('接纳线未标定' if prof.damage_limit is None
                          else '接纳线 %.2f' % prof.damage_limit)
            report['starnet_reject_note'] = (
                'StarNet2 推理成功但被质量门拒：质量分 %(q).3f、残留星 %(r).4f、'
                '误伤 %(d).4f%(m)s；门禁状态 %(g)s（档位 %(p)s，%(l)s）。'
                '回退形态学后残留星劣化至 %(rr).4f。'
                % {'q': failed.get('repair_quality_score', float('nan')),
                   'r': failed.get('residual_star_fraction', float('nan')),
                   'd': failed.get('nebula_damage_ratio', float('nan')),
                   'm': '（不可测）' if not failed.get(
                       'nebula_damage_measurable', True) else '',
                   'g': failed.get('gate_state', '未记录'),
                   'p': prof.name,
                   'l': limit_text,
                   'rr': report.get('residual_star_fraction', float('nan'))})

    result = protect_subject_layers(image, starless, stars, star_mask, exclude_mask)
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


def saturate_stars(image, saturation=1.0):
    """星点层**保亮度**饱和补偿。

    为什么需要：星点链路是 拉伸(arcsinh) → 去绿(SCNR) → 曲线 → 缩星，其中
    拉伸压缩色度、SCNR 又把 Lab a 推向 0，**净效果是去饱和**。外部权威经验
    （线性 Seti 星点法）明确要求「饱和必须在拉伸之后、且要足够激进」，因为
    拉伸本身会显著去饱和。

    为什么不用 `color_tools.enhance_saturation`：它带 V 分位背景保护，
    而星点层绝大部分是纯黑，V 的分位区间退化为 p_low≈p_high≈0，保护逻辑失效。
    （该函数本身已改为保比例公式、不再压死弱通道，但背景保护在这个场景仍不适用。）

    本实现与 `stellar_recompose.process_stars_layer` 的星点饱和公式同源：
    亮度严格不变，只缩放相对亮度的色度差。

    注意：色度放大后若某通道被推到 [0,1] 之外，末尾的 clip 会改变该像素亮度
    （只影响近零/近饱和通道的极饱和星核）。这是该公式的固有行为，与
    `stellar_recompose` 一致。

    saturation == 1.0 时返回原数组（恒等，逐位不变）。
    """
    src = np.asarray(image, dtype=np.float32)
    if src.ndim < 3 or src.shape[2] < 3 or float(saturation) == 1.0:
        return src
    gray = (
        0.2126 * src[..., 0]
        + 0.7152 * src[..., 1]
        + 0.0722 * src[..., 2]
    )[..., None]
    out = np.clip(gray + (src[..., :3] - gray) * float(saturation), 0.0, 1.0)
    if src.shape[2] > 3:
        out = np.dstack([out, src[..., 3:]])
    return out.astype(np.float32)


def combine_starless_stars(
    starless,
    stars,
    star_strength=1.0,
    star_softness=1.0,
    blend_mode="screen",
):
    """
    重新合成星云和星点图像。

    参数:
      starless: 无星底图，范围 [0, 1]
      stars: 星点图层，范围 [0, 1]
      star_strength: 星点强度增益因子
      star_softness: 星点高斯羽化软度
      blend_mode: 混合模式:
        - "screen": 天体物理标准屏幕混合 1 - (1 - starless) * (1 - stars * strength)，
                    平滑过渡且保护高光动态范围，避免星晕被星云底色简单加法染色泛黄
        - "add": 传统线性相加 np.clip(starless + star_strength * stars, 0, 1)
    """
    starless_f = np.clip(np.asarray(starless, dtype=np.float32), 0.0, 1.0)
    stars_f = np.clip(np.asarray(stars, dtype=np.float32), 0.0, 1.0)

    if star_softness != 1.0:
        stars_f = gaussian_filter(stars_f, sigma=star_softness)

    scaled_stars = np.clip(stars_f * float(star_strength), 0.0, 1.0)

    if blend_mode == "screen":
        result = 1.0 - (1.0 - starless_f) * (1.0 - scaled_stars)
    elif blend_mode == "add":
        result = starless_f + scaled_stars
    else:
        raise ValueError(f"不支持的合成混合模式: {blend_mode}，支持: 'screen', 'add'")

    return np.clip(result, 0.0, 1.0).astype(np.float32)


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
    p_sep.add_argument('--starnet-stride', type=int, default=128,
                       help='StarNet2 推理步长：偶数、2~512。512 最快、2 最准但'
                            '极慢（8MP 全幅 stride=2 需 207 万推理块，实测 2400s 未跑完）；'
                            '默认 128 与 pipeline.py --starnet-stride 一致')

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
    p_com.add_argument('--blend-mode', choices=['screen', 'add'], default='screen', help='混合模式 (默认: screen)')

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
            starnet_stride=getattr(args, 'starnet_stride', 128)
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
            star_softness=args.softness, blend_mode=args.blend_mode
        )
        imsave(args.output, img_as_ubyte(result))
        print(f"[合成] 输出: {args.output}")


if __name__ == '__main__':
    main()
