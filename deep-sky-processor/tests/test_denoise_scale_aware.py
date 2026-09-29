"""双边降噪的 `sigma_color` 必须**跨域语义一致**。

管线里两次降噪发生在量级差约 3 个数量级的域上：线性母版（星云 ~1e-3）与非线性
（~1e-1）。`sigma_color` 是**图像值域的绝对量**，所以同一个配置值在两边含义完全不同。

实测（阶跃边缘，边缘对比 = 噪声量级，`sigma_color=0.005`）：

| 量级 | 边缘对比 | 旧（绝对语义）边缘保留 |
|---|---|---|
| 线性域 | 0.001 | **2.5%** |
| 非线性域 | 0.100 | **100.1%** |

根因：`skimage.restoration.denoise_bilateral` 把颜色 LUT 建在 `[0, image.max()]` 上。
线性天文母版的 `max()` 是亮星（~0.95），**整个星云只住在最底下 ~0.3%**，LUT 在那里
几乎没有分辨率；`sigma_color` 又比该处的对比大一个量级 → 值域核恒为 1 → 退化成
`sigma_spatial=15` 的**纯高斯**，低对比细节被毁而噪声只降 13%。

修复：`scale_aware=True`（默认）按图像自身的 p99.9 缩放 `sigma_color`，使配置值表示
**相对强度**。实测线性域保边恢复到 **100.0%**，非线性域不变（100.1%）。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from denoise import denoise_bilateral_wrapper  # noqa: E402


def _edge_scene(lo, hi, h=96, w=96, noise_frac=0.02, seed=3):
    """左半 lo、右半 hi 的阶跃边缘 + 与边缘对比同量级的噪声。"""
    rng = np.random.default_rng(seed)
    img = np.full((h, w), lo, dtype=np.float64)
    img[:, w // 2:] = hi
    img = img + rng.normal(0, noise_frac * (hi - lo), img.shape)
    return np.clip(img, 0, None).astype(np.float32)


def _edge_retention(a):
    """边缘两侧 3px 内的对比度保留率（1.0 = 完全保边）。"""
    w = a.shape[1]
    left = float(a[:, w // 2 - 3:w // 2].mean())
    right = float(a[:, w // 2:w // 2 + 3].mean())
    return right - left


def _retention_ratio(image, **kwargs):
    return _edge_retention(
        denoise_bilateral_wrapper(image, **kwargs)) / _edge_retention(image)


class DenoiseScaleAwareTests(unittest.TestCase):
    # 线性域：星云量级，边缘对比 ~1e-3
    LINEAR_LO, LINEAR_HI = 0.001, 0.002
    # 非线性域：拉伸后量级，边缘对比 ~1e-1
    NONLINEAR_LO, NONLINEAR_HI = 0.10, 0.20

    def test_linear_domain_edge_is_preserved(self):
        """① 核心：线性域的低对比边缘必须被保住（旧实现只留 2.5%）。"""
        x = _edge_scene(self.LINEAR_LO, self.LINEAR_HI)
        for sc in (0.003, 0.005, 0.02):
            with self.subTest(sigma_color=sc):
                ratio = _retention_ratio(x, sigma_color=sc, sigma_spatial=15)
                self.assertGreater(ratio, 0.9, f"线性域保边仅 {ratio:.3f}")

    def test_absolute_semantics_was_broken(self):
        """② 回归护栏：记录旧绝对语义的失效（防止有人改回默认）。"""
        x = _edge_scene(self.LINEAR_LO, self.LINEAR_HI)
        ratio = _retention_ratio(x, sigma_color=0.005, sigma_spatial=15,
                                 scale_aware=False)
        self.assertLess(ratio, 0.1, f"旧语义应几乎不保边，实测 {ratio:.3f}")

    def test_nonlinear_domain_is_unchanged(self):
        """③ 非线性域本来就正常，修复不应把它改坏。"""
        x = _edge_scene(self.NONLINEAR_LO, self.NONLINEAR_HI)
        for sc in (0.003, 0.005, 0.02):
            with self.subTest(sigma_color=sc):
                old = _retention_ratio(x, sigma_color=sc, sigma_spatial=15,
                                       scale_aware=False)
                new = _retention_ratio(x, sigma_color=sc, sigma_spatial=15)
                self.assertGreater(old, 0.9)
                self.assertGreater(new, 0.9)
                self.assertAlmostEqual(new, old, delta=0.05)

    def test_parameter_is_scale_invariant(self):
        """④ 同一个 sigma_color 在两种量级下应给出接近的保边表现。"""
        lin = _retention_ratio(_edge_scene(self.LINEAR_LO, self.LINEAR_HI),
                               sigma_color=0.005, sigma_spatial=15)
        nonlin = _retention_ratio(_edge_scene(self.NONLINEAR_LO, self.NONLINEAR_HI),
                                  sigma_color=0.005, sigma_spatial=15)
        self.assertLess(abs(lin - nonlin), 0.1,
                        f"跨域不一致：线性 {lin:.3f} vs 非线性 {nonlin:.3f}")

    def test_scale_aware_is_the_default(self):
        """⑤ 默认必须是 scale_aware（否则上面的修复不生效）。"""
        x = _edge_scene(self.LINEAR_LO, self.LINEAR_HI)
        default = _retention_ratio(x, sigma_color=0.005, sigma_spatial=15)
        explicit = _retention_ratio(x, sigma_color=0.005, sigma_spatial=15,
                                    scale_aware=True)
        self.assertAlmostEqual(default, explicit, places=9)

    def test_degenerate_input_is_safe(self):
        """⑥ 全零图 / 常数图不得崩（锚点为 0 时直接返回）。"""
        for value in (0.0, 0.5):
            with self.subTest(value=value):
                flat = np.full((32, 32), value, dtype=np.float32)
                out = denoise_bilateral_wrapper(flat, sigma_color=0.005)
                self.assertTrue(np.all(np.isfinite(out)))
                self.assertEqual(out.shape, flat.shape)

    def test_rgb_input_supported(self):
        """⑦ 3 通道输入仍走 channel_axis 分支。"""
        gray = _edge_scene(self.LINEAR_LO, self.LINEAR_HI)
        rgb = np.repeat(gray[..., None], 3, axis=2)
        out = denoise_bilateral_wrapper(rgb, sigma_color=0.005, sigma_spatial=15)
        self.assertEqual(out.shape, rgb.shape)
        self.assertTrue(np.all(np.isfinite(out)))


if __name__ == "__main__":
    unittest.main()
