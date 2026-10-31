"""Verification, feedback loop, and adaptive framing module for siril-moon-stacking.

Performs delivered product sharp guard regression verification, background sky
statistics, automated candidate feedback search, and adaptive sky margin expansion.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import cv2
import numpy as np
from astropy.io import fits

from moon_common import (
    log,
    die,
    load_json,
    dump_json,
    fits_header_info,
    _resolve_px_scale,
)
from moon_optics import (
    _fit_lunar_limb_circle,
)
from moon_wavelets import (
    _load_wavelet_support,
    _guard_luminance,
    _compute_edge_ringing_damping_mask,
    _measure_dark_halo_ratio,
    _measure_chalky_saturation_index,
)
from moon_sharp_guard import (
    _sharp_guard_context,
    _sharp_guard_metrics,
    _sharp_guard_reasons,
    _measure_gradient_kurtosis,
)
from moon_register import (
    _subpixel_phase_correlation,
    _read_frame_plane,
    cmd_register,
)
from moon_import import (
    _structure_quality,
    cmd_import,
)
from moon_stacking import (
    cmd_stack,
)
from moon_viewer import (
    _generate_comparison_html,
)

def _verify_sharp_guard(work: Path, guard: dict, tile_info: dict, px_scale: float) -> dict:
    """Check the delivered TIFF, reversing display geometry before fixed-region QC."""
    import cv2
    from astropy.io import fits

    baseline_path = work / "moon_natural_baseline.fit"
    master_path = work / "moon_natural_master.fit"
    if not baseline_path.exists() or not master_path.exists() or not guard:
        return {"status": "unknown", "reason": "missing_full_baseline_or_receipt"}
    try:
        image = cv2.imread(str(work / "moon_natural.tif"), cv2.IMREAD_UNCHANGED)
        if image is None or image.dtype != np.uint16:
            return {"status": "failed", "reason": "missing_or_invalid_16bit_tiff"}
        padding = tile_info.get("adaptive_sky_margin", {}).get("padding", [0, 0, 0, 0])
        top, bottom, left, right = padding
        image = image[top:image.shape[0] - bottom, left:image.shape[1] - right]
        image = np.rot90(image, int(tile_info.get("rotation_deg", 0)) // 90)
        if image.ndim == 3:
            image = np.moveaxis(image[:, :, [2, 1, 0]], -1, 0)[:, ::-1, :]
        else:
            image = image[::-1, :]
        delivered = image.astype(np.float32) / 65535
        master = fits.getdata(master_path, memmap=False)
        if delivered.shape != master.shape or not np.all(np.isfinite(master)):
            return {"status": "failed", "reason": "invalid_float_master_or_geometry"}
        error = float(np.max(np.abs(delivered - master)))
        if error > 1.0 / 65535 + np.finfo(np.float32).eps:
            return {"status": "failed", "reason": "tiff_master_mismatch", "max_error": error}
        baseline = fits.getdata(baseline_path, memmap=False)
        ctx = _sharp_guard_context(baseline, px_scale, _load_wavelet_support(work, guard))
        metrics = _sharp_guard_metrics(ctx, delivered)
        reasons = _sharp_guard_reasons(ctx, metrics, require_gain=guard.get("alpha", 0) > 0)
        status = "off" if guard.get("mode") == "off" else "passed"
        if ctx["reason"]:
            status = "unavailable" if status != "off" else status
        elif reasons and status != "off":
            status = "failed"
        return {"status": status, "max_error": error, "metrics": metrics, "reasons": reasons,
                "source": "moon_natural.tif"}
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "failed", "reason": str(exc)}


def _background_metrics(data, sky_mask=None):
    """Channel-wise sky statistics; no claim when a sky region cannot be identified."""
    import cv2
    planes = data if data.ndim == 3 else data[None,...]
    green = planes[1] if len(planes) >= 3 else planes[0]
    height, width = green.shape
    side = min(64,max(1,min(height,width)//4))
    corners = [green[:side,:side],green[:side,-side:],green[-side:,:side],green[-side:,-side:]]
    if sky_mask is None:
        seed = min(corners,key=lambda patch:float(np.nanmedian(patch)))
    else:
        mask = np.asarray(sky_mask,dtype=bool) & np.isfinite(green)
        if np.count_nonzero(mask) < 64:
            return {"status":"unavailable: no reliable sky region","std_green":None,"mad_green":None,"relative_noise":None}
        seed = green[mask]
    level = float(np.nanmedian(seed))
    sigma = 1.4826*float(np.nanmedian(np.abs(seed-level)))
    peak = float(np.nanpercentile(green,99.5))
    if peak-level <= max(5*sigma,1e-6):
        return {"status":"unavailable: no reliable sky region","std_green":None,"mad_green":None,"relative_noise":None}
    if sky_mask is None:
        mask = np.isfinite(green) & (green <= level+max(4*sigma,1e-7))
        bright = (green > level+max(5*sigma,0.1*(peak-level))).astype(np.uint8)
        mask &= cv2.dilate(bright,np.ones((9,9),np.uint8)) == 0
        fit = _fit_lunar_limb_circle(green)
        if fit is None:
            count,labels,stats,centers = cv2.connectedComponentsWithStats(bright)
            if count>1:
                index = int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))+1
                x,y,bw,bh,area = stats[index]
                pad = max(bw,bh)
                ax,ay = max(0,x-pad),max(0,y-pad)
                bx,by = min(width,x+bw+pad),min(height,y+bh+pad)
                local_fit = _fit_lunar_limb_circle(green[ay:by,ax:bx])
                if local_fit:
                    fit = (local_fit[0]+ax,local_fit[1]+ay,*local_fit[2:])
        if fit:
            yy,xx = np.ogrid[:height,:width]
            mask &= (xx-fit[0])**2+(yy-fit[1])**2 > (fit[2]+8)**2
    if np.count_nonzero(mask) < 64:
        return {"status":"unavailable: no reliable sky region","std_green":None,"mad_green":None,"relative_noise":None}
    std, mad = [], []
    for plane in planes:
        values = plane[mask]
        std.append(float(np.std(values)))
        mad.append(float(1.4826*np.median(np.abs(values-np.median(values)))))
    gi = 1 if len(planes) >= 3 else 0
    moon = green[(green > level+max(5*sigma,0.2*(peak-level))) & np.isfinite(green)]
    signal = float(np.median(moon))-level if moon.size else 0
    return {"status":"measured","pixels":int(np.count_nonzero(mask)),"std_channels":std,"mad_channels":mad,
            "std_green":std[gi],"mad_green":mad[gi],"background_green":level,
            "relative_noise":mad[gi]/signal if signal>0 else None}


def _feedback_frame(path, reference, region):
    from astropy.io import fits
    import cv2
    data = fits.getdata(path).astype(np.float32)
    plane = data[1] if data.ndim == 3 else data
    h,w = reference.shape
    canvas = np.zeros((max(h,plane.shape[0]),max(w,plane.shape[1])),np.float32)
    template = np.zeros_like(canvas)
    canvas[:plane.shape[0],:plane.shape[1]] = plane
    template[:h,:w] = reference
    dx,dy,response = _subpixel_phase_correlation(template,canvas)
    if response < 0.15:
        return None
    transform = np.float32([[1,0,-round(dx)],[0,1,-round(dy)]])
    aligned = cv2.warpAffine(plane,transform,(w,h),flags=cv2.INTER_NEAREST,borderValue=float("nan"))
    x0,y0,x1,y1 = region
    common = aligned[y0:y1,x0:x1]
    if not common.size or not np.all(np.isfinite(common)):
        return None
    return common


def _feedback_accept(best, candidate):
    return (best["relative_noise"] > 0 and candidate["relative_noise"] <= best["relative_noise"]*0.95 and candidate["detail"] >= best["detail"]*0.97)


def _candidate_feedback(args):
    from astropy.io import fits
    import cv2
    work = Path(args.work).expanduser().resolve()
    original_limit = args.limit
    requested = min(256,original_limit) if original_limit>0 else 256
    history, best, stalled = [], None, 0
    reference = region = anchors = sky_mask = sky_circle = None
    snapshots = {}
    stopping = "candidate_limit"
    try:
        while True:
            args.limit = requested
            cmd_import(args)
            imported = load_json(work/"import_receipt.json")
            if not imported["is_video"] or not imported["video_metadata"]["primary_selected"]:
                die("feedback requires an adaptive, smart-top or smart-cluster video input")
            cmd_register(args)
            args.feedback_round = len(history)+1
            cmd_stack(args)
            stack = load_json(work/"stack_receipt.json")
            rank = load_json(work/"ranking.json")
            if reference is None:
                reference = _read_frame_plane(work/f"{args.seq}{rank['reference_index']:05d}.fit")
                reference /= max(float(np.percentile(reference,99.95)),1e-9)
                scale = float(stack["px_scale"])
                if scale != 1:
                    reference = cv2.resize(reference,None,fx=scale,fy=scale,interpolation=cv2.INTER_LINEAR)
                profile = load_json(work/"video_scores.json")
                centers = [r["tracking_shift"] for r in profile["rows"] if r["sharpness"] > 0]
                mx = int(np.ceil((max(c[0] for c in centers)-min(c[0] for c in centers))*scale))+4 if centers else 64
                my = int(np.ceil((max(c[1] for c in centers)-min(c[1] for c in centers))*scale))+4 if centers else 64
                region = [mx,my,reference.shape[1]-mx,reference.shape[0]-my]
                ax,ay,bx,by = region
                source_index = imported["video_metadata"]["source_mapping"][str(rank["reference_index"])]
                row = next(r for r in profile["rows"] if r["index"] == source_index)
                dx,dy = row["tracking_shift"]
                crop = imported["video_metadata"]["crop_box"] or [0,0]
                anchors = [[int(round((r[0]+dx-crop[0])*scale))-ax,int(round((r[1]+dy-crop[1])*scale))-ay,
                            int(round((r[2]+dx-crop[0])*scale))-ax,int(round((r[3]+dy-crop[1])*scale))-ay] for r in profile["anchors"]]
                anchors = [r for r in anchors if 0 <= r[0] < r[2] <= bx-ax and 0 <= r[1] < r[3] <= by-ay]
                if profile.get("circle"):
                    cx,cy,radius,_ = profile["circle"]
                    cx,cy = (cx+dx-crop[0])*scale-ax,(cy+dy-crop[1])*scale-ay
                    radius = radius*scale+8
                    yy,xx = np.ogrid[:by-ay,:bx-ax]
                    sky_mask = (xx-cx)**2+(yy-cy)**2 > radius**2
                    sky_circle = [cx,cy,radius]
            common = _feedback_frame(work/args.out,reference,region)
            metrics = _background_metrics(common,sky_mask) if common is not None else {"relative_noise":None}
            if common is not None and anchors and metrics.get("relative_noise") is not None:
                bg,noise = metrics["background_green"],max(metrics["mad_green"],1e-7)
                detail = np.median([_structure_quality(common[y0:y1,x0:x1],bg,noise,1.0) for x0,y0,x1,y1 in anchors])
                metrics["detail"] = float(detail)
            selected = imported["video_metadata"]["seeing_probe"]["chosen_frame_count"]
            record = {"round":len(history)+1,"requested":requested,"candidates":selected,"actually_stacked":stack["stacked_frames"], "registered_frames":rank["registered_frames"],
                      "metrics":metrics,"disk_budget":stack["disk_budget"],"accepted":False,"log":stack["log"]}
            valid_metrics = metrics.get("relative_noise") is not None and np.isfinite(metrics["relative_noise"]) and metrics.get("detail",0)>0
            detail_safe = valid_metrics and (not history or metrics["detail"] >= history[0]["metrics"]["detail"]*0.97)
            accepted = best is None or (detail_safe and _feedback_accept(best["metrics"],metrics))
            record["accepted"] = accepted
            stalled = 0 if accepted else stalled+1
            if best is None or (detail_safe and metrics["relative_noise"] < best["metrics"]["relative_noise"]):
                best = record
                shutil.copy2(work/args.out,work/"feedback_best.fit")
                snapshots = {name:(work/name).read_bytes() for name in (f"{args.seq}.seq","ranking.json","stack_receipt.json","import_receipt.json")}
            history.append(record)
            ceiling = min(original_limit,imported["video_metadata"]["candidate_pool_count"]) if original_limit>0 else imported["video_metadata"]["candidate_pool_count"]
            if not valid_metrics:
                stopping = "unavailable_quality_metrics";break
            if stalled >= 2:
                stopping = "two_rounds_without_improvement";break
            if selected < min(requested,ceiling):
                stopping = "disk_budget";break
            if requested >= ceiling:
                stopping = "candidate_limit";break
            requested = min(requested*2,ceiling)
        latest_import = load_json(work/"import_receipt.json")
        for name,contents in snapshots.items():
            (work/name).write_bytes(contents)
        restored = load_json(work/"import_receipt.json")
        restored["frame_count"] = latest_import["frame_count"]
        for key in ("source_mapping","source_files","extracted_frames"):
            restored["video_metadata"][key] = latest_import["video_metadata"][key]
        dump_json(work/"import_receipt.json",restored)
        shutil.copy2(work/"feedback_best.fit",work/args.out)
        dump_json(work/"candidate_feedback.json",{"rounds":history,"winner":best["round"],"stop_reason":stopping,"common_region":region,"sky_circle":sky_circle,"sky_pixels":int(np.count_nonzero(sky_mask)) if sky_mask is not None else None})
        log(f"candidate feedback: selected round {best['round']}, {best['actually_stacked']} actual frames; stopped={stopping}")
    finally:
        args.limit = original_limit
        if hasattr(args,"feedback_round"):
            del args.feedback_round




def _apply_adaptive_sky_margins(
    image: np.ndarray,
    circle: tuple | list | None = None,
    sky_margin_ratio: float = 0.25,
    auto_center: bool = True,
    bg_val: float | int = 0,
) -> tuple[np.ndarray, dict]:
    """Expand sky boundaries and center the lunar disc with guaranteed sky margin ratio.

    Adheres strictly to the authentic zero-distortion rule:
    - Never rescales or interpolates lunar pixels (100% native resolution preserved).
    - Expands bounding canvas using clean background padding.
    - Symmetrically centers the lunar disc so that margins on all 4 sides are >= radius * sky_margin_ratio.
    """
    import cv2
    import numpy as np

    h, w = image.shape[:2]
    meta = {
        "applied": False,
        "sky_margin_ratio": float(sky_margin_ratio),
        "original_shape": [int(h), int(w)],
        "new_shape": [int(h), int(w)],
        "padding": [0, 0, 0, 0],
    }

    if circle is None:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        gray_f = gray.astype(np.float32) if (gray.dtype != np.float32 and gray.dtype != np.float64) else gray.copy()
        circle = _fit_lunar_limb_circle(gray_f)

    if not circle:
        meta["reason"] = "no reliable lunar limb circle fitted"
        return image, meta

    cx, cy, radius = float(circle[0]), float(circle[1]), float(circle[2])
    if radius <= 10.0 or not np.isfinite(radius):
        meta["reason"] = "invalid radius"
        return image, meta

    req_margin = int(np.ceil(radius * sky_margin_ratio))

    if auto_center:
        half_w = max(cx, w - cx, radius + req_margin)
        half_h = max(cy, h - cy, radius + req_margin)

        new_w = int(np.ceil(2.0 * half_w))
        new_h = int(np.ceil(2.0 * half_h))
        new_w += new_w % 2
        new_h += new_h % 2

        pad_left = int(np.round(new_w / 2.0 - cx))
        pad_right = new_w - (pad_left + w)
        pad_top = int(np.round(new_h / 2.0 - cy))
        pad_bottom = new_h - (pad_top + h)
    else:
        pad_left = max(0, int(np.ceil(req_margin - (cx - radius))))
        pad_right = max(0, int(np.ceil(req_margin - (w - (cx + radius)))))
        pad_top = max(0, int(np.ceil(req_margin - (cy - radius))))
        pad_bottom = max(0, int(np.ceil(req_margin - (h - (cy + radius)))))
        new_w = w + pad_left + pad_right
        new_h = h + pad_top + pad_bottom

    pad_left = max(0, pad_left)
    pad_right = max(0, pad_right)
    pad_top = max(0, pad_top)
    pad_bottom = max(0, pad_bottom)

    if pad_left == 0 and pad_right == 0 and pad_top == 0 and pad_bottom == 0:
        meta["applied"] = True
        meta["reason"] = "margins already satisfied"
        meta["circle"] = [cx, cy, radius]
        meta["new_center"] = [cx, cy]
        return image, meta

    padding = [pad_top, pad_bottom, pad_left, pad_right]
    padded = cv2.copyMakeBorder(image, *padding, cv2.BORDER_CONSTANT, value=bg_val)

    meta["applied"] = True
    meta["circle"] = [cx, cy, radius]
    meta["new_shape"] = [padded.shape[0], padded.shape[1]]
    meta["new_center"] = [cx + pad_left, cy + pad_top]
    meta["padding"] = padding
    meta["req_margin"] = req_margin
    return padded, meta




def cmd_verify(args) -> None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(args.work).expanduser().resolve()
    master_path = work / args.master
    nat_tif = work / "moon_natural.tif"
    rank_json = work / "ranking.json"

    if not master_path.exists():
        die(f"master file not found: {master_path}")

    # Inspect master
    with fits.open(master_path, memmap=False) as hdul:
        master_data = hdul[0].data
        m_hdr = hdul[0].header

    px_scale = _resolve_px_scale(args, work, master_path)

    report = {
        "master": str(master_path),
        "shape": list(master_data.shape),
        "bitpix": m_hdr.get("BITPIX"),
        "min": float(master_data.min()),
        "max": float(master_data.max()),
        "mean": float(master_data.mean()),
    }

    # Drizzle supersampling state (from the master header and/or the stack receipt)
    drz_scale = m_hdr.get("DRZSCALE")
    report["drizzle"] = {
        "px_scale": px_scale,
        "enabled": bool(drz_scale and float(drz_scale) != 1.0),
        "scale": float(drz_scale) if drz_scale else px_scale,
        "pixfrac": m_hdr.get("DRZPIXFR"),
        "kernel": m_hdr.get("DRZKRENL"),
        "native_pixel_size": m_hdr.get("ORIGXPIX"),
        "effective_pixel_size": m_hdr.get("XPIXSZ"),
    }

    # Capture device identification and whether any optics came from the table
    report["device"] = {
        "id": m_hdr.get("DEVICE"),
        "match_source": m_hdr.get("DEVSRC"),
        "confidence": m_hdr.get("DEVCONF"),
        "optical_source": m_hdr.get("OPTISRC"),
        "table_priors": m_hdr.get("OPTPRIOR"),
    }

    mosaic_mode = getattr(args, "mosaic_mode", None)
    tile_json = work / "mosaic_tile_info.json"
    t_data = {}
    if tile_json.exists():
        try:
            t_data = load_json(tile_json)
            if not mosaic_mode:
                mosaic_mode = t_data.get("mosaic_mode", "disc")
            report["focal_length"] = t_data.get("focal_length")
            report["aperture"] = t_data.get("aperture")
            report["pixel_size"] = t_data.get("pixel_size")
            report["optical_inference"] = t_data.get("optical_inference")
        except Exception:
            pass
    if not mosaic_mode:
        mosaic_mode = "disc"
    report["mosaic_mode"] = mosaic_mode

    prof_json = work / "lunar_profile.json"
    if prof_json.exists():
        try:
            p_data = load_json(prof_json)
            report["profile_locked"] = bool(p_data.get("locked"))
            report["lock_source"] = p_data.get("lock_source")
            report["profile_hi_lum"] = p_data.get("histogram", {}).get("hi_lum")
            report["profile_kr"] = p_data.get("white_balance", {}).get("k_r")
            report["profile_kb"] = p_data.get("white_balance", {}).get("k_b")
        except Exception:
            pass

    ext_json = work / "extinction_receipt.json"
    if ext_json.exists():
        try:
            report["extinction_compensation"] = load_json(ext_json)
        except Exception:
            pass

    # Measure each channel on identified empty sky.
    background = _background_metrics(master_data)
    report["background_noise"] = background
    report["background_noise_std"] = background.get("std_green")
    report["background_noise_status"] = background["status"]

    # Measure surface sharpness on master vs single frame
    if rank_json.exists():
        r_info = load_json(rank_json)
        ref_idx = r_info.get("reference_index")
        report["reference_frame"] = ref_idx
        report["candidate_frames"] = r_info.get("candidate_frames", r_info.get("total_frames"))
        report["registered_frames"] = r_info.get("registered_frames", r_info.get("kept_frames"))
    if (work/"stack_receipt.json").exists():
        stack_info = load_json(work/"stack_receipt.json")
        report["stacked_frames"] = stack_info.get("stacked_frames")
        report["kept_frames"] = report["stacked_frames"]
        report["dropped_frames"] = stack_info.get("dropped_frames")

    post_receipt_path = work / "postprocess_receipt.json"
    post_receipt = load_json(post_receipt_path) if post_receipt_path.exists() else {}
    guard = post_receipt.get("sharp_guard", {})
    report["wavelet_adaptation"] = post_receipt.get("wavelet_adaptation", {"status": "unknown"})
    delivery = _verify_sharp_guard(work, guard, t_data if tile_json.exists() else {}, px_scale)
    report["sharp_guard"] = dict(guard, delivery=delivery)
    if delivery["status"] == "unknown":
        report["sharp_guard"].update(status="unknown", alpha=None)

    full_baseline = work / "moon_unsharpened.fit"
    if full_baseline.exists() and guard.get("detail_file") in ("moon_lum_sharp.fit", "moon_sharp.fit"):
        base_path = full_baseline
        sharp_path = work / guard["detail_file"]
    else:
        base_path = work / "moon_lum_base.fit"
        sharp_path = work / "moon_lum_sharp.fit"
        if not base_path.exists():
            base_path, sharp_path = work / "moon_base.fit", work / "moon_sharp.fit"
    if base_path.exists() and sharp_path.exists():
        try:
            b_dat = fits.getdata(base_path, memmap=False)
            s_dat = fits.getdata(sharp_path, memmap=False)
            if base_path == full_baseline:
                metrics = _sharp_guard_metrics(_sharp_guard_context(b_dat, px_scale, _load_wavelet_support(work, guard)), s_dat)
                dhr_val, csi_val, gkm_val = (metrics.get(key) for key in ("dhr", "csi", "gkm"))
                report["positive_overshoot_ratio"] = metrics.get("positive_overshoot_ratio")
                report["sharp_guard"]["verified_selected"] = metrics
            else:
                b_dat, s_dat = _guard_luminance(b_dat), _guard_luminance(s_dat)
                mask = _compute_edge_ringing_damping_mask(b_dat, px_scale=px_scale)
                dhr_val = _measure_dark_halo_ratio(b_dat, s_dat, mask)
                csi_val = _measure_chalky_saturation_index(s_dat)
                gkm_val = _measure_gradient_kurtosis(s_dat)
            report["dark_halo_ratio"] = dhr_val
            report["dark_halo_status"] = ("NOT_APPLICABLE (no measurable step edges)" if dhr_val is None else
                "EXCELLENT (artifact-free)" if dhr_val < 0.015 else
                "GOOD (controlled)" if dhr_val < 0.035 else "WARNING (edge ringing detected)")
            report["chalky_saturation_index"] = csi_val
            report["chalky_status"] = ("UNAVAILABLE" if csi_val is None else
                "EXCELLENT (highlight dynamic retained)" if csi_val < 0.010 else
                "GOOD (controlled highlights)" if csi_val < 0.025 else "WARNING (highlight chalkiness / over-saturation)")
            report["gradient_kurtosis"] = gkm_val
            report["brittleness_status"] = ("UNAVAILABLE" if gkm_val is None else
                "ORGANIC (natural smooth texture)" if gkm_val < 6.0 else
                "CRISP (high detail)" if gkm_val < 14.0 else "WARNING (brittle / crunchy texture detected)")
        except (OSError, ValueError, TypeError) as exc:
            report["quality_metric_error"] = str(exc)

    dump_json(work / "verify_report.json", report)
    log("verification summary:")
    log(f"  Mosaic mode: {report['mosaic_mode']}")
    dev = report.get("device") or {}
    if dev.get("id") or dev.get("match_source"):
        prior_note = (f", table priors: {dev.get('table_priors')}"
                      if dev.get("table_priors") else "")
        log(f"  Capture device: {dev.get('id') or 'unknown'} "
            f"(matched from {dev.get('match_source')}, confidence={dev.get('confidence')}"
            f"{prior_note})")
    drz = report.get("drizzle") or {}
    if drz.get("enabled"):
        log(f"  Drizzle: x{float(drz.get('scale', 1.0)):g} supersampled "
            f"(pixfrac={drz.get('pixfrac')}, kernel={drz.get('kernel')}, "
            f"native pixel={drz.get('native_pixel_size')}um -> effective {drz.get('effective_pixel_size')}um)")
    else:
        log("  Drizzle: off (native sampling)")
    if report.get("profile_locked"):
        log(f"  Calibration Profile: LOCKED (source={report.get('lock_source')}, hi_lum={report.get('profile_hi_lum')})")
    elif "profile_locked" in report:
        log(f"  Calibration Profile: ANCHOR MASTER (unlocked baseline, hi_lum={report.get('profile_hi_lum')})")
    if all(report.get(k) for k in ("focal_length","aperture","pixel_size")):
        fl_v = float(report["focal_length"])
        dia_v = float(report.get("aperture") or 80.0)
        px_v = float(report.get("pixel_size") or 3.73)
        f_rat = fl_v / dia_v if dia_v else 0.0
        airy_r = (1.22 * 0.55 * fl_v) / (dia_v * px_v) if (dia_v and px_v) else 0.0
        opt_src = report.get("optical_inference", {}).get("focal_source", "manual") if report.get("optical_inference") else "manual"
        log(f"  Optical Setup: fl={fl_v:.1f}mm, dia={dia_v:.1f}mm (F/{f_rat:.1f}, Airy r={airy_r:.2f}px, source='{opt_src}')")
    log(f"  Master dimensions: {report['shape']} (BITPIX={report['bitpix']})")
    log(f"  Pixel range: [{report['min']:.4f}, {report['max']:.4f}] (mean={report['mean']:.4f})")
    log(f"  Background noise: {report['background_noise_std']} ({report['background_noise_status']})")
    if "extinction_compensation" in report:
        ec = report["extinction_compensation"]
        log(f"  Atmospheric Extinction: COMPENSATED (zenith={ec.get('zenith_angle_deg')}°, B-grad={ec.get('amp_b', 0)*100:.1f}%, conf={ec.get('confidence')})")
    if report.get("dark_halo_ratio") is not None:
        log(f"  Dark Halo Ratio (DHR): {report['dark_halo_ratio']:.4f} -> [{report.get('dark_halo_status', 'N/A')}]")
    if report.get("chalky_saturation_index") is not None:
        log(f"  Chalky Saturation (CSI): {report['chalky_saturation_index']:.4f} -> [{report.get('chalky_status', 'N/A')}]")
    if report.get("gradient_kurtosis") is not None:
        log(f"  Gradient Kurtosis (GKM): {report['gradient_kurtosis']:.2f} -> [{report.get('brittleness_status', 'N/A')}]")
    if (work / "moon_natural.jpg").exists():
        log(f"  [OK] moon_natural.jpg ({round((work / 'moon_natural.jpg').stat().st_size / 1024)} KB)")
    if (work / "moon_mineral.jpg").exists():
        log(f"  [OK] moon_mineral.jpg ({round((work / 'moon_mineral.jpg').stat().st_size / 1024)} KB)")

    # Update interactive HTML dual-window comparison viewer with full verification metrics
    if getattr(args, "html_report", True):
        if not (work / "moon_reference.jpg").exists():
            _render_reference_frame(
                work=work,
                seq_name=getattr(args, "seq", "moon_"),
            )
        html_file = _generate_comparison_html(
            work=work,
            report=report,
        )
        log(f"  - Comparison viewer:   {html_file}")

    if delivery["status"] == "failed":
        die(f"delivered TIFF failed sharp guard verification; see {work / 'verify_report.json'}")



