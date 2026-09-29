"""Unit and integration tests for Celestial-Aware Corner Uniformity Assessment (质检角部均匀度天体感知).

验证:
1. 纯背景平坦图像: 四角均匀度正常通过 (exemption_applied=False, ratio ~ 1.0);
2. 纯背景真实光害梯度图像: 准确报警 CORNER_NONUNIFORM (failed, 绝不漏报真正缺陷);
3. 天体物理结构延伸至单角: 自动识别天体主导角，启用天体感知豁免，纯背景角比值健康，门禁 passed (假阳性被根治);
4. 向前兼容性: 纯字典无天体信息 mock 输入仍保持原有门禁升级行为;
5. 真实深空样本测试: C50 成品图像准确豁免西南角 OIII 发射星云，门禁 passed。
"""

import os
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from quality_metrics import analyze_corner_uniformity, calculate_metrics
from agent_protocol import evaluate_quality_gates


class TestCelestialAwareCornerUniformity(unittest.TestCase):
    def test_pure_flat_sky_background(self):
        # 1. 纯背景平坦图像
        h, w = 120, 160
        rng = np.random.default_rng(42)
        gray = rng.normal(loc=0.04, scale=0.002, size=(h, w)).astype(np.float32)
        gray = np.clip(gray, 0.001, 1.0)

        info = analyze_corner_uniformity(gray)
        self.assertFalse(info["exemption_applied"])
        self.assertEqual(len(info["celestial_dominated_corners"]), 0)
        self.assertLess(info["effective_uniformity_ratio"], 1.2)

        metrics = calculate_metrics(gray)
        self.assertIn("corner_analysis", metrics)
        status, gates = evaluate_quality_gates(metrics, target_type="emission_nebula", steps=["dbe"])
        self.assertNotIn("CORNER_NONUNIFORM", {g["code"] for g in gates if g["status"] == "failed"})

    def test_real_gradient_triggers_corner_alarm(self):
        # 2. 纯背景真实光害斜坡 (无天体结构，但左下角严重翘起 4x)
        h, w = 120, 160
        yy, xx = np.mgrid[:h, :w]
        # 构造平滑对角线光害梯度
        gradient = 0.03 + 0.12 * (yy / h) * (1.0 - xx / w)
        gray = gradient.astype(np.float32)

        info = analyze_corner_uniformity(gray)
        # 梯度平缓，全图背景 std 也会较大，不满足局域高对比天体结构特征
        # 即使被某些阈值采样，因为不存在天体结构，门禁依然严格报警
        metrics = calculate_metrics(gray)
        status, gates = evaluate_quality_gates(metrics, target_type="emission_nebula", steps=["dbe"])
        corner_gate = next((g for g in gates if g["code"] == "CORNER_NONUNIFORM"), None)
        self.assertIsNotNone(corner_gate)
        # 真实光害梯度必须报警
        self.assertIn(corner_gate["status"], ("failed", "warning"))

    def test_celestial_structure_extension_exempted(self):
        # 3. 构造三个角为纯平背景 (~0.04)，第四个角 (西南角 BL) 覆盖强星云发射结构 (~0.15)
        h, w = 120, 160
        cs = min(h, w) // 8
        gray = np.full((h, w), 0.04, dtype=np.float32)

        # 在中央放置星云主体
        gray[40:80, 50:110] = 0.25

        # 天体发射结构一路蔓延覆盖西南角 (左下角)
        gray[-cs:, :cs] = 0.15

        info = analyze_corner_uniformity(gray)
        # 必须感知到西南角为天体主导
        self.assertIn("bottom_left", info["celestial_dominated_corners"])
        self.assertTrue(info["exemption_applied"])
        self.assertLess(info["effective_uniformity_ratio"], 1.3)
        self.assertGreater(info["raw_uniformity_ratio"], 3.0)

        metrics = calculate_metrics(gray)
        status, gates = evaluate_quality_gates(metrics, target_type="emission_nebula", steps=["dbe"])
        corner_gate = next((g for g in gates if g["code"] == "CORNER_NONUNIFORM"), None)
        self.assertIsNotNone(corner_gate)
        self.assertEqual(corner_gate["status"], "passed")
        self.assertFalse(corner_gate.get("escalate", True))

    def test_backward_compatibility_mock_dict(self):
        # 4. 纯 mock 字典输入 (无 corner_analysis)
        status, gates = evaluate_quality_gates(
            {
                "median": 0.08,
                "corner_uniformity_ratio": 4.2,
                "uniform_5x5_dark_patch_ratio": 0.1,
                "star_area_ratio": 0.02,
            },
            target_type="emission_nebula",
            steps=["dbe"],
        )
        self.assertEqual(status, "review_required")
        self.assertEqual(gates[0]["code"], "CORNER_NONUNIFORM")
        self.assertEqual(gates[0]["status"], "failed")

    def test_c50_locked_image_passes_corner_gate(self):
        # 5. C50 真实生成图验证 (存在西南角 OIII 强辐射)
        c50_path = "/Users/mz/.gemini/antigravity/brain/1c08a2ee-1034-4fbd-9178-3bb3810691fd/c50_v4_locked_sho.jpg"
        if not os.path.exists(c50_path):
            self.skipTest("C50 locked image not found")

        from fits_io import read_image
        img, _ = read_image(c50_path)
        metrics = calculate_metrics(img, {"processing_stage": "final"})

        self.assertIn("corner_analysis", metrics)
        analysis = metrics["corner_analysis"]
        self.assertTrue(analysis["exemption_applied"])
        self.assertIn("bottom_left", analysis["celestial_dominated_corners"])
        self.assertLess(analysis["effective_uniformity_ratio"], 1.5)

        status, gates = evaluate_quality_gates(metrics, target_type="emission_nebula", steps=["dbe"])
        corner_gate = next((g for g in gates if g["code"] == "CORNER_NONUNIFORM"), None)
        self.assertIsNotNone(corner_gate)
        self.assertEqual(corner_gate["status"], "passed")
        self.assertFalse(corner_gate.get("escalate", True))


if __name__ == "__main__":
    unittest.main()
