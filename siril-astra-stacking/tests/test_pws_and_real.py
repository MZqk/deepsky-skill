"""Photometric White Subtraction (PWS) and Siril end-to-end real tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import statistics
import tempfile
import unittest

_SKILL = Path(__file__).resolve().parents[1]
for _p in (_SKILL / 'scripts', _SKILL / 'tests'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from astra_test_case import AstraTestCase, section
from astra_stack import find_siril
from param_derive import derive_pipeline
from device_signatures import identify_device, resolve_calibration_plan
from fits_probe import (
    pad_to_block,
    build_card as fits_probe_build_card,
    data_offset,
    read_fits_header_with_warnings,
    read_seq_regdata,
)
from siril_script_gen import ScriptBuilder
from synth_fits import write_cfa_sequence

REAL = "--real" in sys.argv
if REAL:
    sys.argv.remove("--real")


class TestPwsAndReal(AstraTestCase):
    def test_pws_gate(self):
        check = self.check
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

    def test_pws_script_generation(self):
        tmp = self.tmp
        check = self.check
        section("PWS branch script generation")
        from astra_stack import build_script, sequence_stem
        from param_derive import derive_pipeline
        from device_signatures import identify_device, resolve_calibration_plan, resolve_calibration_plan
    
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

    def test_pws_black_corners(self):
        tmp = self.tmp
        check = self.check
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

    def test_pws_xisf_conversion(self):
        tmp = self.tmp
        check = self.check
        section("PWS XISF master conversion (stdlib reader)")
        from pws_bridge import XisfReadError, convert_xisf_master, pad_to_block
        try:
            import numpy
        except ImportError:
            print("  skipped (numpy unavailable; cannot build a real engine XISF)")
            return
    
        # Write a master with the *vendored engine's own writer* so the reader is
        # tested against the exact bytes the pipeline will hand it.
        vendor = str(_SKILL.parent / "pws-stacking" / "scripts" / "vendor")
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

    @unittest.skipUnless(REAL, "pass --real for native Siril fixtures")
    def test_real_probe_reuse(self):
        tmp = self.tmp
        check = self.check
        section("real cached-registration branches and split calibration")
        siril = find_siril(None)
        if siril is None:
            check("Siril available for reuse regression", False)
            return
        source = os.path.join(tmp, "reuse-real-source")
        write_cfa_sequence(source, 200, 150, 4)
        entry = str(_SKILL / "scripts" / "astra_stack.py")
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

    @unittest.skipUnless(REAL, "pass --real for native Siril fixtures")
    def test_real_frame_selection(self):
        tmp = self.tmp
        check = self.check
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

    @unittest.skipUnless(REAL, "pass --real for native Siril fixtures")
    def test_real_rgb_equal(self):
        tmp = self.tmp
        check = self.check
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
        entry = str(_SKILL / "scripts" / "astra_stack.py")
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
            check("real color "+out.name+" schema updated",report["schema_version"]=="1.3")
            stats=report["channel_stats"]
            check("real color "+out.name+" publishes three planes",report["stacking"]["output_naxis"]==3)
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

    @unittest.skipUnless(REAL, "pass --real for native Siril fixtures")
    def test_real_pws_pipeline(self):
        tmp = self.tmp
        keep = getattr(self, "keep", None)
        check = self.check
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
    
        entry = str(_SKILL / "scripts" / "astra_stack.py")
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
              and (report["channel_stats"]["medians_after"] is None or "r" not in report["channel_stats"]["medians_after"])
              and not report["channel_stats"]["background_equalization"]["requested"])
        check("stacking method reported as pws", stacking.get("method") == "pws",
              stacking.get("method"))
        if keep:
            print("  (kept %s)" % out)

    @unittest.skipUnless(REAL, "pass --real for native Siril fixtures")
    def test_real_pipeline(self):
        tmp = self.tmp
        keep = getattr(self, "keep", None)
        check = self.check
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
    
        entry = str(_SKILL / "scripts" / "astra_stack.py")
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

if __name__ == "__main__":
    unittest.main()
