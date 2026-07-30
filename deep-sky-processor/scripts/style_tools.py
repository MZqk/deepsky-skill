#!/usr/bin/env python3
"""Non-generative professional style grading for deep-sky images."""

import argparse
import sys

import numpy as np
from scipy.ndimage import gaussian_filter
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
        "black_floor": 0.018,
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


def _tone_curve(luminance, profile):
    black_floor = profile["black_floor"]
    low = float(np.percentile(luminance, 5))
    floor = min(max(low, 0.0) + black_floor, 0.25)
    toned = np.clip((luminance - floor) / max(1.0 - floor, 1e-6), 0, 1)

    toned = np.power(toned, profile["gamma"])
    contrast = profile["contrast"]
    toned = np.clip(toned + contrast * (toned - 0.5) * 4.0 * toned * (1.0 - toned), 0, 1)

    rolloff = profile["highlight_rolloff"]
    if rolloff > 0:
        compressed = toned / (1.0 + rolloff * toned)
        compressed /= max(float(compressed.max()), 1e-6)
        toned = np.clip(compressed, 0, 1)
    return toned.astype(np.float32)


def apply_professional_style(
    image,
    style="auto",
    target_type=None,
    color_mode="standard",
    strength=1.0,
    diagnostic_report=None,
    user_prefs=None,
):
    """
    Apply a selected non-generative style grade.

    This adjusts tone, saturation, background cleanliness, and local contrast.
    It never adds new structures or colors that are absent from the source.

    增强参数:
        diagnostic_report: analyze.py 的诊断报告，用于数据驱动风格微调
        user_prefs: 用户偏好 dict
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
    toned = _tone_curve(luminance, profile)

    detail = luminance - gaussian_filter(luminance, sigma=10)
    signal_low = np.percentile(luminance, 30)
    signal_high = np.percentile(luminance, 97)
    signal_mask = np.clip((luminance - signal_low) / max(signal_high - signal_low, 1e-6), 0, 1)
    micro = profile["micro_contrast"] * strength
    toned = np.clip(toned + detail * signal_mask * micro, 0, 1)

    lab[..., 0] = (luminance * (1.0 - strength) + toned * strength) * 100.0
    sep = profile["color_separation"] * strength
    lab[..., 1] *= 1.0 + sep
    lab[..., 2] *= 1.0 + sep
    graded = np.clip(lab2rgb(lab), 0, 1)

    hsv = rgb2hsv(graded)
    value = hsv[..., 2]
    background_threshold = np.percentile(value, 35)
    background_mask = gaussian_filter((value < background_threshold).astype(np.float32), sigma=4)
    sat_factor = 1.0 + (profile["saturation"] - 1.0) * strength
    hsv[..., 1] *= sat_factor
    hsv[..., 1] *= 1.0 - background_mask * profile["background_desat"] * strength
    hsv[..., 1] = np.clip(hsv[..., 1], 0, 1)
    graded = hsv2rgb(hsv)

    warmth = profile["warmth"] * strength
    if abs(warmth) > 1e-6:
        gains = np.array([1.0 + warmth, 1.0, 1.0 - warmth], dtype=np.float32)
        graded = np.clip(graded * gains, 0, 1)

    if alpha is not None:
        graded = np.dstack([graded, alpha])
    return np.clip(graded, 0, 1).astype(np.float32), selected, reasoning


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
