#!/usr/bin/env python3
import numpy as np
import cv2

def safe_rgb2lab(image):
    """
    NumPy 2.0-safe and OpenCV-backed alternative to skimage.color.rgb2lab.
    Input image should be float32 in range [0, 1] RGB.
    Returns L in [0, 100], a, b in standard scales.
    """
    img_f = np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    lab = cv2.cvtColor(img_f, cv2.COLOR_RGB2Lab)
    return lab

def safe_lab2rgb(lab):
    """
    NumPy 2.0-safe and OpenCV-backed alternative to skimage.color.lab2rgb.
    Input lab should be float32 with L in [0, 100].
    Returns RGB image in float32 in range [0, 1].
    """
    lab_f = np.asarray(lab, dtype=np.float32).copy()
    lab_f[..., 0] = np.clip(lab_f[..., 0], 0.0, 100.0)
    lab_f[..., 1] = np.clip(lab_f[..., 1], -128.0, 127.0)
    lab_f[..., 2] = np.clip(lab_f[..., 2], -128.0, 127.0)
    rgb = cv2.cvtColor(lab_f, cv2.COLOR_Lab2RGB)
    return np.clip(rgb, 0.0, 1.0)

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
