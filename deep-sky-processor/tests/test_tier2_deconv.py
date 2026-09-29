#!/usr/bin/env python3
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import numpy as np
import pytest
from deconvolution import make_gaussian_psf, richardson_lucy_linear


def test_make_gaussian_psf():
    psf = make_gaussian_psf(fwhm=3.0)
    assert psf.ndim == 2
    assert psf.shape[0] == psf.shape[1]
    assert psf.shape[0] % 2 == 1
    assert np.isclose(np.sum(psf), 1.0, atol=1e-5)
    cy, cx = psf.shape[0] // 2, psf.shape[1] // 2
    assert psf[cy, cx] == np.max(psf)


def test_richardson_lucy_linear_shapes_and_range():
    # 2D 灰度
    img2d = np.full((64, 64), 0.05, dtype=np.float32)
    img2d[32, 32] = 0.8
    res2d = richardson_lucy_linear(img2d, fwhm=3.0, iterations=5)
    assert res2d.shape == img2d.shape
    assert res2d.min() >= 0.0
    assert res2d.max() <= 1.0

    # 3D RGB
    img3d = np.full((64, 64, 3), 0.05, dtype=np.float32)
    img3d[32, 32, :] = [0.8, 0.7, 0.9]
    res3d = richardson_lucy_linear(img3d, fwhm=3.0, iterations=5)
    assert res3d.shape == img3d.shape
    assert res3d.min() >= 0.0
    assert res3d.max() <= 1.0


def test_richardson_lucy_linear_fwhm_reduction():
    # 构造点光源并用已知高斯 PSF 模糊
    from scipy.signal import fftconvolve
    h, w = 64, 64
    point = np.zeros((h, w), dtype=np.float32)
    point[32, 32] = 1.0
    psf = make_gaussian_psf(fwhm=4.0, size=15)
    blurred = fftconvolve(point, psf, mode="same")
    # 叠加上线性背景
    bg = 0.01
    linear_img = np.clip(blurred + bg, 0.0, 1.0)

    # 计算模糊图像的 FWHM 径向展宽（方差）
    def compute_radius_variance(img, cy=32, cx=32):
        y, x = np.ogrid[:h, :w]
        r2 = (y - cy) ** 2 + (x - cx) ** 2
        signal = np.maximum(img - bg, 0.0)
        return np.sum(signal * r2) / max(np.sum(signal), 1e-8)

    var_before = compute_radius_variance(linear_img)
    deconv = richardson_lucy_linear(linear_img, fwhm=4.0, iterations=10)
    var_after = compute_radius_variance(deconv)

    # 反卷积后点光源能量应更集中，方差减小，中心峰值抬升
    assert var_after < var_before
    assert deconv[32, 32] > linear_img[32, 32]
