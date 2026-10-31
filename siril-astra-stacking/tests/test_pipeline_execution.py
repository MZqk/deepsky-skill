"""Pipeline script generation, execution branching, atomicity and cleanup tests."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / 'scripts', _SKILL / 'tests'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from astra_test_case import AstraTestCase, section, run_main
from device_signatures import identify_device, resolve_calibration_plan
from fits_probe import (
    FitsError,
    build_card as fits_probe_build_card,
    data_offset,
    read_fits_header_with_warnings,
    read_seq_regdata,
)
from param_derive import derive_pipeline
from siril_script_gen import ScriptBuilder, ScriptValidationError
from synth_fits import write_cfa_sequence, write_rgb_sequence

FAILED_WARNINGS: list[str] = []


class TestPipelineExecution(AstraTestCase):
    def test_script_validation(self):
        check = self.check
        section("script generation and validation")
    
        def expect_reject(label, fn):
            try:
                fn()
                check(label, False, "no exception raised")
            except ScriptValidationError:
                check(label, True)
            except Exception as exc:  # noqa: BLE001
                check(label, False, "wrong exception %s" % type(exc).__name__)
    
        expect_reject("rejects 1.5-only command stack_mpp",
                      lambda: ScriptBuilder().add("stack_mpp", ["s"]))
        expect_reject("rejects 1.5-only flag -debayer=",
                      lambda: ScriptBuilder().add("stack", ["s", "rej", "w", "4", "3",
                                                           "-debayer=lmmse"]))
        expect_reject("rejects non-scriptable setmag",
                      lambda: ScriptBuilder().add("setmag", ["20"]))
        expect_reject("rejects apply filter before register",
                      lambda: ScriptBuilder().add("seqapplyreg", ["s", "-framing=current", "-filter-wfwhm=90%"]))
        expect_reject("rejects filter flag before register",
                      lambda: ScriptBuilder().add("stack", ["s", "rej", "w", "4", "3",
                                                           "-filter-wfwhm=0.8k"]))
        # ``med`` is the median stacking method in Siril 1.4 (``median`` is the
        # spelled-out alias of the same method). Note that ``m`` is deliberately NOT
        # tested here: it is the median *rejection type* in a different argument
        # slot, and is legal alongside -maximize.
        expect_reject("rejects median with -maximize",
                      lambda: ScriptBuilder().add("stack", ["s", "med", "-maximize"]))
        expect_reject("rejects spelled-out median with -maximize",
                      lambda: ScriptBuilder().add("stack", ["s", "median", "-maximize"]))
    
        def median_rejection_with_maximize():
            # method=rej, rejection type=m(median), with -maximize: legal, and the
            # guard must not fire.
            builder = ScriptBuilder()
            builder.add("register", ["s", "-2pass"])
            builder.add("seqapplyreg", ["s", "-drizzle", "-framing=max"])
            builder.add("stack", ["s", "rej", "m", "4", "3", "-maximize", "-32b"])
            return builder.render()
    
        try:
            rendered = median_rejection_with_maximize()
            check("median rejection type is allowed with -maximize",
                  "stack s rej m 4 3 -maximize" in rendered, rendered)
        except ScriptValidationError as exc:
            check("median rejection type is allowed with -maximize", False, str(exc))
    
        def maximize_without_framing():
            builder = ScriptBuilder()
            builder.add("register", ["s", "-2pass"])
            builder.add("seqapplyreg", ["s", "-framing=current"])
            builder.add("stack", ["s", "rej", "w", "4", "3", "-maximize"])
            builder.render()
    
        expect_reject("rejects -maximize without framing=max", maximize_without_framing)
    
        def framing_without_drizzle():
            builder = ScriptBuilder()
            builder.add("register", ["s", "-2pass"])
            builder.add("seqapplyreg", ["s", "-framing=max"])
            builder.add("stack", ["s", "rej", "w", "4", "3", "-maximize"])
            builder.render()
    
        expect_reject("rejects framing=max without drizzle", framing_without_drizzle)
    
        builder = ScriptBuilder()
        builder.add("calibrate", ["/data/M 42/sub", "-prefix=pp_ sub"])
        text = builder.render()
        check("spaces quote the whole token",
              '"/data/M 42/sub"' in text, text)
        check("key=value is quoted whole", '"-prefix=pp_ sub"' in text, text)
        check("requires header first", text.splitlines()[1].startswith("requires"),
              text.splitlines()[:3])
        check("script ends with exit", "exit" in text.splitlines()[-2:], text.splitlines()[-3:])
    
        builder = ScriptBuilder()
        builder.add("register", ["s", "-2pass"])
        builder.add("stack", ["s", "med", "-32b"])
        try:
            builder.render()
            check("median without maximize is allowed", True)
        except ScriptValidationError as exc:
            check("median without maximize is allowed", False, str(exc))

    def test_pipeline_branches(self):
        tmp = self.tmp
        check = self.check
        section("chain and parameter derivation")
        cfa_header, _ = read_fits_header_with_warnings(
            write_cfa_sequence(tmp, 64, 48, 1)[0][0]
        )
        device = identify_device(cfa_header)
        calibration = resolve_calibration_plan(cfa_header, device, None)
    
        quiet = {"span": 9.0, "span_x": 9.0, "span_y": 6.0,
                 "rotation_max_deg": 0.01, "scale_dev_max": 0.0001,
                 "mosaic": False, "frame_count": 4}
        pipeline = derive_pipeline(cfa_header, device, calibration, quiet)
        check("quiet CFA picks debayer", pipeline["chain"] == "debayer",
              pipeline["chain"])
        check("debayer chain adds -debayer",
              "-debayer" in pipeline["calibrate"], pipeline["calibrate"])
        check("debayer chain has no -maximize",
              pipeline["stack"]["maximize"] is False)
        check("no -cc=dark without a master dark",
              not any(a.startswith("-cc=dark") for a in pipeline["calibrate"]),
              pipeline["calibrate"])
    
        mosaic = dict(quiet, span=400.0, span_x=400.0, mosaic=True)
        pipeline = derive_pipeline(cfa_header, device, calibration, mosaic)
        check("mosaic picks drizzle", pipeline["chain"] == "drizzle",
              pipeline["chain"])
        check("drizzle keeps CFA (no -debayer)",
              "-debayer" not in pipeline["calibrate"], pipeline["calibrate"])
        check("drizzle uses framing=max",
              "-framing=max" in pipeline["apply"]["args"], pipeline["apply"]["args"])
        check("drizzle adds -maximize", pipeline["stack"]["maximize"] is True)
        check("drizzle has no -flat= (device has none)",
              not any(a.startswith("-flat") for a in pipeline["apply"]["args"]))
        check("drizzle flags the missing flat",
              "drizzle_without_flat_weights"
              in pipeline["chain_info"]["degradations"],
              pipeline["chain_info"]["degradations"])
    
        rotated = dict(quiet, rotation_max_deg=7.0)
        pipeline = derive_pipeline(cfa_header, device, calibration, rotated)
        check("large rotation picks drizzle", pipeline["chain"] == "drizzle",
              pipeline["chain"])
        # The rationale must describe edge interpolation loss, not star trailing.
        # Measured on the real 81-frame M8 capture: a homography represents a 33 deg
        # rotation exactly, and corner stars stayed round under the debayer chain.
        rotation_reason = " ".join(pipeline["chain_info"]["reasons"]).lower()
        check("rotation rationale does not claim trailing",
              "trail" not in rotation_reason and "stretch" not in rotation_reason,
              rotation_reason[:120])
        check("rotation rationale cites edge interpolation",
              "interpolate" in rotation_reason or "edge" in rotation_reason,
              rotation_reason[:120])
    
        # The measured M8 case: 33 deg over 81 frames must route to drizzle.
        m8_like = dict(quiet, span=1264.4, span_x=1264.4, span_y=286.5,
                       rotation_max_deg=33.19, mosaic=False)
        pipeline = derive_pipeline(cfa_header, device, calibration, m8_like)
        check("M8-like 33 deg rotation routes to drizzle",
              pipeline["chain"] == "drizzle", pipeline["chain"])
        check("M8-like case does not claim a mosaic",
              pipeline["chain_info"]["mosaic"] is False,
              pipeline["chain_info"]["mosaic"])
        check("M8-like case enables maximize", pipeline["stack"]["maximize"] is True)
    
        rgb_header, _ = read_fits_header_with_warnings(
            write_rgb_sequence(tmp, 64, 48, 1)[0][0]
        )
        # A stand-in device, not a real signature match: SIGNATURES only covers ZWO
        # Seestar, so identify_device() on a TS-Optics header would refuse. What this
        # case exercises is the RGB chain branch, which keys off class and structure.
        rgb_device = {"id": "generic", "class": "smart",
                      "profile": {"flats_available": False, "dark_embedded": True,
                                  "min_pairs": 4}}
        pipeline = derive_pipeline(rgb_header, rgb_device, calibration, mosaic)
        check("RGB never uses drizzle", pipeline["chain"] == "rgb", pipeline["chain"])
        check("RGB stack has no -maximize",
              pipeline["stack"]["maximize"] is False)

    def test_mono_pipeline_end_to_end(self):
        tmp = self.tmp
        check = self.check
        """A mono sensor must produce a script that never demosaics.
    
        Walks the real derivation and script builder for a DSLR header, so the
        assertion covers the emitted ``.sir`` text rather than just the parameter
        dict. The fake Siril is deliberately not involved: what matters here is
        that the chain Siril would be handed keeps the frames CFA.
        """
        section("mono pipeline emits a never-demosaic script")
        import shlex
    
        import astra_stack as entry
        from synth_fits import write_cfa_sequence
    
        source = os.path.join(tmp, "dslr_src")
        out = os.path.join(tmp, "dslr_out")
        write_cfa_sequence(source, 64, 48, 3, headers="dslr")
        header, _warnings = read_fits_header_with_warnings(
            sorted(Path(source).glob("*.fit"))[0]
        )
    
        device = identify_device(header)
        check("the DSLR fixture identifies as the ASIAIR profile",
              device["id"] == "zwo_asiair_dslr", device["id"])
        check("that profile marks a monochrome sensor",
              device["profile"].get("mono_sensor_capable") is True,
              device["profile"])
    
        calibration = {"dark": "/test/d.fit", "bias": "/test/b.fit",
                       "flat": "/test/f.fit", "cc": {"enabled": False}}
        quiet = {"mosaic": False, "rotation_max_deg": 0.4, "scale_dev_max": 0.001,
                 "span": 12.0, "frame_count": 3}
        pipeline = derive_pipeline(header, device, calibration, quiet)
        check("the real DSLR header takes the mono chain",
              pipeline["chain"] == "mono", pipeline["chain"])
    
        with tempfile.TemporaryDirectory() as work:
            master = os.path.join(work, "dslr_stacked_32bit.fits")
            builder, registered = entry.build_script(
                work, "dslr", pipeline, calibration, master,
                include_calibration=True, registration_available=True,
            )
            text = builder.write(os.path.join(work, "astra_dslr.sir"))
    
        commands = [shlex.split(line) for line in text.splitlines()
                    if line and not line.startswith("#")]
        calibrate = next(c for c in commands if c[0] == "calibrate")
        applyreg = next(c for c in commands if c[0] == "seqapplyreg")
        stack = next(c for c in commands if c[0] == "stack")
    
        check("the emitted calibrate command keeps CFA", "-cfa" in calibrate,
              calibrate)
        check("the emitted calibrate command has no -debayer",
              "-debayer" not in calibrate, calibrate)
        check("the emitted calibrate command has no -equalize_cfa",
              "-equalize_cfa" not in calibrate, calibrate)
        check("the emitted apply uses ordinary interpolation",
              "-framing=current" in applyreg and "-drizzle" not in applyreg,
              applyreg)
        check("the emitted stack does not upscale", "-maximize" not in stack,
              stack)
        check("mono keeps the same applied sequence name as drizzle",
              registered == "ap_c_dslr", registered)
        check("no -rgb_equal reaches a mono script", "-rgb_equal" not in stack,
              stack)
    
        # The same body without the profile flag must still demosaic: the mono
        # decision keys on the profile, not on the CFA structure alone.
        colour_device = {"class": device["class"], "profile": dict(device["profile"])}
        colour_device["profile"].pop("mono_sensor_capable")
        colour_pipeline = derive_pipeline(header, colour_device, calibration, quiet)
        check("the same header without the profile flag stays on debayer",
              colour_pipeline["chain"] == "debayer", colour_pipeline["chain"])

    def test_preprocessing_reuse(self):
        tmp = self.tmp
        check = self.check
        section("probe registration reuse and data-state transitions")
        import math
        import shlex
        from types import SimpleNamespace
        from unittest.mock import patch
        import astra_stack as entry
        from pws_bridge import prepare_pws_input
    
        source = os.path.join(tmp, "reuse-source")
        inputs = write_cfa_sequence(source, 64, 48, 4)
        header, _ = read_fits_header_with_warnings(inputs[0][0])
        device = identify_device(header)
        calibration = resolve_calibration_plan(header, device, None)
        cases = [
            ("auto-drizzle", 2, None, False, 12, False, 1, 1, "ap_c_flow"),
            ("auto-debayer", 2, None, False, 0, False, 2, 2, "ap_d_c_flow"),
            ("auto-debayer-final-rotation", 2, None, False, 0, False, 2, 2, "ap_d_c_flow"),
            ("auto-drizzle-final-quiet", 2, None, True, 12, False, 2, 2, "ap_c_flow"),
            ("forced-drizzle", 2, "drizzle", False, 12, False, 1, 1, "ap_c_flow"),
            ("forced-debayer", 2, "debayer", False, 12, False, 1, 1, "ap_c_flow"),
            ("rgb", 3, None, False, 0, False, 1, 1, "ap_c_flow"),
            ("flat-debayer", 2, None, True, 0, False, 1, 1, "ap_c_flow"),
            ("flat-drizzle", 2, None, True, 12, False, 2, 2, "ap_c_flow"),
            ("dry-run", 2, None, False, 0, True, 1, 1, "ap_c_flow"),
            ("dry-drizzle", 2, "drizzle", False, 0, True, 1, 1, "ap_c_flow"),
            ("invalid-probe", 2, None, False, 0, False, 1, 1, None),
            ("nan-probe", 2, None, False, 0, False, 1, 1, None),
            ("reference-excluded", 2, None, False, 0, False, 2, 2, "ap_d_c_flow"),
            ("reference-excluded-equal", 2, None, False, 0, False, 2, 2, "ap_d_c_flow"),
            ("rgb-equal", 3, None, False, 0, False, 1, 1, "ap_c_flow"),
            ("dry-equal", 2, None, False, 0, True, 1, 1, "ap_c_flow"),
            ("unrelated-failure", 2, "debayer", False, 0, False, 1, 1, "ap_c_flow"),
            ("count-mismatch", 2, "debayer", False, 0, False, 1, 1, "ap_c_flow"),
            ("no-dark", 2, None, False, 12, False, 1, 1, "ap_c_flow"),
            ("cold-no-dark", 2, None, False, 12, False, 1, 1, "ap_c_flow"),
            ("cc-disabled", 2, None, False, 12, False, 1, 1, "ap_c_flow"),
            ("calibrate-failed", 2, None, False, 12, False, 1, 0, None),
            ("calibrate-timeout", 2, None, False, 12, False, 1, 0, None),
            ("calibrate-missing", 2, None, False, 12, False, 1, 0, None),
            ("calibrate-start-failure", 2, None, False, 12, False, 1, 0, None),
            ("recalibrate-failed", 2, None, True, 12, False, 2, 2, "ap_c_flow"),
            ("recalibrate-timeout", 2, None, True, 12, False, 2, 2, "ap_c_flow"),
            ("recalibrate-start-failure", 2, None, True, 12, False, 2, 2, "ap_c_flow"),
            ("recalibrate-missing-master", 2, None, True, 12, False, 2, 2, "ap_c_flow"),
            ("later-timeout", 2, None, False, 12, False, 1, 1, "ap_c_flow"),
            ("pws-gate-refused", 2, None, False, 12, False, 1, 1, None),
            # An interruption during post-commit cleanup must not undo success.
            ("commit-interrupted", 2, None, False, 12, False, 1, 1, "ap_c_flow"),
        ]
        for name, naxis, force, flat, rotation, dry, n_cal, n_reg, applied in cases:
            out = os.path.join(tmp, name)
            commands = []
            plan = dict(calibration, flat="/test/masterflat.fit" if flat else None)
            plan.update(bias="/test/masterbias.fit", dark="/test/masterdark.fit")
            plan["cc"] = dict(calibration["cc"], enabled=name != "cc-disabled",
                              siglo=3 if name == "cold-no-dark" else None)
            if name in ("no-dark", "cold-no-dark"):
                plan["dark"] = None
            input_header = dict(header, NAXIS=naxis)
    
            calls = []
            def execute(script_path):
                calls.append(str(script_path))
                standalone = Path(script_path).name == "astra_calibrate.sir"
                formal = Path(script_path).name == "astra_flow.sir"
                if ((name == "calibrate-timeout" and standalone)
                        or (name in ("recalibrate-timeout", "later-timeout") and formal)):
                    raise subprocess.TimeoutExpired(["siril"], 1)
                if ((name == "calibrate-start-failure" and standalone)
                        or (name == "recalibrate-start-failure" and formal)):
                    raise OSError("fixture could not start Siril")
                text = Path(script_path).read_text()
                parsed = [shlex.split(line) for line in text.splitlines()
                          if line and not line.startswith("#")]
                commands.extend(parsed)
                directory = Path(script_path).parent
                for parts in parsed:
                    if parts[0] == "calibrate":
                        if name == "calibrate-failed" and standalone:
                            return SimpleNamespace(returncode=1, stdout="calibration failed", stderr="")
                        if name == "calibrate-missing" and standalone:
                            continue
                        prefix = next(a.split("=", 1)[1] for a in parts if a.startswith("-prefix="))
                        (directory / (prefix + parts[1] + "0000.fit")).write_bytes(b"fixture")
                    elif parts[0] == "register":
                        if name == "recalibrate-failed" and formal:
                            return SimpleNamespace(returncode=1, stdout="registration failed after calibration", stderr="")
                        ref = 3 if name.startswith("reference-excluded") else 0
                        rows = ["S '%s' 0 4 4 4 %d 6 0 0 0" % (parts[1], ref), "L 1"]
                        rows += ["I %d 1" % i for i in range(4)]
                        if name != "invalid-probe":
                            for i in range(4):
                                final_rotation = rotation
                                if name == "auto-debayer-final-rotation" and parts[1] == "d_c_flow":
                                    final_rotation = 12
                                elif name == "auto-drizzle-final-quiet" and sum(c[0] == "register" for c in commands) > 1:
                                    final_rotation = 0
                                a = math.radians(final_rotation * i)
                                matrix = [math.cos(a), -math.sin(a), 3*i,
                                          math.sin(a), math.cos(a), 2*i, 0, 0, 1]
                                if name == "nan-probe":
                                    matrix[0] = float("nan")
                                rows.append("R0 3 4 0.8 0 0.1 30 H " + " ".join(map(str, matrix)))
                        (directory / (parts[1] + ".seq")).write_text("\n".join(rows)+"\n")
                    elif parts[0] == "seqapplyreg":
                        filtered = any(a.startswith("-filter-") for a in parts)
                        if filtered and (name.startswith("reference-excluded") or name == "unrelated-failure"):
                            error = ('Reference image is not included in the filtered list, aborting\n'
                                     'This is not compatible with framing mode "current", change reference image\n'
                                     if name.startswith("reference-excluded") else "disk write failed\n")
                            return SimpleNamespace(returncode=1, stdout=error, stderr="")
                        selected = list(range(3 if filtered else 4))
                        source = read_seq_regdata(str(directory/(parts[1]+".seq")))
                        applied_name = "ap_" + parts[1]
                        ref = selected.index(source["reference_image"])
                        rows = ["S '%s' 0 %d %d 4 %d 6 0 0 0" % (applied_name,len(selected),len(selected),ref), "L 1"]
                        rows += ["I %d 1" % i for i in selected]
                        # Native apply removes the source rotation from output registration.
                        rows += ["R0 3 4 0.8 0 0.1 30 H 1 0 %s 0 1 %s 0 0 1"
                                 % (source["frames"][i]["h"][2], source["frames"][i]["h"][5])
                                 for i in selected]
                        (directory/(applied_name+".seq")).write_text("\n".join(rows)+"\n")
                        for i in selected:
                            (directory/("%s%04d.fit" % (applied_name,i))).write_bytes(b"frame fixture")
                    elif parts[0] == "stack":
                        if name == "recalibrate-missing-master":
                            continue
                        destination = next(a.split("=",1)[1] for a in parts if a.startswith("-out="))
                        Path(destination).write_bytes(b"master fixture")
                stacked = 2 if name == "count-mismatch" else 3
                return SimpleNamespace(returncode=0, stdout="number of filtered-in images: 4\nRejection stacking complete. %d images have been stacked.\n" % stacked, stderr="")
    
            def fake_calibrate(siril, work, script_path, timeout):
                result = execute(script_path)
                log = os.path.join(work, "astra_calibrate.log")
                Path(log).write_text(result.stdout)
                return result.returncode, result.stdout, log
    
            def fake_preview(siril, work, master, preview, mtf):
                Path(preview).write_bytes(b"preview fixture")
                return True, None
    
            if name == "count-mismatch":
                Path(out).mkdir(parents=True, exist_ok=True)
                Path(out, "flow_stacked_32bit.fits").write_bytes(b"previous good master")
            args = [source, "--out", out, "--target", "flow", "--keep-work"]
            if name == "commit-interrupted":
                # Drop --keep-work so the run reaches the scratch cleanup.
                args = [source, "--out", out, "--target", "flow"]
            if force:
                args += ["--force-chain", force]
            if dry:
                args += ["--dry-run"]
            if name.endswith("-equal"):
                args += ["--rgb-equal"]
            if name == "pws-gate-refused":
                args += ["--stack-engine", "pws"]
            with patch.object(entry, "find_siril", return_value="/test/siril"), \
                    patch.object(entry, "siril_version", return_value="siril 1.4.4"), \
                    patch.object(entry, "read_fits_header_with_warnings", side_effect=lambda path:
                        (dict(input_header, STACKCNT=2 if name == "count-mismatch" else 3), [])
                        if "stacked_32bit" in str(path) else (input_header, [])), \
                    patch.object(entry, "identify_device", return_value=device), \
                    patch.object(entry, "resolve_calibration_plan", return_value=plan), \
                    patch.object(entry, "_run_siril", side_effect=fake_calibrate), \
                    patch.object(entry.subprocess, "run", side_effect=lambda cmd, **kw: execute(cmd[-1])), \
                    patch.object(entry, "_make_preview", side_effect=fake_preview), \
                    patch.object(entry, "_channel_statistics_from_args", side_effect=lambda a, *unused, **kw:
                        {"medians": None, "report": {"medians_before": None, "medians_after": None,
                         "gains_applied": None, "background_equalization": {"requested": a.rgb_equal,
                         "applied": bool(a.rgb_equal and not a.dry_run)}, "notes": []}}):
                if name == "commit-interrupted":
                    # Block only post-commit cleanup; stage_workdir also uses rmtree.
                    real_rmtree = entry.shutil.rmtree
    
                    def guarded_rmtree(path, *unused, **kwargs):
                        if str(path).endswith(".work") and Path(out, "flow_report.json").is_file():
                            raise KeyboardInterrupt("blocked")
                        return real_rmtree(path, *unused, **kwargs)
    
                    with patch.object(entry.shutil, "rmtree", side_effect=guarded_rmtree):
                        rc, stdout_text, stderr_text = run_main(args, entry=entry)
                else:
                    rc, stdout_text, stderr_text = run_main(args, entry=entry)
            report = json.loads(Path(out, "flow_report.json").read_text())
            cc_notes = [note for note in report["notes"] if note.startswith("dark-based cosmetic correction [")]
            completed_cc = [note for note in cc_notes if "completed successfully" in note]
            unconfirmed_cc = [note for note in cc_notes if "completion unconfirmed" in note]
            check(name + " keeps cc.enabled as policy", report["calibration"]["cc"]["enabled"] == plan["cc"]["enabled"])
            if name.startswith("calibrate-"):
                check(name + " fails at calibration", rc == 5 and report["failed_stage"] == "calibrate", report)
                check(name + " does not claim completion", not completed_cc and len(unconfirmed_cc) == 1, cc_notes)
                check(name + " labels the independent script", "astra_calibrate.sir" in unconfirmed_cc[0])
                continue
            if name.startswith("recalibrate-"):
                check(name + " fails in the combined script", rc == 5 and report["failed_stage"] == "stack", report)
                check(name + " retains only the first confirmed completion", len(completed_cc) == 1
                      and "astra_calibrate.sir" in completed_cc[0], cc_notes)
                check(name + " does not infer partial calibration completion", len(unconfirmed_cc) == 1
                      and "astra_flow.sir" in unconfirmed_cc[0], cc_notes)
                continue
            if name in ("later-timeout", "pws-gate-refused"):
                check(name + " preserves earlier calibration on failure", rc == 5
                      and len(completed_cc) == 1 and not unconfirmed_cc, cc_notes)
                continue
            if name == "commit-interrupted":
                check(name + " succeeds when cleanup is interrupted", rc == 0, rc)
                check(name + " warns about the interruption",
                      "warning: scratch cleanup failed" in stderr_text
                      and "KeyboardInterrupt: blocked" in stderr_text, stderr_text)
                published = report["artifacts"]
                check(name + " publishes the complete artifact set",
                      set(published) == {"master", "preview", "report"}
                      and all(os.path.exists(path) for path in published.values()),
                      published)
                check(name + " records commit as the last completed stage",
                      report["completed_stages"][-1] == "commit", report["completed_stages"])
                check(name + " records total runtime",
                      isinstance(report["timings"].get("total_s"), float), report["timings"])
                check(name + " keeps the published report self-consistent",
                      report["status"] == "ok" and report.get("failed_stage") is None,
                      (report["status"], report.get("failed_stage")))
                check(name + " records its own published path even when interrupted",
                      report.get("report_path")
                      == os.path.join(out, "flow_report.json"),
                      report.get("report_path"))
                continue
            if name in ("no-dark", "cold-no-dark"):
                check(name + " reports not applicable and unknown device correction", len(cc_notes) == 1
                      and "no master dark" in cc_notes[0] and "device-side correction is not verified" in cc_notes[0]
                      and not completed_cc and not unconfirmed_cc, cc_notes)
            elif name == "cc-disabled":
                check("disabled cosmetic policy never claims execution", len(cc_notes) == 1
                      and "disabled by calibration policy" in cc_notes[0] and not completed_cc, cc_notes)
            elif dry:
                check(name + " records a generated plan without execution", len(cc_notes) == 1
                      and "planned with -cc=dark; not yet executed" in cc_notes[0]
                      and "astra_flow.sir" in cc_notes[0] and not completed_cc, cc_notes)
            else:
                expected_cc = 2 if name in ("flat-drizzle", "auto-drizzle-final-quiet") else 1
                check(name + " records only actual cosmetic calibration commands",
                      len(completed_cc) == expected_cc and not unconfirmed_cc, cc_notes)
                check(name + " does not claim measured correction effectiveness",
                      all("effectiveness was not measured" in note for note in completed_cc))
            if applied is None:
                check(name + " fails before main execution", rc == 5 and report["failed_stage"] == "probe-reg", report)
                check(name + " produces no master", not Path(out, "flow_stacked_32bit.fits").exists())
                continue
            if name in ("unrelated-failure", "count-mismatch"):
                check(name + " fails safely", rc == 5, report.get("error"))
                check(name + " does not retry", len(calls) == 3, calls)
                destination = Path(out, "flow_stacked_32bit.fits")
                check(name + " does not publish", destination.read_bytes() == b"previous good master"
                      if name == "count-mismatch" else not destination.exists())
                continue
            check(name + " completes", rc == 0, report.get("error"))
            if rc:
                continue
            check(name + " never claims unused gains", report["channel_stats"]["gains_applied"] is None)
            # Self-describing path: written into the snapshot that gets published,
            # so downstream readers need not rebuild <out>/<target>_report.json.
            check(name + " records its own published path",
                  report.get("report_path") == os.path.join(out, "flow_report.json"),
                  report.get("report_path"))
            if dry:
                # A dry run generates no master, so it publishes nothing and never
                # reaches the commit stage. The scratch directory is kept on
                # purpose; the report must point at it or it is undiscoverable.
                check(name + " dry-run publishes nothing",
                      report["artifacts"] == {} and "commit" not in report["completed_stages"],
                      report["completed_stages"])
                check(name + " dry-run points at the retained work directory",
                      report.get("work_dir") == os.path.join(out, "flow.work")
                      and os.path.isdir(report["work_dir"])
                      and any("--dry-run" in note and report["work_dir"] in note
                              for note in report["notes"]),
                      report.get("work_dir"))
            else:
                # The published report must be the final state even if the process
                # is killed during commit: artifacts/completed_stages/timings are
                # filled in before atomic_publish renames the snapshot into place.
                published = report["artifacts"]
                check(name + " publishes the complete artifact set",
                      set(published) == {"master", "preview", "report"}
                      and all(os.path.exists(path) for path in published.values()),
                      published)
                check(name + " artifact paths point at the output directory",
                      all(os.path.dirname(path) == os.path.abspath(out)
                          for path in published.values()),
                      published)
                check(name + " records commit as the last completed stage",
                      report["completed_stages"][-1] == "commit", report["completed_stages"])
                check(name + " records total runtime",
                      isinstance(report["timings"].get("total_s"), float), report["timings"])
            check(name + " truthful equalization state", report["channel_stats"]["background_equalization"]["requested"] == name.endswith("-equal")
                  and report["channel_stats"]["background_equalization"]["applied"] == (name.endswith("-equal") and not dry))
            if not dry:
                check(name + " reports pre-selection registration count", report["stacking"]["frames_registered"] == 4)
                check(name + " reports actual stacked count", report["stacking"]["frames_stacked"] == 3)
                check(name + " reports full quality distribution", report["frame_quality"]["frame_count"] == 4)
                check(name + " preserves transformed geometry",
                      report["registration"]["observed_after_run"]["rotation_max_deg"] == 0
                      and report["registration"]["observed_after_run"]["frame_count"] == (4 if name.startswith("reference-excluded") else 3))
                reviews = [note for note in report["notes"] if note.startswith("automatic chain '")]
                expected_review = name in ("auto-debayer-final-rotation", "auto-drizzle-final-quiet")
                check(name + " reviews source geometry only for automatic differences",
                      bool(reviews) == expected_review, report["notes"])
                check(name + " removes obsolete slower-than-necessary note",
                      "slower than necessary" not in " ".join(report["notes"]), report["notes"])
                if expected_review:
                    expected = "drizzle" if name == "auto-debayer-final-rotation" else "debayer"
                    check(name + " reports the final source recommendation",
                          ("'%s' recommended" % expected) in reviews[0], reviews)
                    check(name + " retains initial probe geometry",
                          report["registration_probe"]["rotation_max_deg"] == (0 if expected == "drizzle" else 36))
            if name.startswith("reference-excluded"):
                check("reference fallback retains both attempts", len(calls) == 4
                      and Path(out, "flow.work", "siril.log").exists()
                      and Path(out, "flow.work", "siril_reference_fallback.log").exists(), calls)
                fallback_commands = [shlex.split(line) for line in report["script_text"].splitlines()
                                     if line and not line.startswith("#")]
                check("fallback only applies and stacks", [c[0] for c in fallback_commands] == ["requires","seqapplyreg","stack","exit"], fallback_commands)
                check("fallback uses actual debayered source", fallback_commands[1][1] == "d_c_flow")
                check("fallback selects only at stack", not any(a.startswith("-filter-") for a in fallback_commands[1])
                      and any(a.startswith("-filter-") for a in fallback_commands[2]))
            if dry:
                check(name + " invokes no Siril", not commands, commands)
                commands = [shlex.split(line) for line in report["script_text"].splitlines()
                            if line and not line.startswith("#")]
            calibrations = [c for c in commands if c[0] == "calibrate"]
            registrations = [c for c in commands if c[0] == "register"]
            check(name + " calibration command count", len(calibrations) == n_cal, calibrations)
            check(name + " registration command count", len(registrations) == n_reg, registrations)
            apply = next(c for c in commands if c[0] == "seqapplyreg")
            prefix = next(a.split("=", 1)[1] for a in apply if a.startswith("-prefix="))
            check(name + " actual applied sequence", prefix + apply[1] == applied, apply)
            stack = next(c for c in commands if c[0] == "stack")
            from param_derive import SMART_FILTERS
            check(name + " filters before transforms", [a for a in apply if a.startswith("-filter-")] == SMART_FILTERS, apply)
            check(name + " stack does not select twice", not any(a.startswith("-filter-") for a in stack), stack)
            check(name + " RGB equalization only when explicit", ("-rgb_equal" in stack) == name.endswith("-equal"), stack)
            if name.startswith("reference-excluded"):
                check(name + " fallback preserves color option", ("-rgb_equal" in fallback_commands[2]) == name.endswith("-equal"))
            if name == "auto-debayer":
                check("debayer-only never reapplies masters or cosmetic correction",
                      calibrations[1] == ["calibrate", "c_flow", "-prefix=d_", "-cfa", "-debayer"], calibrations)
            if name == "flat-debayer":
                check("flat equalisation retained", "-equalize_cfa" in calibrations[0], calibrations)
            if name == "auto-drizzle":
                check("auto probe keeps CFA", "-debayer" not in calibrations[0], calibrations)
    
        builder = ScriptBuilder(registration_available=True)
        builder.add("seqapplyreg", ["cached", "-framing=current"])
        builder.add("stack", ["ap_cached", "rej", "w", "4", "3", "-filter-wfwhm=90%"])
        check("validated cached registration permits filters", "stack ap_cached" in builder.render())
        builder = ScriptBuilder(registration_available=True)
        builder.add("calibrate", ["cached", "-cfa", "-debayer"])
        try:
            builder.add("stack", ["d_cached", "rej", "w", "4", "3", "-filter-wfwhm=90%"])
            check("calibration invalidates cached registration", False)
        except ScriptValidationError:
            check("calibration invalidates cached registration", True)
    
        work = Path(tmp, "actual-pws-sequence")
        work.mkdir()
        cards = [fits_probe_build_card("SIMPLE", "T"), fits_probe_build_card("BITPIX", "-32"),
                 fits_probe_build_card("NAXIS", "2"), fits_probe_build_card("NAXIS1", "2"),
                 fits_probe_build_card("NAXIS2", "2"), fits_probe_build_card("END", "")]
        from fits_probe import pad_to_block
        image = pad_to_block("".join(cards).encode()) + pad_to_block(struct.pack(">4f", .1, .2, .3, .4))
        (work / "ap_d_c_flow0000.fit").write_bytes(image)
        prepared = prepare_pws_input(str(work), "flow", registered_seq="ap_d_c_flow")
        check("PWS accepts the actual debayered sequence", prepared["frame_count"] == 1, prepared)
        (work / "ap_c_flow0000.fit").write_bytes(image)
        default = prepare_pws_input(str(work), "flow")
        check("PWS default sequence remains compatible", default["frame_count"] == 1, default)

    def test_atomic_commit(self):
        tmp = self.tmp
        check = self.check
        section("atomic commit leaves no partial artifacts")
        from astra_stack import atomic_publish
    
        work = os.path.join(tmp, "commit_work")
        out = os.path.join(tmp, "commit_out")
        os.makedirs(work, exist_ok=True)
        os.makedirs(out, exist_ok=True)
    
        good = os.path.join(work, "good.fits")
        with open(good, "w", encoding="ascii") as handle:
            handle.write("complete")
    
        published = atomic_publish(work, out, "target", {"master": good})
        check("artifact published", os.path.exists(published["master"]),
              published)
        check("published under the public name",
              os.path.basename(published["master"]) == "target_stacked_32bit.fits",
              published["master"])
    
        # A run that fails partway must not overwrite the good master.
        check("previous master still intact",
              os.path.exists(os.path.join(out, "target_stacked_32bit.fits")))
    
        try:
            atomic_publish(work, out, "target", {"master": os.path.join(work, "nope")})
            check("missing artifact rejected", False, "no exception")
        except RuntimeError:
            check("missing artifact rejected", True)
    
        # The report predicts its own destinations before publishing, so the two
        # must agree exactly or the report would name files that do not exist.
        from astra_stack import _published_paths
    
        predicted = _published_paths(out, "target", {
            "master": good, "preview": os.path.join(work, "p.png"),
            "report": os.path.join(work, "r.json"),
        })
        check("predicted names match the published ones",
              predicted["master"] == os.path.join(out, "target_stacked_32bit.fits")
              and predicted["preview"] == os.path.join(out, "target.png")
              and predicted["report"] == os.path.join(out, "target_report.json"),
              predicted)

    def test_commit_cleanup(self):
        tmp = self.tmp
        check = self.check
        section("post-commit cleanup cannot turn success into failure")
        from unittest.mock import patch
    
        import astra_stack as entry
    
        cases = [
            ("normal", None), ("keep", None),
            ("oserror", OSError("permission denied")),
            ("guard", RuntimeError("deletion refused by file policy")),
            ("interrupt", KeyboardInterrupt("cleanup interrupted")),
            ("exit", SystemExit("cleanup stopped")),
            ("partial", OSError("partial removal")),
            ("rewrite", RuntimeError("deletion refused by file policy")),
            ("publish-failed", None),
        ]
        for name, error in cases:
            out = Path(tmp, "cleanup_" + name)
            work = out / "target.work"
            work.mkdir(parents=True)
            master, preview = work / "master.fits", work / "preview.png"
            master.write_bytes(b"complete master")
            preview.write_bytes(b"complete preview")
            scratch = work / "scratch.log"
            scratch.write_text("scratch fixture")
            report = {"target": "target", "failed_stage": None,
                      "completed_stages": ["stack", "preview"], "timings": {}, "notes": []}
            published_report = out / "target_report.json"
            real_rmtree, real_write, real_publish = entry.shutil.rmtree, entry.write_report, entry.atomic_publish
    
            def cleanup(path, *args, **kwargs):
                if error is not None:
                    if name == "partial":
                        scratch.unlink()
                    raise error
                return real_rmtree(path, *args, **kwargs)
    
            def write_report(path, payload):
                if name == "rewrite" and str(path) == str(published_report):
                    raise OSError("report rewrite refused")
                return real_write(path, payload)
    
            def publish(*args):
                if name == "publish-failed":
                    raise RuntimeError("publish refused")
                return real_publish(*args)
    
            def pipeline(args):
                return entry._commit_and_publish(report, args, entry.time.time(), str(work),
                                                 str(out), "target", str(master), str(preview))
    
            argv = [str(work), "--out", str(out), "--target", "target", "--quiet"]
            if name == "keep":
                argv.append("--keep-work")
            with patch.object(entry, "run_pipeline", side_effect=pipeline), \
                    patch.object(entry.shutil, "rmtree", side_effect=cleanup) as removal, \
                    patch.object(entry, "write_report", side_effect=write_report), \
                    patch.object(entry, "atomic_publish", side_effect=publish):
                rc, _, stderr = run_main(argv, entry=entry)
            saved = json.loads(published_report.read_text())
            label = "cleanup " + name
            if name == "publish-failed":
                check(label + " remains a commit failure", rc == entry.EXIT_COMMIT_FAILED
                      and saved["failed_stage"] == "commit" and work.is_dir(), (rc, saved))
                check(label + " does not attempt cleanup", removal.call_count == 0)
                continue
            check(label + " exits successfully", rc == entry.EXIT_OK, (rc, stderr))
            check(label + " preserves the complete success report",
                  saved["status"] == "ok" and saved["failed_stage"] is None
                  and saved["completed_stages"][-1] == "commit"
                  and isinstance(saved["timings"]["total_s"], float)
                  and saved["artifacts"]["report"] == str(published_report), saved)
            check(label + " preserves both products",
                  Path(saved["artifacts"]["master"]).read_bytes() == b"complete master"
                  and Path(saved["artifacts"]["preview"]).read_bytes() == b"complete preview")
            check(label + " creates no crash report", not (out / "target_report.internal.json").exists())
            check(label + " never retries cleanup", removal.call_count == (0 if name == "keep" else 1))
            if error is not None:
                note = "scratch cleanup failed at %s: %s: %s" % (work, type(error).__name__, error)
                check(label + " warns even in quiet mode", "warning: " + note in stderr, stderr)
                check(label + " records the residual directory", report["work_dir"] == str(work)
                      and work.is_dir() and note in report["notes"], report)
                if name == "rewrite":
                    check(label + " preserves the published snapshot on rewrite failure",
                          "report rewrite refused" in stderr and saved["notes"] == [], (saved, stderr))
                else:
                    check(label + " persists diagnostics", note in saved["notes"]
                          and saved["work_dir"] == str(work), saved)
                if name == "partial":
                    check(label + " permits partial scratch removal", not scratch.exists())
            else:
                check(label + " honors keep-work", work.exists() == (name == "keep"))

    def test_output_contract(self):
        tmp = self.tmp
        check = self.check
        section("stdout carries the script, stderr narrates the run")
        from unittest.mock import patch
    
        import astra_stack as entry
    
        src = os.path.join(tmp, "contract_src")
        os.makedirs(src, exist_ok=True)
        write_cfa_sequence(src, 64, 48, 3)
    
        # Siril itself is stubbed: this layer only checks the stream contract, and
        # depending on a local install would make it non-portable.
        with patch.object(entry, "find_siril", return_value="/test/siril"), \
                patch.object(entry, "siril_version", return_value="siril 1.4.4"):
            loud_out = os.path.join(tmp, "contract_loud")
            code, stdout_text, stderr_text = run_main(
                [src, "--out", loud_out, "--target", "loud", "--dry-run"])
            quiet_out = os.path.join(tmp, "contract_quiet")
            quiet_code, quiet_stdout, quiet_stderr = run_main(
                [src, "--out", quiet_out, "--target", "quiet", "--dry-run", "--quiet"])
    
        check("dry-run succeeds", code == 0, code)
        check("dry-run writes the script to stdout",
              stdout_text.startswith("#") and "exit" in stdout_text, stdout_text[:120])
        check("dry-run keeps the script off stderr", "exit" not in stderr_text,
              stderr_text[-200:])
        check("dry-run narrates progress by default",
              stderr_text.count("[astra]") >= 3 and "probe" in stderr_text,
              stderr_text[:300])
        check("closing line names the report",
              "report=%s" % os.path.join(loud_out, "loud_report.json") in stderr_text,
              stderr_text[-200:])
    
        check("--quiet run still succeeds", quiet_code == 0, quiet_code)
        check("--quiet still writes the script to stdout",
              "exit" in quiet_stdout and "seqapplyreg" in quiet_stdout,
              quiet_stdout[:120])
        check("--quiet drops the progress lines but keeps the conclusion",
              "probe:" not in quiet_stderr and quiet_stderr.count("[astra]") == 1
              and " OK " in quiet_stderr,
              quiet_stderr)
        check("--quiet conclusion names its own report",
              "report=%s" % os.path.join(quiet_out, "quiet_report.json") in quiet_stderr,
              quiet_stderr[-200:])

    def test_report_atomicity(self):
        tmp = self.tmp
        check = self.check
        section("report writes never leave a truncated file")
        from unittest.mock import patch
    
        import astra_stack as entry
        from astra_stack import write_report
    
        out = os.path.join(tmp, "atomic_report")
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, "atomic_report.json")
        write_report(path, {"status": "ok", "target": "atomic"})
    
        def exploding_dump(payload, handle, **kwargs):
            # Emit a plausible prefix, then fail: this is what a serialisation
            # error or a kill mid-write looks like on disk.
            handle.write('{"status": "ok", "targ')
            raise TypeError("simulated serialisation failure")
    
        with patch.object(entry.json, "dump", side_effect=exploding_dump):
            try:
                write_report(path, {"status": "failed"})
                check("write failure propagates", False, "no exception")
            except TypeError:
                check("write failure propagates", True)
    
        with open(path, "r", encoding="utf-8") as handle:
            survived = json.load(handle)
        check("previous report survives a failed rewrite",
              survived == {"status": "ok", "target": "atomic"}, survived)
        leftovers = [name for name in os.listdir(out) if name.endswith(".tmp")]
        check("failed rewrite leaves no scratch file", not leftovers, leftovers)
    
        # A failure with no previous report must not leave a half-written one.
        fresh = os.path.join(out, "fresh_report.json")
        with patch.object(entry.json, "dump", side_effect=exploding_dump):
            try:
                write_report(fresh, {"status": "failed"})
            except TypeError:
                pass
        check("failed first write creates nothing", not os.path.exists(fresh))
        check("failed first write leaves no scratch file",
              not [name for name in os.listdir(out) if name.endswith(".tmp")],
              os.listdir(out))
    
        # A policy-mediated host (sandbox, file-policy agent) can refuse the rename
        # with an exception that is not an OSError. Such a refusal must degrade to
        # a warning, never propagate and mask what the run was actually doing.
        class PolicyRefusal(Exception):
            """Stand-in for a host agent's own exception type."""
    
        def refuse(source, destination):
            raise PolicyRefusal("rename refused by file policy")
    
        guarded = os.path.join(out, "guarded_report.json")
        del FAILED_WARNINGS[:]
        real_warn = entry._warn
        # Swallow the deliberately provoked warnings: they are asserted through
        # FAILED_WARNINGS, and printing them would only pollute the transcript.
        entry._warn = FAILED_WARNINGS.append
        try:
            with patch.object(entry.os, "replace", side_effect=refuse):
                try:
                    write_report(guarded, {"status": "ok"})
                    check("policy refusal propagates out of write_report", False,
                          "no exception")
                except PolicyRefusal:
                    check("policy refusal propagates out of write_report", True)
    
                # Must degrade to a warning instead of propagating.
                entry._emit_final({"status": "ok"}, out, "guarded")
                check("_emit_final survives a non-OSError refusal", True)
    
                # And the crash path must not blow up either.
                entry._write_internal_report(os.path.join(out, "crash_report.json"),
                                             "t", "/nonexistent",
                                             ValueError("boom"), "Traceback...")
                check("_write_internal_report survives a non-OSError refusal", True)
        finally:
            entry._warn = real_warn
    
        check("non-OSError refusal is reported, not silent",
              any("could not write" in line for line in FAILED_WARNINGS),
              FAILED_WARNINGS)
        check("the warning names the exception type",
              any("PolicyRefusal" in line for line in FAILED_WARNINGS),
              FAILED_WARNINGS)
        check("_report_is_complete tolerates a missing path",
              entry._report_is_complete(os.path.join(out, "does-not-exist.json"))
              is False)

    def test_mixed_filter_refusal(self):
        tmp = self.tmp
        check = self.check
        section("mixed-filter input is refused, not silently stacked")
        from astra_stack import EXIT_UNSUPPORTED_INPUT, main
    
        mixed = os.path.join(tmp, "mixed")
        os.makedirs(mixed, exist_ok=True)
        write_cfa_sequence(mixed, 64, 48, 2)
        # Synthetic frames carry FILTER='IRCUT'; patch one frame to 'R' in place
        # (same card width) to fabricate a mixed directory.
        second = sorted(
            os.path.join(mixed, name) for name in os.listdir(mixed)
        )[1]
        with open(second, "rb") as handle:
            data = handle.read()
        assert b"FILTER  = 'IRCUT   '" in data
        data = data.replace(b"FILTER  = 'IRCUT   '", b"FILTER  = 'R       '", 1)
        with open(second, "wb") as handle:
            handle.write(data)
    
        out = os.path.join(tmp, "mixed_out")
        code, stdout_text, stderr_text = run_main([mixed, "--out", out])
        check("mixed-filter run exits UNSUPPORTED_INPUT",
              code == EXIT_UNSUPPORTED_INPUT, code)
        report_path = os.path.join(out, "mixed_report.json")
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        check("refusal names both filters",
              "IRCUT" in (report.get("error") or "")
              and "R" in (report.get("error") or ""),
              report.get("error"))
        check("refusal happened in probe stage",
              report.get("failed_stage") == "probe", report.get("failed_stage"))
        check("no master published",
              not os.path.exists(os.path.join(out, "mixed_stacked_32bit.fits")))
        # The report has always been written; stderr used to be empty, which left a
        # caller with an exit code and nothing to explain it.
        check("refusal keeps stdout clean", stdout_text == "", stdout_text[:120])
        check("refusal explains itself on stderr",
              "[astra]" in stderr_text and "FAILED" in stderr_text
              and "exit=%d" % EXIT_UNSUPPORTED_INPUT in stderr_text
              and "stage=probe" in stderr_text
              and report_path in stderr_text,
              stderr_text[-300:])
        check("refusal does not claim success", " OK " not in stderr_text, stderr_text)

    def test_failure_isolation(self):
        tmp = self.tmp
        check = self.check
        section("failure keeps the previous good result")
    
        src = os.path.join(tmp, "src")
        out = os.path.join(tmp, "out")
        os.makedirs(out, exist_ok=True)
        marker = os.path.join(out, "src_stacked_32bit.fits")
        with open(marker, "w", encoding="ascii") as handle:
            handle.write("PREVIOUS GOOD RESULT")
    
        # A single frame cannot be stacked, so the run must fail.
        lone = os.path.join(tmp, "lone")
        os.makedirs(lone, exist_ok=True)
        write_cfa_sequence(lone, 64, 48, 1)
        code, _, stderr_text = run_main([lone, "--out", out])
        check("single-frame run fails", code != 0, code)
        with open(marker, "r", encoding="ascii") as handle:
            content = handle.read()
        check("previous result untouched", content == "PREVIOUS GOOD RESULT", content)
        check("failure names the exit code and stage",
              "exit=%d" % code in stderr_text and "stage=probe" in stderr_text,
              stderr_text[-200:])

if __name__ == "__main__":
    unittest.main()
