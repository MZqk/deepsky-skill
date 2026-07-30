#!/usr/bin/env python3
"""
Deep-Sky Post-Processing Pipeline (深空天文后期全流程管线)

参照 Siril + SetiAstroSuitePro 专业流程:

  ── 线性阶段 (Siril Linear Phase) ──
  Phase 1:  裁切 (可选)
  Phase 2:  背景提取 (DBE/ABE)
  Phase 3:  颜色校准 (PCC/白平衡)
  Phase 4:  初步降噪 (GXP Silentium-like)
  Phase 5:  去星 (StarNet/SyqonStarless, 在线性数据上)  ← 关键差异
  Phase 6:  拉伸 (自动百分位拉伸, 线性→非线性)

  ── 星点独立处理 (Star Processing, 并行) ──
  Phase S1: 星点拉伸 (Star Stretch)
  Phase S2: 星点去紫 (SCNR)
  Phase S3: 星点曲线微调 (Curves)

  ── 非线性阶段 (Non-Linear Phase) ──
  Phase 7:  星云细节增强 (HDR/Clarity)
  Phase 8:  锐化 (Revela-like)
  Phase 9:  颜色调整 (Vectra-like)

  ── 最终阶段 ──
  Phase 10: 星点合成 (StarComposer)
  Phase 11: 最终降噪 (SCUNet, 必须最后执行)  ← 新增
  Phase 12: 最终输出

流程依据 (Siril 2026 标准流程):
  - 去星在拉伸前: 线性数据上星点未膨胀，AI检测更精确
  - 星点独立拉伸+调色: 与星云完全解耦，避免星点过曝/偏色
  - 双降噪: 初步降噪在线性阶段，最终降噪在星点合成后(整体)

所有处理基于原图真实数据，不引入虚假细节。

用法:
  python pipeline.py M42.fit output.jpg          # FITS 输入
  python pipeline.py input.jpg output.jpg          # PNG/JPG 输入
  python pipeline.py input.jpg output.jpg --strength strong --save-intermediates
  python pipeline.py input.fit output.fit          # FITS 输入→FITS 输出
  python pipeline.py input.fit output.jpg --steps dbe,color,star_remove,stretch
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime
import numpy as np
from pathlib import Path
from scipy.ndimage import gaussian_filter, label
from skimage.transform import resize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fits_io import (
    read_image,
    write_image,
    resolve_celestial_target,
    read_capture_metadata,
    build_physical_priors,
)
from gradient_removal import remove_gradient
from stretch import auto_stretch, arcsinh_stretch, masked_stretch, deep_stretch, apply_luminance_stretch
from denoise import denoise_luminance_chroma
from sharpen import multiscale_sharpen, adaptive_signal_sharpen
from star_tools import (separate_stars, reduce_stars, combine_starless_stars,
                        mild_star_reduce_full, detect_stars, estimate_fwhm)
from enhance import (hdr_multiscale_compress, protected_hdr_compress, apply_curves,
                     local_nebula_enhance, positive_starless_detail_enhance)
from color_tools import (auto_color_calibrate, emission_nebula_calibrate,
                         enhance_saturation, background_neutralize,
                         remove_green_noise, stabilize_emission_channels)
from style_tools import STYLE_PROFILES, apply_professional_style
from quality_metrics import calculate_metrics
from reference_grade import match_reference_grade, optimize_reference_grade
from agent_protocol import (
    SCHEMA_VERSION as AGENT_SCHEMA_VERSION,
    evaluate_quality_gates,
    now_iso,
    write_json_atomic as write_result_json_atomic,
)
try:
    from recognize import (
        ASTRO_INPUT_EXTENSIONS,
        build_recognition_workflow,
        recognize_image,
        write_json_atomic,
    )
except Exception:
    ASTRO_INPUT_EXTENSIONS = {".fit", ".fits", ".fts", ".xisf"}
    build_recognition_workflow = None
    recognize_image = None
    write_json_atomic = None


# skimage.color monkey patch removed because safe conversions are handled by color_conv.py using OpenCV


STRENGTH_PRESETS = {
    'light': {
        # 线性阶段
        'dbe_degree': 2,
        'dbe_pctl_low': 0.5,
        'dbe_pctl_high': 99.5,
        'pre_denoise_lum': 0.012,
        'pre_denoise_chroma': 0.04,
        'star_threshold': 0.88,
        'stretch_factor': 25.0,          # arcsinh factor (luminance-only)
        # 星点处理
        'star_stretch_factor': 8.0,
        'star_scnr_strength': 0.15,
        'star_reduction': 0.2,
        'star_curves_midtones': 1.05,
        'star_combine_strength': 0.8,
        # 非线性阶段
        'hdr_strength': 0.25,
        'sharpen_amount': 0.7,
        # 最终阶段
        'saturation': 1.2,
        'final_denoise_lum': 0.01,
        'final_denoise_chroma': 0.03,
    },
    'medium': {
        'dbe_degree': 2,
        'dbe_pctl_low': 0.3,
        'dbe_pctl_high': 99.7,
        'pre_denoise_lum': 0.02,
        'pre_denoise_chroma': 0.07,
        'star_threshold': 0.85,
        'stretch_factor': 45.0,
        'star_stretch_factor': 12.0,
        'star_scnr_strength': 0.25,
        'star_reduction': 0.3,
        'star_curves_midtones': 1.1,
        'star_combine_strength': 1.0,
        'hdr_strength': 0.5,
        'sharpen_amount': 1.3,
        'saturation': 1.5,
        'final_denoise_lum': 0.015,
        'final_denoise_chroma': 0.05,
    },
    'adaptive': {
        'dbe_method': 'rbf',
        'dbe_degree': 3,
        'dbe_pctl_low': 0.3,
        'dbe_pctl_high': 99.7,
        'pre_denoise_lum': 0.008,   # slightly stronger for dark signals
        'pre_denoise_chroma': 0.02,
        'star_threshold': 0.78,     # very_dense 星场降低阈值
        'stretch_factor': 120.0,
        'target_bg': 0.06,          # lower background for deeper space
        'star_stretch_factor': 24.0,
        'star_scnr_strength': 0.0,  # 无绿噪
        'star_reduction': 0.40,     # very_dense 强缩星
        'star_curves_midtones': 1.1,
        'star_combine_strength': 1.0,
        'hdr_strength': 0.35,       # 发射星云 HDR +0.15 偏移
        'sharpen_amount': 0.6,
        'saturation': 1.45,         # lower saturation to prevent color cast
        'final_denoise_lum': 0.0025,
        'final_denoise_chroma': 0.0075,
        'shadow_pctl': 0.5,
        'highlight_pctl': 99.9,
        'stretch_gamma': 0.42,      # higher gamma to push noise into shadows
    },
    'emission': {
        # 发射星云专用预设：保护 Hα 红色主导，激进拉伸，强缩星
        'dbe_method': 'rbf',
        'dbe_degree': 3,
        'dbe_pctl_low': 0.1,
        'dbe_pctl_high': 99.9,
        'pre_denoise_lum': 0.008,
        'pre_denoise_chroma': 0.02,
        'star_threshold': 0.78,      # very_dense 星场降低阈值
        'stretch_factor': 120.0,
        'target_bg': 0.06,           # lower background
        'star_stretch_factor': 18.0,
        'star_scnr_strength': 0.0,
        'star_reduction': 0.40,      # very_dense 星场强缩星
        'star_curves_midtones': 1.05,
        'star_combine_strength': 0.85,
        'hdr_strength': 0.35,        # 发射星云 HDR +0.15 偏移
        'sharpen_amount': 0.65,
        'saturation': 1.45,          # lower saturation to prevent color cast
        'final_denoise_lum': 0.003,
        'final_denoise_chroma': 0.008,
        'shadow_pctl': 0.5,
        'highlight_pctl': 99.9,
        'stretch_gamma': 0.42,       # higher gamma to deepen shadows
    },
    'strong': {
        'dbe_degree': 3,
        'dbe_pctl_low': 0.2,
        'dbe_pctl_high': 99.8,
        'pre_denoise_lum': 0.035,
        'pre_denoise_chroma': 0.10,
        'star_threshold': 0.82,
        'stretch_factor': 80.0,
        'star_stretch_factor': 18.0,
        'star_scnr_strength': 0.35,
        'star_reduction': 0.4,
        'star_curves_midtones': 1.2,
        'star_combine_strength': 1.15,
        'hdr_strength': 0.75,
        'sharpen_amount': 2.0,
        'saturation': 2.0,
        'final_denoise_lum': 0.025,
        'final_denoise_chroma': 0.08,
    },
}

ALL_STEPS = ['dbe', 'color', 'pre_denoise', 'star_remove', 'stretch',
             'star_process', 'enhance', 'sharpen', 'final_color',
             'style', 'star_combine', 'final_denoise', 'local_enhance',
             'external_detail', 'star_reduce']
EMISSION_STEPS = [
    'color', 'stretch', 'final_color', 'style', 'local_enhance', 'star_reduce'
]


def resolve_step_dependencies(steps):
    """Expand dependent steps and return canonical execution order plus log."""
    requested = list(dict.fromkeys(steps))
    expanded = set(requested)
    log = []

    # star_process is not useful as a standalone operation: it requires a
    # separated stellar layer, a stretched starless base, and recomposition.
    if "star_process" in expanded:
        required = {"star_remove", "stretch", "star_combine"}
        added = sorted(required - expanded, key=ALL_STEPS.index)
        expanded.update(required)
        if added:
            log.append(
                "⭐ star_process 依赖补全: " + ", ".join(added)
            )

    # star_combine requires both source layers and a processed stellar layer.
    if "star_combine" in expanded:
        required = {"star_remove", "stretch", "star_process"}
        added = sorted(required - expanded, key=ALL_STEPS.index)
        expanded.update(required)
        if added:
            log.append(
                "⭐ star_combine 依赖补全: " + ", ".join(added)
            )

    ordered = [step for step in ALL_STEPS if step in expanded]
    return ordered, log


def resolve_ghs_b(cfg):
    """Map generic stretch_factor onto the smaller useful GHS b range."""
    if 'ghs_b' in cfg:
        return float(np.clip(cfg['ghs_b'], 2.0, 15.0))
    factor = max(float(cfg.get('stretch_factor', 45.0)), 0.0)
    return float(np.clip(2.0 + 1.5 * np.log1p(factor), 4.0, 12.0))


def parse_crop(crop):
    """Parse x,y,width,height crop syntax."""
    if crop is None:
        return None
    values = [int(value.strip()) for value in crop.split(',')]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        raise ValueError("--crop 必须为 x,y,width,height，且宽高为正数")
    return tuple(values)


def apply_crop(image, crop):
    """Apply a bounded crop and return image plus normalized bounds."""
    if crop is None:
        return image, None
    x, y, width, height = crop
    h, w = image.shape[:2]
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(w, x + width), min(h, y + height)
    if x0 >= x1 or y0 >= y1:
        raise ValueError(f"裁切区域 {crop} 不在图像范围 {w}x{h} 内")
    return image[y0:y1, x0:x1].copy(), (x0, y0, x1 - x0, y1 - y0)


def detect_black_edge_crop(image, max_invalid_fraction=0.005,
                           max_crop_fraction=0.12, min_run=8):
    """Trim only invalid stacking borders while preserving the full field."""
    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 3:
        luminance = (
            0.299 * source[..., 0]
            + 0.587 * source[..., 1]
            + 0.114 * source[..., 2]
        )
    else:
        luminance = source

    finite = luminance[np.isfinite(luminance)]
    h, w = luminance.shape
    if finite.size == 0:
        raise ValueError("图像不包含有效像素")

    low_reference = float(np.percentile(finite, 0.1))
    black_threshold = max(1e-8, low_reference * 0.15)
    invalid = ~np.isfinite(luminance) | (luminance <= black_threshold)
    components, _component_count = label(invalid)
    edge_ids = np.unique(np.concatenate([
        components[0, :], components[-1, :],
        components[:, 0], components[:, -1],
    ]))
    minimum_component_area = max(16, int(h * w * 0.0001))
    exterior_invalid = np.zeros_like(invalid)
    for component_id in edge_ids:
        if component_id == 0:
            continue
        component = components == component_id
        if int(component.sum()) >= minimum_component_area:
            exterior_invalid |= component
    invalid = exterior_invalid

    def edge_depth(fractions, reverse=False):
        values = fractions[::-1] if reverse else fractions
        limit = max(1, int(len(values) * max_crop_fraction))
        interior_baseline = float(np.percentile(values, 10))
        accepted = interior_baseline + max_invalid_fraction
        for index in range(0, limit + 1):
            end = min(len(values), index + min_run)
            if end - index == min_run and np.all(
                values[index:end] <= accepted
            ):
                return index
        return 0

    top = edge_depth(invalid.mean(axis=1))
    bottom = edge_depth(invalid.mean(axis=1), reverse=True)
    left = edge_depth(invalid.mean(axis=0))
    right = edge_depth(invalid.mean(axis=0), reverse=True)

    if top + bottom >= h or left + right >= w:
        top = bottom = left = right = 0
    crop = (left, top, w - left - right, h - top - bottom)
    trimmed = any((top, bottom, left, right))
    return crop, {
        'mode': 'black_edges',
        'trimmed': trimmed,
        'threshold': black_threshold,
        'edges': {
            'top': top, 'bottom': bottom, 'left': left, 'right': right,
        },
        'source_shape': [h, w],
        'output_shape': [crop[3], crop[2]],
    }


def detect_target_crop(image, padding=2.0, max_preview=900):
    """Locate a compact deep-sky structure and return a square crop."""
    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 3:
        luminance = (
            0.299 * source[..., 0]
            + 0.587 * source[..., 1]
            + 0.114 * source[..., 2]
        )
    else:
        luminance = source
    h, w = luminance.shape
    scale = min(1.0, max_preview / max(h, w))
    if scale < 1.0:
        preview = resize(
            luminance,
            (max(32, round(h * scale)), max(32, round(w * scale))),
            preserve_range=True,
            anti_aliasing=True,
        ).astype(np.float32)
    else:
        preview = luminance

    fine = gaussian_filter(preview, sigma=2)
    broad_sigma = max(12.0, min(preview.shape) / 14.5)
    detail = np.clip(fine - gaussian_filter(preview, sigma=broad_sigma), 0, None)
    score = gaussian_filter(detail, sigma=max(3.0, min(preview.shape) / 84.0))

    margin = max(4, int(min(score.shape) * 0.08))
    score[:margin, :] = 0
    score[-margin:, :] = 0
    score[:, :margin] = 0
    score[:, -margin:] = 0
    peak_y, peak_x = np.unravel_index(np.argmax(score), score.shape)
    peak = float(score[peak_y, peak_x])
    if peak <= 0:
        raise ValueError("无法从图像中定位目标结构")

    components, _count = label(score > peak * 0.15)
    component_id = components[peak_y, peak_x]
    ys, xs = np.where(components == component_id)
    if xs.size < 4:
        raise ValueError("目标结构区域过小，无法可靠自动裁切")

    x0 = float(xs.min()) / scale
    x1 = float(xs.max() + 1) / scale
    y0 = float(ys.min()) / scale
    y1 = float(ys.max() + 1) / scale
    center_x = (x0 + x1) / 2
    center_y = (y0 + y1) / 2
    object_size = max(x1 - x0, y1 - y0)
    crop_size = int(np.ceil(max(128.0, object_size * max(padding, 1.1))))
    crop_size = min(crop_size, w, h)
    crop_x = int(round(center_x - crop_size / 2))
    crop_y = int(round(center_y - crop_size / 2))
    crop_x = min(max(0, crop_x), w - crop_size)
    crop_y = min(max(0, crop_y), h - crop_size)
    return (crop_x, crop_y, crop_size, crop_size), {
        'center': [round(center_x, 2), round(center_y, 2)],
        'object_bbox': [
            round(x0, 2), round(y0, 2),
            round(x1 - x0, 2), round(y1 - y0, 2),
        ],
        'peak_score': peak,
        'padding': padding,
        'source_shape': [h, w],
    }


def make_decision_preview(image, max_dimension=1920):
    """Create a bounded preview for decisions while preserving full output."""
    source = np.asarray(image, dtype=np.float32)
    h, w = source.shape[:2]
    scale = min(1.0, float(max_dimension) / max(h, w))
    if scale >= 1.0:
        return source
    shape = (
        max(32, round(h * scale)),
        max(32, round(w * scale)),
    )
    if source.ndim == 3:
        shape = (*shape, source.shape[2])
    return resize(
        source,
        shape,
        preserve_range=True,
        anti_aliasing=True,
    ).astype(np.float32)


def resolve_memory_plan(shape, steps, low_memory=False,
                        auto_low_memory=True, threshold_mpix=8.0,
                        tile_size=None, external_starless=False):
    """Resolve low-memory behavior without downsampling final processing."""
    h, w = shape[:2]
    megapixels = float(h * w) / 1_000_000.0
    enabled = bool(
        low_memory
        or (auto_low_memory and megapixels >= float(threshold_mpix))
    )
    resolved_steps = list(steps)
    skipped_steps = []
    effective_tile = tile_size
    if enabled:
        effective_tile = effective_tile or 768
        if not external_starless:
            for step in (
                "star_remove", "star_process", "star_combine", "star_reduce"
            ):
                if step in resolved_steps:
                    resolved_steps.remove(step)
                    skipped_steps.append(step)
    return resolved_steps, effective_tile, {
        "enabled": enabled,
        "requested": bool(low_memory),
        "auto_enabled": bool(enabled and not low_memory),
        "threshold_mpix": float(threshold_mpix),
        "input_megapixels": round(megapixels, 3),
        "decision_preview_max_dimension": 1920 if enabled else None,
        "tile_size": effective_tile,
        "skipped_steps": skipped_steps,
        "full_resolution_output": True,
    }


def parse_point(point):
    if point is None:
        return None
    values = [int(value.strip()) for value in point.split(',')]
    if len(values) != 2:
        raise ValueError("坐标必须为 x,y")
    return tuple(values)


def default_recognition_output(output_path):
    """Return default recognition JSON path for a processed image."""
    base, _ = os.path.splitext(output_path)
    return base + ".recognition.json"


def _quality_tag_labels(payload):
    return {
        item.get("label")
        for item in payload.get("quality_tags", [])
        if isinstance(item, dict) and item.get("label")
    }


def build_recognition_comparison(input_payload, final_payload):
    """Build comparison JSON from two single-image recognition payloads."""
    input_tags = _quality_tag_labels(input_payload)
    final_tags = _quality_tag_labels(final_payload)
    input_scene = input_payload.get("scene", {}).get("target_type")
    final_scene = final_payload.get("scene", {}).get("target_type")
    return {
        "schema_version": "1.0",
        "mode": "comparison",
        "input": input_payload,
        "final": final_payload,
        "comparison": {
            "scene_consistent": bool(input_scene and input_scene == final_scene),
            "input_target_type": input_scene,
            "final_target_type": final_scene,
            "added_quality_tags": sorted(final_tags - input_tags),
            "removed_quality_tags": sorted(input_tags - final_tags),
            "quality_tag_changes": sorted(input_tags.symmetric_difference(final_tags)),
        },
    }


def run_optional_recognition(input_path, output_path, recognize=False,
                             recognize_output=None, recognize_input=False,
                             workflow_dir=None, analysis_report=None,
                             plate_solution=None):
    """Run optional recognition after image output. Returns True on success."""
    if not recognize:
        return False
    if recognize_image is None or write_json_atomic is None:
        print("[WARN] 识别模块不可用，跳过 recognition JSON")
        return False
    target_json = recognize_output or default_recognition_output(output_path)
    try:
        final_payload = recognize_image(output_path, stage="final")
        input_is_astronomical = (
            Path(input_path).suffix.lower() in ASTRO_INPUT_EXTENSIONS
        )
        if input_is_astronomical and build_recognition_workflow is not None:
            workflow_path = workflow_dir or str(
                Path(target_json).with_suffix("")
            ) + "_workflow"
            input_workflow = build_recognition_workflow(
                input_path,
                workflow_path,
                analysis_report=analysis_report,
                plate_solution=plate_solution,
                stage="input",
            )
            input_payload = input_workflow[
                "local_cv_auxiliary_validation"
            ]
            comparison = build_recognition_comparison(
                input_payload, final_payload
            )
            payload = {
                "schema_version": "1.1",
                "mode": "hybrid_visual_workflow",
                "status": "awaiting_ai_visual_review",
                "input_workflow": input_workflow,
                "final": final_payload,
                "comparison": comparison["comparison"],
            }
        elif recognize_input:
            input_payload = recognize_image(input_path, stage="input")
            payload = build_recognition_comparison(input_payload, final_payload)
        else:
            payload = final_payload
        write_json_atomic(payload, target_json)
        print(f"  🔎 识别结果: {target_json}")
        return True
    except Exception as exc:
        print(f"[WARN] 识别阶段失败，已保留图像输出: {exc}")
        return False


# ══════════════════════════════════════════════════════════════
# 诊断驱动配置与天体类型安全规则
# ══════════════════════════════════════════════════════════════

def load_analysis_report(path):
    """加载 analyze.py 生成的 JSON 诊断报告。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as exc:
        print(f"[WARN] 无法加载诊断报告 {path}: {exc}")
        return None


def build_config_from_analysis(report, base_preset='medium', target_type=None):
    """
    将 analyze.py 的诊断报告映射为 pipeline 配置参数。

    以 base_preset（默认 medium）为安全基底，用诊断数据中的
    recommendations 逐参数覆盖，实现真正的自适应处理。
    """
    cfg = dict(STRENGTH_PRESETS[base_preset])
    rec = report.get('recommendations', {}) if report else {}
    reasons = []

    # ── DBE ──
    dbe_rec = rec.get('dbe', {})
    if dbe_rec.get('method') and dbe_rec['method'] != 'skip':
        cfg['dbe_method'] = dbe_rec['method']
        if dbe_rec.get('degree') is not None:
            cfg['dbe_degree'] = dbe_rec['degree']
        reasons.append(
            f"DBE: method={cfg['dbe_method']} degree={cfg.get('dbe_degree')}"
        )
    elif dbe_rec.get('method') == 'skip':
        reasons.append("DBE: 诊断建议跳过")

    # ── 初步降噪 ──
    pre = rec.get('pre_denoise', {})
    if 'luminance_strength' in pre:
        cfg['pre_denoise_lum'] = pre['luminance_strength']
    if 'chroma_strength' in pre:
        cfg['pre_denoise_chroma'] = pre['chroma_strength']
    if pre:
        reasons.append(
            f"Pre-denoise: L={cfg['pre_denoise_lum']} C={cfg['pre_denoise_chroma']}"
        )

    # ── 拉伸 ──
    stretch = rec.get('stretch', {})
    if 'factor' in stretch:
        cfg['stretch_factor'] = stretch['factor']
    if 'method' in stretch:
        cfg['stretch_method'] = stretch['method']
    if 'gamma' in stretch:
        cfg['stretch_gamma'] = stretch['gamma']
    if 'target_bg' in stretch:
        cfg['target_bg'] = stretch['target_bg']
    if stretch:
        reasons.append(
            f"Stretch: method={cfg.get('stretch_method', 'auto')} "
            f"factor={cfg.get('stretch_factor')} "
            f"gamma={cfg.get('stretch_gamma')} "
            f"target_bg={cfg.get('target_bg')}"
        )

    # ── 星点工具 ──
    star = rec.get('star_tools', {})
    if 'detection_threshold' in star:
        cfg['star_threshold'] = star['detection_threshold']
    if 'reduction' in star:
        cfg['star_reduction'] = star['reduction']
    if 'star_stretch_factor' in star:
        cfg['star_stretch_factor'] = star['star_stretch_factor']
    if star:
        reasons.append(
            f"Stars: threshold={cfg.get('star_threshold')} "
            f"reduction={cfg.get('star_reduction')}"
        )
    star_density = report.get('starfield', {}).get('star_density') if report else None
    if star_density in ('dense', 'very_dense'):
        cfg['prefer_external_starless'] = True
        cfg['star_combine_strength'] = min(
            cfg.get('star_combine_strength', 1.0), 0.82
        )
        reasons.append(
            f"Stars: density={star_density}, prefer external StarNet++ "
            f"and combine_strength={cfg['star_combine_strength']}"
        )

    # ── HDR/增强 ──
    enh = rec.get('enhance', {})
    if 'hdr_strength' in enh:
        cfg['hdr_strength'] = enh['hdr_strength']
        reasons.append(f"HDR: strength={cfg['hdr_strength']}")

    # ── 锐化 ──
    sh = rec.get('sharpen', {})
    if 'amount' in sh:
        cfg['sharpen_amount'] = sh['amount']
        reasons.append(f"Sharpen: amount={cfg['sharpen_amount']}")

    # ── 颜色 ──
    color = rec.get('color', {})
    if 'saturation_factor' in color:
        cfg['saturation'] = color['saturation_factor']
        reasons.append(f"Color: saturation={cfg['saturation']}")
    if color.get('needs_scnr'):
        cfg['star_scnr_strength'] = max(cfg.get('star_scnr_strength', 0.15), 0.25)
        reasons.append("SCNR: 诊断检测绿噪，提高强度")

    # ── 最终降噪 ──
    final = rec.get('final_denoise', {})
    if 'luminance_strength' in final:
        cfg['final_denoise_lum'] = final['luminance_strength']
    if 'chroma_strength' in final:
        cfg['final_denoise_chroma'] = final['chroma_strength']
    if final:
        reasons.append(
            f"Final-denoise: L={cfg.get('final_denoise_lum')} "
            f"C={cfg.get('final_denoise_chroma')}"
        )

    if reasons:
        print(f"  [自适应] 基于诊断报告调整 {len(reasons)} 项参数:")
        for r in reasons:
            print(f"    → {r}")
    else:
        print("  [自适应] 诊断报告未提供可覆盖参数，使用 base_preset")

    return cfg


def _is_star_poi_target(target_type, target_name):
    """
    判断目标是否为"星点即主体"类型，需要禁用去星/缩星。

    包括：球状星团、疏散星团、M45 昴星团。
    """
    if target_type in ('globular_cluster', 'open_cluster'):
        return True
    if target_name:
        name_upper = target_name.upper()
        if name_upper in ('M45', 'PLEIADES', 'PLEIADES CLUSTER'):
            return True
    return False


def _is_emission_nebula_target(target_type, target_name):
    """判断目标是否为发射星云（包括 M42 等典型发射星云）。"""
    if target_type == 'emission_nebula':
        return True
    if target_name:
        name_upper = target_name.upper()
        # 典型发射星云名称清单（可扩展）
        emission_names = (
            'M42', 'ORION NEBULA', 'NGC7000', 'NORTH AMERICA NEBULA',
            'M16', 'EAGLE NEBULA', 'M8', 'LAGOON NEBULA', 'NGC2237',
            'ROSETTE NEBULA', 'NGC6888', 'CRESCENT NEBULA',
        )
        if any(name in name_upper for name in emission_names):
            return True
    return False


def _is_reflection_nebula_target(target_type, target_name):
    """判断目标是否为反射星云。"""
    if target_type == 'reflection_nebula':
        return True
    if target_name:
        name_upper = target_name.upper()
        reflection_names = (
            'M45', 'PLEIADES', 'NGC7023', 'IRIS NEBULA',
            'IC2118', 'WITCH HEAD NEBULA',
        )
        if any(name in name_upper for name in reflection_names):
            return True
    return False


def _is_m42_target(target_name):
    """判断是否为 M42 猎户座大星云（需要核心保护）。"""
    if not target_name:
        return False
    name_upper = target_name.upper()
    return name_upper in ('M42', 'ORION NEBULA', 'GREAT ORION NEBULA')


def apply_target_aware_safety_rules(cfg, steps, target_type, target_name):
    """
    根据天体类型应用安全规则，修改 cfg 和 steps。

    这是将 references/target_awareness.md 中的领域知识落实到代码的关键函数。
    返回 (modified_cfg, modified_steps, safety_log)。
    """
    cfg = dict(cfg)
    steps = list(steps)
    log = []

    # ── 规则 1: 星点即主体 → 绝对禁止去星/缩星 ──
    if _is_star_poi_target(target_type, target_name):
        banned = []
        for step in ('star_remove', 'star_reduce', 'star_process', 'star_combine'):
            if step in steps:
                steps.remove(step)
                banned.append(step)
        if banned:
            log.append(
                f"🛡️ 星团安全: 禁用 {', '.join(banned)} — 星点即目标主体"
            )
        # 球状星团降噪保守
        cfg['pre_denoise_lum'] = min(cfg.get('pre_denoise_lum', 0.02), 0.01)
        cfg['final_denoise_lum'] = min(cfg.get('final_denoise_lum', 0.015), 0.005)
        log.append("🛡️ 星团安全: 降噪强度限制为保守级别")

    # ── 规则 2: M42 核心保护 ──
    if _is_m42_target(target_name):
        # 降低拉伸因子防止核心过曝
        old_stretch = cfg.get('stretch_factor', 45.0)
        cfg['stretch_factor'] = old_stretch * 0.75
        cfg['hdr_strength'] = min(cfg.get('hdr_strength', 0.35) + 0.15, 0.7)
        cfg['target_bg'] = max(cfg.get('target_bg', 0.08), 0.10)
        log.append(
            f"🛡️ M42 核心保护: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} "
            f"(×0.75), HDR+0.15, target_bg≥0.10"
        )

    # ── 规则 3: 发射星云参数修正 ──
    if _is_emission_nebula_target(target_type, target_name):
        # 发射星云拉伸保守
        old_stretch = cfg.get('stretch_factor', 45.0)
        if old_stretch > 30:
            cfg['stretch_factor'] = old_stretch * 0.85
            log.append(
                f"🛡️ 发射星云: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} (×0.85)"
            )
        # 锐化保守
        old_sharpen = cfg.get('sharpen_amount', 1.3)
        if old_sharpen > 0.7:
            cfg['sharpen_amount'] = old_sharpen * 0.7
            log.append(
                f"🛡️ 发射星云: sharpen {old_sharpen:.1f}→{cfg['sharpen_amount']:.1f} (×0.7)"
            )
        # HDR 增强保护核心
        old_hdr = cfg.get('hdr_strength', 0.5)
        if old_hdr < 0.4:
            cfg['hdr_strength'] = min(old_hdr + 0.1, 0.6)
            log.append(
                f"🛡️ 发射星云: HDR {old_hdr:.2f}→{cfg['hdr_strength']:.2f} (+0.1)"
            )

    # ── 规则 4: 反射星云参数修正 ──
    if _is_reflection_nebula_target(target_type, target_name):
        old_stretch = cfg.get('stretch_factor', 45.0)
        cfg['stretch_factor'] = old_stretch * 1.20
        cfg['hdr_strength'] = max(cfg.get('hdr_strength', 0.5) - 0.1, 0.15)
        # 降噪稍强（暗弱信号噪声高）
        cfg['pre_denoise_lum'] = min(cfg.get('pre_denoise_lum', 0.02) * 1.15, 0.06)
        log.append(
            f"🛡️ 反射星云: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} (+20%), "
            f"HDR-0.1, denoise+15%"
        )

    # ── 规则 5: 星系参数修正 ──
    if target_type == 'galaxy':
        old_hdr = cfg.get('hdr_strength', 0.5)
        cfg['hdr_strength'] = min(old_hdr + 0.15, 0.75)
        old_sharpen = cfg.get('sharpen_amount', 1.3)
        if old_sharpen < 1.5:
            cfg['sharpen_amount'] = min(old_sharpen + 0.3, 2.0)
        log.append(
            f"🛡️ 星系: HDR {old_hdr:.2f}→{cfg['hdr_strength']:.2f} (+0.15), "
            f"sharpen 增强"
        )

    # ── 规则 6: 行星状星云 ──
    if target_type == 'planetary_nebula':
        old_hdr = cfg.get('hdr_strength', 0.5)
        cfg['hdr_strength'] = min(old_hdr + 0.15, 0.8)
        old_sharpen = cfg.get('sharpen_amount', 1.3)
        cfg['sharpen_amount'] = min(old_sharpen + 0.4, 2.5)
        log.append(
            f"🛡️ 行星状星云: HDR+0.15, sharpen 增强"
        )

    # ── 规则 7: 暗星云 ──
    if target_type == 'dark_nebula':
        old_stretch = cfg.get('stretch_factor', 45.0)
        cfg['stretch_factor'] = old_stretch * 1.15
        log.append(
            f"🛡️ 暗星云: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} (+15%) "
            f"— 提亮背景以剪影方式呈现暗结构"
        )

    return cfg, steps, log


def run_pipeline(input_path, output_path, steps=None, preset='medium',
                 save_intermediates=False, work_dir=None,
                 recognize=False, recognize_output=None, recognize_input=False,
                 cleanup=False, keep_all=False, tile_size=None,
                 external_starless=None, external_denoised=None,
                 override_params=None,
                 crop=None, target_type=None, color_mode='auto',
                 style='auto', style_strength=1.0,
                 local_center=None, local_radius=None,
                 local_strength=0.30, external_detail_strength=0.75,
                 auto_crop_target=False, auto_crop_padding=2.0,
                 auto_crop_edges=True,
                 analysis_report=None, target_name=None, stretch_method='auto',
                 use_starnet=False, starnet_path=None, starnet_stride=256,
                 starnet_timeout=900,
                 result_json=None, quality_policy='advisory',
                 plate_solve=False, solve_field_path=None,
                 plate_solve_timeout=180, catalog=None,
                 recognition_workflow_dir=None,
                 low_memory=False, auto_low_memory=True,
                 low_memory_threshold_mpix=8.0,
                 reference_image=None, reference_auto_search=False,
                 reference_strength=0.85,
                 reference_match_orientation=False):
    """执行深空后期处理全流程。"""
    started_at = now_iso()
    capture_metadata = {}
    header_to_use = None
    try:
        capture_metadata, header_to_use = read_capture_metadata(input_path)
    except Exception as exc:
        capture_metadata = {"source": "unavailable", "warning": str(exc)}
    physical_priors = build_physical_priors(capture_metadata)

    # ── 智能天体属性识别与自适应数据填充 ──
    if not target_name or not target_type:
        resolved = resolve_celestial_target(header=header_to_use, target_name=target_name)
        if resolved['resolved_type'] != 'unknown_deep_sky':
            if not target_type:
                target_type = resolved['resolved_type']
                print(f"[自适应] 自动识别并设定天体类型: {target_type}")
        if resolved['resolved_name'] != 'unknown':
            if not target_name:
                target_name = resolved['resolved_name']
                print(f"[自适应] 自动识别并设定天体名称: {target_name}")

    # ── 配置初始化 ──
    if preset == 'adaptive' and analysis_report:
        print("[自适应] 使用诊断报告驱动参数...")
        cfg = build_config_from_analysis(
            analysis_report, base_preset='medium', target_type=target_type
        )
    else:
        cfg = dict(STRENGTH_PRESETS[preset])

    # 物理元数据先验低于用户显式覆盖，但高于静态预设。
    for key, value in physical_priors.get("parameter_overrides", {}).items():
        old_value = cfg.get(key)
        cfg[key] = value
        print(f"  [物理先验] {key}: {old_value} → {value}")

    filter_class = (
        capture_metadata.get("filter_profile", {}).get("class")
        if isinstance(capture_metadata, dict) else None
    )
    if color_mode == "auto" and filter_class in ("dual_band", "narrowband"):
        color_mode = "emission"
        print(f"  [物理先验] filter={capture_metadata.get('filter')} → color_mode=emission")

    # AI 自适应参数覆盖：以预设为基础，逐参数替换
    if override_params:
        for key, value in override_params.items():
            old_value = cfg.get(key, 'N/A')
            cfg[key] = value
            if old_value == 'N/A':
                print(f"  [参数覆盖] {key}: 新增 → {value}")
            else:
                print(f"  [参数覆盖] {key}: {old_value} → {value}")

    # ── 天体类型安全规则（将 target_awareness.md 落实到代码） ──
    if steps is None:
        steps = list(EMISSION_STEPS) if preset == 'emission' else list(ALL_STEPS)
        if preset == 'emission' and external_starless:
            steps.insert(steps.index('star_reduce'), 'external_detail')
    else:
        steps = [s.strip() for s in steps.split(',')]
        invalid = [s for s in steps if s not in ALL_STEPS]
        if invalid:
            raise ValueError(
                f"未知步骤: {', '.join(invalid)}; "
                f"可选步骤: {', '.join(ALL_STEPS)}"
            )
        steps, dependency_log = resolve_step_dependencies(steps)

    if steps is not None and 'dependency_log' not in locals():
        dependency_log = []

    if dependency_log:
        print("\n  ── 步骤依赖规则 ──")
        for entry in dependency_log:
            print(f"    {entry}")

    if 'external_detail' in steps and not external_starless:
        steps.remove('external_detail')
        print("    🛡️ 检测到未提供 --external-starless，自动跳过 external_detail")

    cfg, steps, safety_log = apply_target_aware_safety_rules(
        cfg, steps, target_type, target_name
    )
    if safety_log:
        print("\n  ── 天体类型安全规则 ──")
        for entry in safety_log:
            print(f"    {entry}")

    if 'dbe' in steps and str(cfg.get('dbe_method', '')).lower() == 'skip':
        steps.remove('dbe')
        print("    🛡️ 参数覆盖要求 dbe_method=skip，已显式跳过 DBE")

    # 发射星云无显著梯度时，自动跳过 DBE（但保留用户显式指定）
    if ('dbe' in steps and target_type == 'emission_nebula'
            and analysis_report is not None):
        grad = analysis_report.get('gradient', {})
        dbe_decision = grad.get('dbe_decision')
        if dbe_decision == 'review_chromatic':
            steps.remove('dbe')
            print(
                "    🛡️ 发射星云: 检测到通道间低频梯度差异，"
                "自动 DBE 暂停，需通过背景模型差分确认是真实 Hα 还是色偏"
            )
        elif (
            dbe_decision == 'skip'
            or (
                dbe_decision is None
                and (
                    grad.get('gradient_pattern') == 'none'
                    or grad.get('gradient_severity') == 'none'
                )
            )
        ):
            steps.remove('dbe')
            print("    🛡️ 发射星云: 诊断显示无显著梯度，自动跳过 DBE")

    keep_intermediates = bool(save_intermediates or keep_all)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    if work_dir is None:
        work_dir = os.path.expanduser(f'~/.workbuddy/cache/dsp/{timestamp}')
    try:
        os.makedirs(work_dir, exist_ok=True)
    except PermissionError:
        work_dir = os.path.join(tempfile.gettempdir(), 'dsp', timestamp)
        os.makedirs(work_dir, exist_ok=True)
        print(f"[WARN] 默认缓存目录不可写，改用: {work_dir}")
    artifact_paths = {}

    print(f"\n{'='*65}")
    print(f"  深空天文后期处理管线 (Siril+SASP 流程)")
    print(f"  强度预设: {preset}")
    print(f"  执行步骤: {' → '.join(steps)}")
    print(f"{'='*65}\n")

    img, meta = read_image(input_path)
    img = np.asarray(img, dtype=np.float32)
    is_linear_input = meta.get('is_linear', False)
    print(f"[加载] {input_path} → {img.shape}  格式:{meta['format']}  线性:{is_linear_input}")
    if physical_priors.get("evidence"):
        print(f"[物理先验] evidence={physical_priors['evidence']} "
              f"confidence={physical_priors['confidence']}")

    steps, tile_size, memory_report = resolve_memory_plan(
        img.shape,
        steps,
        low_memory=low_memory,
        auto_low_memory=auto_low_memory,
        threshold_mpix=low_memory_threshold_mpix,
        tile_size=tile_size,
        external_starless=bool(external_starless),
    )
    if memory_report["enabled"]:
        print(
            f"[内存模式] {memory_report['input_megapixels']:.2f}MP, "
            f"tile={memory_report['tile_size']}, "
            "决策预览≤1920px，原尺寸执行与输出"
        )
        if memory_report["skipped_steps"]:
            print(
                "  🛡️ 无外部无星层，跳过高内存步骤: "
                + ", ".join(memory_report["skipped_steps"])
            )

    plate_solution = None
    if plate_solve:
        try:
            from plate_solve import solve_image
            plate_solution = solve_image(
                input_path,
                os.path.join(work_dir, "astrometry"),
                solve_field_path=solve_field_path,
                timeout=plate_solve_timeout,
                catalog=catalog,
            )
            print(f"[天区解析] status={plate_solution.get('status')}")
        except Exception as exc:
            plate_solution = {
                "schema_version": "1.0",
                "status": "failed",
                "error": {"code": "PLATE_SOLVE_EXCEPTION", "message": str(exc)},
            }
            print(f"[WARN] 天区解析失败: {exc}")

    # FITS 输入自动增强: 线性数据暗部更密集，适度提升
    if is_linear_input:
        cfg['stretch_factor'] = cfg['stretch_factor'] * 1.5
        cfg['star_stretch_factor'] = cfg['star_stretch_factor'] * 1.5
        print("[线性输入] stretch因子×1.5，星点拉伸×1.5")

    # RGBA 处理
    has_alpha = img.ndim == 3 and img.shape[2] == 4
    alpha_channel = None
    if has_alpha:
        alpha_channel = img[:, :, 3].copy()
        img = img[:, :, :3]
        print(f"[预处理] Alpha通道暂存 → RGB {img.shape}")

    crop_bounds = parse_crop(crop) if isinstance(crop, str) else crop
    if crop_bounds and auto_crop_target:
        raise ValueError("--crop 与 --auto-crop-target 不能同时使用")

    external_full = None
    if external_starless:
        external_full, _external_meta = read_image(
            external_starless,
            force_linear=True,
        )
        external_full = np.asarray(external_full, dtype=np.float32)
        if external_full.shape != img.shape:
            raise ValueError(
                f"外部无星图形状 {external_full.shape} 与原图 {img.shape} 不一致"
            )

    external_denoised_full = None
    if external_denoised:
        external_denoised_full, _ed_meta = read_image(
            external_denoised,
            force_linear=True,
        )
        external_denoised_full = np.asarray(external_denoised_full, dtype=np.float32)
        if external_denoised_full.shape != img.shape:
            raise ValueError(
                f"外部降噪图形状 {external_denoised_full.shape} 与原图 {img.shape} 不一致"
            )
        print(f"[AI桥接] 外部降噪图已加载: {external_denoised}")
        print("  ⚠️ 使用外部 AI 降噪结果替代管线内置降噪，Phase E 需检查 AI 伪细节")

    auto_crop_info = None
    if auto_crop_target:
        crop_source = external_full if external_full is not None else img
        crop_bounds, auto_crop_info = detect_target_crop(
            crop_source,
            padding=auto_crop_padding,
        )
        auto_crop_info['mode'] = 'target'
        print(
            f"[自动定位] center={auto_crop_info['center']} "
            f"object_bbox={auto_crop_info['object_bbox']}"
        )
    elif crop_bounds is None and auto_crop_edges:
        crop_bounds, auto_crop_info = detect_black_edge_crop(img)
        if auto_crop_info['trimmed']:
            print(f"[自动裁边] edges={auto_crop_info['edges']}")
        else:
            crop_bounds = None
            print("[自动裁边] 未检测到黑边，保留完整画幅")

    img, applied_crop = apply_crop(img, crop_bounds)
    if alpha_channel is not None:
        alpha_channel, _ = apply_crop(alpha_channel, crop_bounds)
    if applied_crop:
        print(f"[裁切] x={applied_crop[0]} y={applied_crop[1]} "
              f"width={applied_crop[2]} height={applied_crop[3]}")

    effective_color_mode = color_mode
    effective_target_type = target_type
    if effective_target_type is None and preset == 'emission':
        effective_target_type = 'emission_nebula'
    if effective_color_mode == 'auto':
        effective_color_mode = (
            'emission'
            if effective_target_type == 'emission_nebula' or preset == 'emission'
            else 'standard'
        )

    decision_preview = (
        make_decision_preview(img)
        if memory_report["enabled"]
        else img
    )
    resolved_stretch_method = stretch_method
    if memory_report["enabled"]:
        current = img
        original_linear = img
    else:
        current = img.copy()
        original_linear = img.copy()
    selected_style = None
    color_calibration_report = None
    emission_channel_report = None
    hdr_report = None
    sharpen_report = None
    reference_grade_report = None
    external_starless_linear = None
    if external_full is not None:
        external_starless_linear = external_full
        external_starless_linear, _ = apply_crop(
            external_starless_linear,
            crop_bounds,
        )
        if external_starless_linear.shape != current.shape:
            raise ValueError(
                f"外部无星图形状 {external_starless_linear.shape} "
                f"与当前图像 {current.shape} 不一致"
            )

    def save_artifact(filename, target):
        target = np.clip(np.asarray(target, dtype=np.float32), 0, 1)
        path = os.path.join(work_dir, filename)
        if keep_intermediates:
            write_image(target, path)
            artifact_paths[filename] = path
        return target

    def save_current(filename='', img=None):
        nonlocal current
        target = img if img is not None else current
        return save_artifact(filename, target)

    def tiled(operation, target):
        effective_tile = tile_size
        if effective_tile is None and max(target.shape[:2]) >= 4096:
            effective_tile = 1024
        if not effective_tile or max(target.shape[:2]) <= effective_tile:
            return operation(target)
        overlap = min(32, max(8, effective_tile // 16))
        result = np.zeros_like(target, dtype=np.float32)
        weight = np.zeros(target.shape[:2], dtype=np.float32)
        h, w = target.shape[:2]
        for y in range(0, h, effective_tile):
            for x in range(0, w, effective_tile):
                y0, x0 = max(0, y - overlap), max(0, x - overlap)
                y1 = min(h, y + effective_tile + overlap)
                x1 = min(w, x + effective_tile + overlap)
                processed = operation(target[y0:y1, x0:x1])
                result[y0:y1, x0:x1] += processed
                weight[y0:y1, x0:x1] += 1
        divisor = weight[..., None] if result.ndim == 3 else weight
        return result / np.maximum(divisor, 1)

    # ══════════════════════════════════════════════════════════════
    # 线性阶段 (Linear Phase)
    # ══════════════════════════════════════════════════════════════

    # Phase 1: 背景提取 (DBE)
    if 'dbe' in steps:
        dbe_method = cfg.get('dbe_method', 'polynomial')
        dbe_degree = cfg.get('dbe_degree', 2)
        print("\n─ 线性阶段 ─")
        print(f"[Phase 1] 背景提取 / 梯度去除 (DBE) [{dbe_method}"
              + (f", degree={dbe_degree}" if dbe_method == 'polynomial' else "")
              + "]...")
        current, _ = remove_gradient(
            current, method=dbe_method,
            degree=dbe_degree if dbe_degree else 2
        )
        current = np.clip(current, 0, None)
        # 百分位归一化：避免单通道热像素主导归一化
        p_low = np.percentile(current[current > 0], cfg['dbe_pctl_low']) if np.any(current > 0) else 0
        p_high = np.percentile(current, cfg['dbe_pctl_high'])
        span = p_high - p_low
        if span > 1e-8:
            current = np.clip((current - p_low) / span, 0, 1)
        elif current.max() > 0:
            current = current / current.max()
        save_current('01_dbe.tif')
        print(f"  ✓ 光害梯度已去除 (归一化: p{cfg['dbe_pctl_low']}→p{cfg['dbe_pctl_high']})")

        # CP1: DBE 后强制四角均匀度检查
        _gray_dbe = np.mean(current, axis=2) if current.ndim == 3 else current
        _cs = max(3, min(_gray_dbe.shape[0], _gray_dbe.shape[1]) // 8)
        _corners_dbe = [
            float(np.mean(_gray_dbe[:_cs, :_cs])),
            float(np.mean(_gray_dbe[:_cs, -_cs:])),
            float(np.mean(_gray_dbe[-_cs:, :_cs])),
            float(np.mean(_gray_dbe[-_cs:, -_cs:])),
        ]
        _min_c = max(min(_corners_dbe), 1e-10)
        _corner_ratio = max(_corners_dbe) / _min_c
        _corner_status = '✅' if _corner_ratio < 1.05 else ('⚠️' if _corner_ratio < 3.0 else '❌')
        print(f"  CP1 四角均匀度: {_corner_status} ratio={_corner_ratio:.2f}x "
              f"corners={[round(c, 5) for c in _corners_dbe]}")
        if _corner_ratio >= 3.0:
            print("  ⚠️ DBE 后四角不均匀度 > 3.0x — 可能需要调整 DBE 参数（degree/method）或检查天体是否靠边")

    # 联合中位数、P99 和有效像素比例判断，避免少量亮星或黑边误判。
    decision_source = decision_preview if memory_report["enabled"] else current
    gray = (
        np.mean(decision_source, axis=2)
        if decision_source.ndim == 3 else decision_source
    )
    dark_median = float(np.median(gray))
    dark_p99 = float(np.percentile(gray, 99.0))
    nonzero_fraction = float(np.mean(gray > 0))
    is_very_dark = (
        nonzero_fraction >= 0.05
        and (
            (dark_median < 0.001 and dark_p99 < 0.02)
            or (dark_median < 0.01 and dark_p99 < 0.08)
        )
    )
    if is_very_dark:
        print(
            f"\n[检测] 极暗数据 detected "
            f"(median={dark_median:.6f}, p99={dark_p99:.6f}, "
            f"nonzero={nonzero_fraction:.3f})，启用保色恢复"
        )

    # Phase 2: 颜色校准
    if 'color' in steps:
        if effective_color_mode == 'emission':
            print("\n[Phase 2] 发射星云校色 (暗部基线 + 星色软校准)...")
            current, color_calibration_report = emission_nebula_calibrate(
                current,
                oiii_blue_injection=cfg.get('oiii_blue_injection', 0.0),
                return_report=True,
            )
            print("  ✓ 保留 Hα/OIII 信号比例，不做全图灰度世界白平衡")
        elif is_very_dark:
            print("\n[Phase 2] 颜色校准 (极暗数据：仅轻绿噪去除)...")
            current = remove_green_noise(current, strength=0.12)
            print("  ✓ 仅轻绿噪去除(0.12)")
        else:
            print("\n[Phase 2] 颜色校准 (PCC-like)...")
            current = auto_color_calibrate(current)
            print("  ✓ 背景中性化 + 白平衡 + 绿噪去除")
        save_current('02_color.tif')

    # Phase 3: 初步降噪 (线性数据)
    if 'pre_denoise' in steps:
        if external_denoised_full is not None:
            # 使用外部 AI 降噪结果替代内置降噪
            print("\n[Phase 3] 初步降噪 (使用外部 AI 降噪结果)...")
            ed_cropped, _ = apply_crop(external_denoised_full.copy(), crop_bounds)
            current = np.clip(ed_cropped, 0, 1)
            print("  ✓ 外部 AI 降噪图替代内置降噪 (L1 级别)")
        elif is_very_dark:
            print("\n[Phase 3] 初步降噪 (极暗数据：极轻度，保护微弱星云信号)...")
            if current.ndim == 2:
                current = np.stack([current] * 3, axis=-1)
            current = tiled(
                lambda tile: denoise_luminance_chroma(
                    tile, lum_strength=0.002, chroma_strength=0.005
                ),
                current,
            )
            print("  ✓ L=0.002/C=0.005 (极轻度)")
        else:
            print("\n[Phase 3] 初步降噪 (GXP Silentium-like)...")
            if current.ndim == 2:
                current = np.stack([current] * 3, axis=-1)
            current = tiled(
                lambda tile: denoise_luminance_chroma(
                    tile, lum_strength=cfg['pre_denoise_lum'],
                    chroma_strength=cfg['pre_denoise_chroma']
                ),
                current,
            )
            print(f"  ✓ L={cfg['pre_denoise_lum']}/C={cfg['pre_denoise_chroma']}")
        save_current('03_pre_denoise.tif')

    # Phase 4: 去星 (在线性数据上！)
    starless = None
    stars = None
    star_removal_fallback = False
    valid_starless_layer = False
    linear_star_mask_val = None
    if 'star_remove' in steps:
        print("\n[Phase 4] 去星 (StarNet-like, 线性数据)...")
        if (
            cfg.get('prefer_external_starless')
            and not external_starless
            and not use_starnet
        ):
            print(
                "  ⚠️ 密集星场优先建议 StarNet++ 外部无星层；"
                "当前仅尝试内置方法，失败后将切换带星保护流程"
            )
        if external_starless:
            starless = external_starless_linear
            stars = np.clip(current - starless, 0, 1)
            valid_starless_layer = True
            print(f"  ✓ 使用外部无星图: {external_starless}")
        else:
            star_method = 'starnet' if use_starnet else 'inpaint'
            starless, stars, star_mask, star_report = separate_stars(
                current, method=star_method,
                star_threshold=cfg['star_threshold'],
                inpaint_radius=5,
                return_report=True,
                starnet_path=starnet_path,
                starnet_stride=starnet_stride,
                starnet_timeout=starnet_timeout,
            )
            if star_report.get('fallback_applied'):
                star_removal_fallback = True
                print(
                    f"  ⚠️ 去星已回退: {star_report.get('fallback_reason')}，"
                    "后续流程保留原始星点"
                )
            else:
                valid_starless_layer = True
                print(
                    f"  ✓ 去星质量={star_report.get('repair_quality_score', 1.0):.3f} "
                    f"method={star_report.get('repair_method', 'external')}"
                )
        save_artifact('04_starless_linear.tif', starless)
        save_artifact('04_stars_linear.tif', stars)
        try:
            gray_linear = np.mean(current[..., :3], axis=2) if current.ndim == 3 else current
            fwhm_est = 4.0
            if 'star_report' in locals() and star_report and star_report.get('estimated_fwhm') is not None:
                fwhm_est = star_report.get('estimated_fwhm')
            linear_star_mask_val = detect_stars(gray_linear, star_threshold=cfg['star_threshold'], fwhm=fwhm_est)
            linear_star_area_ratio = float(np.mean(linear_star_mask_val > 0.5))
            linear_star_metrics = {
                'star_area_ratio': linear_star_area_ratio,
                'estimated_fwhm': float(fwhm_est),
                'n_stars_detected': int(star_report.get('n_components_total', 0)) if 'star_report' in locals() and star_report else None,
            }
            print(f"  ✓ 锁定线性阶段星点指标: area_ratio={linear_star_area_ratio:.6f} FWHM={fwhm_est:.2f}px")
        except Exception as e_metric:
            linear_star_metrics = None
            print(f"  [WARN] 无法计算线性阶段星点指标: {e_metric}")
        print(f"  ✓ 阈值={cfg['star_threshold']} (线性数据)")

    # Phase 5: 拉伸 (线性→非线性, 对去星图像)
    stretch_target = starless if starless is not None else current
    if 'stretch' in steps:
        # 1. 自适应确定拉伸方法
        effective_method = stretch_method
        if effective_method == 'auto':
            if effective_color_mode == 'emission' and star_removal_fallback:
                effective_method = 'masked_ghs'
                print(
                    "[自适应] 去星回退且保留密集星场，"
                    "改用 masked_ghs 保护星核与背景"
                )
            elif effective_color_mode == 'emission':
                effective_method = 'emission'
            elif cfg.get('stretch_method') not in (None, 'auto'):
                effective_method = cfg['stretch_method']
            elif is_very_dark:
                effective_method = 'very_dark'
            elif effective_target_type in ('galaxy', 'emission_nebula', 'planetary_nebula'):
                effective_method = 'masked_ghs'
                print(f"[自适应] 识别为高动态范围天体 ({effective_target_type})，自动升级为 masked_ghs 拉伸以保护高光核心")
            else:
                effective_method = 'masked'
        resolved_stretch_method = effective_method

        # 2. 执行拉伸分发
        print(f"\n[Phase 5] 拉伸 (亮度通道 {effective_method} 拉伸)...")
        if effective_method == 'emission':
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='emission',
                shadow_pctl=cfg.get('shadow_pctl', 1.0),
                highlight_pctl=cfg.get('highlight_pctl', 99.94),
                gamma=cfg.get('stretch_gamma', 0.43),
                target_bg=cfg.get('target_bg', 0.08),
                min_p99=cfg.get('stretch_min_p99', 0.5),
            )
            print(
                f"  ✓ Emission stretch shadow={cfg.get('shadow_pctl', 1.0)} "
                f"highlight={cfg.get('highlight_pctl', 99.94)} "
                f"gamma={cfg.get('stretch_gamma', 0.43)} "
                f"target_bg={cfg.get('target_bg', 0.08)} "
                f"min_p99={cfg.get('stretch_min_p99', 0.5)}"
            )
        elif effective_method == 'very_dark':
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='very_dark',
                factor=cfg.get('stretch_factor', 25.0),
                gamma=cfg.get('stretch_gamma', 0.45),
                shadow_pctl=cfg.get('shadow_pctl', 0.1),
                highlight_pctl=cfg.get('highlight_pctl', 99.5),
                target_bg=cfg.get('target_bg', 0.12),
                min_p99=cfg.get('stretch_min_p99', 0.5),
            )
            print(
                f"  ✓ Very-dark stretch factor={cfg.get('stretch_factor', 25.0)} "
                f"gamma={cfg.get('stretch_gamma', 0.45)} "
                f"target_bg={cfg.get('target_bg', 0.12)}"
            )
        elif effective_method == 'deep':
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='deep',
                shadow_pctl=cfg.get('shadow_pctl', 2.0),
                highlight_pctl=cfg.get('highlight_pctl', 99.9),
                gamma=cfg.get('stretch_gamma', 0.35),
            )
            print(f"  ✓ Deep stretch shadow={cfg.get('shadow_pctl', 2.0)} highlight={cfg.get('highlight_pctl', 99.9)} gamma={cfg.get('stretch_gamma', 0.35)}")
        elif effective_method == 'ghs':
            sp_val = cfg.get('ghs_sp', 0.01)
            b_val = resolve_ghs_b(cfg)
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='ghs',
                sp=sp_val,
                b=b_val,
                c=cfg.get('ghs_c', 0.0)
            )
            print(f"  ✓ GHS stretch sp={sp_val} b={b_val:.2f} c={cfg.get('ghs_c', 0.0)}")
        elif effective_method == 'masked_ghs':
            sp_val = cfg.get('ghs_sp', -1)
            b_val = resolve_ghs_b(cfg)
            prot_val = cfg.get('ghs_protect_strength', 0.5)
            if target_name and 'M42' in str(target_name).upper():
                prot_val = max(prot_val, 0.75)
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='masked_ghs',
                sp=sp_val,
                b=b_val,
                protect_strength=prot_val,
                target_bg=cfg.get('target_bg', 0.08),
                shadow_pctl=cfg.get('shadow_pctl', 0.0),
                highlight_pctl=cfg.get('highlight_pctl', 99.9),
                gamma=cfg.get('stretch_gamma', 0.45),
            )
            print(
                f"  ✓ Masked GHS stretch sp={sp_val} b={b_val:.2f} "
                f"protect={prot_val} target_bg={cfg.get('target_bg', 0.08)} "
                f"shadow={cfg.get('shadow_pctl', 0.0)} "
                f"highlight={cfg.get('highlight_pctl', 99.9)} "
                f"gamma={cfg.get('stretch_gamma', 0.45)}"
            )
        else:
            method_kwargs = {}
            if effective_method == 'masked':
                method_kwargs = {'factor': cfg['stretch_factor'], 'target_bg': cfg.get('target_bg', 0.08)}
            elif effective_method == 'mtf':
                method_kwargs = {'midtones': cfg.get('midtones', 0.3)}
            elif effective_method == 'arcsinh':
                method_kwargs = {'factor': cfg['stretch_factor']}
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method=effective_method,
                **method_kwargs
            )
            print(f"  ✓ {effective_method} stretch kwargs={method_kwargs}")

        current = stretch_target
        save_current('05_stretched_starless.tif')

    # ══════════════════════════════════════════════════════════════
    # 星点独立处理 (Star Processing, 并行)
    # ══════════════════════════════════════════════════════════════

    processed_stars = stars
    if 'star_process' in steps and stars is not None and stars.max() > 0:
        print("\n─ 星点独立处理 ─")
        # S1: 星点拉伸
        print("[Star S1] 星点拉伸 (Star Stretch)...")
        processed_stars = arcsinh_stretch(
            processed_stars, factor=cfg['star_stretch_factor']
        )
        save_artifact('05a_star_stretch.tif', processed_stars)
        print(f"  ✓ factor={cfg['star_stretch_factor']}")

        # S2: 星点去紫 (SCNR)
        print("\n[Star S2] 星点去紫 (SCNR)...")
        processed_stars = remove_green_noise(
            processed_stars, strength=cfg['star_scnr_strength']
        )
        save_artifact('05b_star_scnr.tif', processed_stars)
        print(f"  ✓ strength={cfg['star_scnr_strength']}")

        # S3: 星点曲线微调 + 缩星
        print("\n[Star S3] 星点曲线微调 + 缩星...")
        if processed_stars.ndim >= 3:
            processed_stars = apply_curves(
                processed_stars, midtones=cfg['star_curves_midtones'],
                shadows=0.95, highlights=1.0
            )
        processed_stars = reduce_stars(
            processed_stars, reduction=cfg['star_reduction']
        )
        save_artifact('05c_star_final.tif', processed_stars)
        print(f"  ✓ 缩星reduction={cfg['star_reduction']}")

    # ══════════════════════════════════════════════════════════════
    # 非线性阶段 (Non-Linear Phase)
    # ══════════════════════════════════════════════════════════════

    current_nl = stretch_target if 'stretch' in steps else current

    # Phase 6: 星云细节增强
    if 'enhance' in steps:
        print("\n─ 非线性阶段 ─")
        print("[Phase 6] 星云细节增强 (高光保护 HDR + CLAHE 局部对比)...")
        from enhance import apply_clahe
        hdr_strength = cfg.get('hdr_strength', 0.35)
        current_nl, hdr_report = protected_hdr_compress(
            current_nl,
            strength=hdr_strength,
            knee_percentile=cfg.get('hdr_knee_percentile', 85.0),
        )
        # 按图像短边的 6% 计算 CLAHE kernel，避免固定像素值导致尺度不匹配
        h, w = current_nl.shape[:2]
        kernel_size = int(min(h, w) * 0.06)
        kernel_size = max(64, kernel_size - (kernel_size % 2))  # 确保偶数且 >=64
        # 发射星云使用更低的 clip_limit 避免弥散区域伪影
        clip_limit = 0.003 if effective_color_mode == 'emission' else 0.004
        current_nl = apply_clahe(current_nl, clip_limit=clip_limit, kernel_size=kernel_size)
        current = current_nl
        save_current('06_clahe_enhance.tif')
        print(
            f"  ✓ HDR strength={hdr_strength} "
            f"knee={hdr_report['knee']:.3f} "
            f"P99 {hdr_report['p99_before']:.3f}→{hdr_report['p99_after']:.3f}; "
            f"CLAHE clip={clip_limit}, kernel={kernel_size}"
        )

    # Phase 7: 锐化
    if 'sharpen' in steps:
        print("\n[Phase 7] 信号蒙版锐化 (FWHM-aware)...")
        sharpen_amount = cfg.get('sharpen_amount', 0.7)
        sharpen_fwhm = None
        if 'linear_star_metrics' in locals() and linear_star_metrics:
            sharpen_fwhm = linear_star_metrics.get('estimated_fwhm')
        if sharpen_fwhm is None:
            try:
                fwhm_source = (
                    decision_preview
                    if memory_report["enabled"]
                    else original_linear
                )
                sharpen_gray = (
                    np.mean(fwhm_source[..., :3], axis=2)
                    if fwhm_source.ndim == 3 else fwhm_source
                )
                sharpen_fwhm, _, _, _ = estimate_fwhm(sharpen_gray)
            except Exception:
                sharpen_fwhm = 3.0
        current_nl, sharpen_report = adaptive_signal_sharpen(
            current_nl,
            amount=sharpen_amount,
            fwhm=sharpen_fwhm or 3.0,
            background_percentile=cfg.get(
                'sharpen_background_percentile',
                40.0,
            ),
            star_protection=cfg.get('sharpen_star_protection', 0.75),
        )
        current = current_nl
        save_current('07_sharpen.tif')
        print(
            f"  ✓ amount={sharpen_amount} "
            f"FWHM={sharpen_report['fwhm']:.2f}px "
            f"radius={sharpen_report['radius']:.2f}px "
            f"effective={sharpen_report['effective_pixel_ratio']:.1%}"
        )

    # Phase 8: 颜色调整 (Vectra-like)
    if 'final_color' in steps:
        print("\n[Phase 8] 颜色调整 (Vectra-like)...")
        if current_nl.ndim == 2:
            current_nl = np.stack([current_nl] * 3, axis=-1)
        if effective_color_mode == 'emission':
            target_ratios = None
            if cfg.get('emission_target_r_over_g') or cfg.get('emission_target_r_over_b'):
                target_ratios = {
                    "r_over_g": cfg.get('emission_target_r_over_g'),
                    "r_over_b": cfg.get('emission_target_r_over_b'),
                }
            current_nl, emission_channel_report = stabilize_emission_channels(
                current_nl,
                collapse_ratio=cfg.get('emission_collapse_ratio', 0.02),
                max_gain=cfg.get('emission_channel_max_gain', 1.35),
                strength=cfg.get('emission_channel_recovery_strength', 0.6),
                target_ratios=target_ratios,
            )
            if emission_channel_report.get("applied"):
                print(
                    "  ✓ 星云通道恢复: gains="
                    f"{np.round(emission_channel_report['gains'], 3).tolist()}"
                )
            else:
                print(
                    "  ✓ 星云通道检查: "
                    f"{emission_channel_report.get('reason')}"
                )
            current_nl = enhance_saturation(
                current_nl,
                factor=cfg.get('saturation', 1.85),
                protect_background=True,
                bg_protection_percentile=40,
            )
            # 非线性阶段温和去绿噪 (SCNR)，避开线性数据下溢不稳定性
            pre_scnr = current_nl.copy()
            r0, g0, b0 = (
                pre_scnr[..., 0],
                pre_scnr[..., 1],
                pre_scnr[..., 2],
            )
            signal_floor = np.percentile(np.mean(pre_scnr, axis=2), 55)
            oiii_mask = (
                (np.mean(pre_scnr, axis=2) > signal_floor)
                & (g0 > r0 * 1.03)
                & (b0 > r0 * 0.90)
                & (np.minimum(g0, b0) > 0.015)
            )
            oiii_soft = gaussian_filter(oiii_mask.astype(np.float32), sigma=2.0)
            current_nl = remove_green_noise(current_nl, strength=0.15)
            current_nl = (
                current_nl * (1.0 - oiii_soft[..., None] * 0.85)
                + pre_scnr * (oiii_soft[..., None] * 0.85)
            )
            # Hα 品红警戒：防止 B 通道过高导致品红偏移
            b_ch = current_nl[..., 2]
            g_ch = current_nl[..., 1]
            r_ch = current_nl[..., 0]
            signal = np.mean(current_nl, axis=2)
            magenta_mask = (
                (signal > np.percentile(signal, 45))
                & (b_ch > g_ch * 1.35)
                & (r_ch > g_ch * 1.20)
                & (r_ch > b_ch * 0.75)
                & (oiii_soft < 0.2)
            )
            if magenta_mask.any():
                excess = b_ch - g_ch * 1.25
                current_nl[..., 2] = np.where(
                    magenta_mask,
                    np.clip(g_ch * 1.25 + excess * 0.3, 0, 1),
                    b_ch
                )
                mag_pct = magenta_mask.sum() / magenta_mask.size * 100
                print(f"  ✓ Hα品红警戒: {mag_pct:.1f}%像素压蓝")
            print(
                f"  ✓ OIII青色保护: "
                f"{float(np.mean(oiii_mask)) * 100:.1f}%像素免受强SCNR/压蓝"
            )
            current_nl = apply_curves(
                current_nl, midtones=1.08, shadows=0.75, highlights=1.0
            )
            print(f"  ✓ 发射结构选择性饱和×{cfg.get('saturation', 1.85)}")
        else:
            current_nl = apply_curves(current_nl, midtones=1.03, shadows=0.75)
            print("  ✓ 中性曲线调整")
        current = current_nl
        save_current('08_color.tif')
        print("  ✓ 背景保护曲线完成")

    # Phase 8b: 中央眉月星云局部对比度和纹理增强
    if 'local_enhance' in steps:
        print("\n[Phase 8b] 中央眉月星云局部对比度和纹理增强...")
        parsed_center = (
            parse_point(local_center) if isinstance(local_center, str)
            else local_center
        )
        if parsed_center is None:
            detail_source = (
                external_starless_linear
                if external_starless_linear is not None
                else original_linear
            )
            try:
                _target_crop, target_info = detect_target_crop(detail_source)
                nebula_cx = int(round(target_info['center'][0]))
                nebula_cy = int(round(target_info['center'][1]))
                object_width = target_info['object_bbox'][2]
                object_height = target_info['object_bbox'][3]
                detected_radius = int(max(object_width, object_height) * 1.45)
                print(
                    f"  自动增强定位 center=({nebula_cx},{nebula_cy}) "
                    f"object_bbox={target_info['object_bbox']}"
                )
            except ValueError:
                nebula_cx = current_nl.shape[1] // 2
                nebula_cy = current_nl.shape[0] // 2
                detected_radius = int(min(current_nl.shape[:2]) * 0.38)
        else:
            nebula_cx, nebula_cy = parsed_center
            detected_radius = int(min(current_nl.shape[:2]) * 0.38)
        radius = local_radius or detected_radius
        local_star_mask = None
        if not valid_starless_layer:
            if linear_star_mask_val is None:
                gray_linear = (
                    np.mean(original_linear[..., :3], axis=2)
                    if original_linear.ndim == 3 else original_linear
                )
                linear_star_mask_val = detect_stars(
                    gray_linear, star_threshold=cfg['star_threshold']
                )
            local_star_mask = linear_star_mask_val
        current_nl = local_nebula_enhance(
            current_nl,
            center_y=nebula_cy, center_x=nebula_cx,
            radius=radius, strength=local_strength,
            star_mask=local_star_mask,
        )
        current = current_nl
        save_current('08b_local_enhance.tif')
        print(f"  ✓ 局部增强 center=({nebula_cx},{nebula_cy}) "
              f"radius={radius} strength={local_strength}")

    if 'external_detail' in steps:
        if external_starless_linear is None:
            raise ValueError("external_detail 步骤需要 --external-starless")
        print("\n[Phase 8c] 外部无星层正向结构增强...")
        current_nl = positive_starless_detail_enhance(
            current_nl,
            original_linear=original_linear,
            starless_linear=external_starless_linear,
            strength=external_detail_strength,
            full_frame=not auto_crop_target,
        )
        current = current_nl
        save_current('08c_external_detail.tif')
        print(f"  ✓ 仅正向细节 strength={external_detail_strength}")

    # Phase 8c: 轻微缩星并恢复蓝白星色
    if 'star_reduce' in steps:
        print("\n[Phase 8d] 轻微缩星...")
        if linear_star_mask_val is None:
            gray_linear = (
                np.mean(original_linear[..., :3], axis=2)
                if original_linear.ndim == 3 else original_linear
            )
            linear_star_mask_val = detect_stars(
                gray_linear, star_threshold=cfg['star_threshold']
            )
        current_nl = mild_star_reduce_full(
            current_nl,
            reduction=cfg.get('star_reduction', 0.18),
            color_restore=effective_color_mode != 'emission',
            star_mask=linear_star_mask_val,
        )
        current = current_nl
        save_current('08d_star_reduced.tif')
        print(f"  ✓ 缩星(reduction={cfg.get('star_reduction', 0.18)})")

    # ══════════════════════════════════════════════════════════════
    # 最终阶段 (Final Phase)
    # ══════════════════════════════════════════════════════════════

    # Phase 9: 星点合成 (StarComposer)
    if 'star_combine' in steps and processed_stars is not None:
        print("\n─ 最终阶段 ─")
        print("[Phase 9] 星点合成 (StarComposer)...")
        current = combine_starless_stars(
            current_nl, processed_stars,
            star_strength=cfg['star_combine_strength']
        )
        save_current('09_star_combined.tif')
        print(f"  ✓ 星点强度={cfg['star_combine_strength']}")
    else:
        current = current_nl

    if 'style' in steps:
        print("\n[Phase 9a] AI 风格定调 (非生成式)...")
        effective_style_strength = cfg.get('style_strength', style_strength)
        current, selected_style, _style_reasoning = apply_professional_style(
            current,
            style=style,
            target_type=effective_target_type,
            color_mode=effective_color_mode,
            strength=effective_style_strength,
        )
        current_nl = current
        save_current('09a_style.tif')
        print(f"  ✓ style={selected_style} strength={effective_style_strength}")

    # Phase 10: 最终降噪 (SCUNet-like, 必须最后)
    if 'final_denoise' in steps:
        print("\n[Phase 10] 最终降噪 (SCUNet-like, 整体)...")
        if current.ndim == 2:
            current = np.stack([current] * 3, axis=-1)
        current = tiled(
            lambda tile: denoise_luminance_chroma(
                tile, lum_strength=cfg['final_denoise_lum'],
                chroma_strength=cfg['final_denoise_chroma']
            ),
            current,
        )
        save_current('10_final_denoise.tif')
        print(f"  ✓ Final-Denoise L={cfg['final_denoise_lum']}/C={cfg['final_denoise_chroma']}")

    if reference_image:
        print("\n[Phase 11] 参考图全局定调...")
        reference, _reference_meta = read_image(reference_image)
        reference = np.asarray(reference, dtype=np.float32)
        grade_kwargs = {
            "strength": float(np.clip(reference_strength, 0.0, 1.0)),
            "max_color_gain": cfg.get("reference_max_color_gain", 1.25),
            "max_saturation": cfg.get("reference_max_saturation", 1.45),
            "local_contrast": cfg.get("reference_local_contrast", 0.10),
        }
        if reference_auto_search:
            current, reference_grade_report = optimize_reference_grade(
                current,
                reference,
                preview_size=cfg.get("reference_preview_size", 640),
                **grade_kwargs,
            )
            print(
                "  ✓ 自动搜索 "
                f"{reference_grade_report['evaluated_candidates']} 个候选，"
                f"score={reference_grade_report['final_global_score']:.5f}"
            )
        else:
            current, reference_grade_report = match_reference_grade(
                current,
                reference,
                **grade_kwargs,
            )
            print("  ✓ 全局亮度/色调/饱和度匹配完成")
        orientation_changed = False
        if reference_match_orientation:
            source_landscape = current.shape[1] >= current.shape[0]
            reference_landscape = reference.shape[1] >= reference.shape[0]
            if source_landscape != reference_landscape:
                current = np.rot90(current, k=3)
                orientation_changed = True
        reference_grade_report["orientation_changed"] = orientation_changed
        reference_grade_report["reference_image"] = reference_image
        save_current('11_reference_grade.tif', img=current)

    # 最终输出
    current = np.clip(current, 0, 1)

    if has_alpha:
        current = np.dstack([current, alpha_channel])

    write_image(
        current, output_path,
        fits_header=meta.get('header') if is_linear_input else None,
        data_scale=meta.get('data_scale') if is_linear_input else None,
        data_offset=meta.get('data_offset') if is_linear_input else None,
    )

    output_metrics = calculate_metrics(
        current,
        {
            'processing_stage': 'final',
            'linear_star_metrics':
                linear_star_metrics if 'linear_star_metrics' in locals() else None
        },
    )
    quality_status, quality_gates = evaluate_quality_gates(
        output_metrics,
        target_type=effective_target_type,
        steps=steps,
    )
    recognition_ok = run_optional_recognition(
        input_path=input_path,
        output_path=output_path,
        recognize=recognize,
        recognize_output=recognize_output,
        recognize_input=recognize_input,
        workflow_dir=recognition_workflow_dir,
        analysis_report=analysis_report,
        plate_solution=plate_solution,
    )
    recognition_review_pending = bool(
        recognize
        and Path(input_path).suffix.lower() in ASTRO_INPUT_EXTENSIONS
        and recognition_ok
    )
    warnings = [
        {
            "code": gate["code"],
            "message": gate["message"],
        }
        for gate in quality_gates
    ]
    if recognize and not recognition_ok:
        warnings.append({
            "code": "RECOGNITION_FAILED",
            "message": "识别阶段失败，图像输出已保留",
        })
    if recognition_review_pending:
        warnings.append({
            "code": "AI_VISUAL_REVIEW_PENDING",
            "message": (
                "FITS/XISF 安全预览与识别证据包已生成，"
                "等待 AI 或人工视觉审查回填"
            ),
        })
    status = quality_status
    if recognize and not recognition_ok and status == "success":
        status = "partial_success"
    if recognition_review_pending and status == "success":
        status = "partial_success"
    result = {
        "schema_version": AGENT_SCHEMA_VERSION,
        "status": status,
        "started_at": started_at,
        "finished_at": now_iso(),
        "input": input_path,
        "outputs": {
            "image": output_path,
            "work_dir": work_dir if keep_intermediates else None,
            "recognition": (
                recognize_output or default_recognition_output(output_path)
                if recognize and recognition_ok else None
            ),
        },
        "effective_config": {
            "preset": preset,
            "steps": steps,
            "params": cfg,
            "target_type": effective_target_type,
            "target_name": target_name,
            "color_mode": effective_color_mode,
            "stretch_method": resolved_stretch_method,
            "style": selected_style,
            "style_strength": cfg.get("style_strength", style_strength),
        },
        "capture_metadata": capture_metadata,
        "physical_priors": physical_priors,
        "astrometry": plate_solution,
        "metrics": output_metrics,
        "color_calibration": color_calibration_report,
        "emission_channel_recovery": emission_channel_report,
        "hdr": hdr_report,
        "sharpen": sharpen_report,
        "memory": memory_report,
        "star_removal": (
            star_report if 'star_report' in locals() else None
        ),
        "reference_grade": reference_grade_report,
        "quality_policy": quality_policy,
        "quality_gates": quality_gates,
        "warnings": warnings,
        "errors": [],
        "safety_rules_applied": safety_log,
        "step_dependencies_applied": dependency_log,
        "artifacts": artifact_paths,
    }

    if keep_intermediates:
        manifest_path = os.path.join(work_dir, 'manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as handle:
            json.dump({
                'schema_version': AGENT_SCHEMA_VERSION,
                'status': status,
                'input': input_path,
                'output': output_path,
                'work_dir': work_dir,
                'crop': applied_crop,
                'auto_crop': auto_crop_info,
                'target_type': effective_target_type,
                'target_name': target_name,
                'color_mode': effective_color_mode,
                'stretch_method': resolved_stretch_method,
                'style': selected_style,
                'style_strength': cfg.get('style_strength', style_strength),
                'artifacts': artifact_paths,
                'safety_rules_applied': safety_log,
                'step_dependencies_applied': dependency_log,
                'linear_star_metrics': linear_star_metrics if 'linear_star_metrics' in locals() else None,
                'ai_tools_used': {
                    'external_starless': bool(external_starless),
                    'external_denoised': bool(external_denoised),
                    'ai_artifact_check_required': bool(external_starless or external_denoised),
                },
                'metrics': output_metrics,
                'color_calibration': color_calibration_report,
                'emission_channel_recovery': emission_channel_report,
                'hdr': hdr_report,
                'sharpen': sharpen_report,
                'memory': memory_report,
                'star_removal': (
                    star_report if 'star_report' in locals() else None
                ),
                'reference_grade': reference_grade_report,
                'quality_gates': quality_gates,
                'warnings': warnings,
                'effective_config': result['effective_config'],
                'capture_metadata': capture_metadata,
                'physical_priors': physical_priors,
                'astrometry': plate_solution,
            }, handle, indent=2, ensure_ascii=False)
        result["outputs"]["manifest"] = manifest_path
    else:
        result["outputs"]["manifest"] = None

    if result_json:
        result["outputs"]["result_json"] = result_json
        write_result_json_atomic(result, result_json)
    else:
        result["outputs"]["result_json"] = None

    print(f"\n{'='*65}")
    marker = "✅" if status == "success" else "⚠️"
    print(f"  {marker} 管线状态: {status}  输出: {output_path}")
    if keep_intermediates and work_dir:
        print(f"  📁 中间结果: {work_dir}/")
    print(f"{'='*65}\n")
    if cleanup or not keep_intermediates:
        shutil.rmtree(work_dir, ignore_errors=True)
    return result


def main():
    p = argparse.ArgumentParser(
        description='深空天文摄影后期处理全流程 (Siril+SASP流程)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s M42_Stacked.fit M42_proc.jpg        # FITS 输入→JPG 输出
  %(prog)s M42.fit M42_proc.fit --strength strong  # FITS→FITS
  %(prog)s image.jpg output.jpg --save-intermediates
  %(prog)s image.fit output.jpg --steps dbe,color,star_remove,stretch
        """)
    p.add_argument('input', help='输入图像路径 (FITS/XISF/TIFF/PNG/JPG)')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--strength', default='medium',
                   choices=['light', 'medium', 'strong', 'adaptive', 'emission'],
                   help='处理强度预设 (默认: medium)')
    p.add_argument('--stretch-method', default='auto',
                   choices=['auto', 'arcsinh', 'mtf', 'masked', 'deep',
                            'very_dark', 'emission', 'ghs', 'masked_ghs'],
                   help='拉伸方法 (默认: auto，基于目标类型自适应升级为 masked_ghs)')
    p.add_argument('--steps', default=None,
                   help=f'指定步骤,逗号分隔. 可选: {",".join(ALL_STEPS)}')
    p.add_argument('--save-intermediates', action='store_true',
                   help='保存全部中间结果（兼容参数，等价于 --keep-all）')
    p.add_argument('--work-dir', default=None, help='中间结果目录')
    lifecycle = p.add_mutually_exclusive_group()
    lifecycle.add_argument('--cleanup', action='store_true',
                           help='成功后删除中间产物，仅保留最终输出')
    lifecycle.add_argument('--keep-all', action='store_true',
                           help='保留所有 TIFF 中间产物和 manifest.json')
    p.add_argument('--tile-size', type=int, default=None,
                   help='分块处理尺寸；大于等于4096像素的图像默认使用1024')
    memory_group = p.add_mutually_exclusive_group()
    memory_group.add_argument(
        '--low-memory',
        action='store_true',
        help='强制低内存模式：缩略图决策、分块降噪、跳过内置去星链',
    )
    memory_group.add_argument(
        '--no-auto-low-memory',
        action='store_true',
        help='关闭大图自动低内存模式',
    )
    p.add_argument(
        '--low-memory-threshold-mpix',
        type=float,
        default=8.0,
        help='自动低内存模式阈值，单位百万像素（默认: 8）',
    )
    p.add_argument('--external-starless', default=None,
                   help='外部工具生成的无星图，如 StarNet++ starless.tif')
    p.add_argument('--use-starnet', action='store_true',
                   help='使用 StarNet2 CLI 进行 AI 去星（需要本地已部署二进制）')
    p.add_argument('--starnet-path', default=None,
                   help='StarNet2 CLI (starnet++) 可执行二进制文件的绝对路径')
    p.add_argument('--starnet-stride', type=int, default=256,
                   help='StarNet2 步长，较小的值去星更精细但耗时长，例如 128 或 64 (默认: 256)')
    p.add_argument('--starnet-timeout', type=int, default=900,
                   help='StarNet2 执行超时秒数（默认: 900）')
    p.add_argument('--external-denoised', default=None,
                   help='外部 AI 降噪工具预处理后的图像，如 NoiseXTerminator 输出 (L1 级别)')
    p.add_argument('--override-params', default=None,
                   help='AI 自适应参数覆盖，JSON 格式。例: \'{"stretch_factor":54.0,"pre_denoise_lum":0.005}\'')
    p.add_argument('--external-detail-strength', type=float, default=0.75,
                   help='external_detail 正向结构增强强度')
    p.add_argument('--crop', default=None,
                   help='处理前裁切，格式 x,y,width,height')
    edge_crop = p.add_mutually_exclusive_group()
    edge_crop.add_argument('--auto-crop-edges', dest='auto_crop_edges',
                           action='store_true', default=True,
                           help='自动裁掉无效黑边并保留完整画幅（默认）')
    edge_crop.add_argument('--no-auto-crop-edges', dest='auto_crop_edges',
                           action='store_false',
                           help='关闭自动黑边裁切')
    p.add_argument('--auto-crop-target', action='store_true',
                   help='明确需要主体特写时自动定位并裁切')
    p.add_argument('--auto-crop-padding', type=float, default=2.0,
                   help='自动裁切边距，相对目标包围盒倍数')
    p.add_argument('--target-type', default=None,
                   choices=['emission_nebula', 'reflection_nebula', 'galaxy',
                            'globular_cluster', 'open_cluster',
                            'planetary_nebula', 'dark_nebula', 'wide_field'])
    p.add_argument('--target-name', default=None,
                   help='目标天体名称（如 M42、M45、NGC6888），用于激活特定安全规则')
    p.add_argument('--analysis-report', default=None,
                   help='analyze.py 生成的 JSON 诊断报告路径，adaptive 预设时自动读取')
    p.add_argument('--color-mode', default='auto',
                   choices=['auto', 'standard', 'emission'])
    p.add_argument('--style', default='auto',
                   choices=['auto', *STYLE_PROFILES.keys()],
                   help='非生成式专业风格定调，auto 根据目标类型选择')
    p.add_argument('--style-strength', type=float, default=1.0,
                   help='风格定调强度，0=关闭效果，1=默认，建议不超过1.2')
    p.add_argument('--local-center', default=None,
                   help='局部增强中心，格式 x,y；默认裁切后画面中心')
    p.add_argument('--local-radius', type=int, default=None)
    p.add_argument('--local-strength', type=float, default=0.30)
    p.add_argument('--recognize', action='store_true',
                   help='处理完成后生成 AI 视觉识别 JSON')
    p.add_argument('--recognize-output', default=None,
                   help='识别 JSON 输出路径，默认 output.recognition.json')
    p.add_argument('--recognize-input', action='store_true',
                   help='同时识别原图并生成输入/最终图对比 JSON')
    p.add_argument(
        '--recognition-workflow-dir',
        default=None,
        help='FITS/XISF 混合识别工作流目录；默认位于 recognition JSON 旁',
    )
    p.add_argument('--result-json', default=None,
                   help='写入统一机器结果 JSON')
    p.add_argument('--quality-policy', default='advisory',
                   choices=['advisory', 'strict'],
                   help='质量门禁策略；strict 仍保留产物但状态标记为 review_required')
    p.add_argument('--plate-solve', action='store_true',
                   help='使用本地 Astrometry.net solve-field 求解 WCS')
    p.add_argument('--solve-field-path', default=None,
                   help='solve-field 可执行文件路径')
    p.add_argument('--plate-solve-timeout', type=int, default=180)
    p.add_argument('--catalog-json', default=None,
                   help='可选天体星表 JSON，含 name/ra_deg/dec_deg，用 WCS 投影到画面')
    p.add_argument('--reference-image', default=None,
                   help='可选本地参考成片；仅匹配全局亮度、色调和饱和度')
    p.add_argument('--reference-auto-search', action='store_true',
                   help='在缩略图上自动搜索受约束参考图参数')
    p.add_argument('--reference-strength', type=float, default=0.85,
                   help='参考图全局定调强度（默认: 0.85）')
    p.add_argument('--reference-match-orientation', action='store_true',
                   help='参考图横竖方向不同时旋转最终输出')
    args = p.parse_args()

    if not os.path.exists(args.input):
        print(f"[ERROR] 输入文件不存在: {args.input}")
        sys.exit(1)

    # 解析 --override-params JSON
    override_params = None
    if args.override_params:
        try:
            override_params = json.loads(args.override_params)
            if not isinstance(override_params, dict):
                print(f"[ERROR] --override-params 必须是 JSON 对象，收到: {type(override_params)}")
                sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"[ERROR] --override-params JSON 解析失败: {e}")
            sys.exit(1)

    # 加载诊断报告（adaptive 预设或显式指定时）
    analysis_report = None
    if args.analysis_report:
        analysis_report = load_analysis_report(args.analysis_report)
    elif args.strength == 'adaptive':
        # adaptive 预设但未提供报告：尝试自动运行 analyze.py
        auto_report_path = os.path.join(
            tempfile.gettempdir(),
            f"dsp_auto_analysis_{os.getpid()}.json"
        )
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), 'analyze.py'),
                 args.input, '--output', auto_report_path],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode == 0:
                analysis_report = load_analysis_report(auto_report_path)
                if analysis_report:
                    print(f"[自适应] 自动诊断完成: {auto_report_path}")
            else:
                print(f"[WARN] 自动诊断失败: {result.stderr.strip()}")
        except Exception as exc:
            print(f"[WARN] 自动诊断异常: {exc}")

    # 从 FITS header 自动推断 target_name（用户未指定时）
    target_name = args.target_name
    if not target_name and args.input.lower().endswith(('.fit', '.fits', '.fts')):
        try:
            from astropy.io import fits
            with fits.open(args.input) as hdul:
                header = hdul[0].header
                target_name = header.get('OBJECT') or header.get('TARGNAME')
                if target_name:
                    print(f"[信息] 从 FITS header 读取目标名称: {target_name}")
        except Exception:
            pass

    catalog = None
    if args.catalog_json:
        try:
            with open(args.catalog_json, "r", encoding="utf-8") as handle:
                catalog = json.load(handle)
            if not isinstance(catalog, list):
                raise ValueError("catalog JSON must be an array")
        except Exception as exc:
            print(f"[ERROR] 无法读取 catalog JSON: {exc}", file=sys.stderr)
            sys.exit(1)

    result = run_pipeline(
        args.input, args.output, steps=args.steps,
        preset=args.strength,
        save_intermediates=args.save_intermediates,
        work_dir=args.work_dir,
        recognize=args.recognize,
        recognize_output=args.recognize_output,
        recognize_input=args.recognize_input,
        cleanup=args.cleanup,
        keep_all=args.keep_all,
        tile_size=args.tile_size,
        external_starless=args.external_starless,
        external_denoised=args.external_denoised,
        override_params=override_params,
        crop=args.crop,
        target_type=args.target_type,
        color_mode=args.color_mode,
        style=args.style,
        style_strength=args.style_strength,
        local_center=args.local_center,
        local_radius=args.local_radius,
        local_strength=args.local_strength,
        external_detail_strength=args.external_detail_strength,
        auto_crop_target=args.auto_crop_target,
        auto_crop_padding=args.auto_crop_padding,
        auto_crop_edges=args.auto_crop_edges,
        analysis_report=analysis_report,
        target_name=target_name,
        stretch_method=args.stretch_method,
        use_starnet=args.use_starnet,
        starnet_path=args.starnet_path,
        starnet_stride=args.starnet_stride,
        starnet_timeout=args.starnet_timeout,
        result_json=args.result_json,
        quality_policy=args.quality_policy,
        plate_solve=args.plate_solve,
        solve_field_path=args.solve_field_path,
        plate_solve_timeout=args.plate_solve_timeout,
        catalog=catalog,
        recognition_workflow_dir=args.recognition_workflow_dir,
        low_memory=args.low_memory,
        auto_low_memory=not args.no_auto_low_memory,
        low_memory_threshold_mpix=args.low_memory_threshold_mpix,
        reference_image=args.reference_image,
        reference_auto_search=args.reference_auto_search,
        reference_strength=args.reference_strength,
        reference_match_orientation=args.reference_match_orientation,
    )
    if not args.result_json:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
