"""Background colour-cast metric and its non-escalating gate.

现有 7 道门里没有任何一道检查每通道背景色偏，于是一幅背景 R/G≈3.3、
核心 B/G≈1.14 的严重偏色输出只触发了一条 BACKGROUND_TOO_BRIGHT。
新增的门只记录不升级 status，避免历史正常文件突然全部变成 review_required。
"""

import unittest
from pathlib import Path

import numpy as np

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_protocol import evaluate_quality_gates
from quality_metrics import calculate_metrics


def _neutral_final_metrics(**overrides):
    metrics = {
        "processing_stage": "final",
        "median": 0.08,
        "p1": 0.01,
        "negative_pixel_ratio": 0.0,
        "nonpositive_pixel_ratio": 0.0,
        "corner_uniformity_ratio": 1.0,
        "uniform_5x5_dark_patch_ratio": 0.0,
        "star_area_ratio": 0.02,
    }
    metrics.update(overrides)
    return metrics


class ColorCastMetricTests(unittest.TestCase):
    def test_emits_cast_for_a_green_background(self):
        image = np.full((64, 64, 3), [0.05, 0.10, 0.02], dtype=np.float32)
        image[28:36, 28:36] = [0.8, 0.7, 0.5]

        metrics = calculate_metrics(image)

        self.assertIn("background_color_cast_magnitude", metrics)
        self.assertGreater(metrics["background_color_cast_magnitude"], 0.08)
        self.assertAlmostEqual(
            metrics["background_color_cast"]["r_over_g"], 0.5, places=1
        )

    def test_neutral_background_reports_near_zero_cast(self):
        image = np.full((64, 64, 3), 0.05, dtype=np.float32)
        image[28:36, 28:36] = [0.6, 0.6, 0.6]

        metrics = calculate_metrics(image)

        self.assertLess(metrics["background_color_cast_magnitude"], 0.05)

    def test_clipped_background_does_not_produce_absurd_numbers(self):
        """三通道都被压到 0 时无法测量色偏，不应报出 1e5 量级的假数值。"""
        image = np.zeros((64, 64, 3), dtype=np.float32)
        image[..., 0] = 0.0002          # 只有 R 还剩一点残值
        image[28:36, 28:36] = 0.8

        metrics = calculate_metrics(image)

        magnitude = metrics.get("background_color_cast_magnitude")
        if magnitude is not None:
            self.assertLess(magnitude, 100.0)


class ColorCastGateTests(unittest.TestCase):
    def test_gate_is_recorded_but_does_not_escalate(self):
        status, gates = evaluate_quality_gates(
            _neutral_final_metrics(
                background_color_cast_magnitude=0.5,
                background_color_cast={"r_over_g": 1.5, "b_over_g": 0.8},
            )
        )

        cast_gate = next(g for g in gates if g["code"] == "BACKGROUND_COLOR_CAST")
        self.assertEqual(cast_gate["status"], "warning")
        self.assertFalse(cast_gate["escalate"])
        # 关键：只有这一条门时，状态必须保持 success
        self.assertEqual(status, "success")

    def test_gate_absent_for_neutral_background(self):
        _status, gates = evaluate_quality_gates(
            _neutral_final_metrics(background_color_cast_magnitude=0.01)
        )
        self.assertNotIn("BACKGROUND_COLOR_CAST", {g["code"] for g in gates})

    def test_gate_skipped_for_linear_stage(self):
        _status, gates = evaluate_quality_gates(
            _neutral_final_metrics(
                processing_stage="linear",
                background_color_cast_magnitude=0.9,
            )
        )
        self.assertNotIn("BACKGROUND_COLOR_CAST", {g["code"] for g in gates})

    def test_existing_escalating_gate_still_escalates(self):
        status, gates = evaluate_quality_gates(
            _neutral_final_metrics(
                corner_uniformity_ratio=4.2,
                background_color_cast_magnitude=0.5,
            ),
            target_type="emission_nebula",
            steps=["dbe"],
        )
        self.assertEqual(status, "review_required")
        self.assertEqual(gates[0]["code"], "CORNER_NONUNIFORM")

    def test_star_dominance_is_recorded_but_does_not_escalate(self):
        """密集星场下 star_area_ratio 天然偏高（实测 0.187 vs 阈值 0.18），
        升为 escalate 门会稳定误报 —— 召回 86~100% 的门禁等于没有门禁。
        改为只记录，门禁条目与数值仍进 gates/warnings 供人工参考。"""
        _status, gates = evaluate_quality_gates(
            _neutral_final_metrics(star_area_ratio=0.4)
        )
        star_gate = next(g for g in gates if g["code"] == "STAR_DOMINANCE")
        self.assertEqual(star_gate["status"], "warning")
        self.assertFalse(star_gate["escalate"])
        self.assertEqual(star_gate["value"], 0.4)

    def test_existing_gates_keep_default_escalation(self):
        """保留原始意图：别误改其他门的 escalate。用 DENOISE_PLASTICITY 承担
        「未改动的门仍应升级」这一断言（STAR_DOMINANCE 已改为只记录）。"""
        _status, gates = evaluate_quality_gates(
            _neutral_final_metrics(star_area_ratio=0.02,
                                  uniform_5x5_dark_patch_ratio=0.5)
        )
        denoise_gate = next(g for g in gates
                            if g["code"] == "DENOISE_PLASTICITY")
        self.assertTrue(denoise_gate["escalate"])


if __name__ == "__main__":
    unittest.main()
