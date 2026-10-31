"""Stacking module for siril-moon-stacking.

Handles frame probing, drizzle supersampling heuristic decisions,
Siril sequence alignment & stacking command generation, retry/degradation logic,
and master pixel-scale metadata verification.
"""

from __future__ import annotations

from pathlib import Path
import re
import numpy as np

from moon_common import (
    log,
    die,
    load_json,
    dump_json,
    run_siril_script,
    fits_header_info,
    _hdr_source_label,
    _hdr_positive_float,
    _disk_frame_budget,
    _seq_frame_paths,
)
from moon_optics import (
    _infer_optical_parameters,
    _measure_limb_psf_fwhm,
)


DRIZZLE_KERNELS = ("point", "turbo", "square", "gaussian", "lanczos2", "lanczos3")
DRIZZLE_AIRY_THRESHOLD_PX = 1.5
DRIZZLE_PSF_THRESHOLD_PX = 2.0
PIXEL_SIZE_KEYS = ("XPIXSZ", "YPIXSZ", "PIXSIZE", "PIXSIZE1", "PIXEL_SZ", "PIXELSIZE")
APERTURE_KEYS = ("APERTUR", "DIAMETER", "APERTURE")
FOCAL_KEYS = ("FOCALLEN", "FOCAL_LENGTH", "FOCAL")

WEIGHT_FAILURE_MARKERS = (
    "MAD is null",
    "Statistics cannot be computed",
    "Normalization failed",
    "cannot be normalized",
)


def _read_first_frame_header(work: Path, seq_name: str):
    """FITS header of the first frame of a sequence (memmap=False per repo convention)."""
    from astropy.io import fits

    frames = _seq_frame_paths(work, seq_name)
    if not frames:
        return None
    try:
        with fits.open(frames[0], memmap=False) as hdul:
            return hdul[0].header.copy()
    except Exception:
        return None


def _hdr_positive_float(hdr, keys) -> float | None:
    """First strictly-positive float among `keys` in a FITS header, else None."""
    if hdr is None:
        return None
    for k in keys:
        v = hdr.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def _probe_frame_for_drizzle(work: Path, seq_name: str):
    """Pick the single frame used to measure optics and sampling adequacy.

    Prefers the frame `register` picked as the sharpest reference (ranking.json),
    because limb fitting and edge-spread measurement are far cleaner on a sharp
    frame. Falls back to the first frame of the sequence.
    """
    from astropy.io import fits

    frames = _seq_frame_paths(work, seq_name)
    if not frames:
        return None

    chosen = frames[0]
    origin = "first frame"
    ranking = work / "ranking.json"
    if ranking.exists():
        try:
            ref_idx = int(load_json(ranking).get("reference_index") or 0)
            if ref_idx > 0:
                cand = work / f"{seq_name}{ref_idx:05d}.fit"
                if cand.exists():
                    chosen = cand
                    origin = f"register reference frame #{ref_idx}"
        except Exception:
            pass

    try:
        with fits.open(chosen, memmap=False) as hdul:
            hdr = hdul[0].header.copy()
            data = hdul[0].data
    except Exception:
        return None
    if data is None:
        return None

    if data.ndim == 3:
        lum = (0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]).astype(np.float32)
    else:
        lum = np.asarray(data, dtype=np.float32)
    return hdr, lum, origin


def _decide_drizzle(args, work: Path, seq_name: str) -> dict:
    """Decide whether to supersample the stack with Siril's HST drizzle."""
    mode = str(getattr(args, "drizzle", "auto") or "auto").strip().lower()
    kernel = str(getattr(args, "drizzle_kernel", "square") or "square").strip().lower()
    if kernel not in DRIZZLE_KERNELS:
        log(f"drizzle: unknown kernel '{kernel}' -> falling back to 'square'")
        kernel = "square"
    user_pixfrac = getattr(args, "drizzle_pixfrac", None)

    def pack(enabled: bool, scale: float, reason: str, **extra) -> dict:
        scale = float(scale)
        if user_pixfrac is not None:
            try:
                pf = float(user_pixfrac)
            except (TypeError, ValueError):
                pf = 1.0 / scale
        else:
            pf = 1.0 / scale if scale > 0 else 1.0
        pf = min(max(pf, 0.0), 1.0)
        k = kernel
        if k.startswith("lanczos") and not (abs(scale - 1.0) < 1e-9 and abs(pf - 1.0) < 1e-9):
            log(f"drizzle: kernel '{k}' requires scale == pixfrac == 1.0 "
                f"(got scale={scale:g}, pixfrac={pf:g}); falling back to 'square'")
            k = "square"
        info = {
            "requested": mode,
            "enabled": bool(enabled),
            "scale": scale,
            "pixfrac": round(pf, 4),
            "kernel": k,
            "reason": reason,
        }
        info.update(extra)
        return info

    if mode == "off":
        return pack(False, 1.0, "user_off")

    forced = None
    if mode in ("2", "3"):
        forced = float(mode)

    if getattr(args, "mosaic_mode", "disc") == "tile" and forced is None:
        return pack(False, 1.0, "tile_mode_bypass",
                    hint="pass --drizzle 2 to force it (keep every tile at the same scale)")

    hdr = _read_first_frame_header(work, seq_name)
    focal = getattr(args, "focal", None) or _hdr_positive_float(hdr, FOCAL_KEYS)
    pixsz = getattr(args, "pixel_size", None) or _hdr_positive_float(hdr, PIXEL_SIZE_KEYS)
    aperture = getattr(args, "aperture", None) or _hdr_positive_float(hdr, APERTURE_KEYS)

    focal_source = (("user" if getattr(args, "focal", None)
                     else _hdr_source_label(hdr, "FOCALLEN", "FITS header")) if focal else None)
    pixel_source = (("user" if getattr(args, "pixel_size", None)
                     else _hdr_source_label(hdr, "XPIXSZ", "FITS header")) if pixsz else None)
    aperture_source = (("user" if getattr(args, "aperture", None)
                        else _hdr_source_label(hdr, "APERTURE", "FITS header")) if aperture else None)

    airy_threshold = float(getattr(args, "drizzle_airy_threshold", None) or DRIZZLE_AIRY_THRESHOLD_PX)
    psf_threshold = float(getattr(args, "drizzle_psf_threshold", None) or DRIZZLE_PSF_THRESHOLD_PX)

    if forced is not None:
        return pack(True, forced, "user_forced",
                    criterion="forced",
                    focal_len_mm=focal, focal_source=focal_source,
                    pixel_size_um=pixsz, aperture_mm=aperture,
                    aperture_source=aperture_source)

    probe = None
    probe_origin = None
    optics_probe = None
    psf_probe = None
    if not (focal and pixsz and aperture):
        probe = _probe_frame_for_drizzle(work, seq_name)
        if probe is None:
            log("drizzle auto: no FITS frame available to measure optics from")
        else:
            p_hdr, p_lum, probe_origin = probe
            try:
                optics_probe = _infer_optical_parameters(
                    p_hdr, p_lum, args, mosaic_mode="disc", px_scale=1.0)
            except Exception as exc:
                log(f"drizzle auto: optical inference on {probe_origin} failed: {exc}")
                optics_probe = None

            if optics_probe:
                px_used = str(optics_probe.get("pixel_size_source") or "")
                if not focal:
                    src = str(optics_probe.get("focal_source") or "")
                    if src.startswith("geometric inversion") and px_used.startswith("default"):
                        log("drizzle auto: geometric inversion would use a default 3.73um pixel "
                            "size, which mis-scales the focal length; ignoring it")
                    elif not src.startswith("default") and optics_probe.get("focal_length"):
                        focal = float(optics_probe["focal_length"])
                        focal_source = src
                if not pixsz and not px_used.startswith("default") and optics_probe.get("pixel_size"):
                    pixsz = float(optics_probe["pixel_size"])
                    pixel_source = px_used
                if not aperture:
                    src = str(optics_probe.get("aperture_source") or "")
                    if not src.startswith("default") and optics_probe.get("aperture"):
                        aperture = float(optics_probe["aperture"])
                        aperture_source = src

    if focal and pixsz and aperture:
        r_airy = (1.22 * 0.55 * float(focal)) / (float(aperture) * float(pixsz))
        enabled = r_airy < airy_threshold
        return pack(enabled, 2.0 if enabled else 1.0,
                    "airy_below_threshold" if enabled else "airy_at_or_above_threshold",
                    criterion="airy",
                    r_airy_px=round(r_airy, 3),
                    threshold_px=airy_threshold,
                    focal_len_mm=float(focal), focal_source=focal_source,
                    pixel_size_um=float(pixsz), pixel_size_source=pixel_source,
                    aperture_mm=float(aperture), aperture_source=aperture_source,
                    f_ratio=round(float(focal) / float(aperture), 2),
                    probe_frame=probe_origin)

    unavailable = [n for n, v in (("focal length", focal), ("pixel size", pixsz),
                                  ("aperture", aperture)) if not v]
    if probe is not None:
        circle = None
        disc = (optics_probe or {}).get("lunar_disc")
        if disc:
            circle = (disc["center"][0], disc["center"][1], disc["radius_px"])
        try:
            psf_probe = _measure_limb_psf_fwhm(probe[1], circle=circle, px_scale=1.0)
        except Exception as exc:
            log(f"drizzle auto: limb PSF measurement failed: {exc}")
            psf_probe = None

    if psf_probe:
        fwhm = float(psf_probe["fwhm_px"])
        enabled = fwhm < psf_threshold
        log(f"drizzle auto: {'/'.join(unavailable)} unavailable - falling back to the measured "
            f"PSF width (FWHM {fwhm:.2f}px vs threshold {psf_threshold:.2f}px, "
            f"measured on {probe_origin})")
        return pack(enabled, 2.0 if enabled else 1.0,
                    "psf_below_threshold" if enabled else "psf_at_or_above_threshold",
                    criterion="psf_fwhm",
                    psf_fwhm_px=fwhm,
                    psf_esf_width_px=psf_probe.get("esf_width_10_90_px"),
                    psf_possible_saturation=psf_probe.get("possible_saturation"),
                    limb_radius_px=psf_probe.get("limb_radius_px"),
                    limb_fit_residual_std=psf_probe.get("limb_fit_residual_std"),
                    threshold_px=psf_threshold,
                    focal_len_mm=focal, focal_source=focal_source,
                    pixel_size_um=pixsz, pixel_size_source=pixel_source,
                    aperture_mm=aperture, aperture_source=aperture_source,
                    probe_frame=probe_origin)

    log(f"drizzle auto: cannot establish sampling adequacy ({'/'.join(unavailable)} unavailable "
        f"and the lunar limb could not be measured) - drizzle disabled. Supply "
        f"--focal/--pixel-size/--aperture, or pass --drizzle 2 to force it")
    return pack(False, 1.0, "insufficient_evidence",
                criterion="none", missing=unavailable, probe_frame=probe_origin)


def _verify_master_pixel_scale(
    master: Path, drizzle: dict, work: Path, seq_name: str
) -> dict:
    """Verify (and if needed repair) the master's pixel scale after a drizzle stack."""
    from astropy.io import fits

    scale = float(drizzle.get("scale", 1.0) or 1.0)
    result = {"verified": False, "rewritten": {}, "scale": scale}

    src_hdr = _read_first_frame_header(work, seq_name)
    native_px = _hdr_positive_float(src_hdr, PIXEL_SIZE_KEYS)
    expected = (native_px / scale) if native_px else None

    try:
        with fits.open(master, memmap=False, mode="update") as hdul:
            h = hdul[0].header
            if expected:
                for k in PIXEL_SIZE_KEYS:
                    v = h.get(k)
                    if v is None:
                        continue
                    try:
                        fv = float(v)
                    except (TypeError, ValueError):
                        continue
                    if abs(fv - expected) > 0.02 * expected:
                        h[k] = round(expected, 6)
                        result["rewritten"][k] = [fv, round(expected, 6)]
                result["verified"] = not result["rewritten"]
            else:
                result["verified"] = None
                log("drizzle: no native pixel-size keyword found in the input frames; "
                    "cannot verify the master's pixel scale (postprocess will fall back "
                    "to --pixel-size divided by the drizzle scale)")

            h["DRIZZLE"] = (True, "HST drizzle supersampling applied")
            h["DRZSCALE"] = (scale, "drizzle output grid scale factor")
            h["DRZPIXFR"] = (float(drizzle.get("pixfrac", 1.0)), "drizzle droplet pixel fraction")
            h["DRZKRENL"] = (str(drizzle.get("kernel", "square")), "drizzle droplet kernel")
            if native_px:
                h["ORIGXPIX"] = (round(float(native_px), 6), "native pixel size before drizzle")
            h.add_history(
                f"moon_stack: drizzle scale={scale:g} pixfrac={drizzle.get('pixfrac')} "
                f"kernel={drizzle.get('kernel')}; XPIXSZ now {expected if expected else 'unset'}"
            )
            result["native_px"] = native_px
            result["effective_px"] = expected
    except Exception as exc:
        log(f"warning: could not verify master pixel scale after drizzle: {exc}")
        result["error"] = str(exc)

    if result["rewritten"]:
        log(f"drizzle: repaired master pixel-size keywords {result['rewritten']} "
            f"(Siril left them at the native scale)")
    return result


def _stack_counts(work, seq_name, output, requested):
    exported = re.findall(r"Total:\s*(\d+) failed,\s*(\d+) exported", output)
    stacked = re.findall(r"(\d+) images have been stacked", output)
    if not exported or not stacked:
        die("cannot confirm actual Siril frame counts; inspect the align/stack log before using this master")
    failures, count = map(int, exported[-1])
    actual = int(stacked[-1])
    if actual != count or actual <= 0 or actual > requested:
        die(f"Siril frame-count mismatch: requested={requested}, exported={count}, stacked={actual}")
    resampled = work / f"r_{seq_name}.seq"
    if resampled.exists():
        included = sum(line.startswith("I ") and line.split()[2] == "1" for line in resampled.read_text().splitlines())
        if included != actual:
            die(f"Siril output sequence includes {included} frames, but stack log reports {actual}")
    return {
        "selected_frames": requested,
        "exported_frames": count,
        "stacked_frames": actual,
        "resampling_failed_frames": failures,
        "dropped_frames": requested - actual,
    }


def cmd_stack(args) -> None:
    work = Path(args.work).expanduser().resolve()
    seq_name = args.seq
    seq_path = work / f"{seq_name}.seq"
    if not seq_path.exists():
        die(f"sequence file {seq_path} not found; run register first")

    logs_dir = work / "logs"
    master = work / args.out

    stack_method = getattr(args, "stack_method", "auto")
    is_8bit = False

    receipt_path = work / "import_receipt.json"
    if receipt_path.exists():
        try:
            info = load_json(receipt_path)
            if info.get("orig_bit_depth") == 8 or info.get("is_video"):
                is_8bit = True
        except Exception:
            pass

    if not is_8bit:
        first_frame = next(work.glob(f"{seq_name}*.fit"), None)
        if first_frame:
            try:
                h = fits_header_info(first_frame)
                if h.get("bitpix") == 8:
                    is_8bit = True
                else:
                    from astropy.io import fits
                    with fits.open(first_frame, memmap=False) as hdul:
                        if hdul[0].header.get("ORIG_BIT") == 8:
                            is_8bit = True
            except Exception:
                pass

    use_sum = (stack_method == "sum")
    if stack_method == "auto" and is_8bit:
        log("stacking policy: 8-bit source detected; using rejection mean stacking "
            "(rej w) instead of the legacy 'sum' path - sum normalises by the image "
            "maximum and performs no pixel rejection (pass --stack-method sum to force it)")

    weight_mode = str(getattr(args, "weight", "auto") or "auto").strip().lower()
    if weight_mode not in ("auto", "none", "noise"):
        log(f"stacking policy: unknown --weight '{weight_mode}' -> treating as 'auto'")
        weight_mode = "auto"
    want_weight = (not use_sum) and weight_mode in ("auto", "noise")
    if use_sum and weight_mode == "noise":
        log("stacking policy: 'sum' does not support -weight=; noise weighting ignored")

    def build_stack_cmd(with_weight: bool) -> str:
        if use_sum:
            return f"stack r_{seq_name} sum -filter-included -out={master.stem}"
        wflag = " -weight=noise" if with_weight else ""
        return (f"stack r_{seq_name} rej w {args.sigma[0]} {args.sigma[1]} "
                f"-norm={args.norm} -filter-included{wflag} -out={master.stem}")

    if use_sum:
        log("stacking policy: applying 'sum' stacking (forced by user) - "
            "no pixel rejection, no weighting")
    else:
        log(f"stacking policy: applying Winsorized rejection mean stacking "
            f"(rej w {args.sigma[0]} {args.sigma[1]} -norm={args.norm})"
            f"{' with noise weighting' if want_weight else ''} "
            f"({'forced by user' if stack_method == 'rej' else 'default for all source depths'})")

    mosaic_mode = getattr(args, "mosaic_mode", "disc")
    framing = getattr(args, "framing", None)
    if not framing:
        framing = "max" if mosaic_mode == "tile" else "min"
    maximize_flag = " -maximize" if framing == "max" else ""

    interp_raw = getattr(args, "interp", "cu")
    interp_map = {
        "linear": "li", "bilinear": "li", "li": "li",
        "cubic": "cu", "bicubic": "cu", "cu": "cu",
        "lanczos": "la", "lanczos4": "la", "la": "la",
        "none": "no", "no": "no"
    }
    interp = interp_map.get(str(interp_raw).lower(), "cu")

    drizzle = _decide_drizzle(args, work, seq_name)
    if drizzle["enabled"]:
        airy_note = (f", theoretical Airy radius {drizzle['r_airy_px']}px"
                     if drizzle.get("r_airy_px") else "")
        log(f"drizzle: ENABLED scale={drizzle['scale']:g} pixfrac={drizzle['pixfrac']:g} "
            f"kernel={drizzle['kernel']} (reason={drizzle['reason']}{airy_note}) - the output "
            f"grid is {drizzle['scale']:g}x finer, expect roughly "
            f"{drizzle['scale'] ** 2:.0f}x the pixels, file size and runtime")
    else:
        log(f"drizzle: disabled (reason={drizzle['reason']})")

    seq_text = seq_path.read_text()
    selected = [int(line.split()[1]) for line in seq_text.splitlines() if line.startswith("I ") and line.split()[2] == "1"]
    source_frames = _seq_frame_paths(work, seq_name)
    if source_frames:
        header = fits_header_info(source_frames[0])
        allowed, disk_budget = _disk_frame_budget(work, args, header["width"], header["height"], header["channels"], len(selected), scale=drizzle["scale"] if drizzle["enabled"] else 1, importing=False)
        if allowed < len(selected):
            ranking = load_json(work / "ranking.json") if (work / "ranking.json").exists() else {}
            ordered = sorted([r for r in ranking.get("frames", []) if r["index"] in selected], key=lambda r: r["sharpness"], reverse=True)
            selected = [r["index"] for r in ordered[:allowed]] if ordered else selected[:allowed]
            keep = set(selected)
            lines = seq_text.splitlines()
            for index, line in enumerate(lines):
                if line.startswith("I "):
                    lines[index] = f"I {line.split()[1]} {int(int(line.split()[1]) in keep)}"
                elif line.startswith("S "):
                    fields = line.split()
                    fields[4] = str(allowed)
                    lines[index] = " ".join(fields)
            seq_path.write_text("\n".join(lines) + "\n")
            log(f"disk budget reduced resampling selection to {allowed} frames")
    else:
        disk_budget = None

    reg_drizzle = (f"seqapplyreg {seq_name} -framing={framing} -scale={drizzle['scale']:g} "
                   f"-drizzle -pixfrac={drizzle['pixfrac']:g} -kernel={drizzle['kernel']} -filter-incl")
    reg_plain = f"seqapplyreg {seq_name} -framing={framing} -interp={interp} -filter-incl"

    ladder: list[dict] = [{"drizzle": drizzle["enabled"], "weight": want_weight}]
    if want_weight:
        ladder.append({"drizzle": drizzle["enabled"], "weight": False})
    if drizzle["enabled"] and not drizzle.get("forced"):
        ladder.append({"drizzle": False, "weight": want_weight})
        if want_weight:
            ladder.append({"drizzle": False, "weight": False})
    seen_keys = set()
    remaining: list[dict] = []
    for step in ladder:
        key = (step["drizzle"], step["weight"])
        if key not in seen_keys:
            seen_keys.add(key)
            remaining.append(step)

    receipt = None
    chosen = None
    attempt = 0
    while remaining:
        step = remaining.pop(0)
        reg_line = reg_drizzle if step["drizzle"] else reg_plain
        stk_line = build_stack_cmd(step["weight"])
        log_tag = ("drizzle" if step["drizzle"] else "interp") + \
                  ("+noise-weight" if step["weight"] else "")
        round_tag = f"_round{args.feedback_round}" if hasattr(args, "feedback_round") else ""
        log_path = logs_dir / (f"02_align_stack{round_tag}.log" if attempt == 0
                               else f"02_align_stack{round_tag}_retry{attempt}.log")
        lines = ["requires 1.4.4", reg_line, f"{stk_line}{maximize_flag}", "exit"]
        if attempt == 0:
            log(f"executing Siril registration & stack pipeline "
                f"(framing={framing}, mosaic_mode={mosaic_mode}, resampling={log_tag})...")
        else:
            log(f"retrying the stack with a reduced configuration ({log_tag})...")
        receipt = run_siril_script(args.siril, lines, work, log_path, args.timeout)
        attempt += 1
        if receipt["exit_code"] == 0 and master.exists():
            chosen = step
            break
        if remaining:
            log(f"stack attempt '{log_tag}' failed (exit {receipt['exit_code']}); "
                f"degrading and retrying - see {receipt['log']}")
            out = str(receipt.get("output", ""))
            if step["weight"] and any(m in out for m in WEIGHT_FAILURE_MARKERS):
                log("noise weighting is the likely culprit (per-frame background statistics "
                    "unavailable); skipping any further weighted attempts")
                remaining = [s for s in remaining if not s["weight"]]

    if chosen is None:
        die(f"stacking failed after {attempt} attempt(s); see "
            f"{receipt['log'] if receipt else logs_dir}")

    receipt.update(_stack_counts(work, seq_name, receipt["output"], len(selected)))
    receipt["disk_budget"] = disk_budget
    if receipt["dropped_frames"]:
        log(f"warning: Siril dropped {receipt['dropped_frames']} selected frames; actual stack={receipt['stacked_frames']}")
    receipt["interp"] = interp
    receipt["framing"] = framing
    receipt["mosaic_mode"] = mosaic_mode
    receipt["px_scale"] = float(drizzle["scale"]) if chosen["drizzle"] else 1.0
    receipt["weight"] = "noise" if chosen["weight"] else "none"

    if chosen["drizzle"] != drizzle["enabled"]:
        drizzle = {**drizzle, "fallback": True, "reason": f"{drizzle['reason']}+runtime_fallback"}
        log("drizzle: fell back to plain interpolation after a runtime failure")
    if want_weight and not chosen["weight"]:
        receipt["weight_fallback"] = True
        log("warning: noise weighting was dropped after a failure - the stack completed without it")
    receipt["drizzle"] = drizzle

    if chosen["drizzle"]:
        receipt["drizzle"]["header"] = _verify_master_pixel_scale(master, drizzle, work, seq_name)

    dump_json(work / "stack_receipt.json", receipt)
    log(f"master stack generated successfully: {master} "
        f"(resampling={'drizzle x' + format(drizzle['scale'], 'g') if chosen['drizzle'] else 'interp ' + interp}, "
        f"framing={framing}, weight={'noise' if chosen['weight'] else 'none'})")

    resampled_fits = list(work.glob(f"r_{seq_name}*.fit"))
    if resampled_fits:
        ref_idx = None
        rank_json = work / "ranking.json"
        if rank_json.exists():
            try:
                ref_idx = load_json(rank_json).get("reference_index")
            except Exception:
                pass
        ref_cand = None
        if ref_idx is not None:
            for p in (work / f"r_{seq_name}{ref_idx:05d}.fit", work / f"r_{seq_name}{ref_idx:04d}.fit", work / f"r_{seq_name}{ref_idx}.fit"):
                if p.exists():
                    ref_cand = p
                    break
        if ref_cand is None and resampled_fits:
            ref_cand = sorted(resampled_fits)[0]

        if ref_cand and ref_cand.exists():
            ref_dest = work / f"{seq_name}reference.fit"
            try:
                import shutil
                shutil.copy2(ref_cand, ref_dest)
            except Exception:
                pass

        log(f"cleaning up {len(resampled_fits)} intermediate resampled frames (r_{seq_name}*.fit) to reclaim disk space...")
        for rf in resampled_fits:
            try:
                rf.unlink()
            except Exception:
                pass
    drizzle_tmp = work / "drizztmp"
    if drizzle_tmp.is_dir():
        import shutil

        try:
            shutil.rmtree(drizzle_tmp)
            log("cleaned up Siril's drizzle scratch directory (drizztmp/)")
        except Exception as exc:
            log(f"warning: could not remove {drizzle_tmp}: {exc}")
