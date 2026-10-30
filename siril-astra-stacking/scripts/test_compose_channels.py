"""Channel composition contracts; --real adds native Siril pixel checks."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
from pathlib import Path
import shutil
import statistics
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import compose_channels as composition
from fits_probe import data_offset, read_fits_header_with_warnings
from test_prepare_composition import make_run, values, write_master

REAL = False


def remove_keys(path, keys):
    blob = path.read_bytes()
    for key in keys:
        blob = blob.replace(key.ljust(8).encode("ascii"), b"COMMENT ", 1)
    path.write_bytes(blob)


def make_prepared(root, specs, bundle=False):
    root.mkdir(parents=True, exist_ok=True)
    layers = []
    for label, value in specs:
        path = root / (label + ".fit")
        write_master(path, label, width=16, height=12, value=value)
        remove_keys(path, ("BAYERPAT",))
        header, _ = read_fits_header_with_warnings(str(path))
        layers.append({
            "filter": label, "header": header, "format_ready": True,
            "output": "/old/prepared/" + path.name,
            "source": "/missing/original.fit", "quality_warnings": [],
        })
    manifest = root / ("diagnostics/composition_manifest.json" if bundle
                       else "composition_manifest.json")
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({
        "schema_version": "1.0", "status": "ok", "monochrome_confirmed": True,
        "layers": layers,
    }), encoding="utf-8")
    return manifest


def write_values(path, pixels):
    offset = data_offset(str(path))
    blob = path.read_bytes()
    payload = struct.pack(">%df" % len(pixels), *pixels)
    path.write_bytes(blob[:offset] + payload + blob[offset + len(payload):])


def make_ha_pattern(root, bundle=False):
    make_prepared(root, [("L", 0.04), ("R", 0.1), ("G", 0.2),
                         ("B", 0.3), ("H_ALPHA", 0.2)], bundle=bundle)
    for label in ("L", "R", "G", "B", "H_ALPHA"):
        pixels = []
        for y in range(12):
            for x in range(16):
                pixels.append({
                    "L": 0.04 + 0.003 * x + 0.002 * y,
                    "R": 0.03 + 0.001 * x + 0.002 * y,
                    "G": 0.05 + 0.002 * x + 0.001 * y,
                    "B": 0.07 + 0.001 * x + 0.004 * y,
                    "H_ALPHA": 0.1 + 0.006 * x + 0.003 * y,
                }[label])
        if label == "R":
            pixels[0], pixels[-1] = -0.125, 1.25
        elif label == "H_ALPHA":
            pixels[0], pixels[-1] = -0.25, 2.0
        write_values(root / (label + ".fit"), pixels)


class ChannelCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input = self.root / "prepared inputs"
        self.manifest = make_prepared(self.input, [("R", 0.125), ("G", 0.25), ("B", 0.5)])
        self.args = argparse.Namespace(input=str(self.input), mode="rgb",
                                       out=str(self.root / "composed output"),
                                       siril=None, timeout=60)

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self):
        return {path: path.read_bytes() for path in self.input.rglob("*") if path.is_file()}

    def fake_run(self, siril, directory, name, builder, timeout):
        builder.write(str(directory / (name + ".sir")))
        (directory / (name + ".log")).write_text("test execution")
        if name == "compose":
            master = directory.parent / ("composed_%s_32bit.fits" % self.args.mode)
            write_master(master, "mixed", width=16, height=12, planes=3, value=0.125)
            remove_keys(master, composition.REMOVED_KEYS)
        elif name == "compose_rgb":
            write_master(directory / "rgb_linear.fit", "mixed", width=16, height=12,
                         planes=3, value=0.125)
        elif name == "ha_blend":
            write_master(directory / "R_HA.fit", "R", width=16, height=12, value=0.05)
            remove_keys(directory / "R_HA.fit", ("BAYERPAT",))
        elif name == "preview":
            (directory.parent / ("composed_%s.png" % self.args.mode)).write_bytes(b"preview")

    def run_mocked(self, runner=None):
        with patch.object(composition, "find_siril", return_value="test-siril"), \
             patch.object(composition, "siril_version", return_value="siril 1.4.4"), \
             patch.object(composition, "run_script", side_effect=runner or self.fake_run):
            return composition.compose(self.args)

    def test_modes_aliases_bundle_and_relocated_paths(self):
        cases = [
            ("rgb", [("r", 0.1), ("G", 0.2), ("B", 0.3), ("L", 0.4)], ("R", "G", "B")),
            ("sho", [("s", 0.1), ("Ha", 0.2), ("O", 0.3)], ("SII", "HA", "OIII")),
            ("hoo", [("H-ALPHA", 0.1), ("OIII", 0.2)], ("HA", "OIII", "OIII")),
            ("lrgb", [("l", 0.03), ("R", 0.04), ("G", 0.08), ("B", 0.12)],
             ("L", "R", "G", "B")),
            ("hargb", [("H", 0.1), ("R", 0.04), ("G", 0.08), ("B", 0.12)],
             ("HA", "R", "G", "B")),
            ("halrgb", [("l", 0.03), ("HA", 0.1), ("R", 0.04), ("G", 0.08), ("B", 0.12)],
             ("L", "HA", "R", "G", "B")),
        ]
        for mode, specs, expected in cases:
            with self.subTest(mode=mode):
                root = self.root / mode
                manifest = make_prepared(root, specs, bundle=mode in ("hoo", "lrgb", "halrgb"))
                found, digest, selected = composition.collect_prepared(root, mode)
                self.assertEqual(found, manifest.resolve())
                self.assertEqual(digest, composition.sha256_file(manifest))
                self.assertEqual(set(selected), set(expected))
                self.assertEqual(len(selected), len(set(expected)))
                self.assertTrue(all(Path(source["path"]).parent == root.resolve() for source in selected.values()))
        for alias in ("H", "HA", "HALPHA", "H-ALPHA", "H_ALPHA"):
            root = self.root / ("alias_" + alias)
            make_prepared(root, [(alias, 0.1), ("OIII", 0.2)])
            self.assertEqual(set(composition.collect_prepared(root, "hoo")[2]), {"HA", "OIII"})

    def test_missing_and_duplicate_channels(self):
        with self.assertRaisesRegex(composition.FitsError, "missing required channel.*HA"):
            composition.collect_prepared(self.input, "hargb")
        with self.assertRaisesRegex(composition.FitsError, "missing required channel.*L"):
            composition.collect_prepared(self.input, "lrgb")
        with self.assertRaisesRegex(composition.FitsError, "missing required"):
            composition.collect_prepared(self.input, "sho")
        make_prepared(self.input, [("H", 0.1), ("HA", 0.2), ("OIII", 0.3)])
        with self.assertRaisesRegex(composition.FitsError, "duplicate aliases"):
            composition.collect_prepared(self.input, "hoo")

    def test_duplicate_luminance(self):
        make_prepared(self.input, [("L", 0.03), ("R", 0.04), ("G", 0.08), ("B", 0.12)])
        manifest = json.loads(self.manifest.read_text())
        manifest["layers"].append(dict(manifest["layers"][0], filter="l"))
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(composition.FitsError, "duplicate aliases.*L"):
            composition.collect_prepared(self.input, "lrgb")

    def test_invalid_manifests(self):
        original = self.manifest.read_text()
        for change in ({"schema_version": "2.0"}, {"status": "failed"},
                       {"monochrome_confirmed": False}, {"layers": None}):
            with self.subTest(change=change):
                self.manifest.write_text(json.dumps(dict(json.loads(original), **change)))
                with self.assertRaises(composition.FitsError):
                    composition.collect_prepared(self.input, "rgb")
        self.manifest.write_text(original)
        duplicate = self.input / "diagnostics/composition_manifest.json"
        duplicate.parent.mkdir()
        duplicate.write_text(original)
        with self.assertRaisesRegex(composition.FitsError, "exactly one"):
            composition.collect_prepared(self.input, "rgb")
        self.manifest.unlink()
        duplicate.unlink()
        with self.assertRaisesRegex(composition.FitsError, "exactly one"):
            composition.collect_prepared(self.input, "rgb")

    def test_bad_channels(self):
        cases = [
            ({"width": 18}, "common shape"), ({"planes": 3}, "Float32 mono"),
            ({"value": math.nan}, "non-finite"),
        ]
        for changes, message in cases:
            with self.subTest(changes=changes):
                write_master(self.input / "B.fit", "B", width=changes.get("width", 16),
                             height=12, planes=changes.get("planes", 1),
                             value=changes.get("value", 0.5))
                remove_keys(self.input / "B.fit", ("BAYERPAT",))
                with self.assertRaisesRegex(composition.FitsError, message):
                    composition.collect_prepared(self.input, "rgb")
        write_master(self.input / "B.fit", "B", width=16, height=12, value=0.5)
        with self.assertRaisesRegex(composition.FitsError, "without BAYERPAT"):
            composition.collect_prepared(self.input, "rgb")
        remove_keys(self.input / "B.fit", ("BAYERPAT",))
        path = self.input / "B.fit"
        path.write_bytes(path.read_bytes().replace(b"'test field'", b"'otherfield'"))
        with self.assertRaisesRegex(composition.FitsError, "OBJECT changed"):
            composition.collect_prepared(self.input, "rgb")

    def test_manifest_channel_contract(self):
        original = json.loads(self.manifest.read_text())
        for change in ({"format_ready": False}, {"filter": "../R"}, {"header": {}},
                       {"quality_warnings": "invalid"}):
            with self.subTest(change=change):
                manifest = json.loads(json.dumps(original))
                manifest["layers"][0].update(change)
                self.manifest.write_text(json.dumps(manifest))
                with self.assertRaises(composition.FitsError):
                    composition.collect_prepared(self.input, "rgb")
        self.manifest.write_text(json.dumps(original))
        (self.input / "R.fit").unlink()
        with self.assertRaises(FileNotFoundError):
            composition.collect_prepared(self.input, "rgb")

    def test_publish_and_report(self):
        before = self.snapshot()
        report = self.run_mocked()
        out = Path(self.args.out)
        saved = json.loads((out / "composition_report.json").read_text())
        self.assertEqual(saved, report)
        self.assertEqual(saved["status"], "ok")
        self.assertIsNone(saved["failed_stage"])
        self.assertEqual(saved["mapping"], {"r": "R", "g": "G", "b": "B"})
        self.assertEqual(saved["output_state"], "linear")
        self.assertEqual(saved["normalization"], "none")
        self.assertEqual(saved["schema_version"], "1.4")
        self.assertIsNone(saved["pre_stretch"])
        self.assertIsNone(saved["ha_blend"])
        self.assertTrue(all(Path(path).exists() for path in saved["artifacts"].values()))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(set(path.suffix for path in (out / "diagnostics").iterdir()), {".sir", ".log"})
        script = (out / "diagnostics/compose.sir").read_text()
        self.assertIn("rgbcomp R.fit G.fit B.fit -out=combined -nosum", script)
        self.assertNotIn("mtf", script)
        self.assertIn("mtf", (out / "diagnostics/preview.sir").read_text())

    def test_lrgb_separate_stretches_and_direct_preview(self):
        make_prepared(self.input, [("L", 0.03), ("R", 0.04), ("G", 0.08), ("B", 0.12)])
        self.args.mode = "lrgb"
        before = self.snapshot()
        report = self.run_mocked()
        self.assertEqual(report["mapping"], {"r": "R", "g": "G", "b": "B", "l": "L"})
        self.assertEqual(len(report["sources"]), 4)
        self.assertEqual(report["output_state"], "nonlinear")
        self.assertEqual(report["pre_stretch"]["l"]["mid"], 0.06)
        self.assertEqual(report["pre_stretch"]["rgb"]["mid"], 0.25)
        self.assertIn("mono median", report["pre_stretch"]["l"]["derived_from"])
        self.assertEqual(report["preview"], {"method": "direct"})
        diagnostics = Path(self.args.out) / "diagnostics"
        self.assertNotIn("mtf", (diagnostics / "preview.sir").read_text())
        self.assertIn("rgbcomp -lum=l_stretched.fit rgb_stretched.fit", (diagnostics / "compose.sir").read_text())
        self.assertIn(composition.LRGB_COMMENT, (diagnostics / "compose.sir").read_text())
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list(diagnostics.glob("*.fit")))

    def test_lrgb_failures_preserve_inputs(self):
        make_prepared(self.input, [("L", 0.03), ("R", 0.04), ("G", 0.08), ("B", 0.12)])
        self.args.mode = "lrgb"
        before = self.snapshot()
        for stage in ("compose_rgb", "pre_stretch", "compose", "preview"):
            with self.subTest(stage=stage):
                def runner(*args):
                    if args[2] == stage:
                        raise RuntimeError("test " + stage)
                    self.fake_run(*args)
                with self.assertRaisesRegex(RuntimeError, "test " + stage):
                    self.run_mocked(runner)
                self.assertFalse(Path(self.args.out).exists())
        failures = [json.loads(path.read_text()) for path in self.root.glob(".compose-*/failure.json")]
        self.assertEqual({report["failed_stage"] for report in failures},
                         {"compose_rgb", "pre_stretch", "compose", "preview"})
        self.assertEqual(before, self.snapshot())

    def test_ha_options_are_validated_before_workdir_creation(self):
        invalid = [(None, None), (None, 1.0), (0.25, None), (-0.1, 1), (1.1, 1),
                   (math.nan, 1), (math.inf, 1), (0.25, 0), (0.25, -1),
                   (0.25, math.nan), (0.25, math.inf)]
        for mode in ("hargb", "halrgb"):
            for weight, scale in invalid:
                with self.subTest(mode=mode, weight=weight, scale=scale):
                    self.args.mode, self.args.ha_weight, self.args.ha_scale = mode, weight, scale
                    with self.assertRaises(ValueError):
                        composition.compose(self.args)
                    self.assertFalse(Path(self.args.out).exists())
                    self.assertEqual(list(self.root.glob(".compose-*")), [])
            for weight in (0, 0.25, 1):
                self.args.ha_weight, self.args.ha_scale = weight, 2.0
                composition.validate_ha_options(self.args)
        for mode in ("rgb", "sho", "hoo", "lrgb"):
            self.args.mode = mode
            for weight, scale in ((0, None), (None, 1.0), (0.25, 1.0)):
                self.args.ha_weight, self.args.ha_scale = weight, scale
                with self.assertRaisesRegex(ValueError, "only to hargb/halrgb"):
                    composition.compose(self.args)

    def test_ha_modes_report_and_scripts(self):
        for mode in ("hargb", "halrgb"):
            with self.subTest(mode=mode):
                root = self.root / mode
                make_ha_pattern(root, bundle=mode == "halrgb")
                self.args.input, self.args.mode = str(root), mode
                self.args.out = str(self.root / (mode + " output"))
                self.args.ha_weight, self.args.ha_scale = 0.25, 2.0
                before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
                report = self.run_mocked()
                self.assertEqual(len(report["sources"]), 5 if mode == "halrgb" else 4)
                self.assertEqual(report["mapping"]["r"], "R_HA")
                self.assertEqual(report["normalization"], "explicit_ha_scale")
                self.assertEqual({key: value for key, value in report["ha_blend"].items() if key != "diagnostics"}, {
                    "formula": "(1 - weight) * R + weight * scale * HA",
                    "weight": 0.25, "scale": 2.0, "continuum_subtraction": False,
                })
                diagnostics = Path(self.args.out) / "diagnostics"
                mixture = (diagnostics / "ha_blend.sir").read_text()
                self.assertIn('$R$ * 0.75 + $HA$ * 0.5', mixture)
                self.assertNotIn("-rescale", mixture)
                stage = "compose_rgb" if mode == "halrgb" else "compose"
                self.assertIn("rgbcomp R_HA.fit G.fit B.fit", (diagnostics / (stage + ".sir")).read_text())
                self.assertEqual(report["output_state"], "nonlinear" if mode == "halrgb" else "linear")
                if mode == "halrgb":
                    self.assertEqual(report["mapping"]["l"], "L")
                    self.assertEqual(report["pre_stretch"]["l"]["mid"], 0.147)
                    self.assertEqual(report["pre_stretch"]["rgb"]["mid"], 0.25)
                    self.assertEqual(report["preview"], {"method": "direct"})
                else:
                    self.assertIsNone(report["pre_stretch"])
                    self.assertEqual(report["preview"]["method"], "mtf")
                self.assertFalse(list(diagnostics.glob("*.fit")))
                self.assertTrue(all(path.read_bytes() == blob for path, blob in before.items()))

    def test_ha_alias_conflicts_and_missing_luminance(self):
        make_prepared(self.input, [("HA", 0.1), ("R", 0.04), ("G", 0.08), ("B", 0.12)])
        with self.assertRaisesRegex(composition.FitsError, "missing required channel.*L"):
            composition.collect_prepared(self.input, "halrgb")
        manifest = json.loads(self.manifest.read_text())
        manifest["layers"].append(dict(manifest["layers"][0], filter="H"))
        self.manifest.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(composition.FitsError, "duplicate aliases.*HA"):
            composition.collect_prepared(self.input, "hargb")

    def test_ha_failures_do_not_publish(self):
        make_ha_pattern(self.input)
        self.args.mode = "hargb"
        self.args.ha_weight, self.args.ha_scale = 0.25, 2.0
        before = self.snapshot()
        for exc in (RuntimeError("blend failed"), subprocess.TimeoutExpired("siril", 60)):
            def fail_run(*args):
                raise exc
            with self.assertRaises(type(exc)):
                self.run_mocked(fail_run)
        def invalid_mixture(*args):
            self.fake_run(*args)
            if args[2] == "ha_blend":
                write_values(args[1] / "R_HA.fit", [math.nan] * 192)
        with self.assertRaisesRegex(composition.FitsError, "non-finite"):
            self.run_mocked(invalid_mixture)
        with patch.object(composition, "publish", side_effect=OSError("publish blocked")):
            with self.assertRaises(OSError):
                self.run_mocked()
        self.assertEqual(before, self.snapshot())
        def change_ha(*args):
            self.fake_run(*args)
            if args[2] == "preview":
                with (self.input / "H_ALPHA.fit").open("ab") as handle:
                    handle.write(b" ")
        with self.assertRaisesRegex(composition.FitsError, "input channel changed"):
            self.run_mocked(change_ha)
        self.assertFalse(Path(self.args.out).exists())
        failures = [json.loads(path.read_text()) for path in self.root.glob(".compose-*/failure.json")]
        self.assertEqual(len(failures), 5)
        self.assertTrue(all(report["status"] == "failed" for report in failures))
        self.assertEqual({report["failed_stage"] for report in failures}, {"ha_blend", "publish", "verify"})

    def test_hoo_report_counts_unique_sources_and_keeps_warnings(self):
        make_prepared(self.input, [("H", 0.125), ("OIII", 0.25)])
        manifest = json.loads(self.manifest.read_text())
        manifest["layers"][0]["quality_warnings"] = ["missing dark"]
        self.manifest.write_text(json.dumps(manifest))
        self.args.mode = "hoo"
        report = self.run_mocked()
        self.assertEqual(len(report["sources"]), 2)
        self.assertEqual(report["mapping"], {"r": "HA", "g": "OIII", "b": "OIII"})
        self.assertIn("missing dark", report["warnings"])
        self.assertEqual([source["header"]["STACKCNT"] for source in report["sources"]], [8, 8])

    def test_existing_destination_is_unchanged(self):
        out = Path(self.args.out)
        out.mkdir()
        marker = out / "previous"
        marker.write_text("unchanged")
        with self.assertRaises(FileExistsError):
            composition.compose(self.args)
        self.assertEqual(marker.read_text(), "unchanged")
        self.assertEqual(list(self.root.glob(".compose-*")), [])

    def test_execution_timeout_and_publish_failures(self):
        before = self.snapshot()
        for exc in (RuntimeError("Siril failed"), subprocess.TimeoutExpired("siril", 60)):
            def fail_run(*args):
                raise exc
            with self.subTest(error=exc), self.assertRaises(type(exc)):
                self.run_mocked(runner=fail_run)
        with patch.object(composition, "publish", side_effect=OSError("publish blocked")):
            with self.assertRaisesRegex(OSError, "publish blocked"):
                self.run_mocked()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(Path(self.args.out).exists())
        failures = [json.loads(path.read_text()) for path in self.root.glob(".compose-*/failure.json")]
        self.assertEqual(len(failures), 3)
        self.assertEqual({report["failed_stage"] for report in failures}, {"compose", "publish"})
        self.assertTrue(all(report["status"] == "failed" for report in failures))

    def test_missing_preview_and_changed_inputs_are_not_published(self):
        for change in ("missing_preview", "channel", "manifest"):
            with self.subTest(change=change):
                def runner(*args):
                    self.fake_run(*args)
                    if args[2] == "preview":
                        if change == "missing_preview":
                            (args[1].parent / "composed_rgb.png").unlink()
                        else:
                            path = self.manifest if change == "manifest" else self.input / "R.fit"
                            with path.open("ab") as handle:
                                handle.write(b" ")
                with self.assertRaises(composition.FitsError):
                    self.run_mocked(runner)
                self.assertFalse(Path(self.args.out).exists())

    def test_invalid_output_metadata_is_not_published(self):
        def runner(*args):
            self.fake_run(*args)
            if args[2] == "compose":
                master = args[1].parent / "composed_rgb_32bit.fits"
                remove_keys(master, ("OBJECT",))
        with self.assertRaisesRegex(composition.FitsError, "invalid RGB composition"):
            self.run_mocked(runner)
        self.assertFalse(Path(self.args.out).exists())

    def test_cli_contract(self):
        with contextlib.redirect_stderr(io.StringIO()):
            for options in ([], ["--mode", "invalid"], ["--mode", "rgb", "--timeout", "0"]):
                with self.assertRaises(SystemExit) as caught:
                    composition.main([str(self.input), "--out", self.args.out] + options)
                self.assertEqual(caught.exception.code, 2)
            with patch.object(composition, "compose", side_effect=RuntimeError("failed")):
                self.assertEqual(composition.main([str(self.input), "--out", self.args.out,
                                                  "--mode", "rgb"]), 1)
            with patch.object(composition, "compose") as compose:
                self.assertEqual(composition.main([str(self.input), "--out", self.args.out,
                                                  "--mode", "lrgb"]), 0)
                self.assertEqual(compose.call_args[0][0].mode, "lrgb")
                for mode in ("hargb", "halrgb"):
                    for options in ([], ["--ha-weight", "0.25"],
                                    ["--ha-weight=nan", "--ha-scale", "1"],
                                    ["--ha-weight", "0.25", "--ha-scale=inf"]):
                        with self.assertRaises(SystemExit) as caught:
                            composition.main([str(self.input), "--out", self.args.out,
                                              "--mode", mode] + options)
                        self.assertEqual(caught.exception.code, 2)
                    self.assertEqual(composition.main([
                        str(self.input), "--out", self.args.out, "--mode", mode,
                        "--ha-weight", "0.25", "--ha-scale", "2",
                    ]), 0)
                    self.assertEqual(compose.call_args[0][0].ha_scale, 2.0)
                with self.assertRaises(SystemExit) as caught:
                    composition.main([str(self.input), "--out", self.args.out, "--mode", "rgb",
                                      "--ha-weight", "0.25", "--ha-scale", "2"])
                self.assertEqual(caught.exception.code, 2)


class RealChannelCompositionTests(unittest.TestCase):
    tearDown = ChannelCompositionTests.tearDown
    snapshot = ChannelCompositionTests.snapshot

    def setUp(self):
        if not REAL:
            self.skipTest("pass --real for native Siril fixtures")
        ChannelCompositionTests.setUp(self)
        self.siril = composition.find_siril()
        if not self.siril:
            self.skipTest("Siril unavailable")
        self.args.siril = self.siril

    def test_native_modes_preserve_pixels_and_metadata(self):
        cases = [
            ("rgb", [("R", -0.125), ("G", 0.25), ("B", 1.25)], [-0.125, 0.25, 1.25]),
            ("sho", [("S", -0.125), ("HA", 0.25), ("O", 1.25)], [-0.125, 0.25, 1.25]),
            ("hoo", [("H_ALPHA", -0.125), ("OIII", 1.25)], [-0.125, 1.25, 1.25]),
        ]
        for mode, specs, expected in cases:
            with self.subTest(mode=mode):
                self.args.mode = mode
                self.args.input = str(self.root / (mode + " input"))
                self.args.out = str(self.root / (mode + " output"))
                make_prepared(Path(self.args.input), specs, bundle=mode == "hoo")
                before = {p: p.read_bytes() for p in Path(self.args.input).rglob("*") if p.is_file()}
                report = composition.compose(self.args)
                master = Path(report["artifacts"]["master"])
                header, _ = read_fits_header_with_warnings(str(master))
                actual = values(master)
                self.assertEqual(header["BITPIX"], -32)
                self.assertEqual((header["NAXIS"], header["NAXIS3"]), (3, 3))
                for plane, value in enumerate(expected):
                    self.assertEqual(set(actual[plane * 192:(plane + 1) * 192]), {value})
                self.assertFalse(set(composition.REMOVED_KEYS) & header.keys())
                self.assertEqual(Path(report["artifacts"]["preview"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(report["output_state"], "linear")
                self.assertEqual(report["schema_version"], "1.4")
                self.assertIsNone(report["pre_stretch"])
                self.assertIsNone(report["ha_blend"])
                self.assertTrue(all(p.read_bytes() == blob for p, blob in before.items()))

    def test_hargb_formula_endpoints_and_overflow(self):
        make_ha_pattern(self.input)
        before = self.snapshot()
        red = values(self.input / "R.fit")
        ha = values(self.input / "H_ALPHA.fit")
        green, blue = values(self.input / "G.fit"), values(self.input / "B.fit")
        baseline = composition.compose(self.args)
        self.args.mode = "hargb"
        for label, weight, scale in (("mixed", 0.25, 2.0), ("zero", 0.0, 1e300), ("ha_only", 1.0, 2.0)):
            with self.subTest(label=label):
                self.args.ha_weight, self.args.ha_scale = weight, scale
                self.args.out = str(self.root / label)
                report = composition.compose(self.args)
                master = Path(report["artifacts"]["master"])
                actual = values(master)
                expected = [(1 - weight) * r + weight * scale * h for r, h in zip(red, ha)]
                self.assertTrue(all(math.isclose(a, b, rel_tol=2e-7, abs_tol=2e-8)
                                    for a, b in zip(actual[:192], expected)))
                self.assertEqual(actual[192:], green + blue)
                self.assertLess(min(actual[:192]), 0)
                self.assertGreater(max(actual[:192]), 1)
                self.assertEqual(report["output_state"], "linear")
                self.assertEqual(len(report["sources"]), 4)
                header, _ = read_fits_header_with_warnings(str(master))
                self.assertFalse(set(composition.REMOVED_KEYS) & header.keys())
                self.assertIn(composition.HA_COMMENT.encode("ascii"), master.read_bytes()[:data_offset(str(master))])
                if weight == 0:
                    self.assertEqual(actual, values(Path(baseline["artifacts"]["master"])))
                    self.assertEqual(Path(report["artifacts"]["preview"]).read_bytes(),
                                     Path(baseline["artifacts"]["preview"]).read_bytes())
        self.args.ha_weight, self.args.ha_scale = 1.0, 1e40
        self.args.out = str(self.root / "overflow")
        with self.assertRaises((composition.FitsError, RuntimeError)):
            composition.compose(self.args)
        self.assertFalse(Path(self.args.out).exists())
        failure = next(self.root.glob(".compose-*/failure.json"))
        self.assertEqual(json.loads(failure.read_text())["failed_stage"], "ha_blend")
        self.assertEqual(before, self.snapshot())

    def test_halrgb_matches_reference_and_recomputes_mtf(self):
        root = self.root / "ha bundle"
        make_ha_pattern(root, bundle=True)
        self.input = self.root / "relocated ha bundle"
        shutil.move(str(root), str(self.input))
        self.args.input = str(self.input)
        self.args.mode = "lrgb"
        before = self.snapshot()
        baseline = composition.compose(self.args)
        self.args.mode = "halrgb"
        self.args.ha_weight, self.args.ha_scale = 0.25, 2.0
        self.args.out = str(self.root / "halrgb")
        report = composition.compose(self.args)
        reference = self.root / "ha reference"
        reference.mkdir()
        for label, filename in (("L", "L"), ("R", "R"), ("G", "G"), ("B", "B"), ("HA", "H_ALPHA")):
            shutil.copy2(self.input / (filename + ".fit"), reference / (label + ".fit"))
        script = reference / "reference.sir"
        script.write_text(
            "requires 1.4.0\nsetext fit\nset32bits\n"
            'pm "$R$ * 0.75 + $HA$ * 0.5" -nosum\nsave R_HA\n'
            "rgbcomp R_HA.fit G.fit B.fit -out=rgb_linear -nosum\n", encoding="utf-8",
        )
        result = subprocess.run([self.siril, "-d", str(reference), "-s", str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-2000:])
        rgb = values(reference / "rgb_linear.fit")
        rgb_mid = round(min(max(min(statistics.median(rgb[i * 192:(i + 1) * 192])
                                    for i in range(3)) * 2, 0.01), 0.4), 6)
        l_mid = round(min(max(statistics.median(values(self.input / "L.fit")) * 2, 0.01), 0.4), 6)
        script.write_text(
            "requires 1.4.0\nsetext fit\nset32bits\n"
            "load L.fit\nmtf 0 %s 1\nsave l_stretched\n"
            "load rgb_linear.fit\nmtf 0 %s 1\nsave rgb_stretched\n"
            "rgbcomp -lum=l_stretched.fit rgb_stretched.fit -out=reference -nosum\n"
            "load reference.fit\nsavepng reference\n" % (l_mid, rgb_mid), encoding="utf-8",
        )
        result = subprocess.run([self.siril, "-d", str(reference), "-s", str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-2000:])
        master = Path(report["artifacts"]["master"])
        self.assertEqual(values(master), values(reference / "reference.fit"))
        self.assertEqual(Path(report["artifacts"]["preview"]).read_bytes(), (reference / "reference.png").read_bytes())
        self.assertEqual(report["pre_stretch"]["rgb"]["mid"], rgb_mid)
        self.assertNotEqual(report["pre_stretch"]["rgb"]["mid"], baseline["pre_stretch"]["rgb"]["mid"])
        self.assertEqual(report["pre_stretch"]["l"]["mid"], l_mid)
        self.assertEqual(report["pre_stretch"]["l"], baseline["pre_stretch"]["l"])
        self.assertEqual(report["output_state"], "nonlinear")
        self.assertEqual(report["preview"], {"method": "direct"})
        self.assertEqual(len(report["sources"]), 5)
        header, _ = read_fits_header_with_warnings(str(master))
        self.assertFalse(set(composition.REMOVED_KEYS) & header.keys())
        raw_header = master.read_bytes()[:data_offset(str(master))]
        self.assertIn(composition.HA_COMMENT.encode("ascii"), raw_header)
        self.assertIn(composition.LRGB_COMMENT.encode("ascii"), raw_header)
        self.assertFalse(list((master.parent / "diagnostics").glob("*.fit")))
        self.args.ha_weight = 0
        self.args.out = str(self.root / "zero halrgb")
        zero = composition.compose(self.args)
        self.assertEqual(zero["pre_stretch"], baseline["pre_stretch"])
        self.assertEqual(values(Path(zero["artifacts"]["master"])), values(Path(baseline["artifacts"]["master"])))
        changed = self.root / "changed ha"
        shutil.copytree(self.input, changed)
        write_values(changed / "H_ALPHA.fit", list(reversed(values(changed / "H_ALPHA.fit"))))
        self.args.input = str(changed)
        self.args.ha_weight = 0.25
        self.args.out = str(self.root / "changed halrgb")
        changed_report = composition.compose(self.args)
        self.assertNotEqual(values(Path(changed_report["artifacts"]["master"])), values(master))
        self.assertEqual((changed / "L.fit").read_bytes(), (self.input / "L.fit").read_bytes())
        self.assertEqual(before, self.snapshot())

    def test_lrgb_matches_native_reference_and_uses_luminance(self):
        make_prepared(self.input, [("L", 0.03), ("R", 0.04), ("G", 0.08), ("B", 0.12)])
        for label in ("L", "R", "G", "B"):
            pattern = []
            for y in range(12):
                for x in range(16):
                    pattern.append({
                        "L": 0.015 + 0.002 * x + 0.006 * y,
                        "R": 0.02 + 0.003 * x + 0.001 * y,
                        "G": 0.04 + 0.001 * x + 0.004 * y,
                        "B": 0.07 + 0.002 * x + 0.002 * y,
                    }[label])
            pattern[0], pattern[-1] = -0.05, 1.2
            path = self.input / (label + ".fit")
            write_values(path, pattern)
        before = self.snapshot()
        self.args.mode = "lrgb"
        report = composition.compose(self.args)
        l_mid = round(min(max(statistics.median(values(self.input / "L.fit")) * 2, 0.01), 0.4), 6)
        rgb_mid = round(min(max(min(statistics.median(values(self.input / (label + ".fit")))
                                    for label in ("R", "G", "B")) * 2, 0.01), 0.4), 6)
        self.assertEqual(report["output_state"], "nonlinear")
        self.assertEqual(report["pre_stretch"]["l"]["mid"], l_mid)
        self.assertEqual(report["pre_stretch"]["rgb"]["mid"], rgb_mid)
        self.assertNotEqual(l_mid, rgb_mid)
        self.assertEqual(report["preview"], {"method": "direct"})
        self.assertEqual(len(report["sources"]), 4)
        reference = self.root / "native reference"
        reference.mkdir()
        for label in ("L", "R", "G", "B"):
            shutil.copy2(self.input / (label + ".fit"), reference / (label + ".fit"))
        script = reference / "reference.sir"
        script.write_text(
            "requires 1.4.0\nsetext fit\nset32bits\n"
            "rgbcomp R.fit G.fit B.fit -out=rgb_linear -nosum\n"
            "load L.fit\nmtf 0 %s 1\nsave l_stretched\n"
            "load rgb_linear.fit\nmtf 0 %s 1\nsave rgb_stretched\n"
            "rgbcomp -lum=l_stretched.fit rgb_stretched.fit -out=reference -nosum\n"
            "load reference.fit\nsavepng reference\n" % (l_mid, rgb_mid), encoding="utf-8",
        )
        result = subprocess.run([self.siril, "-d", str(reference), "-s", str(script)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-2000:])
        master = Path(report["artifacts"]["master"])
        self.assertEqual(values(master), values(reference / "reference.fit"))
        self.assertEqual(Path(report["artifacts"]["preview"]).read_bytes(),
                         (reference / "reference.png").read_bytes())
        header, pixels, _ = composition.inspect_pixels(master)
        self.assertEqual((header["BITPIX"], header["NAXIS"], header["NAXIS3"]), (-32, 3, 3))
        self.assertFalse(set(composition.REMOVED_KEYS) & header.keys())
        self.assertTrue(pixels["all_pixels_finite"])
        self.assertIn(composition.LRGB_COMMENT.encode("ascii"), master.read_bytes()[:data_offset(str(master))])
        self.assertFalse(list((master.parent / "diagnostics").glob("*.fit")))
        self.assertEqual(before, self.snapshot())
        self.args.out = str(self.root / "repeated output")
        repeated = composition.compose(self.args)
        self.assertEqual(repeated["pre_stretch"], report["pre_stretch"])
        self.assertEqual(values(Path(repeated["artifacts"]["master"])), values(master))
        changed = self.root / "changed luminance"
        shutil.copytree(self.input, changed)
        path = changed / "L.fit"
        write_values(path, list(reversed(values(path))))
        self.args.input = str(changed)
        self.args.out = str(self.root / "changed output")
        changed_report = composition.compose(self.args)
        self.assertEqual(changed_report["pre_stretch"], report["pre_stretch"])
        self.assertNotEqual(values(Path(changed_report["artifacts"]["master"])), values(master))
        self.assertEqual(before, self.snapshot())

    def test_actual_preparation_handoff(self):
        import prepare_composition
        for mode, specs in (
            ("hoo", [("H", {}), ("OIII", {"shift": 3})]),
            ("lrgb", [("L", {}), ("R", {"shift": 3}), ("G", {"shift": 5}), ("B", {"shift": 7})]),
            ("hargb", [("R", {}), ("G", {"shift": 3}), ("B", {"shift": 5}), ("HA", {"shift": 7})]),
            ("halrgb", [("L", {}), ("R", {"shift": 3}), ("G", {"shift": 5}),
                        ("B", {"shift": 7}), ("HA", {"shift": 2})]),
        ):
            with self.subTest(mode=mode):
                raw = self.root / (mode + " stack reports")
                make_run(raw, specs)
                prepared = self.root / (mode + " real prepared")
                prepare_composition.prepare(argparse.Namespace(
                    run_dir=str(raw), out=str(prepared), monochrome=True,
                    bundle=True, siril=self.siril, timeout=60,
                ))
                relocated = self.root / (mode + " relocated prepared")
                shutil.move(str(prepared), str(relocated))
                self.args.input = str(relocated)
                self.args.mode = mode
                self.args.ha_weight = 0.25 if mode in ("hargb", "halrgb") else None
                self.args.ha_scale = 2.0 if mode in ("hargb", "halrgb") else None
                self.args.out = str(self.root / (mode + " composed"))
                before = {p: p.read_bytes() for p in relocated.rglob("*") if p.is_file()}
                report = composition.compose(self.args)
                actual = values(Path(report["artifacts"]["master"]))
                if mode == "hoo":
                    ha, oxygen = values(relocated / "H.fit"), values(relocated / "OIII.fit")
                    self.assertEqual(actual, ha + oxygen + oxygen)
                elif mode in ("lrgb", "halrgb"):
                    self.assertEqual(report["output_state"], "nonlinear")
                    self.assertEqual(len(report["sources"]), 5 if mode == "halrgb" else 4)
                    self.assertTrue(all(math.isfinite(value) for value in actual))
                else:
                    self.assertEqual(report["output_state"], "linear")
                    self.assertEqual(len(report["sources"]), 4)
                    self.assertTrue(all(math.isfinite(value) for value in actual))
                self.assertTrue(all(p.read_bytes() == blob for p, blob in before.items()))


if __name__ == "__main__":
    import sys
    REAL = "--real" in sys.argv
    if REAL:
        sys.argv.remove("--real")
    unittest.main()
