#!/usr/bin/env python3
import numpy as np
import cv2

# sRGB(D65) ↔ CIE XYZ 变换矩阵，以及 Lab 的分段常数
_RGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
], dtype=np.float32)
_XYZ_TO_RGB = np.linalg.inv(_RGB_TO_XYZ.astype(np.float64)).astype(np.float32)
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883], dtype=np.float32)
_LAB_EPS = 216.0 / 24389.0
_LAB_KAPPA = 24389.0 / 27.0


def safe_rgb2lab(image):
    """
    sRGB [0,1] → CIE Lab（L ∈ [0,100]，a/b 为标准尺度）。

    纯 NumPy 实现，不走 OpenCV：cv2 的 RGB2Lab 对 float32 输入会把
    sRGB→线性 一步按 8 位量化，暗值直接归零（实测 RGB < 0.001 时 L = 0）。
    对线性深空数据（背景常落在 1e-3 量级）这会造成大面积精度丢失 ——
    实测一幅图的 Lab 往返会让 23.6% 的像素变成纯 0。
    """
    rgb = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    linear = np.where(rgb <= 0.04045, rgb / 12.92,
                      ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = (linear @ _RGB_TO_XYZ.T) / _WHITE_D65
    f = np.where(xyz > _LAB_EPS, np.cbrt(xyz),
                 (_LAB_KAPPA * xyz + 16.0) / 116.0)
    lab = np.empty(xyz.shape, dtype=np.float32)
    lab[..., 0] = 116.0 * f[..., 1] - 16.0
    lab[..., 1] = 500.0 * (f[..., 0] - f[..., 1])
    lab[..., 2] = 200.0 * (f[..., 1] - f[..., 2])
    return lab


def safe_lab2rgb(lab):
    """
    CIE Lab → sRGB [0,1]。与 safe_rgb2lab 互逆，暗值处同样保持精度。
    """
    lab_f = np.asarray(lab, dtype=np.float32).copy()
    lab_f[..., 0] = np.clip(lab_f[..., 0], 0.0, 100.0)
    lab_f[..., 1] = np.clip(lab_f[..., 1], -128.0, 127.0)
    lab_f[..., 2] = np.clip(lab_f[..., 2], -128.0, 127.0)

    fy = (lab_f[..., 0] + 16.0) / 116.0
    fx = fy + lab_f[..., 1] / 500.0
    fz = fy - lab_f[..., 2] / 200.0
    f = np.stack([fx, fy, fz], axis=-1)
    f_cubed = f ** 3
    xyz = np.where(f_cubed > _LAB_EPS, f_cubed,
                   (116.0 * f - 16.0) / _LAB_KAPPA) * _WHITE_D65
    linear = xyz @ _XYZ_TO_RGB.T
    rgb = np.where(linear <= 0.0031308, linear * 12.92,
                   1.055 * np.power(np.maximum(linear, 0.0), 1.0 / 2.4) - 0.055)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)

def safe_rgb2hsv(image):
    """
    NumPy 2.0-safe and OpenCV-backed alternative to skimage.color.rgb2hsv.
    Input image should be float32 in range [0, 1] RGB.
    Returns HSV image in float32 in range [0, 1].
    """
    img_f = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    hsv = cv2.cvtColor(img_f, cv2.COLOR_RGB2HSV)
    # OpenCV's HSV representation has H in [0, 360], scale it to [0, 1] to match skimage
    hsv[..., 0] = hsv[..., 0] / 360.0
    return hsv

def safe_hsv2rgb(hsv):
    """
    NumPy 2.0-safe and OpenCV-backed alternative to skimage.color.hsv2rgb.
    Input hsv should be float32 with channels in range [0, 1].
    Returns RGB image in float32 in range [0, 1].
    """
    hsv_f = np.asarray(hsv, dtype=np.float32).copy()
    hsv_f[..., 0] = np.clip(hsv_f[..., 0], 0.0, 1.0) * 360.0
    hsv_f[..., 1] = np.clip(hsv_f[..., 1], 0.0, 1.0)
    hsv_f[..., 2] = np.clip(hsv_f[..., 2], 0.0, 1.0)
    rgb = cv2.cvtColor(hsv_f, cv2.COLOR_HSV2RGB)
    return np.clip(rgb, 0.0, 1.0)
