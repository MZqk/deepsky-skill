import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from skimage.io import imsave

import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pipeline


class StarStepDependencyTests(unittest.TestCase):
    def test_generic_stretch_factor_maps_to_safe_ghs_range(self):
        mapped = pipeline.resolve_ghs_b({"stretch_factor": 100})
        self.assertGreaterEqual(mapped, 4.0)
        self.assertLessEqual(mapped, 12.0)
        self.assertEqual(pipeline.resolve_ghs_b({"ghs_b": 7.5}), 7.5)

    def test_star_process_expands_full_recomposition_chain(self):
        steps, log = pipeline.resolve_step_dependencies(["star_process"])
        self.assertEqual(
            steps,
            ["star_remove", "stretch", "star_process", "star_combine"],
        )
        self.assertTrue(log)

    def test_star_combine_expands_full_recomposition_chain(self):
        steps, log = pipeline.resolve_step_dependencies(["star_combine"])
        self.assertEqual(
            steps,
            ["star_remove", "stretch", "star_process", "star_combine"],
        )
        self.assertTrue(log)

    def test_pipeline_star_process_creates_processed_and_combined_artifacts(self):
        image = np.full((48, 64, 3), 0.03, dtype=np.float32)
        image[18:31, 24:41, 0] += 0.12
        stars = np.zeros_like(image)
        stars[10:13, 10:13] = 0.8
        stars[34:37, 50:53] = 0.9
        starless = np.clip(image - stars, 0, 1)
        star_mask = np.max(stars, axis=2)
        report = {
            "fallback_applied": False,
            "repair_quality_score": 0.9,
            "repair_method": "test",
            "estimated_fwhm": 3.0,
            "n_components_total": 2,
        }

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            work_dir = Path(td) / "work"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))

            with (
                patch(
                    "pipeline.separate_stars",
                    return_value=(starless, stars, star_mask, report),
                ),
                patch(
                    "pipeline.detect_stars",
                    return_value=(star_mask > 0).astype(np.float32),
                ),
            ):
                result = pipeline.run_pipeline(
                    str(source),
                    str(output),
                    steps="star_process",
                    preset="light",
                    keep_all=True,
                    work_dir=str(work_dir),
                )

            self.assertEqual(
                result["effective_config"]["steps"],
                ["star_remove", "stretch", "star_process", "star_combine"],
            )
            for artifact in (
                "04_starless_linear.tif",
                "04_stars_linear.tif",
                "05a_star_stretch.tif",
                "05b_star_scnr.tif",
                "05c_star_final.tif",
                "09_star_combined.tif",
            ):
                self.assertIn(artifact, result["artifacts"])
                self.assertTrue(Path(result["artifacts"][artifact]).exists())
            self.assertTrue(result["step_dependencies_applied"])

    def test_star_cluster_safety_removes_full_star_chain(self):
        cfg, steps, log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["light"],
            ["star_remove", "stretch", "star_process", "star_combine"],
            "globular_cluster",
            "M13",
        )
        self.assertNotIn("star_remove", steps)
        self.assertNotIn("star_process", steps)
        self.assertNotIn("star_combine", steps)
        self.assertIn("stretch", steps)
        self.assertTrue(log)

    def test_m42_single_stretch_owner_at_medium(self):
        """M42 的拉伸只能乘一次 ×0.75，不得再叠加发射星云的 ×0.85。"""
        cfg, _steps, log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["medium"],
            ["stretch"],
            "emission_nebula",
            "M42",
        )
        self.assertAlmostEqual(cfg["stretch_factor"], 33.75)   # 45 × 0.75
        self.assertAlmostEqual(cfg["sharpen_amount"], 0.91)    # 发射星云 ×0.7 仍生效
        self.assertAlmostEqual(cfg["hdr_strength"], 0.65)
        self.assertAlmostEqual(cfg["target_bg"], 0.10)
        self.assertFalse(any("×0.85" in line for line in log))

    def test_spaced_name_triggers_m42_rule(self):
        """'M 42' 这类带空格的写法必须与 'M42' 等价。"""
        cfg, _steps, _log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["medium"], ["stretch"], None, "M 42"
        )
        self.assertAlmostEqual(cfg["stretch_factor"], 33.75)

    def test_non_m42_emission_uses_085(self):
        cfg, _steps, log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["medium"],
            ["stretch"],
            "emission_nebula",
            "NGC7000",
        )
        self.assertAlmostEqual(cfg["stretch_factor"], 38.25)   # 45 × 0.85
        self.assertTrue(any("×0.85" in line for line in log))

    def test_m81_does_not_inherit_emission_rules(self):
        """M81 是星系，不得因名称子串 'M8' 被误判为发射星云。"""
        cfg, _steps, log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["medium"], ["stretch"], None, "M81"
        )
        self.assertAlmostEqual(cfg["stretch_factor"], 45.0)
        self.assertAlmostEqual(cfg["sharpen_amount"], 1.6)     # 星系增强，而非发射星云 ×0.7
        self.assertFalse(any("发射星云" in line for line in log))

    def test_cluster_caps_chroma_denoise_and_saturation(self):
        cfg, _steps, log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["medium"],
            ["star_remove", "stretch", "star_process", "star_combine"],
            "globular_cluster",
            "M13",
        )
        self.assertLessEqual(cfg["pre_denoise_lum"], 0.01)
        self.assertLessEqual(cfg["final_denoise_lum"], 0.005)
        self.assertLessEqual(cfg["pre_denoise_chroma"], 0.03)
        self.assertLessEqual(cfg["final_denoise_chroma"], 0.015)
        self.assertLessEqual(cfg["saturation"], 1.25)
        self.assertTrue(log)

    def test_cluster_caps_never_raise_existing_values(self):
        """上限只能收紧，不得把本就保守的预设抬高。"""
        cfg, _steps, _log = pipeline.apply_target_aware_safety_rules(
            pipeline.STRENGTH_PRESETS["adaptive"], ["stretch"], "open_cluster", "M45"
        )
        self.assertLessEqual(cfg["pre_denoise_chroma"], 0.02)
        self.assertLessEqual(cfg["final_denoise_chroma"], 0.0075)
        self.assertLessEqual(cfg["saturation"], 1.25)

    def test_masked_ghs_raises_m42_core_protection(self):
        image = np.full((48, 64, 3), 0.03, dtype=np.float32)
        calls = []

        def fake_stretch(data, **kwargs):
            calls.append(kwargs)
            return data

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))
            with patch("pipeline.apply_luminance_stretch", side_effect=fake_stretch):
                pipeline.run_pipeline(
                    str(source),
                    str(output),
                    steps="stretch",
                    preset="light",
                    target_type="emission_nebula",
                    target_name="M 42",
                    stretch_method="masked_ghs",
                    cleanup=True,
                )

        masked = [c for c in calls if c.get("method") == "masked_ghs"]
        self.assertTrue(masked, "masked_ghs 拉伸未被调用")
        self.assertTrue(
            any(c.get("protect_strength", 0.0) >= 0.75 for c in masked),
            f"带空格的 'M 42' 未提升核心保护强度: {masked}",
        )

    def test_dense_analysis_prefers_external_starless_and_lower_recombine(self):
        config = pipeline.build_config_from_analysis(
            {
                "recommendations": {
                    "star_tools": {
                        "detection_threshold": 0.78,
                        "reduction": 0.4,
                        "star_stretch_factor": 24.0,
                    }
                },
                "starfield": {"star_density": "very_dense"},
            },
            base_preset="medium",
            target_type="emission_nebula",
        )
        self.assertTrue(config["prefer_external_starless"])
        self.assertLessEqual(config["star_combine_strength"], 0.82)

    def test_emission_fallback_switches_to_masked_ghs(self):
        image = np.full((48, 64, 3), 0.03, dtype=np.float32)
        fallback_report = {
            "fallback_applied": True,
            "fallback_reason": "star_removal_quality_below_threshold",
        }

        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))
            with (
                patch(
                    "pipeline.separate_stars",
                    return_value=(
                        image,
                        np.zeros_like(image),
                        np.zeros(image.shape[:2], dtype=np.float32),
                        fallback_report,
                    ),
                ),
                patch(
                    "pipeline.detect_stars",
                    return_value=np.zeros(image.shape[:2], dtype=np.float32),
                ),
            ):
                result = pipeline.run_pipeline(
                    str(source),
                    str(output),
                    steps="star_remove,stretch",
                    preset="light",
                    target_type="emission_nebula",
                    color_mode="emission",
                    cleanup=True,
                )

        self.assertEqual(
            result["effective_config"]["stretch_method"],
            "masked_ghs",
        )

    def test_override_can_explicitly_skip_dbe(self):
        image = np.full((48, 64, 3), 0.03, dtype=np.float32)
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            imsave(source, np.clip(image * 255, 0, 255).astype(np.uint8))
            result = pipeline.run_pipeline(
                str(source),
                str(output),
                steps="dbe,stretch",
                preset="light",
                override_params={"dbe_method": "skip"},
                cleanup=True,
            )
        self.assertNotIn("dbe", result["effective_config"]["steps"])


class StarStretchFactorScaleTests(unittest.TestCase):
    """分析报告给出的 `star_stretch_factor` 必须与 pipeline 预设**同量纲**。

    历史缺陷：`analyze.py` 曾写 `star_stretch_factor = stretch_factor * 0.25`，
    而预设表把两者成对给出（light 25→88、medium 45→132、strong 80→198、
    adaptive 120→264），比值是 2.2~3.5。旧式小了一个数量级：

        某极暗母版分析给出 stretch_factor=25（= light 预设同档）
        → 旧式 6.25，经 pipeline 的 ×1.5 后为 **9.375**，而预设同档是 **88**。
        → 星点层亮度只有预设的 ~1/4.7（星点像素均值 0.061 vs 0.287），
          成片星点中位 0.561 vs 手动后期的 0.794。
    """

    # 与 analyze.py 的锚点一致（stretch_factor=120 取 adaptive/emission 的均值）
    ANCHORS = ((25.0, 88.0), (45.0, 132.0), (80.0, 198.0), (120.0, 231.0))

    def setUp(self):
        from analyze import _star_stretch_factor_for
        self.fn = _star_stretch_factor_for

    def test_matches_preset_anchors_exactly(self):
        for stretch_factor, expected in self.ANCHORS:
            with self.subTest(stretch_factor=stretch_factor):
                self.assertAlmostEqual(
                    self.fn(stretch_factor), expected, delta=1e-6)

    def test_clamped_to_preset_range(self):
        for stretch_factor in (0.0, 5.0, 12.0, 25.0, 120.0, 200.0, 1000.0):
            with self.subTest(stretch_factor=stretch_factor):
                value = self.fn(stretch_factor)
                self.assertGreaterEqual(value, 88.0)
                self.assertLessEqual(value, 264.0)

    def test_monotonic_non_decreasing(self):
        values = [self.fn(sf) for sf in range(1, 200)]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(values, values[1:])))

    def test_interpolates_between_anchors(self):
        # 30 落在 25→88 与 45→132 之间，应严格居中偏下
        mid = self.fn(30.0)
        self.assertGreater(mid, 88.0)
        self.assertLess(mid, 132.0)

    def test_not_an_order_of_magnitude_below_preset(self):
        """回归护栏：同一 stretch_factor 下，分析建议不得比预设小一个数量级。"""
        from pipeline import STRENGTH_PRESETS as PRESETS
        for name, preset in PRESETS.items():
            sf = preset.get("stretch_factor")
            ref = preset.get("star_stretch_factor")
            if not sf or not ref:
                continue
            with self.subTest(preset=name):
                got = self.fn(sf)
                # 允许 ±30% 偏差（预设间比值本就随强度缓降），但绝不能差 10 倍
                self.assertGreater(got, ref * 0.7,
                                   f"{name}: 分析建议 {got} 远低于预设 {ref}")
                self.assertLess(got, ref * 1.3,
                                f"{name}: 分析建议 {got} 远高于预设 {ref}")


if __name__ == "__main__":
    unittest.main()
