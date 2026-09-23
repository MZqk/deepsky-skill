from __future__ import annotations

import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

SKILL_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import deep_sky_siril_metrics as metrics_mod  # noqa: E402


def _make_mock_stars_tsv(count: int = 30, base_fwhm: float = 3.2) -> str:
    lines = ["x\ty\tfwhm\troundness\tamplitude\tbackground"]
    for i in range(count):
        fwhm = base_fwhm + (i % 5) * 0.1
        rnd = 0.88 - (i % 3) * 0.02
        lines.append(f"{10.0 * i}\t{15.0 * i}\t{fwhm:.3f}\t{rnd:.3f}\t0.45\t0.02")
    return "\n".join(lines) + "\n"


def _make_test_fits(width: int = 100, height: int = 80, val_base: float = 0.02) -> bytes:
    data_list = []
    for y in range(height):
        for x in range(width):
            val = val_base + 0.001 * (x / float(width))
            data_list.append(val)
    raw_pixels = b"".join(struct.pack(">f", val) for val in data_list)

    header = (
        f"{'SIMPLE  ':<8}= {'T':>20}".ljust(80)
        + f"{'BITPIX  ':<8}= {'-32':>20}".ljust(80)
        + f"{'NAXIS   ':<8}= {'2':>20}".ljust(80)
        + f"{'NAXIS1  ':<8}= {str(width):>20}".ljust(80)
        + f"{'NAXIS2  ':<8}= {str(height):>20}".ljust(80)
        + f"{'EXTEND  ':<8}= {'T':>20}".ljust(80)
        + f"{'END':<80}".ljust(80)
    ).encode("ascii")
    header += b" " * (-len(header) % 2880)
    return header + raw_pixels + b"\0" * (-len(raw_pixels) % 2880)


class TestDeepSkySirilMetrics(unittest.TestCase):
    def test_parse_stars_tsv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tsv_path = Path(tmp_dir) / "stars.tsv"
            tsv_path.write_text(_make_mock_stars_tsv(count=25, base_fwhm=2.8), encoding="utf-8")

            res = metrics_mod.parse_stars_tsv(tsv_path)
            self.assertTrue(res["detected"])
            self.assertEqual(res["star_count"], 25)
            self.assertAlmostEqual(res["fwhm_median"], 3.0, delta=0.2)
            self.assertGreater(res["roundness_median"], 0.80)

    def test_analyze_image_histogram_linear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            fits_path = Path(tmp_dir) / "linear_test.fit"
            fits_path.write_bytes(_make_test_fits(width=80, height=60, val_base=0.01))

            metrics = metrics_mod.analyze_image_histogram(fits_path)
            self.assertIn("bg_median", metrics)
            self.assertIn("shadow_clip_rate", metrics)
            self.assertIn("highlight_sat_rate", metrics)
            self.assertEqual(metrics["shadow_clip_rate"], 0.0)
            self.assertEqual(metrics["highlight_sat_rate"], 0.0)

    def test_evaluate_stage_gates(self) -> None:
        # 1. Background subtraction pass evaluation
        parent_m = {"bg_mad": 0.005}
        cand_m = {"bg_mad": 0.0035, "shadow_clip_rate": 0.0001, "highlight_sat_rate": 0.0}
        gates, verdict = metrics_mod.evaluate_stage_gates(
            "background.subtract", cand_m, parent_metrics=parent_m
        )
        self.assertEqual(verdict, "accept")
        self.assertEqual(gates["background"]["verdict"], "pass")
        self.assertIn("reduced by 30", gates["background"]["evidence"])

        # 2. Severe clipping fail evaluation
        bad_m = {"shadow_clip_rate": 0.08, "highlight_sat_rate": 0.0}
        gates_bad, verdict_bad = metrics_mod.evaluate_stage_gates("stretch", bad_m)
        self.assertEqual(verdict_bad, "reject")
        self.assertEqual(gates_bad["background"]["verdict"], "fail")

    def test_generate_metric_report_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            source_fit = tmp_path / "source.fit"
            candidate_fit = tmp_path / "candidate.fit"
            stars_tsv = tmp_path / "stars.tsv"
            out_json = tmp_path / "metrics.json"

            source_fit.write_bytes(_make_test_fits(width=60, height=40, val_base=0.03))
            candidate_fit.write_bytes(_make_test_fits(width=60, height=40, val_base=0.02))
            stars_tsv.write_text(_make_mock_stars_tsv(count=15, base_fwhm=3.1), encoding="utf-8")

            report = metrics_mod.generate_metric_report(
                run_id="050-deconvolve",
                protocol="restoration.deconvolve",
                candidate_path=candidate_fit,
                parent_path=source_fit,
                stars_tsv_path=stars_tsv,
                output_path=out_json,
            )

            self.assertEqual(report["schema"], "starun-siril.metric-report.v1")
            self.assertEqual(report["run_id"], "050-deconvolve")
            self.assertEqual(report["protocol"], "restoration.deconvolve")
            self.assertIn("gate_evaluations", report)
            self.assertIn("recommended_verdict", report)
            self.assertTrue(out_json.is_file())

            persisted = json.loads(out_json.read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema"], "starun-siril.metric-report.v1")
            self.assertEqual(persisted["stars"]["star_count"], 15)


if __name__ == "__main__":
    unittest.main()
