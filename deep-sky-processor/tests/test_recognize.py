import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from skimage.io import imsave


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
RECOGNIZE = SCRIPTS / "recognize.py"
sys.path.insert(0, str(SCRIPTS))

from recognize import (
    build_recognition_workflow,
    finalize_recognition_workflow,
    safe_visual_preview,
)


def make_rgb_scene(path: Path) -> None:
    img = np.zeros((96, 128, 3), dtype=np.float32)
    yy, xx = np.mgrid[0:96, 0:128]
    nebula = np.exp(-(((xx - 62) / 24) ** 2 + ((yy - 48) / 18) ** 2))
    img[..., 0] = 0.05 + nebula * 0.75
    img[..., 1] = 0.04 + nebula * 0.22
    img[..., 2] = 0.06 + nebula * 0.28
    for y, x in [(12, 18), (24, 104), (40, 30), (58, 88), (76, 51)]:
        img[y - 1:y + 2, x - 1:x + 2, :] = 1.0
    imsave(path, np.clip((img * 255).round(), 0, 255).astype(np.uint8))


def make_rgba_scene(path: Path) -> None:
    rgb = np.zeros((32, 40, 3), dtype=np.uint8)
    rgb[10:22, 14:28, 0] = 220
    rgb[10:22, 14:28, 1] = 80
    rgb[10:22, 14:28, 2] = 120
    alpha = np.full((32, 40, 1), 255, dtype=np.uint8)
    imsave(path, np.dstack([rgb, alpha]))


def make_gray_scene(path: Path) -> None:
    gray = np.zeros((40, 48), dtype=np.uint8)
    gray[15:25, 18:30] = 210
    gray[5, 5] = 255
    gray[30, 40] = 255
    imsave(path, gray)


class RecognizeCliTests(unittest.TestCase):
    def test_safe_preview_preserves_extreme_dark_positive_pixels(self):
        image = np.full((64, 80, 3), 2e-5, dtype=np.float32)
        image[20:44, 28:52, 0] += 8e-5
        preview, params = safe_visual_preview(image, target_bg=0.12)
        self.assertEqual(params["shadow_pctl"], 0.0)
        self.assertLess(float(np.mean(preview <= 0)), 0.01)
        self.assertGreater(float(np.median(preview)), 0.08)

    def test_workflow_builds_preview_and_marks_cv_auxiliary(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "scene.png"
            workflow_dir = Path(td) / "workflow"
            make_rgb_scene(image)
            payload = build_recognition_workflow(image, workflow_dir)

            self.assertEqual(
                payload["status"],
                "awaiting_ai_visual_review",
            )
            self.assertEqual(
                payload["recognition_order"],
                [
                    "header_wcs",
                    "raw_numeric_diagnostics",
                    "safe_visual_preview",
                    "ai_visual_review",
                    "local_cv_auxiliary_validation",
                ],
            )
            self.assertEqual(
                payload["local_cv_auxiliary_validation"]["metadata"]["role"],
                "auxiliary_validation_only",
            )
            for path in (
                payload["safe_previews"]["paths"]["full"],
                payload["safe_previews"]["paths"]["target"],
                payload["safe_previews"]["paths"]["preview_master"],
            ):
                self.assertTrue(Path(path).exists())
            self.assertTrue(
                Path(payload["ai_visual_review"]["request_path"]).exists()
            )

    def test_fits_workflow_prefers_header_identity(self):
        from astropy.io import fits

        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "ngc6888.fits"
            workflow_dir = Path(td) / "workflow"
            data = np.full((64, 80), 2e-5, dtype=np.float32)
            data[22:42, 28:52] += 8e-5
            header = fits.Header()
            header["OBJECT"] = "NGC 6888"
            fits.writeto(image, data, header=header, overwrite=True)

            payload = build_recognition_workflow(image, workflow_dir)

            resolved = payload["header_wcs"]["resolved_target"]
            self.assertEqual(resolved["resolved_name"], "NGC6888")
            self.assertEqual(resolved["resolved_type"], "emission_nebula")
            self.assertEqual(
                payload["header_wcs"]["precedence"],
                "header_or_wcs_before_visual_classification",
            )

    def test_finalize_prefers_header_and_flags_visual_conflict(self):
        bundle = {
            "header_wcs": {
                "resolved_target": {
                    "resolved_type": "emission_nebula",
                }
            },
            "ai_visual_review": {"status": "required"},
            "local_cv_auxiliary_validation": {
                "scene": {"target_type": "galaxy"},
            },
        }
        finalized = finalize_recognition_workflow(
            bundle,
            {
                "target_type": "galaxy",
                "target_name": None,
                "confidence": 0.8,
                "visible_features": [],
                "quality_findings": [],
                "uncertainties": [],
            },
        )
        self.assertEqual(finalized["status"], "review_required")
        self.assertEqual(
            finalized["decision"]["target_type"],
            "emission_nebula",
        )
        self.assertEqual(finalized["decision"]["source"], "header_wcs")

    def test_cli_writes_schema_valid_json_for_rgb_image(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "scene.png"
            output = Path(td) / "recognition.json"
            make_rgb_scene(image)

            result = subprocess.run(
                [sys.executable, str(RECOGNIZE), str(image), "--output", str(output)],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], "1.0")
            self.assertEqual(payload["source"]["stage"], "final")
            self.assertEqual(payload["source"]["image_path"], str(image))
            self.assertEqual(payload["scene"]["label"], "deep_sky")
            self.assertIn("target_type", payload["scene"])
            self.assertGreaterEqual(payload["scene"]["confidence"], 0.0)
            self.assertLessEqual(payload["scene"]["confidence"], 1.0)
            self.assertIn("primary_region", payload)
            self.assertEqual(len(payload["primary_region"]["bbox"]), 4)
            self.assertIn("quality_tags", payload)
            self.assertEqual(payload["metadata"]["recognition_backend"], "local_cv")
            self.assertEqual(payload["metadata"]["ai_visual_review"], "agent_skill_optional")

    def test_min_confidence_filters_low_confidence_detections(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "scene.png"
            output = Path(td) / "recognition.json"
            make_rgb_scene(image)

            result = subprocess.run(
                [
                    sys.executable,
                    str(RECOGNIZE),
                    str(image),
                    "--output",
                    str(output),
                    "--min-confidence",
                    "0.95",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(all(item["confidence"] >= 0.95 for item in payload["detections"]))

    def test_rgba_and_grayscale_inputs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            rgba = Path(td) / "rgba.png"
            gray = Path(td) / "gray.png"
            rgba_out = Path(td) / "rgba.json"
            gray_out = Path(td) / "gray.json"
            make_rgba_scene(rgba)
            make_gray_scene(gray)

            for image, output in [(rgba, rgba_out), (gray, gray_out)]:
                result = subprocess.run(
                    [sys.executable, str(RECOGNIZE), str(image), "--output", str(output)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(payload["schema_version"], "1.0")
                self.assertIn(payload["source"]["shape"][-1], [1, 3, 4])

    def test_analysis_report_is_embedded_in_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            image = Path(td) / "scene.png"
            report = Path(td) / "analysis.json"
            output = Path(td) / "recognition.json"
            make_rgb_scene(image)
            report.write_text(
                json.dumps(
                    {
                        "brightness": {"darkness_level": "dark"},
                        "noise": {"noise_level": "low"},
                        "gradient": {"gradient_severity": "mild"},
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(RECOGNIZE),
                    str(image),
                    "--output",
                    str(output),
                    "--analysis-report",
                    str(report),
                    "--stage",
                    "input",
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["source"]["stage"], "input")
            self.assertEqual(payload["metadata"]["analysis_report"]["brightness"]["darkness_level"], "dark")


if __name__ == "__main__":
    unittest.main()
