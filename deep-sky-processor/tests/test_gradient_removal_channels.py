"""Per-channel background removal.

旧实现用 `np.mean(image, axis=2)` 得到一个亮度背景面并复制到所有通道。
只要背景存在轻微色偏（深空图像几乎总是如此），较暗的通道就会被减成负值并
被 clip 到 0 —— 通道相对色偏被急剧放大，且信息不可恢复。
"""

import unittest
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gradient_removal import (
    estimate_background_polynomial,
    remove_gradient,
)


def _casted_background(height=256, width=256):
    """背景带轻微色偏（R>G>B，典型光害），外加一个亮团。

    尺寸要足够大：estimate_background_polynomial 的采样间距是 50px，
    图太小会导致有效样本不足而回退到昂贵的 size=101 中值滤波。
    """
    yy, xx = np.mgrid[0:height, 0:width]
    gradient = 0.02 * xx / width + 0.01 * yy / height
    base = np.stack(
        [0.10 + gradient, 0.09 + gradient, 0.08 + gradient], axis=-1
    )
    blob = 0.5 * np.exp(-(((xx - width * 0.5) / 24) ** 2 +
                          ((yy - height * 0.5) / 24) ** 2))
    base += blob[..., None]
    return np.clip(base, 0, 1).astype(np.float32)


def _legacy_remove_gradient(image, degree=2):
    """复刻旧实现：单一亮度背景面减所有通道。"""
    gray = image.mean(axis=2).astype(np.float64)
    bg = gaussian_filter(estimate_background_polynomial(gray, degree=degree), sigma=10)
    return image.astype(np.float64) - bg[..., None]


class GradientRemovalChannelTests(unittest.TestCase):
    def test_per_channel_dbe_does_not_destroy_the_darkest_channel(self):
        image = _casted_background()
        corrected, _bg = remove_gradient(image, method="polynomial", degree=2)

        negative = [float(np.mean(corrected[..., c] < 0)) for c in range(3)]
        # 各通道被减成负值的比例应当接近（都是"减掉中位背景"的正常结果）
        self.assertLess(max(negative) - min(negative), 0.15)

        # 对照：旧实现会让最暗的 B 通道远高于其它通道
        legacy = _legacy_remove_gradient(image)
        legacy_negative = [float(np.mean(legacy[..., c] < 0)) for c in range(3)]
        self.assertGreater(max(legacy_negative) - min(legacy_negative), 0.3)

    def test_per_channel_dbe_preserves_relative_channel_levels(self):
        image = _casted_background()
        corrected, _bg = remove_gradient(image, method="polynomial", degree=2)

        dark = corrected.mean(axis=2) < np.percentile(corrected.mean(axis=2), 25)
        residual = np.array([corrected[..., c][dark].mean() for c in range(3)])
        # 逐通道各自归零后，残差背景的通道差应远小于输入
        self.assertLess(
            float(residual.max() - residual.min()),
            0.01,
        )

    def test_background_model_shape_follows_input(self):
        rgb = _casted_background()
        _corrected, bg_rgb = remove_gradient(rgb, method="polynomial", degree=2)
        self.assertEqual(bg_rgb.shape, rgb.shape)

        gray = rgb.mean(axis=2)
        _corrected, bg_gray = remove_gradient(gray, method="polynomial", degree=2)
        self.assertEqual(bg_gray.shape, gray.shape)

    def test_2d_path_is_unchanged(self):
        """灰度路径必须与旧公式逐点一致（既有集成测试依赖它）。"""
        gray = _casted_background().mean(axis=2).astype(np.float64)
        corrected, _bg = remove_gradient(gray, method="polynomial", degree=2)
        legacy = gray - gaussian_filter(
            estimate_background_polynomial(gray, degree=2), sigma=10
        )
        self.assertTrue(np.allclose(corrected, legacy, atol=1e-10))


if __name__ == "__main__":
    unittest.main()
