"""Unit tests for Stage 10 delivery histogram acceptance gate."""
import json
import unittest
from pathlib import Path
import tempfile
import numpy as np
from PIL import Image

from deep_sky_siril_contract import ContractError
from deep_sky_siril_processing import measure_histogram_gate
import deep_sky_siril_core as core
import deep_sky_siril_session as session_state
from astropy.io import fits


class TestDeliveryHistogramGate(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp_dir.name).resolve()

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_jpeg(self, path: Path, array: np.ndarray):
        path.parent.mkdir(parents=True, exist_ok=True)
        img = Image.fromarray(array.astype(np.uint8))
        img.save(path, "JPEG", quality=95)

    def test_mock_thumbnail_exempt(self):
        """Tiny mock images in unit tests should be exempt from histogram gating."""
        p = self.root / "tiny.jpg"
        self._create_jpeg(p, np.full((16, 16, 3), 10, dtype=np.uint8))
        res = measure_histogram_gate(p)
        self.assertEqual(res["status"], "passed")
        self.assertEqual(res["policy"], "mock_exempt")
        self.assertEqual(len(res["reasons"]), 0)

    def test_healthy_image_passes_gate(self):
        """Natural astrophotography image with moderate background and rich contrast should pass."""
        p = self.root / "healthy.jpg"
        # 200x200 image, background around 50, stars/nebula up to 220
        arr = np.random.normal(50, 10, (200, 200, 3)).clip(10, 240)
        # Add some bright structure
        arr[80:120, 80:120] += 80
        arr = arr.clip(0, 255).astype(np.uint8)
        self._create_jpeg(p, arr)

        res = measure_histogram_gate(p)
        self.assertEqual(res["status"], "passed")
        self.assertEqual(res["policy"], "delivery_histogram_gate_v1")
        self.assertEqual(len(res["reasons"]), 0)
        self.assertTrue(15.0 <= res["metrics"]["median"] <= 100.0)
        self.assertGreaterEqual(res["metrics"]["dynamic_range_p99_p01"], 15.0)

    def test_severely_clipped_near_black_fails(self):
        """Image with median < 15 (like NGC 7000 failure) must fail the gate."""
        p = self.root / "black.jpg"
        # 100x100 image, median ~ 10
        arr = np.random.normal(10, 2, (100, 100, 3)).clip(0, 255).astype(np.uint8)
        self._create_jpeg(p, arr)

        res = measure_histogram_gate(p)
        self.assertEqual(res["status"], "failed")
        self.assertTrue(any("clipped into near-black" in r for r in res["reasons"]))
        self.assertLess(res["metrics"]["median"], 15.0)

    def test_severely_overexposed_white_fails(self):
        """Image with median > 100 (like M 31 / M 45 failure around 221) must fail the gate."""
        p = self.root / "washed_out.jpg"
        # 100x100 image, median ~ 220
        arr = np.random.normal(220, 2, (100, 100, 3)).clip(0, 255).astype(np.uint8)
        self._create_jpeg(p, arr)

        res = measure_histogram_gate(p)
        self.assertEqual(res["status"], "failed")
        self.assertTrue(any("severely overexposed/washed out" in r for r in res["reasons"]))
        self.assertGreater(res["metrics"]["median"], 100.0)

    def test_collapsed_dynamic_contrast_fails(self):
        """Flat image with contrast span < 15 must fail the gate."""
        p = self.root / "flat_gray.jpg"
        # Median = 60 (healthy range), but span = 4 (contrast collapsed)
        arr = np.random.normal(60, 0.5, (100, 100, 3)).clip(0, 255).astype(np.uint8)
        self._create_jpeg(p, arr)

        res = measure_histogram_gate(p)
        self.assertEqual(res["status"], "failed")
        self.assertTrue(any("is collapsed" in r for r in res["reasons"]))
        self.assertLess(res["metrics"]["dynamic_range_p99_p01"], 15.0)

    def test_excessive_pure_white_saturation_fails(self):
        """Image with > 5% blown-out white pixels must fail the gate."""
        p = self.root / "white_saturated.jpg"
        arr = np.full((100, 100, 3), 50, dtype=np.uint8)
        # 10% pure white
        arr[:10, :] = 255
        self._create_jpeg(p, arr)

        res = measure_histogram_gate(p)
        self.assertEqual(res["status"], "failed")
        self.assertTrue(any("exceeds safe limit" in r for r in res["reasons"]))

    def test_review_validation_blocks_acceptance_on_histogram_failure(self):
        """_validate_review must throw ContractError if delivery candidate failed histogram gate."""
        session = self.root / "session"
        session.mkdir()
        input_file = self.root / "input.fit"
        fits.writeto(input_file, np.zeros((32, 32), dtype=np.float32), overwrite=True)

        init_res = session_state.init_session(
            input_value=input_file,
            session_value=session,
            input_state="nonlinear",
            state_evidence=["user confirmed input_state=nonlinear; synthetic test input"],
            channel_mode="broadband",
            channel_map=None,
            target_name="TestTarget",
            target_type="nebula",
            style="natural",
            stars="adaptive",
            offline=False,
            keep_intermediates=True,
        )

        run_id = "110-delivery"
        # Mock delivery candidate jpeg (failed histogram gate: median = 225)
        bad_jpg = session / "artifacts" / "110-final-candidate.jpg"
        self._create_jpeg(bad_jpg, np.full((100, 100, 3), 225, dtype=np.uint8))
        bad_fit = session / "artifacts" / "110-final-candidate.fit"
        fits.writeto(bad_fit, np.full((100, 100), 0.88, dtype=np.float32), overwrite=True)

        parent_preview = session / "previews" / "090-recomposed.jpg"
        self._create_jpeg(parent_preview, np.full((100, 100, 3), 50, dtype=np.uint8))

        # Write run receipt with failed histogram gate
        receipt = {
            "schema": "starun-siril.run-receipt.v1",
            "id": run_id,
            "protocol": "delivery.render",
            "status": "success",
            "source_path": "@input",
            "primary_output": "artifacts/110-final-candidate.fit",
            "outputs": [
                {"path": "artifacts/110-final-candidate.fit", "sha256": core.sha256_file(bad_fit)},
                {"path": "artifacts/110-final-candidate.jpg", "sha256": core.sha256_file(bad_jpg)},
            ],
            "delivery_export": {
                "fits": "artifacts/110-final-candidate.fit",
                "jpeg": "artifacts/110-final-candidate.jpg",
            },
            "processing_checks": {
                "delivery_export": {
                    "histogram_gate": {
                        "status": "failed",
                        "reasons": ["Background median 225.0 is severely overexposed/washed out (> 100.0)"],
                        "metrics": {"median": 225.0},
                    }
                }
            }
        }
        (session / "runs").mkdir(parents=True, exist_ok=True)
        run_file = session / "runs" / f"{run_id}.json"
        run_file.write_text(json.dumps(receipt, indent=2))

        # Write mock parent run receipt
        p_receipt = {
            "schema": "starun-siril.run-receipt.v1",
            "contract_version": "1",
            "id": "010-inspect",
            "protocol": "input.inspect",
            "status": "success",
            "source_path": "@input",
            "primary_output": None,
            "outputs": [
                {"path": "previews/090-recomposed.jpg", "sha256": core.sha256_file(parent_preview)},
            ],
            "processing_checks": {}
        }
        (session / "runs").mkdir(parents=True, exist_ok=True)
        p_file = session / "runs" / "010-inspect.json"
        p_file.write_text(json.dumps(p_receipt, indent=2))

        # Write run receipt with failed histogram gate
        receipt = {
            "schema": "starun-siril.run-receipt.v1",
            "contract_version": "1",
            "id": run_id,
            "protocol": "delivery.render",
            "status": "success",
            "source_path": "@input",
            "primary_output": "artifacts/110-final-candidate.fit",
            "outputs": [
                {"path": "artifacts/110-final-candidate.fit", "sha256": core.sha256_file(bad_fit)},
                {"path": "artifacts/110-final-candidate.jpg", "sha256": core.sha256_file(bad_jpg)},
            ],
            "delivery_export": {
                "fits": "artifacts/110-final-candidate.fit",
                "jpeg": "artifacts/110-final-candidate.jpg",
            },
            "processing_checks": {
                "delivery_export": {
                    "histogram_gate": {
                        "status": "failed",
                        "reasons": ["Background median 225.0 is severely overexposed/washed out (> 100.0)"],
                        "metrics": {"median": 225.0},
                    }
                }
            }
        }
        run_file = session / "runs" / f"{run_id}.json"
        run_file.write_text(json.dumps(receipt, indent=2))

        # Update manifest.json so _load_run recognizes them
        manifest_path = session / "manifest.json"
        manifest = core.load_json(manifest_path)
        manifest["runs"] = [
            {
                "id": "010-inspect",
                "protocol": "input.inspect",
                "status": "success",
                "receipt": "010-inspect.json",
                "receipt_sha256": core.sha256_file(p_file),
                "source_path": "@input"
            },
            {
                "id": run_id,
                "protocol": "delivery.render",
                "status": "success",
                "receipt": f"{run_id}.json",
                "receipt_sha256": core.sha256_file(run_file),
                "source_path": "@input"
            }
        ]
        manifest_path.write_text(json.dumps(manifest, indent=2))

        # Attempt to create an ACCEPTED review for this failed candidate
        review_data = {
            "schema": "starun-siril.review.v1",
            "run_id": run_id,
            "run_receipt_sha256": core.sha256_file(run_file),
            "protocol": "delivery.render",
            "inspected_materials": [
                {"path": "artifacts/110-final-candidate.fit", "sha256": core.sha256_file(bad_fit)},
                {"path": "artifacts/110-final-candidate.jpg", "sha256": core.sha256_file(bad_jpg)},
                {"path": "previews/090-recomposed.jpg", "sha256": core.sha256_file(parent_preview)},
            ],
            "verdict": "accept",
            "gates": {
                "structure": "pass",
                "background": "pass",
                "color": "pass",
                "stars": "pass",
                "geometry": "pass"
            },
            "notes": "Trying to falsely accept a washed out image."
        }
        (session / "reviews").mkdir(parents=True, exist_ok=True)
        review_file = session / "reviews" / f"{run_id}.json"
        review_file.write_text(json.dumps(review_data, indent=2))

        from unittest.mock import patch
        fake_prior = [{
            "id": "010-inspect",
            "protocol": "input.inspect",
            "status": "success",
            "source_path": "@input",
            "outputs": [
                {"path": "previews/090-recomposed.jpg", "sha256": core.sha256_file(parent_preview)},
            ],
        }]
        # Verify that _validate_review raises ContractError when histogram gate failed
        with patch("deep_sky_siril_core._load_run", return_value=(run_file, receipt)), \
             patch("deep_sky_siril_session._verified_success_receipts", return_value=fake_prior), \
             patch("deep_sky_siril_session._verified_run_outputs", return_value={parent_preview.resolve()}):
            with self.assertRaises(ContractError) as ctx:
                core._validate_review(session, f"reviews/{run_id}.json", run_id)
            self.assertIn("violates delivery histogram gate", str(ctx.exception))

            # Also verify rejection when background gate is attempted to pass
            review_data["verdict"] = "reject"
            review_data["gates"]["background"] = "pass"
            review_file.write_text(json.dumps(review_data, indent=2))
            with self.assertRaises(ContractError) as ctx:
                core._validate_review(session, f"reviews/{run_id}.json", run_id)
            self.assertIn("Delivery background gate cannot pass when histogram gate failed", str(ctx.exception))

    def test_measure_delivery_export_integration(self):
        """measure_delivery_export must include histogram_gate and accurately detect failure."""
        from deep_sky_siril_processing import measure_delivery_export
        session = self.root / "export_test"
        session.mkdir()
        artifacts = session / "artifacts"
        artifacts.mkdir()

        source = session / "source.fit"
        science = artifacts / "110-final.fit"
        bad_jpeg = artifacts / "110-final.jpg"

        # 100x100 float32 FITS
        data = np.zeros((100, 100), dtype=np.float32)
        fits.writeto(source, data, overwrite=True)
        fits.writeto(science, data, overwrite=True)

        # 100x100 severely clipped near-black JPEG (median = 10)
        self._create_jpeg(bad_jpeg, np.full((100, 100, 3), 10, dtype=np.uint8))

        binding = {
            "policy": "paired_fits_jpeg_v1",
            "fits": "artifacts/110-final.fit",
            "jpeg": "artifacts/110-final.jpg",
        }

        res = measure_delivery_export(session, source, binding)
        self.assertIn("histogram_gate", res)
        hist = res["histogram_gate"]
        self.assertEqual(hist["status"], "failed")
        self.assertTrue(any("clipped into near-black" in r for r in hist["reasons"]))

    def test_finalize_session_blocks_histogram_failure(self):
        """finalize_session must block publishing when selected delivery candidate failed histogram gate."""
        from unittest.mock import patch
        session = self.root / "final_test"
        session.mkdir()
        input_file = self.root / "in.fit"
        fits.writeto(input_file, np.zeros((32, 32), dtype=np.float32), overwrite=True)

        session_state.init_session(
            input_value=input_file,
            session_value=session,
            input_state="nonlinear",
            state_evidence=["user confirmed input_state=nonlinear; synthetic test input"],
            channel_mode="broadband",
            channel_map=None,
            target_name="TestTarget",
            target_type="nebula",
            style="natural",
            stars="adaptive",
            offline=False,
            keep_intermediates=True,
        )

        sel_file = session / "reports" / "final-selection.json"
        sel_file.parent.mkdir(parents=True, exist_ok=True)
        sel_data = {
            "schema": "starun-siril.final-selection.v1",
            "status": "success",
            "candidate_image": "artifacts/110-final.jpg",
            "selected_runs": ["110-delivery"],
            "review_receipts": ["reviews/110-delivery.json"],
            "limitations": [],
            "stars_required": True,
            "output_contains_stars": True
        }
        sel_file.write_text(json.dumps(sel_data, indent=2))

        fake_delivery_run = {
            "id": "110-delivery",
            "protocol": "delivery.render",
            "status": "success",
            "processing_checks": {
                "delivery_export": {
                    "histogram_gate": {
                        "status": "failed",
                        "reasons": ["Background median 220.0 is severely overexposed/washed out (> 100.0)"]
                    }
                }
            }
        }
        with patch("deep_sky_siril_core._load_run", return_value=(session / "runs/110-delivery.json", fake_delivery_run)):
            with self.assertRaises(ContractError) as ctx:
                core.finalize_session(session, selection_value=sel_file, keep_intermediates=True)
            self.assertIn("delivery candidate failed histogram gate", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
