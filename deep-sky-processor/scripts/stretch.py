#!/usr/bin/env python3
"""
Deep-Sky Histogram Stretching (直方图拉伸)

原理：
  深空线性图像中绝大部分像素集中在暗部，肉眼看去几乎全黑。
  拉伸通过非线性映射将暗部数据重新分配到整个亮度范围，
  使星云的暗弱结构变得可见。

支持方法：
  - arcsinh: 反双曲正弦拉伸（类似对数，但保留亮部细节）
  - mtf:    Midtone Transfer Function（S曲线保留阴影与高光）
  - masked: 蒙版拉伸（保护亮星不让其过曝）
  - gamma:  伽马校正（简单非线性拉伸）

用法:
  python stretch.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from scipy.ndimage import gaussian_filter, median_filter


def arcsinh_stretch(image, factor=30.0, black_point=0.0):
    """
    反双曲正弦拉伸。
    原理: stretched = arcsinh(x * factor) / arcsinh(factor)
    类似对数拉伸，但在亮部和暗部都保持细节。
    factor 越大，拉伸越激进（暗部提亮越多）。
    """
    norm = np.arcsinh(factor)
    stretched = np.arcsinh((image - black_point) * factor) / norm
    return np.clip(stretched, 0, 1)


def mtf_stretch(image, midtones=0.5, shadows=0.0):
    """
    Midtone Transfer Function (MTF) 拉伸。
    原理: MTF = (m-1)*x / ((2*m-1)*x - m)
    其中 m 控制中间调位置。
    本质是一个 S 曲线：保护阴影和高光，只拉伸中间调。
    midtones: 0-1，控制中间调位置 (<0.5 偏暗, >0.5 偏亮)
    shadows: 阴影保护强度
    """
    m = max(midtones, 0.001)
    x = np.clip(image - shadows, 0, 1)
    stretched = ((m - 1) * x) / ((2 * m - 1) * x - m)
    return np.clip(stretched, 0, 1)


def masked_stretch(image, target_bg=0.1, factor=100.0):
    """
    蒙版拉伸（类似 PixInsight MaskedStretch）。
    原理：先做星点蒙版保护亮星（用阈值+模糊），
    然后对背景和星云区域做激进拉伸，亮星区域保持不变。
    这样亮星不会在拉伸过程中过曝。

    对极暗图像自动降级为全局 arcsinh + 亮度归一化。
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)

    # 检测图像是否极暗（中位数极低或亮部不足），若是则使用积极的全局拉伸
    p50 = np.percentile(img_gray, 50)
    p99 = np.percentile(img_gray, 99)
    if p50 <= 0.02 or p99 <= 0.3:
        # 极暗图像：arcsinh + 百分位归一化 + 背景映射
        stretched = arcsinh_stretch(image, factor=factor)
        # 将 0.1%-99.9% 范围映射到 [0, 1]，充分利用动态范围
        p_low = np.percentile(stretched, 0.1)
        p_high = np.percentile(stretched, 99.9)
        if p_high > p_low:
            normalized = np.clip((stretched - p_low) / (p_high - p_low), 0, 1)
        else:
            normalized = stretched
        # 将暗部映射到目标背景亮度
        bg_current = np.percentile(normalized, 10)
        scale = target_bg / max(bg_current, 1e-6)
        result = np.clip(normalized * scale, 0, 1)
        print(f"[masked_stretch] 极暗数据回退: p50={p50:.6f} p99={p99:.6f} "
              f"归一化后 scale={scale:.2f}x")
        return result

    # 正常亮度图像：使用蒙版拉伸
    # 生成星点蒙版：亮于阈值 + 模糊边缘
    threshold = np.percentile(img_gray, 99)
    star_mask = (img_gray > threshold).astype(np.float32)
    star_mask = gaussian_filter(star_mask, sigma=3)
    star_mask = np.clip(star_mask, 0, 1)

    # 对非星区域做拉伸
    bg_mask = 1.0 - star_mask
    stretched = arcsinh_stretch(image, factor=factor)
    if image.ndim == 3:
        star_mask_3d = np.expand_dims(star_mask, axis=-1)
        bg_mask_3d = np.expand_dims(bg_mask, axis=-1)
        result = image * star_mask_3d + stretched * bg_mask_3d
    else:
        result = image * star_mask + stretched * bg_mask

    # 目标背景亮度
    bg_select = stretched[img_gray <= np.percentile(img_gray, 50)]
    bg_level = np.median(bg_select) if len(bg_select) > 0 else target_bg
    result = result * (target_bg / max(bg_level, 0.001))
    return np.clip(result, 0, 1)


def auto_stretch(image, clip_shadows=0.01, clip_highlights=0.999):
    """
    自动拉伸：基于百分位数裁剪后做线性映射。
    原理：找到暗部和亮部的百分位阈值，将中间部分线性拉伸到[0,1]。
    适合快速预览。
    """
    img_gray = image if image.ndim == 2 else np.mean(image, axis=2)
    shadow = np.percentile(img_gray, clip_shadows * 100)
    highlight = np.percentile(img_gray, clip_highlights * 100)
    stretched = (image - shadow) / max(highlight - shadow, 0.0001)
    return np.clip(stretched, 0, 1)


def deep_stretch(image, shadow_pctl=0.5, highlight_pctl=99.9, gamma=0.4):
    """
    针对极暗深空数据的激进拉伸（两阶段：百分位裁剪 + 伽马增强）。

    原理：
      1. 对每个通道分别计算低/高百分位阈值，线性拉伸到 [0,1]
      2. 用 gamma<1 做伽马校正，大幅提亮暗部、增强暗部对比度
      3. 保留各通道独立的比例关系，不破坏原始颜色差异

    参数:
      shadow_pctl:  暗部裁剪百分位 (默认 0.5，即背景中位数附近)
      highlight_pctl: 亮部裁剪百分位 (默认 99.9，保留最亮星点)
      gamma:        伽马值 (<1 增强暗部，默认 0.4)
    """
    result = image.copy()
    is_color = image.ndim == 3 and image.shape[2] >= 3

    if is_color:
        for c in range(image.shape[2]):
            ch = image[..., c]
            shadow = np.percentile(ch, shadow_pctl)
            highlight = np.percentile(ch, highlight_pctl)
            span = highlight - shadow
            if span > 1e-9:
                diff = ch - shadow
                epsilon = 0.05 * max(shadow, 1e-6)
                val = diff / epsilon
                corrected_ch = np.where(
                    val > 50.0,
                    diff,
                    epsilon * np.log(1.0 + np.exp(np.clip(val, -50.0, 50.0)))
                )
                result[..., c] = np.clip(corrected_ch / span, 0, 1)
    else:
        shadow = np.percentile(image, shadow_pctl)
        highlight = np.percentile(image, highlight_pctl)
        span = highlight - shadow
        if span > 1e-9:
            diff = image - shadow
            epsilon = 0.05 * max(shadow, 1e-6)
            val = diff / epsilon
            corrected_ch = np.where(
                val > 50.0,
                diff,
                epsilon * np.log(1.0 + np.exp(np.clip(val, -50.0, 50.0)))
            )
            result = np.clip(corrected_ch / span, 0, 1)

    # 伽马校正：gamma < 1 时暗部被大幅提亮，亮部变化较小
    result = np.power(result, gamma)
    return np.clip(result, 0, 1)


def _smoothstep(values):
    values = np.clip(values, 0.0, 1.0)
    return values * values * (3.0 - 2.0 * values)


def _lift_underfilled_highlights(luminance, target_bg, target_p99=0.5):
    """Lift signal above the background when the stretched range is underfilled."""
    current_p99 = float(np.percentile(luminance, 99.0))
    if current_p99 >= target_p99 or current_p99 <= target_bg + 1e-6:
        return luminance

    signal_position = (
        (luminance - float(target_bg))
        / max(current_p99 - float(target_bg), 1e-6)
    )
    signal_weight = _smoothstep(signal_position)
    lift = min(float(target_p99) - current_p99, 0.35)
    return np.clip(luminance + signal_weight * lift, 0, 1)


def very_dark_stretch(image, factor=25.0, gamma=0.45,
                      shadow_pctl=0.1, highlight_pctl=99.5,
                      target_bg=0.12, min_p99=0.5):
    """
    极暗数据专用保色拉伸。

    使用每通道保守黑点消除基线，但非线性曲线只从亮度生成，并将相同
    的逐像素增益应用回 RGB，避免独立通道归一化放大噪声或改写色相。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    rgb = source[..., :3] if is_color else source

    if is_color:
        flat = rgb.reshape(-1, 3)
        medians = np.percentile(flat, 50, axis=0)
        black_points = np.percentile(flat, shadow_pctl, axis=0)
        black_points = np.minimum(black_points, medians * 0.25)
        corrected = np.clip(rgb - black_points, 0, None)
        luminance = (
            0.2126 * corrected[..., 0]
            + 0.7152 * corrected[..., 1]
            + 0.0722 * corrected[..., 2]
        )
    else:
        median = float(np.percentile(rgb, 50))
        black_point = min(
            float(np.percentile(rgb, shadow_pctl)),
            median * 0.25,
        )
        corrected = np.clip(rgb - black_point, 0, None)
        luminance = corrected

    scale_ref = float(np.percentile(luminance, highlight_pctl))
    if scale_ref <= 1e-9:
        scale_ref = float(np.max(luminance))
    if scale_ref <= 1e-9:
        return np.zeros_like(source, dtype=np.float32)

    normalized_luminance = np.clip(luminance / scale_ref, 0, None)
    stretched_luminance = (
        np.arcsinh(normalized_luminance * float(factor))
        / np.arcsinh(float(factor))
    )
    stretched_luminance = np.power(
        np.clip(stretched_luminance, 0, 1),
        float(gamma),
    )

    background_mask = luminance <= np.percentile(luminance, 50)
    positive_background = stretched_luminance[
        background_mask & (luminance > 0)
    ]
    if positive_background.size:
        background_level = float(np.median(positive_background))
        stretched_luminance *= (
            float(target_bg) / max(background_level, 1e-6)
        )

    stretched_luminance = _lift_underfilled_highlights(
        stretched_luminance,
        target_bg=float(target_bg),
        target_p99=float(min_p99),
    )

    if is_color:
        gain = stretched_luminance / np.maximum(luminance, 1e-9)
        result = corrected * gain[..., None]
        peak = np.max(result, axis=2, keepdims=True)
        result = result / np.maximum(peak, 1.0)
        if source.shape[2] > 3:
            result = np.dstack([result, source[..., 3:]])
    else:
        result = stretched_luminance

    return np.clip(result, 0, 1).astype(np.float32)


def emission_stretch(image, shadow_pctl=0.5, highlight_pctl=99.9,
                     gamma=0.33, target_bg=0.08,
                     min_p99=0.5):
    """
    发射星云自适应保色拉伸 (优化版)。

    每通道只校正暗部黑点，再从亮度通道生成一条共享拉伸曲线，并将
    同一亮度增益应用回 RGB。这样不会用独立通道归一化篡改 Hα/OIII
    的真实颜色比例，也避免共享 RGB 标尺把弱 G/B 通道数值压死。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    if not is_color:
        return deep_stretch(
            source,
            shadow_pctl=shadow_pctl,
            highlight_pctl=highlight_pctl,
            gamma=gamma,
        )

    flat = source[..., :3].reshape(-1, 3)
    # 限制黑点不超过中位数的 30%，防止极暗数据二次剪切过度
    medians = np.percentile(flat, 50, axis=0)
    black_points = np.percentile(flat, shadow_pctl, axis=0)
    black_points = np.minimum(black_points, medians * 0.3)
    
    diff = source[..., :3] - black_points
    corrected = np.clip(diff, 0, None)
    
    # 从亮度生成共享曲线；RGB 最终只乘同一个逐像素增益。
    luminance = (
        0.2126 * corrected[..., 0]
        + 0.7152 * corrected[..., 1]
        + 0.0722 * corrected[..., 2]
    )

    scale_ref = float(np.percentile(luminance, highlight_pctl))
    if scale_ref <= 1e-9:
        scale_ref = float(np.max(luminance))
    if scale_ref <= 1e-9:
        return np.zeros_like(source, dtype=np.float32)

    stretched_luminance = np.power(
        np.clip(luminance / scale_ref, 0, None),
        gamma,
    )

    background_mask = luminance <= np.percentile(luminance, 50)
    positive_background = stretched_luminance[
        background_mask & (luminance > 0)
    ]
    if positive_background.size:
        background_level = float(np.median(positive_background))
        stretched_luminance *= float(target_bg) / max(background_level, 1e-6)

    stretched_luminance = _lift_underfilled_highlights(
        stretched_luminance,
        target_bg=float(target_bg),
        target_p99=float(min_p99),
    )

    gain = stretched_luminance / np.maximum(luminance, 1e-9)
    result = corrected * gain[..., None]

    # 对过亮像素做逐像素等比例 rolloff，避免单通道裁切改变色相。
    peak = np.max(result, axis=2, keepdims=True)
    result = result / np.maximum(peak, 1.0)

    if source.shape[2] > 3:
        result = np.dstack([result, source[..., 3:]])
    return np.clip(result, 0, 1).astype(np.float32)
def ghs_stretch(image, sp=0.01, b=8.0, c=0.0):
    """
    Generalized Hyperbolic Stretch (GHS) 广义双曲拉伸。

    数学模型:
      f(x) = [sinh(b * (x - sp)) - sinh(-b * sp)] / [sinh(b * (1.0 - sp)) - sinh(-b * sp)]
    sp: 对称点（通常在背景中值附近，如 0.002 ~ 0.05）
    b: 拉伸强度因子（通常为 2 ~ 15，数值越大拉伸越强烈）
    c: 高光平滑 rolloff 因子，可选。
    """
    x = np.clip(image, 0, 1)
    b = max(float(b), 1e-5)
    sp = float(sp)

    denom = np.sinh(b * (1.0 - sp)) - np.sinh(-b * sp)
    if abs(denom) < 1e-9:
        denom = 1e-9

    num = np.sinh(b * (x - sp)) - np.sinh(-b * sp)
    stretched = num / denom

    # 压制高光核心 Rolloff 保护
    if c > 0:
        c = float(c)
        stretched = np.power(stretched, 1.0 + c * (1.0 - stretched))

    return np.clip(stretched, 0, 1)


def masked_ghs_stretch(image, sp=0.01, b=8.0, protect_strength=0.5,
                       smooth_sigma=5.0, target_bg=0.08,
                       shadow_pctl=0.0, highlight_pctl=99.9, gamma=0.45):
    """
    基于亮度掩膜自适应保护的分区 GHS 拉伸。

    高光保护掩膜(Luminance Mask)使得高亮度核心和亮星主要应用温和的拉伸，而暗星云/背景主要
    应用激进的 GHS 拉伸，最后合并并自适应平移背景。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    source_gray = source if not is_color else np.mean(source, axis=2)

    # 极暗线性数据先映射到可用动态范围。shadow_pctl=0 时不减黑位，
    # 所有原本大于零的微弱信号都会保留。
    low = (
        0.0
        if float(shadow_pctl) <= 0
        else float(np.percentile(source_gray, shadow_pctl))
    )
    high = float(np.percentile(source_gray, highlight_pctl))
    if high <= low + 1e-12:
        high = float(np.max(source_gray))
    if high <= low + 1e-12:
        return np.clip(source, 0, 1)

    normalized = np.clip((source - low) / (high - low), 0, 1)
    normalized = np.power(
        normalized,
        float(np.clip(gamma, 0.2, 1.0)),
    )
    img_gray = normalized if not is_color else np.mean(normalized, axis=2)

    # 1. 产生亮度保护掩膜
    mask = gaussian_filter(img_gray, sigma=smooth_sigma)
    mask_max = float(mask.max())
    if mask_max > 1e-8:
        mask = mask / mask_max
    # 用 sqrt (power 0.5) 展宽中高光保护区域
    mask = np.power(mask, 0.5)
    mask = np.clip(mask * float(protect_strength), 0, 1)

    if is_color:
        mask_3d = np.expand_dims(mask, axis=-1)
    else:
        mask_3d = mask

    # 2. 激进 GHS 拉伸轨道
    if sp is None or sp < 0:
        # 自动取灰度中值作为 sp
        sp = float(np.median(img_gray))

    stretched_strong = ghs_stretch(normalized, sp=sp, b=b)

    # 3. 温和拉伸保护轨道（主要针对亮部核心和星点）
    stretched_weak = arcsinh_stretch(normalized, factor=5.0)

    # 4. 根据亮度掩膜插值混合
    blended = stretched_weak * mask_3d + stretched_strong * (1.0 - mask_3d)

    # 5. 目标背景自动亮度归一化
    bg_mask = source_gray <= np.percentile(source_gray, 50)
    positive_bg = blended[bg_mask & (source_gray > 0)]
    bg_level = (
        np.median(positive_bg)
        if positive_bg.size > 0
        else np.median(blended[bg_mask])
    )
    if bg_level > 1e-4:
        result = blended * (float(target_bg) / bg_level)
    else:
        result = blended

    return np.clip(result, 0, 1)


def apply_luminance_stretch(image, method='arcsinh', **kwargs):
    """
    亮度通道拉伸，保留原始色彩比例。
    原理：将图像转换到 Lab 色彩空间，只对 L 通道拉伸，
    保持 a/b 色彩通道不变，避免拉伸时颜色偏移。

    对极暗数据（median < 0.001）不使用 Lab 转换（极低值下不稳定），
    直接对 RGB 做整体拉伸，保持颜色比例。
    """
    is_color = image.ndim == 3 and image.shape[2] >= 3
    if not is_color:
        return globals()[f'{method}_stretch'](image, **kwargs)

    # 这两种方法自己从 RGB 亮度构造共享增益。先转 Lab 会丢失通道
    # 信息，使专用的保色路径失效。
    if method == 'emission':
        return emission_stretch(image, **kwargs)
    if method == 'very_dark':
        return very_dark_stretch(image, **kwargs)

    gray = np.mean(image, axis=2)
    # 极暗数据：跳过 Lab 转换，直接在 RGB 上拉伸
    if np.median(gray) < 0.001:
        stretch_func = {
            'arcsinh': arcsinh_stretch,
            'mtf': mtf_stretch,
            'masked': masked_stretch,
            'auto': auto_stretch,
            'deep': deep_stretch,
            'very_dark': very_dark_stretch,
            'emission': emission_stretch,
            'ghs': ghs_stretch,
            'masked_ghs': masked_ghs_stretch,
        }.get(method, arcsinh_stretch)
        return stretch_func(image, **kwargs)

    from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb
    lab = rgb2lab(image)
    L = lab[..., 0] / 100.0

    stretch_func = {
        'arcsinh': arcsinh_stretch,
        'mtf': mtf_stretch,
        'masked': masked_stretch,
        'auto': auto_stretch,
        'deep': deep_stretch,
        'very_dark': very_dark_stretch,
        'emission': emission_stretch,
        'ghs': ghs_stretch,
        'masked_ghs': masked_ghs_stretch,
    }.get(method, arcsinh_stretch)

    L_stretched = stretch_func(L, **kwargs)

    lab[..., 0] = np.clip(L_stretched * 100.0, 0, 100)
    result = lab2rgb(lab)
    return np.clip(result, 0, 1)


def main():
    p = argparse.ArgumentParser(description='深空图像直方图拉伸')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='masked',
                   choices=['arcsinh', 'mtf', 'masked', 'auto', 'deep',
                            'very_dark', 'emission', 'ghs', 'masked_ghs'],
                   help='拉伸方法 (默认: masked)')
    p.add_argument('--factor', type=float, default=30.0, help='arcsinh factor (默认: 30)')
    p.add_argument('--midtones', type=float, default=0.3, help='MTF midtones (默认: 0.3)')
    p.add_argument('--target-bg', type=float, default=0.08, help='目标背景亮度 (默认: 0.08)')
    p.add_argument('--sp', type=float, default=0.01, help='GHS 对称点 (默认: 0.01)')
    p.add_argument('--b', type=float, default=8.0, help='GHS 强度因子 (默认: 8.0)')
    p.add_argument('--protect-strength', type=float, default=0.5, help='Masked GHS 核心保护强度 (默认: 0.5)')
    p.add_argument('--luminance-only', action='store_true',
                   help='仅在亮度通道上拉伸')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[拉伸] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    kwargs = {'factor': args.factor}
    if args.method == 'mtf':
        kwargs = {'midtones': args.midtones}
    elif args.method == 'masked':
        kwargs = {'target_bg': args.target_bg, 'factor': args.factor}
    elif args.method == 'deep':
        kwargs = {'shadow_pctl': 0.5, 'highlight_pctl': 99.9, 'gamma': 0.4}
    elif args.method == 'very_dark':
        kwargs = {
            'factor': args.factor,
            'gamma': 0.45,
            'target_bg': args.target_bg,
        }
    elif args.method == 'emission':
        kwargs = {
            'shadow_pctl': 1.0,
            'highlight_pctl': 99.94,
            'gamma': 0.43,
            'target_bg': args.target_bg,
        }
    elif args.method == 'ghs':
        kwargs = {'sp': args.sp, 'b': args.b}
    elif args.method == 'masked_ghs':
        kwargs = {'sp': args.sp, 'b': args.b, 'protect_strength': args.protect_strength, 'target_bg': args.target_bg}

    if args.luminance_only:
        result = apply_luminance_stretch(img, method=args.method, **kwargs)
    else:
        stretch_func = {
            'arcsinh': arcsinh_stretch,
            'mtf': mtf_stretch,
            'masked': masked_stretch,
            'auto': auto_stretch,
            'deep': deep_stretch,
            'very_dark': very_dark_stretch,
            'emission': emission_stretch,
            'ghs': ghs_stretch,
            'masked_ghs': masked_ghs_stretch,
        }[args.method]
        result = stretch_func(img, **kwargs)

    imsave(args.output, img_as_ubyte(result))
    print(f"[拉伸] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
