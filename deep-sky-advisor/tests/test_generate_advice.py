import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "generate_advice.py"
SPEC = importlib.util.spec_from_file_location("advisor_generate_advice", SCRIPT)
advisor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(advisor)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


report_zh = _load("advisor_report_zh", "report_zh.py")
software_guidance = _load("advisor_software_guidance", "software_guidance.py")


# Content that used to be duplicated in report_zh.py. If any of it reappears in the
# rendered report, the convergence has regressed.
ENGLISH_LEAK_PHRASES = (
    "Not applicable before integration",
    "Choose rejection by usable frame count",
    "Match calibration frames to the lights",
    "Inspect all borders at high contrast",
    "Determine whether a correctable",
    "Constrain broadband color",
    "Reduce statistically supported",
    "Reveal faint signal",
    "Use subtraction for additive sky glow",
    "Acceptance checks",
    "Rollback conditions",
    "when available",
    "Generated background model",
)


LINEAR_OPERATIONS = {
    "calibrate_integrate", "crop_edges", "background_review", "color_calibration",
    "narrowband_mapping", "linear_denoise", "star_shape_review",
}
NONLINEAR_OPERATIONS = {"controlled_stretch", "highlight_protection", "star_treatment"}


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


def dual_advice(**kwargs):
    return advisor.compile_advice(
        base_analysis(),
        software="siril,pixinsight",
        target_type="galaxy",
        target_name="M31",
        **kwargs,
    )


class GenerateAdviceTests(unittest.TestCase):
    def test_every_active_operation_is_auditable(self):
        advice = advisor.compile_advice(
            base_analysis(),
            software="siril,pixinsight",
            target_type="galaxy",
            target_name="M31",
        )
        self.assertEqual(advisor.validate_advice(advice), [])
        for operation in advice["operations"]:
            self.assertNotEqual(operation["parameter_mode"], "exact")
            self.assertIn(operation["phase"], {"linear", "nonlinear", "finishing", "export"})
            self.assertEqual(operation["phase"], advisor.PHASE[operation["id"]])
            if operation["decision"] in {"recommend", "review"}:
                self.assertTrue(operation["evidence"])
                self.assertTrue(all(item["path"] for item in operation["evidence"]))
                self.assertTrue(operation["acceptance_checks"])
                self.assertTrue(operation["rollback_conditions"])
                for track in ("siril", "pixinsight"):
                    guidance = operation["implementations"][track]
                    self.assertTrue(guidance["primary_tool"])
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
        self.assertTrue(any("真实大尺度信号" in caution for caution in operations["background_review"]["cautions"]))

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
        self.assertTrue(any("母版" in item for item in advice["required_information"]))

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

    def test_cli_dual_track_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_path = root / "target_analysis.json"
            analysis_path.write_text(json.dumps(base_analysis()), encoding="utf-8")
            code = advisor.main([
                str(analysis_path),
                "--software", "siril,pixinsight",
                "--target-type", "galaxy",
                "--target-name", "M31",
            ])
            self.assertEqual(code, 0)
            report = (root / "target_processing_report.md").read_text(encoding="utf-8")
            self.assertIn("轨 A — Siril", report)
            self.assertIn("轨 B — PixInsight", report)
            self.assertIn("双轨对应关系", report)
            self.assertIn("交接给 Photoshop 的契约", report)
            self.assertIn("二次加工 — Photoshop", report)

    def test_cli_default_is_dual_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_path = root / "target_analysis.json"
            analysis_path.write_text(json.dumps(base_analysis()), encoding="utf-8")
            code = advisor.main([str(analysis_path), "--target-type", "galaxy"])
            self.assertEqual(code, 0)
            report = (root / "target_processing_report.md").read_text(encoding="utf-8")
            self.assertIn("轨 A — Siril", report)
            self.assertIn("轨 B — PixInsight", report)

    def test_software_outputs_are_specific(self):
        expected = {
            "siril": ("Generalised Hyperbolic Stretch", "Photometric Color Calibration"),
            "pixinsight": ("ScreenTransferFunction", "SpectrophotometricColorCalibration"),
            "photoshop": ("Curves 调整图层", "Convert to Profile"),
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
            for operation_id in advisor.ALL_OPERATIONS:
                guidance = advisor.get_software_guidance(software, operation_id)
                guidance["checkpoints"] = ["check"]
                guidance["failure_signs"] = ["rollback"]
                self.assertEqual(set(guidance), required)
                for field in required:
                    self.assertTrue(guidance[field], f"{software}.{operation_id}.{field}")

    def test_phase_table_covers_every_operation(self):
        self.assertEqual(set(advisor.PHASE), set(advisor.ALL_OPERATIONS))
        for operation_id, phase in advisor.PHASE.items():
            self.assertIn(phase, {"linear", "nonlinear", "finishing", "export"}, operation_id)
        self.assertNotIn("linear", advisor.TRACK_CAPABILITY["photoshop"])


class DualTrackTests(unittest.TestCase):
    def test_dual_track_shares_one_diagnosis(self):
        advice = dual_advice()
        self.assertEqual(advisor.validate_advice(advice), [])
        self.assertEqual(advice["context"]["tracks"], ["siril", "pixinsight"])
        self.assertEqual(advice["context"]["finishing"], "photoshop")
        self.assertIsNone(advice["context"]["software"])
        for operation in advice["operations"]:
            for track in ("siril", "pixinsight"):
                self.assertIn(track, operation["implementations"], operation["id"])
            self.assertNotEqual(
                operation["implementations"]["siril"]["steps"],
                operation["implementations"]["pixinsight"]["steps"],
            )

    def test_both_tracks_render_the_same_operation_set(self):
        advice = dual_advice()
        markdown = advisor.render_markdown(advice)
        self.assertIn("## 轨 A — Siril 完整流程", markdown)
        self.assertIn("## 轨 B — PixInsight 完整流程", markdown)
        track_a, rest = markdown.split("## 轨 B — PixInsight 完整流程", 1)
        track_b = rest.split("## 双轨对应关系", 1)[0]
        for operation in advice["operations"]:
            if operation["decision"] not in {"recommend", "review"}:
                continue
            heading = f"### {advisor.OPERATION_LABELS[operation['id']]} —"
            self.assertIn(heading, track_a, operation["id"])
            self.assertIn(heading, track_b, operation["id"])

    def test_track_mapping_uses_signature_tools(self):
        advice = dual_advice()
        markdown = advisor.render_markdown(advice)
        mapping = markdown.split("## 双轨对应关系", 1)[1].split("## 交接给", 1)[0]
        self.assertIn("Photometric Color Calibration", mapping)
        self.assertIn("SpectrophotometricColorCalibration", mapping)
        self.assertIn("DynamicBackgroundExtraction", mapping)
        self.assertNotIn("DynamicCrop", mapping)
        self.assertNotIn("Plate Solving", mapping)

    def test_photoshop_never_owns_linear_phase(self):
        advice = dual_advice()
        for operation in advice["operations"]:
            if operation["phase"] == "linear":
                self.assertNotIn("photoshop", operation["implementations"], operation["id"])
            else:
                self.assertIn("photoshop", operation["implementations"], operation["id"])

    def test_photoshop_section_excludes_linear_steps(self):
        advice = dual_advice()
        markdown = advisor.render_markdown(advice)
        section = markdown.split("## 二次加工 — Photoshop", 1)[1]
        for operation_id in LINEAR_OPERATIONS:
            self.assertNotIn(f"### {advisor.OPERATION_LABELS[operation_id]} —", section)
            self.assertNotIn(f"#### {advisor.OPERATION_LABELS[operation_id]} —", section)
        for operation_id in NONLINEAR_OPERATIONS | {"final_export"}:
            self.assertIn(f"#### {advisor.OPERATION_LABELS[operation_id]} —", section)

    def test_handoff_contract_is_derived_from_decisions(self):
        advice = dual_advice()
        contract = advice["handoff_contract"]
        expected = [
            operation["id"] for operation in advice["operations"]
            if operation["phase"] == "linear" and operation["decision"] in {"recommend", "review"}
        ]
        self.assertEqual(contract["upstream_required"], expected)
        self.assertEqual(contract["upstream_tracks"], ["siril", "pixinsight"])
        self.assertEqual(contract["finishing_software"], "photoshop")
        self.assertTrue(contract["color_calibration_done"])
        markdown = advisor.render_markdown(advice)
        handoff = markdown.split("## 交接给 Photoshop 的契约", 1)[1].split("## 二次加工", 1)[0]
        for operation_id in expected:
            self.assertIn(f"- [ ] {advisor.OPERATION_LABELS[operation_id]}", handoff)
        self.assertIn("不在 Photoshop 处理的操作", handoff)

    def test_handoff_reports_uncalibrated_upstream(self):
        analysis = base_analysis()
        analysis["classification"]["filter"] = "Ha"
        advice = advisor.compile_advice(
            analysis,
            software="siril,pixinsight",
            target_type="emission_nebula",
            target_name="NGC6888",
        )
        contract = advice["handoff_contract"]
        self.assertFalse(contract["color_calibration_done"])
        self.assertTrue(contract["narrowband_mapping_used"])
        markdown = advisor.render_markdown(advice)
        self.assertIn("主轨未建议测光校色", markdown)

    def test_single_track_regression(self):
        advice = advisor.compile_advice(
            base_analysis(),
            software="siril",
            target_type="galaxy",
            target_name="M31",
        )
        self.assertEqual(advisor.validate_advice(advice), [])
        self.assertEqual(advice["context"]["tracks"], ["siril"])
        self.assertEqual(advice["context"]["software"], "siril")
        self.assertEqual(advice["context"]["finishing"], "photoshop")
        markdown = advisor.render_markdown(advice)
        self.assertIn("## Siril 完整流程", markdown)
        self.assertNotIn("轨 A", markdown)
        self.assertNotIn("## 双轨对应关系", markdown)

    def test_no_finishing_disables_the_downstream_stage(self):
        advice = dual_advice(finishing=None)
        self.assertIsNone(advice["context"]["finishing"])
        self.assertIsNone(advice["handoff_contract"])
        self.assertEqual(advisor.validate_advice(advice), [])
        markdown = advisor.render_markdown(advice)
        self.assertNotIn("二次加工", markdown)
        self.assertNotIn("交接给 Photoshop 的契约", markdown)
        for operation in advice["operations"]:
            self.assertNotIn("photoshop", operation["implementations"])

    def test_generic_cannot_be_combined_with_a_concrete_application(self):
        with self.assertRaises(ValueError):
            advisor.compile_advice(base_analysis(), software="generic,siril", target_type="galaxy")
        with self.assertRaises(ValueError):
            advisor.compile_advice(base_analysis(), software="siril,unknown_tool", target_type="galaxy")
        with self.assertRaises(ValueError):
            advisor.compile_advice(base_analysis(), software="", target_type="galaxy")

    def test_duplicate_tracks_are_collapsed(self):
        advice = advisor.compile_advice(
            base_analysis(),
            software="siril,siril",
            target_type="galaxy",
            target_name="M31",
        )
        self.assertEqual(advice["context"]["tracks"], ["siril"])

    def test_validation_catches_missing_track_implementation(self):
        advice = dual_advice()
        for operation in advice["operations"]:
            operation["implementations"].pop("pixinsight", None)
        errors = advisor.validate_advice(advice)
        self.assertTrue(
            any("missing implementation for track pixinsight" in error for error in errors),
            errors,
        )

    def test_validation_catches_phase_mismatch(self):
        advice = dual_advice()
        advice["operations"][0]["phase"] = "export"
        errors = advisor.validate_advice(advice)
        self.assertTrue(any("phase does not match the phase table" in error for error in errors), errors)

    def test_validation_catches_illegal_phase_ownership(self):
        advice = dual_advice()
        operation = next(op for op in advice["operations"] if op["phase"] == "linear")
        operation["implementations"]["photoshop"] = dict(operation["implementations"]["siril"])
        errors = advisor.validate_advice(advice)
        self.assertTrue(
            any("track photoshop must not own phase linear" in error for error in errors),
            errors,
        )


class HighlightPressureTests(unittest.TestCase):
    """The aggregate metric is luminance-based and hides single-channel saturation."""

    def _with_clipping(self, aggregate, per_channel=None):
        analysis = base_analysis()
        analysis["clipping"]["highlight_ratio_ge_0_999"] = aggregate
        if per_channel is not None:
            analysis["clipping"]["per_channel"] = per_channel
        return analysis

    def _highlight(self, analysis):
        advice = advisor.compile_advice(
            analysis, software="siril,pixinsight", target_type="galaxy", target_name="M31",
        )
        return next(
            (op for op in advice["operations"] if op["id"] == "highlight_protection"), None
        )

    def test_aggregate_only_trigger_is_unchanged(self):
        operation = self._highlight(self._with_clipping(0.003))
        self.assertIsNotNone(operation)
        self.assertEqual(operation["decision"], "review")
        self.assertEqual(operation["confidence"], "low")
        paths = [item["path"] for item in operation["evidence"]]
        self.assertIn("clipping.highlight_ratio_ge_0_999", paths)
        self.assertFalse(any(path.startswith("clipping.per_channel.") for path in paths))

    def test_clean_aggregate_skips_highlight_review(self):
        self.assertIsNone(self._highlight(self._with_clipping(0.0)))

    def test_single_channel_saturation_is_detected_when_luminance_is_clean(self):
        analysis = self._with_clipping(0.0, {
            "r": {"shadow_ratio_le_0_001": 0.0, "highlight_ratio_ge_0_999": 0.05},
            "g": {"shadow_ratio_le_0_001": 0.003, "highlight_ratio_ge_0_999": 0.0001},
            "b": {"shadow_ratio_le_0_001": 0.025, "highlight_ratio_ge_0_999": 0.0},
        })
        operation = self._highlight(analysis)
        self.assertIsNotNone(operation, "red channel saturation must not be invisible")
        self.assertEqual(operation["decision"], "review")
        self.assertEqual(operation["confidence"], "medium")
        paths = [item["path"] for item in operation["evidence"]]
        self.assertIn("clipping.highlight_ratio_ge_0_999", paths)
        self.assertIn("clipping.per_channel.r.highlight_ratio_ge_0_999", paths)
        self.assertIn("R 通道", operation["starting_point"])
        self.assertTrue(any("R 通道已被单独压到上限" in item for item in operation["cautions"]))

    def test_per_channel_finding_reaches_the_report(self):
        analysis = self._with_clipping(0.0, {
            "r": {"highlight_ratio_ge_0_999": 0.05},
            "g": {"highlight_ratio_ge_0_999": 0.0},
            "b": {"highlight_ratio_ge_0_999": 0.0},
        })
        advice = advisor.compile_advice(
            analysis, software="siril,pixinsight", target_type="emission_nebula",
            target_name="NGC6888",
        )
        self.assertEqual(advisor.validate_advice(advice), [])
        markdown = advisor.render_markdown(advice)
        self.assertIn("clipping.per_channel.r.highlight_ratio_ge_0_999", markdown)
        self.assertIn("R 通道稳健映射亮端占比", markdown)
        self.assertIn("后续提升饱和度或色彩时不要进一步推高该通道", markdown)

    def test_worst_channel_is_the_one_reported(self):
        analysis = self._with_clipping(0.0, {
            "r": {"highlight_ratio_ge_0_999": 0.004},
            "g": {"highlight_ratio_ge_0_999": 0.02},
            "b": {"highlight_ratio_ge_0_999": 0.001},
        })
        operation = self._highlight(analysis)
        paths = [item["path"] for item in operation["evidence"]]
        self.assertIn("clipping.per_channel.g.highlight_ratio_ge_0_999", paths)
        self.assertNotIn("clipping.per_channel.r.highlight_ratio_ge_0_999", paths)

    def test_mono_channel_label_is_handled(self):
        analysis = self._with_clipping(0.0, {"mono": {"highlight_ratio_ge_0_999": 0.02}})
        operation = self._highlight(analysis)
        paths = [item["path"] for item in operation["evidence"]]
        self.assertIn("clipping.per_channel.mono.highlight_ratio_ge_0_999", paths)
        self.assertTrue(any("MONO 通道" in item for item in operation["cautions"]))

    def test_missing_per_channel_block_is_tolerated(self):
        analysis = base_analysis()
        analysis["clipping"].pop("per_channel", None)
        analysis["clipping"]["highlight_ratio_ge_0_999"] = 0.0
        self.assertIsNone(self._highlight(analysis))
        analysis["clipping"]["highlight_ratio_ge_0_999"] = 0.01
        self.assertIsNotNone(self._highlight(analysis))

    def test_malformed_per_channel_block_is_tolerated(self):
        analysis = self._with_clipping(0.0, {"r": "not-a-dict", "g": None})
        self.assertIsNone(self._highlight(analysis))


class ColorRefinementTests(unittest.TestCase):
    """The first operation to use the reserved `finishing` phase."""

    def _advice(self, analysis, **overrides):
        options = dict(software="siril,pixinsight", target_type="galaxy", target_name="M31")
        options.update(overrides)
        return advisor.compile_advice(analysis, **options)

    def _operation(self, advice):
        return next(op for op in advice["operations"] if op["id"] == "color_refinement")

    def test_finishing_phase_is_now_used_by_an_operation(self):
        self.assertIn("finishing", set(advisor.PHASE.values()))
        for software in ("siril", "pixinsight", "photoshop"):
            self.assertIn("finishing", advisor.TRACK_CAPABILITY[software])

    def test_residual_cast_triggers_review(self):
        operation = self._operation(self._advice(base_analysis()))
        self.assertEqual(operation["phase"], "finishing")
        self.assertEqual(operation["decision"], "review")
        self.assertEqual(operation["confidence"], "medium")
        paths = [item["path"] for item in operation["evidence"]]
        self.assertIn("color.background_ratios_to_mean", paths)
        self.assertIn("color.channel_p99_normalized", paths)

    def test_narrowband_defers_to_channel_mapping(self):
        analysis = base_analysis()
        analysis["classification"]["filter"] = "Ha"
        operation = self._operation(
            self._advice(analysis, target_type="emission_nebula", target_name="NGC6888")
        )
        self.assertEqual(operation["decision"], "skip")
        self.assertEqual(operation["confidence"], "high")
        self.assertIn("窄带", operation["starting_point"])

    def test_collapsed_channel_is_never_refined(self):
        analysis = base_analysis()
        analysis["color"]["collapsed_channels"] = ["b"]
        operation = self._operation(self._advice(analysis))
        self.assertEqual(operation["decision"], "skip")
        self.assertEqual(operation["confidence"], "high")
        self.assertIn("color.collapsed_channels", [item["path"] for item in operation["evidence"]])

    def test_balanced_channels_do_not_justify_saturation(self):
        analysis = base_analysis()
        analysis["color"]["background_ratios_to_mean"] = {"r": 1.02, "g": 1.0, "b": 0.99}
        operation = self._operation(self._advice(analysis))
        self.assertEqual(operation["decision"], "skip")
        self.assertEqual(operation["confidence"], "low")
        self.assertIn("纯审美选择", operation["starting_point"])

    def test_saturated_channel_adds_a_headroom_caution(self):
        analysis = base_analysis()
        analysis["clipping"]["highlight_ratio_ge_0_999"] = 0.0
        analysis["clipping"]["per_channel"] = {
            "r": {"highlight_ratio_ge_0_999": 0.05},
            "g": {"highlight_ratio_ge_0_999": 0.0},
            "b": {"highlight_ratio_ge_0_999": 0.0},
        }
        operation = self._operation(self._advice(analysis))
        self.assertEqual(operation["decision"], "review")
        self.assertTrue(any("R 通道已接近或压到上限" in item for item in operation["cautions"]))

    def test_renders_in_both_tracks_and_in_the_finishing_section(self):
        advice = self._advice(base_analysis())
        self.assertEqual(advisor.validate_advice(advice), [])
        markdown = advisor.render_markdown(advice)
        track_a = markdown.split("## 轨 B — PixInsight 完整流程", 1)[0]
        self.assertIn("### 色彩精修 — 确认后执行", track_a)
        finishing = markdown.split("## 二次加工 — Photoshop", 1)[1]
        self.assertIn("#### 色彩精修 — 确认后执行", finishing)

    def test_software_specific_tools_reach_the_report(self):
        markdown = advisor.render_markdown(self._advice(base_analysis()))
        self.assertIn("Color Saturation（色彩饱和度）", markdown)
        self.assertIn("ColorSaturation", markdown)
        self.assertIn("Vibrance 调整图层", markdown)

    def test_not_part_of_the_upstream_handoff_checklist(self):
        advice = self._advice(base_analysis())
        self.assertNotIn("color_refinement", advice["handoff_contract"]["upstream_required"])

    def test_malformed_color_block_is_tolerated(self):
        analysis = base_analysis()
        analysis["color"]["background_ratios_to_mean"] = "not-a-dict"
        analysis["color"]["collapsed_channels"] = None
        operation = self._operation(self._advice(analysis))
        self.assertEqual(operation["decision"], "skip")


class ContentOwnershipTests(unittest.TestCase):
    """Guard the convergence: content lives in exactly one module."""

    def test_report_zh_is_a_label_layer_only(self):
        forbidden = (
            "OPERATION_TEXT", "GENERIC_GUIDANCE", "SOFTWARE_STEPS",
            "CHECKPOINTS", "FAILURES", "TOOL_LABELS", "REQUIRED_INFO",
        )
        for name in forbidden:
            self.assertFalse(
                hasattr(report_zh, name),
                f"report_zh must not carry operational content: {name}",
            )

    def test_generate_advice_carries_no_english_software_map(self):
        self.assertFalse(hasattr(advisor, "SOFTWARE_MAP"))
        self.assertEqual(set(advisor.ALL_OPERATIONS), set(advisor.PHASE))

    def test_every_software_operation_has_full_content(self):
        for software in ("generic", "siril", "pixinsight", "photoshop"):
            for operation_id in advisor.ALL_OPERATIONS:
                merged = software_guidance.get_software_guidance(software, operation_id)
                for field in ("tools", "steps", "parameter_logic", "mask_strategy"):
                    self.assertTrue(merged[field], f"{software}.{operation_id}.{field}")

    def test_report_has_no_english_prose_leak(self):
        markdown = advisor.render_markdown(dual_advice())
        for phrase in ENGLISH_LEAK_PHRASES:
            self.assertNotIn(phrase, markdown, phrase)
        cjk = sum(1 for ch in markdown if "\u4e00" <= ch <= "\u9fff")
        visible = sum(1 for ch in markdown if not ch.isspace())
        self.assertGreater(cjk / visible, 0.35, "报告应以中文为主")

    def test_per_software_parameter_logic_is_rendered(self):
        advice = dual_advice()
        markdown = advisor.render_markdown(advice)
        implementation = next(
            op for op in advice["operations"] if op["id"] == "background_review"
        )["implementations"]["pixinsight"]
        for item in implementation["parameter_logic"]:
            self.assertIn(item, markdown)

    def test_per_software_mask_strategy_is_rendered(self):
        advice = dual_advice()
        markdown = advisor.render_markdown(advice)
        implementation = next(
            op for op in advice["operations"] if op["id"] == "star_treatment"
        )["implementations"]["photoshop"]
        for item in implementation["mask_strategy"]:
            self.assertIn(item, markdown)

    def test_cautions_are_rendered_with_their_real_content(self):
        markdown = advisor.render_markdown(dual_advice())
        self.assertIn("数值趋势本身不足以证明背景可以移除。", markdown)
        self.assertIn("基于矩的 FWHM 不是完整 PSF 拟合", markdown)

    def test_target_specific_caution_reaches_the_report(self):
        analysis = base_analysis()
        analysis["classification"]["filter"] = "Ha"
        advice = advisor.compile_advice(
            analysis,
            software="siril,pixinsight",
            target_type="emission_nebula",
            target_name="NGC6888",
        )
        markdown = advisor.render_markdown(advice)
        self.assertIn("该目标/滤镜可能包含与梯度相似的真实大尺度信号。", markdown)


if __name__ == "__main__":
    unittest.main()
