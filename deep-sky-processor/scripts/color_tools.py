#!/usr/bin/env python3
"""
Deep-Sky Color Tools (颜色校准与调色)

原理：
  深空图像的颜色需要经过多步校准和处理。
  首先是背景中性化（让空荡的天空区域呈现中性灰黑），
  然后是白平衡调整（基于参考恒星的颜色），
  最后是艺术性调色（控制色彩饱和度和色调方向）。

方法：
  - background_neutralize: 背景中性化
  - white_balance:        白平衡调整
  - color_saturation:     色彩饱和度增强
  - green_noise_remove:   去除绿色噪声
  - channel_alignment:    RGB通道对齐

用法:
  python color_tools.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from scipy.ndimage import gaussian_filter, median_filter
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from color_conv import safe_rgb2hsv as rgb2hsv, safe_hsv2rgb as hsv2rgb, safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb


def _background_pixels(rgb, gray, bg_percentile):
    """取亮度最低的一批像素作为背景样本，返回 (N,3) 数组。

    掩膜用 `<=`，并在命中不足时按亮度排序取前 k 个兜底。
    旧实现用严格 `<`，当大量像素恰好等于分位阈值时（DBE 之后很常见）
    掩膜会变成空集，导致整步被静默跳过。
    """
    threshold = float(np.percentile(gray, bg_percentile))
    mask = gray <= threshold
    count = int(np.count_nonzero(mask))
    if count >= 100:
        return rgb[mask]

    total = gray.size
    k = min(total, max(100, int(total * bg_percentile / 100.0)))
    idx = np.argpartition(gray.ravel(), k - 1)[:k]
    return rgb.reshape(-1, 3)[idx]


def background_neutralize(image, bg_percentile=30, sample_radius=20):
    """
    背景中性化：只扣除**通道之间**的背景差异，让背景呈中性。

    为什么不是"各通道各自减到 0"：
      DBE 阶段（`normalize_background_subtracted`）已经给背景留了 pedestal，
      若这里再把每个通道的背景中值整体减掉，等于重复扣黑 —— 实测会把 90%
      的画面压成纯 0，随后所有"背景亮度分位"类判据全部失效。
      因此这里只去掉通道差异，保留共同黑位（取各通道最小背景）。

    为什么是加性而非乘性：
      乘性增益 `bg_mean / max(bg_ch, floor)` 在近零通道上会爆炸
      （B≈0 时可算出 ~96× 增益），且该增益被无差别地乘到天体本体上，
      把暖核染成冷核。加性平移不可能放大任何通道。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return source

    rgb = source[..., :3]
    gray = rgb.mean(axis=2)
    flat = _background_pixels(rgb, gray, bg_percentile)

    black_points = np.median(flat, axis=0).astype(np.float64)
    # 只保留通道差异，共同黑位（pedestal）原样保留
    common = float(np.min(black_points))
    offsets = black_points - common

    if float(np.max(offsets)) <= 1e-9:
        print("[背景中性化-加法] 背景已中性，无需调整")
        return source

    corrected = np.clip(rgb.astype(np.float64) - offsets, 0, None)
    result = corrected.astype(np.float32)
    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])

    print(f"[背景中性化-加法] offsets={offsets.round(6).tolist()} "
          f"(保留共同黑位 {common:.6f})")
    return result


def _reference_star_mask(rgb, sat_limit=0.995):
    """挑出可用作白平衡参考的恒星像素；样本不可用时返回 None。"""
    from skimage.morphology import disk, white_tophat

    gray = rgb.mean(axis=2)
    # 小尺度 top-hat：压制星系/星云等延展结构，只保留紧凑亮源
    response = white_tophat(gray, disk(3))
    positive = response[response > 0]
    if positive.size == 0:
        return None

    median = float(np.median(positive))
    mad = float(np.median(np.abs(positive - median))) * 1.4826
    threshold = max(
        float(np.percentile(positive, 99.5)),
        median + 4.0 * max(mad, 1e-9),
    )
    mask = response >= threshold

    peak = rgb.max(axis=2)
    mask &= peak < sat_limit        # 排除过曝星核（已失去颜色信息）
    mask &= peak > 1e-6

    # 排除落在延展主体（星系盘/星云）上的像素
    body = gaussian_filter(gray, sigma=15.0)
    mask &= body <= float(np.percentile(body, 75))
    return mask


def white_balance_from_stars(image, method='stars', max_gain=1.25,
                             min_star_samples=50, strength=0.35):
    """
    基于参考恒星的白平衡（有界、保守、亮度中性）。

    与旧实现的关键差别：
      - 旧版名为 from_stars，实际用**全图均值**做 gray-world，并硬编码
        R×0.9 / B×1.1 的偏置。深空画面由星系积分色主导而非恒星主导，
        gray-world 假设不成立，那两个偏置还会把画面整体推冷。
      - 新版真正采样恒星像素（见 _reference_star_mask），取样本 RGB 中值，
        增益取"几何平均 / 通道值"，使参考星校正后三通道相等。

    strength 默认 0.35（保守）而不是 1.0：
      把参考星强行拉成中性只在"星应当是白的"这一前提成立时才对。低银纬视场
      （如 M31，受银河尘埃红化）或相机光谱响应偏离 CIE 时该前提不成立 ——
      实测把 1141 颗场星拉中性会把星系盘从 R/G≈1.55 压到 ≈1.01，
      抹掉天体本身的暖色。因此默认只施加 35% 的校正量，
      既能去掉明显的仪器色偏，又不会改写天体固有颜色。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return source

    rgb = source[..., :3]

    def _apply(raw_gains, label):
        gains = 1.0 + float(strength) * (raw_gains - 1.0)
        print(f"[白平衡-{label}] raw={raw_gains.round(3).tolist()} "
              f"strength={float(strength):.2f} → gains={gains.round(3).tolist()}")
        return _apply_gains(source, rgb, gains)

    if method == 'percentile':
        p = np.percentile(rgb.reshape(-1, 3), 95.0, axis=0).astype(np.float64)
        if float(np.min(p)) <= 1e-6:
            print("[白平衡-percentile] 存在近零通道，跳过")
            return source
        log_p = np.log(p)
        raw = np.exp(np.clip(log_p.mean() - log_p,
                             -np.log(max_gain), np.log(max_gain)))
        return _apply(raw, "percentile")

    if method not in ('stars', 'gray_world'):
        return source

    mask = _reference_star_mask(rgb)
    n_samples = 0 if mask is None else int(np.count_nonzero(mask))
    if n_samples < min_star_samples:
        print(f"[白平衡-星采样] 星样本不足 (n={n_samples}<{min_star_samples})，"
              f"跳过（保留加法中性化结果）")
        return source

    star_rgb = np.median(rgb[mask], axis=0).astype(np.float64)
    if float(np.min(star_rgb)) <= 1e-6:
        print("[白平衡-星采样] 参考星存在近零通道，跳过")
        return source

    # 原始增益 = 几何平均 / 通道值，使参考星三通道在校正后相等（亮度不变）
    log_star = np.log(star_rgb)
    raw_gains = np.exp(np.clip(log_star.mean() - log_star,
                               -np.log(max_gain), np.log(max_gain)))
    print(f"[白平衡-星采样] n={n_samples} star_rgb={star_rgb.round(5).tolist()}")
    return _apply(raw_gains, "星采样")


def _apply_gains(source, rgb, gains):
    """按增益缩放 RGB 并夹回 [0,1]（保持与旧版一致的输出契约）。"""
    result = np.clip(rgb.astype(np.float64) * gains, 0, 1).astype(np.float32)
    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])
    return result


def enhance_saturation(image, factor=1.5, protect_background=True,
                       bg_protection_percentile=20):
    """
    增强色彩饱和度。
    原理：在 HSV 色彩空间中，增加 S 通道的值。
    protect_background: 保护暗区不被着色（保持背景纯净）。
    """
    hsv = rgb2hsv(image)

    # 生成背景保护蒙版
    if protect_background:
        v_channel = hsv[..., 2]
        bg_threshold = np.percentile(v_channel, bg_protection_percentile)
        bg_mask = (v_channel < bg_threshold).astype(np.float32)
        bg_mask = gaussian_filter(bg_mask, sigma=5)

        # 在背景区域降低饱和度增强
        local_factor = 1.0 + (factor - 1.0) * (1.0 - bg_mask)
        hsv[..., 1] = np.clip(hsv[..., 1] * local_factor, 0, 1)
    else:
        hsv[..., 1] = np.clip(hsv[..., 1] * factor, 0, 1)

    result = hsv2rgb(hsv)
    return np.clip(result, 0, 1)


def remove_green_noise(image, strength=0.3):
    """
    去除绿色噪声 (SCNR - Subtract Chrominance Noise Reduction)。
    原理：绿色噪声来源——拜耳阵列中有两个绿色像素（RGGB），
    导致绿色通道对噪声更敏感。去除方法是在 Lab 色彩空间中将
    a 通道（绿→品红方向）中偏绿的部分向中性色推移。

    修正按亮度加权：Lab 的 a/b 在近零亮度处极不稳定，对线性阶段很暗的
    背景施加色度修正会把像素推成负值 —— 实测在线性图上直接跑会把 45%
    的画面变成纯 0。SCNR 的意义本来也只在中高亮区。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.max() <= 0:
        return source.copy()

    lab = rgb2lab(source)
    luminance = lab[..., 0]
    reference = float(np.percentile(luminance, 99.9))
    if reference <= 1e-6:
        return source.copy()

    weight = np.clip(luminance / (0.05 * reference), 0.0, 1.0)
    a_channel = lab[..., 1]

    # 只作用于偏绿部分 (a < 0)，且随亮度渐入
    green_mask = (a_channel < 0).astype(np.float32) * weight
    lab[..., 1] = a_channel * (1.0 - green_mask * float(strength))

    result = lab2rgb(lab)
    return np.clip(result, 0, 1)


def channel_alignment(image, shift_b=0, shift_r=0):
    """
    RGB通道对齐。
    原理：大气色散或光学色差可能导致R/G/B通道间有微小偏移。
    这里做简单的亚像素平移校正。
    """
    from scipy.ndimage import shift as nd_shift
    result = image.copy()
    if shift_b != 0:
        result[..., 2] = nd_shift(image[..., 2], (shift_b, shift_b), order=1)
    if shift_r != 0:
        result[..., 0] = nd_shift(image[..., 0], (shift_r, shift_r), order=1)
    return np.clip(result, 0, 1)


def auto_color_calibrate(image, return_report=False):
    """
    自动颜色校准：加性背景中性化 + 有界星采样白平衡 + 绿色噪声去除。
    适用于无法精确测光的 JPG/PNG 深空图像。

    顺序很重要：先做加性逐通道黑点把背景压到 0 附近，
    再做全局白平衡 —— 此时增益作用在已中性的背景上，不会重新引入背景色偏。
    """
    print("[自动色彩校准] 开始（加法黑点 + 星采样白平衡）...")
    result = background_neutralize(image, bg_percentile=25)
    result = white_balance_from_stars(result, method='stars')
    result = remove_green_noise(result, strength=0.25)
    result = np.clip(result, 0, 1)
    if return_report:
        return result, {"mode": "additive_blackpoint_plus_star_white_balance"}
    return result


def emission_nebula_calibrate(image, background_percentile=1.0,
                              star_balance_strength=0.65,
                              oiii_blue_injection=0.0,
                              return_report=False):
    """
    发射星云颜色校准。

    仅减去每通道暗部基线，再用未饱和亮星的软蒙版校正星色。
    不对整幅图应用灰度世界增益，因此保留 Hα 主导的真实红色结构。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return (source, {}) if return_report else source

    flat = source[..., :3].reshape(-1, 3)
    black_points = np.percentile(flat, background_percentile, axis=0)
    # 限制 B 通道背景剪除黑点，防止极暗的 B 通道被过度减除截断为 0
    black_points[2] = min(black_points[2], black_points[1])
    result = np.clip(source[..., :3] - black_points, 0, None)

    # 默认不从 G 人为构造 B。仅在用户明确知道输入是双窄带映射时，
    # 才允许通过参数做有限的 OIII 蓝通道注入。
    if oiii_blue_injection > 0:
        injection = float(np.clip(oiii_blue_injection, 0.0, 1.0))
        result[..., 2] = np.maximum(
            result[..., 2],
            result[..., 1] * injection,
        )

    luminance = (
        0.299 * result[..., 0]
        + 0.587 * result[..., 1]
        + 0.114 * result[..., 2]
    )
    low = np.percentile(luminance, 99.2)
    high = np.percentile(luminance, 99.85)
    star_samples = (luminance > low) & (luminance < high)
    gains = np.ones(3, dtype=np.float32)
    star_sample_count = int(np.count_nonzero(star_samples))
    if star_sample_count >= 100:
        star_rgb = np.median(result[star_samples], axis=0)
        target = float(np.exp(np.mean(np.log(np.maximum(star_rgb, 1e-9)))))
        gains = np.clip(target / np.maximum(star_rgb, 1e-9), 0.65, 1.55)
        star_mask = gaussian_filter(star_samples.astype(np.float32), sigma=2.0)
        star_mask = np.clip(star_mask[..., None] * star_balance_strength, 0, 1)
        result *= 1.0 + (gains - 1.0) * star_mask
        print(
            f"[发射星云校色] black={black_points.round(6).tolist()} "
            f"star_gains={gains.round(3).tolist()}"
        )
    else:
        print(f"[发射星云校色] black={black_points.round(6).tolist()} 星样本不足")

    calibrated = np.clip(result, 0, 1).astype(np.float32)
    report = {
        "black_points": black_points.astype(float).tolist(),
        "star_gains": gains.astype(float).tolist(),
        "star_sample_count": star_sample_count,
        "star_gains_scope": "star_mask_only",
        "oiii_blue_injection": float(oiii_blue_injection),
    }
    return (calibrated, report) if return_report else calibrated


def stabilize_emission_channels(image, collapse_ratio=0.02,
                                max_gain=1.35, strength=0.6,
                                target_ratios=None):
    """
    对发射星云信号区做有边界的通道恢复。

    默认只修复接近数值塌缩的通道。target_ratios 仅供显式覆盖，
    例如 {"r_over_g": 1.8, "r_over_b": 2.2}；不会自动把 Hα
    主导图像强制白平衡。
    """
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        return source, {"applied": False, "reason": "not_rgb"}

    rgb = source[..., :3]
    luminance = (
        0.2126 * rgb[..., 0]
        + 0.7152 * rgb[..., 1]
        + 0.0722 * rgb[..., 2]
    )
    low = float(np.percentile(luminance, 55.0))
    high = float(np.percentile(luminance, 99.2))
    signal_mask = (luminance > low) & (luminance < high)
    if np.count_nonzero(signal_mask) < 100:
        return source, {"applied": False, "reason": "insufficient_signal"}

    background_mask = luminance <= np.percentile(luminance, 35.0)
    background_rgb = np.median(rgb[background_mask], axis=0)
    signal_rgb = np.clip(rgb[signal_mask] - background_rgb, 0, None)
    channel_signal = np.percentile(signal_rgb, 95.0, axis=0)
    strongest = max(float(np.max(channel_signal)), 1e-9)
    gains = np.ones(3, dtype=np.float32)

    for index in range(3):
        relative = float(channel_signal[index]) / strongest
        if relative < float(collapse_ratio):
            needed = (strongest * float(collapse_ratio)) / max(
                float(channel_signal[index]),
                1e-9,
            )
            gains[index] = min(float(max_gain), needed)

    if target_ratios:
        red = max(float(channel_signal[0]), 1e-9)
        for key, index in (("r_over_g", 1), ("r_over_b", 2)):
            target = target_ratios.get(key)
            if target and float(target) > 0:
                desired = red / float(target)
                needed = desired / max(float(channel_signal[index]), 1e-9)
                gains[index] = max(
                    gains[index],
                    min(float(max_gain), max(1.0, needed)),
                )

    if np.allclose(gains, 1.0):
        return source, {
            "applied": False,
            "reason": "channels_not_collapsed",
            "channel_signal": channel_signal.astype(float).tolist(),
            "background_rgb": background_rgb.astype(float).tolist(),
            "gains": gains.astype(float).tolist(),
        }

    soft_mask = gaussian_filter(signal_mask.astype(np.float32), sigma=3.0)
    soft_mask = np.clip(soft_mask * float(strength), 0, 1)[..., None]
    corrected = rgb * (1.0 + (gains - 1.0) * soft_mask)
    peak = np.max(corrected, axis=2, keepdims=True)
    corrected = corrected / np.maximum(peak, 1.0)
    if source.shape[2] > 3:
        corrected = np.dstack([corrected, source[..., 3:]])

    return np.clip(corrected, 0, 1).astype(np.float32), {
        "applied": True,
        "reason": "bounded_signal_recovery",
        "channel_signal": channel_signal.astype(float).tolist(),
        "background_rgb": background_rgb.astype(float).tolist(),
        "gains": gains.astype(float).tolist(),
        "collapse_ratio": float(collapse_ratio),
        "max_gain": float(max_gain),
        "target_ratios": target_ratios,
    }


def main():
    p = argparse.ArgumentParser(description='深空图像色彩工具')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='auto',
                   choices=['auto', 'emission', 'background', 'white_balance',
                            'saturation', 'green_noise'],
                   help='色彩处理方法 (默认: auto)')
    p.add_argument('--factor', type=float, default=1.5, help='饱和度因子 (默认: 1.5)')
    p.add_argument('--strength', type=float, default=0.3, help='绿噪去除强度 (默认: 0.3)')
    p.add_argument('--light', action='store_true', help='轻度处理')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[色彩] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    if args.light:
        args.factor = min(args.factor, 1.2)
        args.strength = min(args.strength, 0.15)

    if args.method == 'auto':
        result = auto_color_calibrate(img)
    elif args.method == 'emission':
        result = emission_nebula_calibrate(img)
    elif args.method == 'background':
        result = background_neutralize(img)
    elif args.method == 'white_balance':
        result = white_balance_from_stars(img, method='gray_world')
    elif args.method == 'saturation':
        result = enhance_saturation(img, factor=args.factor)
    elif args.method == 'green_noise':
        result = remove_green_noise(img, strength=args.strength)

    imsave(args.output, img_as_ubyte(result))
    print(f"[色彩] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
