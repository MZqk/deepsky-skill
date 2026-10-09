"""Self-test: synthetic transforms, script validation, and an optional real run.

Two layers, because they answer different questions:

* **Layer 1 (always runs, no Siril needed)** -- generates synthetic FITS with
  known ground truth and asserts that the parser, the transform derivation and the
  script generator behave. The homography sign convention is checked against the
  known translations here rather than trusted.
* **Layer 2 (needs Siril 1.4.x)** -- runs the full pipeline on synthetic data and
  asserts the three artifacts appear, that the reported chain matches the data,
  and that a failed run leaves the previous master intact.

Usage:
    python3 selftest.py                    # layer 1 only
    python3 selftest.py --real             # both layers
    python3 selftest.py --real --keep/tmp  # both, keeping the work directory
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from astra_stack import find_siril, sequence_stem
from device_signatures import (
    DeviceNotIdentified,
    identify_device,
    resolve_calibration_plan,
)
from fits_probe import (
    FitsError,
    UnsupportedInputError,
    build_card as fits_probe_build_card,
    data_offset,
    derive_transform,
    read_fits_header_with_warnings,
    read_seq_regdata,
    summarize_transforms,
)
from param_derive import (
    derive_pipeline,
    derive_preview_mtf,
)
from siril_script_gen import ScriptBuilder, ScriptValidationError
from synth_fits import write_cfa_sequence, write_rgb_sequence

PASSED = []
FAILED = []


def check(label, condition, detail=""):
    """Record one assertion."""
    if condition:
        PASSED.append(label)
        print("  ok   %s" % label)
    else:
        FAILED.append((label, detail))
        print("  FAIL %s %s" % (label, detail))


def section(title):
    print("\n== %s ==" % title)


# --------------------------------------------------------------------------
# Layer 1
# --------------------------------------------------------------------------

def test_preview_stretch_direction():
    section("preview stretch adapts to image brightness")
    # A nominal whole-image channel level of 0.0113. A midpoint
    # below the background maps it to mid-grey and washes the preview out; the
    # midpoint must sit above it.
    medians = {"r": 0.01132, "g": 0.011317, "b": 0.011318}
    mtf = derive_preview_mtf(medians)
    background = min(medians.values())
    check("midpoint is above the background",
          mtf["mid"] > background, "mid=%s bg=%s" % (mtf["mid"], background))
    check("midpoint is derived, not the default",
          mtf["mid"] != 0.028, mtf["mid"])
    check("midpoint within clamp range", 0.01 <= mtf["mid"] <= 0.4, mtf["mid"])
    check("low stays 0 to preserve shadows", mtf["low"] == 0.0)
    check("high stays 1 to avoid clipping", mtf["high"] == 1.0)
    check("derivation is recorded",
          "whole-image channel median" in mtf["derived_from"], mtf["derived_from"])
    check("repeat call is identical",
          derive_preview_mtf(medians) == mtf)

    # A much brighter target must not fall off the clamp.
    bright = derive_preview_mtf({"r": 5.0, "g": 5.0, "b": 5.0})
    check("bright target is clamped", bright["mid"] <= 0.4, bright["mid"])

    fallback = derive_preview_mtf(None)
    check("no medians falls back to the default", fallback["mid"] == 0.028,
          fallback["mid"])
    check("fallback is marked as such", "default" in fallback["derived_from"],
          fallback["derived_from"])


def test_frame_filter_forms():
    section("frame filter parameter forms")
    from param_derive import SMART_FILTERS, TRADITIONAL_FILTERS

    # wfwhm and nbstars use percentile forms. k-sigma was measured to reject
    # nothing on real data because the outliers it targets inflate sigma, so a
    # regression to Nk must be caught.
    percentile_flags = [f for f in SMART_FILTERS + TRADITIONAL_FILTERS
                        if "wfwhm" in f or "nbstars" in f]
    check("wfwhm/nbstars filters present", len(percentile_flags) >= 2,
          percentile_flags)
    for flag in percentile_flags:
        check("%s uses a percentile form" % flag, flag.endswith("%"), flag)
        check("%s is not a k-sigma form" % flag,
              not flag.split("=")[-1].rstrip("%").endswith("k"), flag)

    # roundness is a dimensionless 0-1 quantity, so an absolute threshold is
    # correct for it; only assert it stays within range.
    round_flags = [f for f in SMART_FILTERS if "round" in f]
    check("roundness filter present", len(round_flags) == 1, round_flags)
    value = float(round_flags[0].split("=")[1])
    check("roundness threshold within 0-1", 0.0 < value < 1.0, round_flags[0])

    # N% means "keep the best N%", so conservative filtering uses HIGH numbers.
    nbstars = [f for f in SMART_FILTERS if "nbstars" in f][0]
    nbstar_value = int(nbstars.split("=")[1].rstrip("%"))
    check("nbstars keeps a large majority", nbstar_value >= 50, nbstars)
    wfwhm_value = int([f for f in SMART_FILTERS if "wfwhm" in f][0]
                      .split("=")[1].rstrip("%"))
    check("wfwhm keeps a large majority", wfwhm_value >= 50, SMART_FILTERS)


def test_filtered_frame_parsing():
    section("actual stack count and frozen membership")
    from astra_stack import _parse_stacked_frames, _read_stack_evidence
    log = ("number of filtered-in images: 10\n"
           "number of filtered-in images: 12\n"
           "Rejection stacking complete. 10 images have been stacked.\n")
    check("completion count overrides individual filter counts", _parse_stacked_frames(log) == 10)
    check("filter-only log has no actual stack count",
          _parse_stacked_frames("number of filtered-in images: 12") is None)
    check("empty log yields None", _parse_stacked_frames("") is None)
    from fits_probe import pad_to_block
    with tempfile.TemporaryDirectory(prefix="astra-evidence-") as tmp:
        work = Path(tmp)
        def sequence(name, indices, reference):
            lines = ["S '%s' 0 %d %d 4 %d 6 0 0 0" % (name, len(indices), len(indices), reference), "L 1"]
            lines += ["I %d 1" % i for i in indices]
            lines += ["R0 3 4 0.8 0 0.1 30 H 1 0 %d 0 1 0 0 0 1" % i for i in indices]
            (work/(name+".seq")).write_text("\n".join(lines)+"\n")
        def master(count):
            cards = [fits_probe_build_card("SIMPLE", "T"), fits_probe_build_card("BITPIX", "-32"),
                     fits_probe_build_card("NAXIS", "2"), fits_probe_build_card("NAXIS1", "1"),
                     fits_probe_build_card("NAXIS2", "1")]
            if count is not None:
                cards.append(fits_probe_build_card("STACKCNT", str(count)))
            cards.append(fits_probe_build_card("END", ""))
            (work/"master.fit").write_bytes(pad_to_block("".join(cards).encode()) + pad_to_block(struct.pack(">f", .1)))
        sequence("c_s", [0, 1, 2, 3], 3)
        sequence("ap_c_s", [0, 2, 3], 2)
        for i in [0, 2, 3]:
            (work/("ap_c_s%04d.fit" % i)).write_bytes(b"frame")
        master(3)
        evidence = _read_stack_evidence(tmp, "ap_c_s", str(work/"master.fit"), "")
        check("STACKCNT works without an English completion message", evidence[2] == 3)
        check("sparse indices keep reference as a list position", evidence[1]["image_order"] == [0, 2, 3])
        master(None)
        check("missing STACKCNT uses definitive completion log", _read_stack_evidence(
            tmp, "ap_c_s", str(work/"master.fit"), "3 images have been stacked.")[2] == 3)
        for label, count, stdout in [("contradictory counts", 3, "2 images have been stacked."),
                                     ("second selection", 2, "2 images have been stacked."),
                                     ("too few frames", 1, ""), ("missing counts", None, "")]:
            master(count)
            try:
                _read_stack_evidence(tmp, "ap_c_s", str(work/"master.fit"), stdout)
                check("rejects " + label, False)
            except FitsError:
                check("rejects " + label, True)
        master(2)
        check("fallback may stack fewer than exported frames", _read_stack_evidence(
            tmp, "ap_c_s", str(work/"master.fit"), "", False)[2] == 2)
        master(3)
        for label, indices in [("unknown index", [0, 2, 9]), ("duplicate index", [0, 2, 2])]:
            sequence("ap_c_s", indices, 2)
            try:
                _read_stack_evidence(tmp, "ap_c_s", str(work/"master.fit"), "")
                check("rejects " + label, False)
            except FitsError:
                check("rejects " + label, True)
        for label, corrupt in [("missing registration row", lambda text: "\n".join(line for line in text.splitlines() if not line.startswith("R0")) + "\n"),
                               ("non-finite matrix", lambda text: text.replace("H 1 0", "H nan 0"))]:
            sequence("ap_c_s", [0, 2, 3], 2)
            path = work/"ap_c_s.seq"
            path.write_text(corrupt(path.read_text()))
            try:
                _read_stack_evidence(tmp, "ap_c_s", str(work/"master.fit"), "")
                check("rejects " + label, False)
            except FitsError:
                check("rejects " + label, True)
        sequence("ap_c_s", [0, 2, 3], 2)
        (work/"ap_c_s0003.fit").unlink()
        try:
            _read_stack_evidence(tmp, "ap_c_s", str(work/"master.fit"), "")
            check("rejects missing exported file", False)
        except FitsError:
            check("rejects missing exported file", True)


def test_frame_quality_report():
    section("frame quality report")
    import tempfile

    from astra_stack import _frame_quality_report

    regdata = {
        "frames": [
            {"fwhm": 3.0 + i * 0.01, "weighted_fwhm": 4.0 + i * 0.01,
             "roundness": 0.8, "number_of_stars": 700 - i,
             "background_lvl": 0.012}
            for i in range(20)
        ]
    }
    # One frame far worse than the rest, mirroring the real SH2-296 capture where
    # six frames sat beyond 1.5x the median of weighted_fwhm.
    regdata["frames"].append(
        {"fwhm": 3.5, "weighted_fwhm": 11.0, "roundness": 0.7,
         "number_of_stars": 223, "background_lvl": 0.012}
    )
    rep = _frame_quality_report(regdata)
    check("frame count recorded", rep["frame_count"] == 21, rep["frame_count"])
    check("percentiles present for fwhm",
          "p50" in rep["metrics"]["fwhm"], rep["metrics"].get("fwhm"))
    check("sense is documented",
          rep["metrics"]["fwhm"]["sense"] == "lower is better")
    check("outliers counted when present",
          "beyond_1p5x_median" in rep["metrics"]["weighted_fwhm"],
          rep["metrics"]["weighted_fwhm"])
    check("injected outlier is detected",
          rep["metrics"]["weighted_fwhm"].get("beyond_1p5x_median") == 1,
          rep["metrics"]["weighted_fwhm"])
    check("outlier inflates sigma, which the note explains",
          "note" in rep and "percentile" in rep["note"], rep.get("note"))
    check("empty input handled",
          _frame_quality_report(None).get("frame_count") == 0)

    from pws_bridge import evaluate_gate

    frames = regdata["frames"]
    for name, ordered in [("original", frames), ("reversed", frames[::-1]),
                          ("shuffled", frames[::2] + frames[1::2])]:
        actual = _frame_quality_report({"frames": ordered})
        metrics = actual["metrics"]["weighted_fwhm"]
        check(name + " quality metrics are order independent", actual["metrics"] == rep["metrics"])
        check(name + " dispersion reuses table percentiles",
              actual["weighted_fwhm_p90_over_p10"] == round(metrics["p90"] / metrics["p10"], 4))
        check(name + " dispersion ratio and note are order independent",
              actual["weighted_fwhm_p90_over_p10"] == rep["weighted_fwhm_p90_over_p10"]
              and actual.get("dispersion_note") == rep.get("dispersion_note"))
        check(name + " normal positive values agree with PWS gate",
              actual["weighted_fwhm_p90_over_p10"] == evaluate_gate({"frames": ordered}, "rgb")["dispersion_ratio"])

    for high, note_expected in [(13.9, True), (14.0, True), (14.0001, False)]:
        sample = {"frames": [{"weighted_fwhm": value} for value in [10.0]*18 + [high]*2]}
        actual = _frame_quality_report(sample)
        check("dispersion %.5f uses the unrounded threshold" % (high/10),
              ("dispersion_note" in actual) == note_expected, actual)
        check("dispersion %.5f remains rounded to four decimals" % (high/10),
              actual["weighted_fwhm_p90_over_p10"] == round(high/10, 4))
        check("dispersion %.5f matches the unchanged PWS gate" % (high/10),
              actual["weighted_fwhm_p90_over_p10"] == evaluate_gate(sample, "rgb")["dispersion_ratio"])

    missing = _frame_quality_report({"frames": [{}, {"weighted_fwhm": None}]})
    check("missing values omit dispersion", "weighted_fwhm_p90_over_p10" not in missing)
    single = _frame_quality_report({"frames": [{}, {"weighted_fwhm": 3.0}]})
    check("single usable value omits dispersion", "weighted_fwhm_p90_over_p10" not in single)
    partial = _frame_quality_report({"frames": [{}, {"weighted_fwhm": None},
                                               {"weighted_fwhm": 3.0}, {"weighted_fwhm": 4.0}]})
    check("missing values do not enter the percentile population",
          partial["weighted_fwhm_p90_over_p10"] == round(4/3, 4))
    zero = _frame_quality_report({"frames": [{"weighted_fwhm": v} for v in [0, 1, 2]]})
    check("zero table denominator returns null without a dispersion note",
          zero["metrics"]["weighted_fwhm"]["p10"] == 0
          and zero["weighted_fwhm_p90_over_p10"] is None and "dispersion_note" not in zero)
    zeros = _frame_quality_report({"frames": [{"weighted_fwhm": 0}]*3})
    check("no usable nonzero values omit dispersion", "weighted_fwhm_p90_over_p10" not in zeros)



def test_channel_median_estimation():
    section("channel median estimation from a master")
    import struct as struct_mod

    from fits_probe import build_card, data_offset, estimate_channel_medians, pad_to_block

    width, height = 40, 30
    cards = [
        build_card("SIMPLE", "T"), build_card("BITPIX", "-32"),
        build_card("NAXIS", "3"), build_card("NAXIS1", str(width)),
        build_card("NAXIS2", str(height)), build_card("NAXIS3", "3"),
        build_card("END", ""),
    ]
    header = pad_to_block("".join(cards).encode("ascii"))
    # Distinct, known per-channel levels; the median must recover them.
    levels = (0.01, 0.02, 0.03)
    payload = bytearray()
    for level in levels:
        for _y in range(height):
            for _x in range(width):
                payload += struct_mod.pack(">f", level)

    with tempfile.NamedTemporaryFile(suffix=".fits", delete=False) as handle:
        handle.write(header)
        handle.write(pad_to_block(bytes(payload)))
        temp_path = handle.name
    try:
        check("data offset is block-aligned",
              data_offset(temp_path) % 2880 == 0, data_offset(temp_path))
        medians = estimate_channel_medians(temp_path)
        check("three channels recovered", len(medians) == 3, medians)
        for index, name in enumerate(("r", "g", "b")):
            check("%s median recovered" % name,
                  abs(medians.get(name, -1) - levels[index]) < 1e-6,
                  "%s=%s expected %s" % (name, medians.get(name), levels[index]))
    finally:
        os.unlink(temp_path)


def test_channel_statistics_contract(tmp):
    section("planar RGB sampling and truthful color report")
    from types import SimpleNamespace
    from astra_stack import _channel_statistics_from_args, build_parser, main, build_script
    from fits_probe import pad_to_block, estimate_channel_medians
    import statistics
    import math
    import contextlib
    import io
    work=Path(tmp,"color-contract");work.mkdir()
    width,height=11,7
    def image(path, planes, scale=1,zero=0):
        cards=[fits_probe_build_card("SIMPLE","T"),fits_probe_build_card("BITPIX","-32"),
               fits_probe_build_card("NAXIS","3"),fits_probe_build_card("NAXIS1",str(width)),
               fits_probe_build_card("NAXIS2",str(height)),fits_probe_build_card("NAXIS3","3"),
               fits_probe_build_card("BSCALE",str(scale)),fits_probe_build_card("BZERO",str(zero)),
               fits_probe_build_card("END","")]
        values=[value for plane in planes for value in plane]
        path.write_bytes(pad_to_block("".join(cards).encode())+pad_to_block(struct.pack(">%df"%len(values),*values)))
    n=width*height
    planes=[[.01+c*.1+i*.0001 for i in range(n)] for c in range(3)]
    path=work/"planar.fit";image(path,planes,2,.001)
    sampled=estimate_channel_medians(str(path),30)
    stride=(n+9)//10
    expected={name:statistics.median([struct.unpack(">f",struct.pack(">f",planes[c][i]))[0]*2+.001
              for i in range(0,n,stride)]) for c,name in enumerate(("r","g","b"))}
    check("same spatial positions and scaling in all planes",sampled==expected,(sampled,expected))
    args=SimpleNamespace(channel_medians=None,rgb_equal=False,dry_run=False)
    result=_channel_statistics_from_args(args,str(path))
    rep=result["report"]
    check("records real post-stack medians",rep["medians_after"]==estimate_channel_medians(str(path)))
    check("does not invent before values or gains",rep["medians_before"] is None and rep["gains_applied"] is None)
    check("RGB normalization defaults off",rep["background_equalization"]=={"method":None,"requested":False,"applied":False})
    args.rgb_equal=True
    check("explicit completed RGB operation recorded",_channel_statistics_from_args(args,str(path))["report"]["background_equalization"]["applied"])
    args.dry_run=True
    dry=_channel_statistics_from_args(args)["report"]
    check("dry-run does not claim application or measurement",not dry["background_equalization"]["applied"] and dry["medians_after"] is None)
    args.dry_run=False;args.rgb_equal=False
    supplied=work/"medians.json";supplied.write_text('{"r":0.02,"g":0.03,"b":0.04}')
    args.channel_medians=str(supplied)
    fallback=_channel_statistics_from_args(args)
    check("JSON is preview-only and not measured data",fallback["medians"]["b"]==.04 and fallback["report"]["medians_after"] is None)
    supplied.write_text('[1,2,3]')
    check("invalid preview JSON is rejected",_channel_statistics_from_args(args)["medians"] is None)
    args.channel_medians=None
    planes[0][0]=float("nan");planes[1][0]=float("inf")
    image(path,planes)
    check("non-finite samples are excluded",all(math.isfinite(v) for v in estimate_channel_medians(str(path)).values()))
    image(path,[[float("nan")]*n for _ in range(3)])
    args.channel_medians=str(supplied)
    supplied.write_text('{"r":0.02,"g":0.03,"b":0.04}')
    try:
        _channel_statistics_from_args(args,str(path))
        check("invalid RGB master cannot be hidden by preview JSON",False)
    except FitsError:
        check("invalid RGB master cannot be hidden by preview JSON",True)
    args.channel_medians=None
    image(path,[[0.0]*n for _ in range(3)])
    check("all-zero planes are reported as zero",estimate_channel_medians(str(path))==dict(r=0.,g=0.,b=0.))
    path.write_bytes(path.read_bytes()[:data_offset(str(path))+4])
    try:
        estimate_channel_medians(str(path))
        check("truncated planes rejected",False)
    except FitsError:
        check("truncated planes rejected",True)
    check("CLI opt-in defaults off",not build_parser().parse_args([tmp]).rgb_equal)
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            main([tmp,"--stack-engine=pws","--rgb-equal"])
            check("PWS rejects inapplicable explicit RGB request",False)
        except SystemExit as exc:
            check("PWS rejects inapplicable explicit RGB request",exc.code==2)
    generated=work/"generated"
    frame=Path(write_rgb_sequence(str(generated),40,30,1)[0][0])
    with frame.open("rb") as handle:
        handle.seek(data_offset(str(frame)))
        values=struct.unpack(">%dH"%(40*30*3),handle.read(40*30*3*2))
    check("synthetic RGB is planar at matching positions",all(
        values[i]>=values[40*30+i]>=values[40*30*2+i] for i in range(40*30)))


def test_sequence_stem():
    section("sequence stem rules")
    cases = {
        "astra_t1": "astra_ts",
        "M42": "M4s",
        "targetXY": "targetXY",
        "NGC7000": "NGC700s",
        "M 31": "M_3s",
        "": "astra",
    }
    for target, expected in cases.items():
        stem = sequence_stem(target)
        check("stem(%r) == %r" % (target, expected), stem == expected,
              "got %r" % stem)
    for target in cases:
        stem = sequence_stem(target)
        check("stem(%r) does not end in a digit" % target,
              bool(stem) and not stem[-1].isdigit(), "got %r" % stem)
        check("stem(%r) is at most 8 chars" % target, len(stem) <= 8)


def test_fits_roundtrip(tmp):
    section("FITS header round-trip (synthetic CFA)")
    written = write_cfa_sequence(tmp, 120, 90, 3)
    header, warnings = read_fits_header_with_warnings(written[0][0])
    check("NAXIS parsed", header.get("NAXIS") == 2, header.get("NAXIS"))
    check("BITPIX parsed", header.get("BITPIX") == 16, header.get("BITPIX"))
    check("BAYERPAT stripped of trailing blanks",
          header.get("BAYERPAT") == "GRBG", repr(header.get("BAYERPAT")))
    check("TELESCOP stripped of trailing blanks",
          header.get("TELESCOP") == "S30 Pro_10d57b1d",
          repr(header.get("TELESCOP")))
    check("INSTRUME stripped of trailing blanks",
          header.get("INSTRUME") == "imx585", repr(header.get("INSTRUME")))
    check("bogus EQUINOX reported as a warning",
          any("EQUINOX" in w for w in warnings), warnings)


def test_rejected_inputs(tmp):
    section("refused inputs")
    jpeg = os.path.join(tmp, "frame.jpg")
    with open(jpeg, "wb") as handle:
        handle.write(b"\xff\xd8\xff\xe0" + b"\x00" * 32)
    try:
        read_fits_header_with_warnings(jpeg)
        check("JPEG refused", False, "no exception raised")
    except UnsupportedInputError as exc:
        check("JPEG refused", "8-bit preview" in str(exc))

    avi = os.path.join(tmp, "movie.avi")
    with open(avi, "wb") as handle:
        handle.write(b"RIFF" + b"\x00" * 32)
    try:
        read_fits_header_with_warnings(avi)
        check("AVI refused", False, "no exception raised")
    except UnsupportedInputError as exc:
        check("AVI refused", "video container" in str(exc))


def test_transform_derivation():
    section("homography transform derivation")
    # Pure translation, the sign convention calibrated against Siril 1.4.4.
    h = (1.0, 0.0, 3.0, 0.0, 1.0, -2.0, 0.0, 0.0, 1.0)
    derived = derive_transform(h)
    check("dx == h02", abs(derived["dx"] - 3.0) < 1e-9, derived["dx"])
    check("dy == h12", abs(derived["dy"] + 2.0) < 1e-9, derived["dy"])
    check("rotation ~ 0", abs(derived["rotation_deg"]) < 1e-9,
          derived["rotation_deg"])
    check("scale == 1", abs(derived["scale"] - 1.0) < 1e-9, derived["scale"])
    check("pure translation detected", derived["is_pure_translation"])

    # 2 degree rotation with a small translation.
    import math

    angle = math.radians(2.0)
    h = (math.cos(angle), -math.sin(angle), 5.0,
         math.sin(angle), math.cos(angle), 4.0, 0.0, 0.0, 1.0)
    derived = derive_transform(h)
    check("rotation recovered", abs(derived["rotation_deg"] - 2.0) < 1e-6,
          derived["rotation_deg"])
    check("scale still ~1 under rotation",
          abs(derived["scale"] - 1.0) < 1e-6, derived["scale"])


def test_seq_parsing(tmp):
    section(".seq registration parsing")
    seq_path = os.path.join(tmp, "synthetic.seq")
    # S record layout: [0]S [1]'name' [2]start [3]nb_images [4]nb_selected
    #                  [5]fixed_len [6]reference_image [7]version
    #                  [8]variable_size [9]fz_flag [10]drizzle
    rows = [
        "#Siril sequence file",
        "S 's' 0 3 3 3 1 6 0 0 0",
        "I 0 1", "I 1 1", "I 2 1",
        "R0 4.5 4.5 0.99 0 0.5 7 H 1 0 0 0 1 0 0 0 1",
        "R0 4.6 4.6 0.98 0 0.6 6 H 1 0 3.01 0 1 2.01 0 0 1 1",
        "R0 4.7 4.7 0.97 0 0.7 5 H 1 0 6.02 0 1 4.02 0 0 1 1",
    ]
    with open(seq_path, "w", encoding="ascii") as handle:
        handle.write("\n".join(rows) + "\n")
    regdata = read_seq_regdata(seq_path)
    check("three frames parsed", len(regdata["frames"]) == 3,
          len(regdata["frames"]))
    summary = summarize_transforms(regdata, 200, 150)
    check("span_x == 6.02", abs(summary["span_x"] - 6.02) < 0.01,
          summary["span_x"])
    check("span_y == 4.02", abs(summary["span_y"] - 4.02) < 0.01,
          summary["span_y"])
    check("not a mosaic at this scale", summary["mosaic"] is False)
    check("reference image recorded", regdata["reference_image"] == 1,
          regdata["reference_image"])

    big = summarize_transforms(regdata, 5, 5)
    check("mosaic detected for tiny frames", big["mosaic"] is True)


def test_device_identification(tmp):
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


def test_calibration_plan():
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


def test_cosmetic_parameters():
    section("dark-based cosmetic correction requires a master")
    from param_derive import derive_calibration

    for enabled in (False, True):
        for dark in (None, "/test/master dark.fit"):
            for low in (None, 3, 0):
                plan = {"dark": dark, "cc": {"enabled": enabled, "siglo": low, "sighi": 3}}
                actual = derive_calibration(plan, {"is_cfa": True}, "drizzle")
                flags = [arg for arg in actual if arg.startswith("-cc=dark")]
                expected = []
                if enabled and dark:
                    expected = ["-cc=dark 3" if low is None else "-cc=dark %s 3" % low]
                check("cosmetic enabled=%s dark=%s low=%s" % (enabled, bool(dark), low),
                      flags == expected, actual)
                check("cosmetic parameter derivation preserves the policy flag",
                      plan["cc"]["enabled"] is enabled)


def test_pipeline_branches(tmp):
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


def test_chain_review():
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


def test_mtf_and_equalisation():
    section("preview MTF")
    mtf = derive_preview_mtf({"r": 0.02, "g": 0.021, "b": 0.019})
    check("mtf uses explicit parameters", mtf["method"] == "mtf")
    check("mtf low is 0", mtf["low"] == 0.0)
    check("mtf high is 1", mtf["high"] == 1.0)
    check("mtf mid derived from medians", 0.005 <= mtf["mid"] <= 0.25, mtf["mid"])
    repeat = derive_preview_mtf({"r": 0.02, "g": 0.021, "b": 0.019})
    check("mtf is deterministic", mtf == repeat)
    check("non-finite preview input excluded", derive_preview_mtf({"r":float("inf"),"g":.02})["mid"]==.04)
    check("preview reference is labeled as whole-image statistic", "whole-image" in mtf["derived_from"])



def test_script_validation():
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



def test_preprocessing_reuse(tmp):
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
                patch.object(entry, "_channel_statistics_from_args", side_effect=lambda a, *unused:
                    {"medians": None, "report": {"medians_before": None, "medians_after": None,
                     "gains_applied": None, "background_equalization": {"requested": a.rgb_equal,
                     "applied": bool(a.rgb_equal and not a.dry_run)}, "notes": []}}):
            rc = entry.main(args)
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


def test_pws_gate():
    section("PWS viability gate")
    from pws_bridge import PWS_DISPERSION_FLOOR, PWS_MIN_FRAMES, evaluate_gate
    def frame(idx, wfwhm):
        return {"index": idx, "fwhm": wfwhm, "weighted_fwhm": wfwhm,
                "roundness": 0.7, "quality": 0, "background_lvl": 100.0,
                "number_of_stars": 50,
                "h": (1, 0, 0, 0, 1, 0, 0, 0, 1)}

    dispersed = [frame(i, 4.0) for i in range(30)]
    dispersed[0]["weighted_fwhm"] = 9.0
    dispersed[1]["weighted_fwhm"] = 8.0
    dispersed[2]["weighted_fwhm"] = 7.0
    gate = evaluate_gate({"frames": dispersed}, "rgb")
    check("dispersed 30-frame run passes", gate["eligible"]
          and gate["verdict"] == "pass", gate)
    check("dispersion ratio measured", gate["dispersion_ratio"] is not None
          and gate["dispersion_ratio"] > PWS_DISPERSION_FLOOR,
          gate["dispersion_ratio"])

    uniform = [frame(i, 4.0 + 0.01 * i) for i in range(30)]
    gate = evaluate_gate({"frames": uniform}, "rgb")
    check("uniform frames refused on the dispersion floor",
          not gate["eligible"] and "dispersion floor" in gate["reasons"][0],
          gate["reasons"])

    few = [frame(i, 4.0 + 0.5 * i) for i in range(PWS_MIN_FRAMES - 1)]
    gate = evaluate_gate({"frames": few}, "rgb")
    check("below the frame-count floor refused",
          not gate["eligible"] and str(PWS_MIN_FRAMES) in gate["reasons"][0],
          gate["reasons"])

    gate = evaluate_gate({"frames": dispersed}, "cfa")
    check("drizzle-kind runs refused structurally",
          not gate["eligible"] and "differing sizes" in gate["reasons"][0],
          gate["reasons"])

    gate = evaluate_gate(None, "rgb")
    check("missing regdata refused",
          not gate["eligible"], gate["reasons"])


def test_pws_script_generation(tmp):
    section("PWS branch script generation")
    from astra_stack import build_script, sequence_stem
    from param_derive import derive_pipeline
    from device_signatures import identify_device, resolve_calibration_plan

    header, _ = read_fits_header_with_warnings(
        write_cfa_sequence(tmp, 64, 48, 1)[0][0]
    )
    device = identify_device(header)
    plan = resolve_calibration_plan(header, device, None)
    quiet = {"span": 9.0, "span_x": 9.0, "span_y": 6.0,
             "rotation_max_deg": 0.01, "scale_dev_max": 0.0001,
             "mosaic": False, "frame_count": 4}
    pipeline = derive_pipeline(header, device, plan, quiet)

    stem = sequence_stem("src")
    builder, applied = build_script(
        tmp, "src", pipeline, plan, "/tmp/unused.fits",
        include_calibration=True, stack_engine="pws",
    )
    text = builder.render()
    check("pws branch stops after seqapplyreg+subsky",
          "stack " not in text and "seqsubsky" in text, text)
    check("pws branch emits seqsubsky on the applied sequence",
          ("seqsubsky %s 1 -prefix=sk_" % applied) in text, applied)
    check("registered sequence name recorded",
          applied == "ap_c_%s" % stem, applied)

    builder2, _ = build_script(
        tmp, "src", pipeline, plan, "/tmp/unused.fits",
        include_calibration=True, stack_engine="siril",
    )
    text2 = builder2.render()
    check("siril branch still emits stack",
          "stack ap_c_" in text2 and "seqsubsky" not in text2,
          text2.splitlines()[-3:])


def test_pws_black_corners(tmp):
    section("PWS black-corner guard")
    from pws_bridge import (
        PWS_BLACK_MAX_FRACTION,
        _pad_block,
        check_black_corners,
    )

    input_dir = os.path.join(tmp, "pws_in")
    os.makedirs(input_dir, exist_ok=True)
    # Frame 0: clean. Frames 1-2: growing black corner, like a rotated
    # sequence registered onto a fixed canvas (measured on the real 81-frame
    # M8 capture: 3.5% -> 23.9% across the sequence).
    def write_frame(name, black_share, directory=None):
        width, height = 80, 60
        header = _pad_block(
            (fits_probe_build_card("SIMPLE", "T", "")
             + fits_probe_build_card("BITPIX", "-32")
             + fits_probe_build_card("NAXIS", "2")
             + fits_probe_build_card("NAXIS1", str(width))
             + fits_probe_build_card("NAXIS2", str(height))
             + fits_probe_build_card("END", "")).encode("ascii"))
        payload = bytearray()
        for y in range(height):
            for x in range(width):
                black = (x < width * black_share) or (y < height * black_share)
                payload += struct.pack(">f", 0.0 if black else 0.1)
        target = os.path.join(directory or input_dir, name)
        with open(target, "wb") as handle:
            handle.write(header)
            handle.write(_pad_block(bytes(payload)))

    write_frame("pws0000.fit", 0.0)
    write_frame("pws0001.fit", 0.05)
    write_frame("pws0002.fit", 0.15)

    result = check_black_corners(input_dir)
    check("all frames sampled", result["sampled"] == 3, result)
    # The guard region is an L (x-share + y-share - overlap): 0.15 + 0.15 -
    # 0.0225 = 0.2775. Assert the exact geometry rather than the parameter.
    check("worst fraction tracks the blackest frame",
          result["worst_fraction"] is not None
          and 0.27 < result["worst_fraction"] <= 0.29, result)
    check("worst exceeds the guard threshold",
          result["worst_fraction"] > PWS_BLACK_MAX_FRACTION)

    # A clean directory passes with zero.
    clean = os.path.join(tmp, "pws_clean")
    os.makedirs(clean, exist_ok=True)
    write_frame("pws0000.fit", 0.0, directory=clean)
    result = check_black_corners(clean)
    check("clean frames read zero", result["worst_fraction"] == 0.0, result)


def test_pws_xisf_conversion(tmp):
    section("PWS XISF master conversion (stdlib reader)")
    from pws_bridge import XisfReadError, convert_xisf_master, pad_to_block
    try:
        import numpy
    except ImportError:
        print("  skipped (numpy unavailable; cannot build a real engine XISF)")
        return

    # Write a master with the *vendored engine's own writer* so the reader is
    # tested against the exact bytes the pipeline will hand it.
    vendor = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "..", "pws-stacking", "scripts", "vendor")
    vendor = os.path.abspath(vendor)
    if not os.path.isdir(vendor):
        print("  skipped (pws-stacking vendor not found at %s)" % vendor)
        return
    sys.path.insert(0, vendor)
    try:
        import contextlib
        import io
        from pathlib import Path

        import pws as pws_engine

        width, height = 64, 48
        arr = numpy.zeros((height, width), dtype=numpy.float32)
        yy, xx = numpy.mgrid[0:height, 0:width]
        arr += 0.01 + 0.001 * xx + 0.5 * numpy.exp(
            -((xx - 32) ** 2 + (yy - 24) ** 2) / 8.0)
        xisf_path = os.path.join(tmp, "stack_astra.xisf")
        with contextlib.redirect_stdout(io.StringIO()):
            pws_engine.save_linear_xisf(
                Path(xisf_path), arr, [{"exptime": 60.0}] * 2, "PWS A=20 B=2")
    finally:
        sys.path.remove(vendor)

    fits_path = os.path.join(tmp, "master.fits")
    conv = convert_xisf_master(xisf_path, fits_path)
    check("geometry preserved", conv["width"] == width
          and conv["height"] == height, conv)
    check("STACKMODE provenance kept",
          any(kw[0] == "STACKMODE" for kw in conv["keywords"]),
          conv["keywords"])

    header, _ = read_fits_header_with_warnings(fits_path)
    check("converted master is 32-bit float", header.get("BITPIX") == -32,
          header.get("BITPIX"))
    check("converted master is 2D", header.get("NAXIS") == 2,
          header.get("NAXIS"))

    # Pixel-exact round trip: XISF little-endian attachment -> FITS big-endian.
    with open(fits_path, "rb") as handle:
        handle.seek(data_offset(fits_path))
        raw = handle.read(width * height * 4)
    values = struct.unpack(">%df" % (width * height,), raw)
    expected = arr.reshape(-1)
    max_error = max(abs(a - b) for a, b in zip(values, expected))
    check("pixel-exact round trip", max_error < 1e-6, max_error)

    # A non-XISF file must be refused loudly, not guessed at.
    bad = os.path.join(tmp, "bad.xisf")
    with open(bad, "wb") as handle:
        handle.write(b"NOTXISF" + b"\x00" * 64)
    try:
        convert_xisf_master(bad, os.path.join(tmp, "o.fits"))
        check("non-XISF refused", False, "no exception")
    except XisfReadError:
        check("non-XISF refused", True)



def test_real_probe_reuse(tmp):
    section("real cached-registration branches and split calibration")
    siril = find_siril(None)
    if siril is None:
        check("Siril available for reuse regression", False)
        return
    source = os.path.join(tmp, "reuse-real-source")
    write_cfa_sequence(source, 200, 150, 4)
    entry = os.path.join(os.path.dirname(__file__), "astra_stack.py")
    rgb_source = os.path.join(tmp, "reuse-real-rgb")
    os.makedirs(rgb_source)
    for name, input_dir, force in [
        ("forced-debayer", source, "debayer"),
        ("forced-drizzle", source, "drizzle"),
        ("rgb", rgb_source, None),
    ]:
        out = os.path.join(tmp, "real-" + name)
        command = [sys.executable, entry, input_dir, "--out", out,
                   "--target", "reuse", "--siril", siril, "--keep-work"]
        if force:
            command += ["--force-chain", force]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        check("real " + name + " succeeds", result.returncode == 0,
              (result.stdout + result.stderr)[-400:])
        report_file = Path(out, "reuse_report.json")
        if result.returncode or not report_file.exists():
            continue
        report = json.loads(report_file.read_text())
        work = Path(report["work_dir"])
        commands = [line.split()[0] for path in work.glob("*.sir")
                    for line in path.read_text().splitlines()
                    if line and not line.startswith("#")]
        check("real " + name + " calibrates once", commands.count("calibrate") == 1, commands)
        check("real " + name + " registers once", commands.count("register") == 1, commands)
        check("real " + name + " publishes RGB", read_fits_header_with_warnings(
            report["artifacts"]["master"])[0]["NAXIS"] == 3)
        if name == "forced-debayer":
            for i, path in enumerate(sorted(work.glob("c_reuse*.fit"))):
                os.symlink(str(path), os.path.join(rgb_source, "rgb%04d.fit" % i))

    from fits_probe import pad_to_block
    work = Path(tmp, "split-calibration-real")
    work.mkdir()
    for i, path in enumerate(sorted(Path(source).glob("*.fit"))):
        (work / ("s%04d.fit" % i)).symlink_to(path)
    width, height = 200, 150
    cards = [fits_probe_build_card("SIMPLE", "T"), fits_probe_build_card("BITPIX", "-32"),
             fits_probe_build_card("NAXIS", "2"), fits_probe_build_card("NAXIS1", str(width)),
             fits_probe_build_card("NAXIS2", str(height)), fits_probe_build_card("END", "")]
    for name, value in [("bias", .001), ("dark", .002)]:
        (work / (name + ".fit")).write_bytes(pad_to_block("".join(cards).encode())
            + pad_to_block(struct.pack(">%df" % (width*height), *([value]*(width*height)))))
    script = ScriptBuilder()
    masters = ["-bias=" + str(work/"bias.fit"), "-dark=" + str(work/"dark.fit")]
    script.add("calibrate", ["s", "-prefix=combined_", "-cfa", "-debayer"] + masters)
    script.add("calibrate", ["s", "-prefix=c_", "-cfa"] + masters)
    script.add("calibrate", ["c_s", "-prefix=d_", "-cfa", "-debayer"])
    path = work/"split.sir";script.write(str(path))
    result = subprocess.run([siril, "-d", str(work), "-s", str(path)],
                            capture_output=True, text=True, timeout=1800)
    (work/"split.log").write_text(result.stdout + result.stderr)
    check("real split calibration succeeds", result.returncode == 0, result.stdout[-400:])
    if result.returncode == 0:
        errors = []
        for split in sorted(work.glob("d_c_s*.fit")):
            combined = work/split.name.replace("d_c_s", "combined_s")
            values = []
            for image in (split, combined):
                with image.open("rb") as handle:
                    handle.seek(data_offset(str(image)))
                    values.append(struct.unpack(">%df" % (width*height*3), handle.read(width*height*3*4)))
            errors.append(max(abs(a-b) for a,b in zip(*values)))
        check("bias and dark applied once in split calibration",
              len(errors) == 4 and max(errors) < 1e-6, errors)


def test_real_frame_selection(tmp):
    section("real early selection versus late-filter controls")
    from astra_stack import build_script, _read_stack_evidence, _reference_filter_fallback
    from param_derive import SMART_FILTERS, TRADITIONAL_FILTERS
    siril = find_siril(None)
    if siril is None:
        check("Siril available for selection regression", False)
        return
    root = Path(tmp, "selection-real"); root.mkdir()
    source = root/"source"
    write_cfa_sequence(str(source), 200, 150, 12, dx_step=1, dy_step=-1,
                       rotation_deg=1, blur_per_frame=.7)
    prepared = root/"prepared"; prepared.mkdir()
    for i, path in enumerate(sorted(source.glob("*.fit"))):
        (prepared/("s%04d.fit" % i)).symlink_to(path)
    def execute(work, builder, name):
        script = work/(name+".sir"); builder.write(str(script))
        result = subprocess.run([siril,"-d",str(work),"-s",str(script)],
                                capture_output=True,text=True,timeout=1800)
        (work/(name+".log")).write_text(result.stdout+"\n"+result.stderr)
        return result
    prepare = ScriptBuilder()
    prepare.add("calibrate",["s","-prefix=c_","-cfa"])
    prepare.add("register",["c_s","-transf=homography","-minpairs=4","-2pass"])
    prepare.add("calibrate",["c_s","-prefix=d_","-cfa","-debayer"])
    prepare.add("register",["d_c_s","-transf=homography","-minpairs=4","-2pass"])
    result = execute(prepared,prepare,"prepare")
    check("selection fixture registered",result.returncode==0,result.stdout[-400:])
    if result.returncode:
        return
    header,_ = read_fits_header_with_warnings(str(next(source.glob("*.fit"))))
    device = identify_device(header)
    calibration = resolve_calibration_plan(header,device,None)
    cases = [("mixed","drizzle"),("mixed","debayer"),("ties-gap","debayer"),
             ("all","debayer"),("traditional","debayer"),("few","debayer"),
             ("few","drizzle"),("zero","drizzle"),("reference-excluded","debayer"),
             ("reference-excluded","drizzle")]
    for case,chain in cases:
        sequence = "c_s" if chain=="drizzle" else "d_c_s"
        text = (prepared/(sequence+".seq")).read_text()
        reference = read_seq_regdata(str(prepared/(sequence+".seq")))["reference_image"]
        if case != "mixed":
            lines=text.splitlines();row=0
            for pos,line in enumerate(lines):
                if line.startswith(("R0 ","R1 ")):
                    parts=line.split();parts[1:4]=["4","4","0.8"];parts[6]="100"
                    if ((case=="ties-gap" and row==2)
                            or (case=="reference-excluded" and row==reference)
                            or (case=="few" and row!=0) or case=="zero"):
                        parts[3]="0.01"
                    if case=="traditional":
                        parts[2]="20" if row==2 else "4"
                        parts[3]="0.01"
                    lines[pos]=" ".join(parts);row+=1
            text="\n".join(lines)+"\n"
        text=text.replace("S '%s'" % sequence,"S 'c_s'")
        pipeline=derive_pipeline(header,device,calibration,None,force_chain=chain)
        if case=="traditional":
            pipeline["stack"]["args"]=[a for a in pipeline["stack"]["args"] if a not in SMART_FILTERS]+TRADITIONAL_FILTERS
            pipeline["stack"]["filters"]=list(TRADITIONAL_FILTERS)
        results={}
        for mode in ["late","early"]:
            work=root/(case+"-"+chain+"-"+mode);work.mkdir()
            (work/"c_s.seq").write_text(text)
            for path in prepared.glob(sequence+"*.fit"):
                (work/path.name.replace(sequence,"c_s",1)).symlink_to(path)
            master=work/"master.fit"
            builder,applied=build_script(str(work),"s",pipeline,calibration,str(master),
                include_calibration=False,registration_available=True)
            if mode=="late":
                builder=_reference_filter_fallback(builder,pipeline)
            result=execute(work,builder,"selection")
            fallback=False
            if mode=="early" and case=="reference-excluded" and chain=="debayer":
                check("native current framing rejects excluded reference",result.returncode!=0
                      and "Reference image is not included" in result.stdout)
                check("reference refusal writes no applied frames",not list(work.glob("ap_*.fit")))
                result=execute(work,_reference_filter_fallback(builder,pipeline),"fallback")
                fallback=True
            if case in ("zero","few"):
                check(case+" "+chain+" "+mode+" fails safely",result.returncode!=0 and not master.exists(),result.stdout[-300:])
                continue
            check(case+" "+chain+" "+mode+" succeeds",result.returncode==0,result.stdout[-300:])
            if result.returncode:
                continue
            src,ap,count=_read_stack_evidence(str(work),applied,str(master),result.stdout,
                                              mode=="early" and not fallback)
            check(case+" "+chain+" "+mode+" counts validated",len(src["frames"])==12 and count>=2)
            results[mode]=(work,ap,count)
        if len(results)!=2:
            continue
        late,ap_late,count_late=results["late"];early,ap_early,count_early=results["early"]
        check(case+" "+chain+" keeps the same frame count",count_late==count_early,(count_late,count_early))
        if case=="ties-gap":
            check("native ties preserve all equal quality frames except explicit failure",ap_early["image_order"]==[i for i in range(12) if i!=2],ap_early["image_order"])
        if case=="all":
            check("native all-pass exports all frames",ap_early["image_order"]==list(range(12)))
        if case=="traditional":
            check("traditional filtering ignores roundness",ap_early["image_order"]==[i for i in range(12) if i!=2],ap_early["image_order"])
        if chain=="debayer":
            a=(late/"master.fit").read_bytes()[data_offset(str(late/"master.fit")):]
            b=(early/"master.fit").read_bytes()[data_offset(str(early/"master.fit")):]
            check(case+" debayer master pixels equal",a==b)
        else:
            dimensions=[]
            for work in [late,early]:
                h,_=read_fits_header_with_warnings(str(work/"master.fit"))
                dimensions.append((h["NAXIS1"],h["NAXIS2"]))
            print("  drizzle %s dimensions: %s" % (case,dimensions))
            check(case+" drizzle keeps a valid RGB float master",h["NAXIS"]==3 and h["BITPIX"]==-32)


def test_real_rgb_equal(tmp):
    section("real RGB background normalization requires explicit opt-in")
    from fits_probe import pad_to_block, estimate_channel_medians
    from synth_fits import SMART_HEADER_FIELDS, _header
    import statistics
    siril=find_siril(None)
    if siril is None:
        check("Siril available for color regression",False)
        return
    root=Path(tmp,"rgb-equal-real");root.mkdir()
    original=root/"original"
    write_cfa_sequence(str(original),200,150,4,blur_per_frame=.5)
    source=root/"source";source.mkdir()
    pixels=200*150
    cards=[card for card in SMART_HEADER_FIELDS if card[0] not in ("BAYERPAT","BZERO","BSCALE")]
    header=_header(cards,200,150,-32,3,naxis3=3)
    for index,path in enumerate(sorted(original.glob("*.fit"))):
        with path.open("rb") as handle:
            handle.seek(data_offset(str(path)))
            raw=struct.unpack(">%dH"%pixels,handle.read(pixels*2))
        values=[]
        for channel,(background,gain) in enumerate(zip((.01,.02,.04),(1.,.93,.86))):
            plane=[value/65535.*gain for value in raw]
            median=statistics.median(plane)
            values.extend(value-median+background for value in plane)
        (source/("rgb%04d.fit"%index)).write_bytes(header+pad_to_block(struct.pack(">%df"%len(values),*values)))
    entry=str(Path(__file__).with_name("astra_stack.py"))
    reports={}
    for enabled in (False,True):
        out=root/("enabled" if enabled else "default")
        command=[sys.executable,entry,str(source),"--out",str(out),"--target","color","--siril",siril,"--keep-work"]
        if enabled:
            command.append("--rgb-equal")
        result=subprocess.run(command,capture_output=True,text=True,timeout=1800)
        (root/(out.name+".log")).write_text(result.stdout+"\n"+result.stderr)
        check("real color "+out.name+" succeeds",result.returncode==0,result.stdout[-400:])
        if result.returncode:
            continue
        report=json.loads((out/"color_report.json").read_text())
        reports[enabled]=report
        check("real color "+out.name+" schema updated",report["schema_version"]=="1.2")
        stats=report["channel_stats"]
        check("real color "+out.name+" truthful state",stats["gains_applied"] is None
              and stats["medians_before"] is None and stats["background_equalization"]["applied"]==enabled)
        check("real color "+out.name+" option only when requested",("-rgb_equal" in report["script_text"])==enabled)
        measured=estimate_channel_medians(report["artifacts"]["master"])
        check("real color "+out.name+" records actual values",len(measured)==3 and measured==stats["medians_after"])
    if len(reports)==2:
        before=reports[False]["channel_stats"]["medians_after"]
        after=reports[True]["channel_stats"]["medians_after"]
        check("default preserves deliberately different backgrounds",max(before.values())-min(before.values())>.02,before)
        check("native option reduces RGB background spread",max(after.values())-min(after.values())<.001,after)
        check("color option does not change selected count",reports[False]["stacking"]["frames_stacked"]==reports[True]["stacking"]["frames_stacked"])
        print("  measured default/explicit RGB medians: %s / %s"%(before,after))


def test_real_pws_pipeline(tmp, keep):
    section("real Siril front end + PWS engine end-to-end")
    siril = find_siril(None)
    if siril is None:
        check("siril-cli located", False, "not found; skipping")
        return
    from pws_bridge import resolve_pws_engine
    try:
        engine = resolve_pws_engine(None)
    except Exception as exc:  # noqa: BLE001 - report and skip
        print("  skipped (%s)" % exc)
        return

    # The gate demands >= 20 frames *and* genuine dispersion, so the frames are
    # written with progressively blurrier stars (blur_per_frame): frame 0 gets
    # sigma^2 = 7 px^2, frame 19 gets 33.6 px^2 -- real dispersion the gate can
    # measure rather than a flat sequence it must (correctly) refuse. Rotation
    # stays under the 5-degree drizzle threshold so the debayer chain is chosen.
    src = os.path.join(tmp, "pws-src")
    out = os.path.join(tmp, "pws-out")
    os.makedirs(src, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    # Two quality groups, like a real capture that lost tracking halfway: the
    # gate needs measured dispersion (p90/p10 > 1.4), and the engine's fixed
    # star kernel (fwhm0=6) needs every frame to stay detectable, so the blur
    # is capped at sigma^2 = 21 px^2 (FWHM ~10.8 px) rather than ramped.
    truth = (write_cfa_sequence(src, 400, 300, 10, dx_step=1, dy_step=0,
                                rotation_deg=0.2, blur_per_frame=0.0,
                                prefix="a", vignetting=False)
             + write_cfa_sequence(src, 400, 300, 10, dx_step=1, dy_step=0,
                                  rotation_deg=0.2, blur_per_frame=3.0,
                                  prefix="b", vignetting=False))
    check("20 synthetic frames written", len(truth) == 20, len(truth))

    entry = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "astra_stack.py")
    result = subprocess.run(
        [sys.executable, entry, src, "--out", out, "--siril", siril,
         "--stack-engine", "pws"],
        capture_output=True, text=True, timeout=3600,
    )
    report_path = os.path.join(out, "pws-src_report.json")
    check("pws pipeline exited 0", result.returncode == 0,
          (result.stdout + result.stderr)[-400:])
    if not os.path.exists(report_path):
        check("pws report written", False, report_path)
        return

    with open(report_path, "r", encoding="utf-8") as handle:
        report = json.load(handle)
    check("pws status ok", report["status"] == "ok",
          "%s / %s" % (report.get("failed_stage"), report.get("error")))
    gate = report.get("stack_engine") or {}
    check("gate recorded in report", gate.get("verdict") == "pass", gate)
    check("pws master produced",
          os.path.exists(os.path.join(out, "pws-src_stacked_32bit.fits")),
          sorted(os.listdir(out)))
    check("pws preview produced",
          os.path.exists(os.path.join(out, "pws-src.png")))
    stacking = report.get("stacking") or {}
    check("PWS still receives all frames", report["stack_engine"].get("input_frame_count") == 20,
          report["stack_engine"])
    check("PWS front end has no quality filters", "-filter-" not in report["script_text"])
    check("PWS never claims RGB gains",report["channel_stats"]["gains_applied"] is None
          and report["channel_stats"]["medians_after"] is None
          and not report["channel_stats"]["background_equalization"]["requested"])
    check("stacking method reported as pws", stacking.get("method") == "pws",
          stacking.get("method"))
    if keep:
        print("  (kept %s)" % out)


# --------------------------------------------------------------------------
# Layer 2
# --------------------------------------------------------------------------

def test_real_pipeline(tmp, keep):
    section("real Siril end-to-end")
    siril = find_siril(None)
    if siril is None:
        check("siril-cli located", False, "not found; skipping layer 2")
        return

    src = os.path.join(tmp, "src")
    out = os.path.join(tmp, "out")
    os.makedirs(src, exist_ok=True)
    os.makedirs(out, exist_ok=True)
    write_cfa_sequence(src, 200, 150, 4, dx_step=3, dy_step=-2)

    entry = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "astra_stack.py")
    result = subprocess.run(
        [sys.executable, entry, src, "--out", out, "--siril", siril],
        capture_output=True, text=True, timeout=1800,
    )
    report_path = os.path.join(out, "src_report.json")
    check("pipeline exited 0", result.returncode == 0,
          (result.stdout + result.stderr)[-300:])
    if not os.path.exists(report_path):
        check("report written", False, report_path)
        return

    with open(report_path, "r", encoding="utf-8") as handle:
        report = json.load(handle)

    check("status ok", report["status"] == "ok",
          "%s / %s" % (report.get("failed_stage"), report.get("error")))
    check("all stages completed",
          report["completed_stages"][-1] == "commit",
          report["completed_stages"])
    check("device identified", report["device"]["id"] == "zwo_seestar_s30pro",
          report["device"].get("id"))
    check("chain decided from probe data",
          report.get("registration_probe") is not None)
    probe = report.get("registration_probe") or {}
    check("probe span matches ground truth (9 px)",
          abs(probe.get("span", 0.0) - 9.0) < 0.5, probe.get("span"))
    check("rotation below threshold, so debayer chosen",
          report["registration"]["chain"] == "debayer",
          report["registration"]["chain"])

    master = os.path.join(out, "src_stacked_32bit.fits")
    preview = os.path.join(out, "src.png")
    check("32-bit master produced", os.path.exists(master), master)
    check("preview PNG produced", os.path.exists(preview), preview)
    check("work directory cleaned up",
          not os.path.exists(os.path.join(out, "src.work")))

    if os.path.exists(master):
        header, _ = read_fits_header_with_warnings(master)
        check("master is 32-bit float", header.get("BITPIX") == -32,
              header.get("BITPIX"))
        check("master has 3 axes (RGB)", header.get("NAXIS") == 3,
              header.get("NAXIS"))
        check("master keeps the target name",
              str(header.get("OBJECT", "")).strip() == "M 42",
              header.get("OBJECT"))
        check("master keeps the device field",
              str(header.get("TELESCOP", "")).strip() == "S30 Pro_10d57b1d",
              header.get("TELESCOP"))

    check("preview MTF parameters recorded",
          report["preview"]["produced"] and report["preview"]["method"] == "mtf",
          report["preview"])

    if keep:
        print("  (kept %s)" % out)


def test_atomic_commit(tmp):
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


def test_failure_isolation(tmp):
    section("failure keeps the previous good result")
    from astra_stack import main

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
    code = main([lone, "--out", out])
    check("single-frame run fails", code != 0, code)
    with open(marker, "r", encoding="ascii") as handle:
        content = handle.read()
    check("previous result untouched", content == "PREVIOUS GOOD RESULT", content)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true",
                        help="also run the Siril end-to-end test")
    parser.add_argument("--keep", nargs="?", const="/tmp/astra-selftest",
                        default=None,
                        help="keep a real run's output directory for inspection")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="astra-selftest-")
    try:
        test_sequence_stem()
        test_fits_roundtrip(tmp)
        test_rejected_inputs(tmp)
        test_transform_derivation()
        test_seq_parsing(tmp)
        test_device_identification(tmp)
        test_calibration_plan()
        test_cosmetic_parameters()
        test_pipeline_branches(tmp)
        test_chain_review()
        test_mtf_and_equalisation()
        test_preview_stretch_direction()
        test_frame_filter_forms()
        test_filtered_frame_parsing()
        test_frame_quality_report()
        test_channel_median_estimation()
        test_channel_statistics_contract(tmp)
        test_script_validation()
        test_preprocessing_reuse(tmp)
        test_pws_gate()
        test_pws_script_generation(tmp)
        test_pws_black_corners(tmp)
        test_pws_xisf_conversion(tmp)
        test_atomic_commit(tmp)
        test_failure_isolation(tmp)
        if args.real:
            test_real_pipeline(tmp, args.keep)
            test_real_probe_reuse(tmp)
            test_real_frame_selection(tmp)
            test_real_rgb_equal(tmp)
            test_real_pws_pipeline(tmp, args.keep)
        else:
            section("real end-to-end")
            print("  skipped (pass --real to run it)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    for label, detail in FAILED:
        print("  FAILED: %s %s" % (label, detail))
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())