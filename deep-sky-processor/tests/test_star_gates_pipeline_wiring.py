"""pipeline 路径的具名星点门禁接线。

背景：`artifact_gates` 的具名伪影门禁（STAR_RINGING / STAR_BLOAT /
STAR_LAYER_LOSS / STAR_HOLES / CORE_BURNING）此前**只在** agent 会话路径
（`agent_protocol.create_review_bundle`）运行，`pipeline.py` 路径完全不跑，
`result.json` 里连 `star_artifact_gates` 字段都没有。实测后果：星点胀大 2.15×
（线性 FWHM 3.91px → 拉伸后 8.41px）在主管线里无人报警。

本文件锁住接线正确性，重点是两个最容易静默失效的点：
  1. steps 过滤——快照发生在去星/缩星之后，若不剔除 star_remove / star_process
     则星点层门禁会被判 skipped（不报错、功能静默消失）；
  2. 形状/通道一致性——带 alpha 的 4 通道图必须被统一到 3 通道，否则成对门禁
     直接 ValueError。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

from pipeline import downsample_for_gates, GATE_PREVIEW_MAX_SIDE  # noqa: E402
from artifact_gates import evaluate_star_artifact_gates  # noqa: E402
from test_artifact_gates import make_star_field, to_rgb  # noqa: E402


class GateStepsFilterTests(unittest.TestCase):
    """steps 过滤是「静默失效」风险点，必须锁死。"""

    def test_unfiltered_steps_skip_layer_gates(self):
        image, _ = make_star_field()
        rgb = to_rgb(image)
        result = evaluate_star_artifact_gates(
            rgb, rgb.copy(), steps=["star_remove", "star_process"])
        statuses = {g["code"]: g["status"] for g in result["gates"]}
        self.assertEqual(statuses["STAR_LAYER_LOSS"], "skipped")
        self.assertEqual(statuses["STAR_HOLES"], "skipped")

    def test_filtered_steps_keep_layer_gates_active(self):
        """过滤掉 star_remove/star_process/star_reduce 后，层门禁必须真的在跑。"""
        image, _ = make_star_field()
        rgb = to_rgb(image)
        steps = ["star_remove", "stretch", "star_process", "star_reduce", "style"]
        filtered = [s for s in steps
                    if s not in ("star_remove", "star_process", "star_reduce")]
        self.assertNotIn("star_remove", filtered)
        self.assertNotIn("star_process", filtered)
        self.assertNotIn("star_reduce", filtered)

        result = evaluate_star_artifact_gates(rgb, rgb.copy(), steps=filtered)
        statuses = {g["code"]: g["status"] for g in result["gates"]}
        self.assertNotEqual(statuses["STAR_LAYER_LOSS"], "skipped")
        self.assertNotEqual(statuses["STAR_HOLES"], "skipped")
        # 干净候选（参照==候选）应为 success
        self.assertEqual(result["status"], "success")


class DownsampleForGatesTests(unittest.TestCase):
    def test_alpha_channel_is_trimmed(self):
        """4 通道输入必须裁到 3 通道，否则成对门禁 shape 不匹配。"""
        rgba = np.zeros((64, 80, 4), dtype=np.float32)
        rgba[..., :3] = 0.1
        rgba[..., 3] = 1.0
        out = downsample_for_gates(rgba)
        self.assertEqual(out.shape, (64, 80, 3))

    def test_large_image_is_bounded_and_pairs_match(self):
        big = np.full((1000, 2000, 3), 0.05, dtype=np.float32)
        small = downsample_for_gates(big)
        self.assertLessEqual(max(small.shape[:2]), GATE_PREVIEW_MAX_SIDE)
        # 同一函数处理两张同尺寸图，结果 shape 必须一致（成对门禁的前提）
        self.assertEqual(small.shape, downsample_for_gates(big.copy()).shape)

    def test_grayscale_is_promoted_to_rgb(self):
        gray = np.full((40, 40), 0.2, dtype=np.float32)
        self.assertEqual(downsample_for_gates(gray).shape, (40, 40, 3))

    def test_small_image_is_untouched(self):
        small = np.full((32, 48, 3), 0.3, dtype=np.float32)
        out = downsample_for_gates(small)
        self.assertTrue(np.array_equal(out, small))


class GateResultShapeTests(unittest.TestCase):
    def test_result_is_serialisable_and_has_schema(self):
        image, _ = make_star_field()
        rgb = to_rgb(image)
        result = evaluate_star_artifact_gates(rgb, rgb.copy(), steps=["stretch"])
        self.assertEqual(result["schema"], "artifact_gates/1.0")
        self.assertIn(result["status"], ("success", "review_required"))
        self.assertIsInstance(result["gates"], list)
        self.assertTrue(all("code" in g for g in result["gates"]))


if __name__ == "__main__":
    unittest.main()
