#!/usr/bin/env python3
"""保真度门禁（Named Fidelity Gates）。

与 `artifact_gates.py` 的「星点伪影门禁」**并存而非替换**，补齐该体系缺失的
三个测量维度：

  噪声增益  BACKGROUND_NOISE_BOUNDED
            BACKGROUND_RELATIVE_NOISE_BOUNDED
  色度保真  HUE_PRESERVED
            SUBJECT_CHROMA_VISIBILITY_PRESERVED
            BACKGROUND_CHROMA_NOISE_BOUNDED
  参考可信度  REFERENCE_AUTHENTICITY_GATE_CODES —— 参考图存在时上述门不得被放松

`artifact_gates` 回答「星点周围有没有可见伪影」，本模块回答「**处理有没有
放大噪声、破坏色度**」。两者互不可替代：`agent_protocol.evaluate_quality_gates`
的 `BACKGROUND_COLOR_CAST` 只看**静态**每通道背景中位数比，严重偏色的输出
（例如背景 R/G≈3.3）能一路通过；本模块做**逐像素相对参考图的**漂移测量。

三态设计（承接「门禁阈值按灾难形态标定会留静默通过区」的教训）：

  不可测量        → status="unavailable", escalate=False   仅记录
  健康（v < d）   → **不 append**                        完全静默
  退化（d≤v<h）   → status="degraded",   escalate=False   进 warnings，不升级
  硬失败（v ≥ h） → status="warning"/"failed", 见THRESHOLDS

`degraded` 态的合法性依据：`agent_protocol.evaluate_quality_gates` 的
`warnings` 收集无status 过滤（可见），而 `quality_state.collect_quality_gates`
与 `build_quality_state` 都过滤 `status in ("warning","failed")`（不产生 issue）。
因此 degraded **可见但不升级、不膨胀 issue 集合**，无需改动 `quality_state`。

所有度量都是**显示域、比较性**的，不是目录测光校色，也不是物理 SNR 测量。
每个 gate 的 `evidence["physical_snr"]` 恒为 False，message 内也写明这一点，
防止下游把它当物理量引用。

门禁清单的 schema 与 `artifact_gates._gate` **逐键一致**，这是
`quality_state` / pipeline warnings 路径能零改动消费的前提。这里刻意
**重新实现**而非 import 那个私有名，以免把两个模块焊死在同一个内部契约上。

CLI:
  python scripts/fidelity_gates.py candidate.tif reference.tif \
      [--steps stretch] [--target-type emission_nebula] \
      [--geometry display] [--json out.json]

  --geometry display 会先过一遍与 pipeline 同口径的降采样
  （GATE_PREVIEW_MAX_SIDE），保证标定口径与运行时一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


SCHEMA = "fidelity_gates/1.0"

# 与 pipeline.GATE_PREVIEW_MAX_SIDE 对齐。标定与运行必须同口径：降采样带
# anti_aliasing=True，会平滑掉高频噪声，1400px 上的 sigma 系统性低于全分辨率。
GATE_PREVIEW_MAX_SIDE = 1400

# 背景样本下限。B 用 64；A 的背景口径多了 alive 过滤（三通道全活），样本数
# 显著少于 B 的 luma<=p45，沿用 64 会在中等亮度图上给出不稳定的 sigma。
# MAD 在 n=256 时相对标准误约 7.8%。
MIN_BACKGROUND_PIXELS = 256

# 主体样本下限，对齐 starun 侧 adaptive_stretch 的 insufficient_regions 口径。
MIN_SIGNAL_PIXELS = 64

# 色度支撑可用性判据，对齐 starun 侧 adaptive_stretch 的 available 定义。
CHROMA_MIN_SOURCE_SIGMA = 1e-8
CHROMA_MIN_SOURCE_SNR = 0.25

MAD_TO_SIGMA = 1.4826

# 硬上限：配置可以更严，但**任何目标类型或参考图都不得放松到这些值之上**。
# 相对噪声与背景色度噪声沿用 starun 侧的专业基线 1.60 / 1.05；主体色度
# 保留比沿用其下界 0.95。这里不提供任何 cfg 覆盖入口——连放宽的接口都不给。
HARD_CEILING_RELATIVE_NOISE = 1.60
HARD_CEILING_CHROMA_NOISE = 1.05
HARD_FLOOR_CHROMA_SNR = 0.95

# 阈值表。degraded / hard 由 references/fidelity_gate_calibration.md 的实测
# 分布标定回填。
#
# 标定于 4 个目标 / 3 种target_type：
#   NGC6888(emission_nebula) M31(galaxy) M45(open_cluster) M17(emission_nebula)
# 口径 = 降采样 1400px（与运行时同几何）。健康组分位用 p90，hard 取
# 「劣化组最轻档」与「健康 p95×1.5」的较大者。
#
# **BACKGROUND_NOISE_BOUNDED 的标定结论是「绝对σ 不可用」**：健康组跨目标
# 相差约 150 倍（M31 6.0e-05 vs NGC6888 9.2e-03），因为 σ 随背景亮度量级漂移，
# 单一阈值必然要么全静默、要么全误报。该门因此**降级为只记录**（hard=None
# ⇒ uncalibrated），噪声判定全部交给相对量门 BACKGROUND_RELATIVE_NOISE_BOUNDED。
# 这是「按灾难形态标定会留静默通过区」的正向应用：实测证明该形态不成立，
# 就改设计而不是硬调数字。
#
# 单例/稀疏标定是已知缺陷：随样本积累必须复核。
THRESHOLDS = {
    # 绝对 σ：跨目标不可比 ⇒ 只记录不判定。
    "BACKGROUND_NOISE_BOUNDED": {
        "degraded": None,
        "hard": None,
        "unit": "display_luma_sigma",
        "note": "absolute sigma varies ~150x across targets; diagnostic only",
    },
    # 相对噪声增益：健康组 1.00–1.15，劣化组（注入 0.3σ 噪声）打到 1.60 上限。
    "BACKGROUND_RELATIVE_NOISE_BOUNDED": {
        "degraded": 1.140661,
        "hard": 1.60,
        "unit": "ratio",
    },
    # 色度漂移：健康组 0.0000–0.00142；饱和度 ×1.15 劣化组 0.0081–0.0379。
    # 两者分离清晰，hard 取劣化组最轻档 0.0379。
    "HUE_PRESERVED": {
        "degraded": 0.004000,
        "hard": 0.037926,
        "unit": "p95_chromaticity_drift",
    },
    # 主体色度保留比：健康组 1.000–1.016。**「压主体」不是有效劣化对照**
    # （分子分母同步骤降，比值反而略升到 1.006–1.018）⇒ 无法从数据定 hard，
    # 保守取专业基线 0.95 / 0.85：只在主体色度严重流失时才提示。
    "SUBJECT_CHROMA_VISIBILITY_PRESERVED": {
        "degraded": 0.950000,
        "hard": 0.850000,
        "unit": "ratio",
    },
    # 背景色度噪声：健康组 0.995–1.000；饱和度 ×1.15 劣化组 1.14–1.15。
    "BACKGROUND_CHROMA_NOISE_BOUNDED": {
        "degraded": 1.020000,
        "hard": 1.120000,
        "unit": "ratio",
    },
}

# 标定覆盖的不同目标类型数。< 3 时强制 PROVISITIONAL，全部新门 escalate=False：
# 保证「阈值不准」的最坏后果只损失召回，不制造全量误报。
CALIBRATION_CORPUS_SIZE = 3
PROVISIONAL = CALIBRATION_CORPUS_SIZE < 3

# hard 触发时才具备升级能力的门。色度三门在 v1 恒 False —— 它们的合理阈值
# 强依赖「这个目标该有多饱和」，而 A 侧 target_type 只有 10 类
# （target_rules._KNOWN_TYPES），不足以分档。
#
# BACKGROUND_NOISE_BOUNDED 不在集合内：标定证明绝对 σ 跨目标相差约 150 倍
# （M31 6.0e-05 vs NGC6888 9.2e-03），单一阈值不可用，该门降级为只记录
# （见THRESHOLDS 注释）。噪声判定由相对量门承担。
ESCALATING_CODES = frozenset({
    "BACKGROUND_RELATIVE_NOISE_BOUNDED",
})

# 参考可信度：参考图存在时，这些门的结果必须原样采纳，不得因为「参考图本身
# 也有噪声」而放宽。对齐 starun 侧 adaptive_stretch 的同名集合。
REFERENCE_AUTHENTICITY_GATE_CODES = frozenset({
    "BACKGROUND_NOISE_BOUNDED",
    "BACKGROUND_RELATIVE_NOISE_BOUNDED",
    "HUE_PRESERVED",
    "SUBJECT_CHROMA_VISIBILITY_PRESERVED",
    "BACKGROUND_CHROMA_NOISE_BOUNDED",
})

# 相对噪声的硬上限语义：即使阈值表被改宽，算出的 gain 也不得超过此值。
_ABSOLUTE_NOISE_GAIN_CAP = HARD_CEILING_RELATIVE_NOISE


def _gate(code, status, message, value=None, threshold=None,
          evidence=None, escalate=False):
    """构造门禁记录。

    键集与 ``artifact_gates._gate`` 逐键一致（这是
    :mod:`quality_state` 与 pipeline warnings 路径能零改动消费的前提）。
    这里刻意**重新实现**而非 import 那个私有名，以免把两个模块焊死
    在同一个内部契约上；但**授权封印**必须一致 —— 统一委托
    :func:`gate_authority.seal_gate`，见该模块文档。
    """
    from gate_authority import seal_gate
    return seal_gate({
        "code": code,
        "status": status,
        "escalate": bool(escalate),
        "message": message,
        "value": value,
        "threshold": threshold,
        "evidence": evidence or {},
    })


def _rgb(image):
    """转成 float32 RGB，裁剪到 [0, 1]，容忍 2D 灰度与 4 通道。"""
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.ndim != 3 or arr.shape[2] < 3:
        raise ValueError("expected gray or RGB image")
    arr = arr[..., :3]
    if arr.size and (float(np.min(arr)) < 0.0 or float(np.max(arr)) > 1.0):
        arr = np.clip(arr, 0.0, 1.0)
    return arr


def _luma(rgb):
    """Rec.709 亮度。与 artifact_gates._luminance 逐位一致。"""
    return (0.2126 * rgb[..., 0]
            + 0.7152 * rgb[..., 1]
            + 0.0722 * rgb[..., 2])


def _downsample(image, max_side=GATE_PREVIEW_MAX_SIDE):
    """与 pipeline.downsample_for_gates 同口径的降采样（含 anti_aliasing）。"""
    arr = _rgb(image)
    height, width = arr.shape[:2]
    if max(height, width) <= max_side:
        return np.array(arr, dtype=np.float32, copy=True)
    from skimage.transform import resize

    scale = max_side / max(height, width)
    resized = resize(
        arr,
        (max(1, int(height * scale)), max(1, int(width * scale)), 3),
        preserve_range=True,
        anti_aliasing=True,
    )
    return np.asarray(resized, dtype=np.float32)


def _mad_sigma(values):
    """MAD 正态一致性估计：sigma = 1.4826 * median(|x - median|)。

    A 侧的既有惯例（gradient_removal / star_tools 都用这个口径）。
    """
    if values.size == 0:
        return 0.0
    median = float(np.median(values))
    return float(MAD_TO_SIGMA * np.median(np.abs(values - median)))


def _background_mask(rgb):
    """背景样本掩膜。

    口径与 ``quality_metrics.calculate_metrics`` 的 ``background_color_cast``
    **完全一致**（alive 过滤 + 三通道均值 p25），让新门与既有
    BACKGROUND_COLOR_CAST 说同一批像素。判据是图像自身的分位，不依赖外部
    参数，因此对任意主体类型都成立。

    与 starun 侧的三处实质差异：
      1. 分位 25 而非 45 —— A 的口径更紧，sigma 样本更纯但对天体边缘更敏感；
         保持一致优先于「更宽松」。
      2. 多了 alive 过滤（三通道全活）。背景被压死时返回**空掩膜**，这正是
         「未触发被等同健康」的高发场景 —— 空掩膜必须报 unavailable，绝不能
         当作 0.0 健康值。
      3. 不构造弱信号掩膜：starun 侧的 weak_signal_measurement_mask 在 A 侧
         无对应物，不用近似替代。
    """
    rgb = _rgb(rgb)
    gray = rgb.mean(axis=2)
    alive = np.min(rgb, axis=2) > 0
    if int(np.count_nonzero(alive)) >= 100:
        pool = gray[alive]
        mask = alive & (gray <= float(np.percentile(pool, 25.0)))
    else:
        mask = np.zeros(gray.shape, dtype=bool)
    return mask


def _signal_mask(rgb, background_mask, luma=None, background_median=None,
                 background_sigma=None):
    """主体样本掩膜（A 侧新增，starun 侧无直接对应物）。

    ``subject_mask`` 是 [0, 1] 连续掩膜时按阈值二值化；缺失时从图像自身亮度
    推导。两条路径都无条件排除背景与近饱和星核。
    """
    rgb = _rgb(rgb)
    if luma is None:
        luma = _luma(rgb)
    if background_median is None or background_sigma is None:
        values = luma[background_mask]
        if values.size == 0:
            return np.zeros(luma.shape, dtype=bool)
        background_median = float(np.median(values))
        background_sigma = _mad_sigma(values)
    signal = luma > background_median + 3.0 * max(background_sigma, 1e-9)
    signal &= ~background_mask
    signal &= rgb.max(axis=2) < 0.98
    return signal


def _resolve_signal_mask(candidate_rgb, subject_mask, background_mask):
    """优先使用外部主体掩膜（二值化），否则从candidate 自身亮度推导。"""
    if subject_mask is not None:
        mask = np.asarray(subject_mask, dtype=np.float32)
        if mask.ndim == 2:
            # 0.12 阈值对齐 starun 侧 adaptive_stretch 的 subject_support 口径。
            # A 的 subject_mask_array 是连续值域掩膜，语义不同，故必须二值化。
            signal = mask >= 0.12
        else:
            signal = mask[..., 0] >= 0.12
        signal = np.asarray(signal, dtype=bool)
        if signal.shape != _rgb(candidate_rgb).shape[:2]:
            signal = np.zeros(_rgb(candidate_rgb).shape[:2], dtype=bool)
        else:
            signal &= ~background_mask
            signal &= _rgb(candidate_rgb).max(axis=2) < 0.98
        return signal
    return _signal_mask(candidate_rgb, background_mask)


def _chroma_map(rgb):
    """逐像素色度幅值：sqrt(sum((chromaticity - 1/3)^2))。

    注意：这是给色度门算 sigma 用的。HUE_PRESERVED 用的是 **L∞ 最大绝对差**，
    两者不要混用。
    """
    rgb = _rgb(rgb)
    total = np.maximum(np.sum(rgb, axis=2, keepdims=True), 1e-7)
    chromaticity = rgb / total
    return np.sqrt(np.sum((chromaticity - (1.0 / 3.0)) ** 2, axis=2))


def _region_summary(chroma, background, signal):
    """返回 (background_sigma, signal_level, snr)。"""
    background_values = chroma[background]
    background_median = float(np.median(background_values))
    background_sigma = _mad_sigma(background_values)
    signal_level = max(0.0, float(np.median(chroma[signal])) - background_median)
    snr = signal_level / max(background_sigma, 1e-9)
    return background_sigma, signal_level, snr


def _chroma_visibility_metrics(candidate_rgb, reference_rgb, *,
                               background_mask, signal_mask):
    """色度 SNR 保留比与背景色度噪声增益。

    **不可测量时所有值为 None**（而非 fallback 成 1.0）—— 这是本模块最重要
    的守卫纪律：把「没测到」当成「没问题」正是门禁静默通过区的来源。
    """
    background = np.asarray(background_mask, dtype=bool)
    signal = np.asarray(signal_mask, dtype=bool)
    if (int(np.count_nonzero(background)) < MIN_SIGNAL_PIXELS
            or int(np.count_nonzero(signal)) < MIN_SIGNAL_PIXELS):
        return {
            "chroma_measurement_available": False,
            "chroma_measurement_status": "insufficient_regions",
            "source_subject_chroma_snr": None,
            "subject_chroma_snr": None,
            "subject_chroma_snr_retention": None,
            "source_background_chroma_sigma": None,
            "background_chroma_sigma": None,
            "background_chroma_noise_gain": None,
            "physical_snr": False,
        }

    candidate_chroma = _chroma_map(candidate_rgb)
    reference_chroma = _chroma_map(reference_rgb)
    source_sigma, _source_signal, source_snr = _region_summary(
        reference_chroma, background, signal)
    candidate_sigma, _candidate_signal, candidate_snr = _region_summary(
        candidate_chroma, background, signal)

    available = bool(source_sigma > CHROMA_MIN_SOURCE_SIGMA
                     and source_snr > CHROMA_MIN_SOURCE_SNR)
    return {
        "chroma_measurement_available": available,
        "chroma_measurement_status": (
            "available" if available else "insufficient_source_chroma_support"),
        "source_subject_chroma_snr": source_snr if available else None,
        "subject_chroma_snr": candidate_snr if available else None,
        "subject_chroma_snr_retention": (
            candidate_snr / max(source_snr, 1e-9) if available else None),
        "source_background_chroma_sigma": source_sigma,
        "background_chroma_sigma": candidate_sigma,
        "background_chroma_noise_gain": (
            candidate_sigma / max(source_sigma, 1e-9) if available else None),
        "physical_snr": False,
    }


def _hue_ratio_drift_p95(candidate_rgb, reference_rgb, *,
                          background_mask):
    """逐像素色度漂移的p95。

    算法（对齐 starun 侧 adaptive_stretch 的同名度量）：
      1. 选区用**参考图**的亮度与背景剖面阈值（不是 candidate 的）
      2. 逐像素归一化成 chromaticity（去亮度，只留色度）
      3. 漂移 = 三通道 chromaticity 差的最大绝对值（L∞，不是 L2）
      4. 取被选像素的 95 分位

    用参考图选区是关键：若改用 candidate 自己的阈值，选区会随曲线漂移，
    色度门就会把自己的选区变化算成色度变化。
    """
    candidate_rgb = _rgb(candidate_rgb)
    reference_rgb = _rgb(reference_rgb)
    reference_luma = _luma(reference_rgb)
    values = reference_luma[np.asarray(background_mask, dtype=bool)]
    if values.size == 0:
        return 0.0
    background_median = float(np.median(values))
    background_sigma = _mad_sigma(values)

    chroma_mask = (
        (reference_luma > background_median + 3.0 * max(background_sigma, 1e-6))
        & (reference_rgb.max(axis=2) < 0.98)
    )
    if not np.any(chroma_mask):
        return 0.0

    count = int(np.count_nonzero(chroma_mask))
    drift = np.empty(count, dtype=np.float32)
    offset = 0
    # 分块处理，避免 8MP 图一次性分配 count×3 的中间数组。
    for row_start in range(0, reference_rgb.shape[0], 256):
        row_end = min(reference_rgb.shape[0], row_start + 256)
        row_mask = chroma_mask[row_start:row_end]
        row_count = int(np.count_nonzero(row_mask))
        if not row_count:
            continue
        source_pixels = reference_rgb[row_start:row_end][row_mask]
        output_pixels = candidate_rgb[row_start:row_end][row_mask]
        source_ratio = source_pixels / np.maximum(
            np.sum(source_pixels, axis=1, keepdims=True), 1e-9)
        output_ratio = output_pixels / np.maximum(
            np.sum(output_pixels, axis=1, keepdims=True), 1e-9)
        drift[offset:offset + row_count] = np.max(
            np.abs(output_ratio - source_ratio), axis=1)
        offset += row_count
    return float(np.percentile(drift, 95.0))


def _classify(code, value, *, higher_is_worse):
    """按 THRESHOLDS 把度量值映射为 (status, escalate)。

    不可测量（value is None）→ unavailable，仅记录。
    阈值尚未标定 → uncalibrated，仅记录。这是与 unavailable **不同**的状态：
    unavailable 是「这份图测不了」，uncalibrated 是「阈值还没定」，两者都不等于
    健康，但排查方向完全不同。
    """
    thresholds = THRESHOLDS.get(code) or {}
    degraded = thresholds.get("degraded")
    hard = thresholds.get("hard")
    if value is None or not np.isfinite(value):
        return "unavailable", False
    if degraded is None or hard is None:
        return "uncalibrated", False
    if higher_is_worse:
        healthy, degraded_hit, hard_hit = value < degraded, value >= degraded, value >= hard
    else:
        healthy, degraded_hit, hard_hit = value > degraded, value <= degraded, value <= hard
    if healthy:
        # 「只在触发时append」：健康时完全静默，避免在 gates/warnings 里造噪音。
        return None, False
    if not hard_hit:
        return "degraded", False
    escalate = (code in ESCALATING_CODES) and (not PROVISIONAL)
    return ("failed" if escalate else "warning"), escalate


def _threshold_text(code, degraded, hard):
    if degraded is None or hard is None:
        return "未标定（见 references/fidelity_gate_calibration.md）"
    if higher_is_worse_code(code):
        return f"degraded>={degraded}, failed>={hard}"
    return f"degraded<={degraded}, failed<={hard}"


def _status_note(status):
    """状态 → 追加到 message 的解释。uncalibrated 与 unavailable 都不是健康。"""
    if status == "uncalibrated":
        return "；阈值尚未标定，本条仅记录不判定"
    if status == "degraded":
        return "；处于退化区，已记录但不升级"
    if status == "warning":
        return "；超过硬阈值，需回退曲线或人工复核"
    if status == "failed":
        return "；超过硬阈值且已升级为强制复核"
    return ""


def higher_is_worse_code(code):
    return code == "SUBJECT_CHROMA_VISIBILITY_PRESERVED"


def _remediation(code, value, degraded, hard):
    """degraded 态必须带可执行处置，否则只是又一个装饰性状态。"""
    table = {
        "BACKGROUND_NOISE_BOUNDED": ("stretch", "stretch_method", "pi_mtf"),
        "BACKGROUND_RELATIVE_NOISE_BOUNDED": ("stretch", "stretch_method", "pi_mtf"),
        "HUE_PRESERVED": ("final_color", "saturation", "降低饱和度并复核 color_mode"),
        "SUBJECT_CHROMA_VISIBILITY_PRESERVED": (
            "style", "style_strength", "降低 style_strength"),
        "BACKGROUND_CHROMA_NOISE_BOUNDED": (
            "final_denoise", "final_denoise_chroma", "提高色度降噪强度"),
    }
    stage, knob, suggested = table.get(code, (None, None, None))
    return {
        "stage": stage,
        "knob": knob,
        "suggested": suggested,
        "rationale": f"{code}={value} 落在 degraded 与 hard 阈值之间",
        "thresholds": {"degraded": degraded, "hard": hard},
    }


def check_background_noise(candidate_rgb, *, background_mask=None):
    """背景亮度噪声绝对量（MAD sigma）。"""
    candidate_rgb = _rgb(candidate_rgb)
    mask = _background_mask(candidate_rgb) if background_mask is None \
        else np.asarray(background_mask, dtype=bool)
    if int(np.count_nonzero(mask)) < MIN_BACKGROUND_PIXELS:
        return _gate(
            "BACKGROUND_NOISE_BOUNDED", "unavailable",
            "背景样本不足，无法测量背景噪声；不可测量不等于健康",
            value=None, threshold="需要>=%d 个背景像素" % MIN_BACKGROUND_PIXELS,
            evidence={"background_px": int(np.count_nonzero(mask))},
        )
    sigma = _mad_sigma(_luma(candidate_rgb)[mask])
    degraded = (THRESHOLDS["BACKGROUND_NOISE_BOUNDED"]["degraded"])
    hard = THRESHOLDS["BACKGROUND_NOISE_BOUNDED"]["hard"]
    status, escalate = _classify(
        "BACKGROUND_NOISE_BOUNDED", sigma, higher_is_worse=True)
    if status is None:
        return None
    gate = _gate(
        "BACKGROUND_NOISE_BOUNDED", status,
        f"背景亮度噪声 sigma={sigma:.6f}" + _status_note(status),
        value=round(sigma, 6), threshold=_threshold_text(
            "BACKGROUND_NOISE_BOUNDED", degraded, hard),
        evidence={"background_px": int(np.count_nonzero(mask)),
                  "physical_snr": False},
        escalate=escalate,
    )
    if status == "degraded":
        gate["evidence"]["remediation"] = _remediation(
            "BACKGROUND_NOISE_BOUNDED", round(sigma, 6), degraded, hard)
    return gate


def _is_identity(candidate_rgb, reference_rgb):
    """candidate 与 reference 是否逐元素相同。

    逐元素相同时所有配对度量在数学上恒为 0漂移 / 1.0 增益，无需测量即可
    判定健康。这既避免纯色图（无色度主体）产生无意义的 unavailable 噪音，
    也是「只在触发时append」纪律的极端形态。
    """
    return bool(np.array_equal(candidate_rgb, reference_rgb))


# 允许配对前对齐的最大像素差。1px 足以吸收降采样的浮点取整误差；超过它说明
# 两侧根本不是同一份数据（如误传了不同尺寸的图），对齐会掩盖真问题。
MAX_GEOMETRY_ALIGN_PX = 1


def _align_geometry(candidate_rgb, reference_rgb):
    """把两侧对齐到公共尺寸，返回 (candidate, reference, note) 或 None。

    ``downsample_for_gates`` 与 ``_downsample`` 各自独立计算 scale，
    ``int()`` 截断在边界上可能差 1px（C50 验收实测 1400×787 vs 1400×788）。
    差 1px 时把两者都缩到公共尺寸即可恢复配对能力；差超过
    ``MAX_GEOMETRY_ALIGN_PX`` 则返回 None，交给调用方如实记录不可配对。
    """
    if candidate_rgb.shape == reference_rgb.shape:
        return candidate_rgb, reference_rgb, None
    height_delta = abs(candidate_rgb.shape[0] - reference_rgb.shape[0])
    width_delta = abs(candidate_rgb.shape[1] - reference_rgb.shape[1])
    if max(height_delta, width_delta) > MAX_GEOMETRY_ALIGN_PX:
        return None
    target = (min(candidate_rgb.shape[0], reference_rgb.shape[0]),
              min(candidate_rgb.shape[1], reference_rgb.shape[1]))

    def resize_to(image):
        from skimage.transform import resize
        resized = resize(image, (*target, 3), preserve_range=True,
                         anti_aliasing=True)
        return np.asarray(resized, dtype=np.float32)

    note = ('resized both sides to %dx%d to recover a 1px geometry delta'
            % target)
    return resize_to(candidate_rgb), resize_to(reference_rgb), note


def check_relative_background_noise(candidate_rgb, reference_rgb, *,
                                   reference_comparable=True):
    """相对背景噪声增益：σ/中位 的比值之比。

    绝对 sigma 依赖图像整体亮度（同一份数据拉伸前后不可直接比），相对增益
    才是「这条曲线比保守基线 amplify 了多少噪声」的可比量。硬上限 1.60 不可
    放松：即使阈值表被改宽，算出的 gain 也会被夹住。
    """
    if not reference_comparable:
        return _gate(
            "BACKGROUND_RELATIVE_NOISE_BOUNDED", "skipped",
            "参考图与候选不同域，相对噪声增益不是有意义的物理量",
            escalate=False, evidence={"reference_comparable": False})
    candidate_rgb = _rgb(candidate_rgb)
    reference_rgb = _rgb(reference_rgb)
    if candidate_rgb.shape != reference_rgb.shape:
        return _gate(
            "BACKGROUND_RELATIVE_NOISE_BOUNDED", "skipped",
            "参考图与候选尺寸不同，配对比较不可靠",
            escalate=False,
            evidence={"candidate_shape": list(candidate_rgb.shape),
                      "reference_shape": list(reference_rgb.shape)})
    if _is_identity(candidate_rgb, reference_rgb):
        return None

    mask = _background_mask(candidate_rgb)
    if int(np.count_nonzero(mask)) < MIN_BACKGROUND_PIXELS:
        return _gate(
            "BACKGROUND_RELATIVE_NOISE_BOUNDED", "unavailable",
            "背景样本不足，无法测量相对噪声增益",
            value=None, threshold="需要 >=%d 个背景像素" % MIN_BACKGROUND_PIXELS,
            evidence={"background_px": int(np.count_nonzero(mask))})

    candidate_values = _luma(candidate_rgb)[mask]
    reference_values = _luma(reference_rgb)[mask]
    candidate_relative = _mad_sigma(candidate_values) / max(
        float(np.median(candidate_values)), 1e-9)
    reference_relative = _mad_sigma(reference_values) / max(
        float(np.median(reference_values)), 1e-9)
    gain = candidate_relative / max(reference_relative, 1e-9)
    if gain > _ABSOLUTE_NOISE_GAIN_CAP:
        gain = _ABSOLUTE_NOISE_GAIN_CAP

    degraded = THRESHOLDS["BACKGROUND_RELATIVE_NOISE_BOUNDED"]["degraded"]
    hard = THRESHOLDS["BACKGROUND_RELATIVE_NOISE_BOUNDED"]["hard"]
    status, escalate = _classify(
        "BACKGROUND_RELATIVE_NOISE_BOUNDED", gain, higher_is_worse=True)
    if status is None:
        return None
    gate = _gate(
        "BACKGROUND_RELATIVE_NOISE_BOUNDED", status,
        f"相对背景噪声增益={gain:.4f}（候选 {candidate_relative:.6f} vs 参考 "
        f"{reference_relative:.6f}）" + _status_note(status),
        value=round(gain, 4), threshold=_threshold_text(
            "BACKGROUND_RELATIVE_NOISE_BOUNDED", degraded, hard),
        evidence={"candidate_relative_noise": candidate_relative,
                  "reference_relative_noise": reference_relative,
                  "hard_ceiling": HARD_CEILING_RELATIVE_NOISE,
                  "physical_snr": False},
        escalate=escalate,
    )
    if status == "degraded":
        gate["evidence"]["remediation"] = _remediation(
            "BACKGROUND_RELATIVE_NOISE_BOUNDED", round(gain, 4), degraded, hard)
    return gate


def check_hue_preserved(candidate_rgb, reference_rgb, *,
                        background_mask=None, reference_comparable=True):
    """色度保真：逐像素 chromaticity 漂移 p95（显示域、比较性）。"""
    if not reference_comparable:
        return _gate("HUE_PRESERVED", "skipped",
                     "参考图与候选不同域，色度漂移不可归因",
                     escalate=False, evidence={"reference_comparable": False})
    candidate_rgb = _rgb(candidate_rgb)
    reference_rgb = _rgb(reference_rgb)
    if candidate_rgb.shape != reference_rgb.shape:
        return _gate("HUE_PRESERVED", "skipped",
                     "参考图与候选尺寸不同，配对比较不可靠", escalate=False,
                     evidence={"candidate_shape": list(candidate_rgb.shape),
                               "reference_shape": list(reference_rgb.shape)})
    if _is_identity(candidate_rgb, reference_rgb):
        return None
    mask = _background_mask(candidate_rgb) if background_mask is None \
        else np.asarray(background_mask, dtype=bool)
    drift = _hue_ratio_drift_p95(
        candidate_rgb, reference_rgb, background_mask=mask)
    degraded = THRESHOLDS["HUE_PRESERVED"]["degraded"]
    hard = THRESHOLDS["HUE_PRESERVED"]["hard"]
    status, escalate = _classify("HUE_PRESERVED", drift, higher_is_worse=True)
    if status is None:
        return None
    gate = _gate(
        "HUE_PRESERVED", status,
        f"色度漂移 p95={drift:.6f}（逐像素三通道 chromaticity 最大绝对差，"
        f"显示域比较性度量，非物理量）" + _status_note(status),
        value=round(drift, 6),
        threshold=_threshold_text("HUE_PRESERVED", degraded, hard),
        evidence={"physical_snr": False, "metric": "L_inf_chromaticity_drift_p95"},
        escalate=escalate,
    )
    if status == "degraded":
        gate["evidence"]["remediation"] = _remediation(
            "HUE_PRESERVED", round(drift, 6), degraded, hard)
    return gate


def check_subject_chroma_visibility(candidate_rgb, reference_rgb, *,
                                    background_mask=None, signal_mask=None,
                                    subject_mask=None,
                                    reference_comparable=True):
    """主体色度可见性保留比（越大越好）。"""
    if not reference_comparable:
        return _gate("SUBJECT_CHROMA_VISIBILITY_PRESERVED", "skipped",
                     "参考图与候选不同域，色度保真不可归因", escalate=False,
                     evidence={"reference_comparable": False})
    candidate_rgb = _rgb(candidate_rgb)
    reference_rgb = _rgb(reference_rgb)
    if candidate_rgb.shape != reference_rgb.shape:
        return _gate("SUBJECT_CHROMA_VISIBILITY_PRESERVED", "skipped",
                     "参考图与候选尺寸不同，配对比较不可靠", escalate=False,
                     evidence={"candidate_shape": list(candidate_rgb.shape),
                               "reference_shape": list(reference_rgb.shape)})
    if _is_identity(candidate_rgb, reference_rgb):
        return None
    if background_mask is None:
        background_mask = _background_mask(candidate_rgb)
    if signal_mask is None:
        signal_mask = _resolve_signal_mask(candidate_rgb, subject_mask, background_mask)
    metrics = _chroma_visibility_metrics(
        candidate_rgb, reference_rgb,
        background_mask=background_mask, signal_mask=signal_mask)
    retention = metrics["subject_chroma_snr_retention"]
    degraded = THRESHOLDS["SUBJECT_CHROMA_VISIBILITY_PRESERVED"]["degraded"]
    hard = THRESHOLDS["SUBJECT_CHROMA_VISIBILITY_PRESERVED"]["hard"]
    if retention is None:
        return _gate(
            "SUBJECT_CHROMA_VISIBILITY_PRESERVED", "unavailable",
            "参考图色度支撑不足（%s），无法测量主体色度保真；"
            "不可测量不等于健康" % metrics["chroma_measurement_status"],
            value=None,
            threshold=_threshold_text(
                "SUBJECT_CHROMA_VISIBILITY_PRESERVED", degraded, hard),
            evidence=metrics)
    status, escalate = _classify(
        "SUBJECT_CHROMA_VISIBILITY_PRESERVED", retention, higher_is_worse=False)
    if status is None:
        return None
    gate = _gate(
        "SUBJECT_CHROMA_VISIBILITY_PRESERVED", status,
        f"主体色度 SNR 保留比={retention:.4f}（显示域相对比较，"
        f"physical_snr=False）" + _status_note(status),
        value=round(retention, 4),
        threshold=_threshold_text(
            "SUBJECT_CHROMA_VISIBILITY_PRESERVED", degraded, hard),
        evidence=metrics, escalate=escalate,
    )
    if status == "degraded":
        gate["evidence"]["remediation"] = _remediation(
            "SUBJECT_CHROMA_VISIBILITY_PRESERVED", round(retention, 4),
            degraded, hard)
    return gate


def check_background_chroma_noise(candidate_rgb, reference_rgb, *,
                                  background_mask=None,
                                  reference_comparable=True):
    """背景色度噪声增益（越大越差）。"""
    if not reference_comparable:
        return _gate("BACKGROUND_CHROMA_NOISE_BOUNDED", "skipped",
                     "参考图与候选不同域，色度噪声不可归因", escalate=False,
                     evidence={"reference_comparable": False})
    candidate_rgb = _rgb(candidate_rgb)
    reference_rgb = _rgb(reference_rgb)
    if candidate_rgb.shape != reference_rgb.shape:
        return _gate("BACKGROUND_CHROMA_NOISE_BOUNDED", "skipped",
                     "参考图与候选尺寸不同，配对比较不可靠", escalate=False,
                     evidence={"candidate_shape": list(candidate_rgb.shape),
                               "reference_shape": list(reference_rgb.shape)})
    if _is_identity(candidate_rgb, reference_rgb):
        return None
    if background_mask is None:
        background_mask = _background_mask(candidate_rgb)
    signal_mask = _signal_mask(candidate_rgb, background_mask)
    metrics = _chroma_visibility_metrics(
        candidate_rgb, reference_rgb,
        background_mask=background_mask, signal_mask=signal_mask)
    gain = metrics["background_chroma_noise_gain"]
    degraded = THRESHOLDS["BACKGROUND_CHROMA_NOISE_BOUNDED"]["degraded"]
    hard = THRESHOLDS["BACKGROUND_CHROMA_NOISE_BOUNDED"]["hard"]
    if gain is None:
        return _gate(
            "BACKGROUND_CHROMA_NOISE_BOUNDED", "unavailable",
            "参考图色度支撑不足（%s），无法测量背景色度噪声；"
            "不可测量不等于健康" % metrics["chroma_measurement_status"],
            value=None,
            threshold=_threshold_text(
                "BACKGROUND_CHROMA_NOISE_BOUNDED", degraded, hard),
            evidence=metrics)
    status, escalate = _classify(
        "BACKGROUND_CHROMA_NOISE_BOUNDED", gain, higher_is_worse=True)
    if status is None:
        return None
    gate = _gate(
        "BACKGROUND_CHROMA_NOISE_BOUNDED", status,
        f"背景色度噪声增益={gain:.4f}（显示域相对比较，"
        f"physical_snr=False）" + _status_note(status),
        value=round(gain, 4),
        threshold=_threshold_text(
            "BACKGROUND_CHROMA_NOISE_BOUNDED", degraded, hard),
        evidence=metrics, escalate=escalate,
    )
    if status == "degraded":
        gate["evidence"]["remediation"] = _remediation(
            "BACKGROUND_CHROMA_NOISE_BOUNDED", round(gain, 4), degraded, hard)
    return gate


def evaluate_fidelity_gates(candidate, reference=None, *, steps=None,
                             target_type=None, reference_comparable=True,
                             display_reference=False, background_mask=None,
                             signal_mask=None, subject_mask=None,
                             geometry="display"):
    """运行全部保真度门禁并聚合。

    返回结构严格镜像 ``artifact_gates.evaluate_star_artifact_gates``，
    这样 pipeline 与 quality_state 的消费代码可以逐字对齐。

    status 判定用**双条件**（escalate AND status in warning/failed）而非
    agent_protocol 的单 escalate 判定 —— 新体系有第三态 degraded，单条件会
    把它误判成 review_required。
    """
    steps = set(steps or [])
    gates = []

    candidate_rgb = candidate if geometry == "native" else _downsample(candidate)
    alignment = None
    if reference is not None:
        reference_rgb = (reference if geometry == "native"
                         else _downsample(reference))
        # 两侧各自独立降采样时，浮点取整可能让同一份数据差 1px（C50 实测
        # 1400×787 vs 1400×788）。先用公共目标尺寸对齐，避免配对门被跳过。
        aligned = _align_geometry(candidate_rgb, reference_rgb)
        if aligned is not None:
            candidate_rgb, reference_rgb, alignment = aligned
        if candidate_rgb.shape != reference_rgb.shape:
            # 无法对齐。不抛异常 —— 门禁自身异常不该让整条管线崩。
            #
            # 但**必须留下记录**：返回空 gates 会让「全部配对门都没跑」看起来
            # 和「跑了但全部健康」完全一样，这是典型的静默失效。C50 验收实测
            # 到：1px 之差让 4 道配对门全部消失，而 summary 仍写「不可测量」，
            # 看起来像数据问题、实际是几何问题。
            skip_gate = _gate(
                "FIDELITY_GEOMETRY_MISMATCH", "skipped",
                "参考图与候选几何无法对齐，配对门禁全部跳过；这是几何问题"
                "不是数据问题，不得据此认为配对门已通过",
                value={'candidate_shape': list(candidate_rgb.shape),
                       'reference_shape': list(reference_rgb.shape)},
                threshold="两者形状必须一致",
                evidence={"candidate_shape": list(candidate_rgb.shape),
                          "reference_shape": list(reference_rgb.shape)},
                escalate=False)
            return {
                "schema": SCHEMA,
                "status": "success",
                "summary": "参考图与候选尺寸不一致，配对保真度门禁已跳过（几何问题）",
                "gates": [skip_gate],
                "context": {
                    "steps": sorted(steps),
                    "target_type": target_type,
                    "reference_provided": True,
                    "reference_comparable": False,
                    "calibration_provisional": PROVISIONAL,
                    "calibration_corpus_size": CALIBRATION_CORPUS_SIZE,
                    "measurement_domain": "display",
                    "geometry_alignment": "failed",
                    "candidate_shape": list(candidate_rgb.shape),
                    "reference_shape": list(reference_rgb.shape),
                },
                "coordinate_convention": "row_col",
                "data_domain": "named_fidelity_diagnostic",
            }
    else:
        reference_rgb = None

    if alignment is not None:
        print(f"[fidelity_gates] 几何对齐：{alignment}")

    if background_mask is None:
        background_mask = _background_mask(candidate_rgb)
    if signal_mask is None and reference_rgb is not None:
        signal_mask = _resolve_signal_mask(candidate_rgb, subject_mask, background_mask)

    gates.append(check_background_noise(candidate_rgb, background_mask=background_mask))

    if reference_rgb is not None:
        gates.extend([
            check_relative_background_noise(
                candidate_rgb, reference_rgb, reference_comparable=reference_comparable),
            check_hue_preserved(
                candidate_rgb, reference_rgb, background_mask=background_mask,
                reference_comparable=reference_comparable),
            check_subject_chroma_visibility(
                candidate_rgb, reference_rgb, background_mask=background_mask,
                signal_mask=signal_mask, reference_comparable=reference_comparable),
            check_background_chroma_noise(
                candidate_rgb, reference_rgb, background_mask=background_mask,
                reference_comparable=reference_comparable),
        ])

    gates = [gate for gate in gates if gate is not None]
    escalating = [
        gate for gate in gates
        if gate.get("escalate", False) and gate["status"] in ("warning", "failed")
    ]
    triggered = [gate["code"] for gate in escalating]
    degraded = [gate["code"] for gate in gates if gate["status"] == "degraded"]
    unavailable = [gate["code"] for gate in gates if gate["status"] == "unavailable"]
    uncalibrated = [gate["code"] for gate in gates if gate["status"] == "uncalibrated"]
    skipped = [gate["code"] for gate in gates if gate["status"] == "skipped"]

    parts = []
    if triggered:
        parts.append(f"{len(triggered)} 项保真度门禁触发：{', '.join(triggered)}")
    if degraded:
        parts.append(f"degraded（可见但不升级）：{', '.join(degraded)}")
    if uncalibrated:
        parts.append(f"未标定（仅记录）：{', '.join(uncalibrated)}")
    if unavailable:
        parts.append(f"不可测量（不等于健康）：{', '.join(unavailable)}")
    if skipped:
        parts.append(f"已跳过：{', '.join(skipped)}")

    return {
        "schema": SCHEMA,
        "status": "review_required" if escalating else "success",
        "summary": "；".join(parts) if parts else (
            "No escalating fidelity findings; degraded and unavailable "
            "diagnostics are retained"),
        "gates": gates,
        "context": {
            "steps": sorted(steps),
            "target_type": target_type,
            "reference_provided": reference is not None,
            "reference_comparable": (
                reference is not None and reference_comparable
                and candidate_rgb.shape == reference_rgb.shape),
            "calibration_provisional": PROVISIONAL,
            "calibration_corpus_size": CALIBRATION_CORPUS_SIZE,
            "measurement_domain": "display",
            "geometry_alignment": alignment or "not_needed",
            "reference_authenticity_codes": sorted(REFERENCE_AUTHENTICITY_GATE_CODES),
            "display_reference": bool(display_reference),
        },
        "coordinate_convention": "row_col",
        "data_domain": "named_fidelity_diagnostic",
    }


def main():
    parser = argparse.ArgumentParser(
        description="保真度门禁：噪声增益 / 色度保真 / 参考可信度")
    parser.add_argument("candidate", help="候选图（处理后）")
    parser.add_argument("reference", nargs="?", default=None,
                        help="参考图（同一数据的保守渲染，用于配对比较）")
    parser.add_argument("--steps", default="stretch",
                        help="逗号分隔的步骤名，仅用于上下文记录")
    parser.add_argument("--target-type", default=None)
    parser.add_argument("--geometry", choices=("display", "native"),
                        default="display",
                        help="display=降采样到 GATE_PREVIEW_MAX_SIDE（标定口径）")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fits_io import read_image

    candidate, _ = read_image(args.candidate)
    reference = None
    if args.reference:
        reference, _ = read_image(args.reference)

    report = evaluate_fidelity_gates(
        candidate, reference,
        steps=[item for item in args.steps.split(",") if item],
        target_type=args.target_type,
        display_reference=reference is not None,
        geometry=args.geometry,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload)


if __name__ == "__main__":
    main()
