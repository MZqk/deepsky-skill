#!/usr/bin/env python3
"""具名星点伪影门禁（Named Star Artifact Gates）。

把 SKILL.md 中「查振铃、核心死白、星点层完整」等文档级检查项落成
可审计、可回归测试的结构化函数，让 review.json 从"AI 说看起来没问题"
变成带定位坐标和量化证据的结构化结果。

与 agent_protocol.evaluate_quality_gates 的标量门禁**并存而非替换**：
标量门禁（BACKGROUND_CRUSHED / CORNER_NONUNIFORM 等）继续承担全局
统计触发器；本模块输出具名伪影检测（哪个伪影、在哪、多严重）。
红线不变：数值门禁是审查触发器，不是视觉质量的证明。

门禁清单：
  STAR_RINGING        亮星周围暗环（反卷积/锐化振铃、去星修补伪影）
  STAR_BLOAT          星点整体胀大（复用 star_tools.measure_paired_star_profiles）
  STAR_LAYER_LOSS     星点层保留率不足（星点被抹掉/压死）
  STAR_HOLES          星点位置出现暗坑（去星修补发黑、形态学过腐蚀）
  CORE_BURNING        处理引入/扩大的高光死白连通域（星点核心烧毁）
  INPAINT_FOOTPRINT   去星修补足迹在拉伸后被放大成可见斑块（**不属聚合器**，
                      走 evaluate_quality_gates 的标量路径）

CLI:
  python scripts/artifact_gates.py reference.tif candidate.tif \
      [--steps stretch,sharpen] [--output star_gates.json]
  仅传 candidate 时只运行 candidate 侧门禁（RINGING / 绝对 BURNING）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from scipy.ndimage import (
    binary_dilation,
    gaussian_filter,
    distance_transform_edt,
    label,
    maximum_filter,
    find_objects,
)

sys.path.insert(0, str(Path(__file__).parent))
from star_tools import estimate_fwhm, measure_paired_star_profiles

SCHEMA = "artifact_gates/1.0"

# ── 工程阈值（与 star_tools.measure_paired_star_profiles 同源） ──
RING_DIP_REL = 0.12          # 暗环深度 / 局部背景 ≥ 12%
RING_DIP_ABS = 0.003         # 且绝对深度 ≥ 0.003（防暗背景噪声误报）
RING_MIN_SAMPLED = 3         # 可测亮星 < 3 → skipped
RETENTION_FAILED = 0.55      # 星点峰值保留率中位 < 0.55 → failed
RETENTION_WARNING = 0.75     # < 0.75 → warning
HOLE_CENTER_RATIO = 0.80     # Center intensity below 80% of the local background
# 新生死白连通域的面积门限 —— **以 3840×2160（8.3MP）为基准等比缩放**。
#
# 为什么不能是固定像素数：绝对阈值让门禁**分辨率依赖** —— 同一份数据、同一份
# 代码，实测 1/1 分辨率 R 新增区域 7 处判 failed，降到 1/2 就变 0 处 passed
# （连通域被降采样打断）。这等于「门禁严不严取决于图像多大」。
#
# 基准 8.3MP 下 25px² ≈ 6px 直径（星点级死白），80px² 是其 3.2 倍；
# 4K（3840×2160）恰好是基准，故 8.3MP 及以下行为与历史完全一致。
CLIPPING_GATE_REFERENCE_MP = 8.3
BURN_MIN_AREA_PX = 25        # 8.3MP 基准：新生死白连通域 ≥ 25px²（≈6px 直径）
BURN_BIG_AREA_PX = 80        # 8.3MP 基准：单个 ≥ 80px² 或 ≥2 个 → failed
BURN_FLOOR_AREA_PX = 2.0     # 缩放下限：单像素只是采样噪点，不算可见伪影


def _scaled_area_threshold(area_px, megapixels):
    """把面积门限按像素数等比缩放到当前分辨率。

    面积随**像素数**线性变化（一阶近似，忽略各向异性缩放），故按
    `megapixels / CLIPPING_GATE_REFERENCE_MP` 线性缩放。

    下限 2px²：线性缩放在小图上会把门限压到近 0，使**单个像素**都算
    「新增死白连通域」—— 实测 64×64 图上 49 个 1px 噪点会被判 failed。
    2px² 是「至少两个像素构成一块」的物理下限：单像素只能是采样噪点，
    不构成用户可见的伪影。
    """
    if megapixels is None or megapixels <= 0:
        return float(area_px)
    scale = float(megapixels) / CLIPPING_GATE_REFERENCE_MP
    return max(float(area_px) * scale, BURN_FLOOR_AREA_PX)


def _image_megapixels(shape):
    """按短边构造等价方形估算像素数（只用于面积门限缩放，不做几何假设）。"""
    if shape is None or len(shape) < 2:
        return None
    height, width = float(shape[0]), float(shape[1])
    if height <= 0 or width <= 0:
        return None
    return max(height, width) * min(height, width) / 1_000_000.0


def _gate(code, status, message, value=None, threshold=None,
          evidence=None, escalate=True):
    """构造门禁记录。

    键集与 fidelity_gates._gate 逐键一致（下游 quality_state / pipeline
    warnings 路径按同一套 schema 消费）。此处**封印**授权字段 ——
    见 gate_authority 模块文档：诊断字段（message/evidence）可自由改写，
    授权字段（code/status/escalate/value/threshold）改动会被复验检出。
    """
    from gate_authority import seal_gate
    return seal_gate({
        "code": code,
        "status": status,           # passed / warning / failed / skipped
        "escalate": escalate,
        "message": message,
        "value": value,
        "threshold": threshold,
        "evidence": evidence or {},
    })


def _luminance(image):
    image = np.asarray(image, dtype=np.float32)
    if not np.all(np.isfinite(image)):
        raise ValueError("artifact gates require finite image values")
    if image.ndim == 2:
        return image
    if image.ndim != 3 or image.shape[2] < 3:
        raise ValueError("expected gray or RGB image")
    return (
        0.2126 * image[..., 0]
        + 0.7152 * image[..., 1]
        + 0.0722 * image[..., 2]
    )


def _estimate_fwhm_or_default(gray):
    fwhm, _std, n_used, _cand = estimate_fwhm(gray)
    if n_used < 3 or not np.isfinite(fwhm) or fwhm <= 0:
        return 3.0, n_used
    # Moments include faint extended filaments. Use the connected half-height
    # core to keep the artifact sampling scale tied to the stellar peak.
    centers = _find_star_centers(gray, 3.0, max_stars=50,
                                 peak_percentile=99.5, min_significance=0.15)
    patch_r = max(8, int(np.ceil(1.5 * fwhm)))
    widths = []
    for cy, cx, _ in centers:
        if (cy < patch_r or cx < patch_r or cy + patch_r >= gray.shape[0]
                or cx + patch_r >= gray.shape[1]):
            continue
        patch = gray[cy - patch_r:cy + patch_r + 1, cx - patch_r:cx + patch_r + 1]
        bg = float(np.percentile(patch, 20))
        components, _ = label(patch >= bg + 0.5 * (float(gray[cy, cx]) - bg))
        core_id = components[patch_r, patch_r]
        if not core_id:
            continue
        core = components == core_id
        if any(np.any(edge) for edge in (core[0], core[-1], core[:, 0], core[:, -1])):
            continue
        # ponytail: half-height area is a diagnostic width; physical resolution needs PSF fitting.
        widths.append(2 * np.sqrt(np.count_nonzero(core) / np.pi))
    if len(widths) >= 3:
        return max(1.5, float(np.median(widths))), len(widths)
    return float(fwhm), n_used


def _find_star_centers(gray, fwhm, max_stars, peak_percentile,
                       min_significance, margin=None):
    """峰值法找亮星（与 measure_paired_star_profiles 同思路，独立实现）。

    返回 [(cy, cx, peak_signal)]，peak_signal = 峰值 − 局部背景(p20)。
    排除饱和峰（≥0.995）——饱和星芯的剖面测量不可靠。
    """
    h, w = gray.shape
    margin = int(margin if margin is not None else max(10, int(3.2 * fwhm)))
    if min(h, w) <= margin * 2:
        return []
    interior = gray[margin:-margin, margin:-margin]
    threshold = float(np.percentile(interior, peak_percentile))
    local_max = maximum_filter(interior, size=max(3, int(round(fwhm))))
    peak_mask = (interior == local_max) & (interior > threshold)
    yy, xx = np.where(peak_mask)
    yy = yy + margin
    xx = xx + margin
    order = np.argsort(gray[yy, xx])[::-1]
    separation = max(3, int(round(fwhm)))

    centers = []
    for index in order:
        cy, cx = int(yy[index]), int(xx[index])
        peak = float(gray[cy, cx])
        if peak >= 0.995:
            continue
        if any(
            (cy - ky) ** 2 + (cx - kx) ** 2 < separation ** 2
            for ky, kx, _ in centers
        ):
            continue
        y0, y1 = cy - 5, cy + 6
        x0, x1 = cx - 5, cx + 6
        patch = gray[max(0, y0):y1, max(0, x0):x1]
        local_bg = float(np.percentile(patch, 20.0))
        signal = peak - local_bg
        if signal < min_significance:
            continue
        centers.append((cy, cx, signal))
        if len(centers) >= max_stars:
            break
    return centers


# ══════════════════════════════════════════════════════════════
# STAR_RINGING：亮星暗环
# ══════════════════════════════════════════════════════════════

def _background_exclusion(gray, fwhm):
    """Mask stellar peaks and their wings, including saturated neighbors."""
    peaks = (gray == maximum_filter(gray, size=max(3, int(round(fwhm)))))
    peaks &= gray - gaussian_filter(gray, fwhm) > 0.03
    radius = int(np.ceil(1.2 * fwhm))
    return distance_transform_edt(~peaks) <= radius if np.any(peaks) else np.zeros_like(peaks)


def _local_background(patch, dx, dy, mask):
    """Fit a clipped local plane; retain all-sample residuals as uncertainty.

    A plane cannot describe curved filaments. Its residual must remain visible
    rather than letting clipping turn a poor background model into a pass.
    """
    mask = mask & np.isfinite(patch)
    if np.count_nonzero(mask) < 12:
        return None
    design = np.column_stack((np.ones(np.count_nonzero(mask)), dx[mask], dy[mask]))
    values = patch[mask]
    active = np.ones(len(values), dtype=bool)
    for _ in range(4):
        coefficients, _, rank, _ = np.linalg.lstsq(design[active], values[active], rcond=None)
        if rank < 3:
            return None
        residual = values - design @ coefficients
        median = np.median(residual[active])
        sigma = 1.4826 * np.median(np.abs(residual[active] - median))
        next_active = np.abs(residual - median) <= max(3.5 * sigma, 1e-7)
        if np.array_equal(active, next_active) or np.count_nonzero(next_active) < 12:
            break
        active = next_active
    plane = coefficients[0] + coefficients[1] * dx + coefficients[2] * dy
    error = float(np.percentile(np.abs(values - design @ coefficients), 90))
    return plane, error


def check_ringing(candidate, fwhm=None, max_stars=120, reference=None,
                  reference_comparable=True):
    """Detect absolute undershoot and attribute only new paired deficits.

    Paired inputs must share geometry and intensity domain. The same centers,
    radial bins and neighbor mask are used for both images. Fitting their
    difference preserves shared field structure even on a curved filament.
    """
    gray = _luminance(candidate)
    ref_gray = _luminance(reference) if reference is not None else None
    paired = ref_gray is not None and reference_comparable and ref_gray.shape == gray.shape
    source = ref_gray if paired else gray
    if fwhm is None:
        fwhm, _n = _estimate_fwhm_or_default(source)
    fwhm = max(float(fwhm), 1.5)
    centers = _find_star_centers(
        source, fwhm, max_stars=max_stars,
        peak_percentile=99.5, min_significance=0.15,
    )
    ring_zone = (1.2 * fwhm, 3.0 * fwhm)
    bg_zone = (3.5 * fwhm, 5.0 * fwhm)
    step = max(0.8, 0.3 * fwhm)
    edges = np.arange(ring_zone[0], ring_zone[1] + step, step)
    patch_r = int(np.ceil(bg_zone[1])) + 1
    excluded = _background_exclusion(gray, fwhm)
    if paired:
        excluded |= _background_exclusion(ref_gray, fwhm)

    h, w = gray.shape
    ringing, preexisting, uncertain, background_diagnostics = [], [], [], []
    n_sampled = 0
    for cy, cx, _signal in centers:
        y0, y1 = cy - patch_r, cy + patch_r + 1
        x0, x1 = cx - patch_r, cx + patch_r + 1
        if y0 < 0 or x0 < 0 or y1 > h or x1 > w:
            continue
        patch = gray[y0:y1, x0:x1]
        dy, dx = np.mgrid[-patch_r:patch_r + 1, -patch_r:patch_r + 1]
        dist = np.hypot(dy, dx)
        valid = ~excluded[y0:y1, x0:x1]
        outer = valid & (dist >= bg_zone[0]) & (dist <= bg_zone[1])
        background = _local_background(patch, dx, dy, outer)
        if background is None:
            uncertain.append({"center_rc": [cy, cx], "reason": "insufficient_background"})
            continue
        plane, error = background
        delta_background = None
        if paired:
            delta = patch - ref_gray[y0:y1, x0:x1]
            delta_background = _local_background(delta, dx, dy, outer)
            if delta_background is None:
                uncertain.append({"center_rc": [cy, cx], "reason": "insufficient_paired_background"})
                continue

        profiles = []
        for lo, hi in zip(edges[:-1], edges[1:]):
            # The target star mask is used only for fitting the background;
            # exclude neighboring stars from profiles without masking the ring.
            sel = (dist >= lo) & (dist < min(hi, ring_zone[1]))
            sel &= valid | (dist <= 1.2 * fwhm + 1)
            if np.count_nonzero(sel) < 3:
                continue
            bg = float(np.mean(plane[sel]))
            if bg < 0.004:
                continue
            depth = float(np.mean((plane - patch)[sel]))
            increment = None
            if delta_background is not None:
                increment = float(np.mean((delta_background[0] - delta)[sel]))
            profiles.append((float(lo + hi) / 2, bg, depth, increment))
        if not profiles or max(t[1] for t in profiles) < 0.004:
            continue
        n_sampled += 1
        suspects = [t for t in profiles if t[2] >= max(RING_DIP_ABS, RING_DIP_REL * t[1])]
        if not suspects:
            limit = max(RING_DIP_ABS, RING_DIP_REL * float(np.median(plane[outer])))
            if error > limit:
                diagnostic = {"center_rc": [cy, cx], "reason": "background_model_unreliable",
                              "background_error": round(error, 5)}
                if paired and delta_background[1] <= limit and all(t[3] < limit for t in profiles):
                    background_diagnostics.append(diagnostic)
                else:
                    uncertain.append(diagnostic)
                    n_sampled -= 1
            continue
        radius, bg, depth, increment = max(
            suspects, key=lambda t: (t[3] if paired else t[2])
            - max(RING_DIP_ABS, RING_DIP_REL * t[1]))
        limit = max(RING_DIP_ABS, RING_DIP_REL * bg)
        measurement = {
            "center_rc": [cy, cx], "radius_px": round(radius, 2),
            "radius_fwhm": round(radius / fwhm, 2),
            "dip_abs": round(depth, 5), "dip_rel": round(depth / max(bg, 1e-6), 4),
            "background_error": round(error, 5),
        }
        if paired:
            measurement.update(reference_dip_abs=round(depth - increment, 5),
                               added_dip_abs=round(increment, 5),
                               delta_background_error=round(delta_background[1], 5))
            if delta_background[1] > limit:
                measurement["reason"] = "paired_background_model_unreliable"
                uncertain.append(measurement)
                n_sampled -= 1
            elif increment < limit:
                preexisting.append(measurement)
            else:
                ringing.append(measurement)
        elif error > limit:
            measurement["reason"] = "background_model_unreliable"
            uncertain.append(measurement)
            n_sampled -= 1
        else:
            ringing.append(measurement)

    n_ring = len(ringing)
    fail_count = max(2, int(np.ceil(0.08 * n_sampled)))
    status = "failed" if n_ring >= fail_count else "warning" if n_ring or uncertain else "passed"
    if n_sampled < RING_MIN_SAMPLED and not uncertain:
        status = "skipped"
    if preexisting or background_diagnostics:
        if status == "passed":
            status = "warning"
    if reference is not None and not paired and status in ("passed", "skipped"):
        status = "warning"
    message = (f"{n_ring}/{n_sampled} stars with {'new or deepened' if paired else 'suspected'} "
               f"dark rings; {len(preexisting)} preexisting, {len(uncertain)} uncertain, "
               f"{len(background_diagnostics)} unchanged background diagnostics")
    if n_ring:
        message += "; 疑似反卷积/锐化振铃或去星修补发黑"
    if reference is not None and not paired:
        message += "; reference geometry or intensity domain is not comparable"
    return _gate(
        "STAR_RINGING", status, message,
        value={"n_sampled": n_sampled, "n_detected": len(centers), "n_ring": n_ring,
               "n_preexisting": len(preexisting), "n_uncertain": len(uncertain),
               "n_background_diagnostics": len(background_diagnostics),
               "fwhm_px": round(fwhm, 2)},
        threshold=f"dip and paired increment ≥ max({RING_DIP_ABS}, {RING_DIP_REL} × local background)",
        evidence={"ringing_stars": ringing[:16], "preexisting_stars": preexisting[:16],
                  "uncertain_stars": uncertain[:16], "background_diagnostics": background_diagnostics[:16],
                  "reference_comparable": paired,
                  "background_model": "clipped_local_plane", "coordinate_convention": "row_col"},
        escalate=bool(n_ring or uncertain or (reference is not None and not paired)),
    )


# ══════════════════════════════════════════════════════════════
# STAR_BLOAT：星点胀大（直接复用配对剖面）
# ══════════════════════════════════════════════════════════════

def check_star_bloat(reference, candidate):
    """星点整体胀大检测 —— star_tools.measure_paired_star_profiles 的门禁封装。

    复用现有工程阈值：成对 FWHM 中位比 > 1.50 或 p90 > 1.90 → review。
    """
    try:
        report = measure_paired_star_profiles(reference, candidate)
    except ValueError as exc:
        return _gate(
            "STAR_BLOAT", "skipped",
            f"配对剖面无法计算：{exc}",
        )

    if report.get("status") == "insufficient_samples":
        return _gate(
            "STAR_BLOAT", "skipped",
            "孤立未饱和星样本不足，胀大检测跳过"
            f"（{report.get('reason')}）",
            value={"n_paired_stars": report.get("n_paired_stars", 0)},
        )

    median_ratio = float(report.get(
        "candidate_to_reference_fwhm_ratio_median", 1.0))
    p90_ratio = float(report.get(
        "candidate_to_reference_fwhm_ratio_p90", 1.0))
    if report.get("status") == "review_required":
        return _gate(
            "STAR_BLOAT", "failed",
            f"成对星点 FWHM 中位比 {median_ratio:.2f}"
            f"（p90 {p90_ratio:.2f}），星点整体胀大",
            value={"median_ratio": round(median_ratio, 3),
                   "p90_ratio": round(p90_ratio, 3),
                   "n_paired_stars": report.get("n_paired_stars")},
            threshold="median≤1.50 且 p90≤1.90",
            evidence={"sample_centers_rc":
                      report.get("sample_centers_rc", [])[:16],
                      "coordinate_convention": "row_col"},
        )
    return _gate(
        "STAR_BLOAT", "passed",
        f"成对星点 FWHM 中位比 {median_ratio:.2f}，星点尺度稳定"
        f"（n={report.get('n_paired_stars')}）",
        value={"median_ratio": round(median_ratio, 3),
               "p90_ratio": round(p90_ratio, 3),
               "n_paired_stars": report.get("n_paired_stars")},
        threshold="median≤1.50 且 p90≤1.90",
    )


# ══════════════════════════════════════════════════════════════
# STAR_LAYER_LOSS / STAR_HOLES：星点层完整性
# ══════════════════════════════════════════════════════════════

def check_star_layer_integrity(reference, candidate, fwhm=None,
                               star_removal_intended=False,
                               star_reduction_step=False, reference_comparable=True):
    """Check stellar retention and newly introduced central undershoot.

    Reference centers are fixed. Hole depth uses a neighbor-masked local plane;
    its increment uses the same fit on candidate minus reference. A missing
    comparable intensity domain remains a review trigger. Intended removal
    skips both gates, while reduction suppresses only retention escalation.
    """
    ref_gray = _luminance(reference)
    cand_gray = _luminance(candidate)
    if ref_gray.shape != cand_gray.shape:
        raise ValueError(
            "reference and candidate must have identical shapes")
    if fwhm is None:
        fwhm, _n = _estimate_fwhm_or_default(ref_gray)
    fwhm = max(float(fwhm), 1.5)

    centers = _find_star_centers(
        ref_gray, fwhm, max_stars=150,
        peak_percentile=99.0, min_significance=0.08,
    )
    skip_gate = _gate(
        "STAR_LAYER_LOSS", "skipped",
        "可测星点不足，星点层完整性检测跳过",
        value={"n_sampled": len(centers)},
    )
    if len(centers) < 3:
        return [skip_gate, _gate(
            "STAR_HOLES", "skipped",
            "可测星点不足，暗坑检测跳过",
            value={"n_sampled": len(centers)},
        )]
    if star_removal_intended:
        reason = "星点剥离步骤（star_remove/star_process）审查：保留率与暗坑为预期行为"
        return [
            _gate("STAR_LAYER_LOSS", "skipped", reason,
                  value={"n_sampled": len(centers)}),
            _gate("STAR_HOLES", "skipped", reason,
                  value={"n_sampled": len(centers)}),
        ]

    h, w = ref_gray.shape
    annulus_r = (1.8 * fwhm, 3.0 * fwhm)
    annulus_patch = int(np.ceil(annulus_r[1])) + 1
    center_r = max(1.5, 0.5 * fwhm)

    retentions = []
    holes, preexisting, uncertain, background_diagnostics = [], [], [], []
    n_bright = 0
    excluded = _background_exclusion(ref_gray, fwhm) | _background_exclusion(cand_gray, fwhm)
    for cy, cx, ref_signal in centers:
        y0, y1 = cy - annulus_patch, cy + annulus_patch + 1
        x0, x1 = cx - annulus_patch, cx + annulus_patch + 1
        if y0 < 0 or x0 < 0 or y1 > h or x1 > w:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        dist = np.hypot(yy - cy, xx - cx)

        # 保留率：candidate 峰值信号（2px 窗口内最大值 − 局部 p20）
        win = cand_gray[max(0, cy - 2):cy + 3, max(0, cx - 2):cx + 3]
        cand_peak = float(np.max(win))
        cand_patch = cand_gray[y0:y1, x0:x1]
        cand_bg = float(np.percentile(cand_patch, 20.0))
        cand_signal = cand_peak - cand_bg
        if ref_signal > 1e-6:
            retentions.append(cand_signal / ref_signal)

        # Compare center deficits against a local plane on identical masks.
        if ref_signal >= 0.15:
            dy, dx = yy - cy, xx - cx
            outer = ((dist >= annulus_r[0]) & (dist <= annulus_r[1])
                     & ~excluded[y0:y1, x0:x1])
            background = _local_background(cand_patch, dx, dy, outer)
            if background is None:
                uncertain.append({"center_rc": [cy, cx], "reason": "insufficient_background"})
                continue
            plane, error = background
            center_mask = dist <= center_r
            bg = float(np.mean(plane[center_mask]))
            if bg < 0.005:
                continue
            n_bright += 1
            center_mean = float(np.mean(cand_patch[center_mask]))
            depth = bg - center_mean
            limit = max(RING_DIP_ABS, (1 - HOLE_CENTER_RATIO) * bg)
            delta_background = None
            if reference_comparable:
                delta = cand_patch - ref_gray[y0:y1, x0:x1]
                delta_background = _local_background(delta, dx, dy, outer)
            if depth < limit:
                if error > limit:
                    diagnostic = {"center_rc": [cy, cx], "reason": "background_model_unreliable",
                                  "background_error": round(error, 5)}
                    if (delta_background is not None and delta_background[1] <= limit
                            and float(np.mean((delta_background[0] - delta)[center_mask])) < limit):
                        background_diagnostics.append(diagnostic)
                    else:
                        uncertain.append(diagnostic)
                        n_bright -= 1
                continue
            measurement = {"center_rc": [cy, cx], "center_mean": round(center_mean, 5),
                           "background_at_center": round(bg, 5), "dip_abs": round(depth, 5),
                           "background_error": round(error, 5)}
            if not reference_comparable:
                measurement["reason"] = "reference_domain_not_comparable"
                uncertain.append(measurement)
                n_bright -= 1
                continue
            if delta_background is None:
                measurement["reason"] = "insufficient_paired_background"
                uncertain.append(measurement)
                n_bright -= 1
                continue
            increment = float(np.mean((delta_background[0] - delta)[center_mask]))
            measurement.update(reference_dip_abs=round(depth - increment, 5),
                               added_dip_abs=round(increment, 5),
                               delta_background_error=round(delta_background[1], 5))
            if delta_background[1] > limit:
                measurement["reason"] = "paired_background_model_unreliable"
                uncertain.append(measurement)
                n_bright -= 1
            elif increment < limit:
                preexisting.append(measurement)
            else:
                holes.append(measurement)

    # ── STAR_LAYER_LOSS ──
    if not retentions:
        loss_gate = skip_gate
    else:
        retention = float(np.median(retentions))
        if retention < RETENTION_FAILED:
            loss_gate = _gate(
                "STAR_LAYER_LOSS", "failed",
                f"星点峰值保留率中位 {retention:.2f}（< {RETENTION_FAILED}），"
                "星点层明显损失",
                value={"retention_median": round(retention, 3),
                       "n_sampled": len(retentions)},
                threshold=f"≥{RETENTION_FAILED}",
                escalate=not star_reduction_step,
            )
        elif retention < RETENTION_WARNING:
            loss_gate = _gate(
                "STAR_LAYER_LOSS", "warning",
                f"星点峰值保留率中位 {retention:.2f}，星点有弱化迹象",
                value={"retention_median": round(retention, 3),
                       "n_sampled": len(retentions)},
                threshold=f"≥{RETENTION_WARNING}",
                escalate=not star_reduction_step,
            )
        else:
            loss_gate = _gate(
                "STAR_LAYER_LOSS", "passed",
                f"星点峰值保留率中位 {retention:.2f}"
                f"（n={len(retentions)}）",
                value={"retention_median": round(retention, 3),
                       "n_sampled": len(retentions)},
                threshold=f"≥{RETENTION_WARNING}",
            )

    # Inherited deficits remain visible without blaming this processing stage.
    n_holes = len(holes)
    fail_count = max(2, int(np.ceil(0.08 * n_bright)))
    status = "failed" if n_holes >= fail_count else "warning" if n_holes or uncertain else "passed"
    if n_bright < 3 and not uncertain:
        status = "skipped"
    if (preexisting or background_diagnostics) and status == "passed":
        status = "warning"
    if not reference_comparable and status in ("passed", "skipped"):
        status = "warning"
    hole_gate = _gate(
        "STAR_HOLES", status,
        f"{n_holes}/{n_bright} stars with new or deepened central deficits; "
        f"{len(preexisting)} preexisting, {len(uncertain)} uncertain, "
        f"{len(background_diagnostics)} unchanged background diagnostics",
        value={"n_holes": n_holes, "n_bright": n_bright,
               "n_preexisting": len(preexisting), "n_uncertain": len(uncertain),
               "n_background_diagnostics": len(background_diagnostics)},
        threshold=f"dip and paired increment ≥ max({RING_DIP_ABS}, {1 - HOLE_CENTER_RATIO:.2f} × local background)",
        evidence={"holes": holes[:16], "preexisting_holes": preexisting[:16],
                  "uncertain_stars": uncertain[:16], "background_diagnostics": background_diagnostics[:16],
                  "reference_comparable": reference_comparable,
                  "background_model": "clipped_local_plane", "coordinate_convention": "row_col"},
        escalate=bool(n_holes or uncertain or not reference_comparable),
    )
    return [loss_gate, hole_gate]


# ══════════════════════════════════════════════════════════════
# CORE_BURNING：核心死白（highlight_clip_ratio 的连通域升级）
# ══════════════════════════════════════════════════════════════

def _clip_components(gray):
    mask = gray >= 0.995
    labeled, n = label(mask, structure=np.ones((3, 3), dtype=int))
    components = []
    if n:
        areas = np.bincount(labeled.ravel())
        for index, bounds in enumerate(find_objects(labeled), 1):
            if bounds is None:
                continue
            area = int(areas[index])
            ys, xs = np.where(labeled[bounds] == index)
            ys = ys + bounds[0].start
            xs = xs + bounds[1].start
            eq_diam = 2.0 * np.sqrt(area / np.pi)
            components.append({
                "label": index,
                "area_px": area,
                "centroid_rc": [float(np.mean(ys)), float(np.mean(xs))],
                "eq_diameter_px": round(float(eq_diam), 2),
            })
    return mask, labeled, components


def check_channel_clipping(candidate, reference=None):
    """Locate channel clipping; compare only registered, same-domain images."""
    candidate = np.asarray(candidate, dtype=np.float32)
    _luminance(candidate)
    if reference is not None and np.shape(reference) != candidate.shape:
        raise ValueError('Channel clipping requires identical image geometry')
    if reference is not None:
        _luminance(reference)
    channels = candidate[..., :3] if candidate.ndim == 3 else candidate[..., None]
    references = (np.asarray(reference)[..., :3] if candidate.ndim == 3
                  else np.asarray(reference)[..., None]) if reference is not None else None
    evidence = {}
    significant = []
    megapixels = _image_megapixels(candidate.shape)
    min_area = _scaled_area_threshold(BURN_MIN_AREA_PX, megapixels)
    big_area = _scaled_area_threshold(BURN_BIG_AREA_PX, megapixels)
    for i, name in enumerate(('r', 'g', 'b')[:channels.shape[-1]]):
        mask, labeled, components = _clip_components(channels[..., i])
        ref_mask = (references[..., i] >= 0.995 if references is not None
                    else np.zeros(mask.shape, dtype=bool))
        ref_expanded = binary_dilation(ref_mask, iterations=2) if references is not None else ref_mask
        additions = np.bincount(labeled[mask & ~ref_expanded], minlength=len(components) + 1)
        regions = []
        for component in components:
            area = int(additions[component['label']])
            if area >= min_area:
                regions.append({**component, 'new_area_px': area})
                significant.append(area)
        evidence[name] = {
            'clip_ratio': float(np.mean(mask)),
            'reference_clip_ratio': float(np.mean(ref_mask)) if references is not None else None,
            'regions': regions[:16],
            'n_regions': len(regions),
        }
    status = ('failed' if any(area >= big_area for area in significant)
              or len(significant) >= 2 else 'warning' if significant else 'passed')
    return _gate('CHANNEL_CLIPPING', status,
                 'Channel clipping requires review' if significant else 'No significant new channel clipping',
                 value=evidence, threshold='clip>=0.995; new region>=25px; large region>=80px',
                 evidence={'channels': evidence, 'reference_comparable': reference is not None,
                           'coordinate_convention': 'row_col'}, escalate=bool(significant))


def check_core_plateau(candidate, reference, subject_mask=None, minimum_level=.5, region_mask=None):
    """Locate new float plateaus, including flattened cores below clipping."""
    gray, original = _luminance(candidate), _luminance(reference)
    if gray.shape != original.shape:
        raise ValueError('Core plateau geometry must match')
    if subject_mask is not None and np.shape(subject_mask) != gray.shape:
        raise ValueError('Core plateau subject mask geometry must match')
    roi = original >= np.percentile(original, 99.5)
    if subject_mask is not None:
        roi |= np.asarray(subject_mask) > 0

    if region_mask is not None:
        if np.shape(region_mask) != gray.shape:
            raise ValueError('Core plateau region geometry must match')
        roi &= np.asarray(region_mask, dtype=bool)
    if not np.isfinite(minimum_level):
        raise ValueError('Core plateau minimum level must be finite')

    def flat_mask(arr):
        differences = np.zeros_like(arr)
        dx, dy = np.abs(np.diff(arr, axis=1)), np.abs(np.diff(arr, axis=0))
        differences[:, 1:] = np.maximum(differences[:, 1:], dx)
        differences[:, :-1] = np.maximum(differences[:, :-1], dx)
        differences[1:, :] = np.maximum(differences[1:, :], dy)
        differences[:-1, :] = np.maximum(differences[:-1, :], dy)
        return differences <= 1e-6

    mask = flat_mask(gray) & (gray >= minimum_level) & roi
    inherited = flat_mask(original)
    labels, count = label(mask, structure=np.ones((3, 3), dtype=int))
    regions, preexisting = [], []
    areas = np.bincount(labels.ravel())
    for i, bounds in enumerate(find_objects(labels), 1):
        if bounds is None or areas[i] < 25:
            continue
        part = labels[bounds] == i
        ys, xs = np.where(part)
        item = {'area_px': int(areas[i]), 'centroid_rc': [float(ys.mean()+bounds[0].start), float(xs.mean()+bounds[1].start)],
                'level': float(np.median(gray[bounds][part]))}
        new_area = int(np.count_nonzero(part & ~inherited[bounds]))
        item['new_area_px'] = new_area
        (regions if new_area >= 25 else preexisting).append(item)
    return _gate('CORE_PLATEAU', 'warning' if regions else 'passed',
                 'New float core plateaus require review' if regions else 'No significant new core plateau',
                 value={'new_regions': regions[:16], 'inherited_regions': preexisting[:16],
                        'new_area_px': sum(r['new_area_px'] for r in regions + preexisting),
                        'inherited_area_px': sum(r['area_px'] - r['new_area_px'] for r in regions + preexisting)},
                 threshold=f'level>={minimum_level}; adjacent difference<=1e-6; new area>=25px',
                 evidence={'coordinate_convention': 'row_col', 'reference_kind': 'source_geometry'},
                 escalate=bool(regions))


def check_core_burning(candidate, reference=None, fwhm=None):
    """高光死白/烧毁连通域检测与**定位**。

    有 reference：只对"处理引入"的死白判 failed —— 新生连通域
    （与 reference 死白区无重叠，含 2px 膨胀容差）或总面积显著增长；
    输入自带的饱和星芯不算处理缺陷。
    无 reference：绝对检测，只记录不升级（escalate=False）。
    """
    gray = _luminance(candidate)
    if fwhm is None:
        fwhm, _n = _estimate_fwhm_or_default(gray)
    fwhm = max(float(fwhm), 1.5)
    star_core_max_diam = max(4.0 * fwhm, 12.0)

    cand_mask, _cand_labeled, cand_components = _clip_components(gray)
    n_pixels = int(gray.size)
    megapixels = _image_megapixels(gray.shape)
    min_area = _scaled_area_threshold(BURN_MIN_AREA_PX, megapixels)
    big_area = _scaled_area_threshold(BURN_BIG_AREA_PX, megapixels)

    if reference is None:
        star_cores = [c for c in cand_components
                      if c["eq_diameter_px"] <= star_core_max_diam
                      and c["area_px"] >= min_area]
        regions = [c for c in cand_components
                   if c["eq_diameter_px"] > star_core_max_diam]
        status = "warning" if any(
            c["area_px"] >= 100 for c in star_cores + regions) else "passed"
        return _gate(
            "CORE_BURNING", status,
            f"绝对检测：{len(cand_components)} 处死白连通域"
            f"（星点级 {len(star_cores)}，大面积 {len(regions)}）；"
            "无参考图，不判定处理责任，仅记录",
            value={"n_components": len(cand_components),
                   "n_star_cores": len(star_cores),
                   "total_area_px": int(np.count_nonzero(cand_mask))},
            threshold="需要 reference 才能判定处理引入的烧毁",
            evidence={"components":
                      cand_components[:16],
                      "coordinate_convention": "row_col"},
            escalate=False,
        )

    ref_gray = _luminance(reference)
    if ref_gray.shape != gray.shape:
        raise ValueError(
            "reference and candidate must have identical shapes")
    ref_mask, _ref_labeled, ref_components = _clip_components(ref_gray)
    ref_mask_dilated = binary_dilation(ref_mask, iterations=2)

    new_star_cores = []
    new_regions = []
    for comp in cand_components:
        # 用质心落点近似归属：质心在 reference 死白（膨胀 2px 容差）
        # 内 → 处理前已存在（输入自带饱和）；否则为处理引入的新生连通域。
        cy, cx = comp["centroid_rc"]
        existing = bool(ref_mask_dilated[int(cy), int(cx)])
        if existing:
            continue
        if comp["eq_diameter_px"] <= star_core_max_diam:
            if comp["area_px"] >= min_area:
                new_star_cores.append(comp)
        else:
            new_regions.append(comp)

    cand_total = int(np.count_nonzero(cand_mask))
    ref_total = int(np.count_nonzero(ref_mask))
    growth = cand_total - ref_total
    growth_ratio = growth / max(ref_total, 1)

    burnt_cores = new_star_cores
    n_new = len(burnt_cores)
    big_new = [c for c in burnt_cores if c["area_px"] >= big_area]
    spread_burn = (
        growth >= 0.0015 * n_pixels and growth_ratio > 0.5
    )

    if n_new >= 2 or big_new or spread_burn:
        status = "failed"
        detail_areas = ", ".join(
            f"{c['area_px']}px²@({int(c['centroid_rc'][0])},"
            f"{int(c['centroid_rc'][1])})"
            for c in burnt_cores[:4])
        message = (
            f"检出 {n_new} 处处理引入的星点核心死白（{detail_areas}）"
            + (f"，死白总面积增长 {growth_ratio:.0%}"
               if spread_burn else "")
            + " —— 疑似拉伸+锐化叠加烧毁，考虑 ghs_hp 高光余量或回落 sharpen_amount"
        )
    elif n_new == 1:
        status = "warning"
        c = burnt_cores[0]
        message = (
            f"检出 1 处新生星点核心死白"
            f"（{c['area_px']}px²@({int(c['centroid_rc'][0])},"
            f"{int(c['centroid_rc'][1])})），建议视觉复核"
        )
    elif new_regions:
        status = "warning"
        message = (
            f"未检出新生星点核心死白，但有 {len(new_regions)} 处大面积"
            "新生死白区域，请确认是否为核心过曝扩张"
        )
    else:
        status = "passed"
        message = (
            f"无处理引入的死白连通域"
            f"（参考图自带 {len(ref_components)} 处，输出 {len(cand_components)} 处）"
        )
    return _gate(
        "CORE_BURNING", status, message,
        value={"n_new_star_cores": n_new,
               "n_new_regions": len(new_regions),
               "clip_area_growth_px": growth,
               "clip_area_growth_ratio": round(growth_ratio, 4),
               "reference_clip_area_px": ref_total,
               "candidate_clip_area_px": cand_total},
        threshold=f"新生星点级死白<{min_area:.0f}px² 不计（8.3MP 基准 "
                  f"{BURN_MIN_AREA_PX}px²，按 {megapixels:.1f}MP 缩放），"
                  f"≥2 处或单处≥{big_area:.0f}px² 判 failed",
        evidence={"new_star_cores": new_star_cores[:16],
                  "new_regions": new_regions[:8],
                  "coordinate_convention": "row_col"},
    )


# ══════════════════════════════════════════════════════════════
# INPAINT_FOOTPRINT：去星修补足迹（拉伸后被放大成可见斑块）
# ══════════════════════════════════════════════════════════════
#
# 机理：线性域形态学去星用 inpaint 填掉星点像素。若修复半径或星点蒙版失配，
# 会在星位留下与邻域不一致的补丁。线性域里这些补丁只有 ~1e-4 量级、看不出来，
# 但极暗母版的拉伸增益可达 ~270×，补丁随之变成肉眼可见的暗斑/亮斑/过平滑区。
#
# 因此本门禁必须在**拉伸后的无星图**上跑，且星点位置来自线性域检测出的
# star mask（位置是几何量，域无关，可直接复用）。
#
# 注意：本门禁**不**进入 evaluate_star_artifact_gates 聚合器——那个聚合器的
# code 集合被 tests/test_artifact_gates.py 精确断言为 5 个具名门禁。本门禁走
# evaluate_quality_gates 的标量路径（见 agent_protocol）。

FOOTPRINT_MIN_SAMPLED = 5      # 可测星位 < 5 → skipped
# inpaint 的物理签名是「平滑填充」：补丁**既**缺纹理（std 显著低于邻域）**又**与
# 邻域有亮度落差。两个条件必须**同时**满足——
#   只看纹理：任何 inpaint（包括成功的）都缺纹理 → 全图误报；
#   只看落差：拉伸后的星云结构落差远大于足迹 → 实测 63% 误报。
# 实测某确有可见斑块的文件：联合判据命中 10.5%，单看落差命中 63%，单看纹理 36%。
FOOTPRINT_SMOOTH_RATIO = 0.35  # 中心 std < 35% × 环带 std（缺纹理）
FOOTPRINT_MIN_DELTA = 0.020    # 且 |中心中值 − 环带中值| ≥ 0.020（有落差）
FOOTPRINT_MAX_STARS = 200
FOOTPRINT_MAX_AREA_FRAC = 0.002       # 单块 > 0.2% 画面 → 星云误检，跳过
FOOTPRINT_MAX_STAR_AREA_FACTOR = 6.0  # 面积 > 6π·fwhm² → 非星点（星云丝/亮核）
FOOTPRINT_FAIL_FRAC = 0.08     # 命中比例 ≥ 8% → failed
FOOTPRINT_WARN_FRAC = 0.02     # ≥ 2% → warning
FOOTPRINT_EVIDENCE_MAX = 16


def check_inpaint_footprint(candidate, star_mask, fwhm=None,
                            max_stars=FOOTPRINT_MAX_STARS):
    """在星点蒙版位置上量「拉伸后可见的 inpaint 修补足迹」。

    candidate: 拉伸后的**无星**图（合成星点前最可见）。
    star_mask: 线性域检测出的星点掩膜（> 0.5 视为星位）。
    fwhm:      **线性域**的 FWHM（px）。必须由调用方传入——蒙版来自线性域，
               若在这里从拉伸后的 candidate 现估 FWHM，会拿到被拉伸放大的
               9~10px（线性真实值约 3.9px），几何关系全错。

    判据是**联合**的：补丁缺纹理（std 显著低于邻域）**且**与邻域有亮度落差。
    单独任一条都会大量误报（见上方阈值注释的实测数字）。
    """
    gray = _luminance(candidate)
    mask = np.asarray(star_mask, dtype=np.float32)
    if mask.ndim == 3:
        mask = mask.mean(axis=2)
    if mask.shape != gray.shape:
        return _gate(
            "INPAINT_FOOTPRINT", "skipped",
            f"星点蒙版形状 {mask.shape} 与图像 {gray.shape} 不一致，足迹检测跳过",
            value={"mask_shape": list(mask.shape), "image_shape": list(gray.shape)},
        )

    mask_bin = mask > 0.5
    if not mask_bin.any():
        return _gate("INPAINT_FOOTPRINT", "skipped", "星点蒙版为空，足迹检测跳过",
                     value={"n_sampled": 0})

    if fwhm is None:
        fwhm, _n = _estimate_fwhm_or_default(gray)
    fwhm = max(float(fwhm), 1.5)

    labeled, n_components = label(mask_bin, structure=np.ones((3, 3), int))
    if n_components < FOOTPRINT_MIN_SAMPLED:
        return _gate(
            "INPAINT_FOOTPRINT", "skipped",
            f"去星位置不足（{n_components} < {FOOTPRINT_MIN_SAMPLED}），足迹检测跳过",
            value={"n_sampled": int(n_components)},
        )

    # 去掉低频背景与星云坡度，只留局部结构
    residual = gray - gaussian_filter(gray, sigma=max(6.0, 3.0 * fwhm))
    abs_resid = np.abs(residual)
    quiet = abs_resid < np.percentile(abs_resid, 50.0)
    noise = 1.4826 * float(np.median(abs_resid[quiet])) if quiet.any() else 0.0

    areas = np.bincount(labeled.ravel())
    # 星点蒙版里混有星云丝/亮核，必须按「星点尺度」过滤掉。
    max_area = min(
        FOOTPRINT_MAX_AREA_FRAC * gray.size,
        FOOTPRINT_MAX_STAR_AREA_FACTOR * np.pi * fwhm * fwhm,
    )
    # 采样顺序：优先「面积最接近典型星点蒙版面积」的连通域，而不是最大的那些。
    # inpaint 足迹只出现在**星点**位置；按面积降序采样会先挑到星云大块，实测
    # 因此把命中率从 ~10% 压到 <1%（掩版里星云块占多数时尤其明显）。
    band_mid = 2.0 * np.pi * fwhm * fwhm
    pref = np.abs(
        np.log(np.maximum(areas[1:].astype(np.float64), 1.0)) - np.log(band_mid)
    )
    order = np.argsort(pref) + 1
    h, w = gray.shape

    flagged = []
    ratios = []
    n_sampled = 0
    for idx in order[:int(max_stars)]:
        area = int(areas[idx])
        if area == 0 or area > max_area:
            continue
        ys, xs = np.where(labeled == idx)
        if ys.size == 0:
            continue
        cy = float(np.mean(ys))
        cx = float(np.mean(xs))
        # 核心半径贴合蒙版本身的等效半径（**不要**放大）：核心取大了会被未受损
        # 像素稀释，纹理与落差两个统计量都会失效。
        radius = max(2.0, float(np.sqrt(area / np.pi)))
        outer = 3.0 * fwhm
        # 按 patch 局部构造坐标网格：全局 np.mgrid 是 O(stars·N)，大图上会卡死
        y0 = max(0, int(cy - outer) - 1)
        y1 = min(h, int(cy + outer) + 2)
        x0 = max(0, int(cx - outer) - 1)
        x1 = min(w, int(cx + outer) + 2)
        if y1 - y0 < 3 or x1 - x0 < 3:
            continue
        py, px = np.ogrid[y0:y1, x0:x1]
        dist = np.hypot(py - cy, px - cx)
        patch = residual[y0:y1, x0:x1]

        core = patch[dist <= radius]
        annulus = patch[(dist >= 1.8 * fwhm) & (dist <= outer)]
        if core.size < 4 or annulus.size < 12:
            continue
        n_sampled += 1

        ring_std = float(np.std(annulus))
        if ring_std < 2.0 * noise:
            continue  # 邻域本身就没纹理，无从判断「缺纹理」
        texture_ratio = float(np.std(core)) / ring_std
        delta = float(np.median(core)) - float(np.median(annulus))
        ratios.append(texture_ratio)

        if texture_ratio < FOOTPRINT_SMOOTH_RATIO and abs(delta) >= FOOTPRINT_MIN_DELTA:
            flagged.append({
                "center_rc": [int(round(cy)), int(round(cx))],
                "kind": "bright" if delta > 0 else "dark",
                "center_resid": round(delta, 5),
                "texture_ratio": round(texture_ratio, 4),
                "annulus_median": round(float(np.median(annulus)), 5),
                "area_px": area,
            })

    if n_sampled < FOOTPRINT_MIN_SAMPLED:
        return _gate(
            "INPAINT_FOOTPRINT", "skipped",
            f"可测去星位置不足（{n_sampled} < {FOOTPRINT_MIN_SAMPLED}），足迹检测跳过",
            value={"n_sampled": int(n_sampled)},
        )

    n_dark = sum(1 for f in flagged if f["kind"] == "dark")
    n_bright = sum(1 for f in flagged if f["kind"] == "bright")
    n_flag = len(flagged)
    frac = n_flag / n_sampled

    value = {
        "n_sampled": int(n_sampled),
        "n_flagged": int(n_flag),
        "n_dark": int(n_dark),
        "n_bright": int(n_bright),
        "flagged_frac": round(frac, 4),
        "median_texture_ratio": round(float(np.median(ratios)), 4) if ratios else None,
        "fwhm_px": round(fwhm, 2),
    }
    threshold = (
        f"flagged_frac<{FOOTPRINT_FAIL_FRAC:.0%} 且每个足迹需同时满足 "
        f"std比<{FOOTPRINT_SMOOTH_RATIO} 与 |落差|≥{FOOTPRINT_MIN_DELTA}"
    )
    evidence = {
        "footprints": flagged[:FOOTPRINT_EVIDENCE_MAX],
        "coordinate_convention": "row_col",
    }

    if frac >= FOOTPRINT_FAIL_FRAC:
        return _gate(
            "INPAINT_FOOTPRINT", "failed",
            f"{n_flag}/{n_sampled}（{frac:.1%}）处去星位置检出修补足迹"
            f"（{n_dark} 暗斑 / {n_bright} 亮斑）：补丁同时缺纹理且与邻域有落差，"
            "疑似 inpaint 半径或去星蒙版失配",
            value=value, threshold=threshold, evidence=evidence, escalate=True,
        )
    if frac >= FOOTPRINT_WARN_FRAC:
        return _gate(
            "INPAINT_FOOTPRINT", "warning",
            f"{n_flag}/{n_sampled}（{frac:.1%}）处去星位置检出修补足迹，建议视觉复核",
            value=value, threshold=threshold, evidence=evidence, escalate=False,
        )
    return _gate(
        "INPAINT_FOOTPRINT", "passed",
        f"{n_sampled} 处去星位置未见修补足迹（命中 {n_flag}，{frac:.1%}）",
        value=value, threshold=threshold, evidence=evidence, escalate=False,
    )


# ══════════════════════════════════════════════════════════════
# 聚合器
# ══════════════════════════════════════════════════════════════

def evaluate_star_artifact_gates(reference, candidate, steps=None,
                                 reference_comparable=True, display_reference=False):
    """运行全部星点具名伪影门禁并聚合。

    reference 为 None 时只跑 candidate 侧门禁（RINGING / 绝对 BURNING）。
    status 规则与 evaluate_quality_gates 一致：
    任何 escalate 的 warning/failed → review_required。
    """
    steps = set(steps or [])
    gray = _luminance(reference if reference is not None and reference_comparable else candidate)
    fwhm, n_fwhm = _estimate_fwhm_or_default(gray)

    star_removal_intended = bool(
        steps & {"star_remove", "star_process"})
    star_reduction_step = "star_reduce" in steps

    ring_comparable = reference_comparable and (not display_reference or np.array_equal(reference, candidate))
    gates = [check_ringing(candidate, fwhm=fwhm, reference=reference,
                           reference_comparable=ring_comparable)]
    if 'stretch' in steps and gates[0]['value']['n_sampled'] < RING_MIN_SAMPLED:
        gates[0].update(status='warning', escalate=True)
        gates[0]['message'] += '; insufficient star samples for stretch acceptance'
        gates[0]['evidence']['insufficient_samples'] = True
    if reference is not None:
        if reference_comparable:
            gates.append(check_star_bloat(reference, candidate))
        else:
            gates.append(_gate('STAR_BLOAT', 'skipped',
                               'Cross-domain FWHM is not a physical expansion measurement',
                               evidence={'reference_comparable': False}, escalate=False))
        gates.extend(check_star_layer_integrity(
            reference, candidate, fwhm=fwhm,
            star_removal_intended=star_removal_intended,
            star_reduction_step=star_reduction_step,
            reference_comparable=reference_comparable,
        ))
    if display_reference:
        gates[0]['evidence']['attribution'] = ('identical_display_pixels' if ring_comparable
                                              else 'unconfirmed_independent_display_curves')
        gates = [_gate('STAR_LAYER_LOSS', 'skipped',
                       'Independent display curves do not measure stellar signal retention',
                       evidence={'measurement_domain': 'display'}, escalate=False)
                 if g['code'] == 'STAR_LAYER_LOSS' else g for g in gates]
    gates.append(check_core_burning(
        candidate,
        reference=reference if reference_comparable else None, fwhm=fwhm))

    escalating = [
        g for g in gates
        if g.get("escalate", True) and g["status"] in ("warning", "failed")
    ]
    triggered = [g["code"] for g in escalating]
    return {
        "schema": SCHEMA,
        "status": "review_required" if escalating else "success",
        "summary": (
            f"{len(triggered)} 项星点门禁触发：{', '.join(triggered)}"
            if triggered else
            "No escalating star artifacts; inherited/background diagnostics are retained"
        ),
        "gates": gates,
        "context": {
            "steps": sorted(steps),
            "reference_provided": reference is not None,
            "reference_comparable": reference is not None and reference_comparable,
            "fwhm_px": round(fwhm, 2),
            "fwhm_n_stars": n_fwhm,
        },
        "coordinate_convention": "row_col",
        "data_domain": "named_star_artifact_diagnostic",
    }


def main():
    parser = argparse.ArgumentParser(
        description="具名星点伪影门禁（暗环/胀大/星点层/死白核心）")
    parser.add_argument(
        "reference",
        help="处理前参考图（FITS/XISF/TIFF/PNG/JPG）；"
             "只检测 candidate 时可传 'none'")
    parser.add_argument(
        "candidate", help="处理后待检图")
    parser.add_argument(
        "--steps", default="",
        help="逗号分隔的处理步骤（如 star_remove,stretch），"
             "用于步骤感知跳过")
    parser.add_argument(
        "--output", default=None, help="结果 JSON 输出路径")
    args = parser.parse_args()

    from fits_io import read_image

    candidate, _meta = read_image(args.candidate)
    candidate = np.clip(
        np.asarray(candidate, dtype=np.float32)[..., :3]
        if candidate.ndim == 3 else candidate, 0, 1)

    reference = None
    if args.reference.lower() != "none":
        reference, _r_meta = read_image(args.reference)
        reference = np.clip(
            np.asarray(reference, dtype=np.float32)[..., :3]
            if reference.ndim == 3 else reference, 0, 1)

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    result = evaluate_star_artifact_gates(
        reference, candidate, steps=steps,
        reference_comparable=reference is not None and _r_meta["is_linear"] == _meta["is_linear"],
    )

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"[artifact_gates] 结果已写入 {args.output}")
    print(text)


if __name__ == "__main__":
    main()
