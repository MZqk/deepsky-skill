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


def background_neutralize(image, bg_percentile=30, sample_radius=20):
    """
    背景中性化。
    原理：采样图像中暗部区域（背景），计算 RGB 各通道在背景中的平均值，
    调整各通道增益使背景呈现中性灰 (R=G=B)。
    
    bg_percentile: 用于确定背景的亮度百分位
    """
    img_gray = np.mean(image, axis=2)
    bg_threshold = np.percentile(img_gray, bg_percentile)
    bg_mask = img_gray < bg_threshold

    if np.sum(bg_mask) < 100:
        print("[WARN] 背景样本不足，跳过背景中性化")
        return image

    bg_r = np.mean(image[..., 0][bg_mask])
    bg_g = np.mean(image[..., 1][bg_mask])
    bg_b = np.mean(image[..., 2][bg_mask])
    bg_mean = (bg_r + bg_g + bg_b) / 3

    if bg_mean < 0.001:
        return image

    scale_r = bg_mean / max(bg_r, 0.001)
    scale_g = bg_mean / max(bg_g, 0.001)
    scale_b = bg_mean / max(bg_b, 0.001)

    result = image.copy()
    result[..., 0] *= scale_r
    result[..., 1] *= scale_g
    result[..., 2] *= scale_b

    print(f"[背景中性化] R:{scale_r:.3f} G:{scale_g:.3f} B:{scale_b:.3f}")
    return np.clip(result, 0, 1)


def white_balance_from_stars(image, method='gray_world'):
    """
    白平衡调整。
    原理：
    - gray_world: 假设整个场景的平均色是中性灰。
      在深空图像中，大量暗弱恒星的平均颜色接近白色，
      因此这个假设近似成立。
    - percentile: 使用亮部的百分位来估计白平衡。
    """
    if method == 'gray_world':
        mean_r = np.mean(image[..., 0])
        mean_g = np.mean(image[..., 1])
        mean_b = np.mean(image[..., 2])
        mean_all = (mean_r + mean_g + mean_b) / 3
        if mean_all < 0.001:
            return image

        result = image.copy()
        result[..., 0] *= mean_all / max(mean_r, 0.001) * 0.9
        result[..., 1] *= mean_all / max(mean_g, 0.001)
        result[..., 2] *= mean_all / max(mean_b, 0.001) * 1.1
        return np.clip(result, 0, 1)

    elif method == 'percentile':
        p_r = np.percentile(image[..., 0], 95)
        p_g = np.percentile(image[..., 1], 95)
        p_b = np.percentile(image[..., 2], 95)
        p_max = max(p_r, p_g, p_b)
        if p_max < 0.001:
            return image
        result = image.copy()
        result[..., 0] *= p_max / max(p_r, 0.001)
        result[..., 1] *= p_max / max(p_g, 0.001)
        result[..., 2] *= p_max / max(p_b, 0.001)
        return np.clip(result, 0, 1)

    return image


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
    """
    if image.max() <= 0:
        return image.copy()
    lab = rgb2lab(image)
    a_channel = lab[..., 1]

    # 只作用于偏绿部分 (a < 0)
    green_mask = (a_channel < 0).astype(np.float32)
    a_channel = a_channel * (1.0 - green_mask * strength)

    lab[..., 1] = a_channel
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


def auto_color_calibrate(image):
    """
    自动颜色校准：背景中性化 + 白平衡 + 绿色噪声去除。
    适用于无法精确测光的 JPG/PNG 深空图像。
    """
    print("[自动色彩校准] 开始...")
    result = background_neutralize(image, bg_percentile=25)
    result = white_balance_from_stars(result, method='gray_world')
    result = remove_green_noise(result, strength=0.25)
    return np.clip(result, 0, 1)


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
