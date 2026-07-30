#!/usr/bin/env python3
"""
Deep-Sky Noise Reduction (多尺度降噪)

原理：
  深空图像中的噪声分为亮度噪点（影响颗粒感）和色彩噪点（影响纯净度）。
  噪声在空间上也有多尺度特性：小尺度噪声（单像素）和大尺度噪声（块状伪影）
  需要不同强度的处理。

方法：
  - bilateral:  双边滤波（边缘保持，适合保护星点）
  - nonlocal:   Non-Local Means（利用图像自相似性降噪）
  - wavelet:    小波多尺度降噪（在多个尺度上分别降噪）
  - luminance_chroma:  分离亮度/色彩通道，色彩通道施加更强降噪

用法:
  python denoise.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from scipy.ndimage import gaussian_filter
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from skimage.restoration import denoise_bilateral, denoise_nl_means, estimate_sigma


def denoise_bilateral_wrapper(image, sigma_color=0.05, sigma_spatial=15):
    """
    双边滤波降噪。
    原理：结合空间邻近性和像素值相似性进行滤波。
    在星点边缘处，相邻像素值差异大 → 权重低 → 边缘得以保留。
    sigma_color: 色彩标准差（越大降噪越强）
    sigma_spatial: 空间标准差（越大平滑范围越大）
    """
    if image.ndim == 3:
        return denoise_bilateral(image, sigma_color=sigma_color,
                                 sigma_spatial=sigma_spatial, channel_axis=-1)
    else:
        return denoise_bilateral(image, sigma_color=sigma_color,
                                 sigma_spatial=sigma_spatial)


def denoise_nonlocal(image, patch_size=7, patch_distance=11, h=0.05):
    """
    Non-Local Means 降噪。
    原理：在整张图中搜索相似的图像块，对相似块做加权平均。
    利用深空图像中星场和星云纹理的自相似性，在保护结构的同时降噪。
    """
    sigma = estimate_sigma(image, channel_axis=-1) if image.ndim == 3 else estimate_sigma(image)
    h_factor = h / max(sigma, 0.001)
    if image.ndim == 3:
        return denoise_nl_means(image, patch_size=patch_size,
                                patch_distance=patch_distance,
                                h=h_factor * sigma, channel_axis=-1)
    else:
        return denoise_nl_means(image, patch_size=patch_size,
                                patch_distance=patch_distance,
                                h=h_factor * sigma)


def denoise_wavelet_multiscale(image, levels=4, threshold_factor=0.8):
    """
    小波多尺度降噪（需要 pywt 库）。
    原理：将图像分解为不同尺度的小波系数，在每个尺度上对高频系数
    （代表噪声）做软阈值处理，保留低频系数（代表结构）。
    """
    try:
        import pywt
    except ImportError:
        print("[ERROR] 小波降噪需要 pywt 库: pip install PyWavelets")
        sys.exit(1)

    if image.ndim == 3:
        result = np.zeros_like(image)
        for c in range(image.shape[2]):
            result[..., c] = _wavelet_denoise_channel(
                image[..., c], levels, threshold_factor
            )
        return result
    else:
        return _wavelet_denoise_channel(image, levels, threshold_factor)


def _wavelet_denoise_channel(channel, levels, threshold_factor):
    import pywt
    coeffs = pywt.wavedec2(channel, 'db4', level=levels)
    coeff_arr, coeff_slices = pywt.coeffs_to_array(coeffs)

    sigma = np.median(np.abs(coeff_arr - np.median(coeff_arr))) / 0.6745
    threshold = sigma * threshold_factor * np.sqrt(2 * np.log(coeff_arr.size))

    for i in range(1, levels + 1):
        coeff_arr[coeff_slices[i]['dd']] = pywt.threshold(
            coeff_arr[coeff_slices[i]['dd']], threshold, mode='soft'
        )

    coeffs_new = pywt.array_to_coeffs(coeff_arr, coeff_slices, output_format='wavedec2')
    return pywt.waverec2(coeffs_new, 'db4')


def denoise_luminance_chroma(image, lum_strength=0.04, chroma_strength=0.10):
    """
    分离亮度/色彩通道降噪。
    原理：人眼对色彩噪点更敏感。提取亮度通道 (Y) 和色彩通道 (Cb/Cr)，
    对色彩通道施加 2-3 倍强度的降噪，同时保护亮度细节。
    """
    from skimage.color import rgb2ycbcr, ycbcr2rgb

    ycbcr = rgb2ycbcr(image)
    Y = ycbcr[..., 0]
    Cb = ycbcr[..., 1]
    Cr = ycbcr[..., 2]

    Y_denoised = denoise_bilateral_wrapper(Y, sigma_color=lum_strength)
    Cb_denoised = gaussian_filter(Cb, sigma=chroma_strength * 150)
    Cr_denoised = gaussian_filter(Cr, sigma=chroma_strength * 150)

    ycbcr_denoised = np.stack([Y_denoised, Cb_denoised, Cr_denoised], axis=-1)
    result = ycbcr2rgb(ycbcr_denoised)
    return np.clip(result, 0, 1)


def validate_external_denoise(original, denoised, threshold=0.05):
    """
    验证外部 AI 降噪结果的质量，检测色彩偏移和过度涂抹。

    原理：AI 降噪（如 NoiseXTerminator）在统计意义上估计真实信号，
    但可能引入色彩偏移或过度平滑。本函数通过以下方式检测：
    1. 背景区域 RGB 均值偏移：检测色彩偏移
    2. 暗区高频能量比：检测过度涂抹（高频能量骤降 = 细节丢失）

    original: 原始图像 (float32, [0,1])
    denoised: AI 降噪后图像 (float32, [0,1])
    threshold: 背景区域允许的最大通道偏移 (默认 5%)

    返回: dict 验证报告
    """
    original = np.asarray(original, dtype=np.float32)
    denoised = np.asarray(denoised, dtype=np.float32)

    report = {'passed': True, 'warnings': []}

    # 1. 背景区域色彩偏移检测
    if original.ndim == 3 and denoised.ndim == 3:
        gray = 0.299 * original[..., 0] + 0.587 * original[..., 1] + 0.114 * original[..., 2]
        bg_thresh = np.percentile(gray, 20)
        bg_mask = gray < bg_thresh

        if np.sum(bg_mask) > 100:
            for ch, name in enumerate(['R', 'G', 'B']):
                orig_mean = float(np.mean(original[..., ch][bg_mask]))
                den_mean = float(np.mean(denoised[..., ch][bg_mask]))
                if orig_mean > 1e-6:
                    shift = abs(den_mean - orig_mean) / orig_mean
                    if shift > threshold:
                        report['passed'] = False
                        report['warnings'].append(
                            f'{name} 通道背景偏移 {shift:.1%} (>{threshold:.0%})'
                        )

    # 2. 高频能量比（检测过度涂抹）
    from scipy.ndimage import gaussian_filter as gf
    if original.ndim == 3:
        orig_gray = 0.299 * original[..., 0] + 0.587 * original[..., 1] + 0.114 * original[..., 2]
        den_gray = 0.299 * denoised[..., 0] + 0.587 * denoised[..., 1] + 0.114 * denoised[..., 2]
    else:
        orig_gray = original
        den_gray = denoised

    orig_hf = orig_gray - gf(orig_gray, sigma=3)
    den_hf = den_gray - gf(den_gray, sigma=3)
    orig_energy = float(np.mean(orig_hf ** 2))
    den_energy = float(np.mean(den_hf ** 2))

    if orig_energy > 1e-10:
        energy_ratio = den_energy / orig_energy
        report['high_frequency_energy_ratio'] = round(energy_ratio, 3)
        if energy_ratio < 0.5:
            report['passed'] = False
            report['warnings'].append(
                f'高频能量比 {energy_ratio:.2f} < 0.5 — AI 可能过度涂抹，细节丢失'
            )
    else:
        report['high_frequency_energy_ratio'] = None

    report['color_shift_threshold'] = threshold
    return report


def main():
    p = argparse.ArgumentParser(description='深空图像降噪')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='luminance_chroma',
                   choices=['bilateral', 'nonlocal', 'wavelet', 'luminance_chroma'],
                   help='降噪方法 (默认: luminance_chroma)')
    p.add_argument('--strength', type=float, default=0.05,
                   help='降噪强度 (默认: 0.05)')
    p.add_argument('--lum-strength', type=float, default=0.03,
                   help='亮度降噪强度 (默认: 0.03)')
    p.add_argument('--chroma-strength', type=float, default=0.10,
                   help='色彩降噪强度 (默认: 0.10)')
    p.add_argument('--light', action='store_true',
                   help='轻度降噪模式（强度减半）')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[降噪] 输入: {args.input}  形状: {img.shape}  方法: {args.method}")

    if args.light:
        args.strength /= 2
        args.lum_strength /= 2
        args.chroma_strength /= 2

    if args.method == 'bilateral':
        result = denoise_bilateral_wrapper(img, sigma_color=args.strength)
    elif args.method == 'nonlocal':
        result = denoise_nonlocal(img, h=args.strength)
    elif args.method == 'wavelet':
        result = denoise_wavelet_multiscale(img, threshold_factor=args.strength * 20)
    elif args.method == 'luminance_chroma':
        result = denoise_luminance_chroma(img, lum_strength=args.lum_strength,
                                          chroma_strength=args.chroma_strength)

    imsave(args.output, img_as_ubyte(result))
    print(f"[降噪] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
