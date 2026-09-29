"""Unit and integration tests for Background Neutralization Lock (背景中性化锁定).

验证:
1. lock_background_neutrality 能够精准拉齐不同色偏 (红偏/绿偏/蓝偏) 的暗背景；
2. Smoothstep 平滑滚降权重能够 100% 保全天体主体、亮细丝与星云色彩；
3. 支持 star_mask 保护恒星；
4. 底电平安全检测杜绝黑位压塌 (p1 保全)；
5. 端到端管线出片前自动消除 BACKGROUND_COLOR_CAST 质量门禁警告。
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

from color_tools import lock_background_neutrality
from quality_metrics import calculate_metrics
from agent_protocol import evaluate_quality_gates
import pipeline


class TestLockBackgroundNeutrality(unittest.TestCase):
    def test_lock_neutralizes_green_background_cast(self):
        # 构造暗背景严重偏绿的测试图 (R=0.03, G=0.06, B=0.03, R/G=0.50, mag=0.50)
        h, w = 60, 60
        img = np.zeros((h, w, 3), dtype=np.float32)
        img[..., 0] = 0.030
        img[..., 1] = 0.060
        img[..., 2] = 0.030

        # 在中央放置一个高亮天体发射星云 (R=0.75, G=0.20, B=0.15)
        img[20:40, 20:40, 0] = 0.75
        img[20:40, 20:40, 1] = 0.20
        img[20:40, 20:40, 2] = 0.15

        locked, report = lock_background_neutrality(img, return_report=True)

        self.assertTrue(report["applied"])
        self.assertGreater(report["pre_cast_magnitude"], 0.40)
        self.assertLess(report["post_cast_magnitude"], 0.04)

        # 检查暗背景区域角落的通道中值已达到完美均衡
        corner = locked[:10, :10]
        med_r = np.median(corner[..., 0])
        med_g = np.median(corner[..., 1])
        med_b = np.median(corner[..., 2])
        self.assertAlmostEqual(med_r, med_g, delta=0.002)
        self.assertAlmostEqual(med_g, med_b, delta=0.002)

    def test_lock_preserves_high_signal_nebulosity(self):
        # 验证进入天体主体区域 (I >= t_high) 后，权重降为 0，高光天体像素 100% 保持原样
        h, w = 60, 60
        img = np.full((h, w, 3), 0.04, dtype=np.float32)
        img[..., 0] = 0.035
        img[..., 1] = 0.050
        img[..., 2] = 0.040

        # 亮核结构
        orig_core = np.array([0.88, 0.45, 0.22], dtype=np.float32)
        img[25:35, 25:35] = orig_core

        locked, report = lock_background_neutrality(img, return_report=True)

        # 核心像素必须完全一致
        np.testing.assert_allclose(locked[30, 30], orig_core, atol=1e-6)

    def test_lock_protects_stars_via_star_mask(self):
        # 验证传入 star_mask 时，星点像素不被加性背景平移修改
        h, w = 40, 40
        img = np.full((h, w, 3), 0.03, dtype=np.float32)
        img[..., 1] += 0.02  # 背景轻微绿偏

        # 一颗位于暗背景上的天然纯蓝白恒星 (R=0.6, G=0.8, B=1.0)
        img[10, 10] = [0.6, 0.8, 1.0]

        star_mask = np.zeros((h, w), dtype=np.float32)
        star_mask[10, 10] = 1.0

        locked, report = lock_background_neutrality(img, star_mask=star_mask, return_report=True)

        # 受保护的恒星中心像素保持不变
        np.testing.assert_allclose(locked[10, 10], [0.6, 0.8, 1.0], atol=1e-5)

    def test_lock_guards_against_black_crush(self):
        # 构造底电平接近 0 的极暗图像
        h, w = 50, 50
        img = np.full((h, w, 3), 0.001, dtype=np.float32)
        img[..., 0] = 0.002
        img[..., 1] = 0.001
        img[..., 2] = 0.0005

        locked, report = lock_background_neutrality(img, return_report=True)

        # p1 必须大于 1e-4，杜绝 BACKGROUND_CRUSHED
        p1 = float(np.percentile(locked, 1.0))
        self.assertGreater(p1, 1e-4)
        self.assertEqual(float(np.min(locked)), float(np.min(locked[locked >= 0])))

    def test_lock_iterative_convergence_with_awakened_near_zero_pixels(self):
        # 构造存在部分单通道为0、在初次平移后会被唤醒的极端病态暗部像素
        h, w = 60, 60
        img = np.full((h, w, 3), 0.03, dtype=np.float32)
        img[..., 0] = 0.035
        img[..., 1] = 0.025
        img[..., 2] = 0.040

        # 在暗部注入单通道塌缩像素 (G 通道为 0)
        img[:15, :15, 1] = 0.0
        img[:15, :15, 0] = 0.01
        img[:15, :15, 2] = 0.025

        locked, report = lock_background_neutrality(img, max_iters=2, return_report=True)

        self.assertTrue(report["applied"])
        self.assertLess(report["post_cast_magnitude"], 0.05)

        # 闭环验证：calculate_metrics 测得的色偏与 report 严格一致并低于 0.08
        qm = calculate_metrics(locked, {})
        self.assertIn("background_color_cast_magnitude", qm)
        self.assertLessEqual(qm["background_color_cast_magnitude"], 0.05)
        self.assertAlmostEqual(qm["background_color_cast_magnitude"], report["post_cast_magnitude"], delta=0.01)

    def test_enhance_saturation_preserves_pedestal_without_zero_collapse(self):
        from color_tools import enhance_saturation
        h, w = 40, 40
        img = np.full((h, w, 3), 0.02, dtype=np.float32)
        # 背景微偏色，弱通道只有 0.008
        img[..., 0] = 0.030
        img[..., 1] = 0.008
        img[..., 2] = 0.020

        # 激进饱和度增强
        boosted = enhance_saturation(img, factor=1.85, protect_background=True, bg_protection_percentile=40)

        # 保证弱通道没有被 HSV 截断压死为 0
        min_g = float(np.min(boosted[..., 1]))
        self.assertGreater(min_g, 1e-4)


class TestPipelineBackgroundLockIntegration(unittest.TestCase):
    def test_pipeline_final_background_lock_clears_cast_gate(self):
        # 构造端到端合成图像并注入非对称背景偏色
        h, w = 64, 64
        rng = np.random.default_rng(123)
        img = rng.normal(loc=0.05, scale=0.005, size=(h, w, 3)).astype(np.float32)
        img = np.clip(img, 0.001, 1.0)

        # 注入背景色偏 (R通道偏高，B通道偏低)
        img[..., 0] += 0.015
        img[..., 2] -= 0.008

        # 注入中心星云结构
        img[20:45, 20:45, 0] += 0.30
        img[20:45, 20:45, 1] += 0.15
        img[20:45, 20:45, 2] += 0.10

        with tempfile.TemporaryDirectory() as td:
            src_path = os.path.join(td, "test_input.png")
            out_path = os.path.join(td, "test_out.jpg")
            work_dir = os.path.join(td, "work")
            imsave(src_path, (np.clip(img, 0, 1) * 255).astype(np.uint8))

            result = pipeline.run_pipeline(
                src_path,
                out_path,
                preset="light",
                steps="stretch,final_color,style",
                background_neutralize_lock=True,
                keep_all=True,
                work_dir=work_dir,
            )

            self.assertTrue(os.path.exists(out_path))
            self.assertIn("10b_background_locked.tif", result["artifacts"])
            self.assertIsNotNone(result.get("background_neutralization"))
            self.assertTrue(result["background_neutralization"]["applied"])

            # 验证 quality_gates 中绝对没有 BACKGROUND_COLOR_CAST 警告
            gate_codes = {g["code"] for g in result["quality_gates"]}
            self.assertNotIn("BACKGROUND_COLOR_CAST", gate_codes)


if __name__ == "__main__":
    unittest.main()
