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


if __name__ == "__main__":
    unittest.main()
