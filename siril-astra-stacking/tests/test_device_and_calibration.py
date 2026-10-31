"""Device identification, calibration planning, and chain review tests."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / 'scripts', _SKILL / 'tests'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from astra_test_case import AstraTestCase, section
from device_signatures import (
    DeviceNotIdentified,
    identify_device,
    resolve_calibration_plan,
)
from fits_probe import read_fits_header_with_warnings, summarize_transforms
from param_derive import derive_pipeline, decide_chain, classify_input
from synth_fits import write_cfa_sequence

MONO_DEVICE = {
    "id": "test_mono_sensor",
    "class": "traditional",
    "profile": {
        "flats_available": True,
        "dark_embedded": False,
        "min_pairs": 8,
        "mono_sensor_capable": True,
    },
}

RGB_DEVICE = {
    "id": "test_colour_sensor",
    "class": "traditional",
    "profile": {
        "flats_available": True,
        "dark_embedded": False,
        "min_pairs": 8,
    },
}

MONO_HEADER = {
    "NAXIS": 2, "BAYERPAT": "RGGB", "BITPIX": 16,
    "CCD-TEMP": 10.0, "GAIN": 80, "EXPTIME": 300.0,
    "XPIXSZ": 4.77, "YPIXSZ": 4.77,
}

MONO_CALIBRATION = {
    "dark": "/test/d.fit", "bias": "/test/b.fit", "flat": "/test/f.fit",
    "cc": {"enabled": False},
}


class TestDeviceAndCalibration(AstraTestCase):
    def test_device_identification(self):
        tmp = self.tmp
        check = self.check
        section("device identification")
        header, _ = read_fits_header_with_warnings(
            write_cfa_sequence(tmp, 64, 48, 1)[0][0]
        )
        device = identify_device(header)
        check("S30 Pro identified", device["id"] == "zwo_seestar_s30pro", device["id"])
        check("class is smart", device["class"] == "smart", device["class"])
        check("matched by TELESCOP", device["matched_by"] == "TELESCOP",
              device["matched_by"])
        check("no flat expected", device["profile"]["flats_available"] is False)
    
        try:
            identify_device({"TELESCOP": "'Unknown Scope'", "NAXIS": 3})
            check("unknown device refused", False, "no exception")
        except DeviceNotIdentified as exc:
            check("unknown device refused", True)
            check("refusal lists evidence", "TELESCOP" in exc.evidence, exc.evidence)

    def test_calibration_plan(self):
        check = self.check
        section("calibration planning")
        device = {"profile": {"flats_available": False, "dark_embedded": True}}
        header = {"CCD-TEMP": 10.0, "GAIN": 80, "EXPTIME": 10.0}
        plan = resolve_calibration_plan(header, device, None)
        check("smart: dark not applied", plan["dark"] is None)
        check("smart: dark_embedded recorded", plan["dark_embedded"] is True)
        check("smart: flat missing reason recorded",
              plan["flat_missing_reason"] == "smart_scope_no_flat")
        check("smart: a warning is raised", len(plan["warnings"]) >= 2, plan["warnings"])
    
        traditional = {"profile": {"flats_available": True, "dark_embedded": False}}
        try:
            resolve_calibration_plan(header, traditional, None)
            check("traditional without dark refuses", False, "no exception")
        except Exception as exc:  # noqa: BLE001 - CalibrationMissing expected
            check("traditional without dark refuses",
                  type(exc).__name__ == "CalibrationMissing", type(exc).__name__)
    
        # Verify that a relative calibration_dir is resolved to an absolute path,
        # and all picked masters (dark, flat, bias) carry absolute paths.
        # Siril executes inside work_dir (-d work_dir), so relative master paths
        # would be resolved against work_dir and fail with file-not-found.
        from fits_probe import build_card, pad_to_block
        from param_derive import derive_calibration
    
        temp_dir = tempfile.mkdtemp(prefix="astra_cal_rel_")
        try:
            cards = [
                build_card("SIMPLE", "T"),
                build_card("BITPIX", "16"),
                build_card("NAXIS", "2"),
                build_card("NAXIS1", "10"),
                build_card("NAXIS2", "10"),
                build_card("CCD-TEMP", "10.0"),
                build_card("GAIN", "80"),
                build_card("EXPTIME", "10.0"),
                build_card("END", ""),
            ]
            hdr_bytes = pad_to_block("".join(cards).encode("ascii"))
            dummy_pixels = pad_to_block(b"\x00" * 200)
    
            for name in ("masterdark.fit", "masterflat.fit", "masterbias.fit"):
                with open(os.path.join(temp_dir, name), "wb") as h:
                    h.write(hdr_bytes)
                    h.write(dummy_pixels)
    
            # Build a relative path from cwd
            rel_cal_dir = os.path.relpath(temp_dir)
            check("test setup: relative path actually relative", not os.path.isabs(rel_cal_dir))
    
            rel_plan = resolve_calibration_plan(header, traditional, rel_cal_dir)
            check("relative calibration_dir resolves dark to absolute path",
                  os.path.isabs(rel_plan["dark"]), rel_plan["dark"])
            check("relative calibration_dir resolves flat to absolute path",
                  os.path.isabs(rel_plan["flat"]), rel_plan["flat"])
            check("relative calibration_dir resolves bias to absolute path",
                  os.path.isabs(rel_plan["bias"]), rel_plan["bias"])
            check("resolved dark path points to existing file",
                  os.path.exists(rel_plan["dark"]), rel_plan["dark"])
    
            cal_cmds = derive_calibration(rel_plan, {"is_cfa": True}, "debayer")
            dark_cmd = next(c for c in cal_cmds if c.startswith("-dark="))
            flat_cmd = next(c for c in cal_cmds if c.startswith("-flat="))
            bias_cmd = next(c for c in cal_cmds if c.startswith("-bias="))
    
            check("derive_calibration emits absolute -dark",
                  os.path.isabs(dark_cmd.split("=", 1)[1]), dark_cmd)
            check("derive_calibration emits absolute -flat",
                  os.path.isabs(flat_cmd.split("=", 1)[1]), flat_cmd)
            check("derive_calibration emits absolute -bias",
                  os.path.isabs(bias_cmd.split("=", 1)[1]), bias_cmd)
    
            # Defense in depth: even if plan had an explicit relative string:
            raw_rel_plan = {"dark": "./rel_dark.fit", "flat": "rel_flat.fit",
                            "bias": "rel_bias.fit", "cc": {"enabled": False}}
            raw_cmds = derive_calibration(raw_rel_plan, {"is_cfa": True}, "debayer")
            check("derive_calibration converts raw relative dark to absolute",
                  os.path.isabs(next(c for c in raw_cmds if c.startswith("-dark=")).split("=", 1)[1]))
            check("derive_calibration converts raw relative flat to absolute",
                  os.path.isabs(next(c for c in raw_cmds if c.startswith("-flat=")).split("=", 1)[1]))
            check("derive_calibration converts raw relative bias to absolute",
                  os.path.isabs(next(c for c in raw_cmds if c.startswith("-bias=")).split("=", 1)[1]))
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_chain_demosaic_placement(self):
        check = self.check
        """The debayer flag must appear in exactly one of the two chains.
    
        Measured on Siril 1.4.4: both chains end in a three-channel RGB master,
        but the drizzle chain has to stay CFA through ``calibrate`` because
        ``seqapplyreg -drizzle`` rejects an already-debayered sequence with
        ``This sequence is not mono / CFA, cannot drizzle.`` Getting this wrong is
        silent -- the generated script still renders -- so it is asserted here
        rather than left to a real run.
        """
        section("demosaic placement differs between the two chains")
        from param_derive import derive_calibration, derive_pipeline
    
        cfa_header = {"NAXIS": 2, "BAYERPAT": "RGGB", "CCD-TEMP": 10.0,
                      "GAIN": 80, "EXPTIME": 300.0, "XPIXSZ": 4.77, "YPIXSZ": 4.77}
        device = {"class": "traditional",
                  "profile": {"flats_available": True, "dark_embedded": False,
                              "min_pairs": 8}}
        mono_device = {"class": "traditional",
                       "profile": {"flats_available": True, "dark_embedded": False,
                                   "min_pairs": 8, "mono_sensor_capable": True}}
        calibration = {"dark": "/test/d.fit", "bias": "/test/b.fit",
                       "flat": "/test/f.fit", "cc": {"enabled": False}}
    
        drizzle_cal = derive_calibration(calibration, {"is_cfa": True}, "drizzle")
        debayer_cal = derive_calibration(calibration, {"is_cfa": True}, "debayer")
        mono_cal = derive_calibration(calibration, {"is_cfa": True}, "mono")
    
        check("drizzle chain does not demosaic during calibration",
              "-debayer" not in drizzle_cal, drizzle_cal)
        check("debayer chain demosaics during calibration",
              "-debayer" in debayer_cal, debayer_cal)
        check("mono chain does not demosaic during calibration",
              "-debayer" not in mono_cal, mono_cal)
        check("mono chain does not equalize RGB layers",
              "-equalize_cfa" not in mono_cal, mono_cal)
    
        # And the same must hold through the full pipeline derivation, which is
        # what actually decides the emitted script. Mono needs a profile that marks
        # a monochrome sensor, otherwise the request is refused by design.
        for chain, expects_debayer in (("drizzle", False), ("debayer", True),
                                       ("mono", False)):
            chain_device = mono_device if chain == "mono" else device
            pipeline = derive_pipeline(cfa_header, chain_device, calibration, None,
                                       force_chain=chain)
            emitted = list(pipeline["calibrate"])
            has_debayer = "-debayer" in emitted
            check("%s pipeline %s an early demosaic"
                  % (chain, "carries" if expects_debayer else "avoids"),
                  has_debayer == expects_debayer, emitted)
            check("%s pipeline keeps CFA handling in calibration" % chain,
                  ("-cfa" in emitted) or not pipeline.get("is_cfa", True), emitted)

    def test_mono_chain(self):
        check = self.check
        """Mono must be reachable only for a mono sensor and only from debayer.
    
        Siril 1.4.4 has no ``-mono`` flag (verified against the binary), so
        "mono" means precisely "never demosaic": ``calibrate -cfa`` with no
        ``-debayer``, then ``seqapplyreg -framing=current``. Measured end to end
        that this yields ``NAXIS=2`` with ``BAYERPAT`` intact.
    
        Drizzle cannot produce mono -- ``seqapplyreg -drizzle`` demosaics
        internally -- which is why mono is reachable only from the debayer
        branch and why ``--force-chain mono`` has to bypass the thresholds
        rather than merely outrank them.
        """
        section("mono chain produces a single-channel master")
        from param_derive import derive_pipeline
    
        quiet = {"mosaic": False, "rotation_max_deg": 0.5, "scale_dev_max": 0.001,
                 "span": 30.0, "frame_count": 4}
    
        pipeline = derive_pipeline(MONO_HEADER, MONO_DEVICE, MONO_CALIBRATION, quiet)
        check("mono device picks the mono chain", pipeline["chain"] == "mono",
              pipeline["chain"])
    
        emitted = list(pipeline["calibrate"])
        check("mono never demosaics during calibration",
              "-debayer" not in emitted, emitted)
        check("mono keeps the frames CFA", "-cfa" in emitted, emitted)
        check("mono does not balance RGB layers",
              "-equalize_cfa" not in emitted, emitted)
    
        check("mono uses ordinary interpolation",
              pipeline["apply"]["args"] == ["-framing=current"],
              pipeline["apply"]["args"])
        check("mono does not upscale the canvas",
              pipeline["stack"]["maximize"] is False, pipeline["stack"])
    
        reason_text = "; ".join(pipeline["chain_info"]["reasons"])
        check("mono decision is recorded in the report",
              "monochrome sensor" in reason_text, reason_text)
    
        # Same geometry, colour device: the profile must be the only difference,
        # otherwise the mono decision leaks into ordinary colour work.
        colour = derive_pipeline(MONO_HEADER, RGB_DEVICE, MONO_CALIBRATION, quiet)
        check("colour device with identical inputs stays on debayer",
              colour["chain"] == "debayer", colour["chain"])
        check("colour device still demosaics",
              "-debayer" in list(colour["calibrate"]), list(colour["calibrate"]))

    def test_mono_chain_conflicts(self):
        check = self.check
        """Drizzle wins over mono, and --force-chain mono overrides that."""
        section("mono yields to drizzle unless explicitly forced")
        from param_derive import decide_chain, classify_input, derive_pipeline
    
        structure = classify_input(MONO_HEADER)
    
        # Rotation past the threshold forces drizzle, where mono is unreachable.
        rotated = {"mosaic": False, "rotation_max_deg": 12.0, "scale_dev_max": 0.001,
                   "span": 30.0, "frame_count": 4}
        decision = decide_chain(structure, rotated, MONO_DEVICE)
        check("large rotation takes drizzle instead of mono",
              decision["chain"] == "drizzle", decision["chain"])
        reason_text = "; ".join(decision["reasons"])
        check("the refusal names the real reason", "drizzle" in reason_text,
              reason_text)
        check("the refusal points at --force-chain mono",
              "--force-chain mono" in reason_text, reason_text)
        check("the refusal explains why drizzle cannot stay mono",
              "NAXIS=2" in reason_text, reason_text)
    
        # Mosaic spans need -framing=max, which only drizzle provides.
        mosaic = {"mosaic": True, "rotation_max_deg": 0.1, "scale_dev_max": 0.001,
                  "span": 9000.0, "frame_count": 4}
        decision = decide_chain(structure, mosaic, MONO_DEVICE)
        check("a mosaic also takes drizzle", decision["chain"] == "drizzle",
              decision["chain"])
        check("mosaic refusal points at --force-chain mono",
              "--force-chain mono" in "; ".join(decision["reasons"]),
              decision["reasons"])
    
        # Explicit override must survive the thresholds, otherwise the suggestion
        # printed in the refusal would be a lie.
        forced = derive_pipeline(MONO_HEADER, MONO_DEVICE, MONO_CALIBRATION,
                                 rotated, force_chain="mono")
        check("forced mono overrides the rotation threshold",
              forced["chain"] == "mono", forced["chain"])
        check("forced mono records that it was forced",
              "--force-chain mono" in "; ".join(forced["chain_info"]["reasons"]),
              forced["chain_info"]["reasons"])
        check("forced mono still emits a mono script",
              "-debayer" not in list(forced["calibrate"])
              and forced["apply"]["args"] == ["-framing=current"]
              and forced["stack"]["maximize"] is False,
              (list(forced["calibrate"]), forced["apply"]["args"]))
    
        # A colour device asking for mono is refused outright: its frames carry
        # real Bayer colour data that must not be thrown away.
        calm = {"mosaic": False, "rotation_max_deg": 0.5, "scale_dev_max": 0.001,
                "span": 30.0, "frame_count": 4}
        refused = derive_pipeline(MONO_HEADER, RGB_DEVICE, MONO_CALIBRATION,
                                  calm, force_chain="mono")
        check("colour sensors cannot force mono",
              refused["chain"] != "mono", refused["chain"])
        check("the colour refusal explains the lost colour data",
              "colour" in "; ".join(refused["chain_info"]["reasons"]).lower()
              or "bayer" in "; ".join(refused["chain_info"]["reasons"]).lower(),
              refused["chain_info"]["reasons"])
    
        # The CLI must actually offer the override the refusal recommends,
        # otherwise the advice cannot be followed.
        import astra_stack as entry
    
        parser = entry.build_parser()
        try:
            args = parser.parse_args(["/data", "--out", "/out", "--force-chain", "mono"])
            check("--force-chain mono is accepted", args.force_chain == "mono",
                  args.force_chain)
        except SystemExit:
            check("--force-chain mono is accepted", False, "SystemExit")
        try:
            parser.parse_args(["/data", "--out", "/out", "--force-chain", "nonsense"])
            check("--force-chain still rejects unknown values", False, "no SystemExit")
        except SystemExit:
            check("--force-chain still rejects unknown values", True)

    def test_mono_sensor_without_bayerpat(self):
        check = self.check
        """A monochrome camera writes NAXIS=2 with no BAYERPAT at all.
    
        That is byte-for-byte the same geometry a colour frame writes minus the
        mosaic, and Siril cannot tell them apart either. The device profile is the
        only admissible evidence, so the refusal must survive every other path:
        no device, a colour profile, and a forced drizzle request.
        """
        section("monochrome sensor with no BAYERPAT")
        from param_derive import classify_input, derive_pipeline
    
        bare = {"NAXIS": 2, "BITPIX": 16, "CCD-TEMP": 0.0, "GAIN": 100,
                "EXPTIME": 300.0}
    
        structure = classify_input(bare, MONO_DEVICE)
        check("mono profile accepts NAXIS=2 without BAYERPAT",
              structure["is_mono"] is True, structure)
        check("such a frame is not treated as CFA",
              structure["is_cfa"] is False, structure)
        check("frame kind records mono", structure["frame_kind"] == "mono", structure)
        check("the inference is recorded as a warning",
              any("without BAYERPAT" in warning for warning in structure["warnings"]),
              structure["warnings"])
    
        for label, device in (("no device", None), ("colour profile", RGB_DEVICE)):
            try:
                classify_input(bare, device)
                check("%s refuses an unmosaiced NAXIS=2 frame" % label, False,
                      "no exception")
            except ValueError as exc:
                check("%s refuses an unmosaiced NAXIS=2 frame" % label,
                      "BAYERPAT" in str(exc), str(exc))
    
        pipeline = derive_pipeline(bare, MONO_DEVICE, MONO_CALIBRATION)
        check("bare mono frame takes the mono chain",
              pipeline["chain"] == "mono", pipeline["chain"])
        check("bare mono frame is never debayered",
              "-debayer" not in list(pipeline["calibrate"]),
              list(pipeline["calibrate"]))
        check("bare mono frame is not marked CFA",
              "-cfa" not in list(pipeline["calibrate"]),
              list(pipeline["calibrate"]))
        check("bare mono frame does not drizzle",
              pipeline["apply"]["args"] == ["-framing=current"],
              pipeline["apply"]["args"])
        check("bare mono frame keeps the canvas",
              pipeline["stack"]["maximize"] is False, pipeline["stack"])
    
        # Drizzle demosaics internally, so honouring it here would silently turn
        # one measured signal into three invented planes.
        forced = derive_pipeline(bare, MONO_DEVICE, MONO_CALIBRATION,
                                 force_chain="drizzle")
        check("a drizzle request cannot demosaic a mono sensor",
              forced["chain"] == "mono", forced["chain"])
        check("the ignored request is reported, not swallowed",
              any("ignored" in reason for reason in forced["chain_info"]["reasons"]),
              forced["chain_info"]["reasons"])

    def test_missing_dark_override(self):
        check = self.check
        """--allow-missing-dark must downgrade the refusal, never hide it."""
        section("explicit missing-dark override")
        traditional = {"profile": {"flats_available": True, "dark_embedded": False}}
        header = {"CCD-TEMP": 0.0, "GAIN": 100, "EXPTIME": 300.0}
    
        try:
            resolve_calibration_plan(header, traditional, None)
            check("without the flag the run still refuses", False, "no exception")
        except Exception as exc:  # noqa: BLE001 - CalibrationMissing expected
            check("without the flag the run still refuses",
                  type(exc).__name__ == "CalibrationMissing", type(exc).__name__)
    
        plan = resolve_calibration_plan(header, traditional, None,
                                        allow_missing_dark=True)
        check("the override continues without a dark", plan["dark"] is None, plan)
        check("the override records why",
              plan["dark_missing_reason"] == "explicit_override", plan)
        check("bad-pixel correction is disabled with no dark",
              plan["cc"]["enabled"] is False, plan["cc"])
        check("the override states the consequence",
              "NOT corrected" in " ".join(plan["warnings"]), plan["warnings"])
    
        from param_derive import derive_calibration
    
        commands = derive_calibration(plan, {"is_cfa": False}, "mono")
        check("no -cc=dark without a master",
              "-cc=dark" not in commands, commands)
        check("no -dark= without a master",
              not any(item.startswith("-dark=") for item in commands), commands)
    
        # The CLI is the only way a user reaches this path.
        import astra_stack as entry
    
        parser = entry.build_parser()
        args = parser.parse_args(["/data", "--out", "/out", "--allow-missing-dark"])
        check("--allow-missing-dark is accepted",
              args.allow_missing_dark is True, args.allow_missing_dark)
        args = parser.parse_args(["/data", "--out", "/out"])
        check("the override is off by default",
              args.allow_missing_dark is False, args.allow_missing_dark)

    def test_cosmetic_parameters(self):
        check = self.check
        section("dark-based cosmetic correction requires a master")
        from param_derive import derive_calibration
    
        for enabled in (False, True):
            for dark in (None, "/test/master dark.fit"):
                for low in (None, 3, 0):
                    plan = {"dark": dark, "cc": {"enabled": enabled, "siglo": low, "sighi": 3}}
                    try:
                        actual = derive_calibration(plan, {"is_cfa": True}, "drizzle")
                    except ValueError:
                        actual = None
                    if enabled and dark and low is None:
                        # Measured on siril-cli 1.4.4: only the bare default form
                        # "-cc=dark" is scriptable; sigma values cannot be passed.
                        check("cosmetic enabled=%s dark=%s low=%s" % (enabled, bool(dark), low),
                              actual is not None and "-cc=dark" in actual, actual)
                    elif enabled and dark:
                        check("custom cc sigmas refused (not scriptable in 1.4.4)",
                              actual is None, actual)
                    else:
                        check("cosmetic enabled=%s dark=%s low=%s" % (enabled, bool(dark), low),
                              actual is not None and "-cc=dark" not in actual, actual)
                    check("cosmetic parameter derivation preserves the policy flag",
                          plan["cc"]["enabled"] is enabled)

    def test_mono_report_contract(self):
        check = self.check
        """The report must say whether a mono master was published.
    
        ``output_naxis`` is the only place a consumer learns the plane count
        without opening the FITS file, and ``mono_requested`` distinguishes "the
        profile wanted mono but drizzle was required" from "this was never a mono
        sensor".
        """
        section("mono report contract")
        import astra_stack as entry
        from param_derive import classify_input, derive_pipeline
    
        calm = {"mosaic": False, "rotation_max_deg": 0.5, "scale_dev_max": 0.001,
                "span": 30.0, "frame_count": 4}
        rotated = {"mosaic": False, "rotation_max_deg": 12.0, "scale_dev_max": 0.001,
                   "span": 30.0, "frame_count": 4}
    
        mono = derive_pipeline(MONO_HEADER, MONO_DEVICE, MONO_CALIBRATION, calm)
        stacking = entry._stacking_report(mono, None)
        registration = entry._registration_report(mono, None)
        check("mono declares a two-plane master", stacking["output_naxis"] == 2,
              stacking["output_naxis"])
        check("mono is flagged in the registration section",
              registration["mono_requested"] is True, registration)
        check("mono registration names the chain",
              registration["chain"] == "mono", registration["chain"])
    
        colour = derive_pipeline(MONO_HEADER, RGB_DEVICE, MONO_CALIBRATION, calm)
        check("colour chain declares a three-plane master",
              entry._stacking_report(colour, None)["output_naxis"] == 3)
        check("colour chain is not flagged as mono",
              entry._registration_report(colour, None)["mono_requested"] is False)
    
        # Drizzle was forced on a mono sensor: the flag stays on, because the
        # profile did ask for mono -- that is exactly what a consumer needs to see
        # in order to understand why a three-channel master appeared.
        forced = derive_pipeline(MONO_HEADER, MONO_DEVICE, MONO_CALIBRATION, rotated)
        check("drizzle on a mono sensor still reports three planes",
              entry._stacking_report(forced, None)["output_naxis"] == 3)
        check("drizzle on a mono sensor keeps the request visible",
              entry._registration_report(forced, None)["mono_requested"] is True)
        check("the drizzle refusal reaches the report reasons",
              "--force-chain mono" in "; ".join(forced["chain_info"]["reasons"]),
              forced["chain_info"]["reasons"])
    
        # Mono is decided by the profile, so re-deciding from the post-transform
        # summary would report a disagreement that is only the rotation leaving.
        notes = entry._chain_disagreement_notes(
            mono, rotated, MONO_HEADER, MONO_DEVICE
        )
        check("mono never reports a chain contradiction", notes == [], notes)
    
        # A colour run still gets the review, so the guard is not blanket.
        colour_notes = entry._chain_disagreement_notes(
            colour, rotated, MONO_HEADER, RGB_DEVICE
        )
        check("colour runs are still reviewed for contradictions",
              isinstance(colour_notes, list))
    
        # The struct classification is untouched: mono frames are still CFA input.
        structure = classify_input(MONO_HEADER)
        check("mono input is still classified as CFA",
              structure["is_cfa"] and structure["frame_kind"] == "cfa", structure)

    def test_drizzle_flat_report(self):
        check = self.check
        section("drizzle flat weights are independent of calibration flats")
        import astra_stack as entry
    
        observed = {"span_x": 10.0, "span_y": 10.0, "span": 14.14,
                    "rotation_max_deg": 12.0, "scale_dev_max": 0.0,
                    "median_scale": 1.0, "frame_count": 4}
        for label, available, flat in (("no-flat-device", False, None),
                                        ("missing-master", True, None),
                                        ("with-master", True, "/test/master flat.fit")):
            device = dict(RGB_DEVICE, profile=dict(RGB_DEVICE["profile"], flats_available=available))
            calibration = dict(MONO_CALIBRATION, flat=flat)
            pipeline = derive_pipeline(MONO_HEADER, device, calibration, observed)
            builder, _ = entry.build_script("/test", "flat-report", pipeline, calibration,
                                             "/test/master.fits")
            calibrate = next(args for _, command, args in builder.commands if command == "calibrate")
            apply = next(args for _, command, args in builder.commands if command == "seqapplyreg")
            check(label + " retains calibration flat selection",
                  [arg for arg in calibrate if arg.startswith("-flat=")]
                  == (["-flat=" + flat] if flat else []), calibrate)
            check(label + " keeps drizzle weights disabled",
                  "-drizzle" in apply and not any(arg.startswith("-flat=") for arg in apply), apply)
            for phase, summary in (("planned", None), ("executed", observed)):
                report = entry._registration_report(pipeline, summary)
                note = report["flat_note"]
                check(label + " " + phase + " reports weights separately",
                      report["flat_used"] == "none" and "seqapplyreg has no -flat=" in note
                      and "calibration.flat" in note, report)
                check(label + " " + phase + " avoids device and execution claims",
                      "device" not in note and "ran" not in note, note)
            check(label + " preserves degradation policy",
                  report["degradations"] == pipeline["chain_info"]["degradations"])
        for chain in ("mono", "debayer"):
            pipeline = derive_pipeline(MONO_HEADER, MONO_DEVICE, MONO_CALIBRATION, force_chain=chain)
            report = entry._registration_report(pipeline, None)
            check(chain + " adds no drizzle flat fields",
                  "flat_used" not in report and "flat_note" not in report, report)

    def test_chain_review(self):
        check = self.check
        section("chain review uses final pre-transform geometry")
        from astra_stack import _chain_disagreement_notes
        from param_derive import MOSAIC_SPAN_FACTOR, ROTATION_THRESHOLD_DEG, SCALE_DEV_THRESHOLD
    
        header = {"NAXIS": 2, "NAXIS1": 64, "NAXIS2": 48,
                  "BITPIX": 16, "BAYERPAT": "GRBG"}
        device = {"profile": {"flats_available": False}}
        quiet = {"span": 9.0, "rotation_max_deg": 0.0, "scale_dev_max": 0.0,
                 "mosaic": False, "frame_count": 4}
        for name, summary, expected in [
            ("quiet", quiet, "debayer"),
            ("mosaic", dict(quiet, mosaic=True, span=80.0), "drizzle"),
            ("rotation", dict(quiet, rotation_max_deg=ROTATION_THRESHOLD_DEG+0.01), "drizzle"),
            ("scale", dict(quiet, scale_dev_max=SCALE_DEV_THRESHOLD+0.001), "drizzle"),
            ("rotation boundary", dict(quiet, rotation_max_deg=ROTATION_THRESHOLD_DEG), "debayer"),
            ("scale boundary", dict(quiet, scale_dev_max=SCALE_DEV_THRESHOLD), "debayer"),
        ]:
            notes = _chain_disagreement_notes({"chain": expected}, summary, header, device)
            check(name + " agrees with automatic selection", notes == [], notes)
            chosen = "debayer" if expected == "drizzle" else "drizzle"
            notes = _chain_disagreement_notes({"chain": chosen}, summary, header, device)
            check(name + " reports both chains and source evidence", len(notes) == 1
                  and ("automatic chain '%s'" % chosen) in notes[0]
                  and ("'%s' recommended" % expected) in notes[0]
                  and "pre-transform registration:" in notes[0], notes)
            check(name + " does not invent missing probe or timing evidence",
                  "no probe" not in " ".join(notes) and "slower" not in " ".join(notes), notes)
    
        for span, expected in [(64*MOSAIC_SPAN_FACTOR, "debayer"),
                               (64*MOSAIC_SPAN_FACTOR+0.01, "drizzle")]:
            summary = summarize_transforms({"frames": [
                {"dx": 0, "dy": 0, "rotation_deg": 0, "scale": 1},
                {"dx": span, "dy": 0, "rotation_deg": 0, "scale": 1},
            ]}, 64, 48, mosaic_factor=MOSAIC_SPAN_FACTOR)
            check("mosaic span boundary %s" % span,
                  _chain_disagreement_notes({"chain": expected}, summary, header, device) == [])
    
        for chosen in ("drizzle", "debayer"):
            summary = quiet if chosen == "drizzle" else dict(quiet, rotation_max_deg=33.19)
            check("forced " + chosen + " is not called a contradiction",
                  _chain_disagreement_notes({"chain": chosen}, summary, header, device,
                                            force_chain=chosen) == [])
        check("RGB geometry does not suggest CFA drizzle",
              _chain_disagreement_notes({"chain": "rgb"}, dict(quiet, mosaic=True),
                                        dict(header, NAXIS=3), device) == [])
        for missing in (None, {}):
            check("missing summary does not invent evidence",
                  _chain_disagreement_notes({"chain": "drizzle"}, missing, header, device) == [])

if __name__ == "__main__":
    unittest.main()
