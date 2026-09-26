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


def calculate_metrics(image, manifest_data=None):
    source = np.asarray(image, dtype=np.float32)
    gray = grayscale(source)
    processing_stage = (
        manifest_data.get("processing_stage", "final")
        if manifest_data else "final"
    )
    corner_size = max(3, min(gray.shape[:2]) // 8)
    corners = [
        float(np.mean(gray[:corner_size, :corner_size])),
        float(np.mean(gray[:corner_size, -corner_size:])),
        float(np.mean(gray[-corner_size:, :corner_size])),
        float(np.mean(gray[-corner_size:, -corner_size:])),
    ]
    corner_min = max(min(corners), 1e-10)
    corner_ratio = float(max(corners) / corner_min)

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
