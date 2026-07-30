import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_advice.py"
SPEC = importlib.util.spec_from_file_location("advisor_generate_advice", SCRIPT)
advisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(advisor)


def base_analysis():
    return {
        "schema_version": "2.0",
        "analysis_json": "/tmp/example_analysis.json",
        "file": {
            "format": "fits",
            "header": {"WCSAXES": 2},
        },
        "classification": {
            "frame_role": "unknown",
            "processing_stage": "stacked_or_integrated",
            "transfer_state": "likely_linear",
            "channel_model": "rgb",
            "filter": "L-Pro",
            "object": "M31",
        },
        "statistics": {
            "exact_min_ratio": 0.0,
            "near_min_ratio": 0.0,
            "exact_max_ratio": 0.0001,
        },
        "clipping": {
            "shadow_ratio_le_0_001": 0.001,
            "highlight_ratio_ge_0_999": 0.003,
        },
        "noise": {
            "background_noise_sigma_normalized": 0.015,
            "block_count": 12,
        },
        "background": {
            "plane": {
                "magnitude_across_frame": 0.12,
                "r_squared": 0.8,
            },
            "corner_median_range": 0.09,
        },
        "stars": {
            "evidence": "measured",
            "usable_star_count": 42,
            "density_per_megapixel": 120,
            "fwhm_major_median_px": 3.4,
            "eccentricity_p90": 0.5,
            "position_angle_median_deg": 88,
        },
        "color": {
            "channel_p99_normalized": {"r": 0.9, "g": 0.8, "b": 0.7},
            "background_ratios_to_mean": {"r": 1.1, "g": 1.0, "b": 0.9},
        },
    }


class GenerateAdviceTests(unittest.TestCase):
    def test_every_active_operation_is_auditable(self):
        advice = advisor.compile_advice(
            base_analysis(),
            software="pixinsight",
            target_type="galaxy",
            target_name="M31",
        )
        self.assertEqual(advisor.validate_advice(advice), [])
        for operation in advice["operations"]:
            self.assertNotEqual(operation["parameter_mode"], "exact")
            if operation["decision"] in {"recommend", "review"}:
                self.assertTrue(operation["evidence"])
                self.assertTrue(all(item["path"] for item in operation["evidence"]))
                self.assertTrue(operation["acceptance_checks"])
                self.assertTrue(operation["rollback_conditions"])
                guidance = operation["software_guidance"]
                self.assertTrue(guidance["tools"])
                self.assertTrue(guidance["steps"])
                self.assertTrue(guidance["parameter_logic"])
                self.assertTrue(guidance["mask_strategy"])

    def test_unintegrated_light_stops_at_preprocessing(self):
        analysis = base_analysis()
        analysis["classification"].update({
            "frame_role": "light",
            "processing_stage": "unknown",
        })
        advice = advisor.compile_advice(analysis, software="siril", target_type="galaxy")
        self.assertEqual([op["id"] for op in advice["operations"]], ["calibrate_integrate"])

    def test_emission_narrowband_gradient_is_review_not_automatic(self):
        analysis = base_analysis()
        analysis["classification"]["filter"] = "Ha"
        advice = advisor.compile_advice(
            analysis,
            software="pixinsight",
            target_type="emission_nebula",
            target_name="NGC6888",
        )
        operations = {op["id"]: op for op in advice["operations"]}
        self.assertEqual(operations["background_review"]["decision"], "review")
        self.assertIn("narrowband_mapping", operations)
        self.assertNotIn("color_calibration", operations)
        self.assertTrue(any("large-scale signal" in caution for caution in operations["background_review"]["cautions"]))

    def test_cluster_skips_star_treatment(self):
        advice = advisor.compile_advice(
            base_analysis(),
            software="pixinsight",
            target_type="globular_cluster",
            target_name="M13",
        )
        star = next(op for op in advice["operations"] if op["id"] == "star_treatment")
        self.assertEqual(star["decision"], "skip")
        self.assertEqual(star["confidence"], "high")
        self.assertEqual(star["evidence"][0]["path"], "user_context.target_type")
        self.assertEqual(star["evidence"][0]["value"], "globular_cluster")

    def test_missing_star_measurement_never_creates_fwhm_parameter_rule(self):
        analysis = base_analysis()
        analysis["stars"] = {
            "evidence": "unavailable",
            "reason": "insufficient samples",
            "usable_star_count": 2,
        }
        advice = advisor.compile_advice(analysis, software="generic", target_type="galaxy")
        for operation in advice["operations"]:
            for rule in operation["parameter_rules"]:
                self.assertNotEqual(rule.get("evidence_path"), "stars.fwhm_major_median_px")

    def test_unknown_stage_does_not_recommend_stretch(self):
        analysis = base_analysis()
        analysis["classification"].update({
            "frame_role": "unknown",
            "processing_stage": "unknown",
            "transfer_state": "unknown",
        })
        advice = advisor.compile_advice(analysis, software="generic", target_type="galaxy")
        operation_ids = {op["id"] for op in advice["operations"]}
        self.assertNotIn("controlled_stretch", operation_ids)
        self.assertTrue(any("integrated master" in item for item in advice["required_information"]))

    def test_cli_writes_json_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_path = root / "target_analysis.json"
            analysis_path.write_text(json.dumps(base_analysis()), encoding="utf-8")
            code = advisor.main([
                str(analysis_path),
                "--software", "photoshop",
                "--target-type", "galaxy",
                "--target-name", "M31",
            ])
            self.assertEqual(code, 0)
            advice_path = root / "target_advice.json"
            report_path = root / "target_processing_report.md"
            self.assertTrue(advice_path.exists())
            self.assertTrue(report_path.exists())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("处理优先级", report)
            self.assertIn("关键工具/入口", report)
            self.assertIn("操作步骤", report)
            self.assertIn("阶段验收", report)
            self.assertIn("失败征象与回退条件", report)

    def test_software_outputs_are_specific(self):
        expected = {
            "siril": ("Generalised Hyperbolic Stretch", "Photometric Color Calibration"),
            "pixinsight": ("ScreenTransferFunction", "SpectrophotometricColorCalibration"),
            "photoshop": ("Curves adjustment layers", "Convert to Profile"),
        }
        for software, phrases in expected.items():
            advice = advisor.compile_advice(
                base_analysis(),
                software=software,
                target_type="galaxy",
                target_name="M31",
            )
            markdown = advisor.render_markdown(advice)
            for phrase in phrases:
                self.assertIn(phrase, markdown)

    def test_markdown_narrative_is_chinese(self):
        advice = advisor.compile_advice(
            base_analysis(),
            software="pixinsight",
            target_type="galaxy",
            target_name="M31",
        )
        markdown = advisor.render_markdown(advice)
        forbidden_sentences = (
            "Determine whether a correctable",
            "Constrain broadband color",
            "Reduce statistically supported",
            "Reveal faint signal",
            "Acceptance checks",
            "Rollback conditions",
            "置信度：medium",
            "参数模式：qualitative",
            "目标类型：galaxy",
            "when available",
            "Generated background model",
        )
        for sentence in forbidden_sentences:
            self.assertNotIn(sentence, markdown)
        self.assertIn("背景模型只包含平滑的非目标低频成分", markdown)
        self.assertIn("使用 ImageSolver 确认 WCS", markdown)
        self.assertIn("目标类型：星系", markdown)
        self.assertIn("置信度：中", markdown)

    def test_all_software_playbooks_cover_all_operations(self):
        required = {"tools", "steps", "parameter_logic", "mask_strategy", "checkpoints", "failure_signs"}
        for software in ("generic", "siril", "pixinsight", "photoshop"):
            for operation_id in advisor.SOFTWARE_MAP[software]:
                guidance = advisor.get_software_guidance(software, operation_id)
                guidance["checkpoints"] = ["check"]
                guidance["failure_signs"] = ["rollback"]
                self.assertEqual(set(guidance), required)
                for field in required:
                    self.assertTrue(guidance[field], f"{software}.{operation_id}.{field}")


if __name__ == "__main__":
    unittest.main()
