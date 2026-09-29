"""Unit and integration tests for Decoupling Palette and Natural Stars.

验证:
1. combine_starless_stars 支持天体物理屏幕混合 (screen) 与经典加法 (add)；
2. neutralize_dark_background 能够将暗背景 R/G/B 通道中位数中性化；
3. apply_dualband_palette 支持 star_mask 保护星点天然色度；
4. apply_professional_style 支持 star_mask 避免 warmth 滤镜导致恒星变黄；
5. 端到端管线在启用 --palette sho 时能够完整执行并记录 star_blend_mode。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imsave

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from palette_tools import (
    apply_dualband_palette,
    neutralize_dark_background,
    neutralize_palette_green,
)
from star_tools import combine_starless_stars
from style_tools import apply_professional_style
import pipeline


class TestCombineStarlessStars(unittest.TestCase):
    def test_screen_blend_formula(self):
        # 构造简单的测试图
        starless = np.full((10, 10, 3), 0.4, dtype=np.float32)
        stars = np.full((10, 10, 3), 0.5, dtype=np.float32)

        # 预期屏幕混合: 1 - (1 - 0.4) * (1 - 0.5) = 1 - 0.6 * 0.5 = 0.7
        combined = combine_starless_stars(starless, stars, star_strength=1.0, blend_mode="screen")
        np.testing.assert_allclose(combined, 0.7, atol=1e-5)

    def test_additive_blend_formula(self):
        starless = np.full((10, 10, 3), 0.4, dtype=np.float32)
        stars = np.full((10, 10, 3), 0.5, dtype=np.float32)

        # 预期加法混合: 0.4 + 0.5 = 0.9
        combined = combine_starless_stars(starless, stars, star_strength=1.0, blend_mode="add")
        np.testing.assert_allclose(combined, 0.9, atol=1e-5)

    def test_screen_blend_prevents_clipping_overflow(self):
        # 极亮星点坐在亮星云上：0.8 + 0.8 在加法中直接被 clip 截断为 1.0 (产生梯级硬切)
        # 在 screen 模式下: 1 - 0.2 * 0.2 = 0.96，平滑保留过渡
        starless = np.full((8, 8, 3), 0.8, dtype=np.float32)
        stars = np.full((8, 8, 3), 0.8, dtype=np.float32)

        combined = combine_starless_stars(starless, stars, star_strength=1.0, blend_mode="screen")
        self.assertAlmostEqual(float(combined[0, 0, 0]), 0.96, places=4)

    def test_invalid_blend_mode_raises(self):
        starless = np.zeros((4, 4, 3), dtype=np.float32)
        stars = np.zeros((4, 4, 3), dtype=np.float32)
        with self.assertRaises(ValueError):
            combine_starless_stars(starless, stars, blend_mode="invalid_mode")


class TestNeutralizeDarkBackground(unittest.TestCase):
    def test_neutralizes_background_color_cast(self):
        # 构造暗背景有严重绿偏的图像 (R=0.03, G=0.06, B=0.03)
        img = np.zeros((50, 50, 3), dtype=np.float32)
        img[..., 0] = 0.03
        img[..., 1] = 0.06
        img[..., 2] = 0.03

        # 在中央放置一个高亮天体目标 (R=0.6, G=0.4, B=0.8)
        img[20:30, 20:30, 0] = 0.6
        img[20:30, 20:30, 1] = 0.4
        img[20:30, 20:30, 2] = 0.8

        neutralized = neutralize_dark_background(img)

        # 检查暗背景区域 (角部) 的 R, G, B 是否趋向均衡
        corner_r = float(neutralized[5, 5, 0])
        corner_g = float(neutralized[5, 5, 1])
        corner_b = float(neutralized[5, 5, 2])

        self.assertAlmostEqual(corner_r, corner_g, delta=0.01)
        self.assertAlmostEqual(corner_g, corner_b, delta=0.01)

        # 检查中心高亮天体没有被破坏
        self.assertGreater(neutralized[25, 25, 0], 0.5)
        self.assertGreater(neutralized[25, 25, 2], 0.7)


class TestPaletteStarProtection(unittest.TestCase):
    def test_palette_preserves_stars_with_mask(self):
        # 构造一个双窄带假想图：暗背景 + 发射星云结构 + 天然蓝白色恒星
        img = np.full((40, 40, 3), 0.02, dtype=np.float32)
        img[5:35, 5:35, 0] = 0.40  # Hα 丰富
        img[5:35, 5:35, 1] = 0.10
        img[5:35, 5:35, 2] = 0.15

        # 中心恒星
        img[18:22, 18:22, 0] = 0.6
        img[18:22, 18:22, 1] = 0.8
        img[18:22, 18:22, 2] = 1.0

        star_mask = np.zeros((40, 40), dtype=np.float32)
        star_mask[18:22, 18:22] = 1.0

        # 不加 mask: 恒星会被双窄带映射打碎染上色板色彩
        unprotected = apply_dualband_palette(img, palette="SHO", star_mask=None)
        # 加 mask: 恒星色彩被锁定
        protected = apply_dualband_palette(img, palette="SHO", star_mask=star_mask)

        # 验证受保护的星点保持蓝白色 (B > R)
        self.assertGreater(protected[20, 20, 2], protected[20, 20, 0])
        # 验证星云区域正常执行了 SHO 调色
        self.assertGreater(protected[10, 10, 1], protected[10, 10, 2])  # SHO 绿色/金色通道高于蓝色通道


class TestStyleToolsStarProtection(unittest.TestCase):
    def test_apply_professional_style_protects_stars_from_warmth(self):
        # 构造带有恒星的图像
        img = np.full((40, 40, 3), 0.2, dtype=np.float32)
        # 恒星像素是纯白/微蓝 (R=0.9, G=0.95, B=1.0)
        img[18:22, 18:22, 0] = 0.90
        img[18:22, 18:22, 1] = 0.95
        img[18:22, 18:22, 2] = 1.00

        star_mask = np.zeros((40, 40), dtype=np.float32)
        star_mask[18:22, 18:22] = 1.0

        # dramatic_nebula 拥有 warmth=0.02
        styled_no_mask, _, _ = apply_professional_style(
            img, style="dramatic_nebula", strength=1.2, star_mask=None
        )
        styled_with_mask, _, _ = apply_professional_style(
            img, style="dramatic_nebula", strength=1.2, star_mask=star_mask
        )

        # 没有 mask 保护时，恒星像素的 B/R 比例下降（变暖变黄）
        ratio_unprotected = styled_no_mask[20, 20, 2] / styled_no_mask[20, 20, 0]
        # 有 mask 保护时，恒星像素的 B/R 比例更高，维持蓝白
        ratio_protected = styled_with_mask[20, 20, 2] / styled_with_mask[20, 20, 0]

        self.assertGreater(ratio_protected, ratio_unprotected)


class TestPipelinePaletteIntegration(unittest.TestCase):
    def test_single_image_palette_with_star_mask_protection(self):
        # 模拟单图流程（无外部星点层，回退到单图保护）
        h, w = 64, 64
        rng = np.random.default_rng(42)
        img = rng.normal(loc=0.04, scale=0.005, size=(h, w, 3)).astype(np.float32)
        img = np.clip(img, 0.001, 1.0)
        img[20:45, 20:45, 0] += 0.25
        img[12, 12] = [0.8, 0.9, 1.0]

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "test_input.png")
            out_path = os.path.join(td, "test_out.jpg")
            work_dir = os.path.join(td, "work")
            imsave(src_path, (np.clip(img, 0, 1) * 255).astype(np.uint8))

            result = pipeline.run_pipeline(
                src_path,
                out_path,
                preset="light",
                palette="sho",
                star_blend_mode="screen",
                steps="stretch,final_color,style",
                keep_all=True,
                work_dir=work_dir,
            )

            self.assertTrue(os.path.exists(out_path))
            self.assertEqual(result["effective_config"]["star_blend_mode"], "screen")
            self.assertIn("08_palette_sho.tif", result["artifacts"])

    def test_decoupled_multilayer_palette_and_screen_combine(self):
        # 模拟拥有纯净无星图与独立星点图层的全流程
        h, w = 64, 64
        rng = np.random.default_rng(42)
        starless = rng.normal(loc=0.04, scale=0.005, size=(h, w, 3)).astype(np.float32)
        starless = np.clip(starless, 0.001, 1.0)
        starless[20:45, 20:45, 0] += 0.25  # Hα
        starless[20:45, 20:45, 1] += 0.08  # OIII
        starless[20:45, 20:45, 2] += 0.12  # OIII

        # 恒星层
        stars = np.zeros((h, w, 3), dtype=np.float32)
        stars[12:15, 12:15] = [0.8, 0.9, 1.0]  # 天然蓝白恒星
        stars[30:33, 40:43] = [0.9, 0.8, 0.6]  # 天然暖金恒星

        full_img = np.clip(starless + stars, 0.0, 1.0)

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "test_input.png")
            starless_path = os.path.join(td, "test_starless.tif")
            out_path = os.path.join(td, "test_out.jpg")
            work_dir = os.path.join(td, "work")

            imsave(src_path, (np.clip(full_img, 0, 1) * 255).astype(np.uint8))
            imsave(starless_path, starless.astype(np.float32))

            result = pipeline.run_pipeline(
                src_path,
                out_path,
                external_starless=starless_path,
                preset="light",
                palette="sho",
                star_blend_mode="screen",
                steps="star_remove,stretch,star_process,final_color,star_combine,style",
                keep_all=True,
                work_dir=work_dir,
            )

            self.assertTrue(os.path.exists(out_path))
            self.assertEqual(result["effective_config"]["star_blend_mode"], "screen")
            self.assertIn("08_palette_sho.tif", result["artifacts"])
            self.assertIn("09_star_combined.tif", result["artifacts"])


if __name__ == "__main__":
    unittest.main()
