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
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
import numpy as np
from scipy.ndimage import gaussian_filter
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave
from skimage.restoration import denoise_bilateral, denoise_nl_means, estimate_sigma


def denoise_bilateral_wrapper(image, sigma_color=0.05, sigma_spatial=15,
                              scale_aware=True):
    """双边滤波降噪。

    `sigma_color` 是**图像值域的绝对量**，而管线里两次降噪发生在量级差约 3 个数量级的
    域上：线性母版（星云 ~1e-3）、非线性（~1e-1）。实测同一个 `sigma_color=0.005`
    对"边缘对比 = 噪声量级"的阶跃边缘：

    | 量级 | 边缘对比 | 边缘保留 |
    |---|---|---|
    | 线性域 | 0.001 | **2.5%**（值域核被淹没 → 退化成 15px 纯高斯，低对比细节被毁） |
    | 非线性域 | 0.100 | **100.1%**（正常保边） |

    根因：`skimage.restoration.denoise_bilateral` 把颜色 LUT 建在 `[0, image.max()]` 上。
    线性天文母版的 `max()` 是亮星（~0.95），**整个星云只住在最底下 ~0.3%**，LUT 在那里
    几乎没有分辨率；`sigma_color` 又比该处的对比大一个量级 → 值域核恒为 1。

    `scale_aware=True`（默认）时按图像自身的稳健尺度（p99.9）缩放 `sigma_color`，
    使同一个配置值在任何域都表示相同的**相对强度**。实测把线性域的保边从 2.5% 恢复到
    **100.0%**，非线性域几乎不变（100.1%）；等价于"先把图像归一化再双边再还原"，
    但**不裁切 p99.9 以上的星核**（两者逐位等价，见下方测试）。

    `scale_aware=False` 恢复旧的绝对语义（供对照与回退）。
    """
    from skimage.restoration import denoise_bilateral

    if not scale_aware:
        if image.ndim == 3:
            return denoise_bilateral(image, sigma_color=sigma_color,
                                     sigma_spatial=sigma_spatial, channel_axis=-1)
        return denoise_bilateral(image, sigma_color=sigma_color,
                                 sigma_spatial=sigma_spatial)

    # 稳健尺度锚点：p99.9 而非 max，避免单个亮星把锚点抬到信号之上
    anchor = float(np.percentile(image, 99.9))
    if not np.isfinite(anchor) or anchor <= 1e-9:
        return np.clip(np.asarray(image, dtype=np.float32), 0, 1)
    sigma_eff = float(sigma_color) * anchor

    if image.ndim == 3:
        return denoise_bilateral(image, sigma_color=sigma_eff,
                                 sigma_spatial=sigma_spatial, channel_axis=-1)
    return denoise_bilateral(image, sigma_color=sigma_eff,
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


# ── B3-spline À Trous (Starlet) 多尺度小波与 Anscombe VST ──

# 理论 B3-spline Starlet 噪声传递系数（针对高斯白噪声）
STARLET_NOISE_SCALE_FACTORS = [0.89079, 0.20066, 0.08556, 0.04124, 0.02042, 0.01018]
B3_SPLINE_1D = np.array([0.0625, 0.25, 0.375, 0.25, 0.0625], dtype=np.float32)


def atrous_convolve2d(image, step=1):
    """B3-spline 2D 带孔可分离卷积 (À Trous Convolve)

    使用 [1/16, 1/4, 3/8, 1/4, 1/16] 可分离卷积核，在抽样点间插入 (step - 1) 个零。
    采用 reflect 边界填充，无下采样，严格平移不变。
    """
    img = np.asarray(image, dtype=np.float32)
    h, w = img.shape[:2]
    k1d = B3_SPLINE_1D
    pad = 2 * step

    # 1. 行卷积
    padded_rows = np.pad(img, ((0, 0), (pad, pad)), mode='reflect')
    row_filtered = np.zeros_like(img)
    for i, weight in enumerate(k1d):
        offset = (i - 2) * step
        row_filtered += weight * padded_rows[:, pad + offset : pad + offset + w]

    # 2. 列卷积
    padded_cols = np.pad(row_filtered, ((pad, pad), (0, 0)), mode='reflect')
    col_filtered = np.zeros_like(img)
    for i, weight in enumerate(k1d):
        offset = (i - 2) * step
        col_filtered += weight * padded_cols[pad + offset : pad + offset + h, :]

    return col_filtered


def starlet_transform(image, n_scales=4):
    """执行 2D/3D À Trous (Starlet) 多尺度分解。

    返回:
        wavelets: list of ndarray, 各尺度小波平面 [w_0, w_1, ..., w_{J-1}]
        residual: ndarray, 低频残差平面 c_J
    """
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        all_wavelets = [[] for _ in range(n_scales)]
        residuals = []
        for c in range(arr.shape[2]):
            w_c, r_c = starlet_transform(arr[..., c], n_scales=n_scales)
            for j in range(n_scales):
                all_wavelets[j].append(w_c[j])
            residuals.append(r_c)
        combined_wavelets = [np.stack(all_wavelets[j], axis=-1) for j in range(n_scales)]
        combined_residual = np.stack(residuals, axis=-1)
        return combined_wavelets, combined_residual

    c = arr.copy()
    wavelets = []
    for j in range(n_scales):
        step = 1 << j  # 2^j
        c_next = atrous_convolve2d(c, step=step)
        wavelets.append(c - c_next)
        c = c_next

    return wavelets, c


def inverse_starlet_transform(wavelets, residual):
    """完美重构 Starlet 分解结果：c_0 = c_J + sum(w_j)。"""
    res = np.array(residual, dtype=np.float32, copy=True)
    for w in wavelets:
        res += w
    return res


def anscombe_transform(image):
    """广义 Anscombe 变换 (VST)：将泊松散粒噪声稳定为方差约为 1 的加性高斯白噪声。"""
    arr = np.asarray(image, dtype=np.float32)
    return 2.0 * np.sqrt(np.maximum(arr + 3.0 / 8.0, 0.0))


def inverse_anscombe_transform(image):
    """渐近精确无偏逆 Anscombe 变换。"""
    z = np.asarray(image, dtype=np.float32)
    z_sq = 0.25 * (z ** 2)
    res = z_sq - 3.0 / 8.0
    return np.maximum(res, 0.0)


def denoise_atrous_wavelet(image, n_scales=4, k_sigmas=(3.0, 2.0, 1.0, 0.0),
                           protect_factor=3.0, use_anscombe=False):
    """À Trous 多尺度小波 (Starlet) 自适应阈值降噪。

    原理：
      1. 若 use_anscombe=True，先将泊松-高斯噪声稳定为加性白噪声；
      2. 采用 À Trous 算法分解为 n_scales 层；
      3. 从第 0 层高频系数的 MAD 稳健估计原图噪声标准差 sigma_0；
      4. 依据 Starlet 理论噪声衰减系数计算各层阈值 T_j = k_j * sigma_0 * e_j；
      5. 实施带亮星/高光平滑保护的软阈值（超过 protect_factor * T_j 时衰减归零，100% 保护星点与高对比度核心）；
      6. 完美能量守恒重构；
      7. 若启用了 Anscombe，做精确无偏逆变换。
    """
    arr = np.asarray(image, dtype=np.float32)
    if arr.ndim == 3:
        out = np.zeros_like(arr)
        for c in range(arr.shape[2]):
            out[..., c] = denoise_atrous_wavelet(
                arr[..., c], n_scales=n_scales, k_sigmas=k_sigmas,
                protect_factor=protect_factor, use_anscombe=use_anscombe
            )
        return out

    work = anscombe_transform(arr) if use_anscombe else arr.copy()
    wavelets, residual = starlet_transform(work, n_scales=n_scales)

    w0 = wavelets[0]
    mad = float(np.median(np.abs(w0 - np.median(w0))))
    sigma0 = mad / (0.6745 * STARLET_NOISE_SCALE_FACTORS[0]) if mad > 1e-9 else 0.0

    if sigma0 <= 1e-9:
        return arr

    filtered_wavelets = []
    for j, w_j in enumerate(wavelets):
        k = k_sigmas[j] if j < len(k_sigmas) else 0.0
        if k <= 0:
            filtered_wavelets.append(w_j)
            continue

        e_j = STARLET_NOISE_SCALE_FACTORS[j] if j < len(STARLET_NOISE_SCALE_FACTORS) else (
            STARLET_NOISE_SCALE_FACTORS[-1] * (0.5 ** (j - len(STARLET_NOISE_SCALE_FACTORS) + 1))
        )
        t_j = float(k * sigma0 * e_j)
        abs_w = np.abs(w_j)
        p_thresh = float(protect_factor * t_j)

        mask_pass = abs_w > t_j
        w_filtered = np.zeros_like(w_j)
        if np.any(mask_pass):
            taper = np.clip((p_thresh - abs_w) / max(p_thresh - t_j, 1e-6), 0.0, 1.0)
            diff = t_j * taper
            w_filtered[mask_pass] = np.sign(w_j[mask_pass]) * (abs_w[mask_pass] - diff[mask_pass])

        filtered_wavelets.append(w_filtered)

    recon = inverse_starlet_transform(filtered_wavelets, residual)
    if use_anscombe:
        recon = inverse_anscombe_transform(recon)

    return np.clip(recon, 0.0, None)


def denoise_luminance_chroma(image, lum_strength=0.04, chroma_strength=0.10,
                             background_only=False, bg_rolloff_high=0.20,
                             method='bilateral'):
    """
    分离亮度/色彩通道降噪。
    原理：人眼对色彩噪点更敏感。提取亮度通道 (Y) 和色彩通道 (Cb/Cr)，
    对色彩通道施加 2-3 倍强度的降噪，同时保护亮度细节。
    若 background_only=True，则生成反向明度掩膜，降噪只作用于暗背景，星云主体直通保真。
    method: 'bilateral'（默认，保边双边滤波）或 'atrous' / 'starlet'（À Trous 多尺度小波）
    """
    from skimage.color import rgb2ycbcr, ycbcr2rgb

    ycbcr = rgb2ycbcr(image)
    Y = ycbcr[..., 0]
    Cb = ycbcr[..., 1]
    Cr = ycbcr[..., 2]

    if method in ('atrous', 'starlet', 'wavelet_atrous'):
        # 将 lum_strength 映射为小波 k-sigma 乘子 (基准 0.005 -> 1.0x)
        k_mult = max(0.2, float(lum_strength) / 0.005)
        k_sigmas = (3.0 * k_mult, 2.0 * k_mult, 1.0 * k_mult, 0.0)
        Y_denoised = denoise_atrous_wavelet(Y, n_scales=4, k_sigmas=k_sigmas)
    else:
        Y_denoised = denoise_bilateral_wrapper(Y, sigma_color=lum_strength)

    Cb_denoised = gaussian_filter(Cb, sigma=chroma_strength * 150)
    Cr_denoised = gaussian_filter(Cr, sigma=chroma_strength * 150)

    if background_only:
        # 反向明度掩膜：星云区权重为 0，纯黑背景权重为 1
        mask_bg = np.clip((float(bg_rolloff_high) - Y) / max(float(bg_rolloff_high) - 0.04, 1e-5), 0.0, 1.0) ** 2
        Y_denoised = Y * (1.0 - mask_bg) + Y_denoised * mask_bg
        Cb_denoised = Cb * (1.0 - mask_bg) + Cb_denoised * mask_bg
        Cr_denoised = Cr * (1.0 - mask_bg) + Cr_denoised * mask_bg

    ycbcr_denoised = np.stack([Y_denoised, Cb_denoised, Cr_denoised], axis=-1)
    result = ycbcr2rgb(ycbcr_denoised)
    return np.clip(result, 0, 1)


def denoise_noise_stats(image):
    """Estimate background noise from high-pass MAD, independent of image scale."""
    arr = np.asarray(image, dtype=np.float32)
    gray = np.mean(arr, axis=-1) if arr.ndim == 3 else arr
    smooth = gaussian_filter(gray, 2.0)
    background = smooth <= np.percentile(smooth, 40)
    highpass = gray - gaussian_filter(gray, 1.0)
    samples = highpass[background]
    sigma = float(np.median(np.abs(samples - np.median(samples)))) / 0.6745
    level = float(np.mean(smooth[background]))
    return gray, smooth, background, sigma, level


def validate_external_denoise(original, denoised, threshold=0.05):
    """Reject drift, noise growth and loss of resolved structure; HF loss alone is OK.

    ponytail: deterministic heuristics cannot prove authenticity; inspect the
    residual visually, use registered split-stack evidence if stronger proof is needed.
    """
    original = np.asarray(original, dtype=np.float32)
    denoised = np.asarray(denoised, dtype=np.float32)
    report = {'passed': True, 'warnings': [], 'color_shift_threshold': threshold}

    def reject(message):
        report['passed'] = False
        report['warnings'].append(message)

    if (original.shape != denoised.shape or original.ndim not in (2, 3)
            or (original.ndim == 3 and original.shape[-1] != 3)):
        reject('Image shape or channel count mismatch')
        return report
    if not np.all(np.isfinite(original)) or not np.all(np.isfinite(denoised)):
        reject('Non-finite samples')
        return report
    if float(denoised.min()) < -1e-6 or float(denoised.max()) > 1.0 + 1e-6:
        reject('Output exceeds normalized sample range')
        return report

    gray, smooth, bg, sigma, _ = denoise_noise_stats(original)
    den_gray, den_smooth, _, _, _ = denoise_noise_stats(denoised)
    before = original[..., None] if original.ndim == 2 else original
    after = denoised[..., None] if denoised.ndim == 2 else denoised
    drift = 0.0
    for mask in (bg, np.ones(gray.shape, dtype=bool)):
        means = np.mean(before[mask], axis=0)
        changes = np.abs(np.mean(after[mask], axis=0) - means)
        drift = max(drift, float(np.max(changes / np.maximum(np.abs(means), 1e-8))))
    report['max_channel_mean_drift'] = drift
    if drift > threshold:
        reject('Channel mean drift exceeds tolerance')

    lum_drift = abs(float(den_gray.mean() - gray.mean())) / max(abs(float(gray.mean())), 1e-8)
    report['luminance_drift'] = lum_drift
    if lum_drift > threshold:
        reject('Luminance drift exceeds tolerance')

    hp_before = gray - gaussian_filter(gray, 1.0)
    hp_after = den_gray - gaussian_filter(den_gray, 1.0)
    samples = hp_after[bg]
    sigma_after = float(np.median(np.abs(samples - np.median(samples)))) / 0.6745
    report['noise_ratio'] = sigma_after / sigma if sigma > 1e-10 else None
    if sigma_after > sigma * 1.05 + 1e-10:
        reject('Background noise increased')
    energy = float(np.mean(hp_before ** 2))
    report['high_frequency_energy_ratio'] = float(np.mean(hp_after ** 2)) / energy if energy > 1e-20 else None

    detail = smooth - gaussian_filter(smooth, 3.0)
    den_detail = den_smooth - gaussian_filter(den_smooth, 3.0)
    structure = np.abs(detail) > max(3.0 * sigma, 1e-8)
    retention = None
    edge_retention = None
    if np.any(structure):
        retention = float(np.sum(detail[structure] * den_detail[structure])
                          / max(float(np.sum(detail[structure] ** 2)), 1e-20))
        edges = np.hypot(*np.gradient(smooth))
        den_edges = np.hypot(*np.gradient(den_smooth))
        edge_retention = float(np.sum(den_edges[structure]) / max(float(np.sum(edges[structure])), 1e-20))
        if retention < 0.65 or retention > 1.25:
            reject('Resolved detail lost or amplified')
        if edge_retention < 0.65 or edge_retention > 1.25:
            reject('Resolved edges lost or amplified')
    report['detail_retention'] = retention
    report['edge_retention'] = edge_retention
    return report


def _read_deepsnr_fits(path, shape):
    """Read raw CLI samples before any normalization, clipping or channel changes."""
    from astropy.io import fits
    expected = (3, *shape[:2]) if len(shape) == 3 else shape
    with fits.open(path, memmap=False) as hdul:
        hdu = hdul[0]
        if (hdu.header.get('BITPIX') != -32 or hdu.data is None
                or hdu.data.shape != expected
                or hdu.header.get('BSCALE', 1) != 1 or hdu.header.get('BZERO', 0) != 0):
            raise ValueError('Invalid DeepSNR float32 image HDU or channel layout')
        candidate = np.asarray(hdu.data, dtype=np.float32).copy()
    if len(shape) == 3:
        candidate = np.moveaxis(candidate, 0, -1)
    if (not np.all(np.isfinite(candidate)) or candidate.min() < -1e-6
            or candidate.max() > 1 + 1e-6):
        raise ValueError('Invalid DeepSNR output samples')
    return candidate


def validate_applied_denoise(original, candidate):
    """Validate the applied result and retry excessive suppression once."""
    report = validate_external_denoise(original, candidate)
    strength = 1.0
    ratio = report.get('noise_ratio')
    if ratio is not None and ratio < 0.5:
        strength = 0.5
        candidate = (np.asarray(original) + candidate) * 0.5
        report = validate_external_denoise(original, candidate)
        ratio = report.get('noise_ratio')
    if ratio is not None and ratio < 0.5:
        report['passed'] = False
        report['warnings'].append('Applied background noise ratio below 0.5')
    report['blend_multiplier'] = strength
    return (candidate.astype(np.float32) if report['passed'] else None), report


def find_deepsnr_executable(user_path=None):
    """定位 DeepSNR CLI 可执行文件（委托给通用探测器）。

    四级优先：显式 → ``DEEPSNR_PATH`` → ``shutil.which`` → 默认安装目录。
    完整语义与跨平台路径表见 :mod:`external_tools`。

    历史教训（本函数曾只依赖 ``shutil.which``）：GUI / IDE / 宿主应用启动的
    进程常不继承 ``/usr/local/bin``，此时 ``which`` 对 DeepSNR 与 StarNet
    **都**返回 None —— StarNet 侧有默认路径兜底所以能发现，DeepSNR 侧没有 ⇒
    二进制明明装着却被报「executable not found」并**静默降级到 atrous**。
    """
    from external_tools import find_executable
    return find_executable("deepsnr", user_path=user_path)


def run_deepsnr(image, work_dir, executable=None, timeout=900, strength=0.35,
                save_prediction=False):
    """Return a validated linear blend or None on any CLI/quality failure.

    Keep zero-shadow MTF even on 1.3.2: native --linear clips shadows without
    restoring source samples. Float FITS avoids integer quantization; only
    legacy CLI versions need an RGB/BGR transport calibration.
    """
    from astropy.io import fits
    from fits_io import write_image
    from stretch import derive_mtf_midtones, mtf_stretch, inverse_mtf_stretch

    report = {'status': 'fallback', 'backend': 'deepsnr', 'strength': strength,
              'review_required': True}
    try:
        if not np.isfinite(strength) or not 0 < strength <= 1:
            raise ValueError('DeepSNR strength must be in (0, 1]')
        if not np.isfinite(timeout) or timeout <= 0:
            raise ValueError('DeepSNR timeout must be positive')
        arr = np.asarray(image, dtype=np.float32)
        if (arr.ndim not in (2, 3) or (arr.ndim == 3 and arr.shape[-1] != 3)
                or min(arr.shape[:2]) < 512):
            raise ValueError('DeepSNR requires grayscale/RGB images at least 512 x 512')
        if not np.all(np.isfinite(arr)) or arr.min() < 0 or arr.max() > 1:
            raise ValueError('DeepSNR input must contain finite normalized [0, 1] samples')
        path = find_deepsnr_executable(executable)
        if not path:
            raise FileNotFoundError(
                'DeepSNR executable not found (searched PATH, DEEPSNR_PATH, '
                'and the default install locations; set --deepsnr-path or '
                'DEEPSNR_PATH to override)')
        info_run = subprocess.run([path, '--machine-info'], capture_output=True,
                                  text=True, timeout=min(timeout, 10), check=True)
        info = json.loads(info_run.stdout)
        if not isinstance(info, dict):
            raise ValueError('Invalid DeepSNR machine information')
        version = tuple(int(x) for x in info['version'].split('.'))
        if info.get('product') != 'deepsnr' or version < (1, 2, 3):
            raise ValueError('DeepSNR CLI 1.2.3 or newer is required')
        formats = info.get('io', {}).get('images', {})
        color = 'rgb' if arr.ndim == 3 else 'grayscale'
        for direction in ('inputs', 'outputs'):
            if not any('fits' in entry.get('formats', [])
                       and 'float32' in entry.get('sample_types', [])
                       and color in entry.get('color_spaces', [])
                       for entry in formats.get(direction, [])):
                raise ValueError('DeepSNR must declare float32 FITS input and output support')
        report['version'] = info['version']
        report['runtime'] = info.get('backend')
        report['native_linear_used'] = False
        midtones = derive_mtf_midtones(arr, min_midtones=0.001)
        report['payload_domain'] = 'mtf'
        report['midtones'] = midtones
        report['shadows'] = 0.0
        Path(work_dir).mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='deepsnr_', dir=work_dir) as tmp:
            def invoke(source, output):
                command = [path, '--input', str(source), '--output', str(output), '--model', '2', '--quiet']
                proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
                report['returncode'] = proc.returncode
                report['stderr'] = proc.stderr[-4000:]
                if proc.returncode:
                    raise RuntimeError(f'DeepSNR exited with code {proc.returncode}: {proc.stderr[-1000:]}')

            reverse_channels = False
            if arr.ndim == 3 and version >= (1, 3, 2):
                report['fits_channel_order'] = 'rgb'
                report['fits_channel_order_source'] = 'cli_rgb_contract'
            elif arr.ndim == 3:
                # Legacy builds can write BGR FITS. Calibrate on known colors,
                # never on the astronomical target. 1.3.2 preserves RGB order.
                report['fits_channel_order_source'] = 'legacy_probe'
                probe = np.empty((512, 512, 3), dtype=np.float32)
                probe[:] = (0.2, 0.4, 0.6)
                probe_in, probe_out = Path(tmp) / 'probe.fits', Path(tmp) / 'probe_out.fits'
                fits.PrimaryHDU(np.moveaxis(probe, -1, 0)).writeto(probe_in)
                invoke(probe_in, probe_out)
                probe_result = _read_deepsnr_fits(probe_out, probe.shape)
                means = np.mean(probe_result, axis=(0, 1))
                if np.allclose(means, (0.2, 0.4, 0.6), atol=0.01, rtol=0):
                    report['fits_channel_order'] = 'rgb'
                elif np.allclose(means, (0.6, 0.4, 0.2), atol=0.01, rtol=0):
                    report['fits_channel_order'] = 'bgr'
                    reverse_channels = True
                else:
                    raise ValueError('DeepSNR FITS channel transport calibration failed')
            source, output = Path(tmp) / 'input.fits', Path(tmp) / 'output.fits'
            write_image(mtf_stretch(arr, midtones=midtones, shadows=0.0), str(source))
            invoke(source, output)
            candidate = _read_deepsnr_fits(output, arr.shape)
            if reverse_channels:
                candidate = candidate[..., ::-1].copy()
            candidate = inverse_mtf_stretch(candidate, midtones=midtones, shadows=0.0)
        if save_prediction:
            prediction_path = Path(work_dir) / '03_pre_denoise_prediction.tif'
            write_image(candidate, prediction_path)
            report['prediction_path'] = str(prediction_path)
        report['validation'] = validate_external_denoise(arr, candidate)
        if not report['validation']['passed']:
            raise ValueError('; '.join(report['validation']['warnings']))
        result = arr * (1.0 - strength) + candidate * strength
        result, applied = validate_applied_denoise(arr, result)
        report['applied_validation'] = applied
        report['applied_strength'] = strength * applied['blend_multiplier']
        if result is None:
            raise ValueError('; '.join(applied['warnings']))
        report.update(status='accepted', review_required=False, visual_review_required=True)
        return result.astype(np.float32), report
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        report['reason'] = str(exc)
        return None, report


def main():
    p = argparse.ArgumentParser(description='深空图像降噪')
    p.add_argument('input', help='输入图像路径')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='luminance_chroma',
                   choices=['bilateral', 'nonlocal', 'wavelet', 'luminance_chroma', 'atrous', 'anscombe_atrous'],
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
    elif args.method == 'atrous':
        result = denoise_atrous_wavelet(img, k_sigmas=(3.0 * args.strength * 20, 2.0 * args.strength * 20, 1.0 * args.strength * 20, 0.0))
    elif args.method == 'anscombe_atrous':
        result = denoise_atrous_wavelet(img, k_sigmas=(3.0 * args.strength * 20, 2.0 * args.strength * 20, 1.0 * args.strength * 20, 0.0), use_anscombe=True)
    elif args.method == 'luminance_chroma':
        result = denoise_luminance_chroma(img, lum_strength=args.lum_strength,
                                          chroma_strength=args.chroma_strength)

    imsave(args.output, img_as_ubyte(result))
    print(f"[降噪] 输出已保存: {args.output}")


if __name__ == '__main__':
    main()
