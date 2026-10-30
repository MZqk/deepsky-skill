from __future__ import annotations

import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
import pytest
from PIL import Image

import siril_auto_samples as auto_samples  # noqa: E402

from ._fits_bytes import _fits_hdu


def _make_test_fits(width: int = 200, height: int = 150) -> bytes:
    # Synthetic float32 data with background around 0.05 and a bright central spot
    data_list = []
    for y in range(height):
        for x in range(width):
            dx = x - width / 2.0
            dy = y - height / 2.0
            dist = (dx * dx + dy * dy) ** 0.5
            val = 0.05 + 0.002 * (x / float(width))
            if dist < 20:
                val += 0.8 * (1.0 - dist / 20.0)
            data_list.append(val)
    raw_pixels = b"".join(struct.pack(">f", val) for val in data_list)
    return _fits_hdu(
        [
            ("SIMPLE", True),
            ("BITPIX", -32),
            ("NAXIS", 2),
            ("NAXIS1", width),
            ("NAXIS2", height),
            ("EXTEND", True),
        ],
        raw_pixels,
    )


class TestSirilAutoSamples(unittest.TestCase):
    def test_generate_samples_from_fits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fits_path = tmp_path / "test_source.fit"
            fits_path.write_bytes(_make_test_fits(width=320, height=240))

            contract = auto_samples.generate_background_samples(
                fits_path, target_count=24, target_type="general"
            )

            self.assertEqual(
                contract["schema"], "starun-siril.background-sample-contract.v1"
            )
            self.assertEqual(contract["source"]["width"], 320)
            self.assertEqual(contract["source"]["height"], 240)
            self.assertEqual(contract["source"]["path"], str(fits_path.resolve()))
            self.assertEqual(len(contract["source"]["sha256"]), 64)

            samples = contract["fit_samples"]
            self.assertGreaterEqual(len(samples), 8)
            self.assertLessEqual(len(samples), 36)

            seen_ids = set()
            seen_coords = set()
            for s in samples:
                self.assertIn("id", s)
                self.assertIn("x", s)
                self.assertIn("y", s)
                self.assertNotIn(s["id"], seen_ids)
                self.assertNotIn((s["x"], s["y"]), seen_coords)
                seen_ids.add(s["id"])
                seen_coords.add((s["x"], s["y"]))

                # Coordinate safety checks
                self.assertGreaterEqual(s["x"], 0.0)
                self.assertLess(s["x"], 320.0)
                self.assertGreaterEqual(s["y"], 0.0)
                self.assertLess(s["y"], 240.0)

                # Bright central nebula (center 160, 120, r=15) should be avoided
                dist_to_center = ((s["x"] - 160) ** 2 + (s["y"] - 120) ** 2) ** 0.5
                self.assertGreater(
                    dist_to_center,
                    10.0,
                    f"Sample {s} landed too close to bright center!",
                )

    def test_cli_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            fits_path = tmp_path / "test_cli.fit"
            fits_path.write_bytes(_make_test_fits(width=300, height=240))
            output_json = tmp_path / "contract.json"

            rc = auto_samples.main(
                [
                    "--source",
                    str(fits_path),
                    "--output",
                    str(output_json),
                    "--samples",
                    "16",
                    "--target-type",
                    "emission_nebula",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(output_json.is_file())

            data = json.loads(output_json.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "starun-siril.background-sample-contract.v1")
            self.assertGreater(len(data["fit_samples"]), 0)


if __name__ == "__main__":
    unittest.main()


def write_array_fits(path, values):
    array = np.asarray(values, dtype=">f4")
    if array.ndim == 2:
        array = array[None]
    cards = [("SIMPLE", True), ("BITPIX", -32), ("NAXIS", 3 if len(array) == 3 else 2),
             ("NAXIS1", array.shape[2]), ("NAXIS2", array.shape[1])]
    if len(array) == 3:
        cards.append(("NAXIS3", 3))
    path.write_bytes(_fits_hdu(cards, array.tobytes()))


@pytest.mark.parametrize("width,height", [(640, 360), (360, 640), (240, 240)])
@pytest.mark.parametrize("gradient", [False, True])
def test_grid_aspect_gradient_and_deterministic_selection(tmp_path, width, height, gradient):
    y, x = np.indices((height, width))
    planes = np.full((3, height, width), .04)
    if gradient:
        planes += np.asarray([.006 * x / width + .003 * y / height,
                              -.003 * x / width + .005 * y / height,
                              .002 * x / width - .004 * y / height])
    source = tmp_path / "source.fit"
    write_array_fits(source, planes)
    report = {}
    contract = auto_samples.generate_background_samples(source, diagnostics=report)
    assert report["grid"]["columns"] == 20
    assert report["grid"]["rows"] == int(np.ceil(20 * height / width))
    assert report["coverage"]["status"] == "sufficient"
    assert all(count >= 2 for count in report["coverage"]["horizontal_thirds_top_to_bottom"])
    assert 12 <= len(contract["fit_samples"]) <= 256
    if height > width:
        from deep_sky_siril_background import sample_rect, intersects
        boxes = [sample_rect(p["x"], p["y"]) for p in contract["fit_samples"]]
        assert all(not intersects(a, b) for i, a in enumerate(boxes) for b in boxes[i+1:])
    assert auto_samples.generate_background_samples(source) == contract
    assert report["exclusions"]["structure_blocks"] == 0
    assert report["sampling_source"] == "scientific-fits"
    assert set(contract) == {"schema", "source", "fit_samples"}


def test_area_medians_preserve_extent_and_do_not_alias_stars():
    planes = np.full((3, 613, 1025), .03, dtype=np.float32)
    planes[:, 21, 33] = .8
    medians, peaks, _, _, xs, ys, step = auto_samples._pool_planes(planes)
    assert step == 3 and medians.shape[1:] == (205, 342)
    assert medians[0, 7, 11] == pytest.approx(.03)
    assert peaks[0, 7, 11] == pytest.approx(.8)
    assert xs[-1] == 1024 and ys[-1] == 612


@pytest.mark.parametrize("signal", ["galaxy", "faint-red", "dense-stars"])
def test_full_native_box_avoids_objects_and_random_grain(tmp_path, signal):
    height, width = 480, 640
    y, x = np.indices((height, width))
    rng = np.random.default_rng(431)
    planes = .03 + rng.normal(0, .00003, (3, height, width))
    protected = np.zeros((height, width), dtype=bool)
    if signal in ("galaxy", "faint-red"):
        radius = ((x - 320) / 110) ** 2 + ((y - 240) / 55) ** 2
        protected = radius < 1
        if signal == "galaxy":
            planes += .008 * np.exp(-radius * 2)
        else:
            # A low contrast feature confined to red must remain protected.
            planes[0] += .00025 * np.exp(-radius * 2)
    else:
        for sy, sx in rng.integers([40, 40], [height - 40, width - 40], size=(130, 2)):
            radius = (x - sx) ** 2 + (y - sy) ** 2
            planes += .01 * np.exp(-radius / 5)
            protected |= radius < 16
    source = tmp_path / "source.fit"
    write_array_fits(source, planes)
    contract = auto_samples.generate_background_samples(source, target_type="galaxy" if signal == "galaxy" else "general")
    protected = protected[::-1]
    for point in contract["fit_samples"]:
        sx, sy = int(point["x"]), int(point["y"])
        assert not protected[sy - 12:sy + 13, sx - 12:sx + 13].any()


def test_top_bottom_only_and_narrow_coverage_fail():
    points = [{"x": x, "y": y} for y in (60, 940) for x in range(60, 941, 80)]
    coverage = auto_samples.assess_sample_coverage(points, 1000, 1000)
    assert coverage["status"] == "insufficient"
    assert coverage["horizontal_thirds_top_to_bottom"][1] == 0
    assert "middle third" in " ".join(coverage["reasons"])
    narrow = [{"x": 450 + x, "y": y} for y in range(60, 941, 80) for x in (0, 30)]
    assert auto_samples.assess_sample_coverage(narrow, 1000, 1000)["status"] == "insufficient"


@pytest.mark.parametrize("failure", ["nonfinite", "no-sky", "middle-obstructed", "source-change", "invalid-edge"])
def test_failure_report_without_executable_contract(tmp_path, monkeypatch, failure):
    source, output, report_path = [tmp_path / name for name in ("source.fit", "samples.json", "report.json")]
    planes = np.full((3, 360, 480), .03)
    if failure == "nonfinite":
        planes[0, 20, 20] = np.nan
    elif failure == "invalid-edge":
        planes[:, :160] = 0
        planes[:, 200:] = 0
    write_array_fits(source, planes)
    if failure in ("no-sky", "middle-obstructed"):
        original = auto_samples._exclusion_mask
        def obstruct(*args):
            mask, residuals, counts = original(*args)
            if failure == "no-sky":
                mask[:] = True
            else:
                mask[100:260] = True
            return mask, residuals, counts
        monkeypatch.setattr(auto_samples, "_exclusion_mask", obstruct)
    elif failure == "source-change":
        original = auto_samples._exclusion_mask
        def replace_source(*args):
            result = original(*args)
            source.write_bytes(source.read_bytes() + b"changed")
            return result
        monkeypatch.setattr(auto_samples, "_exclusion_mask", replace_source)
    with pytest.raises(ValueError):
        auto_samples.main(["--source", str(source), "--output", str(output), "--report", str(report_path)])
    assert not output.exists()
    report = json.loads(report_path.read_text())
    assert report["status"] == "failed" and report["reasons"]


def test_cli_quantity_compatibility_and_separate_report(tmp_path):
    source, output, report = [tmp_path / name for name in ("source.fit", "samples.json", "report.json")]
    write_array_fits(source, np.full((3, 360, 640), .03))
    auto_samples.main(["--source", str(source), "--output", str(output), "--samples", "24", "--report", str(report)])
    assert len(json.loads(output.read_text())["fit_samples"]) == 24
    assert json.loads(report.read_text())["grid"]["mode"] == "target-total"
    auto_samples.main(["--source", str(source), "--output", str(output), "--samples-per-line", "8", "--report", str(report)])
    assert json.loads(report.read_text())["grid"]["columns"] == 8
    with pytest.raises(SystemExit):
        auto_samples.build_parser().parse_args(["--source", str(source), "--output", str(output),
                                               "--samples", "24", "--samples-per-line", "20"])
    with pytest.raises(ValueError, match="per-line"):
        auto_samples.main(["--source", str(source), "--output", str(output), "--regions", "--samples-per-line", "20"])
    auto_samples.main(["--source", str(source), "--output", str(output), "--regions"])
    regions = json.loads(output.read_text())["regions"]
    assert 3 <= len(regions) <= 36
    assert all(region["rect"][2] - region["rect"][0] == 16 for region in regions)


def test_xisf_preview_pixel_centers_and_scientific_fits_priority(tmp_path):
    width, height = 180, 320
    source, preview = tmp_path / "source.xisf", tmp_path / "preview.png"
    from .test_xisf_metrics import xisf
    pixels = np.full((height, width, 3), 20, dtype=np.uint8)
    pixels[50:110, 55:125, 0] = 100
    Image.fromarray(pixels).save(preview)
    xisf(source, pixels.transpose(2, 0, 1).astype(float) / 255)
    planes, _, _, domain = auto_samples._load_sampling_image(source, preview)
    assert domain == "scientific-xisf"
    assert planes[0, 70, 80] == pytest.approx(100 / 255)
    assert auto_samples.preview_y(70, height) == 70
    report = {}
    contract = auto_samples.generate_background_samples(source, preview_path=preview, diagnostics=report)
    assert report["preview_is_scientific_parent"] is False
    assert contract["source"]["path"] == str(source)
    for point in contract["fit_samples"]:
        sx, sy = int(point["x"]), int(auto_samples.preview_y(point["y"], height))
        assert not (pixels[sy - 12:sy + 13, sx - 12:sx + 13, 0] > 20).any()
    fits = tmp_path / "source.fit"
    write_array_fits(fits, np.full((3, height, width), .03))
    assert auto_samples.generate_background_samples(fits, preview_path=preview) == auto_samples.generate_background_samples(fits)


def test_preview_changes_or_mismatches_are_rejected(tmp_path, monkeypatch):
    source, preview, output, report = [tmp_path / name for name in ("source.xisf", "preview.png", "samples.json", "report.json")]
    from .test_xisf_metrics import xisf
    xisf(source, np.full((3, 360, 240), .1))
    Image.new("RGB", (240, 360), (20, 30, 40)).save(preview)
    original = auto_samples._exclusion_mask
    def change_preview(*args):
        result = original(*args)
        Image.new("RGB", (240, 360), (30, 30, 40)).save(preview)
        return result
    monkeypatch.setattr(auto_samples, "_exclusion_mask", change_preview)
    with pytest.raises(ValueError, match="preview changed"):
        auto_samples.main(["--source", str(source), "--preview", str(preview), "--output", str(output), "--report", str(report)])
    assert not output.exists()
    Image.new("RGB", (120, 180), (20, 30, 40)).save(preview)
    with pytest.raises(ValueError, match="geometry"):
        auto_samples.generate_background_samples(source, preview_path=preview)


def test_signed_scientific_sky_is_not_zero_filled_padding(tmp_path):
    source = tmp_path / "signed.fit"
    values = np.full((3, 360, 480), -.003)
    values[:, 150:180, 220:250] += .02
    write_array_fits(source, values)
    contract = auto_samples.generate_background_samples(source)
    assert len(contract["fit_samples"]) >= 12
