#!/usr/bin/env python3
"""
Deep-Sky Sharpening & Deconvolution (锐化与反卷积)

原理：
  大气湍流、望远镜衍射会使星点从理想点光源扩散为光斑 (PSF卷积)。
  锐化/反卷积的目标是恢复被模糊的高频细节，使星点更锐利、
  星云纹理更清晰。

方法：
  - unsharp_mask:  反锐化掩膜（经典锐化，增强边缘对比度）
  - wiener:        维纳滤波反卷积（频域去模糊，需PSF估计）
  - multiscale:    多尺度锐化（小尺度强，大尺度弱）
  - high_pass:     高通滤波增强（提取高频细节叠加回去）

用法:
  python sharpen.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import convolve2d
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from skimage.restoration import wiener


def estimate_psf(size=15, sigma=2.0):
    """
    估计点扩散函数 (PSF)。
    原理：用二维高斯核近似大气+望远镜的模糊效果。
    半高全宽 (FWHM) ≈ 2.355 * sigma 像素。
    深空图像典型 FWHM 为 2-5 像素。
    """
    y, x = np.mgrid[-size//2:size//2+1, -size//2:size//2+1]
    psf = np.exp(-(x**2 + y**2) / (2 * sigma**2))
    psf /= psf.sum()
    return psf


def unsharp_mask(image, amount=1.0, radius=2.0, threshold=0.0):
    """
    反锐化掩膜 (Unsharp Mask)。
    原理：
      1. 对原图做高斯模糊得到「模糊版」
      2. 原图 - 模糊版 = 边缘/高频信息
      3. 将边缘信息乘以 amount 叠加回原图
    radius 控制锐化作用的边缘宽度。
    threshold 保护平滑区域（差异小于阈值的区域不锐化）。
    """
    blurred = gaussian_filter(image, sigma=radius)
    if image.ndim == 3:
        mask = image - blurred
    else:
        mask = image - blurred

    if threshold > 0:
        mask[np.abs(mask) < threshold] = 0

    sharpened = image + amount * mask
    return np.clip(sharpened, 0, 1)


def wiener_deconvolution(image, psf_sigma=2.0, balance=0.1):
    """
    维纳滤波反卷积。
    原理：
      在频域中：F_corrected = F_blurred * conj(PSF_F) / (|PSF_F|² + K)
      其中 K = balance * mean(|PSF_F|²) 是正则化参数，
      抑制噪声在反卷积过程中被放大。
    这是频域中最经典的反卷积算法，适合恢复被均匀模糊的图像。
    """
    psf = estimate_psf(sigma=psf_sigma)

    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c], _ = wiener(image[..., c], psf, balance)
        return np.clip(result, 0, 1)
    else:
        result, _ = wiener(image, psf, balance)
        return np.clip(result, 0, 1)


def multiscale_sharpen(image, layers=3, base_amount=1.5, decay=0.5):
    """
    多尺度锐化（拉普拉斯金字塔细节增强）。
    原理：将图像分解为多个尺度层，提取每层细节（current - blurred），
    按不同强度放大后叠加回原始图像。
    小尺度层（高频细节）施加强锐化，大尺度层施加弱锐化。
    比单层 USM 更能保护平滑区域不产生伪影，同时保持图像整体亮度。
    """
    result = image.copy()
    current = image.copy()

    for layer in range(layers):
        amount = base_amount * (decay ** layer)
        radius = 2.0 ** layer
        blurred = gaussian_filter(current, sigma=radius)
        detail = current - blurred
        result = result + detail * amount
        current = blurred

    return np.clip(result, 0, 1)


def adaptive_signal_sharpen(image, amount=0.7, fwhm=3.0,
                            background_percentile=40.0,
                            star_protection=0.75):
    """
    根据 FWHM 在亮度域锐化，并用信号/星核蒙版保护背景和高光。
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

    radius = float(np.clip(float(fwhm) / 3.0, 0.6, 3.0))
    blurred = gaussian_filter(luminance, sigma=radius)
    detail = luminance - blurred

    background_limit = float(np.percentile(luminance, background_percentile))
    signal_high = float(np.percentile(luminance, 90.0))
    signal_mask = np.clip(
        (luminance - background_limit)
        / max(signal_high - background_limit, 1e-6),
        0,
        1,
    )
    signal_mask = gaussian_filter(signal_mask, sigma=max(radius, 1.0))

    background_detail = detail[luminance <= background_limit]
    noise_sigma = (
        float(np.median(np.abs(
            background_detail - np.median(background_detail)
        ))) * 1.4826
        if background_detail.size else 0.0
    )
    detail_gate = np.clip(
        (np.abs(detail) - noise_sigma * 1.5)
        / max(noise_sigma * 2.0, 1e-6),
        0,
        1,
    )

    star_threshold = float(np.percentile(luminance, 99.2))
    star_mask = gaussian_filter(
        (luminance >= star_threshold).astype(np.float32),
        sigma=max(radius * 0.8, 0.8),
    )
    protection = 1.0 - np.clip(
        star_mask * float(star_protection),
        0,
        0.95,
    )
    effective_mask = signal_mask * detail_gate * protection

    delta = detail * float(amount) * effective_mask
    delta_limit = max(float(np.percentile(np.abs(detail), 99.5)) * 1.5, 0.01)
    delta = np.clip(delta, -delta_limit, delta_limit)
    target_luminance = np.clip(luminance + delta, 0, 1)

    if is_color:
        gain = target_luminance / np.maximum(luminance, 1e-8)
        result = rgb * gain[..., None]
        peak = np.max(result, axis=2, keepdims=True)
        result = result / np.maximum(peak, 1.0)
        if source.shape[2] > 3:
            result = np.dstack([result, source[..., 3:]])
    else:
        result = target_luminance

    report = {
        "amount": float(amount),
        "fwhm": float(fwhm),
        "radius": radius,
        "noise_sigma": noise_sigma,
        "background_limit": background_limit,
        "star_threshold": star_threshold,
        "effective_pixel_ratio": float(np.mean(effective_mask > 0.05)),
        "mean_abs_delta": float(np.mean(np.abs(delta))),
    }
    return np.clip(result, 0, 1).astype(np.float32), report


def high_pass_enhance(image, sigma=10.0, strength=0.3):
    """
    高通滤波增强。
    原理：原图 - 大尺度模糊版 = 中高频信息，
    叠加回原图来增强星云纹理和暗纹。
    适合增强星云内部的细丝结构和尘埃带。
    """
    blurred = gaussian_filter(image, sigma=sigma)
    high_freq = image - blurred
    result = image + strength * high_freq
    return np.clip(result, 0, 1)


def apply_luminance_sharpen(image, method='unsharp_mask', **kwargs):
    """
    仅在亮度通道上锐化。
    避免锐化操作影响色彩。
    """
    from color_conv import safe_rgb2lab as rgb2lab, safe_lab2rgb as lab2rgb

    if image.ndim == 2:
        sharpen_func = globals()[method]
        return sharpen_func(image, **kwargs)

    lab = rgb2lab(image)
    L = lab[..., 0] / 100.0
    sharpen_func = {
        'unsharp_mask': unsharp_mask,
        'wiener_deconvolution': wiener_deconvolution,
        'multiscale_sharpen': multiscale_sharpen,
        'high_pass_enhance': high_pass_enhance,
    }[method]
    L_sharp = sharpen_func(L, **kwargs)
    lab[..., 0] = np.clip(L_sharp * 100.0, 0, 100)
    result = lab2rgb(lab)
    return np.clip(result, 0, 1)


def main():
    p = argparse.ArgumentParser(description='深空图像锐化与反卷积')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='multiscale_sharpen',
                   choices=['unsharp_mask', 'wiener_deconvolution',
                            'multiscale_sharpen', 'high_pass_enhance'],
                   help='锐化方法 (默认: multiscale_sharpen)')
    p.add_argument('--amount', type=float, default=1.5, help='锐化强度 (默认: 1.5)')
    p.add_argument('--radius', type=float, default=2.0, help='锐化半径 (默认: 2.0)')
    p.add_argument('--psf-sigma', type=float, default=2.0, help='PSF sigma (默认: 2.0)')
    p.add_argument('--luminance-only', action='store_true',
                   help='仅在亮度通道上锐化')
    p.add_argument('--light', action='store_true', help='轻度锐化')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[锐化] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    if args.light:
        args.amount /= 2

    kwargs = {}
    if args.method == 'unsharp_mask':
        kwargs = {'amount': args.amount, 'radius': args.radius}
    elif args.method == 'wiener_deconvolution':
        kwargs = {'psf_sigma': args.psf_sigma, 'balance': args.amount * 0.05}
    elif args.method == 'multiscale_sharpen':
        kwargs = {'base_amount': args.amount}
    elif args.method == 'high_pass_enhance':
        kwargs = {'strength': args.amount * 0.2}

    if args.luminance_only:
        result = apply_luminance_sharpen(img, method=args.method, **kwargs)
    else:
        sharpen_func = {
            'unsharp_mask': unsharp_mask,
            'wiener_deconvolution': wiener_deconvolution,
            'multiscale_sharpen': multiscale_sharpen,
            'high_pass_enhance': high_pass_enhance,
        }[args.method]
        result = sharpen_func(img, **kwargs)

    imsave(args.output, img_as_ubyte(result))
    print(f"[锐化] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
