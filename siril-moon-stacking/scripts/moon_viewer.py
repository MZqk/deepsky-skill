"""Comparison viewer and reference frame rendering module for siril-moon-stacking.

Generates isometric reference frames and standalone HTML dual-window side-by-side
interactive comparison viewer with synchronized zoom/pan, loupe inspection,
histogram inspection, and before/after swipe controls.
"""

from __future__ import annotations

import json
from pathlib import Path
import cv2
import numpy as np
from astropy.io import fits

from moon_common import (
    log,
    load_json,
    _estimate_pedestal,
)
from moon_optics import (
    _fit_lunar_limb_circle,
)

def _render_reference_frame(
    work: Path,
    active_profile: dict | None = None,
    seq_name: str = "moon_",
    rotate_deg: int = 0,
    square_crop: bool = False,
    tile_info: dict | None = None,
) -> Path | None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(work).expanduser().resolve()
    out_ref = work / "moon_reference.jpg"
    out_ref_square = work / "moon_reference_square.jpg"
    out_raw_orig = work / "moon_raw_original.jpg"

    # Locate ranking.json
    rank_json = work / "ranking.json"
    ref_idx = None
    if rank_json.exists():
        try:
            r_info = load_json(rank_json)
            ref_idx = r_info.get("reference_index")
        except Exception:
            pass

    # Prioritize true unresampled, uncropped native sequence frames
    # (Avoid r_*.fit or cropped reference.fit produced by seqapplyreg -framing=min)
    candidates = []
    if ref_idx is not None:
        candidates.append(work / f"{seq_name}{ref_idx:05d}.fit")
        candidates.append(work / f"{seq_name}{ref_idx:04d}.fit")
        candidates.append(work / f"{seq_name}{ref_idx}.fit")

    # Look for any uncropped sequence FITS frame in work directory
    for f in sorted(work.glob(f"{seq_name}*.fit")):
        name_lower = f.name.lower()
        if not any(k in name_lower for k in ("master", "lum", "base", "sharp", "color", "clean", "sat", "feedback", "reference", "r_")):
            candidates.append(f)

    # Secondary fallback to intermediate or resampled reference frames
    candidates.extend([
        work / f"{seq_name}reference.fit",
        work / "moon_reference.fit",
    ])
    if ref_idx is not None:
        candidates.append(work / f"r_{seq_name}{ref_idx:05d}.fit")
        candidates.append(work / f"r_{seq_name}{ref_idx:04d}.fit")

    target_fit = None
    for cand in candidates:
        if cand.exists():
            target_fit = cand
            break

    if target_fit is None or not target_fit.exists():
        return out_ref if out_ref.exists() else None

    try:
        with fits.open(target_fit, memmap=False) as hdul:
            data = hdul[0].data.astype(np.float32)
    except Exception as exc:
        log(f"reference frame render: failed to open {target_fit.name}: {exc}")
        return out_ref if out_ref.exists() else None

    # Handle NaNs or Infs
    data = np.nan_to_num(data, nan=0.0, posinf=1.0, neginf=0.0)

    # Normalize to [0.0, 1.0]
    d_max = float(np.max(data))
    if d_max > 1.5:
        norm_factor = 65535.0 if d_max > 255.0 else 255.0
        data = data / norm_factor
    data = np.clip(data, 0.0, 1.0)

    profile = active_profile or {}
    if not profile and (work / "lunar_profile.json").exists():
        try:
            profile = load_json(work / "lunar_profile.json")
        except Exception:
            profile = {}

    hist = profile.get("histogram", {})
    wb = profile.get("white_balance", {})
    mid_val = float(hist.get("midtone", 0.13))
    mid_val = max(0.01, min(0.99, mid_val))

    def _mtf_stretch(x, m):
        denom = (2.0 * m - 1.0) * x - m
        out = np.zeros_like(x)
        mask = np.abs(denom) > 1e-7
        out[mask] = (m - 1.0) * x[mask] / denom[mask]
        return np.clip(out, 0.0, 1.0)

    is_rgb = (data.ndim == 3 and data.shape[0] >= 3)
    if is_rgb:
        bg_r_prof = float(hist.get("bg_r", 0.0))
        bg_g_prof = float(hist.get("bg_g", 0.0))
        bg_b_prof = float(hist.get("bg_b", 0.0))
        bg_r = bg_r_prof if bg_r_prof > 0.0 else _estimate_pedestal(data[0])
        bg_g = bg_g_prof if bg_g_prof > 0.0 else _estimate_pedestal(data[1])
        bg_b = bg_b_prof if bg_b_prof > 0.0 else _estimate_pedestal(data[2])

        r_net = np.maximum(0.0, data[0] - bg_r)
        g_net = np.maximum(0.0, data[1] - bg_g)
        b_net = np.maximum(0.0, data[2] - bg_b)

        # Suppress limb glare outside disk to match product sky baseline
        try:
            net_planes = np.stack([r_net, g_net, b_net], axis=0)
            net_planes, _ = _suppress_lunar_limb_glare(net_planes, glare_mode="soft")
            r_net, g_net, b_net = net_planes[0], net_planes[1], net_planes[2]
        except Exception:
            pass

        hi_lum = float(hist.get("hi_lum", 1.0))
        k_r = float(wb.get("k_r", 1.0))
        k_b = float(wb.get("k_b", 1.0))

        span_r = max(hi_lum - bg_r_prof, 1e-6)
        span_g = max(hi_lum - bg_g_prof, 1e-6)
        span_b = max(hi_lum - bg_b_prof, 1e-6)

        r_lin = np.clip(r_net / span_r * k_r, 0.0, 1.0)
        g_lin = np.clip(g_net / span_g, 0.0, 1.0)
        b_lin = np.clip(b_net / span_b * k_b, 0.0, 1.0)

        r_str = _mtf_stretch(r_lin, mid_val)
        g_str = _mtf_stretch(g_lin, mid_val)
        b_str = _mtf_stretch(b_lin, mid_val)

        # FITS coordinates vertical flip to match display
        r_str = r_str[::-1, :]
        g_str = g_str[::-1, :]
        b_str = b_str[::-1, :]

        bgr = (np.stack([b_str, g_str, r_str], axis=-1) * 255.0).astype(np.uint8)
    else:
        plane = data if data.ndim == 2 else data[0]
        bg_lum_prof = float(hist.get("bg_lum", hist.get("bg_g", 0.0)))
        bg_lum = bg_lum_prof if bg_lum_prof > 0.0 else _estimate_pedestal(plane)
        hi_lum = float(hist.get("hi_lum", 1.0))
        span = max(hi_lum - bg_lum_prof, 1e-6)
        p_net = np.maximum(0.0, plane - bg_lum)
        gray_lin = np.clip(p_net / span, 0.0, 1.0)
        gray_str = _mtf_stretch(gray_lin, mid_val)
        gray_str = gray_str[::-1, :]
        bgr = (gray_str * 255.0).astype(np.uint8)

    # Render True Camera RAW view (native sensor FOV, unaligned, unpadded, no limb glare suppression, no pedestal subtraction)
    try:
        hi_lum_raw = float(hist.get("hi_lum", 1.0))
        span_raw = max(hi_lum_raw, 1e-6)
        if is_rgb:
            k_r_val = float(wb.get("k_r", 1.0))
            k_b_val = float(wb.get("k_b", 1.0))
            r_raw_lin = np.clip(data[0] / span_raw * k_r_val, 0.0, 1.0)
            g_raw_lin = np.clip(data[1] / span_raw, 0.0, 1.0)
            b_raw_lin = np.clip(data[2] / span_raw * k_b_val, 0.0, 1.0)
            r_raw_str = _mtf_stretch(r_raw_lin, mid_val)[::-1, :]
            g_raw_str = _mtf_stretch(g_raw_lin, mid_val)[::-1, :]
            b_raw_str = _mtf_stretch(b_raw_lin, mid_val)[::-1, :]
            raw_orig_bgr = (np.stack([b_raw_str, g_raw_str, r_raw_str], axis=-1) * 255.0).astype(np.uint8)
        else:
            plane_raw = data if data.ndim == 2 else data[0]
            gray_raw_lin = np.clip(plane_raw / span_raw, 0.0, 1.0)
            gray_raw_str = _mtf_stretch(gray_raw_lin, mid_val)[::-1, :]
            raw_orig_bgr = (gray_raw_str * 255.0).astype(np.uint8)

        if rotate_deg in (90, 180, 270):
            rot_flag_raw = {
                90: cv2.ROTATE_90_CLOCKWISE,
                180: cv2.ROTATE_180,
                270: cv2.ROTATE_90_COUNTERCLOCKWISE,
            }[rotate_deg]
            raw_orig_bgr = cv2.rotate(raw_orig_bgr, rot_flag_raw)

        cv2.imwrite(str(out_raw_orig), raw_orig_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
        log(f"original raw frame rendered: {out_raw_orig.name} (native {raw_orig_bgr.shape[1]}x{raw_orig_bgr.shape[0]}, unpadded, unaligned)")
    except Exception as exc:
        log(f"original raw frame render warning: {exc}")

    # Orientation rotation if requested
    if rotate_deg in (90, 180, 270):
        rot_flag = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[rotate_deg]
        bgr = cv2.rotate(bgr, rot_flag)

    # Rigid 1:1 sub-pixel coordinate alignment to finished product (moon_natural.jpg)
    target_img_path = work / "moon_natural.jpg"
    aligned_to_product = False
    if target_img_path.exists():
        try:
            target_img = cv2.imread(str(target_img_path), cv2.IMREAD_UNCHANGED)
            if target_img is not None:
                tgt_h, tgt_w = target_img.shape[:2]
                gray_tgt = cv2.cvtColor(target_img, cv2.COLOR_BGR2GRAY) if target_img.ndim == 3 else target_img
                gray_ref = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr

                c_tgt = _fit_lunar_limb_circle(gray_tgt.astype(np.float32))
                c_ref = _fit_lunar_limb_circle(gray_ref.astype(np.float32))

                cur_h, cur_w = bgr.shape[:2]
                if c_tgt and c_ref:
                    dx0 = float(c_tgt[0] - c_ref[0])
                    dy0 = float(c_tgt[1] - c_ref[1])
                else:
                    dx0 = float((tgt_w - cur_w) / 2.0)
                    dy0 = float((tgt_h - cur_h) / 2.0)

                M0 = np.array([[1.0, 0.0, dx0], [0.0, 1.0, dy0]], dtype=np.float32)
                ref_coarse = cv2.warpAffine(bgr, M0, (tgt_w, tgt_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)

                gray_coarse = cv2.cvtColor(ref_coarse, cv2.COLOR_BGR2GRAY) if ref_coarse.ndim == 3 else ref_coarse
                if c_tgt:
                    cxi, cyi = int(round(c_tgt[0])), int(round(c_tgt[1]))
                    box_r = int(min(c_tgt[2] * 0.5, cxi, cyi, tgt_w - 1 - cxi, tgt_h - 1 - cyi))
                else:
                    cxi, cyi = tgt_w // 2, tgt_h // 2
                    box_r = min(tgt_w, tgt_h) // 4
                box_r = max(32, box_r)

                tgt_roi = gray_tgt[cyi - box_r : cyi + box_r, cxi - box_r : cxi + box_r].astype(np.float32)
                ref_roi = gray_coarse[cyi - box_r : cyi + box_r, cxi - box_r : cxi + box_r].astype(np.float32)

                dx_fine, dy_fine = 0.0, 0.0
                if tgt_roi.shape == ref_roi.shape and tgt_roi.shape[0] >= 32 and tgt_roi.shape[1] >= 32:
                    hann = cv2.createHanningWindow((tgt_roi.shape[1], tgt_roi.shape[0]), cv2.CV_32F)
                    (f_dx, f_dy), resp = cv2.phaseCorrelate(tgt_roi, ref_roi, hann)
                    if resp > 0.08 and abs(f_dx) < 64 and abs(f_dy) < 64:
                        dx_fine, dy_fine = f_dx, f_dy

                total_dx = dx0 - dx_fine
                total_dy = dy0 - dy_fine
                M_final = np.array([[1.0, 0.0, total_dx], [0.0, 1.0, total_dy]], dtype=np.float32)
                bgr = cv2.warpAffine(bgr, M_final, (tgt_w, tgt_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
                aligned_to_product = True
                log(f"reference frame aligned to product: offset=({total_dx:+.1f}, {total_dy:+.1f}) px, canvas={tgt_w}x{tgt_h}")
        except Exception as exc:
            log(f"reference frame product alignment note: {exc}")

    if not aligned_to_product:
        # Harmonize unpadded dimensions with moon_master.fit if master exists
        master_fit = work / "moon_master.fit"
        if master_fit.exists():
            try:
                with fits.open(master_fit, memmap=False) as mhdul:
                    m_h = int(mhdul[0].header.get("NAXIS2", 0))
                    m_w = int(mhdul[0].header.get("NAXIS1", 0))
                if rotate_deg in (90, 270):
                    m_h, m_w = m_w, m_h
                if m_h > 0 and m_w > 0:
                    cur_h, cur_w = bgr.shape[:2]
                    if cur_h != m_h or cur_w != m_w:
                        # Align dimensions to match master grid exactly
                        if cur_h > m_h or cur_w > m_w:
                            top = max(0, (cur_h - m_h) // 2)
                            left = max(0, (cur_w - m_w) // 2)
                            bgr = bgr[top:top + m_h, left:left + m_w]
                        cur_h, cur_w = bgr.shape[:2]
                        if cur_h < m_h or cur_w < m_w:
                            pad_top = max(0, (m_h - cur_h) // 2)
                            pad_bot = max(0, m_h - cur_h - pad_top)
                            pad_left = max(0, (m_w - cur_w) // 2)
                            pad_right = max(0, m_w - cur_w - pad_left)
                            bgr = cv2.copyMakeBorder(bgr, pad_top, pad_bot, pad_left, pad_right, cv2.BORDER_CONSTANT, value=0)
            except Exception as exc:
                log(f"reference frame dimension alignment note: {exc}")

        # Apply adaptive sky padding if recorded in tile_info
        adaptive_margin = (tile_info or {}).get("adaptive_sky_margin")
        if not adaptive_margin and (work / "mosaic_tile_info.json").exists():
            try:
                adaptive_margin = load_json(work / "mosaic_tile_info.json").get("adaptive_sky_margin")
            except Exception:
                adaptive_margin = None
        if adaptive_margin and adaptive_margin.get("applied"):
            pad = adaptive_margin.get("padding")
            if pad and any(p > 0 for p in pad):
                bgr = cv2.copyMakeBorder(bgr, *pad, cv2.BORDER_CONSTANT, value=0)

    cv2.imwrite(str(out_ref), bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
    log(f"reference frame rendered: {out_ref.name} (frame #{ref_idx or 'base'}, isometric MTF applied)")


    # Also render square version if tile_info has square_crop info
    if square_crop:
        sq_info = (tile_info or {}).get("square_crop")
        if not sq_info and (work / "mosaic_tile_info.json").exists():
            try:
                sq_info = load_json(work / "mosaic_tile_info.json").get("square_crop")
            except Exception:
                sq_info = None
        if sq_info and "padding" in sq_info and "box" in sq_info:
            try:
                padding = sq_info["padding"]
                left, top, right, bottom = sq_info["box"]
                padded = cv2.copyMakeBorder(bgr, *padding, cv2.BORDER_CONSTANT, value=0)
                crop = padded[top + padding[0]:bottom + padding[0], left + padding[2]:right + padding[2]]
                cv2.imwrite(str(out_ref_square), crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                log(f"reference square frame rendered: {out_ref_square.name}")
            except Exception as exc:
                log(f"reference square crop warning: {exc}")

    return out_ref


def _generate_comparison_html(
    work: Path,
    report: dict | None = None,
    tile_info: dict | None = None,
    active_profile: dict | None = None,
) -> Path:
    work = Path(work).expanduser().resolve()
    out_html = work / "comparison.html"

    # 1. Gather product images
    manifest = {
        "reference": "moon_reference.jpg" if (work / "moon_reference.jpg").exists() else None,
        "referenceSquare": "moon_reference_square.jpg" if (work / "moon_reference_square.jpg").exists() else None,
        "rawOriginal": "moon_raw_original.jpg" if (work / "moon_raw_original.jpg").exists() else None,
        "targets": [],
    }

    candidates = [
        ("natural", "自然写实母版 (Natural Master)", "moon_natural.jpg", False),
        ("mineral", "电影级地质彩月 (Deep-Cine Mineral)", "moon_mineral.jpg", False),
        ("natural_square", "1:1 自然方形特写 (Natural Square)", "moon_natural_square.jpg", True),
        ("mineral_square", "1:1 彩月方形特写 (Mineral Square)", "moon_mineral_square.jpg", True),
    ]

    for tid, tname, tfile, is_sq in candidates:
        if (work / tfile).exists():
            manifest["targets"].append({
                "id": tid,
                "name": tname,
                "file": tfile,
                "square": is_sq,
            })

    if not manifest["targets"]:
        # Fallback to any existing jpg
        for jpg in sorted(work.glob("*.jpg")):
            if jpg.name not in ("moon_reference.jpg", "moon_reference_square.jpg", "moon_raw_original.jpg"):
                manifest["targets"].append({
                    "id": jpg.stem,
                    "name": jpg.name,
                    "file": jpg.name,
                    "square": "square" in jpg.name,
                })

    # 2. Gather HUD metrics
    hud_data = {}
    rep = report or {}
    if not rep and (work / "verify_report.json").exists():
        try:
            rep = load_json(work / "verify_report.json")
        except Exception:
            rep = {}

    ti = tile_info or {}
    if not ti and (work / "mosaic_tile_info.json").exists():
        try:
            ti = load_json(work / "mosaic_tile_info.json")
        except Exception:
            ti = {}

    prof = active_profile or {}
    if not prof and (work / "lunar_profile.json").exists():
        try:
            prof = load_json(work / "lunar_profile.json")
        except Exception:
            prof = {}

    post_rec = load_json(work / "postprocess_receipt.json") if (work / "postprocess_receipt.json").exists() else {}
    guard = (report or {}).get("sharp_guard") or post_rec.get("sharp_guard") or {"status": "unknown", "alpha": None}
    adaptation = (report or {}).get("wavelet_adaptation") or post_rec.get("wavelet_adaptation") or {"status": "unknown"}
    guard_metrics = guard.get("verified_selected") or guard.get("selected") or {}
    stack_rec = {}
    if (work / "stack_receipt.json").exists():
        try:
            stack_rec = load_json(work / "stack_receipt.json")
        except Exception:
            stack_rec = {}

    rank_rec = {}
    if (work / "ranking.json").exists():
        try:
            rank_rec = load_json(work / "ranking.json")
        except Exception:
            rank_rec = {}

    dev = rep.get("device") or {}
    fl_val = rep.get("focal_length") or ti.get("focal_length")
    ap_val = rep.get("aperture") or ti.get("aperture")
    px_val = rep.get("pixel_size") or ti.get("pixel_size")
    f_ratio = round(float(fl_val) / float(ap_val), 1) if fl_val and ap_val else None
    airy_r = round((1.22 * 0.55 * float(fl_val)) / (float(ap_val) * float(px_val)), 2) if fl_val and ap_val and px_val else None

    drz = rep.get("drizzle") or ti.get("drizzle") or stack_rec.get("drizzle") or {}
    stk_frames = rep.get("stacked_frames") or stack_rec.get("stacked_frames")
    reg_frames = rep.get("registered_frames") or rank_rec.get("registered_frames")
    tot_frames = rep.get("candidate_frames") or rank_rec.get("candidate_frames") or rank_rec.get("total_frames")

    hud_data = {
        "device": {
            "name": dev.get("id") or "Auto-detected",
            "source": dev.get("match_source") or "FITS Header",
            "confidence": dev.get("confidence") or "N/A",
        },
        "optics": {
            "focal_length": float(fl_val) if fl_val else None,
            "aperture": float(ap_val) if ap_val else None,
            "pixel_size": float(px_val) if px_val else None,
            "f_ratio": f_ratio,
            "airy_r": airy_r,
        },
        "stacking": {
            "stacked_frames": stk_frames,
            "registered_frames": reg_frames,
            "total_frames": tot_frames,
            "ratio": f"{(stk_frames / tot_frames * 100):.1f}%" if stk_frames and tot_frames else None,
            "method": stack_rec.get("stack_method", "Winsorized Rej Mean"),
            "weight": stack_rec.get("weight", "noise"),
        },
        "drizzle": {
            "enabled": bool(drz.get("enabled")),
            "scale": drz.get("scale", 1.0),
            "kernel": drz.get("kernel", "square"),
            "pixfrac": drz.get("pixfrac", 1.0),
        },
        "quality": {
            "sharp_guard": {"status": guard.get("status"), "alpha": guard.get("alpha")},
            "wavelet_adaptation": {"status": adaptation.get("status"), "support_coverage": adaptation.get("support_coverage")},
            "dhr": guard_metrics.get("dhr", rep.get("dark_halo_ratio")),
            "dhr_status": rep.get("dark_halo_status"),
            "csi": guard_metrics.get("csi", rep.get("chalky_saturation_index")),
            "csi_status": rep.get("chalky_status"),
            "gkm": guard_metrics.get("gkm", rep.get("gradient_kurtosis")),
            "gkm_status": rep.get("brittleness_status"),
            "bg_noise_std": rep.get("background_noise_std"),
            "bg_noise_status": rep.get("background_noise_status"),
        },
        "color": {
            "wb_kr": prof.get("white_balance", {}).get("k_r"),
            "wb_kb": prof.get("white_balance", {}).get("k_b"),
            "midtone": prof.get("histogram", {}).get("midtone"),
            "mineral_style": prof.get("mineral", {}).get("mineral_style"),
            "fe_boost": prof.get("mineral", {}).get("fe_boost"),
            "ti_boost": prof.get("mineral", {}).get("ti_boost"),
        },
    }

    import json
    manifest_json = json.dumps(manifest)
    hud_json = json.dumps(hud_data)

    html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Siril Moon Stacking · Dual-Window Linked Inspector</title>
  <style>
    :root {
      --bg-dark: #07090e;
      --bg-surface: #0f1523;
      --bg-panel: #141c2e;
      --border-color: #1e293b;
      --border-highlight: #334155;
      --text-main: #f8fafc;
      --text-muted: #94a3b8;
      --accent-cyan: #38bdf8;
      --accent-blue: #2563eb;
      --accent-amber: #f59e0b;
      --accent-emerald: #10b981;
      --accent-rose: #f43f5e;
      --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg-dark);
      color: var(--text-main);
      font-family: var(--font-family);
      overflow: hidden;
      height: 100vh;
      display: flex;
      flex-direction: column;
      user-select: none;
    }

    /* Top Navigation Toolbar */
    .toolbar {
      height: 52px;
      background: rgba(15, 23, 42, 0.9);
      backdrop-filter: blur(12px);
      border-bottom: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 16px;
      z-index: 100;
    }
    .brand-section {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-logo { font-size: 1.3rem; }
    .brand-title {
      font-size: 0.95rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: var(--text-main);
    }
    .badge-sub {
      background: rgba(56, 189, 248, 0.12);
      color: var(--accent-cyan);
      border: 1px solid rgba(56, 189, 248, 0.3);
      padding: 2px 7px;
      border-radius: 9999px;
      font-size: 0.72rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .controls-center {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .btn-group {
      display: flex;
      background: var(--bg-panel);
      padding: 3px;
      border-radius: 8px;
      border: 1px solid var(--border-color);
    }
    .btn-group button {
      background: transparent;
      border: none;
      color: var(--text-muted);
      padding: 5px 11px;
      font-size: 0.8rem;
      font-weight: 600;
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.15s ease;
    }
    .btn-group button:hover { color: var(--text-main); background: rgba(255, 255, 255, 0.05); }
    .btn-group button.active {
      color: #fff;
      background: var(--accent-blue);
      box-shadow: 0 1px 4px rgba(37, 99, 235, 0.35);
    }

    .tool-btn {
      background: var(--bg-panel);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 5px 10px;
      border-radius: 6px;
      font-size: 0.8rem;
      font-weight: 500;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 4px;
      transition: all 0.15s ease;
    }
    .tool-btn:hover { background: var(--border-highlight); border-color: var(--accent-cyan); }
    .zoom-text {
      font-size: 0.8rem;
      color: var(--accent-cyan);
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      min-width: 44px;
      text-align: center;
    }

    .controls-right {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .target-select {
      background: var(--bg-panel);
      color: var(--text-main);
      border: 1px solid var(--border-color);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 500;
      outline: none;
      cursor: pointer;
    }
    .target-select:focus { border-color: var(--accent-cyan); }
    .hud-toggle-btn {
      background: rgba(16, 185, 129, 0.12);
      border: 1px solid rgba(16, 185, 129, 0.3);
      color: var(--accent-emerald);
      padding: 6px 12px;
      border-radius: 6px;
      font-size: 0.82rem;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.15s ease;
    }
    .hud-toggle-btn.active {
      background: var(--accent-emerald);
      color: #07090e;
    }

    /* Main Viewport Workspace */
    .workspace {
      flex: 1;
      position: relative;
      background: #000;
      overflow: hidden;
      display: flex;
    }

    /* Mode: Side by Side */
    .side-by-side-layout {
      display: flex;
      width: 100%;
      height: 100%;
    }
    .side-panel {
      flex: 1;
      height: 100%;
      position: relative;
      overflow: hidden;
      border-right: 1px solid #1e293b;
    }
    .side-panel:last-child { border-right: none; }
    .viewport {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      cursor: grab;
      overflow: hidden;
    }
    .viewport:active { cursor: grabbing; }

    .canvas-layer {
      position: absolute;
      top: 0;
      left: 0;
      transform-origin: 0 0;
      will-change: transform;
    }
    .canvas-layer img {
      display: block;
      pointer-events: none;
      max-width: none;
      max-height: none;
    }

    /* Floating Panel Badges */
    .panel-pill {
      position: absolute;
      top: 14px;
      left: 14px;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(8px);
      border: 1px solid rgba(255, 255, 255, 0.12);
      padding: 6px 12px;
      border-radius: 8px;
      pointer-events: none;
      z-index: 10;
      display: flex;
      flex-direction: column;
      gap: 2px;
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    }
    .pill-title {
      font-size: 0.82rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .ref-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-amber); display: inline-block; }
    .ref-dot.raw-dot { background: #f97316; box-shadow: 0 0 6px rgba(249, 115, 22, 0.8); }
    .master-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--accent-cyan); display: inline-block; }
    .pill-meta { font-size: 0.7rem; color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, monospace; }

    /* Crosshairs */
    .crosshair-h, .crosshair-v {
      position: absolute;
      pointer-events: none;
      z-index: 20;
      display: none;
    }
    .crosshair-h {
      left: 0;
      width: 100%;
      height: 1px;
      background: rgba(56, 189, 248, 0.5);
      box-shadow: 0 0 4px rgba(56, 189, 248, 0.8);
    }
    .crosshair-v {
      top: 0;
      height: 100%;
      width: 1px;
      background: rgba(56, 189, 248, 0.5);
      box-shadow: 0 0 4px rgba(56, 189, 248, 0.8);
    }

    /* Mode: Split Slider Layout */
    .split-layout {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      display: none;
    }
    .split-container {
      width: 100%;
      height: 100%;
      position: relative;
      overflow: hidden;
      cursor: grab;
    }
    .split-container:active { cursor: grabbing; }
    .split-clip-layer {
      position: absolute;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      overflow: hidden;
      pointer-events: none;
    }
    .split-divider-line {
      position: absolute;
      top: 0;
      bottom: 0;
      width: 2px;
      background: #38bdf8;
      box-shadow: 0 0 10px rgba(56, 189, 248, 0.8);
      z-index: 30;
      cursor: ew-resize;
      display: flex;
      align-items: center;
      justify-content: center;
    }
    .split-divider-handle {
      width: 38px;
      height: 38px;
      border-radius: 50%;
      background: #0f172a;
      border: 2px solid #38bdf8;
      color: #38bdf8;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.75rem;
      font-weight: 700;
      box-shadow: 0 2px 10px rgba(0, 0, 0, 0.6);
      user-select: none;
      letter-spacing: -2px;
    }

    /* Mode: Blink Toggle Layout */
    .blink-layout {
      width: 100%;
      height: 100%;
      position: absolute;
      top: 0;
      left: 0;
      display: none;
    }
    .blink-indicator {
      position: absolute;
      bottom: 45px;
      left: 50%;
      transform: translateX(-50%);
      background: rgba(15, 23, 42, 0.88);
      backdrop-filter: blur(10px);
      border: 1px solid var(--border-color);
      padding: 8px 18px;
      border-radius: 9999px;
      font-size: 0.85rem;
      font-weight: 600;
      display: flex;
      align-items: center;
      gap: 8px;
      z-index: 40;
      box-shadow: 0 4px 16px rgba(0, 0, 0, 0.6);
    }

    /* HUD Metrics Drawer */
    .hud-panel {
      position: absolute;
      bottom: 38px;
      right: 18px;
      width: 360px;
      max-height: calc(100vh - 120px);
      overflow-y: auto;
      background: rgba(15, 23, 42, 0.92);
      backdrop-filter: blur(16px);
      border: 1px solid var(--border-highlight);
      border-radius: 12px;
      padding: 16px;
      z-index: 50;
      display: flex;
      flex-direction: column;
      gap: 14px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.7);
      transition: opacity 0.2s ease, transform 0.2s ease;
    }
    .hud-panel.hidden {
      opacity: 0;
      transform: translateY(12px);
      pointer-events: none;
    }
    .hud-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 8px;
    }
    .hud-title { font-size: 0.88rem; font-weight: 700; color: var(--text-main); }
    .hud-card {
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid var(--border-color);
      border-radius: 8px;
      padding: 10px 12px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .card-title {
      font-size: 0.75rem;
      font-weight: 700;
      color: var(--accent-cyan);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .metric-row {
      display: flex;
      justify-content: space-between;
      font-size: 0.78rem;
    }
    .metric-label { color: var(--text-muted); }
    .metric-val {
      color: var(--text-main);
      font-family: ui-monospace, SFMono-Regular, monospace;
      font-weight: 600;
    }
    .status-badge {
      padding: 1px 6px;
      border-radius: 4px;
      font-size: 0.7rem;
      font-weight: 700;
    }
    .status-excellent { background: rgba(16, 185, 129, 0.15); color: #34d399; }
    .status-good { background: rgba(56, 189, 248, 0.15); color: #38bdf8; }
    .status-warning { background: rgba(245, 158, 11, 0.15); color: #fbbf24; }

    /* Bottom Status Bar */
    .statusbar {
      height: 28px;
      background: var(--bg-surface);
      border-top: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 14px;
      font-size: 0.75rem;
      color: var(--text-muted);
      z-index: 100;
    }
    .shortcut-hints { display: flex; gap: 14px; }
    .kbd {
      background: var(--bg-panel);
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 1px 5px;
      border-radius: 4px;
      font-family: monospace;
      font-size: 0.72rem;
    }
  </style>
</head>
<body>

  <!-- Top Toolbar -->
  <header class="toolbar">
    <div class="brand-section">
      <span class="brand-logo">🌙</span>
      <span class="brand-title">Siril Moon Stacking</span>
      <span class="badge-sub">同步审验器</span>
    </div>

    <div class="controls-center">
      <div class="btn-group">
        <button id="btn-mode-side" class="active" title="双窗并排联动视图 (Tab)">🔲 双窗并排 (Side-by-Side)</button>
        <button id="btn-mode-split" title="卷帘滑动对比视图 (Tab)">↔️ 卷帘对比 (Split Slider)</button>
        <button id="btn-mode-blink" title="极速闪烁交替视图 (Space)">👁️ 闪烁交替 (Blink)</button>
      </div>

      <div style="width: 1px; height: 20px; background: var(--border-color); margin: 0 4px;"></div>

      <div class="btn-group" id="ref-source-group" title="切换左侧参考图源 (快捷键 R)">
        <button id="btn-ref-aligned" class="active" title="亚像素坐标配准版，与成片严格重合，专用于卷帘与闪烁微观细节对比 (R)">🎯 对齐对比帧</button>
        <button id="btn-ref-raw" title="相机原生未裁剪画幅，无仿射平移、无扩边留白、无消光，真实还原单帧拍摄底片 (R)">📷 原生真实 RAW</button>
      </div>

      <div style="width: 1px; height: 20px; background: var(--border-color); margin: 0 4px;"></div>

      <button id="btn-zoom-fit" class="tool-btn" title="适屏全貌 (0 / F)">适屏</button>
      <button id="btn-zoom-100" class="tool-btn" title="100% 原始物理像素 (1)">100%</button>
      <button id="btn-zoom-200" class="tool-btn" title="200% 高频细节超采样 (2)">200%</button>
      <button id="btn-center" class="tool-btn" title="月盘一键居中 (C)">🎯 居中</button>
      <span id="zoom-display" class="zoom-text">100%</span>
    </div>

    <div class="controls-right">
      <select id="target-dropdown" class="target-select"></select>
      <button id="btn-hud-toggle" class="hud-toggle-btn" title="显示/隐藏质检 HUD (H)">📊 质检 HUD</button>
    </div>
  </header>

  <!-- Workspace Area -->
  <main class="workspace" id="workspace">

    <!-- Mode 1: Side by Side -->
    <div id="side-layout" class="side-by-side-layout">
      <!-- Left Panel: Reference -->
      <div class="side-panel" id="panel-left">
        <div class="panel-pill">
          <div class="pill-title" id="ref-title-text"><span class="ref-dot"></span>单帧配准版 (Aligned Ref)</div>
          <div class="pill-meta" id="ref-meta-text">1:1 亚像素对齐 · 等度 MTF · 专用于卷帘/闪烁微观比对</div>
        </div>
        <div class="viewport" id="viewport-left">
          <div class="canvas-layer" id="layer-left">
            <img id="img-left" src="" alt="参考单帧">
          </div>
          <div class="crosshair-h" id="ch-left-h"></div>
          <div class="crosshair-v" id="ch-left-v"></div>
        </div>
      </div>

      <!-- Right Panel: Master -->
      <div class="side-panel" id="panel-right">
        <div class="panel-pill">
          <div class="pill-title"><span class="master-dot"></span>堆叠增强成片 (Master)</div>
          <div class="pill-meta" id="master-meta-text">Siril 剔除均值高动态堆叠</div>
        </div>
        <div class="viewport" id="viewport-right">
          <div class="canvas-layer" id="layer-right">
            <img id="img-right" src="" alt="堆叠成片">
          </div>
          <div class="crosshair-h" id="ch-right-h"></div>
          <div class="crosshair-v" id="ch-right-v"></div>
        </div>
      </div>
    </div>

    <!-- Mode 2: Split Slider -->
    <div id="split-layout" class="split-layout">
      <div class="split-container" id="viewport-split">
        <div class="canvas-layer" id="layer-split-ref">
          <img id="img-split-ref" src="" alt="参考单帧">
        </div>
        <div class="split-clip-layer" id="split-clip-container">
          <div class="canvas-layer" id="layer-split-master">
            <img id="img-split-master" src="" alt="堆叠成片">
          </div>
        </div>
        <div class="split-divider-line" id="split-divider">
          <div class="split-divider-handle">◀ ｜ ▶</div>
        </div>
      </div>
    </div>

    <!-- Mode 3: Blink Toggle -->
    <div id="blink-layout" class="blink-layout">
      <div class="viewport" id="viewport-blink">
        <div class="canvas-layer" id="layer-blink">
          <img id="img-blink" src="" alt="闪烁视图">
        </div>
        <div class="blink-indicator" id="blink-indicator">
          <span>当前显示:</span>
          <strong id="blink-status-text" style="color: var(--accent-cyan);">堆叠增强成片</strong>
          <span style="font-size: 0.72rem; color: var(--text-muted);">(按空格键快速交替)</span>
        </div>
      </div>
    </div>

    <!-- HUD Drawer Panel (Default Hidden) -->
    <aside class="hud-panel hidden" id="hud-panel">
      <div class="hud-header">
        <div class="hud-title">🌙 月面质检与光学参数 HUD</div>
        <span class="badge-sub" id="hud-badge-device">光学母版</span>
      </div>

      <!-- Card 1: Optics -->
      <div class="hud-card">
        <div class="card-title">🔭 光学系统与设备</div>
        <div class="metric-row"><span class="metric-label">拍摄设备</span><span class="metric-val" id="hud-device">-</span></div>
        <div class="metric-row"><span class="metric-label">焦距 / 口径</span><span class="metric-val" id="hud-optics-fl">-</span></div>
        <div class="metric-row"><span class="metric-label">焦比 / 艾里斑半径</span><span class="metric-val" id="hud-optics-airy">-</span></div>
      </div>

      <!-- Card 2: Stacking & Drizzle -->
      <div class="hud-card">
        <div class="card-title">🔄 选帧堆叠与 Drizzle</div>
        <div class="metric-row"><span class="metric-label">叠加帧数</span><span class="metric-val" id="hud-stack-frames">-</span></div>
        <div class="metric-row"><span class="metric-label">选帧保留率</span><span class="metric-val" id="hud-stack-ratio">-</span></div>
        <div class="metric-row"><span class="metric-label">Drizzle 超采样</span><span class="metric-val" id="hud-drizzle">-</span></div>
      </div>

      <!-- Card 3: Quality Metrics -->
      <div class="hud-card">
        <div class="card-title">💎 伪影与质感量化指标</div>
        <div class="metric-row"><span class="metric-label">防过锐 / 增强保留率</span><span class="metric-val" id="hud-sharp-guard">-</span></div>
        <div class="metric-row"><span class="metric-label">自适应小波 / 可信覆盖率</span><span class="metric-val" id="hud-wavelet-adapt">-</span></div>
        <div class="metric-row"><span class="metric-label">暗环比率 (DHR)</span><span class="metric-val" id="hud-dhr">-</span></div>
        <div class="metric-row"><span class="metric-label">高光白垩指数 (CSI)</span><span class="metric-val" id="hud-csi">-</span></div>
        <div class="metric-row"><span class="metric-label">梯度峰度脆裂度 (GKM)</span><span class="metric-val" id="hud-gkm">-</span></div>
        <div class="metric-row"><span class="metric-label">深空残噪标准差</span><span class="metric-val" id="hud-noise">-</span></div>
      </div>

      <!-- Card 4: Color Profile -->
      <div class="hud-card">
        <div class="card-title">🎨 色彩校准与地质矿物</div>
        <div class="metric-row"><span class="metric-label">灰世界白平衡 (kr, kb)</span><span class="metric-val" id="hud-wb">-</span></div>
        <div class="metric-row"><span class="metric-label">MTF 中间调拉伸</span><span class="metric-val" id="hud-mtf">-</span></div>
        <div class="metric-row"><span class="metric-label">钛(Ti) / 铁(Fe) 饱和度增益</span><span class="metric-val" id="hud-mineral-boost">-</span></div>
      </div>
    </aside>

  </main>

  <!-- Bottom Status Bar -->
  <footer class="statusbar">
    <div class="shortcut-hints">
      <span><span class="kbd">Space</span> 闪烁</span>
      <span><span class="kbd">R</span> 切换对齐/原生RAW</span>
      <span><span class="kbd">1</span> 100%</span>
      <span><span class="kbd">2</span> 200%</span>
      <span><span class="kbd">0</span> 适屏</span>
      <span><span class="kbd">C</span> 居中</span>
      <span><span class="kbd">Tab</span> 切换视图</span>
      <span><span class="kbd">M</span> 切换成片</span>
      <span><span class="kbd">H</span> 质检面板</span>
    </div>
    <div id="footer-dimensions">就绪</div>
  </footer>

  <script>
    const IMAGES = """ + manifest_json + """;
    const METRICS = """ + hud_json + """;

    // Application state
    const state = {
      mode: 'side-by-side', // 'side-by-side' | 'split' | 'blink'
      refSource: 'aligned', // 'aligned' | 'raw'
      zoom: 1.0,
      panX: 0,
      panY: 0,
      isDragging: false,
      dragStartX: 0,
      dragStartY: 0,
      activeTarget: null,
      splitPercent: 50.0,
      isSplitting: false,
      blinkIsRef: false,
      imgWidth: 1000,
      imgHeight: 1000,
    };

    // DOM Elements
    const dom = {
      sideLayout: document.getElementById('side-layout'),
      splitLayout: document.getElementById('split-layout'),
      blinkLayout: document.getElementById('blink-layout'),
      hudPanel: document.getElementById('hud-panel'),
      zoomDisplay: document.getElementById('zoom-display'),
      targetDropdown: document.getElementById('target-dropdown'),
      splitDivider: document.getElementById('split-divider'),
      splitClipContainer: document.getElementById('split-clip-container'),
      blinkStatusText: document.getElementById('blink-status-text'),
      footerDim: document.getElementById('footer-dimensions'),

      // Reference source toggles
      btnRefAligned: document.getElementById('btn-ref-aligned'),
      btnRefRaw: document.getElementById('btn-ref-raw'),
      refTitleText: document.getElementById('ref-title-text'),
      refMetaText: document.getElementById('ref-meta-text'),

      // Images
      imgLeft: document.getElementById('img-left'),
      imgRight: document.getElementById('img-right'),
      imgSplitRef: document.getElementById('img-split-ref'),
      imgSplitMaster: document.getElementById('img-split-master'),
      imgBlink: document.getElementById('img-blink'),

      // Layers
      layerLeft: document.getElementById('layer-left'),
      layerRight: document.getElementById('layer-right'),
      layerSplitRef: document.getElementById('layer-split-ref'),
      layerSplitMaster: document.getElementById('layer-split-master'),
      layerBlink: document.getElementById('layer-blink'),

      // Crosshairs
      chLeftH: document.getElementById('ch-left-h'),
      chLeftV: document.getElementById('ch-left-v'),
      chRightH: document.getElementById('ch-right-h'),
      chRightV: document.getElementById('ch-right-v'),
    };

    // Initialize Dropdown & Images
    function initTargets() {
      dom.targetDropdown.innerHTML = '';
      if (!IMAGES.targets || IMAGES.targets.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No product images found';
        dom.targetDropdown.appendChild(opt);
        return;
      }

      IMAGES.targets.forEach((t, idx) => {
        const opt = document.createElement('option');
        opt.value = t.file;
        opt.textContent = t.name;
        if (idx === 0) opt.selected = true;
        dom.targetDropdown.appendChild(opt);
      });

      state.activeTarget = IMAGES.targets[0];
      updateImageSources();
    }

    function updateImageSources() {
      if (!state.activeTarget) return;

      const isSquare = state.activeTarget.square;
      let refSrc = '';
      let titleHtml = '';
      let metaText = '';

      if (state.refSource === 'raw' && IMAGES.rawOriginal) {
        refSrc = IMAGES.rawOriginal;
        titleHtml = '<span class="ref-dot raw-dot"></span>相机原生 RAW (True Raw As-Shot)';
        metaText = '原生未加工底片 · 原生画幅 · 无裁切扩边仿射';
      } else if (isSquare && IMAGES.referenceSquare) {
        refSrc = IMAGES.referenceSquare;
        titleHtml = '<span class="ref-dot"></span>单帧配准版 (Aligned Square)';
        metaText = '1:1 方形特写配准 · 视宁度第 1 名';
      } else {
        refSrc = IMAGES.reference || (state.activeTarget ? state.activeTarget.file : '');
        titleHtml = '<span class="ref-dot"></span>单帧配准版 (Aligned Ref)';
        metaText = '1:1 亚像素对齐 · 等度 MTF · 专用于卷帘/闪烁微观比对';
      }

      if (dom.refTitleText) dom.refTitleText.innerHTML = titleHtml;
      if (dom.refMetaText) dom.refMetaText.textContent = metaText;

      const masterSrc = state.activeTarget.file;

      dom.imgLeft.src = refSrc;
      dom.imgRight.src = masterSrc;
      dom.imgSplitRef.src = refSrc;
      dom.imgSplitMaster.src = masterSrc;
      dom.imgBlink.src = state.blinkIsRef ? refSrc : masterSrc;

      // Track loaded dimensions from master
      const testImg = new Image();
      testImg.onload = () => {
        state.imgWidth = testImg.naturalWidth;
        state.imgHeight = testImg.naturalHeight;
        dom.footerDim.textContent = `${state.imgWidth} × ${state.imgHeight} px · ${state.activeTarget.name}`;
        zoomFit();
      };
      testImg.src = masterSrc;
    }

    // Transform application
    function applyTransform() {
      const transform = `translate3d(${state.panX}px, ${state.panY}px, 0) scale(${state.zoom})`;

      dom.layerLeft.style.transform = transform;
      dom.layerRight.style.transform = transform;
      dom.layerSplitRef.style.transform = transform;
      dom.layerSplitMaster.style.transform = transform;
      dom.layerBlink.style.transform = transform;

      dom.zoomDisplay.textContent = `${Math.round(state.zoom * 100)}%`;
      updateSplitClip();
    }

    function updateSplitClip() {
      if (state.mode !== 'split') return;
      const pct = state.splitPercent;
      dom.splitDivider.style.left = `${pct}%`;
      dom.splitClipContainer.style.clipPath = `polygon(${pct}% 0, 100% 0, 100% 100%, ${pct}% 100%)`;
    }

    // Zoom Helpers
    function setZoom(newZoom, centerX, centerY) {
      newZoom = Math.max(0.05, Math.min(newZoom, 12.0));
      if (!centerX || !centerY) {
        const vp = getCurrentViewport();
        const rect = vp.getBoundingClientRect();
        centerX = rect.width / 2;
        centerY = rect.height / 2;
      }
      state.panX = centerX - (centerX - state.panX) * (newZoom / state.zoom);
      state.panY = centerY - (centerY - state.panY) * (newZoom / state.zoom);
      state.zoom = newZoom;
      applyTransform();
    }

    function zoomFit() {
      const vp = getCurrentViewport();
      const rect = vp.getBoundingClientRect();
      if (!rect.width || !rect.height || !state.imgWidth) return;

      const scaleX = rect.width / state.imgWidth;
      const scaleY = rect.height / state.imgHeight;
      const fitZoom = Math.min(scaleX, scaleY) * 0.94;

      state.zoom = fitZoom;
      state.panX = (rect.width - state.imgWidth * fitZoom) / 2;
      state.panY = (rect.height - state.imgHeight * fitZoom) / 2;
      applyTransform();
    }

    function centerMoon() {
      const vp = getCurrentViewport();
      const rect = vp.getBoundingClientRect();
      state.panX = (rect.width - state.imgWidth * state.zoom) / 2;
      state.panY = (rect.height - state.imgHeight * state.zoom) / 2;
      applyTransform();
    }

    function getCurrentViewport() {
      if (state.mode === 'side-by-side') return document.getElementById('viewport-left');
      if (state.mode === 'split') return document.getElementById('viewport-split');
      return document.getElementById('viewport-blink');
    }

    // View Mode Switching
    function setMode(newMode) {
      state.mode = newMode;

      dom.sideLayout.style.display = newMode === 'side-by-side' ? 'flex' : 'none';
      dom.splitLayout.style.display = newMode === 'split' ? 'block' : 'none';
      dom.blinkLayout.style.display = newMode === 'blink' ? 'block' : 'none';

      document.getElementById('btn-mode-side').classList.toggle('active', newMode === 'side-by-side');
      document.getElementById('btn-mode-split').classList.toggle('active', newMode === 'split');
      document.getElementById('btn-mode-blink').classList.toggle('active', newMode === 'blink');

      zoomFit();
    }

    // Blink Toggle Logic
    function toggleBlink() {
      if (state.mode !== 'blink') {
        setMode('blink');
        return;
      }
      state.blinkIsRef = !state.blinkIsRef;
      const isSquare = state.activeTarget && state.activeTarget.square;
      let refSrc = '';
      if (state.refSource === 'raw' && IMAGES.rawOriginal) {
        refSrc = IMAGES.rawOriginal;
      } else if (isSquare && IMAGES.referenceSquare) {
        refSrc = IMAGES.referenceSquare;
      } else {
        refSrc = IMAGES.reference || (state.activeTarget ? state.activeTarget.file : '');
      }
      const masterSrc = state.activeTarget ? state.activeTarget.file : '';

      dom.imgBlink.src = state.blinkIsRef ? refSrc : masterSrc;
      dom.blinkStatusText.textContent = state.blinkIsRef
        ? (state.refSource === 'raw' ? '相机原生 RAW (未加工底片)' : '单帧配准版 (1:1对齐)')
        : '堆叠增强成片 (Master)';
      dom.blinkStatusText.style.color = state.blinkIsRef ? 'var(--accent-amber)' : 'var(--accent-cyan)';
    }

    // Event Listeners: Pan & Zoom
    function bindViewportEvents(vp) {
      vp.addEventListener('mousedown', (e) => {
        if (state.isSplitting) return;
        state.isDragging = true;
        state.dragStartX = e.clientX - state.panX;
        state.dragStartY = e.clientY - state.panY;
      });

      vp.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = vp.getBoundingClientRect();
        const cursorX = e.clientX - rect.left;
        const cursorY = e.clientY - rect.top;
        const factor = e.deltaY < 0 ? 1.15 : (1 / 1.15);
        setZoom(state.zoom * factor, cursorX, cursorY);
      }, { passive: false });

      vp.addEventListener('dblclick', () => {
        if (Math.abs(state.zoom - 1.0) < 0.1) zoomFit();
        else setZoom(1.0);
      });
    }

    window.addEventListener('mousemove', (e) => {
      if (state.isDragging) {
        state.panX = e.clientX - state.dragStartX;
        state.panY = e.clientY - state.dragStartY;
        applyTransform();
      }

      if (state.isSplitting) {
        const vp = dom.splitLayout.getBoundingClientRect();
        const offsetX = e.clientX - vp.left;
        state.splitPercent = Math.max(0, Math.min(100, (offsetX / vp.width) * 100));
        updateSplitClip();
      }

      // Update Synchronized Crosshair in Side-by-Side
      if (state.mode === 'side-by-side') {
        const vpL = document.getElementById('viewport-left').getBoundingClientRect();
        const vpR = document.getElementById('viewport-right').getBoundingClientRect();

        let relX = -1, relY = -1;
        if (e.clientX >= vpL.left && e.clientX <= vpL.right && e.clientY >= vpL.top && e.clientY <= vpL.bottom) {
          relX = e.clientX - vpL.left;
          relY = e.clientY - vpL.top;
        } else if (e.clientX >= vpR.left && e.clientX <= vpR.right && e.clientY >= vpR.top && e.clientY <= vpR.bottom) {
          relX = e.clientX - vpR.left;
          relY = e.clientY - vpR.top;
        }

        if (relX >= 0) {
          dom.chLeftH.style.display = 'block';
          dom.chLeftV.style.display = 'block';
          dom.chRightH.style.display = 'block';
          dom.chRightV.style.display = 'block';

          dom.chLeftH.style.top = `${relY}px`;
          dom.chLeftV.style.left = `${relX}px`;
          dom.chRightH.style.top = `${relY}px`;
          dom.chRightV.style.left = `${relX}px`;
        } else {
          dom.chLeftH.style.display = 'none';
          dom.chLeftV.style.display = 'none';
          dom.chRightH.style.display = 'none';
          dom.chRightV.style.display = 'none';
        }
      }
    });

    window.addEventListener('mouseup', () => {
      state.isDragging = false;
      state.isSplitting = false;
    });

    // Split Divider Dragging
    dom.splitDivider.addEventListener('mousedown', (e) => {
      e.stopPropagation();
      state.isSplitting = true;
    });

    // UI Buttons Binding
    document.getElementById('btn-mode-side').addEventListener('click', () => setMode('side-by-side'));
    document.getElementById('btn-mode-split').addEventListener('click', () => setMode('split'));
    document.getElementById('btn-mode-blink').addEventListener('click', () => setMode('blink'));

    if (dom.btnRefAligned) {
      dom.btnRefAligned.addEventListener('click', () => {
        state.refSource = 'aligned';
        dom.btnRefAligned.classList.add('active');
        if (dom.btnRefRaw) dom.btnRefRaw.classList.remove('active');
        updateImageSources();
      });
    }
    if (dom.btnRefRaw) {
      dom.btnRefRaw.addEventListener('click', () => {
        state.refSource = 'raw';
        dom.btnRefRaw.classList.add('active');
        if (dom.btnRefAligned) dom.btnRefAligned.classList.remove('active');
        updateImageSources();
      });
    }

    document.getElementById('btn-zoom-fit').addEventListener('click', zoomFit);
    document.getElementById('btn-zoom-100').addEventListener('click', () => setZoom(1.0));
    document.getElementById('btn-zoom-200').addEventListener('click', () => setZoom(2.0));
    document.getElementById('btn-center').addEventListener('click', centerMoon);

    dom.targetDropdown.addEventListener('change', (e) => {
      const match = IMAGES.targets.find(t => t.file === e.target.value);
      if (match) {
        state.activeTarget = match;
        updateImageSources();
      }
    });

    document.getElementById('btn-hud-toggle').addEventListener('click', () => {
      const btn = document.getElementById('btn-hud-toggle');
      dom.hudPanel.classList.toggle('hidden');
      btn.classList.toggle('active', !dom.hudPanel.classList.contains('hidden'));
    });

    // Keyboard Shortcuts
    window.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return;

      if (e.code === 'Space') {
        e.preventDefault();
        toggleBlink();
      } else if (e.key.toLowerCase() === 'r') {
        state.refSource = (state.refSource === 'aligned') ? 'raw' : 'aligned';
        if (dom.btnRefAligned) dom.btnRefAligned.classList.toggle('active', state.refSource === 'aligned');
        if (dom.btnRefRaw) dom.btnRefRaw.classList.toggle('active', state.refSource === 'raw');
        updateImageSources();
      } else if (e.key === '1') {
        setZoom(1.0);
      } else if (e.key === '2') {
        setZoom(2.0);
      } else if (e.key === '0' || e.key.toLowerCase() === 'f') {
        zoomFit();
      } else if (e.key.toLowerCase() === 'c') {
        centerMoon();
      } else if (e.key.toLowerCase() === 'h') {
        document.getElementById('btn-hud-toggle').click();
      } else if (e.key === 'Tab') {
        e.preventDefault();
        if (state.mode === 'side-by-side') setMode('split');
        else if (state.mode === 'split') setMode('blink');
        else setMode('side-by-side');
      } else if (e.key.toLowerCase() === 'm') {
        // Cycle Target master
        const idx = IMAGES.targets.findIndex(t => t.file === state.activeTarget.file);
        const nextIdx = (idx + 1) % IMAGES.targets.length;
        state.activeTarget = IMAGES.targets[nextIdx];
        dom.targetDropdown.value = state.activeTarget.file;
        updateImageSources();
      }
    });

    // Populate HUD Metrics
    function populateHUD() {
      if (!METRICS) return;

      const dev = METRICS.device || {};
      document.getElementById('hud-device').textContent = dev.name || '未知设备';
      if (dev.name) document.getElementById('hud-badge-device').textContent = dev.name;

      const opt = METRICS.optics || {};
      const fl = opt.focal_length ? `${opt.focal_length.toFixed(1)}mm` : '未提供';
      const dia = opt.aperture ? `D=${opt.aperture.toFixed(0)}mm` : '';
      document.getElementById('hud-optics-fl').textContent = `${fl} ${dia}`.trim() || '几何反推';

      const fr = opt.f_ratio ? `F/${opt.f_ratio}` : '';
      const airy = opt.airy_r ? `艾里斑 r=${opt.airy_r}px` : '';
      document.getElementById('hud-optics-airy').textContent = `${fr} ${airy}`.trim() || '衍射极限';

      const stk = METRICS.stacking || {};
      const methodZh = (stk.method === 'sum') ? '直接累加' : '剔除均值 (Rej Mean)';
      document.getElementById('hud-stack-frames').textContent = stk.stacked_frames ? `${stk.stacked_frames} 帧 (${methodZh})` : '自动堆叠';
      document.getElementById('hud-stack-ratio').textContent = stk.ratio ? `${stk.ratio} (共 ${stk.total_frames || stk.registered_frames} 帧)` : 'Otsu 聚类';

      const drz = METRICS.drizzle || {};
      document.getElementById('hud-drizzle').textContent = drz.enabled ? `${drz.scale}× (${drz.kernel || 'square'} 核)` : '原生采样 (关闭)';

      const q = METRICS.quality || {};
      const guard = q.sharp_guard || {};
      const guardLabels = {accepted: '通过', reduced: '已降档', fallback: '已回退基准', off: '已关闭', unknown: '未知'};
      document.getElementById('hud-sharp-guard').textContent = `${guardLabels[guard.status] || '未知'}${guard.alpha == null ? '' : ' / ' + Math.round(guard.alpha * 100) + '%'}`;
      const adaptation = q.wavelet_adaptation || {};
      const adaptationLabels = {applied: '已分配', unavailable: '证据不足', bypassed: '已旁路', off: '已关闭', unknown: '未知'};
      document.getElementById('hud-wavelet-adapt').textContent = `${adaptationLabels[adaptation.status] || '未知'}${adaptation.support_coverage == null ? '' : ' / ' + (adaptation.support_coverage * 100).toFixed(1) + '%'}`;
      if (q.dhr !== undefined && q.dhr !== null) {
        let dhrZh = '受控';
        let dhrClass = 'status-good';
        if (q.dhr < 0.02) { dhrZh = '极佳 (无暗环)'; dhrClass = 'status-excellent'; }
        else if (q.dhr >= 0.035) { dhrZh = '警告 (边缘暗环)'; dhrClass = 'status-warning'; }
        document.getElementById('hud-dhr').innerHTML = `${Number(q.dhr).toFixed(4)} <span class="status-badge ${dhrClass}">${dhrZh}</span>`;
      }
      if (q.csi !== undefined && q.csi !== null) {
        let csiZh = '极佳 (高光动态保留)';
        let csiClass = 'status-excellent';
        if (q.csi >= 0.025) { csiZh = '警告 (高光白垩化)'; csiClass = 'status-warning'; }
        else if (q.csi >= 0.015) { csiZh = '良好'; csiClass = 'status-good'; }
        document.getElementById('hud-csi').innerHTML = `${Number(q.csi).toFixed(4)} <span class="status-badge ${csiClass}">${csiZh}</span>`;
      }
      if (q.gkm !== undefined && q.gkm !== null) {
        let gkmZh = '温润纯镜感 (Organic)';
        let gkmClass = 'status-good';
        if (q.gkm >= 14.0) { gkmZh = '警告 (毛刺脆裂)'; gkmClass = 'status-warning'; }
        else if (q.gkm >= 6.0) { gkmZh = '清晰微反差 (Crisp)'; gkmClass = 'status-good'; }
        document.getElementById('hud-gkm').innerHTML = `${Number(q.gkm).toFixed(2)} <span class="status-badge ${gkmClass}">${gkmZh}</span>`;
      }
      if (q.bg_noise_std !== undefined && q.bg_noise_std !== null) {
        const noiseStatusZh = (q.bg_noise_status === 'measured') ? '实测' : (q.bg_noise_status || '实测');
        document.getElementById('hud-noise').textContent = `${Number(q.bg_noise_std).toFixed(5)} (${noiseStatusZh})`;
      }

      const col = METRICS.color || {};
      const kr = col.wb_kr ? Number(col.wb_kr).toFixed(3) : '1.00';
      const kb = col.wb_kb ? Number(col.wb_kb).toFixed(3) : '1.00';
      document.getElementById('hud-wb').textContent = `kr=${kr}, kb=${kb}`;
      document.getElementById('hud-mtf').textContent = col.midtone ? Number(col.midtone).toFixed(2) : '0.13';

      const ti = col.ti_boost ? `钛(Ti) ×${Number(col.ti_boost).toFixed(1)}` : '';
      const fe = col.fe_boost ? `铁(Fe) ×${Number(col.fe_boost).toFixed(1)}` : '';
      document.getElementById('hud-mineral-boost').textContent = `${ti} ${fe}`.trim() || '标准 LRGB';
    }

    // Init
    window.addEventListener('load', () => {
      bindViewportEvents(document.getElementById('viewport-left'));
      bindViewportEvents(document.getElementById('viewport-right'));
      bindViewportEvents(document.getElementById('viewport-split'));
      bindViewportEvents(document.getElementById('viewport-blink'));

      initTargets();
      populateHUD();
    });

    window.addEventListener('resize', zoomFit);
  </script>
</body>
</html>
"""

    out_html.write_text(html_template, encoding="utf-8")
    log(f"comparison viewer generated: {out_html.name}")
    return out_html

