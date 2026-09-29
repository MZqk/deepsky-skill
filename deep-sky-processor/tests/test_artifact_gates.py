"""具名星点伪影门禁（artifact_gates）测试。

合成星场验证：暗环 / 胀星 / 星点层损失与暗坑 / 死白核心 / 步骤感知
跳过 / 星系缩星上限 / review bundle 集成。
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imsave

import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from agent_protocol import create_review_bundle
from artifact_gates import (
    check_core_burning,
    check_ringing,
    check_star_bloat,
    check_star_layer_integrity,
    evaluate_star_artifact_gates,
)
from pipeline import apply_target_aware_safety_rules


FWHM = 3.0
SIGMA = FWHM / 2.355
GRID_STEP = 34          # 抖动网格步长：保证星间距 ≥20px，
JITTER = 6              # 使 estimate_fwhm 的 17×17 拟合窗口内无邻星翼部


def make_star_field(height=192, width=256, n_stars=36, seed=42,
                    background=0.05, brightness=(0.2, 0.9),
                    positions=None, enlarge_sigma=1.0):
    """合成星场：抖动网格布星，返回 (image, star_list[(cy, cx, amp)])。

    星间距 ≥ GRID_STEP-2·JITTER，estimate_fwhm 的拟合窗口（±8px）
    内不会混入邻星，FWHM 估计稳定在 ~3px。enlarge_sigma>1 时按比例
    放大星点 σ（用于胀星用例）。
    """
    rng = np.random.default_rng(seed)
    image = np.full((height, width), background, dtype=np.float32)
    yy, xx = np.mgrid[:height, :width]
    sigma = SIGMA * enlarge_sigma
    if positions is None:
        slots = [
            (int(margin_y + row * GRID_STEP + rng.integers(-JITTER, JITTER + 1)),
             int(margin_x + col * GRID_STEP + rng.integers(-JITTER, JITTER + 1)))
            for row, margin_y in enumerate([20] * ((height - 40) // GRID_STEP + 1))
            for col, margin_x in enumerate([20] * ((width - 40) // GRID_STEP + 1))
            if margin_y + row * GRID_STEP < height - 20
            and margin_x + col * GRID_STEP < width - 20
        ]
        rng.shuffle(slots)
        positions = slots[:n_stars]
    stars = []
    for cy, cx in positions:
        amp = float(rng.uniform(*brightness))
        image += amp * np.exp(
            -((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * sigma ** 2))
        stars.append((cy, cx, amp))
    return image, stars


def add_dark_ring(image, cy, cx, depth=0.015, radius=1.7 * FWHM,
                  width=0.55 * FWHM):
    yy, xx = np.mgrid[:image.shape[0], :image.shape[1]]
    dist = np.hypot(yy - cy, xx - cx)
    ring = np.exp(-((dist - radius) / width) ** 2)
    return image - depth * ring


def to_rgb(gray):
    return np.clip(np.stack([gray] * 3, axis=-1), 0, 1)


class StarFieldHelper(unittest.TestCase):
    def setUp(self):
        self.reference, self.stars = make_star_field()


class CheckRingingTests(StarFieldHelper):
    def test_clean_stars_pass(self):
        gate = check_ringing(to_rgb(self.reference))
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["value"]["n_ring"], 0)

    def test_dark_rings_detected(self):
        candidate = self.reference.copy()
        n_ringed = 0
        for cy, cx, amp in self.stars:
            if amp >= 0.5 and n_ringed < 8:
                candidate = add_dark_ring(candidate, cy, cx)
                n_ringed += 1
        self.assertGreaterEqual(n_ringed, 4)
        gate = check_ringing(to_rgb(candidate))
        self.assertEqual(gate["status"], "failed")
        self.assertGreaterEqual(gate["value"]["n_ring"], 2)
        evidence = gate["evidence"]["ringing_stars"]
        self.assertTrue(evidence)
        self.assertIn("radius_px", evidence[0])
        self.assertIn("疑似", gate["message"])

    def test_insufficient_stars_skipped(self):
        blank = np.full((60, 80), 0.05, dtype=np.float32)
        gate = check_ringing(to_rgb(blank))
        self.assertEqual(gate["status"], "skipped")


class CheckStarBloatTests(StarFieldHelper):
    def test_unchanged_passes(self):
        gate = check_star_bloat(
            to_rgb(self.reference), to_rgb(self.reference.copy()))
        self.assertEqual(gate["status"], "passed")
        self.assertLess(gate["value"]["median_ratio"], 1.2)

    def test_enlarged_stars_fail(self):
        # 相同星位、σ 放大 1.8 倍 → 成对 FWHM 比 ≈ 1.8 > 1.50
        candidate, _ = make_star_field(
            positions=[(cy, cx) for cy, cx, _ in self.stars],
            n_stars=len(self.stars), seed=123,
            enlarge_sigma=1.8)
        gate = check_star_bloat(to_rgb(self.reference), to_rgb(candidate))
        self.assertEqual(gate["status"], "failed")
        self.assertGreater(gate["value"]["median_ratio"], 1.5)


class CheckStarLayerIntegrityTests(StarFieldHelper):
    def test_unchanged_passes(self):
        gates = check_star_layer_integrity(
            to_rgb(self.reference), to_rgb(self.reference.copy()))
        codes = {g["code"]: g for g in gates}
        self.assertEqual(codes["STAR_LAYER_LOSS"]["status"], "passed")
        self.assertEqual(codes["STAR_HOLES"]["status"], "passed")

    def test_star_removal_intended_skips(self):
        starless = np.full_like(self.reference, 0.05)
        gates = check_star_layer_integrity(
            to_rgb(self.reference), to_rgb(starless),
            star_removal_intended=True)
        for gate in gates:
            self.assertEqual(gate["status"], "skipped")

    def test_star_loss_detected(self):
        starless = np.full_like(self.reference, 0.05)
        gates = check_star_layer_integrity(
            to_rgb(self.reference), to_rgb(starless))
        codes = {g["code"]: g for g in gates}
        self.assertEqual(codes["STAR_LAYER_LOSS"]["status"], "failed")
        self.assertLess(
            codes["STAR_LAYER_LOSS"]["value"]["retention_median"], 0.55)

    def test_dark_holes_detected(self):
        candidate = self.reference.copy()
        n_holed = 0
        for cy, cx, amp in self.stars:
            if amp >= 0.55 and n_holed < 6:
                candidate[cy - 2:cy + 3, cx - 2:cx + 3] = 0.01
                n_holed += 1
        self.assertGreaterEqual(n_holed, 3)
        gates = check_star_layer_integrity(
            to_rgb(self.reference), to_rgb(candidate))
        codes = {g["code"]: g for g in gates}
        self.assertIn(codes["STAR_HOLES"]["status"], ("failed", "warning"))
        self.assertGreaterEqual(codes["STAR_HOLES"]["value"]["n_holes"], 1)
        self.assertTrue(codes["STAR_HOLES"]["evidence"]["holes"])

    def test_star_reduce_step_loss_does_not_escalate(self):
        starless = np.full_like(self.reference, 0.05)
        gates = check_star_layer_integrity(
            to_rgb(self.reference), to_rgb(starless),
            star_reduction_step=True)
        codes = {g["code"]: g for g in gates}
        self.assertEqual(codes["STAR_LAYER_LOSS"]["status"], "failed")
        self.assertFalse(codes["STAR_LAYER_LOSS"]["escalate"])


class CheckCoreBurningTests(StarFieldHelper):
    def _burn_disc(self, image, cy, cx, radius=4, value=1.0):
        yy, xx = np.mgrid[:image.shape[0], :image.shape[1]]
        disc = (yy - cy) ** 2 + (xx - cx) ** 2 <= radius ** 2
        image[disc] = value
        return image

    def test_no_new_burn_with_reference(self):
        candidate = self.reference.copy()
        gate = check_core_burning(
            to_rgb(candidate), reference=to_rgb(self.reference.copy()))
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["value"]["n_new_star_cores"], 0)

    def test_preexisting_saturation_not_blamed(self):
        reference = self._burn_disc(
            self.reference.copy(), self.stars[0][0], self.stars[0][1])
        candidate = reference.copy()
        gate = check_core_burning(
            to_rgb(candidate), reference=to_rgb(reference))
        self.assertEqual(gate["status"], "passed")

    def test_new_burnt_cores_fail(self):
        candidate = self.reference.copy()
        for cy, cx, _amp in self.stars[:3]:
            candidate = self._burn_disc(candidate, cy, cx, radius=4)
        gate = check_core_burning(
            to_rgb(candidate), reference=to_rgb(self.reference.copy()))
        self.assertEqual(gate["status"], "failed")
        self.assertEqual(gate["value"]["n_new_star_cores"], 3)
        self.assertTrue(gate["evidence"]["new_star_cores"])

    def test_absolute_mode_records_without_escalation(self):
        candidate = self.reference.copy()
        for cy, cx, _amp in self.stars[:2]:
            candidate = self._burn_disc(candidate, cy, cx, radius=5)
        gate = check_core_burning(to_rgb(candidate))
        self.assertFalse(gate["escalate"])
        self.assertGreaterEqual(gate["value"]["n_star_cores"], 1)


class EvaluateAggregatorTests(StarFieldHelper):
    def test_clean_candidate_success(self):
        result = evaluate_star_artifact_gates(
            to_rgb(self.reference), to_rgb(self.reference.copy()))
        self.assertEqual(result["status"], "success")
        codes = {g["code"] for g in result["gates"]}
        self.assertEqual(
            codes,
            {"STAR_RINGING", "STAR_BLOAT", "STAR_LAYER_LOSS",
             "STAR_HOLES", "CORE_BURNING"})

    def test_ringed_candidate_requires_review(self):
        candidate = self.reference.copy()
        for cy, cx, amp in self.stars[:8]:
            if amp >= 0.5:
                candidate = add_dark_ring(candidate, cy, cx)
        result = evaluate_star_artifact_gates(
            to_rgb(self.reference), to_rgb(candidate))
        self.assertEqual(result["status"], "review_required")
        self.assertIn("STAR_RINGING", result["summary"])

    def test_star_removal_steps_skip_layer_gates(self):
        starless = np.full_like(self.reference, 0.05)
        result = evaluate_star_artifact_gates(
            to_rgb(self.reference), to_rgb(starless),
            steps=["star_remove"])
        layer_gates = {
            g["code"]: g for g in result["gates"]
            if g["code"] in ("STAR_LAYER_LOSS", "STAR_HOLES")}
        for gate in layer_gates.values():
            self.assertEqual(gate["status"], "skipped")

    def test_reference_none_runs_candidate_side_only(self):
        result = evaluate_star_artifact_gates(
            None, to_rgb(self.reference.copy()))
        codes = {g["code"] for g in result["gates"]}
        self.assertEqual(codes, {"STAR_RINGING", "CORE_BURNING"})
        self.assertEqual(result["status"], "success")


class GalaxyStarReductionCapTests(unittest.TestCase):
    def test_galaxy_caps_star_reduction(self):
        cfg, _steps, log = apply_target_aware_safety_rules(
            {"star_reduction": 0.5, "hdr_strength": 0.4,
             "sharpen_amount": 1.3},
            ["star_reduce"], "galaxy", None,
        )
        self.assertEqual(cfg["star_reduction"], 0.25)
        self.assertTrue(any("0.25" in entry for entry in log))

    def test_galaxy_keeps_mild_reduction(self):
        cfg, _steps, _log = apply_target_aware_safety_rules(
            {"star_reduction": 0.18}, ["star_reduce"], "galaxy", None)
        self.assertEqual(cfg["star_reduction"], 0.18)

    def test_non_galaxy_not_capped(self):
        cfg, _steps, _log = apply_target_aware_safety_rules(
            {"star_reduction": 0.4}, ["star_reduce"],
            "emission_nebula", None)
        self.assertEqual(cfg["star_reduction"], 0.4)


class ReviewBundleIntegrationTests(StarFieldHelper):
    def _write(self, path, gray):
        imsave(path, (np.clip(gray, 0, 1) * 255).astype(np.uint8))

    def test_bundle_contains_star_artifact_gates(self):
        with tempfile.TemporaryDirectory() as td:
            before = Path(td) / "before.png"
            after = Path(td) / "after.png"
            review_dir = Path(td) / "review"
            self._write(before, self.reference)
            self._write(after, self.reference.copy())

            payload = create_review_bundle(
                before, after, review_dir,
                context={"target_type": "emission_nebula",
                         "steps": ["stretch"]},
            )
            self.assertIn("star_artifact_gates", payload)
            self.assertEqual(
                payload["star_artifact_gates"]["status"], "success")
            report = json.loads(
                Path(payload["report_path"]).read_text(encoding="utf-8"))
            self.assertIn("star_artifact_gates", report)

    def test_bundle_escalates_on_star_artifact(self):
        candidate = self.reference.copy()
        n_ringed = 0
        for cy, cx, amp in self.stars:
            if amp >= 0.5 and n_ringed < 8:
                candidate = add_dark_ring(candidate, cy, cx)
                n_ringed += 1
        with tempfile.TemporaryDirectory() as td:
            before = Path(td) / "before.png"
            after = Path(td) / "after.png"
            review_dir = Path(td) / "review"
            self._write(before, self.reference)
            self._write(after, candidate)

            payload = create_review_bundle(
                before, after, review_dir,
                context={"target_type": "emission_nebula",
                         "steps": ["sharpen"]},
            )
            self.assertEqual(
                payload["star_artifact_gates"]["status"],
                "review_required")
            self.assertEqual(payload["status"], "review_required")


if __name__ == "__main__":
    unittest.main()
