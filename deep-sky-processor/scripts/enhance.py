#!/usr/bin/env python3
"""
Deep-Sky Detail Enhancement (星云细节增强)

原理：
  拉伸后的深空图像中，星云的暗弱纹理和明亮核心的动态范围仍然很大。
  增强模块通过多尺度动态范围压缩和局部对比度增强，
  让星云的细丝结构、暗纹和明亮核心的细节同时可见。

方法：
  - hdr_compress:  HDR 动态范围压缩（多尺度）
  - clahe:        自适应直方图均衡化 (CLAHE)
  - curves:       S 曲线对比度增强
  - local_contrast: 局部对比度增强

用法:
  python enhance.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from skimage.exposure import equalize_adapthist


def hdr_multiscale_compress(image, layers=3, strength=0.5):
    """
    多尺度 HDR 动态范围压缩。
    原理：
      1. 将图像分解为多个尺度（高频层 = 原图 - 模糊版）
      2. 对高频层施加压缩（减弱极高对比度）
      3. 保留低频层（整体亮度分布）
      
    效果：明亮核心（如星系核、星云中心）的亮度被压缩，
    暗弱外围（如星系悬臂、星云边缘）的细节被提升。
    """
    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c] = _hdr_compress_channel(image[..., c], layers, strength)
        return np.clip(result, 0, 1)
    else:
        return _hdr_compress_channel(image, layers, strength)


def protected_hdr_compress(image, strength=0.5, knee_percentile=85.0):
    """
    亮度域高光压缩。

    仅压缩 knee 以上的亮核/亮星，阴影和中间调保持不变；RGB 使用
    同一个逐像素亮度增益，因此不会产生逐通道 HDR 的色相漂移。
    """
    source = np.asarray(image, dtype=np.float32)
    is_color = source.ndim == 3 and source.shape[2] >= 3
    rgb = source[..., :3] if is_color else source
    if is_color:
        luminance = (
            0.2126 * rgb[..., 0]
            + 0.7152 * rgb[..., 1]
            + 0.0722 * rgb[..., 2]
        )
    else:
        luminance = rgb

    knee = float(np.percentile(luminance, knee_percentile))
    knee = float(np.clip(knee, 0.05, 0.9))
    span = max(1.0 - knee, 1e-6)
    normalized = np.clip((luminance - knee) / span, 0, 1)
    compressed = normalized / (
        1.0 + float(strength) * normalized
    )
    target_luminance = np.where(
        luminance > knee,
        knee + compressed * span,
        luminance,
    )

    if is_color:
        gain = target_luminance / np.maximum(luminance, 1e-8)
        result = rgb * gain[..., None]
        if source.shape[2] > 3:
            result = np.dstack([result, source[..., 3:]])
    else:
        result = target_luminance

    report = {
        "strength": float(strength),
        "knee": knee,
        "p99_before": float(np.percentile(luminance, 99.0)),
        "p99_after": float(np.percentile(target_luminance, 99.0)),
        "median_before": float(np.median(luminance)),
        "median_after": float(np.median(target_luminance)),
        "affected_ratio": float(np.mean(luminance > knee)),
    }
    return np.clip(result, 0, 1).astype(np.float32), report


def _hdr_compress_channel(channel, layers, strength):
    """
    多尺度 HDR 动态范围压缩（修正版拉普拉斯金字塔）。

    原理：
      1. 构建高斯金字塔：逐层模糊
      2. 构建拉普拉斯金字塔：每层 = 当前层 - 下一层模糊版（细节层）
      3. 对过亮的细节层做温和压缩（soft compression）
      4. 从最低频开始，逐层加回压缩后的细节，重建图像

    关键修正（相比旧版）：
      - 重建阶段不再对结果做高斯模糊（旧版 sigma=32 的模糊是严重 bug）
      - 使用 soft compression 代替 aggressive division，保留暗部细节
    """
    # 构建高斯金字塔
    gaussian_layers = [channel.copy()]
    current = channel.copy()
    for i in range(layers):
        sigma = 2.0 ** (i + 2)
        current = gaussian_filter(current, sigma=sigma)
        gaussian_layers.append(current)

    # 构建拉普拉斯金字塔（高频细节层）
    laplacian_layers = []
    for i in range(layers):
        detail = gaussian_layers[i] - gaussian_layers[i + 1]
        # Soft compression: 只对极端对比度做温和压缩
        # 小 detail 几乎不变，大 detail 被压缩
        # 公式: detail / (1 + strength * |detail|)
        compressed = detail / (1.0 + strength * np.abs(detail))
        laplacian_layers.append(compressed)

    # 重建：从最低频开始，逐层加回压缩后的细节
    result = gaussian_layers[-1]
    for detail in reversed(laplacian_layers):
        result = result + detail

    return np.clip(result, 0, 1)


def apply_clahe(image, clip_limit=0.02, kernel_size=64):
    """
    CLAHE (Contrast Limited Adaptive Histogram Equalization)。
    原理：在每个小窗口内做受限的直方图均衡化，
    增强局部对比度而不过分放大噪声。
    适合增强星云内部的细丝和涟漪纹理。

    clip_limit: 对比度限制（防止噪声放大）
    kernel_size: 局部窗口大小
    """
    if image.ndim == 3:
        from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb
        lab = rgb2lab(image)
        L = lab[..., 0] / 100.0
        L_enhanced = equalize_adapthist(L, kernel_size=kernel_size, clip_limit=clip_limit)
        lab[..., 0] = np.clip(L_enhanced * 100.0, 0, 100)
        result = lab2rgb(lab)
    else:
        result = equalize_adapthist(image, kernel_size=kernel_size, clip_limit=clip_limit)
    return np.clip(result, 0, 1)


def apply_curves(image, shadows=1.0, midtones=1.3, highlights=1.0):
    """
    曲线调整。
    原理：分别控制阴影、中间调、高光的亮度。
    - shadows:  阴影亮度因子 (>1 提亮暗部)
    - midtones: 中间调亮度因子 (>1 提亮星云主体)
    - highlights: 高光亮度因子 (=1 保护亮星不过曝)
    """
    # 使用样条插值构造 S 曲线
    x = np.array([0, 0.25, 0.5, 0.75, 1.0])
    y = np.array([0, 0.25 * shadows, 0.5 * midtones,
                  0.75 * highlights, 1.0])
    y = np.clip(y, 0, 1)

    def interpolate(v):
        return np.interp(v, x, y)

    if image.ndim == 3:
        from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb
        lab = rgb2lab(image)
        L = lab[..., 0] / 100.0
        L_adj = interpolate(L)
        lab[..., 0] = np.clip(L_adj * 100.0, 0, 100)
        result = lab2rgb(lab)
    else:
        result = interpolate(image)

    return np.clip(result, 0, 1)


def local_contrast_enhance(image, radius=20, strength=0.3):
    """
    局部对比度增强。
    原理：原图 - 局部模糊版 = 局部细节，
    将局部细节乘以 strength 叠加回去。
    比全局锐化更适合增强星云纹理。
    """
    blurred = gaussian_filter(image, sigma=radius)
    detail = np.subtract(image, blurred)
    result = image + strength * detail
    return np.clip(result, 0, 1)


def local_nebula_enhance(image, center_y, center_x, radius=500, strength=0.25,
                         star_mask=None):
    """
    对指定区域（如眉月星云中央）做局部对比度和纹理增强。

    原理：
      1. 创建以 (center_x, center_y) 为中心的径向软蒙版
      2. 在蒙版区域内做双尺度结构增强
      3. 用软蒙版将增强结果与原图混合，避免硬边缘
      4. 可选星点反向蒙版，避免带星图上同步强化星核

    参数:
      center_y, center_x: 星云中心位置（像素坐标）
      radius: 增强区域半径（像素）
      strength: 增强强度 (0-1, 推荐 0.2-0.35)
    """
    from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb

    h, w = image.shape[:2]
    Y, X = np.ogrid[:h, :w]
    dist = np.sqrt((Y - center_y) ** 2 + (X - center_x) ** 2)
    mask = np.clip(1.0 - dist / radius, 0, 1)
    mask = gaussian_filter(mask, sigma=radius / 3)

    if image.ndim == 3:
        lab = rgb2lab(np.clip(image, 0, 1))
        luminance = lab[..., 0] / 100.0
    else:
        luminance = image

    fine_sigma = max(2.0, radius / 95.0)
    medium_sigma = max(5.0, radius / 32.0)
    fine_smooth = gaussian_filter(luminance, sigma=fine_sigma)
    medium_smooth = gaussian_filter(luminance, sigma=medium_sigma)
    fine_detail = luminance - fine_smooth
    medium_detail = fine_smooth - medium_smooth
    detail = fine_detail * 0.45 + medium_detail * 0.85
    signal_low = np.percentile(medium_smooth, 35)
    signal_high = np.percentile(medium_smooth, 98)
    signal_mask = np.clip(
        (medium_smooth - signal_low) / max(signal_high - signal_low, 1e-6),
        0,
        1,
    )
    mask *= gaussian_filter(signal_mask, sigma=max(3.0, radius / 35.0))
    if star_mask is not None:
        stars = np.asarray(star_mask, dtype=np.float32)
        if stars.ndim == 3:
            stars = np.max(stars, axis=2)
        if stars.shape != luminance.shape:
            raise ValueError(
                f"star_mask shape {stars.shape} != image shape {luminance.shape}"
            )
        stars = gaussian_filter(np.clip(stars, 0, 1), sigma=1.5)
        mask *= np.clip(1.0 - stars * 0.95, 0.05, 1.0)
    enhanced_luminance = np.clip(luminance + strength * detail * mask, 0, 1)

    if image.ndim == 3:
        lab[..., 0] = enhanced_luminance * 100.0
        result = lab2rgb(lab)
    else:
        result = enhanced_luminance

    return np.clip(result, 0, 1)


def positive_starless_detail_enhance(image, original_linear, starless_linear,
                                    strength=0.75, full_frame=False):
    """
    用外部无星线性图引导正向结构增强。

    只提取无星层中高于局部背景的多尺度正细节，并按当前 RGB 比例增益。
    负残差和绝对亮度均不参与融合，因此不会把 StarNet 暗环、黑洞或
    无星层背景色带入成片。
    """
    current = np.asarray(image, dtype=np.float32)
    original = np.asarray(original_linear, dtype=np.float32)
    starless = np.asarray(starless_linear, dtype=np.float32)
    if current.shape != original.shape or current.shape != starless.shape:
        raise ValueError(
            "image, original_linear and starless_linear must have identical shapes"
        )

    difference = np.clip(np.subtract(original, starless), 0, None)
    difference_gray = (
        np.max(difference[..., :3], axis=2)
        if difference.ndim == 3 else difference
    )
    artifact_threshold = np.percentile(difference_gray, 97.0)
    artifact_mask = binary_dilation(
        difference_gray > artifact_threshold,
        iterations=4,
    )
    if starless.ndim == 3:
        local_background = gaussian_filter(starless, sigma=(2, 2, 0))
        cleaned = np.where(artifact_mask[..., None], local_background, starless)
        luminance = (
            0.299 * cleaned[..., 0]
            + 0.587 * cleaned[..., 1]
            + 0.114 * cleaned[..., 2]
        )
    else:
        local_background = gaussian_filter(starless, sigma=2)
        cleaned = np.where(artifact_mask, local_background, starless)
        luminance = cleaned

    broad_detail = np.clip(np.subtract(luminance, gaussian_filter(luminance, 28)), 0, None)
    fine_detail = np.clip(np.subtract(luminance, gaussian_filter(luminance, 5)), 0, None)
    structure = broad_detail + 0.45 * fine_detail
    normalization = float(np.percentile(structure, 99.7))
    if normalization <= 1e-9:
        return current.copy()
    detail = gaussian_filter(np.clip(structure / normalization, 0, 1), sigma=1)

    signal_threshold = np.percentile(luminance, 63)
    signal_mask = gaussian_filter(
        (luminance > signal_threshold).astype(np.float32),
        sigma=12,
    )
    if full_frame:
        radial = 1.0
    else:
        h, w = luminance.shape
        yy, xx = np.mgrid[:h, :w]
        radial = np.exp(
            -(((xx - w / 2) / max(w * 0.43, 1)) ** 4
              + ((yy - h / 2) / max(h * 0.43, 1)) ** 4)
        )
    guide = np.clip(detail * signal_mask * radial, 0, 1)

    if current.ndim == 3:
        current_luminance = (
            0.299 * current[..., 0]
            + 0.587 * current[..., 1]
            + 0.114 * current[..., 2]
        )
        gain = 1.0 + strength * guide * (1.0 - current_luminance)
        result = current * gain[..., None]
    else:
        result = current + strength * guide * (1.0 - current)
    return np.clip(result, 0, 1).astype(np.float32)


def main():
    p = argparse.ArgumentParser(description='深空星云细节增强')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='hdr',
                   choices=['hdr', 'clahe', 'curves', 'local_contrast'],
                   help='增强方法 (默认: hdr)')
    p.add_argument('--strength', type=float, default=0.5,
                   help='增强强度 (默认: 0.5)')
    p.add_argument('--midtones', type=float, default=1.3,
                   help='曲线中间调 (默认: 1.3)')
    p.add_argument('--clip-limit', type=float, default=0.02,
                   help='CLAHE 对比度限制 (默认: 0.02)')
    p.add_argument('--light', action='store_true', help='轻度增强')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[增强] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    if args.light:
        args.strength /= 2

    if args.method == 'hdr':
        result = hdr_multiscale_compress(img, strength=args.strength)
    elif args.method == 'clahe':
        result = apply_clahe(img, clip_limit=args.clip_limit)
    elif args.method == 'curves':
        result = apply_curves(img, midtones=args.midtones, shadows=args.strength)
    elif args.method == 'local_contrast':
        result = local_contrast_enhance(img, strength=args.strength)

    imsave(args.output, img_as_ubyte(result))
    print(f"[增强] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
