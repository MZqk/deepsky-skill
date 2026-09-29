#!/usr/bin/env python3
"""Quantitative anchors for AI visual review checkpoints."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.ndimage import uniform_filter

sys.path.insert(0, str(Path(__file__).parent))
from fits_io import read_image
from star_tools import detect_stars


def grayscale(image):
    if image.ndim == 2:
        return image.astype(np.float32)
    return np.mean(image[..., :3], axis=2, dtype=np.float32)


def analyze_corner_uniformity(image, corner_size=None, manifest_data=None):
    """
    天体物理感知的四角均匀度分析器 (Celestial-Aware Corner Uniformity Analyzer)。

    能够智能区分角区内的真实深空天体信号（弥散发射星云/旋臂/星团）与纯太空背景底电平，
    消除天体靠边构图或视场弥漫所导致的四角不均匀假阳性误报。
    """
    source = np.asarray(image, dtype=np.float32)
    gray = grayscale(source)
    h, w = gray.shape[:2]
    cs = corner_size or max(3, min(h, w) // 8)

    corner_patches = {
        "top_left": gray[:cs, :cs],
        "top_right": gray[:cs, -cs:],
        "bottom_left": gray[-cs:, :cs],
        "bottom_right": gray[-cs:, -cs:],
    }
    corner_keys = ["top_left", "top_right", "bottom_left", "bottom_right"]
    raw_means = [float(np.mean(corner_patches[k])) for k in corner_keys]
    raw_min = max(min(raw_means), 1e-10)
    raw_ratio = float(max(raw_means) / raw_min)

    alive = (gray > 0) & np.isfinite(gray)
    if np.count_nonzero(alive) < 100:
        return {
            "raw_means": [round(v, 6) for v in raw_means],
            "raw_uniformity_ratio": round(raw_ratio, 6),
            "effective_uniformity_ratio": round(raw_ratio, 6),
            "sky_background_medians": [round(v, 6) for v in raw_means],
            "celestial_coverage": {k: 0.0 for k in corner_keys},
            "celestial_dominated_corners": [],
            "pure_sky_corners": corner_keys,
            "exemption_applied": False,
            "exemption_reason": None,
        }

    # 基于暗部前 25% 稳健估算全局天空背景基线与噪声
    pool = gray[alive]
    p25 = float(np.percentile(pool, 25.0))
    bg_pool = pool[pool <= p25]
    bg_median = float(np.median(bg_pool))
    bg_std = float(np.std(bg_pool))

    # 3-sigma 天体物理信号检出门限
    celestial_thresh = max(bg_median + 3.0 * bg_std, bg_median * 1.35)

    # 检查画面主体区域 (Inner Core Region) 是否存在更强盛的天体核心
    ch0, ch1 = int(h * 0.20), int(h * 0.80)
    cw0, cw1 = int(w * 0.20), int(w * 0.80)
    inner = gray[ch0:ch1, cw0:cw1]
    inner_peak = float(np.percentile(inner, 99.0)) if inner.size > 0 else 0.0
    core_contrast = inner_peak / max(bg_median, 1e-6)
    has_celestial_core = (core_contrast >= 3.5)

    sky_medians = []
    coverage_dict = {}
    celestial_dominated = []
    pure_sky_corners = []

    for k in corner_keys:
        patch = corner_patches[k]
        is_celestial = patch > celestial_thresh
        cov = float(np.mean(is_celestial))
        coverage_dict[k] = round(cov, 4)

        # 提取角区内的纯背景像素
        sky_pixels = patch[~is_celestial]
        if sky_pixels.size >= max(30, int(patch.size * 0.15)):
            med = float(np.median(sky_pixels))
        else:
            # 角区几乎完全被天体覆盖，取暗部分位保底
            med = float(np.percentile(patch, 10.0))
        sky_medians.append(med)

        # 判定是否属于天体主导角：
        # 必须满足双重物理准则：
        # 1. 角区天体信号覆盖率显著 (cov >= 0.35) 且均值高于背景；
        # 2. 全图中央/主体区域存在更强盛的天体核心 (inner_peak >= patch_mean * 1.20 且 core_contrast >= 3.5)，
        #    证明角区是真实天体结构的自然延伸，杜绝将单纯的单角严重光害倾斜/漏光误认为天体！
        is_extension = (
            has_celestial_core
            and (inner_peak >= float(np.mean(patch)) * 1.20)
        )
        if cov >= 0.35 and float(np.mean(patch)) > (bg_median * 1.35) and is_extension:
            celestial_dominated.append(k)
        else:
            pure_sky_corners.append(k)

    # 计算有效均匀度比值与豁免评定
    exemption_applied = False
    exemption_reason = None

    if celestial_dominated and len(pure_sky_corners) >= 2:
        pure_bg_vals = [
            sky_medians[corner_keys.index(k)] for k in pure_sky_corners
        ]
        sky_ratio = float(max(pure_bg_vals) / max(min(pure_bg_vals), 1e-10))
        if sky_ratio <= 3.0:
            exemption_applied = True
            effective_ratio = sky_ratio
            dom_names = ", ".join(celestial_dominated)
            exemption_reason = (
                f"天体物理结构显著覆盖角区 ({dom_names})，"
                f"纯背景角均匀度达标 ({sky_ratio:.2f}x <= 3.0x)"
            )
        else:
            effective_ratio = sky_ratio
    else:
        # 无天体主导角，使用原始比值与背景中位比值
        effective_ratio = raw_ratio

    return {
        "raw_means": [round(v, 6) for v in raw_means],
        "raw_uniformity_ratio": round(raw_ratio, 6),
        "sky_background_medians": [round(v, 6) for v in sky_medians],
        "effective_uniformity_ratio": round(effective_ratio, 6),
        "celestial_coverage": coverage_dict,
        "celestial_dominated_corners": celestial_dominated,
        "pure_sky_corners": pure_sky_corners,
        "exemption_applied": exemption_applied,
        "exemption_reason": exemption_reason,
    }


def calculate_metrics(image, manifest_data=None):
    source = np.asarray(image, dtype=np.float32)
    gray = grayscale(source)
    processing_stage = (
        manifest_data.get("processing_stage", "final")
        if manifest_data else "final"
    )
    corner_size = max(3, min(gray.shape[:2]) // 8)
    corner_info = analyze_corner_uniformity(
        gray, corner_size=corner_size, manifest_data=manifest_data
    )
    corners = corner_info["raw_means"]
    corner_ratio = corner_info["effective_uniformity_ratio"]

    local_mean = uniform_filter(gray, size=5, mode="reflect")
    local_sq_mean = uniform_filter(gray * gray, size=5, mode="reflect")
    local_std = np.sqrt(np.maximum(local_sq_mean - local_mean * local_mean, 0))
    dark = gray < np.percentile(gray, 50)
    uniform_patch_ratio = float(np.mean((local_std < 1e-4) & dark))

    centered = gray - float(np.mean(gray))
    spectrum = np.abs(np.fft.rfft2(centered)) ** 2
    yy, xx = np.ogrid[:spectrum.shape[0], :spectrum.shape[1]]
    radius = np.sqrt((yy / max(gray.shape[0], 1)) ** 2 +
                     (xx / max(gray.shape[1], 1)) ** 2)
    total_energy = float(np.sum(spectrum)) or 1.0
    high_frequency_ratio = float(np.sum(spectrum[radius > 0.18]) / total_energy)

    # 优先使用线性阶段去星前的干净星点覆盖率
    linear_metrics = manifest_data.get('linear_star_metrics') if manifest_data else None
    if linear_metrics and linear_metrics.get('star_area_ratio') is not None:
        star_area = linear_metrics['star_area_ratio']
        star_metric_warning = None
    else:
        try:
            star_mask = detect_stars(gray, star_threshold=0.8)
            star_area = float(np.mean(star_mask > 0.5))
            star_metric_warning = None
        except Exception as exc:
            star_area = 0.0
            star_metric_warning = f"star detection unavailable: {exc}"

    res = {
        "processing_stage": processing_stage,
        "median": round(float(np.median(gray)), 6),
        "p50": round(float(np.percentile(gray, 50.0)), 6),
        "p99": round(float(np.percentile(gray, 99.0)), 6),
        "p99_9": round(float(np.percentile(gray, 99.9)), 6),
        "p01": round(float(np.percentile(gray, 0.1)), 6),
        "p1": round(float(np.percentile(gray, 1.0)), 6),
        "highlight_clip_ratio": round(float(np.mean(gray >= 0.995)), 6),
        "negative_pixel_ratio": round(float(np.mean(gray < 0)), 6),
        "nonpositive_pixel_ratio": round(float(np.mean(gray <= 0)), 6),
        "corner_means": [round(value, 6) for value in corners],
        "corner_uniformity_ratio": round(corner_ratio, 6),
        "raw_corner_uniformity_ratio": round(corner_info["raw_uniformity_ratio"], 6),
        "corner_analysis": corner_info,
        "uniform_5x5_dark_patch_ratio": round(uniform_patch_ratio, 6),
        "high_frequency_energy_ratio": round(high_frequency_ratio, 6),
        "star_area_ratio": round(star_area, 6),
    }

    if source.ndim == 3 and source.shape[2] >= 3:
        channel_p99 = np.percentile(
            source[..., :3].reshape(-1, 3),
            99.0,
            axis=0,
        )
        reference = max(float(channel_p99[0]), 1e-9)
        res["channel_p99"] = {
            "r": round(float(channel_p99[0]), 6),
            "g": round(float(channel_p99[1]), 6),
            "b": round(float(channel_p99[2]), 6),
        }
        res["channel_signal_ratios"] = {
            "r_over_g": round(reference / max(float(channel_p99[1]), 1e-9), 4),
            "r_over_b": round(reference / max(float(channel_p99[2]), 1e-9), 4),
        }
        res["collapsed_channels"] = [
            label
            for label, value in zip(("r", "g", "b"), channel_p99)
            if value < max(float(np.max(channel_p99)) * 0.01, 1e-6)
        ]

        # 背景色偏：在**三通道都还活着**的像素里取最暗的 25% 作为背景样本。
        #
        # 两个必须排除的情况：
        #   1. 恰好为 0 的像素 —— DBE 之后常有超过一半画面被 clip 到 0，
        #      直接在整幅图上取暗部，样本会全部落在 0 上，色比变成 0/0。
        #   2. 只有单通道非零的像素 —— 若 G/B 已贴 0 而 R 还剩一点残值，
        #      按 R/G 计算会得到 1e5 量级的假数值。
        # 当找不到足够多"三通道都有信号"的背景像素时，说明背景已被压死，
        # 此时不产出色偏指标（由 BACKGROUND_CRUSHED 门负责报告）。
        rgb = source[..., :3]
        gray_bg = rgb.mean(axis=2)
        alive = np.min(rgb, axis=2) > 0
        if int(np.count_nonzero(alive)) >= 100:
            pool = gray_bg[alive]
            bg_mask = alive & (gray_bg <= float(np.percentile(pool, 25)))
        else:
            bg_mask = np.zeros(gray_bg.shape, dtype=bool)

        if int(np.count_nonzero(bg_mask)) >= 100:
            bg_medians = np.median(rgb[bg_mask], axis=0).astype(np.float64)
            r_over_g = float(bg_medians[0] / max(bg_medians[1], 1e-9))
            b_over_g = float(bg_medians[2] / max(bg_medians[1], 1e-9))
            res["background_channel_medians"] = {
                "r": round(float(bg_medians[0]), 6),
                "g": round(float(bg_medians[1]), 6),
                "b": round(float(bg_medians[2]), 6),
            }
            res["background_color_cast"] = {
                "r_over_g": round(r_over_g, 4),
                "b_over_g": round(b_over_g, 4),
            }
            res["background_color_cast_magnitude"] = round(
                max(abs(r_over_g - 1.0), abs(b_over_g - 1.0)), 4
            )

    if linear_metrics:
        if linear_metrics.get('estimated_fwhm') is not None:
            res['linear_estimated_fwhm_px'] = round(linear_metrics['estimated_fwhm'], 2)
        if linear_metrics.get('n_stars_detected') is not None:
            res['linear_n_stars_detected'] = linear_metrics['n_stars_detected']
    # 风格色调曲线的局部黑位诊断：透传给 evaluate_quality_gates 的
    # STYLE_LOCAL_BLACK_CLIP 门禁（无该字段时门禁不参与判定）。
    style_diagnostics = (manifest_data or {}).get('style_diagnostics')
    if style_diagnostics is not None:
        res['style_diagnostics'] = style_diagnostics
    # 去星修补足迹诊断：透传给 evaluate_quality_gates 的 INPAINT_FOOTPRINT 门禁
    # （无该字段时门禁不参与判定）。
    inpaint_footprint = (manifest_data or {}).get('inpaint_footprint')
    if inpaint_footprint is not None:
        res['inpaint_footprint'] = inpaint_footprint
    if star_metric_warning:
        res['warnings'] = [star_metric_warning]

    return res


def main():
    import os
    parser = argparse.ArgumentParser(description="深空图像质量量化指标")
    parser.add_argument("input")
    parser.add_argument("--output")
    parser.add_argument("--manifest", default=None, help="manifest.json 路径")
    args = parser.parse_args()

    # 尝试加载 manifest
    manifest_data = None
    manifest_path = args.manifest

    # 如果未显式指定，但在 input 同级目录或同级 intermediates/ 目录有 manifest.json，自动尝试加载
    if not manifest_path:
        input_dir = os.path.dirname(os.path.abspath(args.input))
        possible_paths = [
            os.path.join(input_dir, "manifest.json"),
            os.path.join(input_dir, "intermediates", "manifest.json"),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                manifest_path = p
                break

    if manifest_path and os.path.exists(manifest_path):
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                manifest_data = json.load(f)
                print(f"[质量量化] 成功加载关联的元数据: {manifest_path}")
        except Exception as e:
            print(f"[质量量化 WARN] 无法解析 {manifest_path}: {e}")

    image, _meta = read_image(args.input)
    payload = calculate_metrics(image, manifest_data)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
