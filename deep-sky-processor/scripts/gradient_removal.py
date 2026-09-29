#!/usr/bin/env python3
"""
Deep-Sky Background Extraction & Gradient Removal (DBE/ABE-like)

原理：
  光害、月光、气辉会在深空图像上产生不均匀的亮度梯度。
  本模块通过在图像背景区域采样，拟合出一个平滑的背景渐变模型，
  然后将其从原图中减去，得到背景均匀的干净图像。

方法：
  1. 迭代阈值分割，将天体（恒星+星云）与背景分离
  2. 对背景像素进行多项式曲面拟合或大尺度中值滤波
  3. 从原图减去背景模型

用法:
  python gradient_removal.py <input> <output> [options]
"""

import argparse
import sys
import numpy as np
from scipy.ndimage import median_filter, gaussian_filter
from scipy.interpolate import griddata
from skimage import img_as_float32, img_as_ubyte
from skimage.io import imread, imsave


def estimate_background_median(image, filter_size=101, iterations=3):
    """
    使用大尺度中值滤波估计背景。
    原理：中值滤波器移除小尺度天体（恒星），保留大尺度的背景渐变。
    迭代多次以逐步精化。
    """
    bg = image.copy()
    for i in range(iterations):
        bg = median_filter(bg, size=filter_size)
    return bg


def estimate_background_polynomial(image, degree=2, sample_spacing=50, exclusion_mask=None):
    """
    使用多项式曲面拟合估计背景。
    原理：在图像上等间距采样背景点，用低阶多项式拟合出平滑背景面。
    这样可以精确建模光害梯度和渐晕。
    """
    h, w = image.shape
    yy, xx = np.mgrid[0:h, 0:w]

    sample_y = np.arange(sample_spacing // 2, h, sample_spacing)
    sample_x = np.arange(sample_spacing // 2, w, sample_spacing)
    sy, sx = np.meshgrid(sample_y, sample_x, indexing='ij')
    samples = image[sample_y[:, None], sample_x[None, :]]

    # 过滤掉包含天体的高亮度采样点
    bg_median = np.median(samples)
    bg_std = np.std(samples)
    mask = (samples < bg_median + 2 * bg_std) & (samples > bg_median - 2 * bg_std)
    if exclusion_mask is not None:
        ex_arr = np.asarray(exclusion_mask > 0, dtype=bool)
        if ex_arr.shape == image.shape:
            ex_samples = ex_arr[sample_y[:, None], sample_x[None, :]]
            mask = mask & (~ex_samples)

    valid_y = sy[mask].ravel()
    valid_x = sx[mask].ravel()
    valid_z = samples[mask].ravel()

    if len(valid_z) < 10:
        print("[WARN] Too few valid background samples, falling back to median filter")
        return estimate_background_median(image, filter_size=101)

    # 如果存在排除蒙版且 degree >= 2，检查中心区域是否有足够背景样本支撑曲率拟合
    if degree >= 2 and exclusion_mask is not None:
        center_samples = np.sum((valid_y >= 0.25 * h) & (valid_y <= 0.75 * h) &
                                (valid_x >= 0.25 * w) & (valid_x <= 0.75 * w))
        if center_samples < 8:
            print(f"[DBE] 中心背景样本仅 {center_samples} 个，不足以约束 2 阶曲面，自动降阶至 degree=1 线性梯度拟合")
            degree = 1

    A = np.column_stack([valid_x**i * valid_y**j
                         for i in range(degree + 1)
                         for j in range(degree + 1 - i)])
    coeffs, _, _, _ = np.linalg.lstsq(A, valid_z, rcond=None)

    bg = np.zeros_like(image, dtype=np.float64)
    k = 0
    for i in range(degree + 1):
        for j in range(degree + 1 - i):
            bg += coeffs[k] * (xx**i * yy**j)
            k += 1

    return bg.astype(np.float32)


def estimate_background_rbf(image, num_samples=300, exclusion_mask=None):
    """
    使用径向基函数(RBF)插值估计背景。
    原理：在图像上随机采样背景点，使用RBF插值生成平滑背景面。
    这是最接近PixInsight DBE的方法。

    极暗数据优化：
      - 增加采样点数量 (200→300)
      - 降低标准差阈值 (1.5σ→1.0σ)，避免零值主导导致样本耗尽
      - 回退时 polynomial 自动升阶至 3
    """
    from scipy.interpolate import RBFInterpolator

    h, w = image.shape
    yy, xx = np.mgrid[0:h, 0:w]

    rng = np.random.RandomState(42)
    sample_y = rng.randint(0, h, num_samples * 3)
    sample_x = rng.randint(0, w, num_samples * 3)
    sample_values = image[sample_y, sample_x]

    bg_median = np.median(sample_values)
    bg_std = np.std(sample_values)

    # 极暗数据检测：若 median < 0.001 或 std 极小，使用更宽松的阈值
    is_very_dark = bg_median < 0.001 or bg_std < 1e-6
    sigma_factor = 1.0 if is_very_dark else 1.5

    mask = (sample_values < bg_median + sigma_factor * bg_std) & \
           (sample_values > bg_median - sigma_factor * bg_std)
    if exclusion_mask is not None:
        ex_arr = np.asarray(exclusion_mask > 0, dtype=bool)
        if ex_arr.shape == image.shape:
            ex_hit = ex_arr[sample_y, sample_x]
            mask = mask & (~ex_hit)

    valid_y = sample_y[mask]
    valid_x = sample_x[mask]
    valid_z = sample_values[mask]

    if len(valid_z) < 20:
        print(f"[WARN] RBF 样本不足 ({len(valid_z)}<20)，回退至 polynomial deg=3")
        return estimate_background_polynomial(image, degree=3, exclusion_mask=exclusion_mask)

    indices = rng.choice(len(valid_z), min(num_samples, len(valid_z)), replace=False)
    pts = np.column_stack([valid_x[indices].astype(float), valid_y[indices].astype(float)])
    vals = valid_z[indices]

    rbf = RBFInterpolator(pts, vals, kernel='thin_plate_spline', smoothing=0.1)
    all_pts = np.column_stack([xx.ravel().astype(float), yy.ravel().astype(float)])
    bg = rbf(all_pts).reshape(h, w)

    return bg.astype(np.float32)


def _estimate_bg_plane(plane, method='polynomial', degree=2, filter_size=101, exclusion_mask=None):
    """对单个通道估计背景面（未平滑）。"""
    if method == 'median':
        return estimate_background_median(plane, filter_size=filter_size)
    if method == 'rbf':
        return estimate_background_rbf(plane, exclusion_mask=exclusion_mask)
    return estimate_background_polynomial(plane, degree=degree, exclusion_mask=exclusion_mask)


def estimate_background_noise(image):
    """稳健的背景噪声尺度（MAD × 1.4826）。

    排除恰好为 0 的像素：DBE 之后大片背景会被 clip 到 0，
    直接取 MAD 会退化成 0，使任何"相对噪声"的判据失效。
    """
    values = np.asarray(image, dtype=np.float64)
    if values.ndim == 3:
        values = values.mean(axis=2)
    positive = values[values > 0]
    if positive.size < 100:
        return 0.0
    lower = positive[positive <= float(np.percentile(positive, 50))]
    if lower.size < 100:
        return 0.0
    median = float(np.median(lower))
    return float(np.median(np.abs(lower - median))) * 1.4826


def normalize_background_subtracted(image, pctl_high=99.7, pedestal_sigmas=3.0):
    """把 DBE 输出归一化到 [0,1]。

    黑点取**背景的稳健中心**：逐通道 DBE 之后背景以 0 为中心、噪声对称分布。
    对于多通道图像，分别计算各通道的背景中值并分别对齐至 0，避免单标量导致通道间底电平微弱失调。
    白点**锚定真实峰值**，最后加上 pedestal（默认 3σ）。
    """
    values = np.asarray(image, dtype=np.float64)
    if values.ndim == 3 and values.shape[2] >= 3:
        black = np.median(values[..., :3], axis=(0, 1))
        peak = float(np.max(values))
        white = max(float(np.percentile(values, pctl_high)), peak)
        span = white - float(np.min(black))
        if span <= 1e-8:
            if peak <= 0:
                return np.zeros_like(values, dtype=np.float32)
            return (values / peak).astype(np.float32)

        normalized = values.copy()
        normalized[..., :3] = (normalized[..., :3] - black) / span
        noise = estimate_background_noise(normalized)
        pedestal = min(0.05, max(pedestal_sigmas * noise, 0.002))
        return np.clip(normalized + pedestal, 0, 1).astype(np.float32)

    black = float(np.median(values))
    peak = float(np.max(values))
    white = max(float(np.percentile(values, pctl_high)), peak)
    span = white - black
    if span <= 1e-8:
        if peak <= 0:
            return np.zeros_like(values, dtype=np.float32)
        return (values / peak).astype(np.float32)

    normalized = (values - black) / span
    noise = estimate_background_noise(normalized)
    pedestal = min(0.05, max(pedestal_sigmas * noise, 0.002))
    return np.clip(normalized + pedestal, 0, 1).astype(np.float32)


def remove_gradient(image, method='polynomial', degree=2, filter_size=101, exclusion_mask=None):
    """
    从图像中去除背景梯度。
    返回：(校正后图像, 背景模型)

    多通道输入时**对每个通道独立估计并减去各自的背景面**。
    exclusion_mask: 可选目标排除蒙版（True/1 为天体主体区），采样点将避开此区域，保护弱信号外盘。
    """
    if image.ndim == 2:
        bg_model = gaussian_filter(
            _estimate_bg_plane(image, method, degree, filter_size, exclusion_mask=exclusion_mask), sigma=10
        )
        corrected = image.astype(np.float64) - bg_model.astype(np.float64)
        return corrected, bg_model.astype(np.float32)

    n_channels = image.shape[2]
    corrected = image.astype(np.float64).copy()
    bg_channels = []
    for c in range(n_channels):
        plane = image[..., c]
        bg_model = gaussian_filter(
            _estimate_bg_plane(plane, method, degree, filter_size, exclusion_mask=exclusion_mask), sigma=10
        )
        bg_channels.append(bg_model)
        corrected[..., c] = plane.astype(np.float64) - bg_model.astype(np.float64)

    bg_stack = np.stack(bg_channels, axis=-1).astype(np.float32)
    return corrected, bg_stack


def main():
    p = argparse.ArgumentParser(description='Deep-sky background extraction & gradient removal')
    p.add_argument('input', help='输入图像路径 (PNG/JPG)')
    p.add_argument('output', help='输出图像路径')
    p.add_argument('--method', default='polynomial', choices=['polynomial', 'median', 'rbf'],
                   help='背景估计方法 (默认: polynomial)')
    p.add_argument('--degree', type=int, default=2, help='多项式阶数 (默认: 2)')
    p.add_argument('--filter-size', type=int, default=101, help='中值滤波核大小 (默认: 101)')
    p.add_argument('--save-bg', default=None, help='保存背景模型图像到指定路径')
    args = p.parse_args()

    img = img_as_float32(imread(args.input))
    print(f"[梯度去除] 输入图像: {args.input}  形状: {img.shape}  方法: {args.method}")

    corrected, bg_model = remove_gradient(
        img, method=args.method, degree=args.degree, filter_size=args.filter_size
    )

    # 裁剪并归一化
    corrected = np.clip(corrected, 0, None)
    if corrected.max() > 0:
        corrected = corrected / corrected.max()
    result = img_as_ubyte(corrected)
    imsave(args.output, result)
    print(f"[梯度去除] 输出已保存: {args.output}")

    if args.save_bg:
        if bg_model.ndim == 3:
            bg_vis = bg_model / bg_model.max() if bg_model.max() > 0 else bg_model
        else:
            bg_vis = bg_model / bg_model.max() if bg_model.max() > 0 else bg_model
        imsave(args.save_bg, img_as_ubyte(bg_vis))
        print(f"[梯度去除] 背景模型已保存: {args.save_bg}")


if __name__ == '__main__':
    main()
