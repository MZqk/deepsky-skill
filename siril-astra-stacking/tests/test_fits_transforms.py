"""FITS headers, sequences, transforms, and image statistics tests."""
from __future__ import annotations

import sys
from pathlib import Path

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / 'scripts', _SKILL / 'tests'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import os
from pathlib import Path
import struct
import tempfile
import unittest

from astra_test_case import AstraTestCase, section
from astra_stack import sequence_stem
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
from param_derive import derive_preview_mtf
from synth_fits import write_cfa_sequence, write_rgb_sequence


class TestFitsTransforms(AstraTestCase):
    def test_preview_stretch_direction(self):
        check = self.check
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

    def test_frame_filter_forms(self):
        check = self.check
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

    def test_filtered_frame_parsing(self):
        check = self.check
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

    def test_frame_quality_report(self):
        check = self.check
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

    def test_channel_median_estimation(self):
        check = self.check
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
    
        # A mono master is NAXIS=2: one plane, and its median must still be
        # measured, otherwise the preview silently falls back to a hard-coded
        # midpoint that has nothing to do with the data.
        mono_cards = [
            build_card("SIMPLE", "T"), build_card("BITPIX", "-32"),
            build_card("NAXIS", "2"), build_card("NAXIS1", str(width)),
            build_card("NAXIS2", str(height)), build_card("BAYERPAT", "RGGB"),
            build_card("END", ""),
        ]
        mono_header = pad_to_block("".join(mono_cards).encode("ascii"))
        mono_payload = bytearray()
        for _y in range(height):
            for _x in range(width):
                mono_payload += struct_mod.pack(">f", 0.017)
        with tempfile.NamedTemporaryFile(suffix=".fits", delete=False) as handle:
            handle.write(mono_header)
            handle.write(pad_to_block(bytes(mono_payload)))
            mono_path = handle.name
        try:
            mono_medians = estimate_channel_medians(mono_path)
            check("mono master yields a single median", len(mono_medians) == 1,
                  mono_medians)
            check("mono median is recovered",
                  abs(mono_medians.get("m", -1) - 0.017) < 1e-6, mono_medians)
        finally:
            os.unlink(mono_path)

    def test_channel_statistics_contract(self):
        tmp = self.tmp
        check = self.check
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

    def test_sequence_stem(self):
        check = self.check
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

    def test_work_dir_sequence_extension(self):
        tmp = self.tmp
        check = self.check
        """Siril finds a sequence by its configured extension, not by content.
    
        Measured on siril-cli 1.4.4: a work directory holding ``<stem>NNNN.fits``
        symlinks aborts with "No sequence found, verify working directory or
        change FITS extension in settings (current is .fit)" before calibration
        runs -- even though Siril reads the same bytes happily when the file is
        named explicitly. The staged name is therefore fixed, not inherited.
        """
        section("work directory sequence extension")
        from astra_stack import stage_workdir
    
        source = Path(tmp, "fits_suffix_src")
        source.mkdir(exist_ok=True)
        for index in range(2):
            (source / ("light_%d.fits" % index)).write_bytes(b"placeholder")
        work, _state = stage_workdir(str(source), tmp, "fitsuffix", force=True)
        staged = sorted(name for name in os.listdir(work)
                        if name.endswith((".fit", ".fits", ".fts")))
        check("every staged frame uses the .fit extension",
              bool(staged) and all(name.endswith(".fit") for name in staged), staged)
        check("all source frames were staged", len(staged) == 2, staged)

    def test_fits_roundtrip(self):
        tmp = self.tmp
        check = self.check
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

    def test_rejected_inputs(self):
        tmp = self.tmp
        check = self.check
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

    def test_transform_derivation(self):
        check = self.check
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

    def test_seq_parsing(self):
        tmp = self.tmp
        check = self.check
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

    def test_mtf_and_equalisation(self):
        check = self.check
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

if __name__ == "__main__":
    unittest.main()
