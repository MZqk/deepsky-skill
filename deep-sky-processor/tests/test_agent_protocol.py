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

from agent_protocol import (
    create_review_bundle,
    evaluate_quality_gates,
    intent_to_overrides,
    validate_action,
    validate_review,
)
from agent_workflow import _optional_pipeline_kwargs, apply_action, initialize_session
from pipeline import run_pipeline


def write_scene(path, background=0.04):
    image = np.full((48, 64, 3), background, dtype=np.float32)
    yy, xx = np.mgrid[:48, :64]
    nebula = np.exp(-(((xx - 32) / 14) ** 2 + ((yy - 24) / 10) ** 2))
    image[..., 0] += nebula * 0.35
    image[..., 1] += nebula * 0.10
    image[..., 2] += nebula * 0.08
    imsave(path, np.clip(image * 255, 0, 255).astype(np.uint8))


class ActionFieldValidationTests(unittest.TestCase):
    """未知 action 字段必须在提交前报错，不能静默走默认值。

    背景：`external_starless` 放进 `params` 会被静默忽略（它走 run_pipeline 的
    独立形参，不读 cfg），表现为 star_remove 悄悄走内存跳过分支、`artifacts/`
    只有 manifest、**无任何报错**。`external_starless_domain` 漏转发更隐蔽 ——
    默认 `'linear'` 会把 MTF 域无星层按线性读入，域错色比错且不报错。
    """

    def test_typo_field_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            validate_action({"operation": "run_step", "step": "stretch",
                             "external_starnetless": "/tmp/x.tif"})
        self.assertIn("external_starnetless", str(ctx.exception))

    def test_declared_pipeline_field_is_accepted(self):
        action = validate_action({
            "operation": "run_step", "step": "star_remove",
            "external_starless": "/tmp/s.tif",
            "external_starless_domain": "mtf",
            "external_starless_midtones": 0.02,
        })
        self.assertEqual(action["external_starless_domain"], "mtf")

    def test_control_fields_of_every_operation_are_accepted(self):
        """accept / create_variants / masked_adjustment 的合法字段不得被误伤。"""
        for action in (
            {"operation": "accept", "artifact": "/tmp/a.tif",
             "resolutions": [{"issue_id": "x", "outcome": "false_positive",
                              "reason": "r", "evidence": ["/tmp/e.json"]}]},
            {"operation": "create_variants", "step": "stretch", "preset": "light",
             "variants": [{"id": "a", "params": {"target_bg": 0.06}},
                          {"id": "b", "params": {"target_bg": 0.08}}]},
            {"operation": "masked_adjustment",
             "mask": {"type": "range", "low": 0.1, "high": 0.5},
             "adjustment": {"method": "saturation", "factor": 1.2}},
        ):
            with self.subTest(operation=action["operation"]):
                self.assertIs(validate_action(action), action)

    def test_optional_kwargs_skip_absent_and_none(self):
        """键缺失或值为 None 时不得转发 —— 否则会覆盖 run_pipeline 的默认值。

        `deepsnr=None` 会触发 "DeepSNR mode must be auto, always or never"；
        `low_memory_threshold_mpix=None` 会触发 float(None) TypeError。
        """
        self.assertEqual(_optional_pipeline_kwargs({}), {})
        self.assertEqual(
            _optional_pipeline_kwargs({"deepsnr": None,
                                       "low_memory_threshold_mpix": None}), {})
        self.assertEqual(
            _optional_pipeline_kwargs({"deepsnr": "never"}),
            {"deepsnr": "never"})

    def test_optional_kwargs_covers_previously_dropped_fields(self):
        """曾经静默失效的关键字段必须在转发集合里。"""
        forwarded = _optional_pipeline_kwargs({
            "external_starless_domain": "mtf",
            "external_starless_midtones": 0.02,
            "external_detail_strength": 0.5,
        })
        self.assertEqual(forwarded["external_starless_domain"], "mtf")
        self.assertEqual(forwarded["external_starless_midtones"], 0.02)
        self.assertEqual(forwarded["external_detail_strength"], 0.5)


class AgentProtocolTests(unittest.TestCase):
    def test_semantic_intent_maps_to_bounded_params(self):
        params = intent_to_overrides({
            "background": "slightly_darker",
            "core_protection": "strong",
            "star_dominance": "reduce_slightly",
            "noise_tolerance": "preserve_detail",
        })
        self.assertEqual(params["target_bg"], 0.07)
        self.assertEqual(params["ghs_protect_strength"], 0.75)
        self.assertLessEqual(params["star_reduction"], 0.18)
        self.assertLessEqual(params["final_denoise_lum"], 0.006)

    def test_action_and_review_validation(self):
        action = validate_action({
            "operation": "run_step",
            "step": "stretch",
            "intent": {"background": "slightly_darker"},
        })
        self.assertEqual(action["step"], "stretch")
        review = validate_review({
            "verdict": "retry",
            "actions": [action],
        })
        self.assertEqual(review["verdict"], "retry")
        with self.assertRaises(ValueError):
            validate_action({"operation": "invent_pixels"})

    def test_quality_gate_marks_dbe_corner_failure_for_review(self):
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

    def test_linear_negative_background_does_not_use_final_median_gate(self):
        status, gates = evaluate_quality_gates(
            {
                "processing_stage": "linear",
                "median": -0.003,
                "p1": -0.01,
                "negative_pixel_ratio": 0.2,
                "nonpositive_pixel_ratio": 0.2,
                "corner_uniformity_ratio": 1.0,
                "uniform_5x5_dark_patch_ratio": 0.0,
                "star_area_ratio": 0.0,
            },
            target_type="emission_nebula",
        )
        codes = {gate["code"] for gate in gates}
        self.assertEqual(status, "review_required")
        self.assertIn("LINEAR_BACKGROUND_UNDERSHOOT", codes)
        self.assertNotIn("BACKGROUND_CRUSHED", codes)
        self.assertNotIn("BACKGROUND_LOW", codes)

    def test_final_background_crush_uses_clipping_evidence(self):
        _status, gates = evaluate_quality_gates(
            {
                "processing_stage": "final",
                "median": 0.08,
                "p1": 0.0,
                "negative_pixel_ratio": 0.0,
                "nonpositive_pixel_ratio": 0.04,
                "corner_uniformity_ratio": 1.0,
                "uniform_5x5_dark_patch_ratio": 0.0,
                "star_area_ratio": 0.0,
            },
            target_type="emission_nebula",
        )
        self.assertIn("BACKGROUND_CRUSHED", {gate["code"] for gate in gates})

    def test_review_bundle_contains_visual_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            before = Path(td) / "before.png"
            after = Path(td) / "after.png"
            review_dir = Path(td) / "review"
            write_scene(before, 0.04)
            write_scene(after, 0.06)

            payload = create_review_bundle(
                before,
                after,
                review_dir,
                context={"target_type": "emission_nebula", "steps": ["stretch"]},
            )

            self.assertTrue(Path(payload["report_path"]).exists())
            for path in payload["previews"].values():
                self.assertTrue(Path(path).exists())
            self.assertIn("critic_checklist", payload)
            self.assertIn("metrics_before", payload)
            self.assertIn("metric_delta", payload)

    def test_pipeline_returns_machine_result(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            output = Path(td) / "output.tif"
            result_path = Path(td) / "result.json"
            write_scene(source)

            result = run_pipeline(
                str(source),
                str(output),
                steps="stretch",
                preset="light",
                cleanup=True,
                result_json=str(result_path),
            )

            self.assertIn(result["status"], {"success", "review_required"})
            self.assertEqual(result["schema_version"], "1.0")
            self.assertEqual(result["outputs"]["image"], str(output))
            self.assertIn("quality_gates", result)
            persisted = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], "1.0")

    def test_session_executes_one_step_and_waits_for_review(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            session_dir = Path(td) / "session"
            write_scene(source)
            state = initialize_session(
                str(source),
                session_dir,
                target_type="emission_nebula",
            )
            self.assertEqual(state["current_artifact"], str(source.resolve()))

            event = apply_action(session_dir, {
                "operation": "run_step",
                "step": "stretch",
                "preset": "light",
                "intent": {"core_protection": "strong"},
            })

            self.assertEqual(event["operation"], "run_step")
            updated = json.loads(
                (session_dir / "session.json").read_text(encoding="utf-8")
            )
            self.assertTrue(Path(updated["current_artifact"]).exists())
            self.assertTrue(Path(updated["pending_review"]).exists())

    def test_session_accepts_normalized_local_region(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.png"
            session_dir = Path(td) / "session"
            write_scene(source)
            initialize_session(str(source), session_dir)

            event = apply_action(session_dir, {
                "operation": "run_step",
                "step": "local_enhance",
                "preset": "light",
                "region": {"bbox": [0.25, 0.25, 0.5, 0.5]},
                "local_strength": 0.15,
            })

            result = event["result"]
            self.assertTrue(Path(result["artifact"]).exists())
            self.assertEqual(
                result["review"]["context"]["region"]["bbox"],
                [0.25, 0.25, 0.5, 0.5],
            )


if __name__ == "__main__":
    unittest.main()
