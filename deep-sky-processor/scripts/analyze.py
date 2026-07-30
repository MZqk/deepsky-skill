#!/usr/bin/env python3
"""
Deep-Sky Image Diagnostic Analyzer (深空图像诊断分析器)

为 AI 驱动的自适应处理提供图像质量评估和特征分析。
输出结构化 JSON 诊断报告，AI 可据此做出智能化处理决策。

分析维度:
  1. 亮度与动态范围 — 决定拉伸策略
  2. 噪声水平 — 决定降噪强度
  3. 背景梯度 — 决定 DBE 方法和阶数
  4. 色彩平衡 — 决定颜色校准策略
  5. 星场密度 — 决定星点分离参数
  6. 锐度评估 — 决定锐化强度
  7. 天体特征 — 辅助目标识别和处理策略

用法:
  python analyze.py <input_image>                    # 输出 JSON 到 stdout
  python analyze.py <input_image> --format readable  # 人类可读格式
  python analyze.py <input_image> --output report.json
"""

import argparse
import json
import sys
import numpy as np
from scipy.ndimage import gaussian_filter, median_filter, sobel, uniform_filter
from scipy.signal import convolve2d
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fits_io import read_image, build_physical_priors
from recognize import (analyze_starfield as recognize_starfield,
                       classify_scene, color_features, normalize_image)


def analyze_image(filepath):
    """完整图像诊断分析，返回结构化诊断报告。"""
    img, meta = read_image(filepath)

    # 处理 RGBA
    has_alpha = img.ndim == 3 and img.shape[2] == 4
    if has_alpha:
        img = img[:, :, :3]

    # 转为灰度
    if img.ndim == 3:
        gray = 0.299 * img[:, :, 0] + 0.587 * img[:, :, 1] + 0.114 * img[:, :, 2]
    else:
        gray = img.copy()

    target_hint = _infer_target_type(img)
    color_report = _analyze_color(img) if img.ndim == 3 else None
    if color_report:
        _interpret_color_signal(color_report, target_hint)

    report = {
        'file': filepath,
        'format': meta.get('format', 'unknown'),
        'is_linear': meta.get('is_linear', False),
        'shape': list(img.shape),
        'has_alpha': has_alpha,
        'channels': _detect_channels(filepath, img, meta),
        'capture_metadata': meta.get('capture_metadata', {}),
        'physical_priors': build_physical_priors(
            meta.get('capture_metadata', {})
        ),

        # 1. 亮度与动态范围
        'brightness': _analyze_brightness(gray, img),

        # 2. 噪声评估
        'noise': _analyze_noise(gray, img),

        # 3. 背景梯度
        'gradient': _analyze_gradient(gray, img),

        # 4. 色彩平衡 (仅 RGB)
        'color': color_report,

        # 5. 星场密度
        'starfield': _analyze_starfield(gray),

        # 6. 锐度
        'sharpness': _analyze_sharpness(gray),

        # 本地 CV 初步目标类型，仅作为 Phase B 的策略提示。
        'target_type_hint': target_hint,

        # 7. 综合推荐
        'recommendations': {},
    }

    # 生成综合推荐
    report['recommendations'] = _generate_recommendations(report)

    return report


def _interpret_color_signal(color_report, target_hint):
    """Distinguish expected emission color from a correctable background cast."""
    target_type = target_hint.get('target_type')
    ratios = color_report.get('color_cast_ratios', {})
    red_dominant = ratios.get('r', 1.0) > max(
        ratios.get('g', 1.0),
        ratios.get('b', 1.0),
    ) * 1.25
    if target_type == 'emission_nebula' and red_dominant:
        color_report['signal_interpretation'] = 'expected_emission_dominance'
        color_report['color_health_effective'] = 'emission_dominant'
        color_report['recommended_mode'] = 'emission'
    else:
        color_report['signal_interpretation'] = 'background_cast'
        color_report['color_health_effective'] = color_report['color_health']
        color_report['recommended_mode'] = 'standard'


def _detect_channels(filepath, img, meta):
    """Detect channel count and likely semantic channel type."""
    count = 1 if img.ndim == 2 else int(img.shape[2])
    header = meta.get('header')
    filter_name = str(header.get('FILTER', '') if header is not None else '').strip()
    stem = Path(filepath).stem.lower()
    token = filter_name.lower() or stem
    if count == 4:
        channel_type = 'rgba'
    elif count == 3:
        channel_type = 'rgb'
    elif any(name in token for name in ('sii', 's2', 'sulfur')):
        channel_type = 'sii'
    elif any(name in token for name in ('ha', 'halpha', 'h-alpha')):
        channel_type = 'ha'
    elif any(name in token for name in ('oiii', 'o3', 'oxygen')):
        channel_type = 'oiii'
    elif filter_name.upper() in ('L', 'LUM', 'LUMINANCE') or 'luminance' in token:
        channel_type = 'luminance'
    elif filter_name.upper() in ('R', 'G', 'B'):
        channel_type = filter_name.lower()
    else:
        channel_type = 'mono' if count == 1 else f'{count}_channel'
    return {
        'channel_count': count,
        'channel_type': channel_type,
        'filter': filter_name or None,
    }


def _infer_target_type(img):
    """Return a low-cost local-CV scene hint for Phase A."""
    try:
        rgb, gray, _shape, _has_alpha = normalize_image(img)
        starfield = recognize_starfield(gray)
        colors = color_features(rgb, gray)
        scene = classify_scene(rgb, gray, starfield, colors)
        return {
            'target_type': scene['target_type'],
            'confidence': scene['confidence'],
            'backend': 'local_cv',
            'provisional': True,
        }
    except Exception as exc:
        return {
            'target_type': 'unknown_deep_sky',
            'confidence': 0.0,
            'backend': 'local_cv',
            'provisional': True,
            'warning': str(exc),
        }


def _analyze_brightness(gray, img):
    """亮度统计与动态范围分析。"""
    percentiles = [0.1, 1, 5, 10, 25, 50, 75, 90, 95, 99, 99.9]
    pvals = {f'p{p}': float(np.percentile(gray, p)) for p in percentiles}

    dark_thresholds = [0.01, 0.02, 0.05, 0.10]
    dark_ratios = {}
    for t in dark_thresholds:
        dark_ratios[f'below_{int(t*100)}pct'] = float(np.mean(gray < t))

    nonzero_mask = gray > 0
    nonzero_frac = float(np.mean(nonzero_mask))

    # 动态范围
    data_range = pvals['p99'] - pvals['p1']
    if data_range > 0 and pvals['p50'] > 0:
        dynamic_range_ratio = data_range / pvals['p50']
    else:
        dynamic_range_ratio = 0

    # 暗度分级：联合中位数、P99 与有效像素比例，避免少量亮星或
    # 大面积零值让单一统计量误判。
    median_val = pvals['p50']
    p99_val = pvals['p99']
    has_signal = nonzero_frac >= 0.05
    if median_val < 0.001 and p99_val < 0.02 and has_signal:
        darkness_level = 'extreme_dark'
        brightness_class = 'very_dark'
    elif median_val < 0.01 and p99_val < 0.08 and has_signal:
        darkness_level = 'very_dark'
        brightness_class = 'very_dark'
    elif median_val < 0.05:
        darkness_level = 'dark'
        brightness_class = 'dark'
    elif median_val < 0.15:
        darkness_level = 'moderate'
        brightness_class = 'moderate'
    else:
        darkness_level = 'bright'
        brightness_class = 'bright'

    return {
        'mean': float(np.mean(gray)),
        'median': float(np.median(gray)),
        'std': float(np.std(gray)),
        'min': float(gray.min()),
        'max': float(gray.max()),
        'percentiles': pvals,
        'dark_pixel_ratios': dark_ratios,
        'nonzero_fraction': nonzero_frac,
        'dynamic_range_ratio': round(dynamic_range_ratio, 2),
        'darkness_level': darkness_level,
        'brightness_class': brightness_class,
        'is_practically_black': bool(
            median_val < 0.001 and p99_val < 0.02
        ),
        'very_dark_eligible': bool(brightness_class == 'very_dark'),
    }


def _analyze_noise(gray, img):
    """噪声水平评估（暗部局部方差）。"""
    # 选取暗部区域（< median）做噪声分析
    median_val = np.median(gray)
    dark_mask = gray < median_val
    if np.sum(dark_mask) < 100:
        # 暗部不足，使用底部 50%
        dark_thresh = np.percentile(gray, 50)
        dark_mask = gray < dark_thresh

    # 局部标准差作为噪声估计
    h, w = gray.shape
    block_size = min(32, h // 4, w // 4)
    if block_size < 4:
        block_size = 4

    # 将暗部区域分成块，计算局部标准差
    local_std_estimates = []
    for y in range(0, h - block_size, block_size):
        for x in range(0, w - block_size, block_size):
            block = gray[y:y+block_size, x:x+block_size]
            if np.mean(block < median_val) > 0.5:
                local_std_estimates.append(np.std(block))

    if local_std_estimates:
        background_noise = float(np.median(local_std_estimates))
    else:
        background_noise = 0

    # 噪声分级
    if background_noise < 0.005:
        noise_level = 'very_low'
    elif background_noise < 0.015:
        noise_level = 'low'
    elif background_noise < 0.04:
        noise_level = 'moderate'
    elif background_noise < 0.08:
        noise_level = 'high'
    else:
        noise_level = 'very_high'

    # 色彩噪声 (RGB)
    chroma_noise = 0
    if img.ndim == 3 and img.shape[2] >= 3:
        from color_conv import safe_rgb2lab as rgb2lab
        try:
            lab = rgb2lab(np.clip(img, 0, 1))
            a_std = float(np.std(lab[..., 1][dark_mask])) if np.sum(dark_mask) > 0 else 0
            b_std = float(np.std(lab[..., 2][dark_mask])) if np.sum(dark_mask) > 0 else 0
            chroma_noise = round((a_std + b_std) / 2, 5)
        except Exception:
            chroma_noise = 0

    return {
        'background_noise_std': round(background_noise, 5),
        'noise_level': noise_level,
        'chroma_noise_estimate': round(chroma_noise, 5),
        'sample_blocks': len(local_std_estimates),
    }


def _estimate_channel_gradient(channel):
    """Estimate a robust low-frequency plane for one channel."""
    h, w = channel.shape
    ds_factor = max(1, min(h, w) // 40)
    small = channel[::ds_factor, ::ds_factor]
    sh, sw = small.shape
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    values = small.ravel()
    median = float(np.median(values))
    fit_mask = values < median * 1.5
    if np.sum(fit_mask) < 20:
        fit_mask = np.ones_like(values, dtype=bool)
    design = np.column_stack([
        np.ones(int(np.sum(fit_mask)), dtype=np.float32),
        xx.ravel()[fit_mask] / max(sw, 1),
        yy.ravel()[fit_mask] / max(sh, 1),
    ])
    coeffs, _, _, _ = np.linalg.lstsq(design, values[fit_mask], rcond=None)
    gx = float(coeffs[1])
    gy = float(coeffs[2])
    magnitude = float(np.hypot(gx, gy))
    normalized = magnitude / max(abs(median), 1e-8)
    return {
        'gradient_x': round(gx, 6),
        'gradient_y': round(gy, 6),
        'gradient_magnitude': round(magnitude, 6),
        'gradient_normalized': round(normalized, 4),
        'gradient_x_normalized': round(gx / max(abs(median), 1e-8), 4),
        'gradient_y_normalized': round(gy / max(abs(median), 1e-8), 4),
    }


def _analyze_gradient(gray, rgb=None):
    """背景梯度分析与渐晕检测。"""
    h, w = gray.shape

    # 大幅降采样后检测梯度
    ds_factor = max(1, min(h, w) // 40)
    small = gray[::ds_factor, ::ds_factor]
    sh, sw = small.shape

    # 线性回归拟合梯度
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    yy_flat = yy.ravel()
    xx_flat = xx.ravel()
    zz_flat = small.ravel()

    # 排除亮区（天体）做拟合
    z_median = np.median(zz_flat)
    fit_mask = zz_flat < z_median * 1.5
    if np.sum(fit_mask) < 20:
        fit_mask = np.ones_like(zz_flat, dtype=bool)

    A = np.column_stack([
        np.ones_like(yy_flat[fit_mask]),
        xx_flat[fit_mask] / sw,
        yy_flat[fit_mask] / sh,
    ])
    b = zz_flat[fit_mask]
    try:
        coeffs, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        gradient_x = float(coeffs[1])  # 水平方向梯度强度
        gradient_y = float(coeffs[2])  # 垂直方向梯度强度
        gradient_magnitude = float(np.sqrt(gradient_x**2 + gradient_y**2))
        gradient_angle = float(np.arctan2(gradient_y, gradient_x))
    except np.linalg.LinAlgError:
        gradient_x, gradient_y, gradient_magnitude, gradient_angle = 0, 0, 0, 0

    # 渐晕检测（角落 vs 中心亮度）
    corner_size = max(3, min(h, w) // 8)
    corners = [
        gray[:corner_size, :corner_size],           # 左上
        gray[:corner_size, -corner_size:],          # 右上
        gray[-corner_size:, :corner_size],          # 左下
        gray[-corner_size:, -corner_size:],         # 右下
    ]
    center = gray[h//2-corner_size//2:h//2+corner_size//2,
                  w//2-corner_size//2:w//2+corner_size//2]

    corner_means = [float(np.mean(c)) for c in corners]
    center_mean = float(np.mean(center))
    avg_corner_mean = float(np.mean(corner_means))

    if center_mean > 0.001:
        vignetting_ratio = avg_corner_mean / center_mean
    else:
        vignetting_ratio = 1.0

    # 梯度严重度分级
    grad_norm = gradient_magnitude / max(np.median(gray), 1e-8)
    if grad_norm < 0.05:
        gradient_severity = 'none'
    elif grad_norm < 0.2:
        gradient_severity = 'mild'
    elif grad_norm < 0.5:
        gradient_severity = 'moderate'
    else:
        gradient_severity = 'severe'

    # 梯度模式分析 — 决定 DBE 方法选择
    # 计算线性模型的残差：实际值 vs 线性预测
    try:
        y_pred = A @ coeffs
        residuals = b - y_pred
        residual_rms = float(np.sqrt(np.mean(residuals**2)))
        # 线性模型的 R²：模型解释了多大比例的方差
        ss_res = float(np.sum(residuals**2))
        ss_tot = float(np.sum((b - np.mean(b))**2))
        r_squared = 1 - ss_res / max(ss_tot, 1e-12)
    except Exception:
        residual_rms = 0
        r_squared = 0

    # 梯度模式分类
    rel_residual = residual_rms / max(np.median(gray), 1e-8)

    # 渐晕优先检测 — 对称渐变在线性回归中梯度接近零，会误判为 'none'
    if vignetting_ratio < 0.6:
        gradient_pattern = 'vignetting_dominant'
    elif gradient_severity == 'none':
        gradient_pattern = 'none'
    elif r_squared > 0.85 and rel_residual < 0.3:
        gradient_pattern = 'linear'
    elif r_squared < 0.5:
        gradient_pattern = 'complex'
    else:
        gradient_pattern = 'irregular'

    # DBE 方法推荐
    if gradient_pattern == 'none':
        dbe_method = 'skip'
        dbe_method_reason = '无显著梯度，可跳过 DBE'
    elif gradient_pattern == 'linear':
        dbe_method = 'polynomial'
        dbe_method_reason = '线性梯度 — polynomial 模型最有效，计算速度快'
    elif gradient_pattern == 'vignetting_dominant':
        dbe_method = 'polynomial'
        dbe_method_reason = '渐晕主导 — polynomial degree=3 可同时建模径向衰减和线性梯度'
    elif gradient_pattern == 'complex':
        dbe_method = 'rbf'
        dbe_method_reason = '复杂非线性梯度 — RBF thin-plate spline 适合不规则光害模式'
    else:  # irregular
        # 不规则梯度：优先 RBF，但如果噪声高则用 median
        if gradient_severity == 'severe':
            dbe_method = 'rbf'
            dbe_method_reason = '严重不规则梯度 — RBF 拟合复杂空间模式'
        else:
            dbe_method = 'median'
            dbe_method_reason = '噪声主导的不规则背景 — median filter 无需模型假设'

    channel_gradients = None
    chromatic_gradient_spread = 0.0
    chromatic_gradient_review = False
    if rgb is not None and rgb.ndim == 3 and rgb.shape[2] >= 3:
        labels = ('r', 'g', 'b')
        channel_gradients = {
            label: _estimate_channel_gradient(rgb[..., index])
            for index, label in enumerate(labels)
        }
        vectors = np.asarray([
            [
                channel_gradients[label]['gradient_x_normalized'],
                channel_gradients[label]['gradient_y_normalized'],
            ]
            for label in labels
        ], dtype=np.float32)
        chromatic_gradient_spread = float(max(
            np.linalg.norm(vectors[i] - vectors[j])
            for i in range(len(labels))
            for j in range(i + 1, len(labels))
        ))
        max_channel_gradient = float(max(
            channel_gradients[label]['gradient_normalized']
            for label in labels
        ))
        # 灰度平均可能抵消单通道低频色偏。此时不应自动做 DBE，也不应
        # 把结果直接解释成真实 Hα；交给人工/试运行差分审查。
        chromatic_gradient_review = (
            gradient_severity == 'none'
            and max_channel_gradient >= 0.12
            and chromatic_gradient_spread >= 0.08
        )

    if gradient_pattern == 'none' and chromatic_gradient_review:
        dbe_decision = 'review_chromatic'
        dbe_method_reason = (
            '灰度梯度不显著，但通道间低频梯度差异较大；'
            '需区分真实发射信号与色彩梯度，禁止自动强制 DBE'
        )
    elif gradient_pattern == 'none':
        dbe_decision = 'skip'
    else:
        dbe_decision = 'apply'

    return {
        'gradient_x': round(gradient_x, 4),
        'gradient_y': round(gradient_y, 4),
        'gradient_magnitude': round(gradient_magnitude, 4),
        'gradient_angle_rad': round(gradient_angle, 4),
        'gradient_severity': gradient_severity,
        'gradient_pattern': gradient_pattern,
        'r_squared': round(r_squared, 4),
        'residual_rms': round(residual_rms, 6),
        'dbe_method_recommendation': dbe_method,
        'dbe_method_reason': dbe_method_reason,
        'dbe_decision': dbe_decision,
        'channel_gradients': channel_gradients,
        'chromatic_gradient_spread': round(chromatic_gradient_spread, 4),
        'chromatic_gradient_review': chromatic_gradient_review,
        'vignetting_ratio': round(vignetting_ratio, 3),
        'has_vignetting': bool(vignetting_ratio < 0.7),
        'corner_means': [round(m, 4) for m in corner_means],
        'center_mean': round(center_mean, 4),
    }


def _analyze_color(img):
    """色彩平衡与颜色特性分析。"""
    if img.ndim != 3 or img.shape[2] < 3:
        return None

    r, g, b = img[..., 0], img[..., 1], img[..., 2]

    # 背景区域颜色
    gray = 0.299 * r + 0.587 * g + 0.114 * b
    bg_thresh = np.percentile(gray, 30)
    bg_mask = gray <= bg_thresh
    if np.sum(bg_mask) < 100:
        bg_mask = gray <= np.percentile(gray, 50)

    bg_r = float(np.mean(r[bg_mask]))
    bg_g = float(np.mean(g[bg_mask]))
    bg_b = float(np.mean(b[bg_mask]))
    bg_mean = (bg_r + bg_g + bg_b) / 3

    if bg_mean > 0.0001:
        color_cast_r = round(bg_r / bg_mean, 3)
        color_cast_g = round(bg_g / bg_mean, 3)
        color_cast_b = round(bg_b / bg_mean, 3)
    else:
        color_cast_r = color_cast_g = color_cast_b = 1.0

    # 绿色偏置检测 (SCNR 需求)
    green_bias = max(0, color_cast_g - max(color_cast_r, color_cast_b, 1.0))

    black_points = np.percentile(
        img[..., :3].reshape(-1, 3),
        1.0,
        axis=0,
    )
    signal = np.clip(img[..., :3] - black_points, 0, None)
    channel_p99 = np.percentile(signal.reshape(-1, 3), 99.0, axis=0)

    # 色彩饱和度
    from color_conv import safe_rgb2hsv as rgb2hsv
    try:
        hsv = rgb2hsv(np.clip(img, 0, 1))
        saturation_mean = float(np.mean(hsv[..., 1]))
    except Exception:
        saturation_mean = 0

    # 颜色问题诊断
    max_cast = max(color_cast_r, color_cast_g, color_cast_b)
    min_cast = min(color_cast_r, color_cast_g, color_cast_b)
    color_balance_deviation = max_cast - min_cast

    if color_balance_deviation < 0.05:
        color_health = 'good'
    elif color_balance_deviation < 0.15:
        color_health = 'mild_cast'
    elif color_balance_deviation < 0.3:
        color_health = 'moderate_cast'
    else:
        color_health = 'severe_cast'

    return {
        'background_rgb': [round(bg_r, 5), round(bg_g, 5), round(bg_b, 5)],
        'color_cast_ratios': {
            'r': color_cast_r, 'g': color_cast_g, 'b': color_cast_b,
        },
        'green_bias': round(green_bias, 3),
        'needs_scnr': bool(green_bias > 0.05),
        'color_balance_deviation': round(color_balance_deviation, 3),
        'color_health': color_health,
        'saturation_mean': round(saturation_mean, 4),
        'channel_signal_p99': {
            'r': round(float(channel_p99[0]), 6),
            'g': round(float(channel_p99[1]), 6),
            'b': round(float(channel_p99[2]), 6),
        },
        'channel_signal_ratios': {
            'r_over_g': round(
                float(channel_p99[0]) / max(float(channel_p99[1]), 1e-9),
                4,
            ),
            'r_over_b': round(
                float(channel_p99[0]) / max(float(channel_p99[2]), 1e-9),
                4,
            ),
        },
    }


def _analyze_starfield(gray):
    """星场密度与星点特性分析。"""
    from scipy.ndimage import binary_dilation
    from skimage.morphology import disk, white_tophat

    try:
        # Top-hat 星点检测
        selem = disk(3)
        tophat = white_tophat(gray, selem)
        positive = tophat[tophat > 0]

        if len(positive) < 10:
            return {
                'star_count_estimate': 0,
                'star_density': 'none',
                'mean_star_size_px': 0,
                'star_coverage_pct': 0,
            }

        threshold = max(np.percentile(positive, 80), 0.01)
        star_mask = tophat > threshold
        star_mask = binary_dilation(star_mask, structure=disk(1))

        # 连通分量计数
        from scipy.ndimage import label
        labeled, num_features = label(star_mask)
        star_count = num_features
        star_coverage = float(np.mean(star_mask)) * 100

        # 平均星点大小
        if num_features > 0:
            sizes = []
            for i in range(1, num_features + 1):
                sizes.append(np.sum(labeled == i))
            mean_star_size = float(np.median(sizes))
        else:
            mean_star_size = 0

        # 密度分级 (相对于图像面积)
        total_px = gray.shape[0] * gray.shape[1]
        density = star_count / total_px * 1e6  # 每百万像素星点数

        if density < 50:
            density_level = 'sparse'
        elif density < 200:
            density_level = 'moderate'
        elif density < 600:
            density_level = 'dense'
        else:
            density_level = 'very_dense'

        return {
            'star_count_estimate': int(star_count),
            'star_density': density_level,
            'density_per_mpix': round(density, 1),
            'mean_star_size_px': round(mean_star_size, 1),
            'star_coverage_pct': round(star_coverage, 2),
        }

    except Exception as e:
        return {
            'star_count_estimate': 0,
            'star_density': 'unknown',
            'error': str(e),
        }


def _analyze_sharpness(gray):
    """图像锐度/模糊程度评估。"""
    # 拉普拉斯方差 (衡量高频能量)
    try:
        laplacian = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
        lap_response = convolve2d(gray, laplacian, mode='valid')
        laplacian_var = float(np.var(lap_response))

        # 梯度幅值
        gx = sobel(gray, axis=0)
        gy = sobel(gray, axis=1)
        gradient_mag = np.sqrt(gx**2 + gy**2)
        gradient_mean = float(np.mean(gradient_mag))

    except Exception:
        laplacian_var = 0
        gradient_mean = 0

    # 锐度分级
    if laplacian_var < 5e-7:
        sharpness_level = 'very_blurry'
    elif laplacian_var < 2e-6:
        sharpness_level = 'blurry'
    elif laplacian_var < 8e-6:
        sharpness_level = 'moderate'
    elif laplacian_var < 3e-5:
        sharpness_level = 'sharp'
    else:
        sharpness_level = 'very_sharp'

    return {
        'laplacian_variance': round(laplacian_var, 10),
        'gradient_magnitude_mean': round(gradient_mean, 6),
        'sharpness_level': sharpness_level,
    }


def _generate_recommendations(r):
    """基于所有分析维度生成处理建议。"""
    b = r['brightness']
    n = r['noise']
    g = r['gradient']
    c = r['color'] or {}
    s = r['starfield']
    sh = r['sharpness']
    is_linear = r['is_linear']

    rec = {}

    # --- DBE 建议 (方法+阶数，基于梯度模式和严重度) ---
    pattern = g.get('gradient_pattern', g['gradient_severity'])
    severity = g['gradient_severity']
    method_rec = g.get('dbe_method_recommendation', 'polynomial')
    method_reason = g.get('dbe_method_reason', '')
    has_vig = g.get('has_vignetting', False)

    if pattern == 'none' or severity == 'none':
        rec['dbe'] = {
            'method': 'polynomial',
            'degree': 1,
            'reason': '无显著背景梯度，可跳过DBE或用1阶微调',
        }
    elif pattern == 'linear':
        rec['dbe'] = {
            'method': 'polynomial',
            'degree': 2,
            'reason': f'线性梯度 — polynomial 2阶 {("+ 渐晕检测" if has_vig else "")}',
        }
    elif pattern == 'vignetting_dominant':
        rec['dbe'] = {
            'method': 'polynomial',
            'degree': 3,
            'reason': '渐晕主导 — polynomial 3阶可建模径向衰减+线性梯度',
        }
    elif pattern == 'complex':
        rec['dbe'] = {
            'method': 'rbf',
            'degree': None,  # RBF 不使用 degree 参数
            'reason': f'复杂非线性梯度 — RBF thin-plate spline {method_reason}',
        }
    elif pattern == 'irregular':
        rec['dbe'] = {
            'method': method_rec,
            'degree': 2 if method_rec == 'polynomial' else None,
            'reason': method_reason,
        }
    else:
        rec['dbe'] = {
            'method': 'polynomial',
            'degree': 2,
            'reason': '未知梯度模式，默认 polynomial 2阶',
        }

    # --- 降噪建议 ---
    nl = n['noise_level']
    if nl == 'very_low':
        pre_denoise_lum = 0.005
        pre_denoise_chroma = 0.015
    elif nl == 'low':
        pre_denoise_lum = 0.012
        pre_denoise_chroma = 0.035
    elif nl == 'moderate':
        pre_denoise_lum = 0.025
        pre_denoise_chroma = 0.07
    elif nl == 'high':
        pre_denoise_lum = 0.04
        pre_denoise_chroma = 0.12
    else:
        pre_denoise_lum = 0.06
        pre_denoise_chroma = 0.18

    rec['pre_denoise'] = {
        'luminance_strength': pre_denoise_lum,
        'chroma_strength': pre_denoise_chroma,
        'reason': f'噪声水平: {nl}',
    }

    # 最终降噪 (比初步降噪减半)
    rec['final_denoise'] = {
        'luminance_strength': round(pre_denoise_lum * 0.5, 3),
        'chroma_strength': round(pre_denoise_chroma * 0.5, 3),
    }

    # --- 拉伸建议 ---
    darkness = b['darkness_level']
    if darkness == 'extreme_dark':
        stretch_factor = 120.0 if is_linear else 80.0
        stretch_method = 'very_dark'
        stretch_gamma = 0.42
        target_bg = 0.12
    elif darkness == 'very_dark':
        stretch_factor = 80.0 if is_linear else 50.0
        stretch_method = 'very_dark'
        stretch_gamma = 0.45
        target_bg = 0.10
    elif darkness == 'dark':
        stretch_factor = 45.0 if is_linear else 30.0
        stretch_method = 'luminance_arcsinh'
        stretch_gamma = 0.45
        target_bg = 0.08
    elif darkness == 'moderate':
        stretch_factor = 25.0
        stretch_method = 'luminance_arcsinh'
        stretch_gamma = 0.48
        target_bg = 0.07
    else:
        stretch_factor = 12.0
        stretch_method = 'luminance_arcsinh'
        stretch_gamma = 0.5
        target_bg = 0.06

    rec['stretch'] = {
        'method': stretch_method,
        'factor': stretch_factor,
        'gamma': stretch_gamma,
        'target_bg': target_bg,
        'reason': f'暗度: {darkness}, 线性数据: {is_linear}',
    }

    # --- 星点处理建议 ---
    density = s.get('star_density', 'moderate')
    if density == 'sparse':
        star_threshold = 0.90
        star_reduction = 0.15
    elif density == 'moderate':
        star_threshold = 0.85
        star_reduction = 0.3
    elif density == 'dense':
        star_threshold = 0.82
        star_reduction = 0.35
    else:
        star_threshold = 0.78
        star_reduction = 0.4

    rec['star_tools'] = {
        'detection_threshold': star_threshold,
        'reduction': star_reduction,
        'star_stretch_factor': stretch_factor * 0.25,
        'reason': f'星场密度: {density}',
    }

    # --- 增强建议 ---
    dr = b['dynamic_range_ratio']
    if dr > 10:
        hdr_strength = 0.6
    elif dr > 5:
        hdr_strength = 0.4
    elif dr > 2:
        hdr_strength = 0.25
    else:
        hdr_strength = 0.15

    rec['enhance'] = {
        'hdr_strength': round(hdr_strength, 2),
        'method': 'hdr_multiscale',
        'reason': f'动态范围比: {dr:.1f}',
    }

    # --- 锐化建议 ---
    sharpness_level = sh['sharpness_level']
    if sharpness_level == 'very_blurry':
        sharpen_amount = 2.5
    elif sharpness_level == 'blurry':
        sharpen_amount = 1.8
    elif sharpness_level == 'moderate':
        sharpen_amount = 1.2
    elif sharpness_level == 'sharp':
        sharpen_amount = 0.6
    else:
        sharpen_amount = 0.3

    rec['sharpen'] = {
        'amount': round(sharpen_amount, 1),
        'method': 'multiscale_sharpen',
        'reason': f'当前锐度: {sharpness_level}',
    }

    # --- 颜色建议 ---
    color_health = c.get('color_health_effective', c.get('color_health', 'good'))
    if color_health in ('good', 'emission_dominant'):
        saturation_factor = 1.2
    elif color_health == 'mild_cast':
        saturation_factor = 1.3
    elif color_health == 'moderate_cast':
        saturation_factor = 1.5
    else:
        saturation_factor = 1.4  # 严重偏色时饱和保守，先矫正

    rec['color'] = {
        'saturation_factor': saturation_factor,
        'needs_scnr': c.get('needs_scnr', False),
        'needs_background_neutralize': bool(
            color_health not in ('good', 'emission_dominant')
        ),
        'color_health': color_health,
        'mode': c.get('recommended_mode', 'standard'),
    }

    # --- 整体策略 ---
    if c.get('recommended_mode') == 'emission':
        overall_strategy = 'emission_rgb'
        if g.get('gradient_severity') == 'none':
            strategy_desc = (
                'RGB/OSC发射星云 — 跳过DBE，发射专用校色→保色拉伸'
                '→局部结构增强→轻度缩星'
            )
        else:
            strategy_desc = (
                'RGB/OSC发射星云 — 保守背景提取→发射专用校色'
                '→保色拉伸→局部结构增强'
            )
    elif is_linear and b['darkness_level'] in ('extreme_dark', 'very_dark'):
        overall_strategy = 'deep_recovery'
        strategy_desc = '极暗线性数据深度恢复 — 先DBE→降噪→拉伸→增强'
    elif is_linear:
        overall_strategy = 'standard_linear'
        strategy_desc = '标准线性处理 — DBE→校色→降噪→去星→拉伸→增强'
    elif b['darkness_level'] in ('extreme_dark', 'very_dark'):
        overall_strategy = 'dark_recovery'
        strategy_desc = '暗图恢复 — 激进拉伸+降噪，注意噪声控制'
    else:
        overall_strategy = 'enhancement'
        strategy_desc = '已有基础处理的图像 — 重在增强和微调'

    rec['overall'] = {
        'strategy': overall_strategy,
        'description': strategy_desc,
        'is_linear_data': is_linear,
        'expected_challenge': _identify_challenge(r),
    }

    # --- AI 工具适用性评估 ---
    ai_assess = {
        'ai_denoise_recommended': n['noise_level'] in ('high', 'very_high'),
        'ai_denoise_reason': (
            '高噪声水平适合 AI 降噪工具（NoiseXTerminator / Topaz Denoise）'
            if n['noise_level'] in ('high', 'very_high')
            else '噪声水平适中，内置降噪即可'
        ),
        'ai_star_removal_recommended': s.get('star_density') in ('dense', 'very_dense'),
        'ai_star_removal_reason': (
            '密集星场适合 AI 去星工具（StarNet++ v2），形态学方法精度不足'
            if s.get('star_density') in ('dense', 'very_dense')
            else '星场密度适中，形态学去星可满足'
        ),
        'ai_superres_recommended': False,
        'ai_superres_reason': 'AI 超分辨率在天文摄影中禁止使用 — 会引入伪细节，等于数据造假',
        'ai_colorize_recommended': False,
        'ai_colorize_reason': 'AI 着色在天文摄影中禁止使用 — 天文色彩来自发射线物理，AI 着色必然失真',
    }
    rec['ai_tools'] = ai_assess

    return rec


def _identify_challenge(r):
    """识别处理中的主要挑战。"""
    challenges = []

    if r['brightness']['darkness_level'] in ('extreme_dark', 'very_dark'):
        challenges.append('极暗数据 — 拉伸时噪声会被大幅放大')
    if r['noise']['noise_level'] in ('high', 'very_high'):
        challenges.append('高噪声 — 需要在细节保留和降噪间平衡')
    if r['gradient']['gradient_severity'] in ('moderate', 'severe'):
        challenges.append('严重光害梯度 — DBE可能留下残留')
    if r['starfield'].get('star_density') == 'very_dense':
        challenges.append('密集星场 — 星点分离难度大，可能残留')
    if (r['color']
            and r['color'].get('color_health_effective',
                               r['color'].get('color_health')) == 'severe_cast'):
        challenges.append('严重偏色 — 颜色校准需要多步矫正')

    if not challenges:
        challenges.append('无明显困难 — 标准流程即可')

    return challenges


def format_readable(report):
    """人类可读格式输出。"""
    b = report['brightness']
    n = report['noise']
    g = report['gradient']
    c = report['color'] or {}
    s = report['starfield']
    sh = report['sharpness']
    r = report['recommendations']

    lines = []
    lines.append('=' * 60)
    lines.append('  深空图像诊断报告')
    lines.append('=' * 60)
    lines.append(f'  文件: {report["file"]}')
    lines.append(f'  尺寸: {report["shape"][1]}x{report["shape"][0]}')
    lines.append(f'  格式: {report["format"]}  |  线性数据: {report["is_linear"]}')
    lines.append(f'  RGBA: {report["has_alpha"]}')
    channels = report['channels']
    lines.append(f'  通道: {channels["channel_count"]} ({channels["channel_type"]})')
    hint = report['target_type_hint']
    lines.append(f'  类型初判: {hint["target_type"]} (confidence={hint["confidence"]:.2f})')

    lines.append('\n── 1. 亮度与动态范围 ──')
    lines.append(f'  暗度等级: {b["darkness_level"]}')
    lines.append(f'  亮度分类: {b.get("brightness_class", b["darkness_level"])}')
    lines.append(f'  中位数:   {b["median"]:.5f}')
    lines.append(f'  P99:      {b["percentiles"]["p99"]:.5f}')
    lines.append(f'  标准差:   {b["std"]:.5f}')
    lines.append(f'  非零比例: {b["nonzero_fraction"]:.1%}')
    lines.append(f'  暗像素(<1%): {b["dark_pixel_ratios"]["below_1pct"]:.1%}')
    lines.append(f'  暗像素(<5%): {b["dark_pixel_ratios"]["below_5pct"]:.1%}')
    lines.append(f'  动态范围比: {b["dynamic_range_ratio"]:.1f}')

    lines.append('\n── 2. 噪声评估 ──')
    lines.append(f'  背景噪声σ: {n["background_noise_std"]:.5f}')
    lines.append(f'  噪声等级: {n["noise_level"]}')
    lines.append(f'  色彩噪声: {n.get("chroma_noise_estimate", 0):.5f}')

    lines.append('\n── 3. 背景梯度 ──')
    lines.append(f'  梯度强度: {g["gradient_magnitude"]:.4f}')
    lines.append(f'  严重度:   {g["gradient_severity"]}')
    lines.append(f'  梯度模式: {g.get("gradient_pattern", "N/A")}')
    lines.append(f'  R²(线性): {g.get("r_squared", 0):.3f}')
    lines.append(f'  推荐方法: {g.get("dbe_method_recommendation", "polynomial")}')
    lines.append(f'  推荐理由: {g.get("dbe_method_reason", "")}')
    lines.append(f'  渐晕:     {"是" if g["has_vignetting"] else "否"} (角落/中心={g["vignetting_ratio"]:.2f})')

    if c:
        lines.append('\n── 4. 色彩平衡 ──')
        lines.append(f'  颜色健康: {c.get("color_health", "N/A")}')
        lines.append(f'  信号解释: {c.get("signal_interpretation", "N/A")}')
        lines.append(f'  推荐模式: {c.get("recommended_mode", "standard")}')
        lines.append(f'  偏色比例: R={c["color_cast_ratios"]["r"]:.3f} G={c["color_cast_ratios"]["g"]:.3f} B={c["color_cast_ratios"]["b"]:.3f}')
        signal_ratios = c.get('channel_signal_ratios', {})
        lines.append(
            f'  信号比例: R/G={signal_ratios.get("r_over_g", 1.0):.3f} '
            f'R/B={signal_ratios.get("r_over_b", 1.0):.3f}'
        )
        lines.append(f'  SCNR需求: {"是" if c.get("needs_scnr") else "否"}')
        lines.append(f'  饱和度:   {c.get("saturation_mean", 0):.4f}')

    lines.append('\n── 5. 星场分析 ──')
    lines.append(f'  估计星数: {s.get("star_count_estimate", 0)}')
    lines.append(f'  星场密度: {s.get("star_density", "unknown")}')
    lines.append(f'  覆盖比例: {s.get("star_coverage_pct", 0):.1f}%')

    lines.append('\n── 6. 锐度评估 ──')
    lines.append(f'  锐度等级: {sh["sharpness_level"]}')
    lines.append(f'  Laplacian方差: {sh["laplacian_variance"]:.2e}')

    lines.append('\n── 7. 处理建议 ──')
    ov = r['overall']
    lines.append(f'  策略: {ov["strategy"]} — {ov["description"]}')
    lines.append(f'  DBE:      方法={r["dbe"]["method"]} 阶数={r["dbe"]["degree"]} ({r["dbe"]["reason"]})')
    lines.append(f'  降噪:     L={r["pre_denoise"]["luminance_strength"]} C={r["pre_denoise"]["chroma_strength"]}')
    lines.append(
        f'  拉伸:     method={r["stretch"]["method"]} '
        f'factor={r["stretch"]["factor"]} '
        f'gamma={r["stretch"].get("gamma")} '
        f'target_bg={r["stretch"].get("target_bg")}'
    )
    lines.append(f'  星点:     阈值={r["star_tools"]["detection_threshold"]} 缩星={r["star_tools"]["reduction"]}')
    lines.append(f'  增强:     HDR={r["enhance"]["hdr_strength"]}')
    lines.append(f'  锐化:     amount={r["sharpen"]["amount"]}')
    lines.append(f'  色彩:     饱和×{r["color"]["saturation_factor"]} SCNR={r["color"]["needs_scnr"]}')
    lines.append(f'  挑战:     {"; ".join(ov["expected_challenge"])}')

    ai = r.get('ai_tools', {})
    if ai:
        lines.append('\n── AI 工具适用性评估 ──')
        lines.append(f'  AI降噪:   {"推荐" if ai["ai_denoise_recommended"] else "不需要"} — {ai["ai_denoise_reason"]}')
        lines.append(f'  AI去星:   {"推荐" if ai["ai_star_removal_recommended"] else "不需要"} — {ai["ai_star_removal_reason"]}')
        lines.append(f'  AI超分:   ❌ 禁止 — {ai["ai_superres_reason"]}')
        lines.append(f'  AI着色:   ❌ 禁止 — {ai["ai_colorize_reason"]}')

    lines.append('\n' + '=' * 60)
    return '\n'.join(lines)


def main():
    p = argparse.ArgumentParser(description='深空图像诊断分析器')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('--format', choices=['json', 'readable'], default='json',
                   help='输出格式 (默认: json)')
    p.add_argument('--output', '-o', default=None, help='输出到文件')
    args = p.parse_args()

    report = analyze_image(args.input)

    if args.format == 'readable':
        output = format_readable(report)
    else:
        output = json.dumps(report, indent=2, ensure_ascii=False, default=str)

    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f'报告已保存到: {args.output}', file=sys.stderr)
    else:
        print(output)


if __name__ == '__main__':
    main()
