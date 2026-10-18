#!/usr/bin/env python3
"""
Deep-Sky Post-Processing Pipeline (深空天文后期全流程管线)

参照 Siril + SetiAstroSuitePro 专业流程。
以下编号与运行时打印的 [Phase N] 标签一致:

  ── 线性阶段 (Siril Linear Phase) ──
  自动裁边 (可选, 非编号阶段)
  Phase 1:  背景提取 (DBE/ABE)
  Phase 2:  颜色校准 (PCC/白平衡)
  Phase 3:  Preliminary denoise (DeepSNR CLI / Starlet)
  Phase 4:  去星 (StarNet/SyqonStarless, 在线性数据上)  ← 关键差异
  Phase 5:  拉伸 (亮度通道拉伸, 线性→非线性)

  ── 星点独立处理 (Star Processing, 与星云解耦) ──
  Star S1:  星点拉伸 (Star Stretch)
  Star S2:  星点去紫 (SCNR)
  Star S3:  星点曲线微调 (Curves) + 缩星

  ── 非线性阶段 (Non-Linear Phase) ──
  Phase 6:  星云细节增强 (HDR/Clarity)
  Phase 7:  信号蒙版锐化 (Revela-like)
  Phase 8:  颜色调整 (Vectra-like)
  Phase 8b: 目标区域局部对比度和纹理增强
  Phase 8c: 外部无星层正向结构增强 (需 --external-starless)
  Phase 8d: 轻微缩星

  ── 最终阶段 ──
  Phase 9:  星点合成 (StarComposer)
  Phase 9a: AI 风格定调 (非生成式 profile)
  Phase 10: 最终降噪 (必须最后执行)
  Phase 11: 参考图全局定调 (可选)

流程依据 (Siril 2026 标准流程):
  - 去星在拉伸前: 线性数据上星点未膨胀，AI检测更精确
  - 星点独立拉伸+调色: 与星云完全解耦，避免星点过曝/偏色
  - 双降噪: 初步降噪在线性阶段，最终降噪在星点合成后(整体)

所有处理基于原图真实数据，不引入虚假细节。

用法:
  python pipeline.py M42.fit output.jpg          # FITS 输入
  python pipeline.py input.jpg output.jpg --steps final_color,style
  python pipeline.py input.jpg output.jpg --steps final_color,style --strength strong --save-intermediates
  python pipeline.py input.fit output.fit          # FITS 输入→FITS 输出
  python pipeline.py input.fit output.jpg --steps dbe,color,star_remove,stretch
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import hashlib
from datetime import datetime
import numpy as np
from pathlib import Path
from scipy.ndimage import gaussian_filter, label, median_filter
from skimage.transform import resize

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fits_io import (
    read_image,
    write_image,
    resolve_celestial_target,
    read_capture_metadata,
    build_physical_priors,
)
from target_rules import (
    is_star_poi_target,
    is_emission_nebula_target,
    is_reflection_nebula_target,
    is_m42_target,
    resolve_target_type,
    recommend_stretch,
    _KNOWN_TYPES,
)
from gradient_removal import (
    remove_gradient,
    normalize_background_subtracted,
)
from stretch import (auto_stretch, arcsinh_stretch, masked_stretch, deep_stretch,
                     apply_luminance_stretch, is_stretch_collapsed,
                     inverse_mtf_stretch, derive_mtf_midtones, render_protected_stretch,
                      background_compressed_stretch)
from denoise import (denoise_luminance_chroma, denoise_noise_stats,
                     run_deepsnr, validate_external_denoise, validate_applied_denoise)
from sharpen import multiscale_sharpen, adaptive_signal_sharpen
from star_tools import (separate_stars, reduce_stars, combine_starless_stars,
                        mild_star_reduce_full, detect_stars, estimate_fwhm,
                        saturate_stars)
from star_tools import protect_subject_layers
from star_removal_profiles import (
    get_profile as _star_removal_profile,
    apply_profile as _apply_star_removal_profile,
    TIGHTER_IS_HIGHER as _STAR_RATCHET,
)


def _star_removal_profile_for(cfg, target_type):
    """把 cfg 中的去星门禁覆盖项应用到目标类型档（棘轮受约束）。

    此前这些字段**只能**改代码里的 profile 表，没有任何 CLI 入口，
    导致 `star_flux_normalize` 这类机制开关无法在运行时打开。
    逐字段走 apply_profile ⇒ 仍然是单向棘轮，不会被悄悄放松。
    """
    prof = _star_removal_profile(target_type)
    for key in _STAR_RATCHET:
        if key not in cfg:
            continue
        value = cfg.get(key)
        prof, _bound = _apply_star_removal_profile(prof, value, key)
    return prof
from artifact_gates import check_channel_clipping, check_core_plateau
from stellar_recompose import is_stars_layer_empty
from stellar_repair import run_stellar_repair
from enhance import (hdr_multiscale_compress, protected_hdr_compress, apply_curves,
                     local_nebula_enhance, positive_starless_detail_enhance,
                     measure_stage_effect, MIN_EFFECTIVE_HF_AMPLIFICATION)
from color_tools import (auto_color_calibrate, emission_nebula_calibrate,
                         enhance_saturation, background_neutralize,
                         remove_green_noise, stabilize_emission_channels,
                         lock_background_neutrality)
from color_conv import safe_rgb2hsv as rgb2hsv, safe_hsv2rgb as hsv2rgb
from style_tools import STYLE_PROFILES, apply_professional_style
from quality_metrics import calculate_metrics, analyze_corner_uniformity
from quality_state import read_quality_state, build_quality_state, collect_quality_gates, persist_quality_state, verify_quality_state
from runtime_info import ExecutionConfig, runtime_info
from reference_grade import (match_reference_grade, optimize_reference_grade,
                             match_signal_color)
from neural_star_bridge import (
    detect_neural_starnet_environment,
    evaluate_neural_bridge_recommendation,
    export_starless_payload,
    build_bridge_run_instructions,
    verify_external_starless,
    format_bridge_recommendation_card,
    read_starnet_payload_meta,
    write_starnet_payload_meta,
    solve_channel_rebalance_gains,
)
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
        'star_stretch_factor': 88.0,
        'star_scnr_strength': 0.15,
        'star_reduction': 0.2,
        'star_curves_midtones': 1.05,
        'star_saturation': 1.08,
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
        'star_stretch_factor': 132.0,
        'star_scnr_strength': 0.25,
        'star_reduction': 0.3,
        'star_curves_midtones': 1.1,
        'star_saturation': 1.08,
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
        'star_stretch_factor': 264.0,
        'star_scnr_strength': 0.0,  # 无绿噪
        'star_reduction': 0.40,     # very_dense 强缩星
        'star_curves_midtones': 1.1,
        'star_saturation': 1.08,
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
        'star_stretch_factor': 198.0,
        'star_scnr_strength': 0.0,
        'star_reduction': 0.40,      # very_dense 星场强缩星
        'star_curves_midtones': 1.05,
        'star_saturation': 1.08,
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
        'star_stretch_factor': 198.0,
        'star_scnr_strength': 0.35,
        'star_reduction': 0.4,
        'star_curves_midtones': 1.2,
        'star_saturation': 1.08,
        'star_combine_strength': 1.15,
        'hdr_strength': 0.75,
        'sharpen_amount': 2.0,
        'saturation': 2.0,
        'final_denoise_lum': 0.025,
        'final_denoise_chroma': 0.08,
    },
}

# 步骤契约的**单一事实源**是 stage_registry（16 主步 + 3 可选步+ 32 产物，
# import 时自校验顺序/唯一性/产物归属/域一致性/与实际落盘的一致性）。
# 此处仅派生别名，历史调用点（resolve_step_dependencies / --steps 帮助文本等）
# 一律引用注册表，不再各自维护一份列表。
from stage_registry import (  # noqa: E402
    ALL_STEPS,
    EMISSION_STEPS,
    STEP_BY_ARTIFACT,
    sort_steps,
    step_order,
)
from failure_policy import (  # noqa: E402
    AUTO_FALLBACK,
    PRESERVE_REVIEW,
    STOP,
    action_status,
    resolve_failure_actions,
)
from config_fingerprint import (  # noqa: E402
    describe as describe_config,
    stage_fingerprint,
)


def resolve_step_dependencies(steps, star_layers=None):
    """Expand dependent steps and return canonical execution order plus log."""
    requested = list(dict.fromkeys(steps))
    expanded = set(requested)
    log = []
    if star_layers:
        if 'star_process' in expanded or 'star_combine' in expanded:
            if star_layers['domain'] == 'linear':
                expanded.add('stretch')
            if 'star_combine' in expanded and not star_layers.get('processed_stars'):
                expanded.add('star_process')
        return [step for step in sort_steps(expanded)], log

    # star_process is not useful as a standalone operation: it requires a
    # separated stellar layer, a stretched starless base, and recomposition.
    if "star_process" in expanded:
        required = {"star_remove", "stretch", "star_combine"}
        added = sorted(required - expanded, key=sort_steps)
        expanded.update(required)
        if added:
            log.append(
                "⭐ star_process 依赖补全: " + ", ".join(added)
            )

    # star_combine requires both source layers and a processed stellar layer.
    if "star_combine" in expanded:
        required = {"star_remove", "stretch", "star_process"}
        added = sorted(required - expanded, key=sort_steps)
        expanded.update(required)
        if added:
            log.append(
                "⭐ star_combine 依赖补全: " + ", ".join(added)
            )

    ordered = [step for step in sort_steps(expanded)]
    return ordered, log


def resolve_ghs_b(cfg):
    """Map generic stretch_factor onto the smaller useful GHS b range."""
    if 'ghs_b' in cfg:
        return float(np.clip(cfg['ghs_b'], 2.0, 15.0))
    factor = max(float(cfg.get('stretch_factor', 45.0)), 0.0)
    return float(np.clip(2.0 + 1.5 * np.log1p(factor), 4.0, 12.0))


def resolve_ghs_lphp(cfg):
    """解析显式 GHS LP/HP 锚点；None 表示沿用旧的百分位推导。

    lp 以下与 hp 以上只做等比缩放（切线延伸），用于避免极暗数据拉伸坍缩，
    并为亮核留出高光 headroom。两者都未设置时返回 (None, None)，行为不变。
    窗口退化（hp <= lp）时一并回退，避免曲线出现 NaN。
    """
    lp = cfg.get('ghs_lp')
    hp = cfg.get('ghs_hp')
    lp = None if lp is None else float(np.clip(lp, 0.0, 1.0))
    hp = None if hp is None else float(np.clip(hp, 0.0, 1.0))
    if lp is not None and hp is not None and hp <= lp:
        print(
            f"  [WARN] ghs_hp({hp:.4f}) <= ghs_lp({lp:.4f})，窗口退化，"
            "已回退为不启用 LP/HP"
        )
        return None, None
    return lp, hp


def parse_crop(crop):
    """Parse x,y,width,height crop syntax."""
    if crop is None:
        return None
    values = [int(value.strip()) for value in crop.split(',')]
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        raise ValueError("--crop 必须为 x,y,width,height，且宽高为正数")
    return tuple(values)


# 具名星点门禁的输入尺寸上限。artifact_gates 内部要对两张图做星点检测与成对
# 剖面比对，全分辨率 24MP 上会到几十秒；降采样后仍足够检出胀星/暗坑/振铃。
GATE_PREVIEW_MAX_SIDE = 1400


def downsample_for_gates(image, max_side=GATE_PREVIEW_MAX_SIDE):
    """把图像统一成「3 通道 + 有界尺寸」，供具名星点门禁成对比较。

    artifact_gates 的成对门禁（STAR_BLOAT / STAR_LAYER_LOSS / STAR_HOLES）要求
    两图 shape 完全一致；而带 alpha 的输入是 4 通道，会直接 ValueError。
    故这里统一裁到前 3 通道并按 max_side 降采样。

    始终返回**独立副本**：本函数在 Phase 9 后被用作快照，而 `current` 之后还要
    经过 style / final_denoise / background lock，若返回视图会被原地改写污染参照。
    """
    from skimage.transform import resize

    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    arr = arr[..., :3]
    h, w = arr.shape[:2]
    if max(h, w) <= max_side:
        return np.array(arr, dtype=np.float32, copy=True)
    scale = max_side / max(h, w)
    target = (max(1, int(round(h * scale))), max(1, int(round(w * scale))), 3)
    return resize(arr, target, preserve_range=True, anti_aliasing=True).astype(np.float32)


def _display_domain_channel_ratios(image, mask, percentiles=(55.0,)):
    """把线性图按 pi_mtf 拉到显示域，返回信号区逐通道 p99 的归一化比值。

    补偿增益必须在**显示域**求解：MTF 是非线性曲线，线性域的通道增益比
    不等于显示域的增益比。实测同一份 NGC6888 数据，线性域一次补偿只能把
    R/G 从 3.663 降到 2.316，显示域一轮即到 1.223（原图 1.220）。
    """
    probe_cfg = {'target_bg': 0.045, 'stretch_headroom': 0.05}
    display, _ = render_protected_stretch(np.clip(image, 0, 1).copy(),
                                          'pi_mtf', dict(probe_cfg))
    values = [float(np.percentile(display[..., c][mask], 99.0)) for c in range(3)]
    scale = max(max(values), 1e-12)
    return np.array([v / scale for v in values], dtype=np.float32)


def _rebalance_starless_channels(current, starless, cfg, warnings):
    """对「退化但未塌缩」的通道做逐通道保真回补，返回诊断与修正后的图。

    只在门禁报出 `degraded_channels` 时触发（塌缩无法靠增益救回，那是
    拒绝而非补偿的场景）。增益求解委托给 `solve_channel_rebalance_gains`，
    由它内部的 probe 度量色比 —— 这里只负责提供「显示域探针」并施加结果。
    """
    mask = _nebula_signal_mask_for_rebalance(current)
    if mask is None:
        return {'applied': False, 'reason': 'insufficient_signal'}

    def probe(image):
        return _display_domain_channel_ratios(image, mask)

    solved = _solve_gains_with_probe(current, starless, probe, cfg)
    if not solved.get('applied'):
        warnings.append({
            'code': 'STARLESS_CHANNEL_REBALANCE_SKIPPED',
            'message': f"外部无星层通道退化但回补未生效（{solved.get('reason')}），"
                       f"色比仍偏离原图，需人工确认",
        })
        return solved

    gain = np.asarray(solved['gains'], dtype=np.float32)
    corrected = starless * gain
    print(f"  ✓ 无星层通道保真回补: gains={np.round(gain, 3).tolist()} "
          f"({solved.get('iterations')} 轮求解, 残差 {solved.get('residual', 0):.2%})")
    warnings.append({
        'code': 'STARLESS_CHANNEL_REBALANCE_APPLIED',
        'message': f"外部无星层 G/B 通道强度偏低，已按原图色比回补 "
                   f"(增益 {np.round(gain, 3).tolist()})",
    })
    return {**solved, '_corrected': corrected}


def _nebula_signal_mask_for_rebalance(image):
    from neural_star_bridge import _nebula_signal_mask
    rgb = np.asarray(image, dtype=np.float32)
    if rgb.ndim == 2:
        rgb = np.stack([rgb] * 3, axis=-1)
    mask = _nebula_signal_mask(rgb[..., :3])
    return mask if int(np.count_nonzero(mask)) >= 100 else None


def _solve_gains_with_probe(current, starless, probe, cfg):
    """Reuse the same final convergence and clipping checks for every caller."""
    return solve_channel_rebalance_gains(
        current, starless, probe=probe,
        max_gain=float(cfg.get('starless_rebalance_max_gain', 4.0)),
        iterations=int(cfg.get('starless_rebalance_iterations', 4)),
    )


def export_starnet_payload_for_domain(current, work_dir, domain='mtf'):
    """按域导出 StarNet2 载荷并写 sidecar，返回 (payload_path, midtones)。

    domain='mtf'（默认）：先做 MTF 拉伸再量化。极暗数据**必须**用这个——线性直接
    量化会让弱色通道被压成 0（实测星云区 G 有 93% 像素精确为 0），StarNet2 输出
    随之丢掉 OIII。实测先拉伸可把 G 的量化级数从 70 抬到 5800，且通道比值
    全程保持（R/G 3.15→3.17、B/G 0.83→0.84）。

    sidecar 记录实际的 domain 与 midtones，回流侧据此自动逆变换，避免用户
    手记参数、漏传导致逆变换用错值。
    """
    if domain == 'mtf':
        midtones = derive_mtf_midtones(current)
        filename = "starnet_payload_stretched.tif"
    else:
        midtones = None
        filename = "starnet_payload_linear.tif"

    payload_path = os.path.join(work_dir, filename)
    export_starless_payload(
        current, payload_path, domain=domain, midtones=midtones, shadows=0.0,
    )
    arr = np.asarray(current, dtype=np.float32)
    lum = np.mean(arr[..., :3], axis=2) if arr.ndim == 3 else arr
    write_starnet_payload_meta(
        work_dir, domain=domain, midtones=midtones, shadows=0.0,
        source_median=float(np.median(lum)),
    )
    return payload_path, midtones


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


def _star_suppressed_luminance(luminance):
    """去掉点源后再估计弥散结构。

    用**中值滤波**而不是高通细节检测：星点是小而尖的孤立体，尺度远小于窗口，
    在窗口内占比极低，所以会被中值直接抹掉；星系/星云的弥散发射跨越几十到几百
    像素，在同尺寸窗口内是**主要成分**，因此被保留。这正是原算法失效的原因 ——
    双高通残差 fine - broad 对尖锐点源的响应远强于弥散盘，密集星场里必然
    锁到最亮的那颗星（M33 实测锁到 peak/天空=472x 的恒星，而真实星系在帧中心、
    半径约 118px）。
    """
    src = np.asarray(luminance, dtype=np.float32)
    if src.ndim != 2 or src.size == 0:
        return None
    # 窗口需显著大于 PSF：取画幅短边的约 2.5%，并至少 9px。
    ksize = int(max(9, round(min(src.shape) * 0.025) | 1))
    if ksize > min(src.shape):
        ksize = min(src.shape) | 1
    if ksize < 3:
        return None
    return median_filter(src, size=ksize, mode='nearest')


def _downscale_luminance(luminance, max_preview):
    src = np.asarray(luminance, dtype=np.float32)
    h, w = src.shape
    scale = min(1.0, max_preview / max(h, w))
    if scale >= 1.0:
        return src, scale
    preview = resize(
        src,
        (max(32, round(h * scale)), max(32, round(w * scale))),
        preserve_range=True,
        anti_aliasing=True,
    ).astype(np.float32)
    return preview, scale


def detect_extended_target(image, max_preview=900, star_suppress=True):
    """定位**弥散**深空主体（星系盘、星云团）：先抑制星点，再检测弥散发射。

    与 detect_target_crop 的双高通细节检测互补：那条路径为「紧凑结构」设计，
    在密集星场里会把最亮的恒星当成目标。本函数面向弥散目标。

    流程：
      1. 中值滤波抑制点源，保留弥散发射；
      2. 减去鲁棒天空底（低分位）得到发射图；
      3. 轻度平滑取全图峰值（**不**做边缘归零 —— 天文目标可能出画或贴边）；
      4. 由峰值连通域给出面积等效圆半径，center 取亮度加权质心。

    返回 dict（坐标为 [x, y]，像素，全分辨率），字段与 detect_target_crop 的
    center / object_bbox 保持同构，便于替换调用方。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 3:
        if source.shape[2] < 3:
            luminance = source[..., 0]
        else:
            luminance = (
                0.299 * source[..., 0]
                + 0.587 * source[..., 1]
                + 0.114 * source[..., 2]
            )
    else:
        luminance = source
    if luminance.ndim != 2 or luminance.size == 0:
        raise ValueError("无法从图像中定位弥散主体：输入不是二维亮度图")

    base = _star_suppressed_luminance(luminance) if star_suppress else np.asarray(
        luminance, dtype=np.float32
    )
    if base is None:
        raise ValueError("星点抑制失败：图像尺寸过小")
    preview, scale = _downscale_luminance(base, max_preview)

    # 鲁棒天空底：低分位抗单点污染；减去后得到弥散发射图。
    sky = float(np.percentile(preview, 20.0))
    if not np.isfinite(sky):
        raise ValueError("无法从图像中定位弥散主体：天空底非有限值")
    emission = preview - sky
    if emission.max() <= 0:
        raise ValueError("无法从图像中定位弥散主体：无高于天空底的弥散信号")

    # 大尺度平滑抑制残余噪声尖峰，使峰值落在真实团块中心而非单像素。
    smooth = gaussian_filter(emission, sigma=max(2.0, min(preview.shape) / 120.0))
    peak = float(smooth.max())
    if peak <= 0:
        raise ValueError("无法从图像中定位弥散主体：平滑后无显著峰值")

    # 圈定主体：先用较宽松的阈值（峰值的 35% 或 p65）确定连通域。
    # 阈值不宜过高 —— 弥散星系的亮度沿旋臂衰减很快，50% 阈值只圈到核球
    # （M33 实测 45px，而盘面外沿约 130px），会让半径严重偏小。
    for fraction, floor_pctl in ((0.35, 65.0), (0.25, 55.0), (0.15, 45.0)):
        thresh = max(peak * fraction, float(np.percentile(smooth, floor_pctl)))
        mask = smooth >= thresh
        if mask.any():
            break
    if not mask.any():
        raise ValueError("无法从图像中定位弥散主体：阈值下无像素")

    components, count = label(mask)
    if count == 0:
        raise ValueError("无法从图像中定位弥散主体：连通域为空")
    peak_y, peak_x = np.unravel_index(np.argmax(smooth), smooth.shape)
    comp_id = components[peak_y, peak_x]
    ys, xs = np.where(components == comp_id)
    if xs.size < 4:
        raise ValueError("目标主体区域过小，无法可靠定位")

    # 亮度加权质心：比 bbox 中心更抗不对称的旋臂/核球外扩。
    weights = smooth[ys, xs].astype(np.float64)
    total = float(weights.sum())
    if total <= 0:
        center_x = float((xs.min() + xs.max()) / 2)
        center_y = float((ys.min() + ys.max()) / 2)
    else:
        center_x = float((xs * weights).sum() / total)
        center_y = float((ys * weights).sum() / total)

    # 半径用**流量加权 RMS 半径**，而不是连通域面积等效圆半径：
    # 后者对阈值极其敏感（同一张 M33，阈值 50%→45px、20%→127px），
    # 而 RMS 半径在各阈值下都落在同一量级（M33: 31→73→107→136px），
    # 是描述弥散主体尺度的稳健统计量。
    if total > 0:
        offsets_sq = (
            (xs - center_x) ** 2 + (ys - center_y) ** 2
        ).astype(np.float64)
        radius_preview = float(
            np.sqrt(float((weights * offsets_sq).sum() / total))
        )
    else:
        radius_preview = float(np.sqrt(xs.size / np.pi))
    x0 = float(xs.min()) / scale
    x1 = float(xs.max() + 1) / scale
    y0 = float(ys.min()) / scale
    y1 = float(ys.max() + 1) / scale

    return {
        'center': [round(center_x / scale, 2), round(center_y / scale, 2)],
        'radius': round(radius_preview / scale, 2),
        'object_bbox': [
            round(x0, 2), round(y0, 2),
            round(x1 - x0, 2), round(y1 - y0, 2),
        ],
        'peak_score': peak,
        'sky_level': sky,
        'star_suppressed': bool(star_suppress),
        'source_shape': [int(luminance.shape[0]), int(luminance.shape[1])],
    }


# 弥散检测与参考中心（WCS/帧中心）偏差超过此值即提示人工确认。
# 取值约为短边的 15%：明显超出这个范围，说明自动定位很可能锁错了结构。
_GALAXY_CENTER_DISAGREEMENT_WARN_PX = 0.15

# 星系核保护半径（占画幅短边的比例）。该半径内的亮像元不参与去星，
# 因此核球与内盘原样保留，星点只在盘外被分离。
GALAXY_CORE_PROTECTION_FRACTION = 0.06


def _galaxy_wcs_center(plate_solution, target_name):
    """从板解结果取标称中心的像素坐标 (x, y)，取不到返回 None。

    只认 status == success 且目标名精确匹配的目录项，避免把
    用户没指定的目标当成星系中心。仅作**交叉验证**，不覆盖视觉检测。
    """
    if not isinstance(plate_solution, dict):
        return None
    if plate_solution.get("status") != "success":
        return None
    wanted = str(target_name or "").strip().lower()
    if not wanted:
        return None
    for item in plate_solution.get("objects") or []:
        if str(item.get("name", "")).strip().lower() != wanted:
            continue
        pixel = item.get("pixel")
        if isinstance(pixel, (list, tuple)) and len(pixel) >= 2:
            try:
                return [float(pixel[0]), float(pixel[1])]
            except (TypeError, ValueError):
                return None
    return None


def _frame_center_fallback(shape):
    h, w = int(shape[0]), int(shape[1])
    return [round(w / 2.0, 2), round(h / 2.0, 2)]


def resolve_galaxy_center(
    image,
    wcs_center=None,
    max_preview=900,
    star_suppress=True,
):
    """为星系目标解析可靠中心，带降级链与一致性审计。

    优先级：
      1. wcs_center（板解/WCS 反投影的标称中心）—— 最权威；
      2. detect_extended_target 的星点抑制弥散检测 —— 适合无 WCS 的定标帧；
      3. 帧中心 —— 盲场/无解时的最后兜底。

    返回 (center, info)。info[method] 记录实际采用的路径，
    info[disagreement_px] 记录弥散检测与参考中心之间的偏差，供审查。
    """
    source = np.asarray(image)
    shape = source.shape[:2]
    frame_center = _frame_center_fallback(shape)

    diffuse_info = None
    diffuse_center = None
    try:
        diffuse_info = detect_extended_target(
            source, max_preview=max_preview, star_suppress=star_suppress
        )
        diffuse_center = diffuse_info["center"]
    except Exception:
        diffuse_info = None

    reference = wcs_center if wcs_center is not None else frame_center
    disagreement = None
    if diffuse_center is not None:
        disagreement = float(
            np.hypot(
                diffuse_center[0] - reference[0],
                diffuse_center[1] - reference[1],
            )
        )

    info = {
        'method': None,
        'frame_center': frame_center,
        'diffuse_center': diffuse_center,
        'diffuse_info': diffuse_info,
        'wcs_center': (
            [round(float(wcs_center[0]), 2), round(float(wcs_center[1]), 2)]
            if wcs_center is not None else None
        ),
        'disagreement_px': (None if disagreement is None
                            else round(disagreement, 2)),
        'source_shape': [int(shape[0]), int(shape[1])],
    }

    if wcs_center is not None:
        info['method'] = 'wcs'
        return [round(float(wcs_center[0]), 2),
                round(float(wcs_center[1]), 2)], info
    if diffuse_center is not None:
        info['method'] = 'diffuse'
        return [round(float(diffuse_center[0]), 2),
                round(float(diffuse_center[1]), 2)], info
    info['method'] = 'frame_center'
    return frame_center, info

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


def apply_target_aware_safety_rules(cfg, steps, target_type, target_name):
    """
    根据天体类型应用安全规则，修改 cfg 和 steps。

    这是将 references/target_awareness.md 中的领域知识落实到代码的关键函数。
    返回 (modified_cfg, modified_steps, safety_log)。

    拉伸因子采用"单一归属"：优先级为 M42 > 发射星云 > 反射星云 > 暗星云，
    由第一个命中的规则独占乘法，避免同一目标被连续缩放（历史上 M42 曾被
    M42 规则 ×0.75 与发射星云规则 ×0.85 叠加成 ×0.6375）。
    非拉伸调整（锐化/HDR/降噪/饱和）不受此约束，仍按规则叠加，因此 M42
    在独占拉伸的同时依然获得发射星云的锐化 ×0.7 保护。
    谓词实现见 target_rules 模块（名称统一经 normalize_target_name 规范化）。
    """
    cfg = dict(cfg)
    steps = list(steps)
    log = []

    star_poi = is_star_poi_target(target_type, target_name)
    m42 = is_m42_target(target_name)
    emission = is_emission_nebula_target(target_type, target_name)
    reflection = is_reflection_nebula_target(target_type, target_name)
    resolved_type = resolve_target_type(target_type, target_name)
    stretch_owned = False

    # ── 规则 1: 星点即主体 → 绝对禁止去星/缩星，降噪与饱和保守 ──
    if star_poi:
        banned = []
        for step in ('star_remove', 'star_reduce', 'star_process', 'star_combine'):
            if step in steps:
                steps.remove(step)
                banned.append(step)
        if banned:
            log.append(
                f"🛡️ 星团安全: 禁用 {', '.join(banned)} — 星点即目标主体"
            )
        # 降噪保守：亮度与色彩通道按同一比例收紧
        # (analyze.py 中 chroma:lum 恒为 3:1)
        cfg['pre_denoise_lum'] = min(cfg.get('pre_denoise_lum', 0.02), 0.01)
        cfg['final_denoise_lum'] = min(cfg.get('final_denoise_lum', 0.015), 0.005)
        cfg['pre_denoise_chroma'] = min(cfg.get('pre_denoise_chroma', 0.04), 0.03)
        cfg['final_denoise_chroma'] = min(
            cfg.get('final_denoise_chroma', 0.03), 0.015
        )
        log.append(
            "🛡️ 星团安全: 降噪限制为保守级别 (L≤0.01/0.005, C≤0.03/0.015)"
        )
        # 饱和度微调：密集恒星主体不宜强化色彩
        cfg['saturation'] = min(cfg.get('saturation', 1.2), 1.25)
        log.append("🛡️ 星团安全: 饱和度上限 1.25")

    # ── 规则 2: M42 核心保护（拉伸优先级最高，独占拉伸乘法）──
    if m42:
        # 降低拉伸因子防止核心过曝
        old_stretch = cfg.get('stretch_factor', 45.0)
        cfg['stretch_factor'] = old_stretch * 0.75
        stretch_owned = True
        cfg['hdr_strength'] = min(cfg.get('hdr_strength', 0.35) + 0.15, 0.7)
        cfg['target_bg'] = max(cfg.get('target_bg', 0.08), 0.10)
        log.append(
            f"🛡️ M42 核心保护: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} "
            f"(×0.75), HDR+0.15, target_bg≥0.10"
        )

    # ── 规则 3: 发射星云参数修正 ──
    if emission:
        # 发射星云拉伸保守；若更高优先级规则已接管拉伸则让位
        if not stretch_owned:
            old_stretch = cfg.get('stretch_factor', 45.0)
            if old_stretch > 30:
                cfg['stretch_factor'] = old_stretch * 0.85
                stretch_owned = True
                log.append(
                    f"🛡️ 发射星云: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} (×0.85)"
                )
        # 锐化保守（M42 同样保留此项）
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
    if reflection:
        old_stretch = cfg.get('stretch_factor', 45.0)
        if not stretch_owned:
            cfg['stretch_factor'] = old_stretch * 1.20
            stretch_owned = True
        cfg['hdr_strength'] = max(cfg.get('hdr_strength', 0.5) - 0.1, 0.15)
        # 降噪稍强（暗弱信号噪声高）
        cfg['pre_denoise_lum'] = min(cfg.get('pre_denoise_lum', 0.02) * 1.15, 0.06)
        log.append(
            f"🛡️ 反射星云: stretch {old_stretch:.1f}→{cfg['stretch_factor']:.1f} (+20%), "
            f"HDR-0.1, denoise+15%"
        )

    # ── 规则 5: 星系参数修正 ──
    if resolved_type == 'galaxy':
        old_hdr = cfg.get('hdr_strength', 0.5)
        cfg['hdr_strength'] = min(old_hdr + 0.15, 0.75)
        old_sharpen = cfg.get('sharpen_amount', 1.3)
        if old_sharpen < 1.5:
            cfg['sharpen_amount'] = min(old_sharpen + 0.3, 2.0)
        # 缩星保守化：星系场的 HII 区/旋臂结有被卷入缩星的风险。
        # 不照搬"星系场禁用缩星"——本地是 Telea/NS 修复 + 线性星点蒙版，
        # 与形态学 threshold 法的环状伪影机理不同；改为限制强度上限，
        # 蒙版保护由 mild_star_reduce_full / reduce_stars 的星点蒙版承担
        # （连通域过滤已把面积>π(4·FWHM)² 且圆度<0.25 的星云亮核排除在外）。
        old_reduction = float(cfg.get('star_reduction', 0.3))
        if old_reduction > 0.25:
            cfg['star_reduction'] = 0.25
            log.append(
                f"🛡️ 星系: star_reduction {old_reduction:.2f}→0.25 "
                "— HII 区/旋臂结保护"
            )
        log.append(
            f"🛡️ 星系: HDR {old_hdr:.2f}→{cfg['hdr_strength']:.2f} (+0.15), "
            f"sharpen 增强"
        )

    # ── 规则 6: 行星状星云 ──
    if resolved_type == 'planetary_nebula':
        old_hdr = cfg.get('hdr_strength', 0.5)
        cfg['hdr_strength'] = min(old_hdr + 0.15, 0.8)
        old_sharpen = cfg.get('sharpen_amount', 1.3)
        cfg['sharpen_amount'] = min(old_sharpen + 0.4, 2.5)
        log.append(
            f"🛡️ 行星状星云: HDR+0.15, sharpen 增强"
        )

    # ── 规则 7: 暗星云（拉伸优先级最低，仅在无人接管时生效）──
    if resolved_type == 'dark_nebula' and not stretch_owned:
        old_stretch = cfg.get('stretch_factor', 45.0)
        cfg['stretch_factor'] = old_stretch * 1.15
        stretch_owned = True
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
                 use_starnet=False, starnet_path=None, starnet_stride=128,
                 star_removal_baseline=None,
                 starnet_domain='mtf',
                 starnet_timeout=900,
                 result_json=None, quality_policy='advisory',
                 plate_solve=False, solve_field_path=None,
                 plate_solve_timeout=180, catalog=None,
                 recognition_workflow_dir=None,
                 low_memory=False, auto_low_memory=True,
                 low_memory_threshold_mpix=8.0,
                 reference_image=None, reference_auto_search=False,
                 reference_color_match=False, reference_color_only=False,
                 reference_strength=0.85,
                 reference_match_orientation=False,
                 stellar_repair=False,
                 stellar_repair_mode=None,
                 stellar_repair_strength=None,
                 palette='none',
                 linear_deconv=False,
                 deconv_iterations=10,
                 stacking_crop='none',
                 halo_guard=False,
                 star_blend_mode='screen',
                 background_neutralize_lock=True,
                 export_starnet_payload=False,
                 export_starnet_payload_domain='mtf',
                 external_starless_domain='linear',
                 external_starless_midtones=None,
                 starnet_recommendation='auto',
                 deepsnr='auto', deepsnr_path=None, deepsnr_timeout=900,
                 deepsnr_strength=0.35, input_is_linear=None,
                 capture_metadata=None, star_layers=None, subject_mask=None, input_quality_state=None):
    """执行深空后期处理全流程。"""
    started_at = now_iso()
    requested_config = {'preset': preset, 'steps': steps,
                        'params': dict(override_params or {}), 'input_is_linear': input_is_linear,
                        'stretch_method': stretch_method, 'color_mode': color_mode,
                        'style': style, 'style_strength': style_strength}
    if capture_metadata is not None and not isinstance(capture_metadata, dict):
        raise ValueError('capture_metadata must be an object')
    if star_layers is not None:
        if not isinstance(star_layers, dict) or star_layers.get('domain') not in ('linear', 'nonlinear'):
            raise ValueError('Invalid star layer state')
        if not all(star_layers.get(key) for key in ('source', 'starless', 'stars')):
            raise ValueError('Star layer state requires source, starless and stars')
        if 'stars_is_linear' in star_layers and not isinstance(star_layers['stars_is_linear'], bool):
            raise ValueError('stars_is_linear must be a boolean')
    source_capture_metadata = capture_metadata
    capture_metadata = {}
    header_to_use = None
    try:
        capture_metadata, header_to_use = read_capture_metadata(input_path)
    except Exception as exc:
        capture_metadata = {"source": "unavailable", "warning": str(exc)}
    if source_capture_metadata is not None:
        capture_metadata = dict(source_capture_metadata)
    physical_priors = build_physical_priors(capture_metadata)

    # ── 智能天体属性识别与自适应数据填充 ──
    if not target_name or not target_type:
        resolved = resolve_celestial_target(header=header_to_use,
                                           target_name=target_name or capture_metadata.get('object'))
        if resolved['resolved_type'] != 'unknown_deep_sky':
            if not target_type:
                target_type = resolved['resolved_type']
                print(f"[自适应] 自动识别并设定天体类型: {target_type}")
        if resolved['resolved_name'] != 'unknown':
            if not target_name:
                target_name = resolved['resolved_name']
                print(f"[自适应] 自动识别并设定天体名称: {target_name}")

    # ── 配置初始化 ──
    if preset == 'adaptive' and analysis_report is None:
        from analyze import analyze_image
        linear_hint = (override_params or {}).get('input_is_linear', input_is_linear)
        analysis_report = analyze_image(input_path, input_is_linear=linear_hint,
                                        capture_metadata=capture_metadata)
    stellar_analysis = (star_layers or {}).get('stellar_analysis')
    if stellar_analysis and analysis_report:
        analysis_report = dict(analysis_report)
        analysis_report['starfield'] = stellar_analysis['starfield']
        analysis_report['recommendations'] = {**analysis_report.get('recommendations', {}),
                                               'star_tools': stellar_analysis['star_tools']}
    if preset == 'adaptive':
        print("[自适应] 使用诊断报告驱动参数...")
        cfg = build_config_from_analysis(
            analysis_report, base_preset='medium', target_type=target_type
        )
    else:
        cfg = dict(STRENGTH_PRESETS[preset])

    cfg.update(deepsnr=deepsnr, deepsnr_path=deepsnr_path,
               deepsnr_timeout=deepsnr_timeout, deepsnr_strength=deepsnr_strength,
               input_is_linear=input_is_linear)

    # 物理元数据先验低于用户显式覆盖，但高于静态预设。
    for key, value in physical_priors.get("parameter_overrides", {}).items():
        old_value = cfg.get(key)
        cfg[key] = value
        print(f"  [物理先验] {key}: {old_value} → {value}")

    filter_class = (
        capture_metadata.get("filter_profile", {}).get("class")
        if isinstance(capture_metadata, dict) else None
    )
    if color_mode == "auto" and filter_class in ("dual_band", "narrowband", "multi_band"):
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

    if cfg['deepsnr'] not in ('auto', 'always', 'never'):
        raise ValueError('DeepSNR mode must be auto, always or never')
    if not 0 < float(cfg['deepsnr_strength']) <= 1:
        raise ValueError('DeepSNR strength must be in (0, 1]')
    if not np.isfinite(float(cfg['deepsnr_timeout'])) or float(cfg['deepsnr_timeout']) <= 0:
        raise ValueError('DeepSNR timeout must be positive')
    if cfg['input_is_linear'] is not None and not isinstance(cfg['input_is_linear'], bool):
        raise ValueError('input_is_linear must be a boolean')

    if stellar_repair_mode:
        cfg['stellar_repair_mode'] = stellar_repair_mode
    if stellar_repair_strength:
        cfg['stellar_repair_strength_profile'] = stellar_repair_strength

    # ── 天体类型安全规则（将 target_awareness.md 落实到代码） ──
    steps_explicitly_specified = steps is not None
    if steps is None:
        steps = list(EMISSION_STEPS) if preset == 'emission' else list(ALL_STEPS)
        if preset == 'emission' and external_starless:
            steps.insert(steps.index('star_reduce'), 'external_detail')
    else:
        if isinstance(steps, str):
            steps = [s.strip() for s in steps.split(',')]
        else:
            steps = list(steps)
        invalid = [s for s in steps if s not in ALL_STEPS]
        if invalid:
            # 可选步骤（linear_deconv / reference_grade /
            # background_neutralization）**不在主流程内**，只能由各自的
            # CLI 开关触发（--linear-deconv 等），不接受 --steps 传入。
            # 错误信息显式说明这一点，避免用户以为「传了就会跑」。
            raise ValueError(
                f"未知步骤: {', '.join(invalid)}; "
                f"可选步骤: {', '.join(ALL_STEPS)}"
                + "（可选步骤 linear_deconv / reference_grade / "
                  "background_neutralization 请用各自的 CLI 开关）"
            )

    requested_steps = list(steps)
    sr_diag = (analysis_report or {}).get("stellar_repair", {}) if isinstance(analysis_report, dict) else {}
    stellar_repair_requested = (
        stellar_repair
        or (steps_explicitly_specified and 'stellar_repair' in steps)
        or sr_diag.get("decision") == "apply"
    )
    if not stellar_repair_requested and 'stellar_repair' in steps:
        steps.remove('stellar_repair')
    elif stellar_repair_requested and 'stellar_repair' not in steps:
        idx = steps.index('color') + 1 if 'color' in steps else 0
        steps.insert(idx, 'stellar_repair')

    if star_layers:
        if os.path.realpath(input_path) != os.path.realpath(star_layers['starless']):
            raise ValueError('Star layer source does not match the current artifact')
        if linear_deconv or set(steps) & {'dbe', 'color', 'pre_denoise', 'star_remove', 'stellar_repair'}:
            raise ValueError('Roll back before separation to rerun upstream processing')
        subject_mask = subject_mask or star_layers.get('subject_mask')
        if star_layers['domain'] == 'nonlinear' and 'stretch' in steps:
            raise ValueError('The starless layer has already been stretched')
        cfg['input_is_linear'] = star_layers['domain'] == 'linear'
    # 星系：核球与星系盘无法靠亮度区分，去星确实可能误伤核心。但「完全不做
    # 分离」的代价已被实测证明更高：全局 max 由稀疏亮星决定，主体被压进
    # 归一化轴最底端（某母版星点独占 99.4% 高光余量，主体 p99 仅占余量的
    # 0.53%），此后任何拉伸参数都无法让主体突出 —— 盘面/天空比被锁死在 ~1.22。
    #
    # 因此改为**默认分离**，用 `galaxy_core_protection` 半径内的亮像元判定
    # 保护：分离只发生在「非保护且确认为恒星」的位置，核球/内盘原样保留。
    # 显式传入 reviewed `subject_mask` 时仍走原路径（更严格）。
    if target_type == 'galaxy' and not star_layers:
        star_steps = {'star_remove', 'star_process', 'star_combine', 'star_reduce'}
        if subject_mask is None:
            # 只在**完整管线**（未显式指定 steps）时补齐分离链路。
            # Agent 逐阶段 `run_step` 会显式传 steps，此时绝不能静默扩步 ——
            # 否则请求 pre_denoise 会被塞进 star_remove+stretch+star_combine，
            # 一次动作跑完整条链，阶段可追溯性直接失效。
            if not steps_explicitly_specified and 'star_remove' not in steps:
                for step in ('star_remove', 'star_process', 'star_combine'):
                    if step not in steps:
                        steps.append(step)
                steps, _ = resolve_step_dependencies(steps, star_layers=star_layers)
            cfg['galaxy_core_protection'] = GALAXY_CORE_PROTECTION_FRACTION
            print(
                "  [去星] 星系走「核球保护 + 分离」路径："
                "分离半径外的恒星会被分离，用于把高光余量让给主体。"
                "核心保护半径=短边×%.2f；如需更严格控制请传入 --subject-mask"
                % GALAXY_CORE_PROTECTION_FRACTION
            )
        else:
            cfg['galaxy_core_protection'] = None
            print("  [去星] 星系使用已审查 subject_mask 保护核心")
    steps, dependency_log = resolve_step_dependencies(steps, star_layers=star_layers)

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
    if is_m42_target(target_name) and 'target_bg' in (override_params or {}):
        cfg['target_bg'] = override_params['target_bg']
        safety_log.append('Explicit target_bg retained for measured background matching')
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

    keep_intermediates = bool(save_intermediates or keep_all or star_layers
                              or ('star_remove' in steps and 'star_combine' not in steps))
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
    # 产物附注：与 artifact_paths 平行，只在产物**语义需要澄清**时有条目。
    # 例如回退时的 04_starless_fallback.tif 是原图副本而非无星图，
    # 必须让下游/人工审查能一眼区分，不能靠「看文件名猜」。
    artifact_meta = {}
    # 早于 Phase 4 就可能被 append（外部无星图分支），必须在此初始化。
    warnings = []

    print(f"\n{'='*65}")
    print(f"  深空天文后期处理管线 (Siril+SASP 流程)")
    print(f"  强度预设: {preset}")
    print(f"  执行步骤: {' → '.join(steps)}")
    print(f"{'='*65}\n")

    stretch_policy_version = cfg.setdefault("stretch_policy_version", 3)
    runtime = runtime_info(__file__, stretch_policy_version)
    print(f"[Runtime] skill={runtime['skill_version']} policy={stretch_policy_version} "
          f"sha256={runtime['source_sha256']} script={runtime['script_path']}")
    if stretch_policy_version == 1:
        warnings.append({'code': 'LEGACY_STRETCH_POLICY',
                         'message': 'Policy 1 retained for compatibility; initialize a new session for policy 3'})
    if stretch_policy_version not in (1, 2, 3):
        raise ValueError("stretch_policy_version must be 1, 2 or 3")

    img, meta = read_image(input_path)
    img = np.asarray(img, dtype=np.float32)
    is_linear_input = (meta.get('is_linear', False) if cfg['input_is_linear'] is None
                       else cfg['input_is_linear'])
    inherited_quality = read_quality_state(input_path)
    if input_quality_state is not None:
        verify_quality_state(input_path, input_quality_state)
        saved_issues = {issue['id']: issue for issue in (inherited_quality or {}).get('issues', [])}
        saved_issues.update({issue['id']: issue for issue in input_quality_state['issues']})
        inherited_quality = {**input_quality_state, 'issues': list(saved_issues.values())}
    if (meta.get('stage_source') == 'sidecar' and cfg['input_is_linear'] is not None
            and cfg['input_is_linear'] != meta['is_linear']):
        raise ValueError('Input stage conflicts with saved sidecar')
    if 'color' in steps and not is_linear_input:
        raise ValueError('Color calibration requires linear input; roll back before stretching')
    cfg['input_is_linear'] = is_linear_input
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
        external_starless=bool(external_starless or star_layers),
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
        print("[线性输入] stretch因子×1.5，星点拉伸×1.5")
    stars_is_linear = star_layers.get('stars_is_linear', True) if star_layers else is_linear_input
    if stars_is_linear:
        cfg['star_stretch_factor'] *= 1.5

    subject_mask_array = None
    if subject_mask:
        if str(subject_mask).lower().endswith(('.tif', '.tiff')):
            import tifffile
            subject_mask_array = tifffile.imread(subject_mask)
        else:
            subject_mask_array, _ = read_image(subject_mask, force_linear=True)
        if (subject_mask_array.shape != img.shape[:2]
                or not np.all(np.isfinite(subject_mask_array))
                or subject_mask_array.min() < 0 or subject_mask_array.max() > 1):
            raise ValueError('Subject mask must match image geometry with finite samples in [0, 1]')
    if star_layers and (crop or auto_crop_target or stacking_crop != 'none'):
        raise ValueError('Crop before separation; resumed star layers must retain geometry')
    if star_layers:
        auto_crop_edges = False

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
    if subject_mask_array is not None:
        subject_mask_array, _ = apply_crop(subject_mask_array, crop_bounds)
    if alpha_channel is not None:
        alpha_channel, _ = apply_crop(alpha_channel, crop_bounds)
    if applied_crop:
        print(f"[裁切] x={applied_crop[0]} y={applied_crop[1]} "
              f"width={applied_crop[2]} height={applied_crop[3]}")

    stacking_crop_box = None
    if stacking_crop == 'auto':
        from border_tools import apply_stacking_border_trim
        trimmed_img, border_box = apply_stacking_border_trim(img)
        t, b, l, r = border_box
        if t > 0 or b > 0 or l > 0 or r > 0:
            print(f"[叠边裁切] 检测到抖动叠加无效边缘 top={t}, bottom={b}, left={l}, right={r}，执行裁切")
            img = trimmed_img
            stacking_crop_box = border_box
            if subject_mask_array is not None:
                subject_mask_array = subject_mask_array[t:subject_mask_array.shape[0]-b if b else None,
                                                        l:subject_mask_array.shape[1]-r if r else None]
            if alpha_channel is not None:
                alpha_channel = alpha_channel[t:alpha_channel.shape[0]-b if b>0 else None, l:alpha_channel.shape[1]-r if r>0 else None]
            if external_full is not None:
                external_full = external_full[t:external_full.shape[0]-b if b>0 else None, l:external_full.shape[1]-r if r>0 else None]
            if external_denoised_full is not None:
                external_denoised_full = external_denoised_full[t:external_denoised_full.shape[0]-b if b>0 else None, l:external_denoised_full.shape[1]-r if r>0 else None]


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
    stretch_recovery = None
    stretch_diagnostics = None
    cfg = ExecutionConfig(cfg)
    # target_type 必须进 cfg：它是**门禁阈值的选择依据**（去星门禁按 target_type
    # 分档），不进 cfg 就等于「配置指纹相同但门禁结果不同」—— 那正是
    # config_fingerprint 声明要防的不可区分产物。
    cfg['target_type'] = effective_target_type
    star_layer_empty = False
    star_combine_skipped = None
    neural_bridge_report = None
    pre_denoise_report = None
    skip_final_denoise = bool(cfg.get('skip_final_denoise', False))
    channel_clipping_gates = []
    # 保真度门禁记录（噪声增益 / 色度保真）。仅在拉伸段成功且参考可比时填充，
    # 透传给 evaluate_quality_gates；空列表时门禁体系不参与判定。
    fidelity_gates = []
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

    def save_artifact(filename, target, is_linear=None):
        # 产物 → 步骤的映射由 stage_registry.STEP_BY_ARTIFACT 单一维护，
        # 且注册表在 import 时已校验「与本文件的实际落盘调用一致」，
        # 因此这里新增产物时漏登记会在 import 阶段就失败。
        completed = STEP_BY_ARTIFACT.get(filename)
        if completed == cfg.phase and completed not in cfg.completed:
            cfg.completed.append(completed)
        target = np.clip(np.asarray(target, dtype=np.float32), 0, 1)
        path = os.path.join(work_dir, filename)
        if keep_intermediates:
            write_image(target, path)
            artifact_paths[filename] = path
            write_result_json_atomic({'capture_metadata': capture_metadata,
                                      'is_linear': (is_linear if is_linear is not None else stars_is_linear if filename == '04_stars_linear.tif'
                                                    else bool(is_linear_input and filename[:2] in ('00', '01', '02', '03', '04')))},
                                     path + '.meta.json')
        return target

    def save_current(filename='', img=None):
        nonlocal current
        target = img if img is not None else current
        return save_artifact(filename, target)

    if subject_mask_array is not None:
        save_artifact('04_subject_mask.tif', subject_mask_array)
        subject_mask = artifact_paths.get('04_subject_mask.tif', subject_mask)

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

    cached_target_info = None
    galaxy_center_resolution = None
    if effective_target_type == 'galaxy':
        # 星系主体是**弥散**目标：必须走星点抑制的弥散检测。原先用
        # detect_target_crop 的双高通细节检测，在密集星场里必然锁到最亮的
        # 恒星（M33 实测锁到 peak/天空=472x 的星点，而真实星系在帧中心）。
        # 该错误会沿 Phase 1/8/8b 传导：DBE 保护区、饱和掩膜、局部增强全部
        # 作用在空天上，导致 texture_snr 失真、local_enhance 空转。
        galaxy_wcs_center = _galaxy_wcs_center(plate_solution, target_name)
        try:
            galaxy_center, galaxy_center_resolution = resolve_galaxy_center(
                current, wcs_center=galaxy_wcs_center
            )
        except Exception:
            galaxy_center, galaxy_center_resolution = None, None
        if galaxy_center is not None:
            _diffuse = (galaxy_center_resolution or {}).get('diffuse_info') or {}
            cached_target_info = {
                'center': [galaxy_center[0], galaxy_center[1]],
                'object_bbox': _diffuse.get('object_bbox', [0.0, 0.0, 0.0, 0.0]),
                'radius': _diffuse.get('radius'),
                'detection': galaxy_center_resolution,
            }
            _gm = (galaxy_center_resolution or {}).get('method', 'unknown')
            _gd = (galaxy_center_resolution or {}).get('disagreement_px')
            _shape = current.shape[:2]
            _warn_px = _GALAXY_CENTER_DISAGREEMENT_WARN_PX * min(_shape)
            print(
                f"  [星系定位] 方法={_gm} center=({galaxy_center[0]:.0f}, "
                f"{galaxy_center[1]:.0f})"
                + (f" 半径={_diffuse.get('radius', 0):.0f}px" if _diffuse.get('radius') else '')
                + (f" 与参考中心偏差={_gd:.0f}px" if _gd is not None else '')
            )
            if _gd is not None and _gd > _warn_px:
                print(
                    f"    ⚠️ 弥散检测与参考中心偏差 {_gd:.0f}px 超过 "
                    f"{_warn_px:.0f}px（短边 {_GALAXY_CENTER_DISAGREEMENT_WARN_PX:.0%}），"
                    f"请人工确认目标位置"
                )

    # Phase 1: 背景提取 (DBE)
    if 'dbe' in steps:
        cfg.phase = 'dbe'
        dbe_method = cfg.get('dbe_method', 'polynomial')
        dbe_degree = cfg.get('dbe_degree', 2)
        dbe_exclusion_mask = None
        if effective_target_type == 'galaxy' and cached_target_info is not None:
            tcx = float(cached_target_info['center'][0])
            tcy = float(cached_target_info['center'][1])
            ob_w = float(cached_target_info['object_bbox'][2])
            ob_h = float(cached_target_info['object_bbox'][3])
            # 弥散检测直接给出流量加权半径，优先用它；退化时回退到 bbox 换算。
            _det_radius = cached_target_info.get('radius')
            if _det_radius and float(_det_radius) > 0:
                ex_r = float(_det_radius) * 3.0
            else:
                ex_r = max(ob_w, ob_h) * 2.2
            ex_r = max(ex_r, 1.0)
            yy, xx = np.ogrid[:current.shape[0], :current.shape[1]]
            dbe_exclusion_mask = ((xx - tcx)**2 + (yy - tcy)**2) <= (ex_r ** 2)
            print(f"  [DBE] 设定星系目标保护排除区 center=({int(tcx)}, {int(tcy)}), radius={int(ex_r)}px")
        elif effective_target_type == 'emission_nebula':
            # 发射星云主体排除区提取
            # 使用大尺度低通滤波提取主要的 Hα / OIII 弥散发射结构，避免 DBE 多项式曲面拟合把星云自身拟合成穹顶减除
            ref_ch = current[..., 0] if (current.ndim == 3 and current.shape[2] >= 3) else current
            ds = max(1, min(ref_ch.shape[:2]) // 270)
            sub = ref_ch[::ds, ::ds]
            sub_smooth = gaussian_filter(sub, sigma=15.0)
            thresh = float(np.percentile(sub_smooth, 35.0))
            sub_mask = sub_smooth > thresh
            mask_full = resize(sub_mask.astype(np.float32), ref_ch.shape[:2], order=1, preserve_range=True) > 0.5
            coverage = float(np.mean(mask_full))
            if coverage > 0.05:
                dbe_exclusion_mask = mask_full
                print(f"  [DBE] 设定发射星云弥散主体保护排除区 (覆盖率: {coverage:.1%})")
                if coverage > 0.30 and dbe_degree > 1:
                    dbe_degree = 1
                    print(f"  [DBE] 大尺度弥散发射星云覆盖度较高 ({coverage:.1%})，自动约束 degree=1 线性梯度倾斜拟合以防中心被凹陷减除")

        if subject_mask_array is not None:
            protected = subject_mask_array > 0
            dbe_exclusion_mask = protected if dbe_exclusion_mask is None else dbe_exclusion_mask | protected
        print("\n─ 线性阶段 ─")
        print(f"[Phase 1] 背景提取 / 梯度去除 (DBE) [{dbe_method}"
              + (f", degree={dbe_degree}" if dbe_method == 'polynomial' else "")
              + "]...")
        current, _ = remove_gradient(
            current, method=dbe_method,
            degree=dbe_degree if dbe_degree else 2,
            exclusion_mask=dbe_exclusion_mask,
        )

        # ── 归一化 ──
        # 黑点取背景稳健中心、白点锚定真实峰值、并保留 pedestal。
        # 细节与踩过的坑见 normalize_background_subtracted 的 docstring。
        current = normalize_background_subtracted(
            current, pctl_high=cfg['dbe_pctl_high']
        )
        cfg.used['dbe']['dbe_degree'] = dbe_degree if dbe_method == 'polynomial' else None
        save_current('01_dbe.tif')
        # 注意：黑点已改为背景稳健中心、白点锚定峰值，不再使用 dbe_pctl_low /
        # dbe_pctl_high 作为黑/白点（dbe_pctl_high 仅作为白点的下界保留）。
        print(f"  ✓ 光害梯度已去除 (归一化: 黑点=背景中心, 白点=峰值, "
              f"p{cfg['dbe_pctl_high']} 为白点下界)")

        # CP1: DBE 后强制四角均匀度检查 (天体感知)
        _corner_info = analyze_corner_uniformity(current)
        _corner_ratio = _corner_info["effective_uniformity_ratio"]
        _corners_dbe = _corner_info["raw_means"]
        _ex_applied = _corner_info["exemption_applied"]
        if _ex_applied:
            _corner_status = '✅'
            _desc = f" (天体感知: {_corner_info['celestial_dominated_corners']} 覆盖天体结构已豁免)"
        else:
            _corner_status = '✅' if _corner_ratio < 1.05 else ('⚠️' if _corner_ratio < 3.0 else '❌')
            _desc = ""
        print(f"  CP1 四角均匀度: {_corner_status} ratio={_corner_ratio:.2f}x{_desc} "
              f"corners={[round(c, 5) for c in _corners_dbe]}")
        if not _ex_applied and _corner_ratio >= 3.0:
            print("  ⚠️ DBE 后四角不均匀度 > 3.0x — 可能需要调整 DBE 参数（degree/method）或检查天体是否靠边")
    cfg.phase = None

    # 联合中位数、P99 和有效像素比例判断，避免少量亮星或黑边误判。
    decision_source = decision_preview if memory_report["enabled"] else current
    gray = (
        np.mean(decision_source, axis=2)
        if decision_source.ndim == 3 else decision_source
    )
    dark_median = float(np.median(gray))
    dark_p99 = float(np.percentile(gray, 99.0))
    dark_peak = float(np.max(gray))
    nonzero_fraction = float(np.mean(gray > 0))
    # 峰值低才是"欠曝"的本质特征。仅看中位数/p99 会把高动态范围天体误判成
    # 极暗数据：线性数据归一化到峰值后，亮核很小的星系（如 M31）在分位上
    # 确实很低（p99≈0.011），但峰值接近 1.0，说明曝光充分、只是动态范围大。
    is_very_dark = (
        nonzero_fraction >= 0.05
        and dark_peak < 0.5
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
        cfg.phase = 'color'
        if effective_color_mode == 'emission':
            print("\n[Phase 2] 发射星云校色 (暗部基线 + 星色软校准)...")
            current, color_calibration_report = emission_nebula_calibrate(
                current,
                star_balance_strength=cfg.get('star_balance_strength', 0.45),
                oiii_blue_injection=cfg.get('oiii_blue_injection', 0.0),
                return_report=True,
            )
            print("  ✓ 保留 Hα/OIII 信号比例，不做全图灰度世界白平衡")
        elif is_very_dark:
            print("\n[Phase 2] 颜色校准 (极暗数据：仅轻绿噪去除)...")
            current = remove_green_noise(current, strength=0.12)
            print("  ✓ 仅轻绿噪去除(0.12)")
        else:
            print("\n[Phase 2] 颜色校准 (加性黑点 + 星采样白平衡)...")
            current, color_calibration_report = auto_color_calibrate(
                current, return_report=True
            )
            print("  ✓ 背景中性化(加性) + 星采样白平衡 + 绿噪去除")
        save_current('02_color.tif')
    cfg.phase = None

    # Phase 2b: 线性星点色差与 PSF 形变修复 (RGB 亚像素对齐 + 亮星紫晕抑制 + PSF 形变)
    stellar_repair_report = None
    if 'stellar_repair' in steps:
        cfg.phase = 'stellar_repair'
        print("\n[Phase 2b] 线性星点像差/色差诊断与受控修复...")
        if not is_linear_input:
            print("  ⚠️ 非线性输入禁止执行 stellar_repair，已跳过")
        else:
            sr_mode = str(cfg.get('stellar_repair_mode', 'auto'))
            sr_strength = str(cfg.get('stellar_repair_strength_profile', 'balanced'))
            channel_sem = "broadband_rgb" if current.ndim == 3 and current.shape[2] == 3 else "monochrome"
            if effective_color_mode == 'emission' and current.ndim == 3:
                channel_sem = "narrowband_or_mapped_color"

            current, stellar_repair_report = run_stellar_repair(
                current,
                is_linear=is_linear_input,
                channel_semantics=channel_sem,
                mode=sr_mode,
                strength_profile=sr_strength,
                max_runtime_seconds=300.0,
                max_workers=4,
                execute_repairs=True,
                include_preview_image=True,
            )
            sr_preview = (stellar_repair_report or {}).pop('_preview_image', None)
            if sr_preview is not None:
                save_artifact('02b_stellar_repair_preview.tif', sr_preview)
            if (stellar_repair_report or {}).get('applied'):
                save_current('02b_stellar_repaired.tif')
                print(
                    f"  ✓ 修复已生效: "
                    f"{len(stellar_repair_report.get('operations', []))} 项操作成功通过门禁"
                )
            else:
                print(
                    f"  ✓ 诊断完成: 无需修复或未达硬门禁 "
                    f"(status={stellar_repair_report.get('status')})"
                )
            if keep_intermediates and stellar_repair_report:
                sr_report_path = os.path.join(work_dir, '02b_stellar_repair_report.json')
                with open(sr_report_path, 'w', encoding='utf-8') as handle:
                    json.dump(stellar_repair_report, handle, indent=2, ensure_ascii=False)
                artifact_paths['02b_stellar_repair_report.json'] = sr_report_path
    cfg.phase = None

    # Phase 2c: 线性域物理反卷积与 PSF 锐化 (Richardson-Lucy)
    if linear_deconv:
        cfg.phase = 'linear_deconv'
        print("\n[Phase 2c] 线性域物理反卷积与 PSF 锐化 (Richardson-Lucy)...")
        if not is_linear_input:
            print("  ⚠️ 非线性输入跳过线性反卷积")
        else:
            from deconvolution import richardson_lucy_linear
            fwhm_deconv = 3.6
            try:
                gray_src = np.mean(current, axis=2) if current.ndim == 3 else current
                fwhm_est, _, n_used, _ = estimate_fwhm(gray_src)
                if n_used >= 3 and fwhm_est > 1.5:
                    fwhm_deconv = fwhm_est
            except Exception:
                pass
            print(f"  [反卷积] 采用实测 PSF FWHM={fwhm_deconv:.2f}px, 迭代={deconv_iterations}轮")
            current = richardson_lucy_linear(current, fwhm=fwhm_deconv, iterations=deconv_iterations)
            save_current('02c_linear_deconv.tif')
            print("  ✓ 线性反卷积完成，星点与星云高频结构已物理聚焦")
    cfg.phase = None

    # Phase 3: Denoise the current linear stage, after DBE/color/stellar repair.
    if 'pre_denoise' in steps:
        cfg.phase = 'pre_denoise'
        before_denoise = current
        candidate = None
        pre_denoise_report = {'status': 'skipped', 'backend': 'starlet',
                             'review_required': False}
        if external_denoised_full is not None:
            ed_cropped, _ = apply_crop(external_denoised_full.copy(), crop_bounds)
            validation = validate_external_denoise(current, ed_cropped)
            pre_denoise_report.update(backend='external', validation=validation,
                                     status='accepted' if validation['passed'] else 'fallback',
                                     review_required=not validation['passed'])
            if validation['passed']:
                candidate = ed_cropped
            else:
                pre_denoise_report['reason'] = '; '.join(validation['warnings'])
        elif cfg['deepsnr'] != 'never':
            reason = None
            if not is_linear_input:
                reason = 'nonlinear_input'
            elif cfg['deepsnr'] == 'auto':
                if is_star_poi_target(effective_target_type, target_name):
                    reason = 'stars_are_subject'
                elif min(current.shape[:2]) < 512:
                    reason = 'image_below_cli_minimum_size'
                elif np.all(np.isfinite(current)):
                    _, _, _, noise, background = denoise_noise_stats(current)
                    relative_noise = noise / max(abs(background), 1e-8)
                    pre_denoise_report['relative_background_noise'] = relative_noise
                    # ponytail: 5% background noise is a conservative trigger;
                    # calibrate on representative masters before changing this threshold.
                    if noise <= 1e-10 or relative_noise < 0.05:
                        reason = 'low_noise'
            if reason:
                pre_denoise_report['reason'] = reason
            else:
                print("\n[Phase 3] DeepSNR CLI (zero-shadow MTF / float32 FITS)...")
                candidate, cli_report = run_deepsnr(
                    current, work_dir, executable=cfg['deepsnr_path'],
                    timeout=float(cfg['deepsnr_timeout']), strength=float(cfg['deepsnr_strength']),
                    save_prediction=keep_intermediates,
                )
                pre_denoise_report.update(cli_report)
        else:
            pre_denoise_report['reason'] = 'disabled'

        if candidate is not None:
            if 'applied_validation' not in pre_denoise_report:
                candidate, applied = validate_applied_denoise(before_denoise, candidate)
                pre_denoise_report['applied_validation'] = applied
                if candidate is None:
                    pre_denoise_report.update(status='fallback', review_required=True,
                                             reason='; '.join(applied['warnings']))
        if candidate is not None:
            current = candidate
            skip_final_denoise = True
            print("  DeepSNR/external result accepted; final denoise suppressed")
        elif pre_denoise_report.get('reason') == 'low_noise':
            pre_denoise_report.update(status='skipped', fallback_backend=None)
            print('\n[Phase 3] Skipped all preliminary denoise: low noise')
        else:
            backend = 'atrous' if pre_denoise_report['status'] == 'fallback' else cfg.get('denoise_method', 'atrous')
            lum = min(cfg['pre_denoise_lum'], 0.005)
            chroma = min(cfg['pre_denoise_chroma'], 0.01)
            if is_very_dark:
                lum, chroma = 0.002, 0.005
            print(f"\n[Phase 3] Conservative {backend}: L={lum}/C={chroma}")
            if current.ndim == 2:
                current = np.stack([current] * 3, axis=-1)
            cfg.used['pre_denoise'].update(pre_denoise_lum=lum, pre_denoise_chroma=chroma, denoise_method=backend)
            fallback_candidate = tiled(
                lambda tile: denoise_luminance_chroma(
                    tile, lum_strength=lum, chroma_strength=chroma, method=backend), current,
            )
            accepted_fallback, applied = validate_applied_denoise(current, fallback_candidate)
            pre_denoise_report['fallback_backend'] = backend
            if pre_denoise_report.get('applied_validation'):
                pre_denoise_report['rejected_applied_validation'] = pre_denoise_report['applied_validation']
            pre_denoise_report['applied_validation'] = applied
            if accepted_fallback is not None:
                current = accepted_fallback
                pre_denoise_report['fallback_applied'] = True
            else:
                pre_denoise_report.update(fallback_applied=False, review_required=True,
                                         reason='; '.join(applied['warnings']))
                if pre_denoise_report['status'] != 'fallback':
                    pre_denoise_report['status'] = 'rejected'
            if pre_denoise_report['review_required']:
                warnings.append({'code': 'PRE_DENOISE_FALLBACK',
                                 'message': pre_denoise_report.get('reason', 'External denoise rejected')})
        if keep_intermediates:
            if pre_denoise_report.get('prediction_path'):
                prediction_path = pre_denoise_report['prediction_path']
                artifact_paths['03_pre_denoise_prediction.tif'] = prediction_path
                write_result_json_atomic({'capture_metadata': capture_metadata, 'is_linear': True},
                                         prediction_path + '.meta.json')
            save_artifact('03_pre_denoise_before.tif', before_denoise)
            original_rgb = (np.stack([before_denoise] * 3, axis=-1)
                            if before_denoise.ndim == 2 and current.ndim == 3 else before_denoise)
            save_artifact('03_pre_denoise_residual.tif', np.abs(original_rgb - current))
        if current.ndim == 2:
            current = np.stack([current] * 3, axis=-1)
        save_current('03_pre_denoise.tif')
        if keep_intermediates:
            report_path = os.path.join(work_dir, '03_pre_denoise_report.json')
            write_result_json_atomic(pre_denoise_report, report_path)
            artifact_paths['03_pre_denoise_report.json'] = report_path
    cfg.phase = None

    # Phase 4: 去星 (在线性数据上！)
    starless = None
    stars = None
    star_removal_fallback = False
    valid_starless_layer = False
    linear_star_mask_val = None
    external_starless_diagnostics = None
    loaded_processed_stars = None
    layer_source_path = None
    if star_layers:
        starless = current
        if star_layers.get('source_sha256'):
            with open(star_layers['source'], 'rb') as handle:
                if hashlib.file_digest(handle, 'sha256').hexdigest() != star_layers['source_sha256']:
                    raise ValueError('Star layer source was modified after separation')
        arrays = {}
        for role in ('source', 'stars', 'processed_stars'):
            if star_layers.get(role):
                layer, _ = read_image(star_layers[role], force_linear=True)
                if (layer.shape != current.shape or not np.all(np.isfinite(layer))
                        or layer.min() < 0 or layer.max() > 1):
                    raise ValueError(f'Invalid {role} layer geometry or samples')
                arrays[role] = layer
        original_linear = arrays['source']
        stars = arrays['stars']
        loaded_processed_stars = arrays.get('processed_stars')
        layer_source_path = star_layers['source']
        star_layer_empty = is_stars_layer_empty(stars)
        valid_starless_layer = True
        linear_star_metrics = star_layers.get('linear_star_metrics')
        linear_star_mask_val = detect_stars(np.mean(original_linear, axis=-1) if original_linear.ndim == 3
                                            else original_linear, star_threshold=cfg['star_threshold'])
    if 'star_remove' in steps:
        cfg.phase = 'star_remove'
        separation_reference = current.copy()
        save_artifact('04_separation_reference.tif', separation_reference)
        layer_source_path = artifact_paths.get('04_separation_reference.tif')
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

            # ── 域解析：显式 flag > sidecar > linear ──
            # 极暗数据必须走「先 MTF 拉伸 → StarNet2 → 逆 MTF 回线性」，否则 16-bit
            # 量化会把弱色通道压成 0（实测星云区 G 有 93% 像素精确为 0），拉伸后
            # 整片星云变纯红。sidecar 让走完整桥接流程的用户无需手记 midtones。
            resolved_domain = external_starless_domain
            resolved_midtones = external_starless_midtones
            if resolved_domain == 'linear':
                payload_meta = read_starnet_payload_meta(
                    os.path.dirname(os.path.abspath(external_starless)),
                    work_dir,
                )
                if payload_meta and payload_meta.get('domain') == 'mtf':
                    resolved_domain = 'mtf'
                    if resolved_midtones is None:
                        resolved_midtones = payload_meta.get('midtones')
                    print("  ℹ️ sidecar 声明外部无星层为 MTF 域，自动按 mtf 逆变换解析")
            if resolved_domain not in ('linear', 'mtf'):
                raise ValueError(
                    f"未知的外部无星层域: {resolved_domain!r}（只接受 'linear' 或 'mtf'）"
                )

            if resolved_domain == 'mtf':
                m_val = (float(resolved_midtones) if resolved_midtones is not None
                         else derive_mtf_midtones(current))
                starless = inverse_mtf_stretch(starless, midtones=m_val, shadows=0.0)
                print(f"  ✓ 外部无星层 MTF 逆变换回线性域 (midtones={m_val:g})")

            # 校验必须在**同域**之后做：否则 stars_energy 与后续相减都无意义
            verification = verify_external_starless(current, starless)
            if not verification.get("valid"):
                raise ValueError(f"外部无星图真实性校验失败: {verification.get('error')}")
            if not verification.get("stars_extracted_successfully"):
                warnings.append("外部无星图未剥离出显著星点能量，可能与原图完全一致或过度减损")
            scale_info = verification.get("scale_consistency") or {}
            if scale_info.get("status") == "warn":
                warnings.append(scale_info.get("message") or "外部无星层尺度偏离")

            # ── 通道保真回补（degraded 态）──
            # StarNet2 对弱色通道的剥离量系统性高于 R（实测 MTF 域保留率
            # R 0.856 / G 0.703 / B 0.739），使无星层从构造上就偏红。这**不是**
            # 16bit 量化的往返误差（无损对照实测往返 rel 偏差 <0.03%），所以
            # 门禁按「灾难形态」标定的 2.0 阈值检不出（实测 G level_drop=1.51
            # 静默通过）。此处对「退化但未塌缩」的通道做逐通道保真回补。
            rebalance_info = None
            degraded = ((verification.get("channel_integrity") or {})
                        .get("degraded_channels") or [])
            if degraded and cfg.get('starless_channel_rebalance', True):
                rebalance_info = _rebalance_starless_channels(
                    current, starless, cfg, warnings)
                if rebalance_info.get('applied'):
                    starless = rebalance_info.pop('_corrected')
                else:
                    rebalance_info.pop('_corrected', None)

            stars = np.clip(current - starless, 0, 1)
            starless, stars, star_mask = protect_subject_layers(
                current, starless, stars, None, subject_mask_array)
            valid_starless_layer = True
            external_starless_diagnostics = {
                "domain": resolved_domain,
                "midtones": (None if resolved_domain == 'linear'
                             else float(resolved_midtones) if resolved_midtones is not None
                             else float(m_val)),
                "channel_integrity": verification.get("channel_integrity"),
                "scale_consistency": scale_info or None,
                "channel_rebalance": rebalance_info,
            }
            print(f"  ✓ 使用外部无星图: {external_starless} (域={resolved_domain}, 星点能量均值={verification.get('stars_energy_mean'):.6f})")
        else:
            galaxy_center = None
            galaxy_core_radius_px = None
            if effective_target_type == 'galaxy' and cached_target_info is not None:
                tcx = float(cached_target_info['center'][0])
                tcy = float(cached_target_info['center'][1])
                galaxy_center = (tcy, tcx)
                _frac = cfg.get('galaxy_core_protection')
                if _frac:
                    # 核保护半径 = max(配置比例×短边, 弥散检测半径的一半)。
                    # 后者保证保护范围随实际盘面大小走，而不是只按画幅猜。
                    _det = float(cached_target_info.get('radius') or 0.0)
                    galaxy_core_radius_px = max(
                        float(_frac) * min(current.shape[:2]),
                        _det * 0.5,
                        15.0,
                    )
                    print(
                        f"  [去星] 核球保护半径={galaxy_core_radius_px:.0f}px "
                        f"(center=({int(tcx)}, {int(tcy)}))"
                    )

            star_method = 'starnet' if use_starnet else 'inpaint'
            # 相对判据基线读回（v0.1.44）。**必须校验来源一致**：
            # 换了图或换了 target_type 还沿用旧基线，得到的 delta 毫无意义
            # （甚至会给出「这次更健康」的荒谬结论）。
            star_flux_baseline_reference = None
            if star_removal_baseline:
                import json as _json
                try:
                    with open(star_removal_baseline, 'r', encoding='utf-8') as _fh:
                        _prior = _json.load(_fh)
                    _prev_sr = _prior.get('star_removal') or {}
                    _prev_base = _prev_sr.get('star_flux_baseline')
                    # result.json 的 input 字段是**纯路径字符串**，不是 dict：
                    # 写成 dict 会让这里每次都抛 AttributeError 并静默退化成
                    # 「无基线」—— 相对判据永远不会触发，且不报错。
                    _prev_in = _prior.get('input')
                    if isinstance(_prev_in, dict):
                        _prev_path = _prev_in.get('path') or _prev_in.get('file')
                    else:
                        _prev_path = _prev_in
                    _prev_prof = _prev_sr.get('star_removal_profile')
                    _prev_type = (_prev_prof.get('target_type')
                                  if isinstance(_prev_prof, dict) else None)
                    _same_input = bool(_prev_path) and os.path.realpath(
                        _prev_path) == os.path.realpath(input_path)
                    _same_type = (_prev_type is None
                                  or _prev_type == effective_target_type)
                    if _prev_base is not None and _same_input and _same_type:
                        star_flux_baseline_reference = float(_prev_base)
                        print("  [去星] 读回相对判据基线 = %.4f（来源一致）"
                              % star_flux_baseline_reference)
                    else:
                        print("  [去星] 忽略基线文件：%s"
                              % ("缺基线值" if _prev_base is None else
                                 ("输入不一致" if not _same_input
                                  else "target_type 不一致")))
                except Exception as _exc:
                    print("  [去星] 读取基线文件失败，按无基线处理: %s" % _exc)
            starless, stars, star_mask, star_report = separate_stars(
                current, method=star_method,
                star_threshold=cfg['star_threshold'],
                inpaint_radius=5,
                return_report=True,
                starnet_path=starnet_path,
                starnet_stride=starnet_stride,
                star_flux_baseline_reference=star_flux_baseline_reference,
                starnet_domain=starnet_domain,
                starnet_timeout=starnet_timeout,
                galaxy_center=galaxy_center,
                galaxy_core_radius=galaxy_core_radius_px,
                exclude_mask=subject_mask_array,
                target_type=effective_target_type,
                profile=_star_removal_profile_for(cfg, effective_target_type),
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
        # 产物语义：文件名承诺「无星图」就必须真的是无星图。
        # 回退时 starless 是原图副本（star_tools._safe_star_removal_fallback
        # 返回 image.copy()），落进 04_starless_linear.tif 会让后续流程与
        # 人工审查读到一个「名为无星图、实为原图」的文件，也会让离线劣化
        # 标定拿不到健康态对照（拿这对文件构造，orig 与 starless 恒等
        # ⇒ damage ≡ 0，对照组无意义）。故回退时改落专用名并显式标注。
        if star_removal_fallback:
            save_artifact('04_starless_fallback.tif', starless)
            artifact_meta['04_starless_fallback.tif'] = {
                'is_fallback_copy': True,
                'reason': star_report.get('fallback_reason') if 'star_report' in locals() and star_report else None,
                'note': '原图副本，非去星结果；不可用于无星图评估',
            }
            # stars 在回退时是全零层，落盘只会污染下游「星点层是否存在」的判读
            print("  ℹ️ 回退副本落盘为 04_starless_fallback.tif（原图副本，非无星图）")
        else:
            save_artifact('04_starless_linear.tif', starless)
            save_artifact('04_stars_linear.tif', stars)
        star_layer_empty = bool(stars is None or is_stars_layer_empty(stars))
        if star_layer_empty:
            print("  ⚠️ 星点层为空（去星回退），后续星点合成将显式跳过")
        try:
            gray_linear = np.mean(current[..., :3], axis=2) if current.ndim == 3 else current
            # FWHM 必须实测：`fwhm_est` 同时喂给 detect_stars、apply_star_halo_guard
            # 与 INPAINT_FOOTPRINT 的采样环带，硬编码会在采样不足（真实 FWHM ≲ 3px）
            # 的数据上把蒙版面积放大 3 倍。
            #
            # 优先级：star_report（内置去星时的实测值）> 本处实测 > 兜底常量。
            #
            # 测量对象**必须是含星的 gray_linear**，不能用 starless —— 无星层上
            # estimate_fwhm 拟合的是星云纹理（实测同一数据 4.07 → 9.909）。
            fwhm_est = 4.0
            fwhm_source = 'fallback_constant'
            if 'star_report' in locals() and star_report and star_report.get('estimated_fwhm') is not None:
                fwhm_est = float(star_report['estimated_fwhm'])
                fwhm_source = 'star_report'
            else:
                measured_fwhm, _fwhm_std, n_fwhm_used, _n_cand = estimate_fwhm(gray_linear)
                if n_fwhm_used >= 3 and 1.5 < measured_fwhm < 50.0:
                    fwhm_est = float(measured_fwhm)
                    fwhm_source = 'measured'
            linear_star_mask_val = detect_stars(gray_linear, star_threshold=cfg['star_threshold'], fwhm=fwhm_est)
            linear_star_area_ratio = float(np.mean(linear_star_mask_val > 0.5))
            linear_star_metrics = {
                'star_area_ratio': linear_star_area_ratio,
                'estimated_fwhm': float(fwhm_est),
                'fwhm_source': fwhm_source,
                'n_stars_detected': int(star_report.get('n_components_total', 0)) if 'star_report' in locals() and star_report else None,
            }
            print(f"  ✓ 锁定线性阶段星点指标: area_ratio={linear_star_area_ratio:.6f} "
                  f"FWHM={fwhm_est:.2f}px ({fwhm_source})")
        except Exception as e_metric:
            linear_star_metrics = None
            print(f"  [WARN] 无法计算线性阶段星点指标: {e_metric}")
        print(f"  ✓ 阈值={cfg['star_threshold']} (线性数据)")

        if (halo_guard or (effective_target_type == 'emission_nebula' and stars is not None)) and starless is not None and not star_removal_fallback:
            print("  [星晕平整] 检查并平整亮星周围残余彩色晕轮 (Halo Guard)...")
            from star_tools import apply_star_halo_guard
            fwhm_for_halo = fwhm_est if 'fwhm_est' in locals() and fwhm_est else 3.5
            starless = apply_star_halo_guard(starless, stars=stars, fwhm=fwhm_for_halo)
            if subject_mask_array is not None:
                weight = subject_mask_array[..., None] if starless.ndim == 3 else subject_mask_array
                starless = starless * (1.0 - weight) + separation_reference * weight
            save_artifact('04_starless_linear.tif', starless)
            print("  ✓ 亮星星晕守卫完成，背景亮度 L 严格保全")

        # ── 外部神经网络去星桥接建议与载荷导出 (Neural Star Bridge) ──
        env_info = detect_neural_starnet_environment(custom_path=starnet_path)
        star_density_val = None
        if analysis_report and 'starfield' in analysis_report:
            star_density_val = analysis_report['starfield'].get('star_density')
        star_area_ratio_val = linear_star_metrics.get('star_area_ratio') if linear_star_metrics else None

        bridge_rec = evaluate_neural_bridge_recommendation(
            image_shape=current.shape,
            star_density=star_density_val,
            star_area_ratio=star_area_ratio_val,
            star_removal_quality=star_report if 'star_report' in locals() else None,
            target_type=effective_target_type,
            env_info=env_info,
        )

        should_export_payload = bool(export_starnet_payload)
        payload_exported_path = None
        payload_midtones = None
        if should_export_payload:
            payload_exported_path, payload_midtones = export_starnet_payload_for_domain(
                current, work_dir, domain=export_starnet_payload_domain,
            )
            artifact_paths[os.path.basename(payload_exported_path)] = payload_exported_path
            print(
                f"  ✓ 已导出 StarNet2 16-bit 载荷（域={export_starnet_payload_domain}"
                f"{'' if payload_midtones is None else f', midtones={payload_midtones:g}'}）: "
                f"{payload_exported_path}"
            )

        bridge_instructions = build_bridge_run_instructions(
            payload_path=payload_exported_path or os.path.join(work_dir, "03_pre_denoise.tif"),
            output_starless_path=os.path.join(work_dir, "04_starless_external.tif"),
            input_fits_path=input_path,
            final_output_path=output_path,
            env_info=env_info,
            work_dir=work_dir,
            domain=export_starnet_payload_domain,
            stride=starnet_stride,
            midtones=payload_midtones,
        )

        should_show_card = False
        if starnet_recommendation == 'always':
            should_show_card = True
        elif starnet_recommendation == 'auto':
            if should_export_payload or (bridge_rec.get('recommended') and not external_starless and not use_starnet):
                should_show_card = True

        if should_show_card:
            print("\n" + format_bridge_recommendation_card(bridge_rec, bridge_instructions) + "\n")

        neural_bridge_report = {
            "environment": env_info,
            "recommendation": bridge_rec,
            "payload_exported": bool(should_export_payload),
            "payload_path": payload_exported_path,
            "instructions": bridge_instructions,
        }
    cfg.phase = None

    if neural_bridge_report is None and export_starnet_payload:
        env_info = detect_neural_starnet_environment(custom_path=starnet_path)
        payload_path, payload_midtones = export_starnet_payload_for_domain(
            current, work_dir, domain=export_starnet_payload_domain,
        )
        artifact_paths[os.path.basename(payload_path)] = payload_path
        bridge_instructions = build_bridge_run_instructions(
            payload_path=payload_path,
            output_starless_path=os.path.join(work_dir, "04_starless_external.tif"),
            input_fits_path=input_path,
            final_output_path=output_path,
            env_info=env_info,
            work_dir=work_dir,
            domain=export_starnet_payload_domain,
            stride=starnet_stride,
            midtones=payload_midtones,
        )
        bridge_rec = evaluate_neural_bridge_recommendation(
            image_shape=current.shape,
            target_type=effective_target_type,
            env_info=env_info,
        )
        if starnet_recommendation != 'never':
            print("\n" + format_bridge_recommendation_card(bridge_rec, bridge_instructions) + "\n")
        neural_bridge_report = {
            "environment": env_info,
            "recommendation": bridge_rec,
            "payload_exported": True,
            "payload_path": payload_path,
            "instructions": bridge_instructions,
        }

    # Phase 5: 拉伸 (线性→非线性, 对去星图像)
    stretch_target = starless if starless is not None else current
    if 'star_remove' in steps and 'stretch' not in steps:
        current = stretch_target
    if 'stretch' in steps:
        cfg.phase = 'stretch'
        _pre_stretch_input = stretch_target.copy()
        # Explicit caller choices precede target policy and analysis advice.
        override_method = (override_params or {}).get('stretch_method', 'auto')
        effective_method = stretch_method if stretch_method != 'auto' else override_method
        if effective_method == 'auto':
            if stretch_policy_version >= 2:
                effective_method, _ = recommend_stretch(effective_target_type, target_name, effective_color_mode)
                if star_removal_fallback and effective_method == 'emission':
                    effective_method = 'masked_ghs_standard'
            else:
                if effective_color_mode == 'emission' and star_removal_fallback:
                    effective_method = 'masked_ghs'
                    print(
                        "[自适应] 去星回退且保留密集星场，"
                        "改用 masked_ghs 保护星核与背景"
                    )
                elif effective_color_mode == 'emission':
                    effective_method = 'emission'
                elif is_very_dark:
                    effective_method = 'very_dark'
                elif effective_target_type in ('galaxy', 'emission_nebula', 'planetary_nebula'):
                    effective_method = 'masked_ghs'
                    print(f"[自适应] 识别为高动态范围天体 ({effective_target_type})，自动升级为 masked_ghs 拉伸以保护高光核心")
                elif cfg.get('stretch_method') not in (None, 'auto'):
                    effective_method = (analysis_report or {}).get('recommendations', {}).get('stretch', {}).get('legacy_method', cfg['stretch_method'])
                else:
                    effective_method = 'masked'
        # Layer validity, not ndarray identity, defines the starless source.
        stretching_starless = valid_starless_layer and starless is not None and is_linear_input
        automatic_method = stretch_method == 'auto' and override_method == 'auto'
        if stretching_starless and automatic_method and (
                stretch_policy_version >= 3 or effective_method in (
                    'ghs_standard', 'masked_ghs_standard')):
            effective_method = 'background_compressed'
        new_stretch_path = effective_method in (
            'pi_mtf', 'ghs_standard', 'masked_ghs_standard', 'background_compressed') or (
            stretch_policy_version >= 2 and automatic_method)
        if new_stretch_path:
            _, default_bg = recommend_stretch(effective_target_type, target_name, effective_color_mode)
            cfg['target_bg'] = (override_params or {}).get('target_bg', default_bg)
        elif stretch_policy_version == 1 and preset == 'adaptive' and 'target_bg' not in (override_params or {}):
            cfg['target_bg'] = (analysis_report or {}).get('recommendations', {}).get('stretch', {}).get('legacy_target_bg', cfg['target_bg'])
        resolved_stretch_method = effective_method
        stretch_diagnostics = {'requested_method': stretch_method, 'selected_method': effective_method,
                               'policy_version': stretch_policy_version,
                               'algorithm': 'legacy_sinh' if effective_method in ('ghs', 'masked_ghs') else effective_method}

        # 2. 执行拉伸分发
        print(f"\n[Phase 5] 拉伸 (亮度通道 {effective_method} 拉伸)...")
        if effective_method == 'background_compressed':
            if not is_linear_input or is_star_poi_target(effective_target_type, target_name):
                raise ValueError('Background-compressed stretch requires a linear non-stellar subject')
            if stretch_policy_version >= 3:
                cfg.setdefault('stretch_subject_midtones',
                               .20 if effective_target_type in ('reflection_nebula', 'dark_nebula') else .25)
            stretch_target, applied = background_compressed_stretch(
                stretch_target,
                target_bg=cfg.get('target_bg', 0.035),
                ceiling=1.0 - cfg.get('stretch_headroom', 0.05),
                protect=cfg.get('ghs_protect_strength', 0.85),
                mask_sigma=1.0 if effective_target_type == 'planetary_nebula' else 5.0,
                subject_midtones=cfg.get('stretch_subject_midtones'),
                target_type=effective_target_type,
            )
            stretch_diagnostics.update(applied)
            resolved_stretch_method = applied['method']
            if applied.get('fallback_reason'):
                stretch_recovery = 'starless_anchor_unavailable_pi_mtf'
            stretch_diagnostics['unused_params'] = [
                key for key in cfg
                if key in ('stretch_factor', 'stretch_gamma', 'shadow_pctl',
                           'highlight_pctl', 'ghs_c', 'ghs_standard_log_d')
            ]
            stretch_diagnostics['unused_legacy_params'] = [
                key for key in cfg if key.startswith('ghs_')
                and not key.startswith('ghs_standard_')
                and not (key == 'ghs_protect_strength')
            ]
            print(f"  ✓ Starless: method={resolved_stretch_method} "
                  f"background={applied['actual_background']:.4f} "
                  f"subject={applied.get('actual_subject')}")
        elif effective_method in ('pi_mtf', 'ghs_standard', 'masked_ghs_standard'):
            stretch_target, applied = render_protected_stretch(stretch_target, effective_method, cfg)
            stretch_diagnostics.update(applied)
            if 'log_d' in applied:
                for key in ('log_d', 'b', 'sp', 'lp', 'hp'):
                    cfg['ghs_standard_' + key] = applied[key]
            stretch_diagnostics['unused_params'] = [key for key in cfg
                if key in ('stretch_factor', 'stretch_gamma', 'shadow_pctl', 'highlight_pctl', 'ghs_c')]
            stretch_diagnostics['unused_legacy_params'] = [
                key for key in cfg if key.startswith('ghs_')
                and not key.startswith('ghs_standard_')
                and not (key == 'ghs_protect_strength' and effective_method == 'masked_ghs_standard')]
            if effective_method == 'pi_mtf':
                stretch_diagnostics['unused_standard_params'] = [key for key in cfg if key.startswith('ghs_standard_')]
        elif effective_method == 'emission':
            stretch_diagnostics['unused_params'] = ['stretch_factor'] + [
                key for key in cfg if key.startswith('ghs_')]
            # headroom 必须与 pi_mtf 走 render_protected_stretch 时的同一份高光余量
            # 预算（默认 0.05）。缺它时 emission 峰值触顶、R 通道出现成片裁切，
            # CHANNEL_CLIPPING 判 failed 后整条链回退到 pi_mtf —— 首选方法永远用不上。
            # 用 cfg.get 而非字面量，使 ExecutionConfig 记录该参数已被消费。
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='emission',
                shadow_pctl=cfg.get('shadow_pctl', 1.0),
                highlight_pctl=cfg.get('highlight_pctl', 99.94),
                gamma=cfg.get('stretch_gamma', 0.43),
                target_bg=cfg.get('target_bg', 0.08),
                min_p99=cfg.get('stretch_min_p99', 0.5),
                headroom=cfg.get('stretch_headroom', 0.05),
            )
            print(
                f"  ✓ Emission stretch shadow={cfg.get('shadow_pctl', 1.0)} "
                f"highlight={cfg.get('highlight_pctl', 99.94)} "
                f"gamma={cfg.get('stretch_gamma', 0.43)} "
                f"target_bg={cfg.get('target_bg', 0.08)} "
                f"min_p99={cfg.get('stretch_min_p99', 0.5)} "
                f"headroom={cfg.get('stretch_headroom', 0.05)}"
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
            lp_val, hp_val = resolve_ghs_lphp(cfg)
            lp_eff = 0.0 if lp_val is None else lp_val
            hp_eff = 1.0 if hp_val is None else hp_val
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='ghs',
                sp=sp_val,
                b=b_val,
                c=cfg.get('ghs_c', 0.0),
                lp=lp_eff,
                hp=hp_eff,
            )
            print(f"  ✓ GHS stretch sp={sp_val} b={b_val:.2f} c={cfg.get('ghs_c', 0.0)} lp={lp_eff} hp={hp_eff}")
        elif effective_method == 'masked_ghs':
            sp_val = cfg.get('ghs_sp', -1)
            b_val = resolve_ghs_b(cfg)
            prot_val = cfg.get('ghs_protect_strength', 0.5)
            if is_m42_target(target_name):
                prot_val = max(prot_val, 0.75)
            hl_pctl = cfg.get('highlight_pctl', 99.9)
            if effective_target_type in ('galaxy', 'globular_cluster', 'planetary_nebula') or is_m42_target(target_name):
                hl_pctl = 100.0
            lp_val, hp_val = resolve_ghs_lphp(cfg)
            # 内层 GHS 切线保护（归一化 [0,1] 空间），与外层 lp/hp 语义不同，故独立取键。
            # ghs_c 与裸 ghs 分支复用同一键名（见上方 ghs 分支）。
            c_val = cfg.get('ghs_c', 0.0)
            plp_val = cfg.get('ghs_protect_lp')
            php_val = cfg.get('ghs_protect_hp')
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method='masked_ghs',
                sp=sp_val,
                b=b_val,
                protect_strength=prot_val,
                target_bg=cfg.get('target_bg', 0.08),
                shadow_pctl=cfg.get('shadow_pctl', 0.0),
                highlight_pctl=hl_pctl,
                gamma=cfg.get('stretch_gamma', 0.45),
                lp=lp_val,
                hp=hp_val,
                protect_lp=plp_val,
                protect_hp=php_val,
                c=c_val,
            )
            print(
                f"  ✓ Masked GHS stretch sp={sp_val} b={b_val:.2f} "
                f"protect={prot_val} target_bg={cfg.get('target_bg', 0.08)} "
                f"shadow={cfg.get('shadow_pctl', 0.0)} "
                f"highlight={hl_pctl} "
                f"gamma={cfg.get('stretch_gamma', 0.45)}"
            )
            if lp_val is not None or hp_val is not None:
                print(f"    ↳ 显式锚点 lp={lp_val} hp={hp_val}（覆盖百分位推导）")
            if plp_val is not None or php_val is not None or c_val:
                print(
                    f"    ↳ 内层保护 protect_lp={plp_val} protect_hp={php_val} c={c_val}"
                )
        else:
            method_kwargs = {}
            if effective_method == 'masked':
                method_kwargs = {'factor': cfg['stretch_factor'], 'target_bg': cfg.get('target_bg', 0.08)}
            elif effective_method == 'mtf':
                method_kwargs = {'midtones': cfg.get('midtones', 0.3)}
            elif effective_method == 'emission':
                # `emission_stretch` 接受 shadow/highlight_pctl、gamma、target_bg。
                # 此前这里落到空 kwargs，导致 **cfg 里的 stretch_gamma / target_bg /
                # shadow_pctl / highlight_pctl 被静默丢弃**，实际一直跑该函数的默认值
                # （gamma=0.33 / target_bg=0.08）—— 分析报告算出的曲线参数完全没生效。
                # 实测某极暗母版：传入 cfg 值后拉伸层 p75 由 0.307 降到 0.280。
                method_kwargs = {
                    'shadow_pctl': cfg.get('shadow_pctl', 0.5),
                    'highlight_pctl': cfg.get('highlight_pctl', 99.9),
                    'gamma': cfg.get('stretch_gamma', 0.33),
                    'target_bg': cfg.get('target_bg', 0.08),
                }
            elif effective_method == 'very_dark':
                method_kwargs = {
                    'factor': cfg['stretch_factor'],
                    'gamma': cfg.get('stretch_gamma', 0.45),
                    'shadow_pctl': cfg.get('shadow_pctl', 0.1),
                    'highlight_pctl': cfg.get('highlight_pctl', 99.5),
                    'target_bg': cfg.get('target_bg', 0.12),
                }
            elif effective_method in ('arcsinh', 'luminance_arcsinh'):
                method_kwargs = {'factor': cfg['stretch_factor']}
                effective_method = 'arcsinh'
            stretch_target = apply_luminance_stretch(
                stretch_target,
                method=effective_method,
                **method_kwargs
            )
            print(f"  ✓ {effective_method} stretch kwargs={method_kwargs}")

        from artifact_gates import evaluate_star_artifact_gates
        display_reference, reference_curve = render_protected_stretch(_pre_stretch_input, 'pi_mtf', cfg)
        display_reference_small = downsample_for_gates(display_reference)
        # 保真度门禁的掩膜必须由**降采样后**的参考图重建，才能与降采样后的候选
        # 同 shape。用全分辨率参考算掩膜再套降采样候选会 shape 不匹配。
        fidelity_background_mask = None
        fidelity_signal_mask = None
        try:
            from fidelity_gates import (
                _background_mask as _fidelity_background_mask,
                _resolve_signal_mask as _fidelity_resolve_signal_mask,
            )
            fidelity_background_mask = _fidelity_background_mask(display_reference_small)
            fidelity_signal_mask = _fidelity_resolve_signal_mask(
                display_reference_small, subject_mask_array, fidelity_background_mask)
        except Exception as exc:
            print(f"  [WARN] 保真度掩膜构建失败：{exc}")

        def stretch_fidelity(candidate):
            try:
                from fidelity_gates import evaluate_fidelity_gates
                report = evaluate_fidelity_gates(
                    downsample_for_gates(candidate), display_reference_small,
                    steps=['stretch'], target_type=effective_target_type,
                    reference_comparable=True, display_reference=True,
                    background_mask=fidelity_background_mask, signal_mask=fidelity_signal_mask,
                    geometry='native')
                report['context']['measurement_geometry'] = {
                    'max_side': GATE_PREVIEW_MAX_SIDE,
                    'shape': list(np.shape(display_reference_small)),
                    'note': 'calibration and runtime share this downsampled geometry',
                }
                return report
            except Exception as exc:
                print(f"  [WARN] Fidelity gate evaluation failed: {exc}")
                return {'schema': 'fidelity_gates/1.0', 'status': 'error',
                        'summary': f'Fidelity gate evaluation failed: {exc}', 'gates': []}

        def stretch_checks(candidate):
            collapsed = is_stretch_collapsed(_pre_stretch_input, candidate, stellar_subject=is_star_poi_target(effective_target_type, target_name))[0]
            clip = check_channel_clipping(candidate)
            plateau = check_core_plateau(candidate, _pre_stretch_input, subject_mask_array)
            display = evaluate_star_artifact_gates(display_reference_small, downsample_for_gates(candidate), steps=['stretch'], display_reference=True)
            display['context'].update(measurement_domain='display', reference_type='fixed_mtf')
            display_failed = any(
                (g['status'] == 'failed' or (g['code'] == 'STAR_BLOAT' and g['status'] == 'warning'))
                and not (g['code'] == 'STAR_RINGING'
                         and not g.get('evidence', {}).get('reference_comparable', False))
                for g in display['gates'])
            fidelity_failed = False
            if effective_method == 'background_compressed':
                display['fidelity_gates'] = stretch_fidelity(candidate)
                fidelity_failed = any(
                    gate['status'] in ('warning', 'failed') and gate.get('escalate', True)
                    for gate in display['fidelity_gates']['gates'])
            return (collapsed or clip['escalate'] or plateau['escalate']
                    or display_failed or fidelity_failed), clip, plateau, display

        rejected, initial_clip, initial_plateau, final_display = stretch_checks(stretch_target)
        if (rejected and resolved_stretch_method == 'background_compressed'
                and cfg.get('stretch_subject_midtones') is not None):
            initial_report = dict(stretch_diagnostics)
            initial_rejection = {'channel_clipping': initial_clip, 'core_plateau': initial_plateau,
                                 'display_gates': final_display, 'curve': initial_report}
            save_artifact('05_rejected_stretch.tif', stretch_target)
            requested_midtones = cfg['stretch_subject_midtones']
            retry_midtones = cfg['target_bg'] + .5 * (requested_midtones - cfg['target_bg'])
            retried, retry_report = background_compressed_stretch(
                _pre_stretch_input, target_bg=cfg['target_bg'],
                ceiling=1 - cfg.get('stretch_headroom', .05),
                mask_sigma=1.0 if effective_target_type == 'planetary_nebula' else 5.0,
                subject_midtones=retry_midtones, target_type=effective_target_type)
            rejected, initial_clip, initial_plateau, final_display = stretch_checks(retried)
            stretch_diagnostics.update(retry_report)
            stretch_diagnostics.update(
                rejected_initial=initial_rejection,
                requested_subject_midtones=requested_midtones,
                subject_retry={'midtones': retry_midtones, 'rejected': rejected,
                               'curve': retry_report, 'channel_clipping': initial_clip,
                               'core_plateau': initial_plateau, 'display_gates': final_display})
            stretch_target = retried
            resolved_stretch_method = retry_report['method']
            if not rejected:
                cfg['stretch_subject_midtones'] = retry_midtones
                stretch_recovery = ('starless_anchor_unavailable_pi_mtf'
                                    if retry_report.get('fallback_reason') else 'reduced_subject_midtones')
        if rejected:
            save_artifact('05_rejected_recovery.tif' if stretch_diagnostics.get('subject_retry')
                          else '05_rejected_stretch.tif', stretch_target)
            stretch_diagnostics.setdefault('rejected_initial', {
                'channel_clipping': initial_clip, 'core_plateau': initial_plateau,
                'display_gates': final_display, 'curve': dict(stretch_diagnostics)})
            if new_stretch_path:
                recovered, recovery_report = render_protected_stretch(_pre_stretch_input, 'pi_mtf', cfg)
                still_rejected, recovered_clip, recovered_plateau, recovered_display = stretch_checks(recovered)
                if not still_rejected:
                    stretch_target = recovered
                    resolved_stretch_method = 'pi_mtf'
                    stretch_recovery = 'conservative_pi_mtf'
                    if effective_method == 'background_compressed':
                        attempt = dict(stretch_diagnostics)
                        stretch_diagnostics = {
                            **recovery_report,
                            **{key: attempt[key] for key in (
                                'requested_method', 'selected_method', 'policy_version',
                                'rejected_initial', 'subject_retry') if key in attempt},
                            'starless_anchor_attempt': attempt,
                        }
                    else:
                        stretch_diagnostics.update(recovery_report)
                    final_display = recovered_display
                    stretch_diagnostics['unused_standard_params'] = [key for key in cfg if key.startswith('ghs_standard_')]
                else:
                    save_artifact('05_rejected_recovery.tif', recovered)
                    stretch_recovery = 'failed'
            else:
                recovered = apply_luminance_stretch(
                    _pre_stretch_input,
                    method='masked',
                    factor=cfg['stretch_factor'],
                    target_bg=cfg.get('target_bg', 0.08),
                )
                _, _, recovered_health = is_stretch_collapsed(
                    _pre_stretch_input, recovered, stellar_subject=is_star_poi_target(effective_target_type, target_name)
                )
                still_rejected, recovered_clip, _, recovered_display = stretch_checks(recovered)
                if not still_rejected:
                    stretch_target = recovered
                    resolved_stretch_method = 'masked'
                    stretch_recovery = 'masked'
                    final_display = recovered_display
                    print(
                        f"  ✓ 已恢复: span={recovered_health['span']:.4f} "
                        f"core_ratio={recovered_health['core_ratio']:.2f}"
                    )
                else:
                    recovered = apply_luminance_stretch(
                        _pre_stretch_input, method='masked_ghs', sp=cfg.get('ghs_sp', -1),
                        b=min(resolve_ghs_b(cfg), 6.0), gamma=0.4,
                        protect_strength=max(cfg.get('ghs_protect_strength', 0.5), 0.85),
                        target_bg=cfg.get('target_bg', 0.08), shadow_pctl=0.0, highlight_pctl=100.0)
                    still_rejected, recovered_clip, _, recovered_display = stretch_checks(recovered)
                    if not still_rejected:
                        stretch_target = recovered
                        resolved_stretch_method = 'masked_ghs'
                        stretch_recovery = 'conservative_masked_ghs'
                        final_display = recovered_display
                        cfg.update(ghs_b=min(resolve_ghs_b(cfg), 6.0), stretch_gamma=0.4,
                                   ghs_protect_strength=max(cfg.get('ghs_protect_strength', 0.5), 0.85),
                                   shadow_pctl=0.0, highlight_pctl=100.0)
                    else:
                        stretch_recovery = 'failed'

        if stretch_recovery == 'failed':
            stretch_target = _pre_stretch_input
            # 位置运算改走 stage_registry.step_order（可选步骤返回 None，
            # 排在 stretch 之后视为「拉伸之后」，与旧 ALL_STEPS.index 行为一致
            # —— 旧实现在 steps 含孤儿步骤时会ValueError）。
            _stretch_at = step_order('stretch')
            def _after_stretch(step):
                order = step_order(step)
                return _stretch_at if order is None else order >= _stretch_at
            stretch_diagnostics['skipped_after_failure'] = [
                step for step in steps if _after_stretch(step)]
            # ──失败处置策略（failure_policy）──────────────────────────
            # 三态而非「一律截断」：此前无论失败原因如何，拉伸之后的步骤
            # 全部丢弃且不出图，用户连线性域的中间产物都拿不到。
            #   auto_fallback  继续跑完（现状语义）
            #   preserve_review 保留全部中间产物 + 标记 degraded，可审但不阻断
            #   stop           硬失败，不出图
            _action = resolve_failure_actions(
                'stretch',
                requested=cfg.get('stretch_failure_action'),
                overrides={k[7:]: v for k, v in cfg.items()
                            if k.startswith('failure_') and k.endswith('_action')})
            stretch_diagnostics['failure_action'] = _action
            if _action == 'stop':
                # 严格模式：不产出成片。仍写诊断，让用户知道失败原因。
                stretch_diagnostics['failure_stop'] = (
                    '拉伸链全部候选被拒，按 stop 处置不交付成片；'
                    '线性域中间产物已保留供审查')
                steps = []
                stretch_diagnostics['skipped_after_failure'] = list(
                    stretch_diagnostics['skipped_after_failure'])
            else:
                # auto_fallback / preserve_review 都继续跑完后续步骤。
                # 两者的差别在于**是否保留现场并标记 degraded**：
                # preserve_review 强制 keep_intermediates 语义（产物已落盘
                # 的话本就已保留），并把 degradation 写进 diagnostics。
                steps = [step for step in steps if not _after_stretch(step)]
                if _action == 'preserve_review':
                    stretch_diagnostics['preserve_review'] = {
                        'reason': '拉伸链全部候选被拒，保留线性域中间产物供复核',
                        'retained_steps': list(steps),
                        'artifact_domain': 'linear',
                    }
            resolved_stretch_method = 'rejected'
        final_stretch_clip = check_channel_clipping(stretch_target)
        final_stretch_clip['stage'] = 'stretch'
        if stretch_recovery != 'failed':
            channel_clipping_gates.append(final_stretch_clip)
            save_artifact('05_display_reference.tif', display_reference)
            stretch_diagnostics['applied_method'] = resolved_stretch_method
            stretch_diagnostics['algorithm'] = ('legacy_sinh' if resolved_stretch_method in ('ghs', 'masked_ghs')
                                                else 'standard_ghs' if resolved_stretch_method in ('ghs_standard', 'masked_ghs_standard')
                                                else 'linked_mtf' if resolved_stretch_method == 'pi_mtf' else resolved_stretch_method)
            if effective_method in ('ghs', 'masked_ghs') and stretch_recovery:
                stretch_diagnostics['algorithm'] = 'legacy_sinh' if resolved_stretch_method == 'masked_ghs' else resolved_stretch_method
            stretch_diagnostics['display_reference'] = {
                'path': artifact_paths.get('05_display_reference.tif'), 'curve': reference_curve,
                'measurement_domain': 'display', 'source': input_path,
                'source_sha256': hashlib.sha256(np.ascontiguousarray(_pre_stretch_input).tobytes()).hexdigest()}
            stretch_diagnostics['display_gates'] = final_display
            stretch_diagnostics['core_plateau'] = check_core_plateau(stretch_target, _pre_stretch_input, subject_mask_array)
            # Reuse the accepted candidate's checks; legacy curves are checked here.
            final_fidelity = (final_display.get('fidelity_gates')
                              or stretch_fidelity(stretch_target))
            stretch_diagnostics['fidelity_gates'] = final_fidelity
            fidelity_gates = final_fidelity['gates']
        current = stretch_target
        save_artifact('05_stretched_starless.tif', current,
                      is_linear=is_linear_input if stretch_recovery == 'failed' else False)
    cfg.phase = None

    # ══════════════════════════════════════════════════════════════
    # 星点独立处理 (Star Processing, 并行)
    # ══════════════════════════════════════════════════════════════

    processed_stars = loaded_processed_stars if loaded_processed_stars is not None else stars
    if 'star_process' in steps and stars is not None and stars.max() > 0:
        cfg.phase = 'star_process'
        processed_stars = stars
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

        # S2b: 星点保亮度饱和补偿
        #
        # 插在 S2 之后、S3 之前：S1 的 arcsinh 压色度、S2 的 SCNR 又把 Lab a 推向 0，
        # 两者叠加是**最大损耗源**；而 S3 的 apply_curves 用亮度增益乘回 RGB（保比例、
        # 不改饱和），缩星只做腐蚀——所以在 S2 之后一次补回，S3 不会撤销它。
        star_sat = float(cfg.get('star_saturation', 1.08))
        if processed_stars.ndim >= 3 and star_sat != 1.0:
            print(f"\n[Star S2b] 星点保亮度饱和补偿 (×{star_sat})...")
            processed_stars = saturate_stars(processed_stars, saturation=star_sat)
            save_artifact('05b2_star_saturation.tif', processed_stars)
            print(f"  ✓ saturation={star_sat}")

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
        if subject_mask_array is not None:
            processed_stars *= (1.0 - subject_mask_array[..., None] if processed_stars.ndim == 3
                                else 1.0 - subject_mask_array)
        save_artifact('05c_star_final.tif', processed_stars)
        print(f"  ✓ 缩星reduction={cfg['star_reduction']}")
    cfg.phase = None

    # ══════════════════════════════════════════════════════════════
    # 非线性阶段 (Non-Linear Phase)
    # ══════════════════════════════════════════════════════════════

    current_nl = stretch_target if 'stretch' in steps else current

    # Phase 6: 星云细节增强
    if 'enhance' in steps:
        cfg.phase = 'enhance'
        print("\n─ 非线性阶段 ─")
        print("[Phase 6] 星云细节增强 (高光保护 HDR + CLAHE 局部对比)...")
        from enhance import (apply_clahe, decide_enhance_mode, measure_texture_snr,
                             preview_clahe_hf_amplification,
                             CLAHE_MIN_TEXTURE_SNR, CLAHE_MAX_HF_AMPLIFICATION)
        hdr_strength = cfg.get('hdr_strength', 0.35)

        # HDR 与 CLAHE 二选一：外部经验「HDRMT 与 LHE 同时用太过头」。
        # 信号**现算**而不是用 post_health——后者只在 'stretch' in steps 时定义，
        # 缺 stretch 步会 NameError。默认分支仍是 both（= 历史行为）。
        _g = (
            np.mean(current_nl[..., :3], axis=2)
            if current_nl.ndim == 3 else current_nl
        )
        _p50 = float(np.median(_g))
        _p99 = float(np.percentile(_g, 99.0))
        enhance_signals = {
            "highlight_clip_ratio": float(np.mean(_g >= 0.995)),
            "p50": _p50,
            "p99": _p99,
            "span": _p99 - _p50,
        }
        # 噪声守卫：星云区纹理是否真的比背景噪声地板多出结构。
        # 实测某极暗母版该比值 1.24（噪声主导），跑 CLAHE 后星云区高通能量被放大
        # 5.4×，成片出现灰色蜂窝斑块；而干净输入的比值 ≥2.3。
        _texture_snr = measure_texture_snr(_g)
        if _texture_snr is not None:
            enhance_signals["texture_snr"] = _texture_snr
        enhance_mode = cfg.get('enhance_mode', 'auto')
        guard_fired = False
        if enhance_mode == 'auto':
            # 先算「不带噪声守卫」的基准模式，用于报告守卫是否真的改变了决策
            base_signals = {k: v for k, v in enhance_signals.items()
                            if k != 'texture_snr'}
            base_mode = decide_enhance_mode(base_signals, effective_target_type)
            enhance_mode = decide_enhance_mode(enhance_signals, effective_target_type)
            guard_fired = enhance_mode != base_mode
        if guard_fired:
            print(
                f"  ⚠️ 星云区纹理/背景噪声 = {_texture_snr:.2f} "
                f"< {CLAHE_MIN_TEXTURE_SNR}，判定为噪声主导："
                f"已把增强模式从 {base_mode} 降为 {enhance_mode}（局部均衡只会放大噪声成蜂窝斑块）"
            )

        if enhance_mode in ('both', 'hdr_only'):
            current_nl, hdr_report = protected_hdr_compress(
                current_nl,
                strength=hdr_strength,
                knee_percentile=cfg.get('hdr_knee_percentile', 85.0),
            )
            save_current('06a_hdr.tif', img=current_nl)
        else:
            hdr_report = {
                "applied": False,
                "strength": hdr_strength,
                "skipped_reason": enhance_mode,
            }

        # 按图像短边的 6% 计算 CLAHE kernel，避免固定像素值导致尺度不匹配
        h, w = current_nl.shape[:2]
        kernel_size = int(min(h, w) * 0.06)
        kernel_size = max(64, kernel_size - (kernel_size % 2))  # 确保偶数且 >=64
        # 发射星云使用更低的 clip_limit 避免弥散区域伪影
        clip_limit = 0.003 if effective_color_mode == 'emission' else 0.004
        clahe_mask_gamma = cfg.get('clahe_mask_gamma', 2.0)
        clahe_hf_amplification = None
        if enhance_mode in ('both', 'clahe_only'):
            # 实测守卫：CLAHE 的 clip_limit 在窄直方图上会饱和，无法靠调小它减弱效果，
            # 只能二选一。因此在下采样预览上实测它会把星云区高频放大多少倍。
            clahe_hf_amplification = preview_clahe_hf_amplification(
                current_nl, clip_limit, kernel_size,
                mask_gamma=clahe_mask_gamma,
                mask_high_pctl=cfg.get('clahe_mask_high_pctl', 95.0),
            )
            if (clahe_hf_amplification is not None
                    and clahe_hf_amplification > CLAHE_MAX_HF_AMPLIFICATION):
                print(
                    f"  ⚠️ CLAHE 会把星云区高频放大 {clahe_hf_amplification:.2f}× "
                    f"> {CLAHE_MAX_HF_AMPLIFICATION}，判定为过度增强：跳过 CLAHE"
                )
                enhance_mode = 'hdr_only' if enhance_mode == 'both' else 'none'
            else:
                current_nl = apply_clahe(
                    current_nl, clip_limit=clip_limit, kernel_size=kernel_size,
                    mask_gamma=clahe_mask_gamma,
                    mask_high_pctl=cfg.get('clahe_mask_high_pctl', 95.0),
                )
        hdr_report.update({"enhance_mode": enhance_mode, **enhance_signals})
        if clahe_hf_amplification is not None:
            hdr_report["clahe_hf_amplification"] = round(clahe_hf_amplification, 4)
        current = current_nl
        save_current('06_clahe_enhance.tif')
        print(
            f"  ✓ HDR strength={hdr_strength} "
            + (
                f"knee={hdr_report['knee']:.3f} "
                f"P99 {hdr_report['p99_before']:.3f}→{hdr_report['p99_after']:.3f}; "
                if hdr_report.get('applied', True) else "已跳过; "
            )
            + f"CLAHE clip={clip_limit}, kernel={kernel_size}, "
              f"mask_gamma={clahe_mask_gamma}; 模式={enhance_mode}"
        )
    cfg.phase = None

    # Phase 7: 锐化
    if 'sharpen' in steps:
        cfg.phase = 'sharpen'
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
    cfg.phase = None

    # Phase 8: 颜色调整 (Vectra-like)
    if 'final_color' in steps:
        cfg.phase = 'final_color'
        before_final_color = current_nl.copy()
        print("\n[Phase 8] 颜色调整 (Vectra-like)...")
        if current_nl.ndim == 2:
            current_nl = np.stack([current_nl] * 3, axis=-1)

        # 调色板假彩色映射 (AstroBOH Dual-band Palette Mapping)
        if palette != 'none':
            print(f"  [调色板] 执行双窄带假彩色映射: {palette.upper()}...")
            from palette_tools import apply_dualband_palette
            palette_star_mask = None
            if not valid_starless_layer:
                if linear_star_mask_val is None:
                    gray_linear = (
                        np.mean(original_linear[..., :3], axis=2)
                        if original_linear.ndim == 3 else original_linear
                    )
                    linear_star_mask_val = detect_stars(
                        gray_linear, star_threshold=cfg['star_threshold']
                    )
                palette_star_mask = linear_star_mask_val
                print("  [调色板] 单图流程：挂载星点掩膜软羽化保护恒星真彩色")
            else:
                print("  [调色板] 多图层流程：当前为纯净无星底图，解耦假彩色调色板与独立星点层")
            current_nl = apply_dualband_palette(
                current_nl, palette=palette, star_mask=palette_star_mask
            )
            save_artifact(f'08_palette_{palette.lower()}.tif', current_nl)
            print(f"  ✓ {palette.upper()} 调色板映射完成 (感官亮度 L 严格守恒)")

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
            # 双窄带 / 发射星云 OIII 青蓝物理特征掩膜：
            # OIII 在绿色与蓝色窗口均有透射响应，且能量较纯红 Hα 区更均衡。
            signal_floor = np.percentile(np.mean(pre_scnr, axis=2), 35)
            oiii_balance = (
                (b0 / np.maximum(g0, 1e-6) >= 0.60)
                & (b0 / np.maximum(g0, 1e-6) <= 1.65)
            )
            # 条件一：高氧辐射区 (G+B 相对 R 显著，如空腔与核心)
            oiii_cavity = (
                (np.mean(pre_scnr, axis=2) > signal_floor)
                & ((g0 + b0) / (2.0 * np.maximum(r0, 1e-6)) >= 0.42)
                & (np.minimum(g0, b0) > 0.015)
            )
            # 条件二：纯 OIII/低氢区 (G 或 B 直接超出 R)
            oiii_pure = (
                (np.mean(pre_scnr, axis=2) > signal_floor)
                & ((g0 > r0 * 0.95) | (b0 > r0 * 0.90))
                & (np.minimum(g0, b0) > 0.015)
            )
            oiii_mask = (oiii_balance & (oiii_cavity | oiii_pure))
            oiii_soft = gaussian_filter(oiii_mask.astype(np.float32), sigma=2.5)
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
            emission_shadows = cfg.get('emission_shadows', 0.92)
            current_nl = apply_curves(
                current_nl, midtones=1.08, shadows=emission_shadows, highlights=1.0
            )
            print(f"  ✓ 发射结构选择性饱和×{cfg.get('saturation', 1.85)}")
        else:
            if effective_target_type == 'galaxy':
                h, w = current_nl.shape[:2]
                if cached_target_info is not None:
                    gcx = float(cached_target_info['center'][0])
                    gcy = float(cached_target_info['center'][1])
                else:
                    gcx, gcy = w / 2.0, h / 2.0
                gradius = max(min(h, w) * 0.45, 200.0)
                yy, xx = np.ogrid[:h, :w]
                dist = np.sqrt((xx - gcx)**2 + (yy - gcy)**2)
                spatial_mask = np.clip(1.0 - (dist / gradius)**2, 0.0, 1.0)
                spatial_mask = gaussian_filter(spatial_mask, sigma=15.0)

                hsv = rgb2hsv(current_nl)
                sat_factor = cfg.get('saturation', 1.45)
                hsv[..., 1] *= (1.0 + (sat_factor - 1.0) * spatial_mask)
                hsv[..., 1] = np.clip(hsv[..., 1], 0, 1)
                current_nl = hsv2rgb(hsv)
                current_nl = apply_curves(current_nl, midtones=1.04, shadows=0.88, highlights=1.0)
                print(f"  ✓ 宽带星系空间自适应饱和度提升×{sat_factor:.2f} + 温和曲线微调")
            else:
                current_nl = apply_curves(current_nl, midtones=1.03, shadows=0.75)
                print("  ✓ 中性曲线调整")
        current = current_nl
        gate = check_channel_clipping(current, before_final_color)
        gate['stage'] = 'final_color'
        channel_clipping_gates.append(gate)
        save_current('08_color.tif')
        print("  ✓ 背景保护曲线完成")
    cfg.phase = None

    # Phase 8b: 目标区域局部对比度和纹理增强
    if 'local_enhance' in steps:
        cfg.phase = 'local_enhance'
        target_label = target_name or effective_target_type or '目标区域'
        print(f"\n[Phase 8b] {target_label} 局部对比度和纹理增强...")
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
                if cached_target_info is not None:
                    target_info = cached_target_info
                elif effective_target_type == 'galaxy':
                    # 星系走星点抑制的弥散检测（见 Phase 1 前的同类注释）：
                    # 双高通细节检测会锁到亮星，局部增强因此在空天上运行。
                    target_info = detect_extended_target(detail_source)
                else:
                    target_info = detect_target_crop(detail_source)[1]
                nebula_cx = int(round(target_info['center'][0]))
                nebula_cy = int(round(target_info['center'][1]))
                if effective_target_type == 'galaxy':
                    # 弥散检测直接给出流量加权 RMS 半径，无需 bbox 换算。
                    detected_radius = int(round(float(target_info.get('radius') or 0.0)))
                    min_galaxy_radius = int(min(current_nl.shape[:2]) * 0.38)
                    if detected_radius < min_galaxy_radius:
                        print(
                            f"  星系盘面半径补偿: 原始检测半径={detected_radius}px "
                            f"< 保底半径={min_galaxy_radius}px，调整为={min_galaxy_radius}px"
                        )
                        detected_radius = min_galaxy_radius
                else:
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
        _before_8b = current_nl
        current_nl = local_nebula_enhance(
            current_nl,
            center_y=nebula_cy, center_x=nebula_cx,
            radius=radius, strength=cfg.get('local_strength', local_strength),
            star_mask=local_star_mask,
        )
        local_enhance_effect = measure_stage_effect(_before_8b, current_nl)
        current = current_nl
        save_current('08b_local_enhance.tif')
        if local_enhance_effect and local_enhance_effect['no_op']:
            print(f"  ⚠️ 局部增强空操作：星云区高频仅放大 "
                  f"{local_enhance_effect['hf_amplification']:.3f}× "
                  f"< {MIN_EFFECTIVE_HF_AMPLIFICATION}，已记入 skipped_reasons")
        else:
            print(f"  ✓ 局部增强 center=({nebula_cx},{nebula_cy}) "
                  f"radius={radius} strength={cfg.get('local_strength', local_strength)}")
    cfg.phase = None

    if 'external_detail' in steps:
        cfg.phase = 'external_detail'
        if external_starless_linear is None:
            raise ValueError("external_detail 步骤需要 --external-starless")
        print("\n[Phase 8c] 外部无星层正向结构增强...")
        _before_8c = current_nl
        current_nl = positive_starless_detail_enhance(
            current_nl,
            original_linear=original_linear,
            starless_linear=external_starless_linear,
            strength=external_detail_strength,
            full_frame=not auto_crop_target,
        )
        external_detail_effect = measure_stage_effect(_before_8c, current_nl)
        current = current_nl
        save_current('08c_external_detail.tif')
        if external_detail_effect and external_detail_effect['no_op']:
            print(f"  ⚠️ 外部细节空操作：星云区高频仅放大 "
                  f"{external_detail_effect['hf_amplification']:.3f}× "
                  f"< {MIN_EFFECTIVE_HF_AMPLIFICATION}，已记入 skipped_reasons")
        else:
            print(f"  ✓ 仅正向细节 strength={external_detail_strength}")
    cfg.phase = None

    # Phase 8d: 轻微缩星并恢复蓝白星色
    if 'star_reduce' in steps:
        cfg.phase = 'star_reduce'
        if valid_starless_layer and processed_stars is not None and not star_layer_empty:
            print("\n[Phase 8d] 跳过无星层缩星：当前为纯净无星图，缩星已在星点层独立完成")
            save_current('08d_star_reduced.tif')
        else:
            print("\n[Phase 8d] 带星图全局缩星...")
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
    cfg.phase = None

    # ══════════════════════════════════════════════════════════════
    # 最终阶段 (Final Phase)
    # ══════════════════════════════════════════════════════════════

    # Phase 9: 星点合成 (StarComposer)
    effective_blend_mode = cfg.get('star_blend_mode', star_blend_mode)
    # 具名星点门禁的同域参照图：合成后、后续非线性润色（style / final_denoise /
    # background lock）之前的快照。pipeline 里此前没有任何「含星 + 非线性 + 与最终
    # current 同域」的图，导致 STAR_BLOAT / STAR_LAYER_LOSS / STAR_HOLES 无法运行。
    star_gate_reference = None
    if 'star_combine' in steps and processed_stars is not None and not star_layer_empty:
        cfg.phase = 'star_combine'
        print("\n─ 最终阶段 ─")
        print("[Phase 9] 星点合成 (StarComposer)...")
        current = combine_starless_stars(
            current_nl, processed_stars,
            star_strength=cfg['star_combine_strength'],
            blend_mode=effective_blend_mode,
        )
        save_current('09_star_combined.tif')
        print(f"  ✓ 星点强度={cfg['star_combine_strength']} 混合模式={effective_blend_mode}")
        star_gate_reference = downsample_for_gates(current)
    else:
        if 'star_combine' in steps and star_layer_empty:
            star_combine_skipped = {
                "reason": "empty_stars_layer",
                "detail": "星点层全零（去星回退），合成会是空操作，已显式跳过",
            }
            print("\n─ 最终阶段 ─")
            print("[Phase 9] 跳过星点合成：星点层为空（记录原因，不做静默空操作）")
        current = current_nl
    cfg.phase = None

    # 去星修补足迹检测。
    #
    # 位置很重要：必须量**合成星点之后**的 `current`，而不是 Phase 9 之前的无星
    # 层。实测同一文件：无星层 0/191 命中（passed），合成后 19/191=10.0% 命中
    # （failed，全部为亮斑、纹理比低至 0.046）——因为可见斑块是星点层 screen
    # 混合到无星底图上才出现的，无星层上量不到。这与最初设计假设相反，以实测为准。
    #
    # 星位来自线性域的星点蒙版（位置是几何量、域无关）；FWHM 也必须用线性域实测值
    # （约 3.9px），否则会拿到被拉伸放大的 9~10px，几何关系全错。
    inpaint_footprint = None
    if (('star_remove' in steps or (star_layers and 'star_combine' in steps)) and not star_removal_fallback
            and linear_star_mask_val is not None):
        try:
            from artifact_gates import check_inpaint_footprint
            _lin_fwhm = None
            if 'linear_star_metrics' in locals() and linear_star_metrics:
                _lin_fwhm = linear_star_metrics.get('estimated_fwhm')
            inpaint_footprint = check_inpaint_footprint(
                current, linear_star_mask_val, fwhm=_lin_fwhm
            )
            print(
                f"  [足迹] INPAINT_FOOTPRINT: {inpaint_footprint['status']} "
                f"— {inpaint_footprint['message']}"
            )
        except Exception as exc:  # 门禁失败不阻断出图
            inpaint_footprint = {
                "code": "INPAINT_FOOTPRINT", "status": "error",
                "escalate": False, "message": f"足迹检测失败：{exc}", "evidence": {},
            }
    cfg.phase = None

    style_diagnostics = None
    if 'style' in steps:
        cfg.phase = 'style'
        before_style = current.copy()
        print("\n[Phase 9a] AI 风格定调 (非生成式)...")
        effective_style_strength = cfg.get('style_strength', style_strength)
        style_star_mask = None
        if processed_stars is not None and not star_layer_empty:
            style_star_mask = np.max(processed_stars, axis=2) if processed_stars.ndim == 3 else processed_stars
        elif linear_star_mask_val is not None:
            style_star_mask = linear_star_mask_val
        (current, selected_style, _style_reasoning,
         style_diagnostics) = apply_professional_style(
            current,
            style=style,
            target_type=effective_target_type,
            color_mode=effective_color_mode,
            strength=effective_style_strength,
            star_mask=style_star_mask,
            return_diagnostics=True,
            highlight_ceiling=cfg.get('style_highlight_ceiling', 0.98),
        )
        current_nl = current
        gate = check_channel_clipping(current, before_style)
        gate['stage'] = 'style'
        channel_clipping_gates.append(gate)
        save_current('09a_style.tif')
        print(f"  ✓ style={selected_style} strength={effective_style_strength}")
        if style_diagnostics:
            print(
                "    局部黑位: "
                f"global={style_diagnostics['floor_global']:.4f} "
                f"local=[{style_diagnostics['floor_min']:.4f}, "
                f"{style_diagnostics['floor_max']:.4f}] "
                f"裁切区块(before/after)="
                f"{style_diagnostics['blocks_clipped_before_repair']}/"
                f"{style_diagnostics['blocks_clipped_after_repair']}"
            )
            if style_diagnostics.get("repair_applied"):
                print(
                    "    ⚠ 自愈：黑位全局下移 "
                    f"{style_diagnostics['repair_shift']:.5f} 以避免局部背景被裁成纯黑"
                )
    cfg.phase = None

    # Phase 10: 最终降噪 (SCUNet-like, 必须最后)
    if 'final_denoise' in steps and skip_final_denoise:
        print('\n[Phase 10] Skipped: AI denoise already accepted')
    if 'final_denoise' in steps and not skip_final_denoise:
        cfg.phase = 'final_denoise'
        final_denoise_backend = cfg.get('final_denoise_method', cfg.get('denoise_method', 'bilateral'))
        print(f"\n[Phase 10] 最终降噪 ({'À Trous Wavelet (Starlet)' if final_denoise_backend in ('atrous', 'starlet') else 'SCUNet-like'}, 整体)...")
        if current.ndim == 2:
            current = np.stack([current] * 3, axis=-1)
        current = tiled(
            lambda tile: denoise_luminance_chroma(
                tile, lum_strength=cfg['final_denoise_lum'],
                chroma_strength=cfg['final_denoise_chroma'],
                method=final_denoise_backend
            ),
            current,
        )
        save_current('10_final_denoise.tif')
        print(f"  ✓ Final-Denoise L={cfg['final_denoise_lum']}/C={cfg['final_denoise_chroma']}  方法={final_denoise_backend}")
    cfg.phase = None

    if reference_image:
        cfg.phase = 'reference_grade'
        print("\n[Phase 11] 参考图全局定调...")
        reference, _reference_meta = read_image(reference_image)
        reference = np.asarray(reference, dtype=np.float32)
        grade_kwargs = {
            "strength": float(np.clip(reference_strength, 0.0, 1.0)),
            "max_color_gain": cfg.get("reference_max_color_gain", 1.25),
            "max_saturation": cfg.get("reference_max_saturation", 1.45),
            "local_contrast": cfg.get("reference_local_contrast", 0.10),
        }
        reference_grade_report = None
        if reference_color_only:
            # 只做信号区色比匹配：跳过全局亮度/色调/饱和度定调。
            # 全局定调会改动 tone/stretch 并可能引入 CORE_BURNING，且它的参数空间
            # 里没有色比控制 —— 实测它先把 R/G 冲到 1.10 再靠色比匹配拉回 1.375，
            # 属于"先错后纠"。只做色比匹配可直接从 1.78 命中 1.375。
            print("  (--reference-color-only：跳过全局亮度/色调/饱和度匹配)")
            reference_grade_report = {
                "method": "signal_color_match_only",
                "structural_transfer": False,
                "global_grade_skipped": True,
            }
        elif reference_auto_search:
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

        # 信号区色比匹配（可选）：闭式解通道增益，把星云主体的 Hα/OIII 比对齐参考图。
        # 与上面的全局定调互补 —— 后者的搜索空间里没有色比控制，实测会把 R/G
        # 从 1.77 冲到 0.79（目标 1.37）而 B/G 完全不动。
        if reference_color_match:
            current, signal_report = match_signal_color(
                current,
                reference,
                strength=cfg.get("reference_color_match_strength", 1.0),
                max_gain=cfg.get("reference_max_signal_color_gain", 2.0),
            )
            reference_grade_report["signal_color_match"] = signal_report
            if signal_report.get("applied"):
                src_r = signal_report["source_ratios"]
                dst_r = signal_report["result_ratios"]
                ref_r = signal_report["reference_ratios"]
                print(
                    f"  ✓ 信号区色比匹配 gains={signal_report['effective_gains']}"
                    f"{' (已限幅)' if signal_report['clipped'] else ''}"
                )
                print(
                    f"    R/G {src_r['r_over_g']}→{dst_r['r_over_g']}（参考 {ref_r['r_over_g']}）  "
                    f"B/G {src_r['b_over_g']}→{dst_r['b_over_g']}（参考 {ref_r['b_over_g']}）"
                )
            else:
                print(f"  ⚠️ 信号区色比匹配跳过: {signal_report.get('reason')}")
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
    cfg.phase = None

    # 终极背景中性化锁定 (Final Background Neutralization Lock)
    output_is_linear = bool(is_linear_input and 'stretch' not in steps)
    bg_neutralize_report = None
    if background_neutralize_lock and not output_is_linear:
        cfg.phase = 'background_neutralization'
        bg_star_mask = None
        if processed_stars is not None and not star_layer_empty:
            bg_star_mask = np.max(processed_stars, axis=2) if processed_stars.ndim == 3 else processed_stars
        elif linear_star_mask_val is not None:
            bg_star_mask = linear_star_mask_val

        current, bg_neutralize_report = lock_background_neutrality(
            current,
            bg_percentile=25.0,
            upper_percentile=50.0,
            star_mask=bg_star_mask,
            return_report=True,
        )
        if bg_neutralize_report.get("applied"):
            save_current('10b_background_locked.tif', img=current)
            print(
                f"  ✓ 终极背景中性化锁定完成 (迭代 {bg_neutralize_report.get('iterations_run', 1)} 轮): "
                f"色偏量级 {bg_neutralize_report['pre_cast_magnitude']} → {bg_neutralize_report['post_cast_magnitude']}, "
                f"offsets={bg_neutralize_report['offsets']}"
            )
    cfg.phase = None

    # 最终输出
    current = np.clip(current, 0, 1)
    if not output_is_linear:
        final_reference = img if not is_linear_input and img.shape == current.shape else None
        gate = check_channel_clipping(current, final_reference)
        gate['stage'] = 'final'
        channel_clipping_gates.append(gate)

    if has_alpha:
        current = np.dstack([current, alpha_channel])

    write_image(
        current, output_path,
        fits_header=meta.get('header') if is_linear_input else None,
        data_scale=meta.get('data_scale') if is_linear_input else None,
        data_offset=meta.get('data_offset') if is_linear_input else None,
    )
    write_result_json_atomic({'capture_metadata': capture_metadata, 'is_linear': output_is_linear},
                             str(output_path) + '.meta.json')

    output_star_layers = None
    if valid_starless_layer and 'star_combine' not in steps:
        save_artifact('04_stars_linear.tif', stars)
        processed_path = None
        if loaded_processed_stars is not None or 'star_process' in steps:
            save_artifact('05c_star_final.tif', processed_stars)
            processed_path = artifact_paths.get('05c_star_final.tif')
        output_star_layers = {
            'source': layer_source_path,
            'starless': str(output_path),
            'stars': artifact_paths.get('04_stars_linear.tif'),
            'processed_stars': processed_path,
            'domain': 'linear' if output_is_linear else 'nonlinear',
            'subject_mask': subject_mask,
            'stars_is_linear': stars_is_linear,
            'linear_star_metrics': linear_star_metrics if 'linear_star_metrics' in locals() else None,
            'stellar_analysis': stellar_analysis or {
                'starfield': (analysis_report or {}).get('starfield', {}),
                'star_tools': (analysis_report or {}).get('recommendations', {}).get('star_tools', {}),
            },
        }
        with open(layer_source_path, 'rb') as handle:
            output_star_layers['source_sha256'] = hashlib.file_digest(handle, 'sha256').hexdigest()

    # 具名星点伪影门禁（与标量门禁并存）。
    #
    # 快照点 star_gate_reference 在 Phase 9 合成后，即去星/缩星都已结束，因此这里
    # 必须把 star_remove / star_process / star_reduce 从 steps 里剔除：
    #   - 含 star_remove / star_process → artifact_gates 会把星点层门禁判为 skipped
    #   - 含 star_reduce → STAR_LAYER_LOSS 门不会升级
    # 剔掉之后，量的才是「末端润色（style / final_denoise / background lock）对星点
    # 层的损伤」，这正是 pipeline 路径此前完全看不到的部分。
    star_artifact_gates = None
    if star_gate_reference is not None:
        _gate_steps = [
            s for s in steps
            if s not in ('star_remove', 'star_process', 'star_reduce')
        ]
        try:
            from artifact_gates import evaluate_star_artifact_gates
            star_artifact_gates = evaluate_star_artifact_gates(
                star_gate_reference,
                downsample_for_gates(current),
                steps=_gate_steps,
            )
            star_artifact_gates["context"].update(
                reference_stage="post_star_combine", candidate_stage="final")
        except Exception as exc:  # 门禁失败不阻断出图
            star_artifact_gates = {
                "schema": "artifact_gates/1.0",
                "status": "error",
                "summary": f"星点门禁执行失败：{exc}",
                "gates": [],
            }

    output_metrics = calculate_metrics(
        current,
        {
            'processing_stage': 'linear' if output_is_linear else 'final',
            'linear_star_metrics':
                linear_star_metrics if 'linear_star_metrics' in locals() else None,
            'style_diagnostics': style_diagnostics,
            'inpaint_footprint': inpaint_footprint,
            'channel_clipping_gates': channel_clipping_gates,
            'fidelity_gates': fidelity_gates,
        },
    )
    output_metrics['channel_clipping_gates'] = channel_clipping_gates
    quality_status, quality_gates = evaluate_quality_gates(
        output_metrics,
        target_type=effective_target_type,
        steps=steps,
    )
    if stretch_diagnostics and stretch_diagnostics.get('review_required'):
        from gate_authority import seal_gate
        quality_gates.append(seal_gate({
            'code': ('STARLESS_ANCHOR_UNAVAILABLE' if stretch_diagnostics.get('fallback_reason')
                     else 'STARLESS_ANCHOR_TARGET'),
            'status': 'warning', 'escalate': True,
            'message': 'Starless subject anchoring requires review',
            'value': {key: stretch_diagnostics.get(key) for key in (
                'fallback_reason', 'subject_samples', 'subject_midtones',
                'actual_subject', 'target_bg', 'actual_background')},
            'threshold': 'subject tolerance=max(0.01,5%); background tolerance=max(0.003,10%)',
            'stage': 'stretch',
        }))
        if quality_status == 'success':
            quality_status = 'review_required'
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
    # 注意：warnings 已在管线早期初始化（外部无星图分支会提前 append），
    # 这里必须 extend 而不是重新赋值，否则会丢掉早期警告。
    warnings.extend([
        {
            "code": gate["code"],
            "message": gate["message"],
        }
        for gate in quality_gates
    ])
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
    if stretch_recovery == 'failed':
        warnings.append({
            "code": "STRETCH_COLLAPSE",
            "message": "拉伸输出色调范围异常收窄且保守回退无效，需人工检查黑点/白点设置",
        })
    if star_combine_skipped is not None:
        warnings.append({
            "code": "STAR_COMBINE_SKIPPED",
            "message": star_combine_skipped["detail"],
        })
    status = quality_status
    if stretch_diagnostics:
        display_review = stretch_diagnostics.get('display_gates', {}).get('status') == 'review_required'
        plateau_review = stretch_diagnostics.get('core_plateau', {}).get('escalate', False)
        if (display_review or plateau_review) and status == 'success':
            status = 'review_required'
    if stretch_recovery == 'failed' and status == 'success':
        status = 'review_required'
    if memory_report['skipped_steps']:
        warnings.append({'code': 'MEMORY_STEPS_SKIPPED',
                         'message': 'Requested steps skipped: ' + ', '.join(memory_report['skipped_steps'])})
        if status == 'success':
            status = 'partial_success'
    if pre_denoise_report and pre_denoise_report['review_required'] and status == 'success':
        status = 'review_required'
    # 具名星点门禁是**加法式**升级：只在原本 success 时升级为 review_required，
    # 不覆盖既有 partial_success / failed 语义；warnings 只收 escalate=True 的
    # warning/failed（passed/skipped 不进 warnings，避免噪音）。
    if star_artifact_gates and star_artifact_gates.get("status") == "review_required":
        if status == "success":
            status = "review_required"
        for _gate in star_artifact_gates.get("gates", []):
            if _gate.get("escalate", True) and _gate.get("status") in ("warning", "failed"):
                warnings.append({
                    "code": _gate["code"],
                    "message": _gate["message"],
                })
    if recognize and not recognition_ok and status == "success":
        status = "partial_success"
    if recognition_review_pending and status == "success":
        status = "partial_success"
    skipped_reasons = {}
    if is_star_poi_target(target_type, target_name):
        skipped_reasons.update({step: 'star_subject_protection' for step in
                               ('star_remove', 'star_reduce', 'star_process', 'star_combine')})
    if not is_linear_input:
        skipped_reasons['stellar_repair'] = 'nonlinear_input'
    if pre_denoise_report and (pre_denoise_report['status'] in ('skipped', 'rejected')
                              or pre_denoise_report.get('fallback_applied') is False):
        skipped_reasons['pre_denoise'] = pre_denoise_report.get('reason', 'validation_rejected')
    if stretch_recovery == 'failed':
        skipped_reasons.update({step: 'stretch_recovery_failed'
                                for step in stretch_diagnostics['skipped_after_failure']})
    if star_removal_fallback:
        skipped_reasons['star_remove'] = 'star_separation_rejected'
    if star_combine_skipped:
        skipped_reasons['star_combine'] = star_combine_skipped['reason']
    if hdr_report and hdr_report.get('enhance_mode') == 'none':
        skipped_reasons['enhance'] = 'enhancement_not_justified'
    if hdr_report and not hdr_report.get('applied', True):
        cfg.used.get('enhance', {}).pop('hdr_strength', None)
    if 'star_reduce' in steps and valid_starless_layer and not star_layer_empty:
        skipped_reasons['star_reduce'] = 'already_processed_stellar_layer'
    if 'style' in steps and cfg.get('style_strength', style_strength) == 0:
        skipped_reasons['style'] = 'zero_strength'
    # 「执行了但效果为 0」—— 与上面 9 条「前置条件不满足」是**不同**类别。
    # cfg.completed 由 save_artifact 追加（save 了就算 executed），因此空操作阶段
    # 本会进 executed_steps 拿「已执行」背书；这里把它从 executed 里摘出去。
    # 门禁副作用仅限 skipped_reasons，不动 evaluate_quality_gates —— 与 CLAHE
    # 过度增强守卫（2690-2711 附近）完全同构。
    for _step, _effect in (
        ('local_enhance', locals().get('local_enhance_effect')),
        ('external_detail', locals().get('external_detail_effect')),
    ):
        if _effect and _effect['no_op'] and _step in cfg.completed:
            skipped_reasons[_step] = 'no_op_zero_effect'
    executed_steps = [step for step in cfg.completed if step is not None and step not in skipped_reasons]
    used_params = {step: dict(cfg.used.get(step, {})) for step in executed_steps}
    if 'stretch' in used_params and stretch_diagnostics:
        common_stretch = {'target_bg', 'stretch_headroom'}
        standard_params = {'ghs_standard_' + key for key in ('log_d', 'b', 'sp', 'lp', 'hp')}
        allowed = {
            'emission': {'shadow_pctl', 'highlight_pctl', 'stretch_gamma', 'target_bg',
                         'stretch_min_p99', 'stretch_headroom'},
            'background_compressed': common_stretch | {'stretch_subject_midtones'}
                | ({'ghs_protect_strength'} if cfg.get('stretch_subject_midtones') is None else set()),
            'pi_mtf': common_stretch,
            'ghs_standard': common_stretch | standard_params,
            'masked_ghs_standard': common_stretch | standard_params | {'ghs_protect_strength'},
            'arcsinh': {'stretch_factor'}, 'luminance_arcsinh': {'stretch_factor'},
            'masked': {'stretch_factor', 'target_bg'}, 'mtf': {'midtones'},
            'deep': {'shadow_pctl', 'highlight_pctl', 'stretch_gamma'},
            'very_dark': {'stretch_factor', 'stretch_gamma', 'shadow_pctl', 'highlight_pctl', 'target_bg', 'stretch_min_p99'},
            'ghs': {'ghs_sp', 'ghs_b', 'ghs_c', 'ghs_lp', 'ghs_hp', 'stretch_factor'},
            'masked_ghs': {'ghs_sp', 'ghs_b', 'ghs_c', 'ghs_lp', 'ghs_hp', 'stretch_factor',
                           'ghs_protect_lp', 'ghs_protect_hp', 'ghs_protect_strength',
                           'target_bg', 'shadow_pctl', 'highlight_pctl', 'stretch_gamma'},
        }.get(resolved_stretch_method, set(used_params['stretch']))
        used_params['stretch'] = {key: cfg.get(key, value) for key, value in used_params['stretch'].items()
                                  if key in allowed}
        stretch_keys = {key for key in cfg if (key.startswith(('ghs_', 'stretch_'))
                        or key in ('shadow_pctl', 'highlight_pctl', 'target_bg', 'midtones'))
                        and key not in ('stretch_policy_version', 'stretch_method')}
        stretch_diagnostics['unused_params'] = sorted(stretch_keys - allowed)
        stretch_diagnostics['unused_legacy_params'] = sorted(key for key in stretch_keys - allowed
                                                             if key.startswith('ghs_') and not key.startswith('ghs_standard_'))
        stretch_diagnostics['unused_standard_params'] = sorted(key for key in stretch_keys - allowed
                                                               if key.startswith('ghs_standard_'))
        if stretch_diagnostics['algorithm'] == 'standard_ghs':
            for key in ('log_d', 'b', 'sp', 'lp', 'hp'):
                used_params['stretch']['ghs_standard_' + key] = stretch_diagnostics[key]
            if resolved_stretch_method == 'masked_ghs_standard':
                used_params['stretch']['ghs_protect_strength'] = stretch_diagnostics['protect_strength']
    used_params['runtime'] = {'input_is_linear': is_linear_input,
                              'stretch_policy_version': stretch_policy_version}
    consumed = set().union(*(set(values) for values in used_params.values()))
    execution = {
        'executed_steps': executed_steps,
        'used_params': used_params,
        'unused_params': {key: {'value': value, 'reason': 'not_consumed_by_applied_operations'}
                          for key, value in cfg.items() if key not in consumed},
        'skipped_steps': [{'step': step, 'reason': skipped_reasons.get(step,
                           'memory_limit' if step in memory_report['skipped_steps']
                           else 'selection_rules_or_no_application')}
                          for step in dict.fromkeys(requested_steps + steps) if step not in executed_steps],
    }
    result = {
        "runtime": runtime,
        "requested_config": requested_config,
        "execution": execution,
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
        "pre_denoise": pre_denoise_report,
        "effective_config": {
            "input_is_linear": is_linear_input,
            "output_is_linear": output_is_linear,
            "skip_final_denoise": skip_final_denoise,
            "preset": preset,
            "steps": steps,
            "params": cfg,
            "target_type": effective_target_type,
            "target_name": target_name,
            "color_mode": effective_color_mode,
            "stretch_method": resolved_stretch_method,
            "style": selected_style,
            "style_strength": cfg.get("style_strength", style_strength),
            "star_blend_mode": effective_blend_mode,
        },
        "capture_metadata": capture_metadata,
        "physical_priors": physical_priors,
        "astrometry": plate_solution,
        "metrics": output_metrics,
        "color_calibration": color_calibration_report,
        "stellar_repair": stellar_repair_report,
        "emission_channel_recovery": emission_channel_report,
        "hdr": hdr_report,
        "sharpen": sharpen_report,
        "memory": memory_report,
        "stretch_recovery": stretch_recovery,
        "stretch_diagnostics": stretch_diagnostics,
        "star_combine_skipped": star_combine_skipped,
        "star_removal": (
            star_report if 'star_report' in locals() else None
        ),
        "reference_grade": reference_grade_report,
        "background_neutralization": bg_neutralize_report,
        "style_diagnostics": style_diagnostics,
        "star_artifact_gates": star_artifact_gates,
        "inpaint_footprint": inpaint_footprint,
        "external_starless_diagnostics": external_starless_diagnostics,
        "neural_star_removal_bridge": neural_bridge_report,
        "quality_policy": quality_policy,
        "quality_gates": quality_gates,
        "warnings": warnings,
        "errors": [],
        "safety_rules_applied": safety_log,
        "step_dependencies_applied": dependency_log,
        "artifacts": artifact_paths,
        "artifact_meta": artifact_meta,
        "star_layers": output_star_layers,
        "channel_clipping_gates": channel_clipping_gates,
    }

    step_status = status
    # 降级阶段收集：凡走了保守回退/ preserve_review 的步骤都记入，
    # 让 quality_state 能区分「干净出图」与「回退后出图」（failure_policy）。
    degraded_stages = sorted({
        *( [ 'stretch' ] if stretch_recovery is not None else [] ),
        *( ['star_remove'] if star_removal_fallback else [] ),
    })
    result['degraded_stages'] = degraded_stages
    # 累计配置指纹：证明「这份产物是用哪套配置产出的」。artifact_sha256 只能
    # 证明「文件没被改」，证明不了「配置没被改」—— 改一个上游参数重跑时，
    # 旧配置的 issues 会被 parent_state 继承到新产物上。指纹不同即切断继承。
    config_fp = stage_fingerprint(cfg, input_path, upto_step='stretch')
    result['config_fingerprint'] = config_fp
    result['config_used'] = describe_config(cfg, upto_step='stretch')
    quality_state = build_quality_state(output_path, input_path, inherited_quality,
                                        ','.join(executed_steps),
                                        collect_quality_gates(result),
                                        degraded_stages=degraded_stages,
                                        config_fingerprint=config_fp)
    result['quality_state'] = quality_state
    result['step_status'] = step_status
    if quality_state['issues'] and result['status'] == 'success':
        result['status'] = status = 'review_required'
    elif (quality_state['status'] == 'degraded'
          and result['status'] == 'success'):
        # degraded 不阻断出图，但要让调用方看得出「这不是一次干净的处理」。
        result['status'] = status = 'degraded'
    persist_quality_state(output_path, quality_state)

    if keep_intermediates:
        manifest_path = os.path.join(work_dir, 'manifest.json')
        with open(manifest_path, 'w', encoding='utf-8') as handle:
            json.dump({
                'schema_version': AGENT_SCHEMA_VERSION,
                'runtime': runtime,
                'quality_state': quality_state,
                'step_status': step_status,
                'requested_config': requested_config,
                'execution': execution,
                'stretch_diagnostics': stretch_diagnostics,
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
                'star_blend_mode': effective_blend_mode,
                'artifacts': artifact_paths,
                'safety_rules_applied': safety_log,
                'step_dependencies_applied': dependency_log,
                'linear_star_metrics': linear_star_metrics if 'linear_star_metrics' in locals() else None,
                'ai_tools_used': {
                    'external_starless': bool(external_starless),
                    'external_denoised': bool(external_denoised),
                    'deepsnr': bool(pre_denoise_report and pre_denoise_report['backend'] == 'deepsnr'
                                    and pre_denoise_report['status'] == 'accepted'),
                    'ai_artifact_check_required': bool(external_starless or external_denoised
                                                       or (pre_denoise_report and pre_denoise_report['status'] == 'accepted')),
                },
                'pre_denoise': pre_denoise_report,
                'neural_star_removal_bridge': neural_bridge_report,
                'metrics': output_metrics,
                'color_calibration': color_calibration_report,
                'emission_channel_recovery': emission_channel_report,
                'hdr': hdr_report,
                'sharpen': sharpen_report,
                'memory': memory_report,
                'stretch_recovery': stretch_recovery,
                'star_combine_skipped': star_combine_skipped,
                'star_removal': (
                    star_report if 'star_report' in locals() else None
                ),
                'reference_grade': reference_grade_report,
                'background_neutralization': bg_neutralize_report,
                'style_diagnostics': style_diagnostics,
                'star_artifact_gates': star_artifact_gates,
                'external_starless_diagnostics': external_starless_diagnostics,
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
    if (cleanup or not keep_intermediates) and output_star_layers is None:
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
                            'very_dark', 'emission', 'ghs', 'masked_ghs',
                            'ghs_standard', 'masked_ghs_standard', 'pi_mtf', 'background_compressed'],
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
    p.add_argument('--subject-mask', help='Reviewed subject protection mask matching input geometry')
    p.add_argument('--star-layers', help='JSON file containing a previous result star_layers object')
    p.add_argument('--capture-metadata', help='JSON file containing source capture metadata')
    p.add_argument('--external-starless-domain', default='linear',
                   choices=['linear', 'mtf'],
                   help='外部无星图所在域。linear=线性（默认，兼容旧输入）；'
                        'mtf=已做 MTF 拉伸，回流时会自动逆变换回线性。'
                        '若 work-dir 存在 starnet_payload_meta.json 则会自动识别，无需手传')
    p.add_argument('--external-starless-midtones', type=float, default=None,
                   help='mtf 域回流时使用的 MTF midtones；缺省则按原图背景推导。'
                        '必须与导出载荷时用的值一致（sidecar 会记录）')
    p.add_argument('--use-starnet', action='store_true',
                   help='使用 StarNet2 CLI 进行 AI 去星（需要本地已部署二进制）')
    p.add_argument('--starnet-path', default=None,
                   help='StarNet2 CLI (starnet++) 可执行二进制文件的绝对路径')
    p.add_argument('--starnet-stride', type=int, default=128,
                   help='StarNet2 步长，较小的值去星更精细但耗时长，例如 128 或 64 (默认: 128)。'
                        '内置 --use-starnet 路径与外部桥接命令共用此值')
    p.add_argument('--starnet-domain', default='mtf', choices=['linear', 'mtf'],
                   help='送进 StarNet2 的载荷域（内置 --use-starnet 路径）。'
                        'mtf=先 MTF 拉伸再量化、读回后逆变换（默认，推荐）；'
                        'linear=原样量化（旧行为，极暗数据会丢弱色通道）')
    p.add_argument('--starnet-timeout', type=int, default=900,
                   help='StarNet2 执行超时秒数（默认: 900）')
    p.add_argument('--star-removal-baseline', default=None,
                   help='上一次同图运行的 result.json 路径。提供后，去星误伤门禁改用'
                        '**相对判据**（本次 |1-q| 减去该文件里的基线），'
                        '这是绝对读数跨样本不可比时的唯一可行路线 '
                        '(references/star_damage_gate_calibration.md §8)')
    p.add_argument('--deepsnr', choices=['auto', 'always', 'never'], default='auto',
                   help='DeepSNR pre-denoise mode; auto skips low noise and star clusters')
    p.add_argument('--deepsnr-path', default=None, help='DeepSNR executable path')
    p.add_argument('--deepsnr-timeout', type=float, default=900, help='DeepSNR processing timeout in seconds')
    p.add_argument('--deepsnr-strength', type=float, default=0.35, help='Linear-domain blend strength in (0, 1]')
    p.add_argument('--input-linear', dest='input_is_linear', action='store_true', default=None,
                   help='Declare a linear TIFF intermediate')
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
                   # 从 target_rules._KNOWN_TYPES 派生，避免两处清单各写一份
                   # 而悄悄分叉（此前 CLI 漏了 star_cluster 与
                   # supernova_remnant，而 target_type 一律会被 resolve_target_type
                   # 按 _KNOWN_TYPES 校验 —— CLI 能接受的值反而少于 API）。
                   choices=sorted(_KNOWN_TYPES))
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
    p.add_argument('--reference-color-only', action='store_true',
                   help='只做信号区色比匹配，跳过全局亮度/色调/饱和度定调。'
                        '全局定调会改动 tone/stretch 且可能引入 CORE_BURNING，'
                        '其参数空间里也没有色比控制（会先冲过头再被拉回）')
    p.add_argument('--reference-color-match', action='store_true',
                   help='在全局定调之后，把星云**信号区**的 R/G、B/G 色比对齐参考图'
                        '（闭式解通道增益，确定性、可复现）。修正 Hα/OIII 比 —— '
                        '全局定调与背景色偏匹配都做不到这一点')
    p.add_argument('--reference-auto-search', action='store_true',
                   help='在缩略图上自动搜索受约束参考图参数')
    p.add_argument('--reference-strength', type=float, default=0.85,
                   help='参考图全局定调强度（默认: 0.85）')
    p.add_argument('--reference-match-orientation', action='store_true',
                   help='参考图横竖方向不同时旋转最终输出')
    p.add_argument('--stellar-repair', action='store_true',
                   help='在线性阶段执行星点像差、色散微调与紫晕抑制 (Phase 2b)')
    p.add_argument('--stellar-repair-mode', choices=['auto', 'color_only', 'shape_only'], default=None,
                   help='星点修复模式: auto (默认), color_only, shape_only')
    p.add_argument('--stellar-repair-strength', choices=['conservative', 'balanced', 'strong'], default=None,
                   help='星点修复强度档位: conservative, balanced (默认), strong')
    p.add_argument('--palette', default='none',
                   choices=['none', 'hoo', 'sho', 'hso', 'osh', 'ohs', 'hos'],
                   help='双窄带 (Duo-Band/Dual-Band) 哈勃色板假彩色映射 (默认: none)')
    p.add_argument('--linear-deconv', action='store_true',
                   help='在线性阶段执行带背景保护的正则化 Richardson-Lucy 物理反卷积')
    p.add_argument('--deconv-iterations', type=int, default=10,
                   help='反卷积迭代轮次 (默认: 10)')
    p.add_argument('--stacking-crop', choices=['none', 'auto'], default='none',
                   help='自动检测并裁切抖动叠加低信噪比边缘 (默认: none)')
    p.add_argument('--halo-guard', action='store_true',
                   help='去星后执行大亮星残余彩色星晕平整守卫')
    p.add_argument('--star-blend-mode', choices=['screen', 'add'], default='screen',
                   help='星点合成混合模式 (默认: screen，天体物理屏幕混合保护天然星色与高光)')
    bg_neut = p.add_mutually_exclusive_group()
    bg_neut.add_argument('--background-neutralize', dest='background_neutralize_lock',
                         action='store_true', default=True,
                         help='开启出片前终极背景中性化锁定（默认开启，严格消除背景色偏警告）')
    bg_neut.add_argument('--no-background-neutralize', dest='background_neutralize_lock',
                         action='store_false',
                         help='关闭出片前终极背景中性化锁定')
    p.add_argument('--export-starnet-payload', action='store_true',
                   help='导出当前线性阶段标准 16-bit 载荷 (starnet_payload_stretched.tif) 供外部神经网络去星使用')
    p.add_argument('--export-starnet-payload-domain', default='mtf',
                   choices=['linear', 'mtf'],
                   help='载荷导出域。mtf=先做 MTF 拉伸再量化（默认，推荐）；'
                        'linear=原样量化。极暗数据用 linear 会让弱色通道被量化归零'
                        '（实测星云区 G 有 93%% 像素为 0），拉伸后整片星云变纯红')
    p.add_argument('--starnet-recommendation', choices=['auto', 'always', 'never'], default='auto',
                   help='外部神经网络去星推荐模式: auto (默认), always, never')
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
        subject_mask=args.subject_mask,
        star_layers=json.loads(Path(args.star_layers).read_text()) if args.star_layers else None,
        capture_metadata=json.loads(Path(args.capture_metadata).read_text()) if args.capture_metadata else None,
        external_denoised=args.external_denoised,
        deepsnr=args.deepsnr, deepsnr_path=args.deepsnr_path,
        deepsnr_timeout=args.deepsnr_timeout, deepsnr_strength=args.deepsnr_strength,
        input_is_linear=args.input_is_linear,
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
        starnet_domain=args.starnet_domain,
        starnet_timeout=args.starnet_timeout,
        star_removal_baseline=args.star_removal_baseline,
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
        reference_color_match=args.reference_color_match or args.reference_color_only,
        reference_color_only=args.reference_color_only,
        reference_strength=args.reference_strength,
        reference_match_orientation=args.reference_match_orientation,
        stellar_repair=args.stellar_repair,
        stellar_repair_mode=args.stellar_repair_mode,
        stellar_repair_strength=args.stellar_repair_strength,
        palette=args.palette,
        linear_deconv=args.linear_deconv,
        deconv_iterations=args.deconv_iterations,
        stacking_crop=args.stacking_crop,
        halo_guard=args.halo_guard,
        star_blend_mode=args.star_blend_mode,
        background_neutralize_lock=args.background_neutralize_lock,
        export_starnet_payload=args.export_starnet_payload,
        export_starnet_payload_domain=args.export_starnet_payload_domain,
        external_starless_domain=args.external_starless_domain,
        external_starless_midtones=args.external_starless_midtones,
        starnet_recommendation=args.starnet_recommendation,
    )
    if not args.result_json:
        print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
