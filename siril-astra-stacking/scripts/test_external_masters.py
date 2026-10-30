"""Explicit external-master provenance and native FITS/XISF handoff."""
from __future__ import annotations

import argparse
import array
import json
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import compose_channels
import prepare_composition as preparation
from fits_probe import data_offset
from test_batch_filters import update_header
from test_prepare_composition import write_master

REAL = False


def write_xisf(path, fits):
    offset = data_offset(str(fits))
    header, _ = preparation.read_fits_header_with_warnings(str(fits))
    payload = array.array("f")
    count = header["NAXIS1"] * header["NAXIS2"]
    raw = fits.read_bytes()[offset:offset + count * 4]
    row_bytes = header["NAXIS1"] * 4
    # Native XISF/FITS loading uses opposite row orders for these fixtures.
    payload.frombytes(b"".join(raw[start:start + row_bytes]
                               for start in range(len(raw) - row_bytes, -1, -row_bytes)))
    payload.byteswap()
    document = ET.Element("xisf", {"version": "1.0"})
    image = ET.SubElement(document, "Image", {
        "geometry": "%d:%d:1" % (header["NAXIS1"], header["NAXIS2"]),
        "sampleFormat": "Float32", "colorSpace": "Gray", "location": "attachment:4096:%d" % (count * 4),
    })
    ET.SubElement(image, "FITSKeyword", {"name": "FILTER", "value": "'" + header["FILTER"] + "'"})
    ET.SubElement(image, "FITSKeyword", {"name": "EXPTIME", "value": "300"})
    ET.SubElement(image, "FITSKeyword", {"name": "HISTORY", "value": "",
                                         "comment": "DrizzleIntegration.numberOfImages: 8"})
    xml = ET.tostring(document, encoding="utf-8")
    prefix = b"XISF0100" + struct.pack("<II", len(xml), 0) + xml
    path.write_bytes(prefix + b"\0" * (4096 - len(prefix)) + payload.tobytes())


def make_masters(directory, real=False):
    directory.mkdir()
    for label in ("L", "R", "G", "B", "HA"):
        path = directory / (label + ".fit")
        write_master(path, label, width=200 if real else 16, height=150 if real else 12,
                     value=None if real else 0.05)
        update_header(path, {"OBJECT": None, "BAYERPAT": None, "STACKCNT": None, "LIVETIME": None})
        if label in ("R", "HA"):
            write_xisf(path.with_suffix(".xisf"), path)
            path.unlink()


class ExternalMasterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.inputs = self.root / "source masters"
        make_masters(self.inputs)
        self.args = argparse.Namespace(run_dir=str(self.inputs), out=str(self.root / "prepared"),
                                       monochrome=True, bundle=True, siril=None, timeout=60,
                                       external_masters=True, linear=True, target="M51")

    def tearDown(self):
        self.temp.cleanup()

    def fake_run(self, siril, directory, name, builder, timeout):
        builder.write(str(directory / (name + ".sir")))
        (directory / (name + ".log")).write_text("external import fixture")
        if name == "import_external":
            for index, alias in enumerate(sorted(directory.glob("source_*"))):
                header, _ = preparation.external_metadata(alias)
                output = directory / ("converted_%04d.fit" % index)
                write_master(output, header["FILTER"], width=16, height=12, value=0.05)
                update_header(output, {"OBJECT": "M51", "BAYERPAT": None, "STACKCNT": None, "LIVETIME": None})
        elif name == "prepare_mono":
            for index, alias in enumerate(sorted(directory.glob("source_*.fit"))):
                shutil.copyfile(alias, directory.parent / ("mono/channel%04d.fit" % index))

    def fake_align(self, siril, work, layers, mono, timeout):
        for index, layer in enumerate(layers):
            shutil.copyfile(mono / ("channel%04d.fit" % index), work / (layer["filter"] + ".fit"))
        return {"reference_image": 0, "frames": [{"h": preparation.IDENTITY} for _ in layers]}, True

    def run_import(self):
        with patch.object(preparation, "find_siril", return_value="siril"), \
             patch.object(preparation, "siril_version", return_value="siril 1.4.4"), \
             patch.object(preparation, "run_script", side_effect=self.fake_run), \
             patch.object(preparation, "align_layers", side_effect=self.fake_align):
            return preparation.prepare(self.args)

    def test_explicit_import_provenance_nullable_exposure_and_handoff(self):
        before = {path: preparation.sha256_file(path) for path in self.inputs.iterdir()}
        manifest = self.run_import()
        self.assertEqual(manifest["schema_version"], "1.1")
        self.assertEqual(manifest["origin"], "external_masters")
        self.assertTrue(manifest["linear_confirmed"])
        _, _, selected = compose_channels.collect_prepared(self.args.out, "halrgb")
        self.assertEqual(len(selected), 5)
        self.assertEqual(selected["HA"]["exposure"]["stacked_frames"], 8)
        self.assertEqual(selected["HA"]["exposure"]["total_seconds"], None)
        self.assertIsNone(selected["G"]["exposure"]["stacked_frames"])
        for source in selected.values():
            self.assertEqual(source["origin"], "external_masters")
            self.assertEqual(source["header"]["OBJECT"], "M51")
            self.assertNotIn("STACKCNT", source["header"])
            self.assertNotIn("LIVETIME", source["header"])
        for path, digest in before.items():
            self.assertEqual(preparation.sha256_file(path), digest)
        self.assertFalse((Path(self.args.out) / "external").exists())

    def test_missing_confirmation_conflicts_duplicates_and_invalid_metadata(self):
        self.args.linear = False
        with self.assertRaisesRegex(preparation.FitsError, "requires"):
            preparation.prepare(self.args)
        self.assertFalse(Path(self.args.out).exists())
        self.args.linear = True
        path = self.inputs / "G.fit"
        original = path.read_bytes()
        for change in ({"OBJECT": "other target"}, {"FILTER": "B"}, {"EXPTIME": -1}, {"STACKCNT": 0}):
            update_header(path, change)
            with self.assertRaises(preparation.FitsError):
                self.run_import()
            self.assertFalse(Path(self.args.out).exists())
            path.write_bytes(original)

    def test_external_schema_requires_explicit_origin_and_linear_state(self):
        self.run_import()
        path = Path(self.args.out) / "diagnostics/composition_manifest.json"
        original = path.read_text()
        for change in ({"origin": "astra_stacks"}, {"linear_confirmed": False}, {"schema_version": "1.0"}):
            manifest = json.loads(original)
            manifest.update(change)
            path.write_text(json.dumps(manifest))
            with self.assertRaises(preparation.FitsError):
                compose_channels.collect_prepared(self.args.out, "halrgb")


@unittest.skipUnless(REAL, "pass --real for native external-master fixtures")
class RealExternalMasterTests(unittest.TestCase):
    def test_unicode_sources_native_import_and_halrgb(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs = root / "外部 母版"
            make_masters(inputs, real=True)
            before = {path: preparation.sha256_file(path) for path in inputs.iterdir()}
            out = root / "准备 文件"
            result = preparation.main([str(inputs), "--out", str(out), "--monochrome", "--bundle",
                                       "--external-masters", "--linear", "--target", "M51"])
            self.assertEqual(result, 0)
            result = compose_channels.main([str(out), "--out", str(root / "halrgb"), "--mode", "halrgb",
                                            "--ha-weight", "0.25", "--ha-scale", "1.0"])
            self.assertEqual(result, 0)
            report = json.loads((root / "halrgb/composition_report.json").read_text())
            self.assertEqual(report["schema_version"], "1.4")
            self.assertEqual(report["input_origin"], "external_masters")
            self.assertEqual(report["output_state"], "nonlinear")
            self.assertEqual(report["preview"]["method"], "direct")
            self.assertEqual(len(report["sources"]), 5)
            for source in report["sources"]:
                self.assertIsNone(source["exposure"]["total_seconds"])
            for path, digest in before.items():
                self.assertEqual(preparation.sha256_file(path), digest)


if __name__ == "__main__":
    REAL = "--real" in sys.argv
    if REAL:
        sys.argv.remove("--real")
        RealExternalMasterTests.__unittest_skip__ = False
    unittest.main()
