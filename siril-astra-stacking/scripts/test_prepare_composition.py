"""Focused composition tests; --real adds Siril end-to-end fixtures."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import prepare_composition as composition
from fits_probe import build_card, data_offset, pad_to_block, read_fits_header_with_warnings
from synth_fits import STARS

REAL = False


def write_master(path, label, width=200, height=150, planes=1, shift=0, rotation=False,
                 value=None, count=8):
    cards = [build_card("SIMPLE", "T"), build_card("BITPIX", "-32"),
             build_card("NAXIS", str(2 if planes == 1 else 3)),
             build_card("NAXIS1", str(width)), build_card("NAXIS2", str(height))]
    if planes == 3:
        cards.append(build_card("NAXIS3", "3"))
    cards += [build_card("FILTER", "'" + label + "'"),
              build_card("OBJECT", "'test field'"), build_card("STACKCNT", str(count)),
              build_card("EXPTIME", "300."), build_card("LIVETIME", str(count * 300)),
              build_card("BAYERPAT", "'RGGB'"), build_card("END", "")]
    data = []
    for channel in range(planes):
        for y in range(height):
            for x in range(width):
                sx, sy = (width - 1 - x, height - 1 - y) if rotation else (x, y)
                intensity = 0.03
                for star_x, star_y, brightness in list(STARS) + [(12, 40, 12000), (25, 110, 10000), (145, 30, 15000), (155, 130, 12000)]:
                    squared = (sx - star_x - shift) ** 2 + (sy - star_y) ** 2
                    if squared < 225:
                        intensity += brightness / 65535 * math.exp(-squared / 7)
                data.append(value if value is not None else intensity * (1 + channel * 0.1))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pad_to_block("".join(cards).encode("ascii"))
                     + pad_to_block(struct.pack(">%df" % len(data), *data)))


def make_run(root, specs):
    for label, options in specs:
        master = root / label / (label + ".fit")
        write_master(master, label, **options)
        report = {"status": "ok", "completed_stages": ["commit"],
                  "artifacts": {"master": str(master)},
                  "stacking": {"frames_stacked": options.get("count", 8)},
                  "warnings": [], "calibration": {"warnings": []}}
        (root / label / (label + "_report.json")).write_text(json.dumps(report))


def values(path):
    header, _ = read_fits_header_with_warnings(str(path))
    count = header["NAXIS1"] * header["NAXIS2"] * header.get("NAXIS3", 1)
    with path.open("rb") as handle:
        handle.seek(data_offset(str(path)))
        return struct.unpack(">%df" % count, handle.read(count * 4))


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_collection_and_quality(self):
        make_run(self.root, [("R", {"planes": 3}), ("H", {"value": 0}), ("S", {"count": 2})])
        layers = composition.collect_inputs(self.root)
        self.assertEqual([r["filter"] for r in layers], ["R", "H", "S"])
        self.assertEqual(layers[0]["conversion"], "equal_mean_rgb")
        self.assertIn("nonpositive", layers[1]["quality_warnings"][0])
        self.assertIn("fewer", layers[2]["quality_warnings"][0])

    def test_bad_reports_and_missing_master(self):
        make_run(self.root, [("R", {}), ("B", {})])
        report = self.root / "B/B_report.json"
        original = json.loads(report.read_text())
        for modification in ({"status": "failed"}, {"artifacts": {"master": "/missing.fit"}},
                             {"stacking": {"frames_stacked": 99}}):
            report.write_text(json.dumps(dict(original, **modification)))
            with self.assertRaises(composition.FitsError):
                composition.collect_inputs(self.root)

    def test_duplicate_filter_and_wrong_target(self):
        make_run(self.root, [("R", {}), ("B", {})])
        write_master(self.root / "B/B.fit", "R")
        with self.assertRaisesRegex(composition.FitsError, "duplicate"):
            composition.collect_inputs(self.root)
        write_master(self.root / "B/B.fit", "B")
        path = self.root / "B/B.fit"
        path.write_bytes(path.read_bytes().replace(b"'test field'", b"'otherfield'"))
        with self.assertRaisesRegex(composition.FitsError, "OBJECT"):
            composition.collect_inputs(self.root)

    def test_nonfinite_and_truncated(self):
        path = self.root / "nan.fit"
        write_master(path, "R", width=2, height=2, value=math.nan)
        with self.assertRaisesRegex(composition.FitsError, "non-finite"):
            composition.inspect_pixels(path)
        write_master(path, "R", width=2, height=2)
        path.write_bytes(path.read_bytes()[:data_offset(str(path)) + 4])
        with self.assertRaisesRegex(composition.FitsError, "truncated"):
            composition.inspect_pixels(path)

    def test_unsafe_siril_string_is_rejected(self):
        make_run(self.root, [("R", {}), ("B", {})])
        path = self.root / "B/B.fit"
        path.write_bytes(path.read_bytes().replace(b"'test field'", b"'bad\"object'"))
        with self.assertRaisesRegex(composition.FitsError, "unsupported"):
            composition.collect_inputs(self.root)

    def test_registration_requires_all_filters(self):
        def row(index, matrix):
            return {"index": index, "h": matrix}
        reg = {"frames": [row(0, composition.IDENTITY), row(1, composition.IDENTITY)],
               "warnings": [], "reference_image": 1}
        self.assertTrue(composition.validate_registration(reg, 2))
        reg["frames"][1]["h"] = (1, 0, 3, 0, 1, 0, 0, 0, 1)
        self.assertFalse(composition.validate_registration(reg, 2))
        reg["frames"].pop()
        with self.assertRaises(composition.FitsError):
            composition.validate_registration(reg, 2)

    def test_existing_output_and_publish_failure(self):
        work, out = self.root / "work", self.root / "out"
        work.mkdir()
        out.mkdir()
        marker = out / "previous"
        marker.write_text("unchanged")
        with self.assertRaises(FileExistsError):
            composition.publish(work, out)
        self.assertEqual(marker.read_text(), "unchanged")
        fresh = self.root / "fresh"
        with patch.object(composition.os, "rename", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                composition.publish(work, fresh)
        self.assertTrue(work.exists())
        self.assertFalse(fresh.exists())

    def test_collect_inputs_captures_preview(self):
        make_run(self.root, [("R", {}), ("B", {})])
        preview_file = self.root / "R/R.png"
        preview_file.write_bytes(b"fake_png")
        report_path = self.root / "R/R_report.json"
        rep = json.loads(report_path.read_text())
        rep["artifacts"]["preview"] = str(preview_file)
        report_path.write_text(json.dumps(rep))

        layers = composition.collect_inputs(self.root)
        r_layer = next(l for l in layers if l["filter"] == "R")
        b_layer = next(l for l in layers if l["filter"] == "B")
        self.assertEqual(r_layer["preview_source"], str(preview_file.resolve()))
        self.assertIsNone(b_layer["preview_source"])

    def test_bundle_structure_publishing(self):
        make_run(self.root, [("R", {}), ("B", {})])
        preview_file = self.root / "R/R.png"
        preview_file.write_bytes(b"fake_png")
        report_path = self.root / "R/R_report.json"
        rep = json.loads(report_path.read_text())
        rep["artifacts"]["preview"] = str(preview_file)
        report_path.write_text(json.dumps(rep))

        with patch.object(composition, "find_siril", return_value="/bin/true"), \
             patch.object(composition, "siril_version", return_value="siril 1.4.4"), \
             patch.object(composition, "prepare_mono"), \
             patch.object(composition, "align_layers") as mock_align, \
             patch.object(composition, "inspect_pixels", side_effect=lambda p: (
                 {"BITPIX": -32, "NAXIS1": 200, "NAXIS2": 150, "NAXIS": 2, "FILTER": "R" if "R" in str(p) else "B",
                  "STACKCNT": 8, "EXPTIME": 300.0, "LIVETIME": 2400.0, "OBJECT": "test field"},
                 {"pixel_count": 30000, "all_pixels_finite": True, "zero_pct": 0, "min": 0, "max": 1, "medians": {"m": 0.05}},
                 []
             )):
            def fake_align(siril, work, layers, mono, timeout):
                for layer in layers:
                    (work / (layer["filter"] + ".fit")).write_bytes(b"fake_fit")
                return {"frames": [{"h": composition.IDENTITY}, {"h": composition.IDENTITY}], "reference_image": 0}, True
            mock_align.side_effect = fake_align

            out = self.root / "bundle_out"
            args = argparse.Namespace(
                run_dir=str(self.root), out=str(out), monochrome=True,
                bundle=True, siril=None, timeout=60
            )
            manifest = composition.prepare(args)
            self.assertTrue(manifest["bundle"])
            self.assertTrue((out / "R.fit").is_file())
            self.assertTrue((out / "B.fit").is_file())
            self.assertTrue((out / "previews" / "R_preview.png").is_file())
            self.assertTrue((out / "diagnostics" / "composition_manifest.json").is_file())
            self.assertTrue((out / "README.md").is_file())
            self.assertFalse((out / "03_raw_masters_archive").exists())


class RealCompositionTests(unittest.TestCase):
    tearDown = CompositionTests.tearDown
    def setUp(self):
        if not REAL:
            self.skipTest("pass --real for Siril fixtures")
        CompositionTests.setUp(self)
        self.siril = composition.find_siril()
        if not self.siril:
            self.skipTest("Siril unavailable")

    def test_mixed_size_rotation_and_exposure(self):
        make_run(self.root, [
            ("L", {"planes": 3}), ("R", {"width": 230, "height": 170, "shift": 3}),
            ("G", {"width": 210, "height": 160, "shift": 6, "rotation": True}),
        ])
        before = {p: p.read_bytes() for p in self.root.glob("*/*")}
        out = self.root / "composition"
        args = argparse.Namespace(run_dir=str(self.root), out=str(out), monochrome=True,
                                  siril=self.siril, timeout=60)
        composition.prepare(args)
        manifest = json.loads((out / "composition_manifest.json").read_text())
        self.assertEqual(manifest["status"], "ok")
        self.assertEqual(manifest["failed_stage"], None)
        shapes = set()
        for label in ("L", "R", "G"):
            header, _, _ = composition.inspect_pixels(out / (label + ".fit"))
            self.assertEqual(header["NAXIS"], 2)
            self.assertNotIn("BAYERPAT", header)
            self.assertEqual(header["STACKCNT"], 8)
            self.assertEqual(header["LIVETIME"], 2400)
            shapes.add((header["NAXIS1"], header["NAXIS2"]))
        self.assertEqual(len(shapes), 1)
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))

    def test_mono_conversion(self):
        make_run(self.root, [("R", {"planes": 3}), ("G", {})])
        layers = composition.collect_inputs(self.root)
        work = self.root / "work"
        work.mkdir()
        mono = composition.prepare_mono(self.siril, work, layers, 60)
        rgb = values(self.root / "R/R.fit")
        length = len(rgb) // 3
        expected = [(rgb[i] + rgb[i + length] + rgb[i + 2 * length]) / 3 for i in range(length)]
        actual = values(mono / "channel0000.fit")
        self.assertLess(max(abs(a - b) for a, b in zip(actual, expected)), 1.5e-7)
        self.assertEqual(values(mono / "channel0001.fit"), values(self.root / "G/G.fit"))

    def test_identity_copy_and_crop(self):
        make_run(self.root, [("R", {}), ("G", {"width": 230, "height": 170})])
        layers = composition.collect_inputs(self.root)
        work = self.root / "work"
        work.mkdir()
        mono = composition.prepare_mono(self.siril, work, layers, 60)
        regdata = {"frames": [{"index": i, "h": composition.IDENTITY} for i in range(2)],
                   "warnings": [], "reference_image": 0}
        with patch.object(composition, "read_seq_regdata", return_value=regdata):
            _, identity = composition.align_layers(self.siril, work, layers, mono, 60)
        self.assertTrue(identity)
        for f in ("R", "G"):
            header, _, _ = composition.inspect_pixels(work / (f + ".fit"))
            self.assertEqual((header["NAXIS1"], header["NAXIS2"]), (200, 150))
        self.assertEqual(values(work / "R.fit"), values(mono / "channel0000.fit"))

    def test_failed_publish_preserves_sources(self):
        make_run(self.root, [("R", {}), ("G", {})])
        before = {p: p.read_bytes() for p in self.root.glob("*/*")}
        out = self.root / "composition"
        args = argparse.Namespace(run_dir=str(self.root), out=str(out), monochrome=True,
                                  siril=self.siril, timeout=60)
        with patch.object(composition, "publish", side_effect=OSError("publish blocked")):
            with self.assertRaises(OSError):
                composition.prepare(args)
        failure = next(self.root.glob(".composition-*/failure.json"))
        self.assertEqual(json.loads(failure.read_text())["failed_stage"], "publish")
        self.assertFalse(out.exists())
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))

    def test_failed_siril_preserves_sources(self):
        make_run(self.root, [("R", {}), ("G", {})])
        before = {p: p.read_bytes() for p in self.root.glob("*/*")}
        out = self.root / "composition"
        args = argparse.Namespace(run_dir=str(self.root), out=str(out), monochrome=True,
                                  siril=self.siril, timeout=60)
        with patch.object(composition, "run_script", side_effect=RuntimeError("Siril failed")):
            with self.assertRaises(RuntimeError):
                composition.prepare(args)
        self.assertFalse(out.exists())
        self.assertEqual(len(list(self.root.glob(".composition-*/failure.json"))), 1)
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))


if __name__ == "__main__":
    import sys
    REAL = "--real" in sys.argv
    if REAL:
        sys.argv.remove("--real")
    unittest.main()
