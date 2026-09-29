#!/usr/bin/env python3
"""Non-generative professional style grading for deep-sky images."""

import argparse
import sys

import numpy as np
from scipy.ndimage import gaussian_filter, zoom
from skimage import img_as_float32, img_as_ubyte
from color_conv import safe_hsv2rgb as hsv2rgb, safe_rgb2hsv as rgb2hsv, safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb
from skimage.io import imread, imsave


STYLE_PROFILES = {
    "natural": {
        "description": "保守自然，低处理痕迹",
        "black_floor": 0.015,
        "gamma": 1.03,
        "contrast": 0.08,
        "highlight_rolloff": 0.18,
        "saturation": 1.08,
        "background_desat": 0.35,
        "micro_contrast": 0.08,
        "color_separation": 0.05,
        "warmth": 0.0,
    },
    "deep_clean": {
        "description": "深黑背景、干净现代",
        "black_floor": 0.035,
        "gamma": 1.10,
        "contrast": 0.14,
        "highlight_rolloff": 0.25,
        "saturation": 1.18,
        "background_desat": 0.65,
        "micro_contrast": 0.12,
        "color_separation": 0.08,
        "warmth": -0.02,
    },
    "dramatic_nebula": {
        "description": "发射星云主体突出、色彩有冲击但受控",
        "black_floor": 0.025,
        "gamma": 0.95,
        "contrast": 0.18,
        "highlight_rolloff": 0.35,
        "saturation": 1.32,
        "background_desat": 0.55,
        "micro_contrast": 0.18,
        "color_separation": 0.12,
        "warmth": 0.02,
    },
    "soft_dust": {
        "description": "反射星云和暗尘埃的柔和胶片感",
        "black_floor": 0.012,
        "gamma": 0.92,
        "contrast": 0.06,
        "highlight_rolloff": 0.30,
        "saturation": 1.12,
        "background_desat": 0.25,
        "micro_contrast": 0.06,
        "color_separation": 0.06,
        "warmth": -0.04,
    },
    "galaxy_core": {
        "description": "星系黄核、蓝臂和尘埃带层次",
        "black_floor": 0.008,
        "gamma": 1.00,
        "contrast": 0.16,
        "highlight_rolloff": 0.45,
        "saturation": 1.16,
        "background_desat": 0.45,
        "micro_contrast": 0.16,
        "color_separation": 0.10,
        "warmth": 0.015,
    },
    "widefield_punch": {
        "description": "宽场星野的深背景和星云可见度",
        "black_floor": 0.030,
        "gamma": 1.06,
        "contrast": 0.18,
        "highlight_rolloff": 0.22,
        "saturation": 1.20,
        "background_desat": 0.70,
        "micro_contrast": 0.10,
        "color_separation": 0.08,
        "warmth": -0.01,
    },
    "planetary_detail": {
        "description": "行星状星云小尺度结构清晰、OIII青蓝精致",
        "black_floor": 0.020,
        "gamma": 0.98,
        "contrast": 0.20,
        "highlight_rolloff": 0.50,
        "saturation": 1.22,
        "background_desat": 0.60,
        "micro_contrast": 0.22,
        "color_separation": 0.14,
        "warmth": -0.01,
    },
    "supernova_remnant": {
        "description": "超新星遗迹丝状结构、边缘锐利、中等饱和",
        "black_floor": 0.028,
        "gamma": 1.02,
        "contrast": 0.19,
        "highlight_rolloff": 0.38,
        "saturation": 1.18,
        "background_desat": 0.58,
        "micro_contrast": 0.20,
        "color_separation": 0.11,
        "warmth": 0.01,
    },
    "star_cluster": {
        "description": "星团专用：解析密集恒星、色彩自然",
        "black_floor": 0.010,
        "gamma": 1.05,
        "contrast": 0.10,
        "highlight_rolloff": 0.20,
        "saturation": 1.05,
        "background_desat": 0.30,
        "micro_contrast": 0.10,
        "color_separation": 0.04,
        "warmth": 0.0,
    },
}


def choose_style_profile(
    target_type=None,
    color_mode="standard",
    user_style="auto",
    diagnostic_report=None,
    user_prefs=None,
):
    """
    智能风格选择 — 基于目标类型、诊断数据、用户偏好的多维决策。

    参数:
        target_type: 天体类型字符串
        color_mode: 色彩模式 (standard/emission/narrowband)
        user_style: 用户强制指定的风格 (auto 表示自动选择)
        diagnostic_report: analyze.py 输出的诊断报告 dict，用于数据驱动微调
        user_prefs: 用户偏好 dict，如 {"prefer_natural": True, "max_saturation": 1.2}

    返回:
        tuple(str, dict, list): (profile_name, adapted_params, reasoning_chain)
        - profile_name: 选中的风格名称
        - adapted_params: 经诊断微调后的参数字典 (None 表示使用原始 profile)
        - reasoning_chain: 决策理由列表，供 AI 审查和理解
    """
    reasoning = []

    # ── 1. 用户强制指定 ──
    if user_style and user_style != "auto":
        if user_style not in STYLE_PROFILES:
            raise ValueError(f"unknown style profile: {user_style}")
        reasoning.append(f"用户强制指定风格: {user_style}")
        return user_style, None, reasoning

    # ── 2. 基础目标类型映射 (扩展覆盖) ──
    base_profile = _select_base_profile(target_type, color_mode, reasoning)

    # ── 3. 诊断驱动自适应微调 ──
    adapted = None
    if diagnostic_report is not None:
        adapted, diag_reasons = _adapt_profile_by_diagnostics(
            base_profile, diagnostic_report
        )
        reasoning.extend(diag_reasons)

    # ── 4. 用户偏好叠加 ──
    if user_prefs is not None and adapted is not None:
        adapted, pref_reasons = _apply_user_prefs(adapted, user_prefs)
        reasoning.extend(pref_reasons)

    return base_profile, adapted, reasoning


def _select_base_profile(target_type, color_mode, reasoning):
    """基于目标类型和色彩模式的基础映射 (扩展版)。"""

    # 规范化 target_type
    tt = (target_type or "").lower().replace(" ", "_")

    # 色彩模式优先 (emission / narrowband 强烈暗示发射星云)
    if color_mode in ("emission", "narrowband", "hoo", "sho"):
        reasoning.append(f"color_mode={color_mode} 强烈暗示发射特征 → 选择 dramatic_nebula")
        return "dramatic_nebula"

    # 发射星云族
    if tt in ("emission_nebula", "hii_region", "diffuse_nebula"):
        reasoning.append(f"目标类型={tt} 属于发射星云 → dramatic_nebula")
        return "dramatic_nebula"

    # 行星状星云
    if tt in ("planetary_nebula", "planetary"):
        reasoning.append(f"目标类型={tt} 属于行星状星云 → planetary_detail (小尺度结构优先)")
        return "planetary_detail"

    # 超新星遗迹
    if tt in ("supernova_remnant", "snr"):
        reasoning.append(f"目标类型={tt} 属于超新星遗迹 → supernova_remnant (丝状结构优先)")
        return "supernova_remnant"

    # 反射星云
    if tt in ("reflection_nebula", "dark_nebula", "molecular_cloud",
              "bok_globule", "dark_cloud"):
        reasoning.append(f"目标类型={tt} 属于暗弱尘埃特征 → soft_dust")
        return "soft_dust"

    # 星系族
    if tt in ("galaxy", "spiral_galaxy", "elliptical_galaxy",
              "irregular_galaxy", "barred_galaxy"):
        reasoning.append(f"目标类型={tt} 属于星系 → galaxy_core")
        return "galaxy_core"

    # 星团族
    if tt in ("globular_cluster", "open_cluster", "star_cluster",
              "association", "multiple_star"):
        reasoning.append(f"目标类型={tt} 属于星团 → star_cluster (解析优先)")
        return "star_cluster"

    # 宽场
    if tt in ("wide_field", "milky_way", "star_field", "constellation"):
        reasoning.append(f"目标类型={tt} 属于宽场星野 → widefield_punch")
        return "widefield_punch"

    # 彗星/太阳系小天体
    if tt in ("comet", "asteroid"):
        reasoning.append(f"目标类型={tt} 属于太阳系天体 → natural (保守处理)")
        return "natural"

    # 默认兜底
    reasoning.append(f"目标类型={tt} 未匹配已知类型 → deep_clean (通用现代风格)")
    return "deep_clean"


def _adapt_profile_by_diagnostics(profile_name, diagnostic_report):
    """
    基于 analyze.py 诊断报告对 profile 参数进行数据驱动微调。
    返回: (adapted_params_dict, reasoning_list)
    """
    if profile_name not in STYLE_PROFILES:
        return None, []

    base = dict(STYLE_PROFILES[profile_name])
    adapted = dict(base)
    reasons = []

    # 安全提取诊断值
    brightness = diagnostic_report.get("brightness", {})
    noise = diagnostic_report.get("noise", {})
    color_rpt = diagnostic_report.get("color", {}) or {}
    gradient = diagnostic_report.get("gradient", {})
    sharpness = diagnostic_report.get("sharpness", {})

    darkness_level = brightness.get("darkness_level", "moderate")
    noise_level = noise.get("noise_level", "moderate")
    dr_ratio = brightness.get("dynamic_range_ratio", 10.0)
    color_health = color_rpt.get("color_health_effective",
                                  color_rpt.get("color_health", "good"))
    is_practically_black = brightness.get("is_practically_black", False)

    # ── 暗度驱动 ──
    if darkness_level == "extreme_dark" or is_practically_black:
        # 极暗数据：提高黑场以压掉背景噪声，降低对比度避免噪点被放大
        adapted["black_floor"] = min(adapted["black_floor"] + 0.012, 0.060)
        adapted["contrast"] = max(adapted["contrast"] * 0.75, 0.03)
        adapted["micro_contrast"] = max(adapted["micro_contrast"] * 0.70, 0.03)
        reasons.append(
            f"暗度={darkness_level} → 提高 black_floor (+0.012), "
            f"降低 contrast/micro_contrast (×0.75/×0.70) 以抑制暗部噪声"
        )
    elif darkness_level == "very_dark":
        adapted["black_floor"] = min(adapted["black_floor"] + 0.006, 0.050)
        adapted["micro_contrast"] = max(adapted["micro_contrast"] * 0.85, 0.03)
        reasons.append(
            f"暗度={darkness_level} → 适度提高 black_floor (+0.006), "
            f"降低 micro_contrast (×0.85)"
        )
    elif darkness_level == "bright":
        # 偏亮数据：降低黑场，保持更多暗部细节
        adapted["black_floor"] = max(adapted["black_floor"] - 0.005, 0.005)
        reasons.append(
            f"暗度={darkness_level} → 降低 black_floor (-0.005) 保留暗部细节"
        )

    # ── 噪声驱动 ──
    if noise_level in ("high", "very_high"):
        adapted["micro_contrast"] = max(adapted["micro_contrast"] * 0.60, 0.02)
        adapted["background_desat"] = min(adapted["background_desat"] + 0.15, 0.90)
        adapted["color_separation"] = max(adapted["color_separation"] * 0.70, 0.01)
        reasons.append(
            f"噪声={noise_level} → 降低 micro_contrast (×0.60), "
            f"提高 background_desat (+0.15), 降低 color_separation (×0.70)"
        )
    elif noise_level == "very_low":
        # 极低噪声：可以适度提高微观对比度
        adapted["micro_contrast"] = min(adapted["micro_contrast"] * 1.15, 0.30)
        reasons.append(
            f"噪声={noise_level} → 提高 micro_contrast (×1.15) 利用高信噪比"
        )

    # ── 动态范围驱动 ──
    if dr_ratio > 50:
        adapted["highlight_rolloff"] = min(adapted["highlight_rolloff"] + 0.10, 0.65)
        adapted["contrast"] = max(adapted["contrast"] * 0.85, 0.04)
        reasons.append(
            f"动态范围比={dr_ratio} > 50 → 提高 highlight_rolloff (+0.10), "
            f"降低 contrast (×0.85) 防止高光过曝"
        )
    elif dr_ratio < 5:
        adapted["contrast"] = min(adapted["contrast"] + 0.03, 0.30)
        adapted["highlight_rolloff"] = max(adapted["highlight_rolloff"] - 0.05, 0.05)
        reasons.append(
            f"动态范围比={dr_ratio} < 5 → 提高 contrast (+0.03), "
            f"降低 highlight_rolloff (-0.05) 增强层次"
        )

    # ── 色彩健康度驱动 ──
    if color_health in ("poor", "bad"):
        adapted["saturation"] = max(adapted["saturation"] * 0.85, 0.95)
        adapted["color_separation"] = max(adapted["color_separation"] * 0.60, 0.01)
        adapted["warmth"] = adapted["warmth"] * 0.5
        reasons.append(
            f"色彩健康={color_health} → 降低 saturation (×0.85), "
            f"降低 color_separation (×0.60), 减弱 warmth 避免加剧偏色"
        )
    elif color_health == "excellent":
        adapted["saturation"] = min(adapted["saturation"] * 1.08, 1.50)
        reasons.append(
            f"色彩健康={color_health} → 适度提高 saturation (×1.08)"
        )

    # ── 梯度驱动 (渐晕严重时加深背景去饱和) ──
    gradient_pattern = gradient.get("gradient_pattern", "none")
    if gradient_pattern in ("strong_corner", "strong_vignette"):
        adapted["background_desat"] = min(adapted["background_desat"] + 0.10, 0.90)
        reasons.append(
            f"梯度模式={gradient_pattern} → 提高 background_desat (+0.10) "
            f"弱化光害区域的彩色噪点"
        )

    # ── 锐度驱动 ──
    sharpness_level = sharpness.get("sharpness_level", "moderate")
    if sharpness_level == "very_low":
        adapted["micro_contrast"] = min(adapted["micro_contrast"] * 1.20, 0.30)
        adapted["contrast"] = min(adapted["contrast"] + 0.02, 0.30)
        reasons.append(
            f"锐度={sharpness_level} → 提高 micro_contrast (×1.20) "
            f"和 contrast (+0.02) 补偿图像柔和度"
        )
    elif sharpness_level == "very_high":
        adapted["micro_contrast"] = max(adapted["micro_contrast"] * 0.80, 0.02)
        reasons.append(
            f"锐度={sharpness_level} → 降低 micro_contrast (×0.80) "
            f"避免过度锐化产生伪影"
        )

    # 四舍五入到合理精度
    for k in adapted:
        if isinstance(adapted[k], float):
            adapted[k] = round(adapted[k], 4)

    if len(reasons) == 0:
        reasons.append("诊断指标均在正常范围，无需参数微调")
        return None, reasons

    return adapted, reasons


def _apply_user_prefs(adapted_params, user_prefs):
    """
    将用户偏好叠加到自适应参数上。
    user_prefs 支持:
      - prefer_natural: bool — 整体向 natural 风格偏移
      - max_saturation: float — 饱和度上限
      - max_contrast: float — 对比度上限
      - prefer_warm: bool / prefer_cool: bool — 色温倾向
      - deep_black: bool — 强制深黑背景
    """
    params = dict(adapted_params)
    reasons = []

    if user_prefs.get("prefer_natural"):
        params["saturation"] = max(params["saturation"] * 0.88, 0.95)
        params["contrast"] = max(params["contrast"] * 0.85, 0.03)
        params["micro_contrast"] = max(params["micro_contrast"] * 0.80, 0.02)
        params["color_separation"] = max(params["color_separation"] * 0.70, 0.01)
        reasons.append("用户偏好: 自然风格 → 全面降低处理强度")

    if "max_saturation" in user_prefs:
        cap = user_prefs["max_saturation"]
        if params["saturation"] > cap:
            old = params["saturation"]
            params["saturation"] = cap
            reasons.append(f"用户偏好: 饱和度上限 {cap} → 从 {old} 限制到 {cap}")

    if "max_contrast" in user_prefs:
        cap = user_prefs["max_contrast"]
        if params["contrast"] > cap:
            old = params["contrast"]
            params["contrast"] = cap
            reasons.append(f"用户偏好: 对比度上限 {cap} → 从 {old} 限制到 {cap}")

    if user_prefs.get("prefer_warm"):
        params["warmth"] = min(params["warmth"] + 0.02, 0.06)
        reasons.append("用户偏好: 暖色调 → 增加 warmth")
    elif user_prefs.get("prefer_cool"):
        params["warmth"] = max(params["warmth"] - 0.02, -0.06)
        reasons.append("用户偏好: 冷色调 → 降低 warmth")

    if user_prefs.get("deep_black"):
        params["black_floor"] = min(params["black_floor"] + 0.008, 0.060)
        params["background_desat"] = min(params["background_desat"] + 0.10, 0.90)
        reasons.append("用户偏好: 深黑背景 → 提高 black_floor 和 background_desat")

    for k in params:
        if isinstance(params[k], float):
            params[k] = round(params[k], 4)

    return params, reasons


def _block_slices(shape, block):
    """把 (h, w) 切成 block×block 的块网格。

    返回 (block, ny, nx, pad_h, pad_w)。块边长会 clamp 到 [8, min(h, w)]，
    小图因此退化为单块（局部 == 全局）。
    """
    h, w = shape
    block = max(8, min(int(block), h, w))
    ny = max(1, -(-h // block))
    nx = max(1, -(-w // block))
    return block, ny, nx, ny * block - h, nx * block - w


def _block_values(values, block, ny, nx, pad_h, pad_w):
    """(h, w) → (ny*nx, block*block)。

    边界**必须**用 edge 复制：补 0 会把块分位拉到 0，让黑位与后置断言双双失真。
    """
    padded = np.pad(values, ((0, pad_h), (0, pad_w)), mode="edge")
    tiles = padded.reshape(ny, block, nx, block).transpose(0, 2, 1, 3)
    return tiles.reshape(ny * nx, block * block)


def _block_medians(gray, block=96):
    """逐块中位数（1D，长度 ny*nx）。"""
    block, ny, nx, pad_h, pad_w = _block_slices(gray.shape, block)
    return np.median(_block_values(gray, block, ny, nx, pad_h, pad_w), axis=1)


def _upsample_block_map(block_map, out_shape, block):
    """块网格 → 原尺寸：块内轻度高斯 + 双线性上采样 + 全分辨率轻平滑。"""
    h, w = out_shape
    ny, nx = block_map.shape
    smooth = gaussian_filter(block_map.astype(np.float32), sigma=1.0, mode="nearest")
    up = zoom(smooth, (h / ny, w / nx), order=1, mode="nearest", prefilter=False)
    up = up[:h, :w]
    if up.shape != (h, w):
        up = np.pad(
            up,
            ((0, max(0, h - up.shape[0])), (0, max(0, w - up.shape[1]))),
            mode="edge",
        )
    # 抹掉双线性上采样留下的棱面
    up = gaussian_filter(up, sigma=max(1.0, block / 8.0), mode="nearest")
    return np.clip(up, 0.0, 1.0).astype(np.float32)


def _local_background(luminance, block=96, percentiles=(5.0, 25.0)):
    """分块低分位 + 平滑上采样，稳健估计局部背景。

    与 gaussian_filter(luminance) 的区别：分块低分位对星点/星云亮核稳健
    （高亮不会抬高 p5/p25），而高斯模糊会被亮区整体抬高。

    返回 ({分位: 与输入同形状的 float32 图}, (ny, nx))。
    """
    lum = np.asarray(luminance, dtype=np.float32)
    block, ny, nx, pad_h, pad_w = _block_slices(lum.shape[:2], block)
    pcts = [float(p) for p in percentiles]

    padded = np.pad(lum, ((0, pad_h), (0, pad_w)), mode="edge")
    maps = {p: np.empty((ny, nx), dtype=np.float32) for p in pcts}
    # 逐块行处理：临时内存 O(nx * block²)，避免整幅铺平
    for j in range(ny):
        row = padded[j * block:(j + 1) * block]
        row = row.reshape(block, nx, block).transpose(1, 0, 2).reshape(nx, -1)
        vals = np.percentile(row, pcts, axis=1)
        for i, p in enumerate(pcts):
            maps[p][j, :] = vals[i]

    out = {
        p: _upsample_block_map(maps[p], lum.shape[:2], block)
        for p in pcts
    }
    return out, (ny, nx)


def _repair_local_clip(luminance, floor_map, block, margin=1e-4):
    """确定性保证：任一 block×block 区块的 base toned 中位数 > 0。

    充分条件：块内 ``max(floor_map) < 块内 median(luminance)``。此时
    ``L <= floor`` 的像素占比 <= 50%，故 ``toned > 0`` 的像素占比 >= 50%，
    中位数必为正。

    以**全局常量下移** floor_map 满足该条件：保持 floor_map 平滑，不产生块缝。
    单次计算，无迭代。

    返回 (floor_map, report)。
    """
    block, ny, nx, pad_h, pad_w = _block_slices(luminance.shape, block)
    tiles_l = _block_values(luminance, block, ny, nx, pad_h, pad_w)
    tiles_f = _block_values(floor_map, block, ny, nx, pad_h, pad_w)

    block_median = np.median(tiles_l, axis=1)
    block_floor_max = tiles_f.max(axis=1)

    risky = block_median > 0.0  # 真·黑块不是 bug，不参与修复
    excess = np.where(risky, block_floor_max - block_median, -np.inf)
    n_before = int(np.count_nonzero(excess >= 0.0))
    shift = float(excess.max() + margin) if n_before > 0 else 0.0

    n_after = 0
    if shift > 0.0:
        floor_map = np.maximum(floor_map - shift, 0.0).astype(np.float32)
        tiles_f2 = _block_values(floor_map, block, ny, nx, pad_h, pad_w)
        excess2 = np.where(risky, tiles_f2.max(axis=1) - block_median, -np.inf)
        n_after = int(np.count_nonzero(excess2 >= 0.0))

    report = {
        "block_size": int(block),
        "block_grid": [int(ny), int(nx)],
        "n_blocks": int(ny * nx),
        "blocks_clipped_before_repair": n_before,
        "blocks_clipped_after_repair": n_after,
        "repair_shift": round(shift, 6),
        "repair_applied": bool(shift > 0.0),
    }
    return floor_map, report


def _tone_curve(luminance, profile, return_diagnostics=False, block=96):
    luminance = np.asarray(luminance, dtype=np.float32)
    black_floor = profile["black_floor"]
    low_global = float(np.percentile(luminance, 5))
    bg_global = float(np.percentile(luminance, 25))
    # black_floor 是 L/100 单位的绝对量，但黑位不得越过背景亮度本身。
    # 曝光充分的图上背景 L/100 只有 ~0.03，直接加 0.018 会把整片背景裁成
    # 纯 0（实测 p1 掉到 2e-06，重新触发 BACKGROUND_CRUSHED）。
    # 限制黑位最多吃掉背景亮度的一半，保证背景仍留有层次。
    global_floor = min(max(low_global, 0.0) + black_floor, bg_global * 0.5, 0.25)

    # 上面的全局黑位在**空间非均匀**背景上会超过最暗区域的局部背景，把整片
    # 裁成纯黑：实测某图 BR 角背景 0.0413 < 全局 floor 0.043 → 该角归零，
    # 四角比值 2.07 → 5203，触发 CORNER_NONUNIFORM。
    # 改用逐像素局部黑位，并以 global_floor 封顶。由外层 np.minimum 保证
    # floor_map <= global_floor 逐像素成立；而基础项 (L-f)/(1-f) 关于 f 单调
    # 不增（导数 (L-1)/(1-f)² <= 0），故**黑位本身**只会更低。
    #
    # 注意：这**不**等于「最终 toned 逐像素只会更亮」。末端的 highlight_rolloff
    # 做了 `compressed /= max(compressed)` 的全局重标定，分母会随整体变亮而变大，
    # 因此少数中间调像素可能轻微下降（实测最大 -1.9e-4）。可保证的是：
    # 黑位不升高、归零像素严格减少、净效果为变亮。
    local_maps, _grid = _local_background(
        luminance, block=block, percentiles=(5.0, 25.0)
    )
    low_map, bg_map = local_maps[5.0], local_maps[25.0]
    floor_map = np.minimum(
        global_floor,
        np.minimum(np.maximum(low_map, 0.0) + black_floor, bg_map * 0.5),
    ).astype(np.float32)

    # 后置保证：平滑上采样可能把暗块的局部背景从邻块抬高，公式本身并不保证
    # 「无区块被裁」。这里做一次确定性的全局下移兜住。
    floor_map, repair = _repair_local_clip(luminance, floor_map, block)

    toned = np.clip(
        (luminance - floor_map) / np.maximum(1.0 - floor_map, 1e-6), 0.0, 1.0
    )

    toned = np.power(toned, profile["gamma"])
    contrast = profile["contrast"]
    toned = np.clip(toned + contrast * (toned - 0.5) * 4.0 * toned * (1.0 - toned), 0, 1)

    rolloff = profile["highlight_rolloff"]
    if rolloff > 0:
        compressed = toned / (1.0 + rolloff * toned)
        compressed /= max(float(compressed.max()), 1e-6)
        toned = np.clip(compressed, 0, 1)
    toned = toned.astype(np.float32)

    if not return_diagnostics:
        return toned

    diagnostics = {
        "floor_global": round(global_floor, 6),
        "floor_min": round(float(floor_map.min()), 6),
        "floor_max": round(float(floor_map.max()), 6),
        "floor_mean": round(float(floor_map.mean()), 6),
        "local_bg_min": round(float(bg_map.min()), 6),
        "local_bg_max": round(float(bg_map.max()), 6),
        "clipped_block_frac_before": round(
            repair["blocks_clipped_before_repair"] / max(repair["n_blocks"], 1), 6
        ),
        **repair,
    }
    return toned, diagnostics


def apply_professional_style(
    image,
    style="auto",
    target_type=None,
    color_mode="standard",
    strength=1.0,
    diagnostic_report=None,
    user_prefs=None,
    star_mask=None,
    return_diagnostics=False,
):
    """
    Apply a selected non-generative style grade.

    This adjusts tone, saturation, background cleanliness, and local contrast.
    It never adds new structures or colors that are absent from the source.

    增强参数:
        diagnostic_report: analyze.py 的诊断报告，用于数据驱动风格微调
        user_prefs: 用户偏好 dict
        return_diagnostics: True 时额外返回黑位/区块裁切诊断 dict（第 4 个返回值）

    默认返回三元组 (graded, selected, reasoning)，与既有调用方兼容。
    """
    selected, adapted, reasoning = choose_style_profile(
        target_type=target_type,
        color_mode=color_mode,
        user_style=style,
        diagnostic_report=diagnostic_report,
        user_prefs=user_prefs,
    )
    profile = adapted if adapted is not None else dict(STYLE_PROFILES[selected])
    strength = float(np.clip(strength, 0.0, 1.5))

    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 2:
        source = np.stack([source] * 3, axis=-1)
    if source.shape[2] > 3:
        alpha = source[..., 3:]
        source = source[..., :3]
    else:
        alpha = None

    source = np.clip(source, 0, 1)
    lab = rgb2lab(source)
    luminance = np.clip(lab[..., 0] / 100.0, 0, 1)
    if return_diagnostics:
        toned, style_diagnostics = _tone_curve(
            luminance, profile, return_diagnostics=True
        )
    else:
        toned = _tone_curve(luminance, profile)
        style_diagnostics = None

    detail = luminance - gaussian_filter(luminance, sigma=10)
    signal_low = np.percentile(luminance, 30)
    signal_high = np.percentile(luminance, 97)
    signal_mask = np.clip((luminance - signal_low) / max(signal_high - signal_low, 1e-6), 0, 1)
    micro = profile["micro_contrast"] * strength
    toned = np.clip(toned + detail * signal_mask * micro, 0, 1)

    # 用色调曲线得到的亮度增益作用于 RGB，逐像素保持通道比例。
    #
    # 不能只替换 Lab 的 L 通道：a/b 是**绝对**色度，L 被 black_floor 压暗后
    # 色度不变等于相对放大。实测暗背景的 B/G 由 1.29 一步跳到 3.20（整片
    # 背景发蓝，触发 BACKGROUND_COLOR_CAST），同时暗部被整体压暗约 7 倍。
    blended_luminance = luminance * (1.0 - strength) + toned * strength
    gain = blended_luminance / np.maximum(luminance, 1e-6)
    graded = np.clip(source * gain[..., None], 0, 1)

    # color_separation：以亮度为轴的保比例饱和度提升（不改变通道比例关系）
    sep = profile["color_separation"] * strength
    if abs(sep) > 1e-6:
        neutral = graded.mean(axis=2, keepdims=True)
        graded = np.clip(neutral + (graded - neutral) * (1.0 + sep), 0, 1)

    hsv = rgb2hsv(graded)
    value = hsv[..., 2]
    edge_band = max(16, int(min(value.shape[:2]) * 0.05))
    edges = np.concatenate([
        value[:edge_band, :].flatten(),
        value[-edge_band:, :].flatten(),
        value[:, :edge_band].flatten(),
        value[:, -edge_band:].flatten()
    ])
    edge_median = float(np.median(edges))
    background_threshold = min(max(float(np.percentile(value, 35)), edge_median * 1.15), 0.25)
    background_mask = gaussian_filter((value < background_threshold).astype(np.float32), sigma=4)
    if color_mode == 'emission' or target_type == 'emission_nebula':
        oiii_mask = ((graded[..., 1] + graded[..., 2]) / (2.0 * np.maximum(graded[..., 0], 1e-6))) >= 0.42
        if np.any(oiii_mask):
            oiii_protect = gaussian_filter(oiii_mask.astype(np.float32), sigma=3.0)
            background_mask = background_mask * (1.0 - np.clip(oiii_protect, 0.0, 1.0))
    if star_mask is not None:
        sm = np.asarray(star_mask, dtype=np.float32)
        if sm.ndim == 3:
            sm = np.mean(sm, axis=2)
        star_protect = gaussian_filter(np.clip(sm, 0.0, 1.0), sigma=1.2)
        background_mask = background_mask * (1.0 - star_protect)
    else:
        star_protect = None
    # ── 饱和度提升：RGB 域、以像素均值为轴的保比例色度缩放 ──
    # 旧实现直接乘 HSV 的 S：保持 max 通道不变、把 (max−min) 拉开，非最大通道被压向 0。
    # 当 G≠B 时后果尤其严重 —— 实测受控输入 [0.25,0.10,0.06] 经 ×1.32 后 B 被压到 0，
    # B/G 从 0.600 掉到 0.000；[0.50,0.15,0.09] 同样归零。对发射星云即系统性破坏 OIII。
    # 改用与 :680 color_separation 相同的公式：像素均值严格不变（未越界裁切时），
    # 弱通道只按其偏离均值的比例缩放，不会塌到 0（同例 B/G 保留 0.402）。
    #
    # 掩膜顺序：background_mask / oiii_mask / value 均已基于**提升前**的 graded 算好，
    # 本步不回头改它们 —— 掩膜语义与旧实现逐位一致。
    # （若把本步挪到 rgb2hsv 之前以省一次转换，oiii_mask 会在提升后的图上计算：
    #  (G+B)/2R 会下降，实测 0.45 → 0.350，跌破 :699 的 0.42 阈值，恰好让待保护的
    #  OIII 区失去保护、交还给 background_desat —— 与修复目标反向。）
    sat_factor = 1.0 + (profile["saturation"] - 1.0) * strength
    if abs(sat_factor - 1.0) > 1e-6:
        neutral = graded.mean(axis=2, keepdims=True)
        graded = np.clip(neutral + (graded - neutral) * sat_factor, 0, 1)

    # background_desat 仍在 HSV 域执行（它受 oiii_protect 保护，对 B/G 贡献极小）。
    # graded 已被上式改写，S 必须**重新取**；掩膜沿用提升前的定义。
    desat = profile["background_desat"] * strength
    if abs(desat) > 1e-6:
        hsv_sat = rgb2hsv(graded)
        hsv_sat[..., 1] *= 1.0 - background_mask * desat
        hsv_sat[..., 1] = np.clip(hsv_sat[..., 1], 0, 1)
        graded = hsv2rgb(hsv_sat)

    warmth = profile["warmth"] * strength
    if abs(warmth) > 1e-6:
        signal_blend = np.clip(1.0 - background_mask, 0.0, 1.0)[..., None]
        if star_protect is not None:
            signal_blend = signal_blend * (1.0 - star_protect[..., None])
        edge_weight = np.ones_like(value)
        ew_y = max(8, int(value.shape[0] * 0.06))
        ew_x = max(8, int(value.shape[1] * 0.06))
        edge_weight[:ew_y, :] *= np.linspace(0, 1, ew_y)[:, None]
        edge_weight[-ew_y:, :] *= np.linspace(1, 0, ew_y)[:, None]
        edge_weight[:, :ew_x] *= np.linspace(0, 1, ew_x)[None, :]
        edge_weight[:, -ew_x:] *= np.linspace(1, 0, ew_x)[None, :]
        signal_blend *= edge_weight[..., None]
        gains = np.array([1.0 + warmth, 1.0, 1.0 - warmth], dtype=np.float32)
        warm_graded = np.clip(graded * gains, 0, 1)
        graded = graded * (1.0 - signal_blend) + warm_graded * signal_blend

    if alpha is not None:
        graded = np.dstack([graded, alpha])
    graded = np.clip(graded, 0, 1).astype(np.float32)

    if not return_diagnostics:
        return graded, selected, reasoning

    # 在**最终 RGB 亮度**上再验一次「无区块中位归零」——比色调曲线内部更下游、
    # 更强的证据（后续饱和度/去饱和/加温都还可能有影响）。
    gray = graded[..., :3].mean(axis=2)
    block = style_diagnostics["block_size"]
    final_medians = _block_medians(gray, block)
    style_diagnostics["min_block_median_final"] = round(float(final_medians.min()), 6)
    style_diagnostics["blocks_zeroed_final"] = int(np.count_nonzero(final_medians <= 0.0))
    return graded, selected, reasoning, style_diagnostics


def main():
    import json
    parser = argparse.ArgumentParser(description="深空图像非生成式风格定调 (增强版)")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--style", default="auto",
                        choices=["auto", *STYLE_PROFILES.keys()])
    parser.add_argument("--target-type", default=None)
    parser.add_argument("--color-mode", default="standard")
    parser.add_argument("--strength", type=float, default=1.0)
    parser.add_argument("--diagnostic-report", default=None,
                        help="analyze.py 输出的 JSON 诊断报告路径")
    parser.add_argument("--user-prefs", default=None,
                        help="用户偏好 JSON 字符串")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="输出详细决策理由")
    args = parser.parse_args()

    diagnostic_report = None
    if args.diagnostic_report:
        with open(args.diagnostic_report, "r", encoding="utf-8") as f:
            diagnostic_report = json.load(f)

    user_prefs = None
    if args.user_prefs:
        user_prefs = json.loads(args.user_prefs)

    img = img_as_float32(imread(args.input))
    result, selected, reasoning = apply_professional_style(
        img,
        style=args.style,
        target_type=args.target_type,
        color_mode=args.color_mode,
        strength=args.strength,
        diagnostic_report=diagnostic_report,
        user_prefs=user_prefs,
    )
    imsave(args.output, img_as_ubyte(result))
    print(f"[风格定调] style={selected} output={args.output}")
    if args.verbose:
        print("[决策理由]")
        for r in reasoning:
            print(f"  → {r}")
        if selected in STYLE_PROFILES:
            print(f"[使用参数]")
            for k, v in STYLE_PROFILES[selected].items():
                print(f"  {k}={v}")


if __name__ == "__main__":
    main()
