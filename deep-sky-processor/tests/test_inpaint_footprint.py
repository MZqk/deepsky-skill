"""INPAINT_FOOTPRINT：去星修补足迹门禁。

机理：线性域形态学去星用 inpaint 填掉星点像素。修复半径或星点蒙版失配时，
星位会留下与邻域不一致的补丁。线性域里只有 ~1e-4 量级、看不出来；但极暗母版
的拉伸增益可达 ~270×，补丁随之变成肉眼可见的暗斑/亮斑/过平滑区。实测该缺陷
此前完全没有任何门禁覆盖。

归属：**不**进 evaluate_star_artifact_gates 聚合器（那个聚合器的 code 集合被
tests/test_artifact_gates.py:230-234 精确断言为 5 个），走 evaluate_quality_gates
的标量路径。
"""

import sys
import unittest
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

from artifact_gates import (  # noqa: E402
    check_inpaint_footprint,
    evaluate_star_artifact_gates,
)
from agent_protocol import evaluate_quality_gates  # noqa: E402


FWHM = 3.0


def _scene(n_stars=24, height=256, width=320, seed=5):
    """背景 + 星云坡度 + 稀疏星位掩膜，返回 (gray, star_mask, star_positions)。"""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width]
    base = 0.10 + 0.06 * (yy / height)          # 低频坡度，用于检验残差去背景
    base = base + rng.normal(0, 1e-3, base.shape)
    gray = base.astype(np.float32)
    mask = np.zeros((height, width), dtype=np.float32)

    positions = []
    for i in range(n_stars):
        cy = int(40 + (i % 6) * 34)
        cx = int(40 + (i // 6) * 46)
        if cy + 4 >= height or cx + 4 >= width:
            continue
        mask[cy - 2:cy + 3, cx - 2:cx + 3] = 1.0
        positions.append((cy, cx))
    return gray, mask, positions


def _inject_footprint(gray, positions, offset):
    """把星位区域替换成**平滑**补丁（= inpaint 的物理行为）。

    inpaint 用插值填洞，产出的是**无纹理**的平滑块。只做均匀偏移而不消除纹理
    是不真实的——那种补丁的 std 与邻域相同，纹理判据根本不该触发。
    """
    for cy, cx in positions:
        patch = gray[cy - 2:cy + 3, cx - 2:cx + 3]
        gray[cy - 2:cy + 3, cx - 2:cx + 3] = float(np.median(patch)) + offset
    return gray


class InpaintFootprintTests(unittest.TestCase):
    def test_clean_starless_layer_passes(self):
        gray, mask, _ = _scene()
        gate = check_inpaint_footprint(gray, mask, fwhm=FWHM)
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["value"]["n_flagged"], 0)
        self.assertGreaterEqual(gate["value"]["n_sampled"], 5)
        self.assertFalse(gate["escalate"])

    def test_dark_footprint_is_detected_with_coordinates(self):
        gray, mask, positions = _scene()
        _inject_footprint(gray, positions[:8], -0.05)
        gate = check_inpaint_footprint(gray, mask, fwhm=FWHM)
        self.assertIn(gate["status"], ("warning", "failed"))
        self.assertGreaterEqual(gate["value"]["n_dark"], 1)
        footprints = gate["evidence"]["footprints"]
        self.assertTrue(footprints)
        self.assertIn("center_rc", footprints[0])
        self.assertIn("texture_ratio", footprints[0])
        self.assertEqual(footprints[0]["kind"], "dark")
        self.assertEqual(gate["evidence"]["coordinate_convention"], "row_col")

    def test_bright_footprint_is_detected(self):
        gray, mask, positions = _scene()
        _inject_footprint(gray, positions[:8], +0.06)
        gate = check_inpaint_footprint(gray, mask, fwhm=FWHM)
        self.assertIn(gate["status"], ("warning", "failed"))
        self.assertGreaterEqual(gate["value"]["n_bright"], 1)

    def test_uniform_offset_without_texture_loss_is_not_flagged(self):
        """关键反例：只偏移亮度但**保留纹理**的补丁不应被判为足迹。

        这条锁住「联合判据」的设计——单看亮度落差会大量误报（实测 63%）。
        """
        gray, mask, positions = _scene()
        for cy, cx in positions[:8]:
            gray[cy - 2:cy + 3, cx - 2:cx + 3] -= 0.06   # 均匀偏移，纹理保留
        gate = check_inpaint_footprint(gray, mask, fwhm=FWHM)
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["value"]["n_flagged"], 0)

    def test_insufficient_samples_skipped(self):
        gray, mask, _ = _scene(n_stars=2)
        gate = check_inpaint_footprint(gray, mask, fwhm=FWHM)
        self.assertEqual(gate["status"], "skipped")

    def test_shape_mismatch_skipped(self):
        gray, _, _ = _scene()
        bad_mask = np.zeros((10, 10), dtype=np.float32)
        gate = check_inpaint_footprint(gray, bad_mask, fwhm=FWHM)
        self.assertEqual(gate["status"], "skipped")

    def test_not_part_of_aggregator(self):
        """INPAINT_FOOTPRINT 必须留在聚合器之外。"""
        gray, _, _ = _scene()
        rgb = np.stack([gray] * 3, axis=-1)
        result = evaluate_star_artifact_gates(rgb, rgb.copy(), steps=["stretch"])
        codes = {g["code"] for g in result["gates"]}
        self.assertNotIn("INPAINT_FOOTPRINT", codes)


class FootprintGateWiringTests(unittest.TestCase):
    def _metrics(self, footprint):
        return {
            "processing_stage": "final",
            "median": 0.10,
            "p1": 0.01,
            "nonpositive_pixel_ratio": 0.0,
            "corner_uniformity_ratio": 1.2,
            "uniform_5x5_dark_patch_ratio": 0.05,
            "star_area_ratio": 0.02,
            "inpaint_footprint": footprint,
        }

    def test_passed_footprint_is_not_appended(self):
        status, gates = evaluate_quality_gates(
            self._metrics({"status": "passed", "message": "ok"}),
            target_type="emission_nebula",
        )
        self.assertNotIn("INPAINT_FOOTPRINT", [g["code"] for g in gates])
        self.assertEqual(status, "success")

    def test_skipped_and_error_are_not_appended(self):
        for st in ("skipped", "error"):
            _, gates = evaluate_quality_gates(
                self._metrics({"status": st, "message": "n/a"}),
                target_type="emission_nebula",
            )
            self.assertNotIn("INPAINT_FOOTPRINT", [g["code"] for g in gates])

    def test_warning_is_recorded_without_escalating(self):
        status, gates = evaluate_quality_gates(
            self._metrics({
                "status": "warning",
                "value": {"n_flagged": 1, "n_sampled": 30},
                "threshold": "flagged<max(2, 8%×n_sampled)",
                "message": "1/30 处检出修补足迹",
            }),
            target_type="emission_nebula",
        )
        gate = next(g for g in gates if g["code"] == "INPAINT_FOOTPRINT")
        self.assertEqual(gate["status"], "warning")
        self.assertFalse(gate["escalate"])
        self.assertEqual(status, "success")

    def test_failed_escalates_to_review_required(self):
        status, gates = evaluate_quality_gates(
            self._metrics({
                "status": "failed",
                "value": {"n_flagged": 5, "n_sampled": 30},
                "threshold": "flagged<max(2, 8%×n_sampled)",
                "message": "5/30 处检出修补足迹",
            }),
            target_type="emission_nebula",
        )
        gate = next(g for g in gates if g["code"] == "INPAINT_FOOTPRINT")
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["escalate"])
        self.assertEqual(status, "review_required")


if __name__ == "__main__":
    unittest.main()
