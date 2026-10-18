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
    _estimate_fwhm_or_default,
    _image_megapixels,
    _scaled_area_threshold,
    check_channel_clipping,
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


def make_filament_field():
    """Curved bright filaments raise the outer annuli near stellar positions."""
    image, stars = make_star_field(brightness=(0.25, 0.65))
    yy, xx = np.mgrid[:image.shape[0], :image.shape[1]]
    for cy, cx, _ in stars:
        dy, dx = yy - cy, xx - cx
        image += (0.06 * (1 - np.exp(-((dx + 0.025 * dy * dy) / 6) ** 2))
                  * np.exp(-(dx * dx + dy * dy) / (2 * 18 ** 2)))
    return image, stars


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


class PairedDeficitTests(unittest.TestCase):
    def test_gate_scale_tracks_stellar_cores_on_filaments_and_broad_stars(self):
        image, _ = make_filament_field()
        fwhm, n_used = _estimate_fwhm_or_default(image)
        self.assertGreaterEqual(n_used, 3)
        self.assertAlmostEqual(fwhm, FWHM, delta=0.8)
        for width in (6, 10):
            with self.subTest(width=width):
                image, _ = make_star_field(brightness=(0.4, 0.7), enlarge_sigma=width / FWHM)
                fwhm, n_used = _estimate_fwhm_or_default(image)
                self.assertGreaterEqual(n_used, 3)
                self.assertAlmostEqual(fwhm, width, delta=1.0)

    def test_unchanged_filaments_do_not_escalate(self):
        reference, _ = make_filament_field()
        absolute = check_ringing(to_rgb(reference), fwhm=FWHM)
        self.assertTrue(absolute["escalate"])
        self.assertGreater(absolute["value"]["n_uncertain"], 0)
        result = evaluate_star_artifact_gates(to_rgb(reference), to_rgb(reference.copy()))
        self.assertEqual(result["status"], "success")
        gates = {g["code"]: g for g in result["gates"]}
        self.assertFalse(gates["STAR_RINGING"]["escalate"])
        self.assertGreater(gates["STAR_RINGING"]["value"]["n_preexisting"], 0)
        self.assertFalse(gates["STAR_HOLES"]["escalate"])

    def test_new_rings_on_filaments_still_fail(self):
        reference, stars = make_filament_field()
        candidate = reference.copy()
        for cy, cx, _ in stars[:8]:
            candidate = add_dark_ring(candidate, cy, cx, depth=0.03)
        gate = check_ringing(to_rgb(candidate), fwhm=FWHM, reference=to_rgb(reference))
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["escalate"])
        self.assertGreaterEqual(gate["value"]["n_ring"], 2)
        self.assertGreater(gate["evidence"]["ringing_stars"][0]["added_dip_abs"], 0.003)
        result = evaluate_star_artifact_gates(to_rgb(reference), to_rgb(candidate))
        self.assertEqual(result["status"], "review_required")
        self.assertIn("STAR_RINGING", result["summary"])

    def test_new_holes_on_filaments_still_fail(self):
        reference, stars = make_filament_field()
        candidate = reference.copy()
        for cy, cx, _ in stars[:8]:
            candidate[cy - 2:cy + 3, cx - 2:cx + 3] = 0.01
        gate = check_star_layer_integrity(to_rgb(reference), to_rgb(candidate), fwhm=FWHM)[1]
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["escalate"])
        self.assertGreaterEqual(gate["value"]["n_holes"], 2)
        self.assertGreater(gate["evidence"]["holes"][0]["added_dip_abs"], 0.003)

    def test_preexisting_rings_are_diagnostic_but_deepening_fails(self):
        reference, stars = make_star_field(brightness=(0.4, 0.7))
        for cy, cx, _ in stars[:8]:
            reference = add_dark_ring(reference, cy, cx, depth=0.01)
        gate = check_ringing(to_rgb(reference), fwhm=FWHM, reference=to_rgb(reference.copy()))
        self.assertFalse(gate["escalate"])
        self.assertGreaterEqual(gate["value"]["n_preexisting"], 2)
        self.assertEqual(gate["value"]["n_ring"], 0)
        candidate = reference.copy()
        for cy, cx, _ in stars[:8]:
            candidate = add_dark_ring(candidate, cy, cx, depth=0.015)
        gate = check_ringing(to_rgb(candidate), fwhm=FWHM, reference=to_rgb(reference))
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["escalate"])

    def test_preexisting_holes_are_diagnostic_but_deepening_fails(self):
        reference, stars = make_star_field(brightness=(0.4, 0.7))
        for cy, cx, _ in stars[:8]:
            reference[cy - 2:cy + 3, cx - 2:cx + 3] = 0.003
            reference[cy, cx] = 0.3
        gate = check_star_layer_integrity(to_rgb(reference), to_rgb(reference.copy()), fwhm=FWHM)[1]
        self.assertFalse(gate["escalate"])
        self.assertGreaterEqual(gate["value"]["n_preexisting"], 2)
        candidate = reference.copy()
        for cy, cx, _ in stars[:8]:
            candidate[cy, cx] = 0.01
        gate = check_star_layer_integrity(to_rgb(reference), to_rgb(candidate), fwhm=FWHM)[1]
        self.assertEqual(gate["status"], "failed")
        self.assertTrue(gate["escalate"])

    def test_unreliable_changed_background_requires_review(self):
        reference, _ = make_filament_field()
        yy, xx = np.mgrid[:reference.shape[0], :reference.shape[1]]
        candidate = reference + 0.08 * np.sin((xx + yy) / 6) ** 2
        gate = check_ringing(to_rgb(candidate), fwhm=FWHM, reference=to_rgb(reference))
        self.assertEqual(gate["status"], "warning")
        self.assertTrue(gate["escalate"])
        self.assertGreater(gate["value"]["n_uncertain"], 0)
        self.assertTrue(gate["evidence"]["uncertain_stars"])

    def test_linear_gradient_is_removed_by_local_background(self):
        reference, _ = make_star_field(brightness=(0.25, 0.65))
        yy, xx = np.mgrid[:reference.shape[0], :reference.shape[1]]
        candidate = reference + 0.0003 * xx + 0.0002 * yy
        gate = check_ringing(to_rgb(candidate), fwhm=FWHM, reference=to_rgb(reference))
        self.assertEqual(gate["status"], "passed")
        self.assertEqual(gate["value"]["n_ring"], 0)

    def test_noncomparable_domain_never_suppresses_deficits_as_preexisting(self):
        reference, _ = make_filament_field()
        result = evaluate_star_artifact_gates(
            to_rgb(reference), to_rgb(reference.copy()), reference_comparable=False)
        self.assertEqual(result["status"], "review_required")
        gates = {g["code"]: g for g in result["gates"]}
        for code in ("STAR_RINGING", "STAR_HOLES"):
            self.assertTrue(gates[code]["escalate"])
            self.assertEqual(gates[code]["value"]["n_preexisting"], 0)
            self.assertFalse(gates[code]["evidence"]["reference_comparable"])

    def test_edge_stars_are_excluded_from_sample_denominator(self):
        image, _ = make_star_field(height=120, width=160, brightness=(0.4, 0.7),
                                  positions=[(12, 12), (12, 60), (12, 110),
                                             (50, 50), (50, 100), (80, 70)])
        gate = check_ringing(to_rgb(image), fwhm=FWHM, reference=to_rgb(image.copy()))
        self.assertEqual(gate["value"]["n_detected"], 6)
        self.assertEqual(gate["value"]["n_sampled"], 3)
        self.assertEqual(gate["status"], "passed")


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

    def test_bundle_rejects_domain_or_geometry_mismatch_for_attribution(self):
        with tempfile.TemporaryDirectory() as td:
            before, after = Path(td) / "before.png", Path(td) / "after.png"
            self._write(before, self.reference)
            self._write(after, self.reference.copy())
            payload = create_review_bundle(
                before, after, Path(td) / "domain_review",
                context={"input_is_linear": True, "output_is_linear": False},
            )
            self.assertEqual(payload["star_artifact_gates"]["status"], "review_required")
            self.assertFalse(payload["star_artifact_gates"]["context"]["reference_comparable"])
            self._write(after, self.reference[:-5, :-5])
            payload = create_review_bundle(before, after, Path(td) / "geometry_review")
            self.assertEqual(payload["star_artifact_gates"]["status"], "review_required")
            self.assertFalse(payload["star_artifact_gates"]["context"]["reference_comparable"])

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


class ClippingGateResolutionTests(unittest.TestCase):
    """面积门限必须按分辨率缩放，否则门禁「严不严取决于图像多大」。

    实测 NGC6888：同一份数据、同一份代码，1/1 分辨率 R 新增区域 7 处判 failed，
    降到 1/2 就变 0 处 passed（连通域被降采样打断）。根因是绝对像素阈值
    `BURN_MIN_AREA_PX=25` / `BURN_BIG_AREA_PX=80`。
    """

    def test_threshold_scales_with_pixel_count(self):
        reference = 8.3
        self.assertAlmostEqual(
            _scaled_area_threshold(25, reference), 25.0, places=4)
        # 像素数降到 1/4 → 面积门限也降到 1/4
        self.assertAlmostEqual(
            _scaled_area_threshold(25, reference / 4.0), 25.0 / 4.0, places=4)
        # 像素数升到 2 倍 → 面积门限升到 2 倍
        self.assertAlmostEqual(
            _scaled_area_threshold(25, reference * 2.0), 50.0, places=4)

    def test_at_reference_resolution_keeps_legacy_thresholds(self):
        """基准分辨率（≈8.3MP，即 3840×2160）保持历史阈值不变。"""
        self.assertAlmostEqual(
            _scaled_area_threshold(25, 8.3), 25.0, places=4)
        self.assertAlmostEqual(
            _scaled_area_threshold(80, 8.3), 80.0, places=4)

    def test_smaller_images_get_proportionally_looser_thresholds(self):
        """小图按像素数等比放宽 —— 绝对阈值会让小图门禁过严。

        注意方向：低分辨率下同一伪影的连通域像素数更少，阈值须同步下调，
        否则「门禁严不严取决于图像多大」。
        """
        self.assertLess(_scaled_area_threshold(25, 2.07), 25.0)
        self.assertAlmostEqual(
            _scaled_area_threshold(25, 2.07), 25.0 * 2.07 / 8.3, places=4)

    def test_unknown_size_falls_back_to_legacy_threshold(self):
        for size in (None, (0, 0), (10,)):
            self.assertEqual(_scaled_area_threshold(25, _image_megapixels(size)), 25.0)

    def test_tiny_image_never_collapses_threshold_below_noise_floor(self):
        """极小图不得把门限缩到 1px —— 否则单个采样噪点都算可见伪影。

        实测 64×64 图上 49 个 1px² 噪点会被判 failed。
        """
        from artifact_gates import BURN_FLOOR_AREA_PX
        self.assertGreaterEqual(BURN_FLOOR_AREA_PX, 2.0)
        self.assertEqual(_scaled_area_threshold(25, 0.004), BURN_FLOOR_AREA_PX)
        self.assertEqual(_scaled_area_threshold(80, 0.004), BURN_FLOOR_AREA_PX)

    def test_single_pixel_noise_is_never_an_artifact(self):
        """1px² 的孤立饱和点不构成可见伪影，任何分辨率下都不该计入。"""
        from artifact_gates import BURN_FLOOR_AREA_PX
        for size in (64, 256, 1920):
            before = np.full((size, size, 3), 0.1, np.float32)
            specks = before.copy()
            specks[::10, ::10, 0] = 1.0
            self.assertEqual(
                check_channel_clipping(specks, before)["status"], "passed",
                msg=f"{size}px 图上的 1px 噪点被误判为伪影")

    def test_same_artifact_verdict_across_resolutions(self):
        """同一物理伪影在不同分辨率下判定应一致。"""
        rng = np.random.default_rng(5)
        base = np.full((960, 1280, 3), 0.04, np.float32)
        yy, xx = np.mgrid[:960, :1280]
        # 一块明确的死白斑（各分辨率下尺寸按像素数等比）
        verdicts = []
        for scale in (1, 2, 4):
            size = max(1, 64 // scale)
            h, w = 960 // scale, 1280 // scale
            yy2, xx2 = np.mgrid[:h, :w]
            patch = np.zeros((h, w, 3), np.float32)
            cy, cx = h // 2, w // 2
            patch[cy - size // 2:cy + size // 2, cx - size // 2:cx + size // 2] = 1.0
            small = np.full((h, w, 3), 0.04, np.float32) + patch
            gate = check_channel_clipping(small)
            verdicts.append(gate["status"])
            self.assertGreater(
                gate["value"]["r"]["n_regions"], 0,
                msg=f"scale={scale} 应检出到死白斑")
        self.assertEqual(len(set(verdicts)), 1,
                         f"同伪影在 1/1、1/2、1/4 下判定不一致: {verdicts}")


if __name__ == "__main__":
    unittest.main()
