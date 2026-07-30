import unittest
from pathlib import Path
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import stretch

class TestGhsStretch(unittest.TestCase):
    def test_ghs_stretch_boundaries(self):
        # 边界与常量值输入
        img_zeros = np.zeros((10, 10), dtype=np.float32)
        img_ones = np.ones((10, 10), dtype=np.float32)

        res_zeros = stretch.ghs_stretch(img_zeros, sp=0.01, b=8.0)
        res_ones = stretch.ghs_stretch(img_ones, sp=0.01, b=8.0)

        self.assertTrue(np.allclose(res_zeros, 0.0, atol=1e-6))
        self.assertTrue(np.allclose(res_ones, 1.0, atol=1e-6))

        # 对称性点测试
        img_mid = np.full((10, 10), 0.5, dtype=np.float32)
        res_mid = stretch.ghs_stretch(img_mid, sp=0.5, b=5.0)
        self.assertAlmostEqual(float(np.mean(res_mid)), 0.5, places=5)

    def test_masked_ghs_stretch(self):
        # 创建一个混合了“背景底噪（极暗）”和“亮核（极亮）”的模拟图像
        # 将底噪设为 0.002，使拉伸平移归一化逻辑正常工作
        image = np.full((64, 64), 0.002, dtype=np.float32)
        # 亮核
        image[20:44, 20:44] = 0.8
        # 弱星云区域
        image[5:15, 5:15] = 0.015

        # 我们把 sp 设为 0.002 (即背景中位数)，b 设为 8.0
        res = stretch.masked_ghs_stretch(image, sp=0.002, b=8.0, protect_strength=0.8, target_bg=0.08)

        # 验证输出在 [0, 1] 内
        self.assertTrue(np.all(res >= 0.0) and np.all(res <= 1.0))

        # 暗部提亮倍率（0.015 处的星云）:
        bg_stretched = res[8, 8]
        bg_ratio = bg_stretched / 0.015

        # 亮部提亮倍率（0.8 处的亮核）:
        core_stretched = res[32, 32]
        core_ratio = core_stretched / 0.8

        self.assertGreater(bg_ratio, core_ratio)
        self.assertGreater(bg_stretched, 0.08) # 应该被显著提亮

    def test_apply_luminance_stretch_ghs(self):
        # 验证 Lab 空间的拉伸
        # 创建一个彩色图像
        image = np.zeros((16, 16, 3), dtype=np.float32)
        # 给每个通道不同的值，模拟恒星颜色
        image[..., 0] = 0.01  # R
        image[..., 1] = 0.005 # G
        image[..., 2] = 0.02  # B

        # 对 L 通道进行拉伸，色彩比例应基本保留
        stretched = stretch.apply_luminance_stretch(image, method='masked_ghs', sp=0.01, b=8.0, target_bg=0.08)

        # 验证色彩关系（比如拉伸后B仍然是最大，R是次大，G是最小）
        self.assertTrue(np.all(stretched[..., 2] > stretched[..., 0]))
        self.assertTrue(np.all(stretched[..., 0] > stretched[..., 1]))

        # 验证数值有效性
        self.assertTrue(np.all(stretched >= 0.0) and np.all(stretched <= 1.0))

    def test_masked_ghs_preserves_positive_extreme_dark_signal(self):
        image = np.full((96, 128), 2e-5, dtype=np.float32)
        yy, xx = np.mgrid[:96, :128]
        shell = np.exp(
            -((np.sqrt((xx - 64) ** 2 + (yy - 48) ** 2) - 24) / 3) ** 2
        )
        image += shell.astype(np.float32) * 8e-5
        image[20, 20] = 8e-4

        result = stretch.masked_ghs_stretch(
            image,
            sp=-1,
            b=8.0,
            target_bg=0.12,
            shadow_pctl=0.0,
            highlight_pctl=99.9,
            gamma=0.45,
        )

        self.assertLess(float(np.mean(result <= 0)), 0.01)
        self.assertGreater(float(np.median(result)), 0.08)
        self.assertGreater(
            float(np.mean(result[shell > 0.7])),
            float(np.median(result)),
        )

if __name__ == "__main__":
    unittest.main()
