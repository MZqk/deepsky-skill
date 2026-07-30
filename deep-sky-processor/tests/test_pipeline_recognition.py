import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import pipeline


class PipelineRecognitionTests(unittest.TestCase):
    def test_default_recognition_output_appends_suffix(self):
        result = pipeline.default_recognition_output("processed.jpg")
        self.assertEqual(result, "processed.recognition.json")

    def test_comparison_payload_contains_input_final_and_changes(self):
        input_payload = {
            "scene": {"target_type": "emission_nebula"},
            "quality_tags": [{"label": "high_noise", "confidence": 0.8}],
        }
        final_payload = {
            "scene": {"target_type": "emission_nebula"},
            "quality_tags": [{"label": "clean_background", "confidence": 0.7}],
        }

        payload = pipeline.build_recognition_comparison(input_payload, final_payload)

        self.assertEqual(payload["schema_version"], "1.0")
        self.assertEqual(payload["mode"], "comparison")
        self.assertTrue(payload["comparison"]["scene_consistent"])
        self.assertIn("high_noise", payload["comparison"]["removed_quality_tags"])
        self.assertIn("clean_background", payload["comparison"]["added_quality_tags"])

    def test_optional_recognition_failure_is_swallowed(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "processed.jpg"
            output.write_bytes(b"not an image")

            with patch("pipeline.recognize_image", side_effect=RuntimeError("boom")):
                ok = pipeline.run_optional_recognition(
                    input_path=str(output),
                    output_path=str(output),
                    recognize=True,
                    recognize_output=str(Path(td) / "recognition.json"),
                    recognize_input=False,
                )

            self.assertFalse(ok)
            self.assertTrue(output.exists())

    def test_optional_recognition_writes_comparison_json(self):
        with tempfile.TemporaryDirectory() as td:
            input_image = Path(td) / "input.jpg"
            final_image = Path(td) / "final.jpg"
            output_json = Path(td) / "recognition.json"
            input_image.write_bytes(b"input")
            final_image.write_bytes(b"final")

            def fake_recognize(path, stage="final", min_confidence=0.35, analysis_report=None):
                return {
                    "schema_version": "1.0",
                    "source": {"image_path": str(path), "stage": stage},
                    "scene": {"target_type": "galaxy"},
                    "quality_tags": [{"label": f"{stage}_tag", "confidence": 0.5}],
                }

            with patch("pipeline.recognize_image", side_effect=fake_recognize):
                ok = pipeline.run_optional_recognition(
                    input_path=str(input_image),
                    output_path=str(final_image),
                    recognize=True,
                    recognize_output=str(output_json),
                    recognize_input=True,
                )

            self.assertTrue(ok)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "comparison")
            self.assertEqual(payload["input"]["source"]["stage"], "input")
            self.assertEqual(payload["final"]["source"]["stage"], "final")

    def test_astronomical_input_uses_hybrid_visual_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            input_image = Path(td) / "input.fits"
            final_image = Path(td) / "final.jpg"
            output_json = Path(td) / "recognition.json"
            input_image.write_bytes(b"fits")
            final_image.write_bytes(b"final")
            final_payload = {
                "schema_version": "1.0",
                "source": {"image_path": str(final_image), "stage": "final"},
                "scene": {"target_type": "emission_nebula"},
                "quality_tags": [],
            }
            input_cv = {
                "schema_version": "1.0",
                "source": {"image_path": "safe_full.png", "stage": "input_safe_preview"},
                "scene": {"target_type": "emission_nebula"},
                "quality_tags": [],
            }
            workflow = {
                "status": "awaiting_ai_visual_review",
                "local_cv_auxiliary_validation": input_cv,
            }
            with (
                patch("pipeline.recognize_image", return_value=final_payload),
                patch("pipeline.build_recognition_workflow", return_value=workflow),
            ):
                ok = pipeline.run_optional_recognition(
                    input_path=str(input_image),
                    output_path=str(final_image),
                    recognize=True,
                    recognize_output=str(output_json),
                )

            self.assertTrue(ok)
            payload = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(payload["mode"], "hybrid_visual_workflow")
            self.assertEqual(payload["status"], "awaiting_ai_visual_review")
            self.assertIn("input_workflow", payload)

    def test_run_pipeline_auto_resolves_celestial_target(self):
        from astropy.io import fits
        with tempfile.TemporaryDirectory() as td:
            fits_path = Path(td) / "test_m42.fits"
            output_jpg = Path(td) / "test_m42_proc.jpg"
            work_dir = Path(td) / "work"

            # 创建 32x32 的小图像并写入 M42 头部
            data = np.zeros((32, 32), dtype=np.float32)
            hdr = fits.Header()
            hdr['OBJECT'] = 'M 42'
            fits.writeto(fits_path, data, header=hdr, overwrite=True)

            # 运行管线并仅执行 stretch 步骤以缩短时间
            pipeline.run_pipeline(
                input_path=str(fits_path),
                output_path=str(output_jpg),
                preset='light',
                steps='stretch',
                keep_all=True,
                work_dir=str(work_dir)
            )

            manifest_path = work_dir / 'manifest.json'
            self.assertTrue(manifest_path.exists())

            manifest_payload = json.loads(manifest_path.read_text(encoding='utf-8'))
            self.assertEqual(manifest_payload['target_name'], 'M42')
            self.assertEqual(manifest_payload['target_type'], 'emission_nebula')

    def test_run_pipeline_auto_upgrades_to_masked_ghs(self):
        from astropy.io import fits
        with tempfile.TemporaryDirectory() as td:
            fits_path = Path(td) / "test_m31.fits"
            output_jpg = Path(td) / "test_m31_proc.jpg"
            work_dir = Path(td) / "work"

            # 创建 32x32 的非极暗模拟图像，写入 M31 头部（M31 是星系）
            data = np.full((32, 32), 0.03, dtype=np.float32)
            data[10:22, 10:22] = 0.5
            hdr = fits.Header()
            hdr['OBJECT'] = 'M 31'
            fits.writeto(fits_path, data, header=hdr, overwrite=True)

            # 运行管线，由于 M31 会被识别为 galaxy 且 stretch_method 默认为 auto，
            # 应该会被升级为 masked_ghs 拉伸。
            pipeline.run_pipeline(
                input_path=str(fits_path),
                output_path=str(output_jpg),
                preset='light',
                steps='stretch',
                keep_all=True,
                work_dir=str(work_dir)
            )

            manifest_path = work_dir / 'manifest.json'
            self.assertTrue(manifest_path.exists())

            manifest_payload = json.loads(manifest_path.read_text(encoding='utf-8'))
            self.assertEqual(manifest_payload['target_name'], 'M31')
            self.assertEqual(manifest_payload['target_type'], 'galaxy')
            # 验证 manifest 中记录的拉伸方法是否为 masked_ghs
            self.assertEqual(manifest_payload['stretch_method'], 'masked_ghs')


if __name__ == "__main__":
    unittest.main()
