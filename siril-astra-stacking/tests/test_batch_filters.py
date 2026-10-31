"""Batch checkpoints and calibration contracts; --real exercises native Siril."""
from __future__ import annotations

import sys
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / 'scripts', _SKILL / 'tests'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


import argparse
import contextlib
import json
import random
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

import batch_filters as batch
from device_signatures import CalibrationMissing, DeviceNotIdentified, identify_device, resolve_calibration_plan
from fits_probe import build_card, data_offset, pad_to_block, read_fits_header
from test_prepare_composition import write_master

REAL = False


def update_header(path, changes):
    blob = path.read_bytes()
    offset = data_offset(str(path))
    cards, remaining = [], dict(changes)
    def card(key, value):
        text = ("T" if value else "F") if isinstance(value, bool) else (
            "'" + value.replace("'", "''") + "'" if isinstance(value, str) else str(value))
        return build_card(key, text).encode("ascii")
    for start in range(0, offset, 80):
        original = blob[start:start + 80]
        key = original[:8].decode("ascii").strip()
        if key == "END":
            break
        if key in remaining:
            value = remaining.pop(key)
            if value is not None:
                cards.append(card(key, value))
        else:
            cards.append(original)
    cards.extend(card(key, value) for key, value in remaining.items() if value is not None)
    cards.append(build_card("END", "").encode("ascii"))
    path.write_bytes(pad_to_block(b"".join(cards)) + blob[offset:])


def make_inputs(root, labels=("R", "B"), frames=2, real=False):
    inputs, masters = root / "lights", root / "masters"
    inputs.mkdir()
    masters.mkdir()
    settings = {"INSTRUME": "ATR585M", "OBJECT": "test field", "GAIN": 100, "OFFSET": 20,
                "CCD-TEMP": -10.0, "XBINNING": 1, "YBINNING": 1, "BAYERPAT": None,
                "STACKCNT": None, "LIVETIME": None, "IMAGETYP": "Light"}
    size = {"width": 200 if real else 16, "height": 150 if real else 12}
    for label in labels:
        for index in range(frames):
            path = inputs / (label + "_%02d.FITS" % index)
            write_master(path, label, value=None if real else 0.1, shift=index, **size)
            update_header(path, settings)
            if real:
                # A zero-MAD background makes native addscale normalization singular.
                blob = path.read_bytes()
                offset = data_offset(str(path))
                count = size["width"] * size["height"]
                generator = random.Random(label + str(index))
                pixels = [value + generator.uniform(-0.0001, 0.0001) for (value,) in
                          struct.iter_unpack(">f", blob[offset:offset + count * 4])]
                path.write_bytes(blob[:offset] + struct.pack(">%df" % count, *pixels) + blob[offset + count * 4:])
        path = masters / ("master_flat_" + label + ".fit")
        write_master(path, label, value=1.0, **size)
        update_header(path, dict(settings, IMAGETYP="Flat", EXPTIME=1.0))
    path = masters / "master_dark.fit"
    write_master(path, "dark", value=0.0, **size)
    update_header(path, dict(settings, IMAGETYP="Dark", FILTER=None))
    return inputs, masters


class BatchFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.input, self.masters = make_inputs(self.root)
        self.args = argparse.Namespace(input=str(self.input), out=str(self.root / "output"),
                                       monochrome=True, calibration_dir=str(self.masters),
                                       device_profile=None, siril=None, timeout=60,
                                       allow_missing_dark=False, resume=False)
        self.out = Path(self.args.out)
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def read_report(self):
        return json.loads((self.out / "batch_report.json").read_text())

    def fake_child(self, command, log):
        self.calls.append(command)
        log.write_text("test child execution\n")
        destination = Path(command[command.index("--out") + 1])
        if Path(command[2]).name == "astra_stack.py":
            label = command[command.index("--target") + 1]
            input_dir = Path(command[3])
            sources = sorted(input_dir.glob("*.fit"))
            header = read_fits_header(str(sources[0]))
            destination.mkdir(parents=True)
            master = destination / (label + "_stacked_32bit.fits")
            preview = destination / (label + ".png")
            report_path = destination / (label + "_report.json")
            write_master(master, label, width=header["NAXIS1"], height=header["NAXIS2"],
                         value=0.1, count=len(sources))
            update_header(master, {"OBJECT": header["OBJECT"], "BAYERPAT": None,
                                   "EXPTIME": header["EXPTIME"], "LIVETIME": len(sources) * header["EXPTIME"]})
            preview.write_bytes(b"test preview")
            calibration = Path(command[command.index("--calibration-dir") + 1])
            report = {
                "status": "ok", "completed_stages": ["commit"],
                "artifacts": {"master": str(master), "preview": str(preview), "report": str(report_path)},
                "stacking": {"frames_stacked": len(sources)},
                "calibration": {kind: str(calibration / ("master_" + kind + ".fit"))
                                if (calibration / ("master_" + kind + ".fit")).is_file() else None
                                for kind in batch.CALIBRATION_KINDS},
            }
            report_path.write_text(json.dumps(report))
        else:
            (destination / "diagnostics").mkdir(parents=True)
            layers = []
            for directory in sorted(Path(command[3]).iterdir()):
                label = directory.name
                source = directory / (label + "_stacked_32bit.fits")
                shutil.copy2(source, destination / (label + ".fit"))
                header = read_fits_header(str(source))
                layers.append({"filter": label, "header": header, "format_ready": True,
                               "source_sha256": batch.sha256_file(source)})
            manifest = {"schema_version": "1.0", "status": "ok", "monochrome_confirmed": True,
                        "layers": layers, "output_size": [header["NAXIS1"], header["NAXIS2"]]}
            (destination / "diagnostics/composition_manifest.json").write_text(json.dumps(manifest))
        return 0

    def run_batch(self, runner=None):
        with patch.object(batch, "find_siril", return_value="/test/siril"), \
             patch.object(batch, "siril_version", return_value="Siril 1.4.4"), \
             patch.object(batch, "run_child", side_effect=runner or self.fake_child):
            return batch.batch(self.args)

    def test_full_batch_commands_and_idempotent_resume(self):
        before = {path: batch.sha256_file(path) for path in self.root.rglob("*") if path.is_file()}
        # Nested FITS are deliberately outside this flat input contract.
        write_master(self.input / "nested/ignored.fit", "S", width=2, height=2)
        report = self.run_batch()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(list(report["buckets"]), ["B", "R"])
        self.assertEqual(len(self.calls), 3)
        for command in self.calls[:2]:
            self.assertIn("--timeout", command)
            self.assertEqual(command[command.index("--timeout") + 1], "60")
            self.assertNotIn("--resume", command)
            self.assertNotIn("--force-chain", command)
        self.assertIn("--bundle", self.calls[-1])
        for path, digest in before.items():
            self.assertEqual(batch.sha256_file(path), digest)
        saved = (self.out / "batch_report.json").read_bytes()
        self.args.resume = True
        self.run_batch()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual((self.out / "batch_report.json").read_bytes(), saved)

    def test_fail_fast_then_resume_successful_bucket(self):
        def fail_second(command, log):
            if command[command.index("--target") + 1] == "R":
                log.write_text("injected stack failure")
                destination = Path(command[command.index("--out") + 1])
                destination.mkdir()
                (destination / "partial").write_text("kept")
                return 5
            return self.fake_child(command, log)
        with self.assertRaisesRegex(batch.FitsError, "R failed"):
            self.run_batch(fail_second)
        report = self.read_report()
        self.assertEqual(report["buckets"]["B"]["status"], "ok")
        self.assertEqual(report["buckets"]["R"]["exit_code"], 5)
        self.assertFalse((self.out / "composition_inputs").exists())
        saved = (self.out / "stacks/B/B_stacked_32bit.fits").read_bytes()
        self.calls.clear()
        self.args.resume = True
        report = self.run_batch()
        self.assertTrue(report["buckets"]["B"]["reused"])
        self.assertEqual(len(self.calls), 2)
        self.assertEqual((self.out / "stacks/B/B_stacked_32bit.fits").read_bytes(), saved)
        self.assertTrue(list((self.out / "diagnostics/attempts").glob("R-*/R/partial")))

    def test_post_commit_cleanup_failure_continues_batch(self):
        import astra_stack as entry

        def cleanup_refused(command, log):
            self.fake_child(command, log)
            if Path(command[2]).name != "astra_stack.py":
                return 0
            label = command[command.index("--target") + 1]
            paths = batch.stack_paths(self.out.resolve(), label)
            report = json.loads(paths["report"].read_text())
            report.update(notes=[], timings={}, failed_stage=None, completed_stages=["stack"])
            work = paths["master"].parent / (label + ".work")
            work.mkdir()
            for path in paths.values():
                path.rename(work / path.name)
            with log.open("a") as stream, contextlib.redirect_stderr(stream), \
                    patch.object(entry.shutil, "rmtree", side_effect=RuntimeError("delete guard refusal")):
                return entry._commit_and_publish(
                    report, argparse.Namespace(keep_work=False), entry.time.time(), str(work),
                    str(paths["master"].parent), label,
                    str(work / paths["master"].name), str(work / paths["preview"].name))

        report = self.run_batch(cleanup_refused)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["preparation"]["status"], "ok")
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(report, self.read_report())
        for label, state in report["buckets"].items():
            self.assertEqual(state["status"], "ok")
            self.assertEqual(state["exit_code"], 0)
            for artifact in state["artifacts"].values():
                self.assertEqual(batch.sha256_file(self.out / artifact["path"]), artifact["sha256"])
            paths = batch.stack_paths(self.out, label)
            saved = json.loads(paths["report"].read_text())
            self.assertIn("delete guard refusal", saved["notes"][-1])
            self.assertTrue(Path(saved["work_dir"]).is_dir())
            self.assertIn("scratch cleanup failed", (self.out / "diagnostics" / (label + ".log")).read_text())
        self.args.resume = True
        self.run_batch(cleanup_refused)
        self.assertEqual(len(self.calls), 3)

    def test_prepare_failure_resume_and_uncheckpointed_bundle(self):
        def fail_prepare(command, log):
            result = self.fake_child(command, log)
            return 5 if "--bundle" in command else result
        with self.assertRaisesRegex(batch.FitsError, "preparation failed"):
            self.run_batch(fail_prepare)
        self.assertEqual(self.read_report()["preparation"]["status"], "failed")
        self.args.resume = True
        self.calls.clear()
        self.run_batch()
        self.assertEqual(len(self.calls), 1)
        self.assertIn("--bundle", self.calls[0])
        self.assertTrue(list((self.out / "diagnostics/attempts").glob("prepare-*/composition_inputs")))

    def test_interruption_before_success_checkpoint_retries_bucket(self):
        original_write = batch.write_report
        def interrupt(path, report):
            if report["buckets"]["B"]["status"] == "ok":
                raise OSError("checkpoint interrupted")
            return original_write(path, report)
        with patch.object(batch, "write_report", side_effect=interrupt):
            with self.assertRaisesRegex(OSError, "interrupted"):
                self.run_batch()
        self.assertEqual(self.read_report()["buckets"]["B"]["status"], "running")
        self.args.resume = True
        self.calls.clear()
        self.run_batch()
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(list((self.out / "diagnostics/attempts").glob("B-*/B")))

    def test_resume_refuses_source_calibration_parameter_and_artifact_changes(self):
        self.run_batch()
        self.args.resume = True
        paths = [self.input / "R_00.FITS", self.masters / "master_dark.fit",
                 self.out / "stacks/B/B.png", self.out / "composition_inputs/R.fit"]
        saved_report = (self.out / "batch_report.json").read_bytes()
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.assertRaises((batch.FitsError, OSError)):
                self.run_batch()
            self.assertEqual((self.out / "batch_report.json").read_bytes(), saved_report)
            path.write_bytes(original)
        self.args.timeout += 1
        with self.assertRaisesRegex(batch.FitsError, "parameters changed"):
            self.run_batch()
        self.assertEqual(len(self.calls), 3)

    def test_profile_changes_are_part_of_fingerprint(self):
        profile = self.root / "profile.json"
        profile.write_text('{"touptek_atr585m_mono": {"min_pairs": 4}}')
        self.args.device_profile = str(profile)
        self.run_batch()
        self.assertIn("--device-profile", self.calls[0])
        profile.write_text('{"touptek_atr585m_mono": {"min_pairs": 5}}')
        self.args.resume = True
        with self.assertRaisesRegex(batch.FitsError, "parameters changed"):
            self.run_batch()

    def test_input_change_during_run_stops_before_preparation(self):
        def mutate(command, log):
            result = self.fake_child(command, log)
            path = self.input / "R_00.FITS"
            path.write_bytes(path.read_bytes() + b"changed")
            return result
        with self.assertRaisesRegex(batch.FitsError, "changed"):
            self.run_batch(mutate)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.out / "composition_inputs").exists())

    def test_selected_master_changed_during_preflight_is_rejected(self):
        original = batch.resolve_calibration_plan
        calls = 0
        def change_master(*args, **kwargs):
            nonlocal calls
            plan = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                path = self.masters / "master_dark.fit"
                path.write_bytes(path.read_bytes() + b"changed")
            return plan
        with patch.object(batch, "resolve_calibration_plan", side_effect=change_master):
            with self.assertRaisesRegex(batch.FitsError, "changed"):
                self.run_batch()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.out.exists())

    def test_change_during_preparation_quarantines_bundle(self):
        def mutate(command, log):
            result = self.fake_child(command, log)
            if "--bundle" in command:
                path = self.input / "R_00.FITS"
                path.write_bytes(path.read_bytes() + b"changed")
            return result
        with self.assertRaisesRegex(batch.FitsError, "changed"):
            self.run_batch(mutate)
        self.assertFalse((self.out / "composition_inputs").exists())
        self.assertEqual(self.read_report()["preparation"]["status"], "failed")

    def test_preflight_rejects_bad_headers_and_mixed_settings(self):
        path = self.input / "R_01.FITS"
        original = path.read_bytes()
        for changes in ({"OBJECT": "other target"}, {"INSTRUME": "unknown camera"},
                        {"FILTER": None}, {"FILTER": "../R"}, {"EXPTIME": 0},
                        {"EXPTIME": float("nan")}, {"EXPTIME": 301}, {"GAIN": 101},
                        {"GAIN": None}, {"OFFSET": None}, {"XBINNING": 2},
                        {"CCD-TEMP": None}, {"NAXIS1": 17}, {"BITPIX": 16}):
            with self.subTest(changes=changes):
                update_header(path, changes)
                with self.assertRaises((batch.FitsError, CalibrationMissing, DeviceNotIdentified)):
                    self.run_batch()
                self.assertEqual(self.calls, [])
                self.assertFalse(self.out.exists())
                path.write_bytes(original)

    def test_alias_conflicts_counts_and_duplicate_sources(self):
        missing = self.input / "R_01.FITS"
        original = missing.read_bytes()
        missing.unlink()
        with self.assertRaisesRegex(batch.FitsError, "at least two"):
            self.run_batch()
        missing.write_bytes(original)
        duplicate = self.input / "duplicate.fit"
        duplicate.symlink_to(self.input / "R_00.FITS")
        with self.assertRaisesRegex(batch.FitsError, "more than once"):
            self.run_batch()
        duplicate.unlink()
        extra = []
        for label in ("C1", "C2", "C3", "C4", "C5", "C6", "C7"):
            for index in range(2):
                path = self.input / (label + str(index) + ".fit")
                path.write_bytes(original)
                update_header(path, {"FILTER": label})
                extra.append(path)
        with self.assertRaisesRegex(batch.FitsError, "2 to 8"):
            self.run_batch()
        for path in extra:
            path.unlink()
        for index in range(2):
            path = self.input / ("R_%02d.FITS" % index)
            update_header(path, {"FILTER": "H" if index == 0 else "HALPHA"})
        with self.assertRaisesRegex(batch.FitsError, "aliases"):
            self.run_batch()
        for path in self.input.glob("R*"):
            update_header(path, {"FILTER": "r "})
        self.assertEqual(list(self.run_batch()["buckets"]), ["B", "R"])

    def test_color_profile_is_refused_despite_monochrome_confirmation(self):
        profile = self.root / "color-profile.json"
        profile.write_text('{"touptek_atr585m_mono": {"mono_sensor_capable": false}}')
        self.args.device_profile = str(profile)
        with self.assertRaisesRegex(batch.FitsError, "monochrome sensor"):
            self.run_batch()
        self.assertFalse(self.out.exists())

    def test_missing_dark_override_and_strict_flat_selection(self):
        (self.masters / "master_dark.fit").unlink()
        with self.assertRaises(CalibrationMissing):
            self.run_batch()
        self.args.allow_missing_dark = True
        self.run_batch()
        self.assertIn("--allow-missing-dark", self.calls[0])

    def test_strict_matching_geometry_settings_ties_and_unlabelled_flats(self):
        header = read_fits_header(str(self.input / "R_00.FITS"))
        device = identify_device(header)
        def resolve(strict=True):
            return resolve_calibration_plan(header, device, str(self.masters), strict=strict)
        self.assertTrue(resolve()["dark"])
        duplicate = self.masters / "duplicate_dark.fit"
        shutil.copy2(self.masters / "master_dark.fit", duplicate)
        with self.assertRaisesRegex(CalibrationMissing, "ambiguous"):
            resolve()
        self.assertTrue(resolve(False)["dark"])
        for changes in ({"OFFSET": 21}, {"OFFSET": None}, {"XBINNING": 2},
                        {"NAXIS1": 20}, {"GAIN": float("nan")}):
            shutil.copy2(self.masters / "master_dark.fit", duplicate)
            update_header(duplicate, changes)
            self.assertEqual(resolve()["dark"], str(self.masters / "master_dark.fit"))
        duplicate.unlink()
        flat = self.masters / "master_flat_R.fit"
        update_header(flat, {"FILTER": None})
        with self.assertRaisesRegex(CalibrationMissing, "missing FILTER"):
            resolve()
        self.assertEqual(resolve(False)["flat"], str(flat))
        flat.unlink()
        self.assertIsNone(resolve()["flat"])

    def test_temperature_drift_must_keep_one_calibration_plan(self):
        path = self.input / "R_01.FITS"
        update_header(path, {"CCD-TEMP": -9.5})
        self.run_batch()
        self.args.out = str(self.root / "other")
        alternate = self.masters / "warm_dark.fit"
        shutil.copy2(self.masters / "master_dark.fit", alternate)
        update_header(alternate, {"CCD-TEMP": -9.5})
        with self.assertRaisesRegex(batch.FitsError, "mixed capture"):
            self.run_batch()

    def test_failed_launch_and_invalid_child_artifacts_never_commit(self):
        def bad_output(command, log):
            result = self.fake_child(command, log)
            label = command[command.index("--target") + 1]
            batch.stack_paths(self.out, label)["preview"].unlink()
            return result
        with self.assertRaisesRegex(batch.FitsError, "missing or empty"):
            self.run_batch(bad_output)
        self.assertEqual(self.read_report()["buckets"]["B"]["status"], "failed")
        self.args.resume = True
        def failed_launch(command, log):
            raise OSError("launch failed")
        with self.assertRaisesRegex(OSError, "launch failed"):
            self.run_batch(failed_launch)
        self.assertEqual(self.read_report()["buckets"]["B"]["status"], "failed")
        self.assertFalse((self.out / "composition_inputs").exists())

    def test_existing_foreign_outputs_and_invalid_cli(self):
        self.out.mkdir()
        marker = self.out / "keep"
        marker.write_text("untouched")
        with self.assertRaisesRegex(batch.FitsError, "already exists"):
            self.run_batch()
        self.args.resume = True
        with self.assertRaises(OSError):
            self.run_batch()
        self.assertEqual(marker.read_text(), "untouched")
        for options in ([], ["--monochrome", "--timeout", "0"]):
            with self.assertRaises(SystemExit) as result:
                batch.main([str(self.input), "--out", str(self.out)] + options)
            self.assertEqual(result.exception.code, 2)


@unittest.skipUnless(REAL, "pass --real for native Siril fixtures")
class RealBatchFilterTests(unittest.TestCase):
    def test_native_batch_retry_resume_and_rgb_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inputs, masters = make_inputs(root, labels=("R", "G", "B"), frames=4, real=True)
            siril = batch.find_siril(None)
            self.assertTrue(siril, "Siril 1.4.x is required")
            args = argparse.Namespace(input=str(inputs), out=str(root / "batch"), monochrome=True,
                                       calibration_dir=str(masters), device_profile=None,
                                       siril=siril, timeout=120, allow_missing_dark=False, resume=False)
            before = {path: batch.sha256_file(path) for path in root.rglob("*") if path.is_file()}
            original_runner = batch.run_child
            def fail_second(command, log):
                if "--target" in command and command[command.index("--target") + 1] == "G":
                    log.write_text("injected failure after first real stack")
                    return 5
                return original_runner(command, log)
            with patch.object(batch, "run_child", side_effect=fail_second):
                with self.assertRaisesRegex(batch.FitsError, "G failed"):
                    batch.batch(args)
            out = Path(args.out)
            first_master = out / "stacks/B/B_stacked_32bit.fits"
            first_bytes, first_mtime = first_master.read_bytes(), first_master.stat().st_mtime_ns
            args.resume = True
            report = batch.batch(args)
            self.assertEqual(report["status"], "ok")
            self.assertTrue(report["buckets"]["B"]["reused"])
            self.assertEqual(first_master.read_bytes(), first_bytes)
            self.assertEqual(first_master.stat().st_mtime_ns, first_mtime)
            for label in ("B", "G", "R"):
                header = read_fits_header(str(out / "composition_inputs" / (label + ".fit")))
                self.assertEqual(header["BITPIX"], -32)
                self.assertEqual(header["NAXIS"], 2)
                self.assertNotIn("BAYERPAT", header)
                self.assertEqual(header["LIVETIME"], 300 * header["STACKCNT"])
            command = [sys.executable, "-B", str(batch.SCRIPTS / "compose_channels.py"),
                       str(out / "composition_inputs"), "--mode", "rgb", "--out", str(root / "rgb"),
                       "--siril", siril]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = (out / "batch_report.json").read_bytes()
            with patch.object(batch, "run_child", side_effect=AssertionError("completed batch ran a child")):
                batch.batch(args)
            command = [sys.executable, "-B", str(batch.SCRIPTS / "batch_filters.py"), str(inputs),
                       "--out", str(out), "--monochrome", "--calibration-dir", str(masters),
                       "--siril", siril, "--timeout", "120", "--resume"]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((out / "batch_report.json").read_bytes(), saved)
            for path, digest in before.items():
                self.assertEqual(batch.sha256_file(path), digest)


if __name__ == "__main__":
    REAL = "--real" in sys.argv
    if REAL:
        sys.argv.remove("--real")
        RealBatchFilterTests.__unittest_skip__ = False
    unittest.main()
