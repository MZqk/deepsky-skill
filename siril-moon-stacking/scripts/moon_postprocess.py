"""Postprocessing pipeline module for siril-moon-stacking.

Handles linear pedestal subtraction, lunar limb glare suppression, Gray-World neutral
white balancing, atmospheric extinction gradient compensation, MTF midtone stretching,
L/RGB split & synthesis, authentic continuous mineral moon chrominance rendering,
adaptive sky margins, and mosaic profile export.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import hashlib
import math
from contextlib import ExitStack
from pathlib import Path
import cv2
import numpy as np
from astropy.io import fits

from moon_common import (
    log,
    die,
    load_json,
    dump_json,
    run_siril_script,
    _px,
    _odd,
    _resolve_px_scale,
    _estimate_pedestal,
    _pixel_full_scale,
    _disk_available,
)
from moon_optics import (
    _fit_lunar_limb_circle,
    _infer_optical_parameters,
)
from moon_register import (
    align_rgb_channels,
)
from moon_wavelets import (
    _guard_luminance,
    _compute_edge_ringing_damping_mask,
    _apply_anti_ringing_damping,
    _run_wavelet_adaptation,
)
from moon_sharp_guard import (
    _sharp_guard_context,
    _sharp_guard_metrics,
    _sharp_guard_reasons,
    _select_guarded_sharpening,
    _estimate_adaptive_midtone,
    _estimate_adaptive_sharpening,
    _deconvolution_choice,
)
from moon_verify import (
    _verify_sharp_guard,
    _apply_adaptive_sky_margins,
)
from moon_viewer import (
    _render_reference_frame,
    _generate_comparison_html,
)

def _suppress_lunar_limb_glare(
    planes: np.ndarray,
    glare_mode: str = "auto",
    px_scale: float = 1.0,
) -> tuple[np.ndarray, dict]:
    """Suppress atmospheric and optical forward scattering glare outside lunar physical limb.

    Operates strictly in 32-bit linear space before non-linear MTF stretch.
    Leaves lunar surface (r <= R + 1px) 100% unaltered.
    Smoothly transitions over a narrow delta (2.5px to 8.0px) into deep space zero.

    `px_scale` is the drizzle supersampling factor; the transition width and the
    limb guard band are native-pixel quantities and scale with it.
    """
    if glare_mode == "off":
        return planes, {"active": False}

    is_3d = (planes.ndim == 3)
    if is_3d:
        lum_2d = 0.299 * planes[0] + 0.587 * planes[1] + 0.114 * planes[2]
    else:
        lum_2d = planes
    H, W = lum_2d.shape

    fit_res = _fit_lunar_limb_circle(lum_2d, px_scale=px_scale)
    if fit_res is None:
        return planes, {"active": False, "reason": "circle_fit_failed"}

    xc, yc, R, res_std = fit_res

    if glare_mode == "aggressive":
        delta = _px(2.5, px_scale)
    elif glare_mode == "mild":
        delta = _px(8.0, px_scale)
    else:  # "auto"
        delta = _px(4.5, px_scale)

    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - xc)**2 + (yy - yc)**2)

    r0 = R + _px(1.0, px_scale)
    t = np.clip((r_grid - r0) / delta, 0.0, 1.0)
    w_smooth = 1.0 - (3.0 * t**2 - 2.0 * t**3)

    weight = np.ones((H, W), dtype=np.float32)
    apply_mask = r_grid > r0
    weight[apply_mask] = w_smooth[apply_mask]
    outer_space_mask = r_grid > (r0 + delta)
    weight[outer_space_mask] = 0.0

    if is_3d:
        out_planes = planes * weight[np.newaxis, :, :]
    else:
        out_planes = planes * weight

    meta = {
        "active": True,
        "glare_mode": glare_mode,
        "center_x": xc,
        "center_y": yc,
        "radius": R,
        "residual_std": res_std,
        "delta": delta,
    }
    return out_planes.astype(np.float32), meta


def _load_locked_profile(
    lock_profile_path: str | None = None,
    lock_from_dir: str | None = None,
) -> tuple[dict | None, str | None]:
    """Load calibration profile from explicit file or anchor work directory."""
    if lock_profile_path:
        p = Path(lock_profile_path).expanduser().resolve()
        if not p.exists():
            die(f"locked profile file not found: {p}")
        return load_json(p), str(p)

    if lock_from_dir:
        d = Path(lock_from_dir).expanduser().resolve()
        if not d.is_dir():
            die(f"lock-from directory not found: {d}")
        # Search priority: lunar_profile.json -> mosaic_tile_info.json
        p_json = d / "lunar_profile.json"
        if p_json.exists():
            return load_json(p_json), str(p_json)
        t_json = d / "mosaic_tile_info.json"
        if t_json.exists():
            info = load_json(t_json)
            if "calibration_profile" in info:
                return info["calibration_profile"], str(t_json)
            rec = {
                "version": "1.0",
                "source_master": info.get("master"),
                "histogram": {
                    "hi_unified": info.get("hi_unified", info.get("hi_lum", 0.03)),
                    "hi_lum": info.get("hi_unified", info.get("hi_lum", 0.03)),
                    "midtone": info.get("midtone", 0.13),
                },
            }
            return rec, str(t_json)
        die(f"no lunar_profile.json or mosaic_tile_info.json found in anchor directory: {d}")

    return None, None


def _estimate_atmospheric_extinction_gradient(
    net_planes: np.ndarray,
    mask: np.ndarray,
    mode: str = "auto",
) -> dict:
    """Robustly estimate first-order atmospheric extinction spatial gradient.

    Physical basis:
      In low-altitude lunar imaging (altitude < 30°), the airmass gradient across
      the 0.5° lunar disc induces an asymmetric Rayleigh/aerosol extinction slope:
      shorter blue wavelengths attenuate significantly faster towards the horizon than red.
      This produces a macroscopic 'warm/dark horizon, cool/bright zenith' color/luminance tilt.

    Mathematical strategy:
      1. Coarse grid median subsampling: partitions the moon into 16x16 blocks to decouple
         high-frequency crater details and sharp terminator topography.
      2. Log-color-ratio mapping:
         s_B = ln(B / G), s_R = ln(R / G)
         Completely cancels out surface albedo variations (mare vs highlands).
      3. Robust Huber / IRLS 2D planar regression:
         Fits s_c(x, y) = c + alpha_x * nx + alpha_y * ny
         Suppresses local geological anomalies (e.g. localized titanium-rich Mare Tranquillitatis).
      4. Rayleigh physical consistency check:
         Verifies that blue and red slope vectors are negatively collinear (opposing directions).
      5. Significance thresholding:
         If total color-ratio slope < 1.5% across the disc, automatically bypasses (zero disturbance at high altitude).
    """
    import math
    import numpy as np

    if mode == "off" or net_planes.shape[0] != 3:
        return {"active": False, "applied": False, "mode": mode, "reason": "mode off or non-RGB"}

    h, w = net_planes[0].shape
    xc, yc = w / 2.0, h / 2.0
    r_norm = 0.5 * math.hypot(w, h)

    # 1. Coarse grid median subsampling
    n_grid = 16
    gh, gw = max(4, h // n_grid), max(4, w // n_grid)
    grid_pts = []

    for gy in range(n_grid):
        y0 = gy * gh
        y1 = (gy + 1) * gh if gy < n_grid - 1 else h
        for gx in range(n_grid):
            x0 = gx * gw
            x1 = (gx + 1) * gw if gx < n_grid - 1 else w
            sub_m = mask[y0:y1, x0:x1]
            if np.count_nonzero(sub_m) < 30:
                continue
            sub_r = net_planes[0, y0:y1, x0:x1][sub_m]
            sub_g = net_planes[1, y0:y1, x0:x1][sub_m]
            sub_b = net_planes[2, y0:y1, x0:x1][sub_m]

            med_g = float(np.median(sub_g))
            med_r = float(np.median(sub_r))
            med_b = float(np.median(sub_b))
            if med_g <= 1e-4 or med_r <= 1e-4 or med_b <= 1e-4:
                continue

            cell_xc = (x0 + x1) / 2.0
            cell_yc = (y0 + y1) / 2.0
            nx = (cell_xc - xc) / r_norm
            ny = (cell_yc - yc) / r_norm

            l_bg = math.log(med_b / med_g)
            l_rg = math.log(med_r / med_g)
            grid_pts.append((nx, ny, l_bg, l_rg))

    if len(grid_pts) < 12:
        return {"active": False, "applied": False, "mode": mode, "reason": "insufficient valid grid cells (<12)"}

    pts = np.array(grid_pts, dtype=np.float64)
    xs = pts[:, 0]
    ys = pts[:, 1]
    y_bg = pts[:, 2]
    y_rg = pts[:, 3]

    # 2. Robust IRLS Plane Fitting
    def _fit_plane_irls(x_arr, y_arr, target):
        A = np.column_stack([np.ones_like(x_arr), x_arr, y_arr])
        # Initial least squares
        beta = np.linalg.lstsq(A, target, rcond=None)[0]
        for _ in range(5):
            res = target - A @ beta
            mad = np.median(np.abs(res - np.median(res))) + 1e-6
            delta = 1.345 * mad * 1.4826
            weights = np.ones_like(res)
            outliers = np.abs(res) > delta
            weights[outliers] = delta / np.abs(res[outliers])
            W = np.diag(weights)
            try:
                beta = np.linalg.solve(A.T @ W @ A, A.T @ W @ target)
            except np.linalg.LinAlgError:
                break
        return beta  # [intercept, slope_x, slope_y]

    beta_bg = _fit_plane_irls(xs, ys, y_bg)
    beta_rg = _fit_plane_irls(xs, ys, y_rg)

    gx_b, gy_b = float(beta_bg[1]), float(beta_bg[2])
    gx_r, gy_r = float(beta_rg[1]), float(beta_rg[2])

    amp_b = 2.0 * math.hypot(gx_b, gy_b)
    amp_r = 2.0 * math.hypot(gx_r, gy_r)

    # 3. Physical consistency check (Rayleigh negative collinearity)
    denom = (math.hypot(gx_b, gy_b) * math.hypot(gx_r, gy_r)) + 1e-8
    cos_br = (gx_b * gx_r + gy_b * gy_r) / denom

    zenith_angle_deg = math.degrees(math.atan2(gy_b, gx_b)) % 360.0
    confidence = float(np.clip(-cos_br, 0.0, 1.0))

    threshold = 0.015 if mode == "auto" else 0.005
    is_significant = (amp_b >= threshold)

    applied = False
    if mode == "auto":
        # Atmospheric extinction requires high confidence and strict opposing collinearity
        # to avoid misinterpreting intrinsic lunar geological albedo as atmospheric tilt.
        if is_significant and cos_br <= -0.70 and confidence >= 0.70:
            applied = True
    elif mode in ("mild", "aggressive"):
        if is_significant and cos_br < 0.0:
            applied = True

    strength = 0.85
    if mode == "mild":
        strength = 0.50
    elif mode == "aggressive":
        strength = 1.00

    return {
        "active": True,
        "applied": applied,
        "mode": mode,
        "zenith_angle_deg": round(zenith_angle_deg, 2),
        "amp_b": round(amp_b, 4),
        "amp_r": round(amp_r, 4),
        "slope_b": (gx_b, gy_b),
        "slope_r": (gx_r, gy_r),
        "cos_br": round(cos_br, 3),
        "confidence": round(confidence, 3),
        "strength": strength,
        "r_norm": r_norm,
        "center": (xc, yc),
    }


def _apply_extinction_compensation(
    r_net: np.ndarray,
    g_net: np.ndarray,
    b_net: np.ndarray,
    ext_info: dict,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply first-order planar transmission division with flux conservation."""
    import numpy as np

    if not ext_info.get("applied"):
        return r_net, g_net, b_net

    h, w = r_net.shape
    xc, yc = ext_info["center"]
    r_norm = ext_info["r_norm"]
    strength = ext_info.get("strength", 0.85)

    gx_b, gy_b = ext_info["slope_b"]
    gx_r, gy_r = ext_info["slope_r"]

    # Scale slopes by dampening factor
    gx_b_s, gy_b_s = gx_b * strength, gy_b * strength
    gx_r_s, gy_r_s = gx_r * strength, gy_r * strength
    # Luminance flux compensation along zenith axis (approx 40% of blue extinction slope)
    gx_l_s, gy_l_s = gx_b_s * 0.40, gy_b_s * 0.40

    yy, xx = np.mgrid[0:h, 0:w]
    nx = ((xx - xc) / r_norm).astype(np.float32)
    ny = ((yy - yc) / r_norm).astype(np.float32)

    T_b = np.exp(gx_b_s * nx + gy_b_s * ny).astype(np.float32)
    T_r = np.exp(gx_r_s * nx + gy_r_s * ny).astype(np.float32)
    T_l = np.exp(gx_l_s * nx + gy_l_s * ny).astype(np.float32)

    # Flux conservation normalization across lunar disc
    if np.any(mask):
        T_b /= float(np.mean(T_b[mask]))
        T_r /= float(np.mean(T_r[mask]))
        T_l /= float(np.mean(T_l[mask]))

    b_corr = (b_net / (T_b * T_l)).astype(np.float32)
    r_corr = (r_net / (T_r * T_l)).astype(np.float32)
    g_corr = (g_net / T_l).astype(np.float32)

    return r_corr, g_corr, b_corr


def _calculate_channel_balance(
    d: np.ndarray,
    bg_r: float,
    bg_g: float,
    bg_b: float,
    mid_val: float | None = None,
    wb_mode: str = "gray-world",
    glare_mode: str = "auto",
    extinction_comp: str = "auto",
    locked_profile: dict | None = None,
    lock_wb: tuple[float, float] | None = None,
    lock_stretch: tuple[float, float] | None = None,
    px_scale: float = 1.0,
) -> dict:
    """Calculate channel white point stretch parameters with neutral Gray-World balance.

    In astronomical imaging, white balance must be performed linearly before non-linear MTF stretch.
    Applying independent non-linear MTF stretches with different white/black points causes non-linear
    color divergence, destroying color fidelity in shadows and generating purple fringing along
    high-contrast boundaries (the lunar limb).

    Supports Global Profile Locking (P2) for mosaic multi-panel stitching:
    Locks histogram stretch ceiling (hi_lum) and linear white balance gains (k_R, k_B)
    to eliminate seam brightness stepping and patch chrominance drift across panels.

    Supports Atmospheric Extinction Gradient Compensation (First-order planar field compensation).
    """
    import numpy as np

    p999_r = float(np.percentile(d[0], 99.95))
    p999_g = float(np.percentile(d[1], 99.95))
    p999_b = float(np.percentile(d[2], 99.95))

    hi_r_raw = max(p999_r * 1.10, bg_r + 0.01)
    hi_g_raw = max(p999_g * 1.10, bg_g + 0.01)
    hi_b_raw = max(p999_b * 1.10, bg_b + 0.01)

    lum_legacy = (0.299 * d[0] + 0.587 * d[1] + 0.114 * d[2]).astype(np.float32)
    bg_lum_legacy = _estimate_pedestal(lum_legacy)
    p999_lum_legacy = float(np.percentile(lum_legacy, 99.95))
    hi_lum_legacy = max(p999_lum_legacy * 1.10, bg_lum_legacy + 0.01)

    initial_mid = float(mid_val) if mid_val is not None else 0.42
    res = {
        "bg_r": bg_r, "hi_r": hi_r_raw,
        "bg_g": bg_g, "hi_g": hi_g_raw,
        "bg_b": bg_b, "hi_b": hi_b_raw,
        "bg_lum": bg_lum_legacy, "hi_lum": hi_lum_legacy,
        "lum_data": lum_legacy,
        "color_data": d,
        "mode": wb_mode,
        "ratio_r": 1.0,
        "ratio_b": 1.0,
        "k_r": 1.0,
        "k_b": 1.0,
        "moon_pixels": 0,
        "wb_locked": False,
        "stretch_locked": False,
        "mid_val": initial_mid,
        "mid_info": {"midtone": initial_mid, "median_raw": initial_mid, "target": 0.50},
    }

    if wb_mode != "gray-world":
        return res

    # 1. Linear pedestal subtraction
    r_net = np.maximum(0.0, d[0] - bg_r)
    g_net = np.maximum(0.0, d[1] - bg_g)
    b_net = np.maximum(0.0, d[2] - bg_b)

    # 2. Lunar Limb Glare Suppression (physics-based radial falloff outside celestial limb)
    if glare_mode != "off":
        net_planes = np.stack([r_net, g_net, b_net], axis=0)
        net_planes, glare_meta = _suppress_lunar_limb_glare(
            net_planes, glare_mode=glare_mode, px_scale=px_scale)
        r_net, g_net, b_net = net_planes[0], net_planes[1], net_planes[2]
        res["glare_meta"] = glare_meta

    lum_approx = 0.299 * r_net + 0.587 * g_net + 0.114 * b_net
    p999_lum_raw = float(np.percentile(lum_approx, 99.95))
    if p999_lum_raw <= 1e-4:
        return res

    # Mask valid lunar surface: exclude dark background/shadows and overexposed peaks
    full_scale = _pixel_full_scale(d)
    mask = (lum_approx > (0.05 * p999_lum_raw)) & (lum_approx < (0.95 * p999_lum_raw)) & np.all(d < 0.995*full_scale,axis=0)
    valid_count = int(np.count_nonzero(mask))
    res["moon_pixels"] = valid_count

    # 2.5 Atmospheric Extinction Gradient Compensation (First-order planar field compensation)
    ext_info = _estimate_atmospheric_extinction_gradient(
        np.stack([r_net, g_net, b_net], axis=0),
        mask,
        mode=extinction_comp,
    )
    res["extinction_meta"] = ext_info
    if ext_info.get("applied"):
        r_net, g_net, b_net = _apply_extinction_compensation(r_net, g_net, b_net, ext_info, mask)
        lum_approx = 0.299 * r_net + 0.587 * g_net + 0.114 * b_net

    # Determine Gray-World gains (check locked profile first)
    is_wb_locked = False
    if lock_wb is not None:
        k_r = float(lock_wb[0])
        k_b = float(lock_wb[1])
        ratio_r = 1.0 / max(k_r, 1e-6)
        ratio_b = 1.0 / max(k_b, 1e-6)
        is_wb_locked = True
    elif locked_profile and "white_balance" in locked_profile:
        wb_prof = locked_profile["white_balance"]
        if "k_r" in wb_prof and "k_b" in wb_prof:
            k_r = float(wb_prof["k_r"])
            k_b = float(wb_prof["k_b"])
            ratio_r = float(wb_prof.get("ratio_r", 1.0 / max(k_r, 1e-6)))
            ratio_b = float(wb_prof.get("ratio_b", 1.0 / max(k_b, 1e-6)))
            is_wb_locked = True

    if not is_wb_locked:
        if valid_count < 200:
            return res
        # Sample mid-reflectance terrain (35th to 85th percentile of lunar terrain)
        # to prevent dark basalt maria albedo from skewing neutral White Balance
        valid_luma = lum_approx[mask]
        p35_luma = float(np.percentile(valid_luma, 35))
        p85_luma = float(np.percentile(valid_luma, 85))
        mask_wb = mask & (lum_approx >= p35_luma) & (lum_approx <= p85_luma)
        if np.count_nonzero(mask_wb) < 100:
            mask_wb = mask

        net_r = float(np.median(r_net[mask_wb]))
        net_g = float(np.median(g_net[mask_wb]))
        net_b = float(np.median(b_net[mask_wb]))
        if net_g <= 1e-5 or net_r <= 1e-5 or net_b <= 1e-5:
            return res
        ratio_r = net_r / net_g
        ratio_b = net_b / net_g
        k_r = 1.0 / ratio_r
        k_b = 1.0 / ratio_b

    # 3. Linear channel balancing
    r_lin = r_net * k_r
    g_lin = g_net
    b_lin = b_net * k_b

    # 4. Edge Defringe (Optical Chromatic Aberration & Purple Fringe Suppression)
    max_b_body = np.maximum(g_lin, r_lin) * 1.15
    max_b_edge = np.maximum(g_lin, r_lin) * 1.00
    edge_zone = lum_approx < (0.10 * p999_lum_raw)
    max_allowed_b = np.where(edge_zone, max_b_edge, max_b_body)
    b_clean = np.minimum(b_lin, max_allowed_b)

    # 5. Calibrated Luminance and Color planes
    lum_clean = (0.299 * r_lin + 0.587 * g_lin + 0.114 * b_clean).astype(np.float32)
    color_clean = np.stack([r_lin, g_lin, b_clean], axis=0).astype(np.float32)

    # Determine unified stretch ceiling (check locked profile first)
    is_stretch_locked = False
    if lock_stretch is not None:
        hi_unified = float(lock_stretch[1])
        is_stretch_locked = True
    elif locked_profile and "histogram" in locked_profile:
        hist_prof = locked_profile["histogram"]
        if "hi_unified" in hist_prof:
            hi_unified = float(hist_prof["hi_unified"])
            is_stretch_locked = True
        elif "hi_lum" in hist_prof:
            hi_unified = float(hist_prof["hi_lum"])
            is_stretch_locked = True

    if not is_stretch_locked:
        p999_clean = float(np.percentile(lum_clean, 99.95))
        # Provide +8% headroom to absorb subsequent deconvolution & wavelet peak energy without clipping
        hi_unified = max(p999_clean * 1.08, 1e-4)

    # Determine midtone stretch (locked profile > user override > adaptive)
    if locked_profile and "histogram" in locked_profile and "midtone" in locked_profile["histogram"] and mid_val is None:
        effective_mid = float(locked_profile["histogram"]["midtone"])
        mid_info = {"midtone": effective_mid, "median_raw": effective_mid, "target": 0.50, "locked": True}
    elif mid_val is not None:
        effective_mid = float(mid_val)
        mid_info = {"midtone": effective_mid, "median_raw": effective_mid, "target": 0.50, "locked": False}
    else:
        mid_info = _estimate_adaptive_midtone(lum_clean, hi_val=hi_unified)
        effective_mid = mid_info["midtone"]

    res.update({
        "bg_r": 0.0, "hi_r": hi_unified,
        "bg_g": 0.0, "hi_g": hi_unified,
        "bg_b": 0.0, "hi_b": hi_unified,
        "bg_lum": 0.0, "hi_lum": hi_unified,
        "lum_data": lum_clean,
        "color_data": color_clean,
        "ratio_r": ratio_r,
        "ratio_b": ratio_b,
        "k_r": k_r,
        "k_b": k_b,
        "wb_locked": is_wb_locked,
        "stretch_locked": is_stretch_locked,
        "mid_val": effective_mid,
        "mid_info": mid_info,
    })
    return res


def _render_deep_cine_mineral(
    lum_sharp: np.ndarray,
    color_balanced: np.ndarray,
    circle_meta: dict | None = None,
    fe_boost: float = 4.5,
    ti_boost: float = 5.5,
    gamma: float = 1.00,
    px_scale: float = 1.0,
    return_float: bool = False,
) -> np.ndarray:
    """Render authentic geological mineral moon via continuous chrominance space amplification.

    Features:
      1. Continuous Geological Chrominance: Eliminates artificial binary classification and
         isolated false color blotches. Smoothly amplifies titanium basalt blues (Mare Tranquillitatis)
         and iron-rich terracotta/peach hues (Mare Serenitatis, Imbrium) continuously across maria
         and highland systems.
      2. Bilateral Chroma Filtering: Strips Bayer sensor chroma noise and subpixel rim dispersion
         while rigorously preserving large-scale geological boundaries.
      3. Physical Limb Defringe & Desaturation Zone: Smoothly fades chrominance to neutral gray
         within 18px of the celestial limb (with strict 4px neutral deadzone), completely eliminating
         optical dispersion, secondary spectrum chromatic fringing, and edge halos.
      4. Natural Wide Dynamic Range: Protects deep basalt maria micro-contrast without harsh S-curves
         or shadow crushing.
      5. Soft-Knee Highlight Protection: Precludes clipped highlights on high-albedo crater peaks.
    """
    import cv2
    import numpy as np

    H, W = lum_sharp.shape
    lum_sharp = np.ascontiguousarray(lum_sharp, dtype=np.float32)
    r = np.ascontiguousarray(color_balanced[0], dtype=np.float32)
    g = np.ascontiguousarray(color_balanced[1], dtype=np.float32)
    b = np.ascontiguousarray(color_balanced[2], dtype=np.float32)

    lum_lin = 0.299 * r + 0.587 * g + 0.114 * b
    p999_lin = float(np.percentile(lum_lin[lum_lin > 0.0001], 99.95))
    safe_lum = np.maximum(lum_lin, 0.005 * p999_lin)

    # 1. Base relative chrominance ratios
    cr_r = r / safe_lum
    cr_g = g / safe_lum
    cr_b = b / safe_lum

    # 2. High-performance Bilateral chrominance low-pass
    # Decouples Bayer CFA high-frequency noise from macro geological mineral boundaries
    sigma_space = max(7, int(round(min(H, W) * 0.018)))
    cr_r_f = cv2.bilateralFilter(cr_r.astype(np.float32), d=7, sigmaColor=0.08, sigmaSpace=sigma_space)
    cr_g_f = cv2.bilateralFilter(cr_g.astype(np.float32), d=7, sigmaColor=0.08, sigmaSpace=sigma_space)
    cr_b_f = cv2.bilateralFilter(cr_b.astype(np.float32), d=7, sigmaColor=0.08, sigmaSpace=sigma_space)

    dr = cr_r_f - 1.0
    dg = cr_g_f - 1.0
    db = cr_b_f - 1.0

    # 3. Geometry & Limb Distance Field
    is_tile = bool(circle_meta and circle_meta.get("is_tile"))
    fit = None
    if not is_tile:
        if circle_meta and circle_meta.get("active"):
            cx = float(circle_meta["center_x"])
            cy = float(circle_meta["center_y"])
            R = float(circle_meta["radius"])
            fit = (cx, cy, R)
        else:
            fit_res = _fit_lunar_limb_circle(lum_sharp, px_scale=px_scale)
            if fit_res:
                cx, cy, R, _ = fit_res
                fit = (cx, cy, R)

    if fit is not None:
        cx, cy, R = fit
        yy, xx = np.mgrid[0:H, 0:W]
        r_grid = np.sqrt((xx - cx)**2 + (yy - cy)**2)
        limb_dist = R - r_grid
    elif not is_tile:
        luma_mask = (lum_sharp > 0.02 * p999_lin).astype(np.uint8)
        limb_dist = cv2.distanceTransform(luma_mask, cv2.DIST_L2, 5)
    else:
        limb_dist = np.full((H, W), 999.0, dtype=np.float32)

    # Physical Limb Defringe & Neutralization Zone:
    # Outer 4px is strict neutral deadzone (limb_dist <= 4.0 -> chroma_fade = 0.0)
    # Between 4px and 18px from the limb, saturation smoothly transitions to full mineral saturation
    # using a smooth cubic Hermite polynomial.
    if not is_tile:
        fade_val = np.clip((limb_dist - _px(4.0, px_scale)) / _px(14.0, px_scale), 0.0, 1.0)
        limb_chroma_fade = fade_val * fade_val * (3.0 - 2.0 * fade_val)
    else:
        limb_chroma_fade = 1.0

    # 4. Continuous Hue-Selective Amplification
    # Ti-rich (b > r, db > 0) receives ti_boost; Fe-rich (r > b, dr > 0) receives fe_boost
    delta_rb = dr - db
    blend_fe = 1.0 / (1.0 + np.exp(-np.clip(delta_rb / 0.03, -10.0, 10.0)))
    continuous_gain = (1.0 - blend_fe) * ti_boost + blend_fe * fe_boost

    # Highlight and shadow saturation rolloff
    luma_fade = np.clip((lum_sharp - 0.03) / 0.07, 0.0, 1.0) * (1.0 - np.clip((lum_sharp - 0.88) / 0.10, 0.0, 1.0))
    total_chroma_weight = limb_chroma_fade * luma_fade

    dr_boost = dr * continuous_gain * total_chroma_weight
    dg_boost = dg * continuous_gain * total_chroma_weight
    db_boost = db * continuous_gain * total_chroma_weight

    cr_r_new = np.clip(1.0 + dr_boost, 0.1, 4.0)
    cr_g_new = np.clip(1.0 + dg_boost, 0.1, 4.0)
    cr_b_new = np.clip(1.0 + db_boost, 0.1, 4.0)

    # 5. Natural Wide Dynamic Range Tone Sculpting
    lum_safe = np.nan_to_num(np.clip(lum_sharp, 0.0, 1.0), nan=0.0)
    if abs(gamma - 1.0) > 0.01:
        lum_cine = np.power(lum_safe, gamma)
    else:
        lum_cine = lum_safe

    # 6. Soft-Knee Highlight Protection (Guarantees zero harsh clipping)
    knee = 0.75
    above_knee = lum_cine > knee
    if np.any(above_knee):
        lum_cine[above_knee] = knee + (1.0 - knee) * np.tanh((lum_cine[above_knee] - knee) / (1.0 - knee))

    final_r = np.nan_to_num(np.clip(lum_cine * cr_r_new, 0.0, 1.0), nan=0.0)
    final_g = np.nan_to_num(np.clip(lum_cine * cr_g_new, 0.0, 1.0), nan=0.0)
    final_b = np.nan_to_num(np.clip(lum_cine * cr_b_new, 0.0, 1.0), nan=0.0)

    # Outer space clean zeroing
    if not is_tile and fit is not None:
        space_cut = np.clip((limb_dist + _px(1.0, px_scale)) / _px(2.5, px_scale), 0.0, 1.0)
        final_r *= space_cut
        final_g *= space_cut
        final_b *= space_cut

    # Flip vertically to match image row 0 at top convention
    rgb_fits = np.stack([final_r, final_g, final_b], axis=-1)
    if return_float:
        return np.moveaxis(rgb_fits, -1, 0).astype(np.float32)
    rgb_jpg = rgb_fits[::-1, :, :]
    bgr_jpg = cv2.cvtColor((rgb_jpg * 255.0).astype(np.uint8), cv2.COLOR_RGB2BGR)
    return bgr_jpg



def _compose_natural_lrgb(lum, color, circle_meta=None, px_scale=1.0):
    """Keep the existing natural-color tone and limb treatment in float FITS coordinates."""
    color_lum = _guard_luminance(color)
    rgb = np.clip(color * (lum / np.maximum(color_lum, 1e-6)), 0.0, 1.0)
    knee = 0.75
    high = rgb > knee
    rgb[high] = knee + (1.0 - knee) * np.tanh((rgb[high] - knee) / (1.0 - knee))
    if circle_meta and circle_meta.get("active"):
        yy, xx = np.mgrid[:lum.shape[0], :lum.shape[1]]
        distance = float(circle_meta["radius"]) - np.hypot(xx - float(circle_meta["center_x"]), yy - float(circle_meta["center_y"]))
        fade = np.clip((distance - _px(4.0, px_scale)) / _px(14.0, px_scale), 0.0, 1.0)
        fade = fade * fade * (3.0 - 2.0 * fade)
        rgb = lum + (rgb - lum) * fade
        rgb *= np.clip((distance + _px(1.0, px_scale)) / _px(2.5, px_scale), 0.0, 1.0)
    return np.clip(rgb, 0.0, 1.0).astype(np.float32)


def _write_moon_product(work: Path, name: str, data: np.ndarray, header) -> None:
    """Write the float master and both display formats from the same FITS-oriented pixels."""
    import cv2
    from astropy.io import fits

    if not np.all(np.isfinite(data)):
        raise ValueError(f"{name} contains non-finite pixels")
    data = np.clip(data, 0.0, 1.0).astype(np.float32)
    fits.writeto(work / f"{name}_master.fit", data, header=header, overwrite=True)
    screen = np.moveaxis(data[[2, 1, 0], ::-1, :], 0, -1) if data.ndim == 3 else data[::-1, :]
    for suffix, scale in (("tif", 65535), ("jpg", 255)):
        if suffix == "tif" and name == "moon_mineral":
            continue
        pixels = np.rint(screen * scale).astype(np.uint16 if suffix == "tif" else np.uint8)
        options = [int(cv2.IMWRITE_JPEG_QUALITY), 95] if suffix == "jpg" else []
        if not cv2.imwrite(str(work / f"{name}.{suffix}"), pixels, options):
            raise OSError(f"failed to write {name}.{suffix}")




def cmd_postprocess(args) -> None:
    from astropy.io import fits
    import cv2
    import numpy as np

    work = Path(args.work).expanduser().resolve()
    master = Path(os.path.abspath(work / args.master))
    resolved_master = master.resolve()
    if not master.is_relative_to(work) or not resolved_master.is_relative_to(work):
        die("--master must be inside the work directory; copy the file into --work first")
    if not master.is_file():
        die(f"master FITS not found: {master}; run stack first")
    if master == work / "adc_receipt.json" or resolved_master == work / "adc_receipt.json":
        die("--master cannot use the ADC receipt path; choose a different working filename")

    logs_dir = work / "logs"
    with ExitStack() as staging:
        with fits.open(master, memmap=False) as hdul:
            hdr, d = hdul[0].header, hdul[0].data
        if not np.all(np.isfinite(d)):
            die("master FITS contains non-finite pixels; repair the source before postprocessing")

        candidate, adc_res = master, None
        if not getattr(args, "no_adc", False) and d.ndim == 3 and d.shape[0] >= 3:
            del d, hdul
            _, _, available = _disk_available(work, args, resolved_master.parent)
            needed = int(np.ceil(resolved_master.stat().st_size * 1.25))
            if needed > available:
                die(f"insufficient ADC disk budget: need {needed} bytes, available {available}; use more disk space")
            scratch = Path(staging.enter_context(tempfile.TemporaryDirectory(prefix=".moon-adc-", dir=resolved_master.parent)))
            candidate = scratch / resolved_master.name
            try:
                shutil.copy2(resolved_master, candidate)
                adc_res = align_rgb_channels(candidate)
            except Exception as exc:
                die(f"ADC failed; original master was not modified: {type(exc).__name__}: {exc}")
            with fits.open(candidate, memmap=False) as hdul:
                hdr, d = hdul[0].header, hdul[0].data
            if not np.all(np.isfinite(d)):
                die("ADC produced non-finite pixels; the master was not modified")

        mosaic_mode = getattr(args, "mosaic_mode", "disc")
        px_scale = _resolve_px_scale(args, work, candidate)
        if px_scale != 1.0:
            log(f"drizzle supersampling detected: the working grid is {px_scale:g}x finer than "
                f"native, so pixel-domain tolerances and kernels are scaled by {px_scale:g}")
        optics = _infer_optical_parameters(hdr, d, args, mosaic_mode=mosaic_mode, px_scale=px_scale)
        px_size = optics["pixel_size"]
        fl = optics["focal_length"]
        dia = optics["aperture"]
        requested_deconv = getattr(args, "deconv", "auto")
        deconv_method, deconv_reason = _deconvolution_choice(requested_deconv, optics["trusted"])
        if deconv_method in ("sb", "wiener") and not optics["trusted"]:
            die("physical deconvolution needs trusted focal length, aperture and pixel size; provide --focal/--aperture/--pixel-size or use --deconv auto/none")

        if adc_res and adc_res.get("applied"):
            receipt_dir = Path(staging.enter_context(tempfile.TemporaryDirectory(prefix=".moon-adc-receipt-", dir=work)))
            receipt_path = receipt_dir / "adc_receipt.json"
            dump_json(receipt_path, adc_res)
            receipt_target = work / "adc_receipt.json"
            had_receipt = receipt_target.exists() or receipt_target.is_symlink()
            if had_receipt and not (receipt_target.is_file() or receipt_target.is_symlink()):
                die("ADC receipt path is not a file; original master was not modified")
            previous_receipt = None
            published = False
            try:
                if had_receipt:
                    fd, name = tempfile.mkstemp(prefix=".moon-adc-previous-", dir=work)
                    os.close(fd)
                    previous_receipt = Path(name)
                    try:
                        os.replace(receipt_target, previous_receipt)
                    except BaseException:
                        previous_receipt.unlink(missing_ok=True)
                        raise
                try:
                    os.replace(receipt_path, receipt_target)
                    published = True
                    os.replace(candidate, resolved_master)
                except BaseException:
                    try:
                        if had_receipt:
                            os.replace(previous_receipt, receipt_target)
                        elif published:
                            receipt_target.unlink(missing_ok=True)
                    except OSError as exc:
                        location = previous_receipt if had_receipt else receipt_target
                        die(f"ADC commit failed; master was not modified; inspect receipt recovery at {location}: {exc}")
                    raise
            except OSError as exc:
                die(f"ADC commit failed; original master was not modified: {exc}")
            if previous_receipt:
                try:
                    previous_receipt.unlink(missing_ok=True)
                except OSError as exc:
                    log(f"previous ADC receipt retained at {previous_receipt}: {exc}")
            log(f"ADC applied: R shift={adc_res['shift_r']}, B shift={adc_res['shift_b']} (resp R={adc_res['response_r']:.2f}, B={adc_res['response_b']:.2f})")
        elif not getattr(args, "no_adc", False):
            log("ADC check: channels aligned or single channel data")

    if optics.get("lunar_disc"):
        disc = optics["lunar_disc"]
        log(f"optical inference: fitted lunar disc D={disc['diameter_px']:.1f}px (R={disc['radius_px']:.1f}px, res={disc['res_std']:.2f}px)")
        log(f"optical inference: sensor diameter={disc['sensor_dia_mm']:.2f}mm -> inferred focal={fl:.0f}mm (range {disc['focal_range'][0]:.0f}~{disc['focal_range'][1]:.0f}mm)")
    log(f"optical setup: focal={fl}, aperture={dia}, pixel_size={px_size}; trusted={optics['trusted']}")
    if requested_deconv == "auto" and not optics["trusted"]:
        log("deconvolution auto: missing trusted optics, continuing without an assumed Airy PSF")

    # Adaptive PSF kernel size: covers ~3 full Airy diffraction rings
    # Short focal (small Airy disc) → smaller kernel avoids high-frequency ringing
    # Long focal (large Airy disc) → larger kernel captures complete diffraction pattern
    r_airy = optics["airy_radius_px"] or 0.0
    ks = int(max(15, min(65, round(r_airy * 6.0))))
    if ks % 2 == 0:
        ks += 1  # PSF kernel must be odd
    log(f"adaptive PSF kernel: ks={ks} (from Airy radius {r_airy:.2f}px × 6.0)")

    deconv_lines = []
    if deconv_method == "sb":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks={ks} -savepsf=psf_airy.fit",
            "sb -loadpsf=psf_airy.fit -iters=2 -alpha=2000",
        ]
        log(f"deconvolution: Split Bregman with physical Airy PSF (dia={dia:.1f}mm, fl={fl:.1f}mm, px={px_size:.2f}um)")
    elif deconv_method == "wiener":
        deconv_lines = [
            f"makepsf manual -airy -dia={dia:.1f} -fl={fl:.1f} -pixelsize={px_size:.2f} -ks={ks} -savepsf=psf_airy.fit",
            "wiener -loadpsf=psf_airy.fit -alpha=0.01",
        ]
        log(f"deconvolution: Wiener with physical Airy PSF (dia={dia:.1f}mm, fl={fl:.1f}mm, px={px_size:.2f}um)")
    elif deconv_method == "rl":
        deconv_lines = [
            "makepsf manual -gaussian -fwhm=2.0 -savepsf=psf.fit",
            "rl -loadpsf=psf.fit -iters=10 -tv -alpha=1000",
        ]
        log("deconvolution: classic Richardson-Lucy with Gaussian PSF")
    else:
        log("deconvolution: bypassed (none)")

    # Detect interpolation method from stack receipt for interp-aware wavelet tuning
    interp_used = "cu"
    stack_receipt_file = work / "stack_receipt.json"
    if stack_receipt_file.exists():
        try:
            sr = load_json(stack_receipt_file)
            interp_used = sr.get("interp", "cu")
        except Exception:
            pass

    mineral_mode = getattr(args, "mineral_mode", "lrgb")
    wb_mode = getattr(args, "white_balance", "gray-world")
    is_rgb = (d.ndim == 3 and d.shape[0] >= 3)
    user_mid_arg = getattr(args, "midtone", None)
    user_mid = float(user_mid_arg) if user_mid_arg is not None else None

    sat_base = float(getattr(args, "sat_base", 0.3))
    sat_fe = float(getattr(args, "sat_fe", 0.8))
    sat_ti = float(getattr(args, "sat_ti", 0.8))
    sat_bg = float(getattr(args, "sat_bg_factor", 1.2))

    imported = load_json(work/"import_receipt.json") if (work/"import_receipt.json").exists() else {}
    saturation = imported.get("video_metadata",{}).get("source_saturation_rgb")
    severe_clipping = bool(saturation and max(saturation) > 0.05)
    if severe_clipping:
        sat_base, sat_fe, sat_ti = min(sat_base,0.1), min(sat_fe,0.2), min(sat_ti,0.2)
        log("source channel clipping detected: restrained mineral saturation; lost colors cannot be recovered")
    sat_lines = []
    if sat_base > 0:
        sat_lines.append(f"satu {sat_base:.2f} {sat_bg:.1f} 6")  # base foundation across all hues
    if sat_fe > 0:
        sat_lines.append(f"satu {sat_fe:.2f} {sat_bg:.1f} 1")    # targeted orange-yellow (Fe-rich basalt/highlands)
    if sat_ti > 0:
        sat_lines.append(f"satu {sat_ti:.2f} {sat_bg:.1f} 3")    # targeted cyan (Ti-rich boundary)
        sat_lines.append(f"satu {sat_ti:.2f} {sat_bg:.1f} 4")    # targeted cyan-magenta (Mare Tranquillitatis core Ti)
    if not sat_lines:
        sat_lines = [f"satu 0.70 {sat_bg:.1f} 6", f"satu 0.40 1.0 6"]

    mosaic_mode = getattr(args, "mosaic_mode", "disc")
    glare_mode = getattr(args, "glare_suppress", "auto")
    if mosaic_mode == "tile":
        glare_mode = "off"
        log("mosaic tile mode active: bypassing lunar limb detection and glare suppression")

    # Load locked calibration profile (P2: Global color and histogram locking)
    lock_profile_path = getattr(args, "lock_profile", None)
    lock_from_dir = getattr(args, "lock_from", None)
    locked_profile, lock_source = _load_locked_profile(lock_profile_path, lock_from_dir)

    lock_wb = None
    if getattr(args, "lock_wb", None):
        lock_wb = (float(args.lock_wb[0]), float(args.lock_wb[1]))
    lock_stretch = None
    if getattr(args, "lock_stretch", None):
        lock_stretch = (float(args.lock_stretch[0]), float(args.lock_stretch[1]))

    if locked_profile:
        log(f"loaded calibration profile from {lock_source}")
        # Inherit mineral aesthetic params if user did not specify override
        if "mineral" in locked_profile:
            m_prof = locked_profile["mineral"]
            if getattr(args, "mineral_fe_boost", None) is None and "fe_boost" in m_prof:
                args.mineral_fe_boost = float(m_prof["fe_boost"])
            if getattr(args, "mineral_ti_boost", None) is None and "ti_boost" in m_prof:
                args.mineral_ti_boost = float(m_prof["ti_boost"])
            if getattr(args, "mineral_gamma", None) is None and "gamma" in m_prof:
                args.mineral_gamma = float(m_prof["gamma"])

    if is_rgb:
        bg_r = _estimate_pedestal(d[0])
        bg_g = _estimate_pedestal(d[1])
        bg_b = _estimate_pedestal(d[2])
        extinction_comp = getattr(args, "extinction_comp", "auto")
        wb = _calculate_channel_balance(
            d, bg_r, bg_g, bg_b,
            mid_val=user_mid,
            wb_mode=wb_mode,
            glare_mode=glare_mode,
            extinction_comp=extinction_comp,
            locked_profile=locked_profile,
            lock_wb=lock_wb,
            lock_stretch=lock_stretch,
            px_scale=px_scale,
        )
        mid_val = wb.get("mid_val", 0.42)
        mid_info = wb.get("mid_info", {})
        if "glare_meta" in wb and wb["glare_meta"].get("active"):
            gm = wb["glare_meta"]
            log(f"lunar limb glare suppression active: center=({gm['center_x']:.1f}, {gm['center_y']:.1f}), R={gm['radius']:.1f}px (res_std={gm['residual_std']:.2f}px, falloff={gm['delta']:.1f}px)")
        if "extinction_meta" in wb and wb["extinction_meta"].get("applied"):
            em = wb["extinction_meta"]
            log(f"atmospheric extinction gradient compensated: zenith_angle={em['zenith_angle_deg']:.1f}°, B-grad={em['amp_b']*100:.1f}%, R-grad={em['amp_r']*100:.1f}%, confidence={em['confidence']:.2f}")
            dump_json(work / "extinction_receipt.json", em)
        if wb.get("wb_locked"):
            log(f"neutral Gray-World balance LOCKED: R/G={wb['ratio_r']:.3f}, B/G={wb['ratio_b']:.3f} (k_r={wb['k_r']:.3f}, k_b={wb['k_b']:.3f})")
        elif wb["moon_pixels"] >= 200 and wb_mode == "gray-world":
            log(f"neutral Gray-World balance applied: R/G={wb['ratio_r']:.3f}, B/G={wb['ratio_b']:.3f} ({wb['moon_pixels']} lunar surface pixels sampled)")
        if wb.get("stretch_locked"):
            log(f"calibrated channels (LOCKED hi_unified={wb['hi_lum']:.5f}, midtone={mid_val:.3f})")
        elif user_mid is None:
            log(f"calibrated channels with adaptive midtone: m={mid_val:.3f} (median={mid_info.get('median_raw', 0.5):.3f}, target={mid_info.get('target', 0.5):.2f})")
        else:
            log(f"calibrated channels (bg=[{wb['bg_r']:.5f}, {wb['bg_g']:.5f}, {wb['bg_b']:.5f}], hi=[{wb['hi_r']:.5f}, {wb['hi_g']:.5f}, {wb['hi_b']:.5f}], midtone={mid_val:.3f})")
        if getattr(args,"luminance_channel","weighted") == "green":
            wb["lum_data"] = np.maximum(0.0, d[1]-bg_g).astype(np.float32)
            wb["bg_lum"] = 0.0
            wb["hi_lum"] = max(float(np.percentile(wb["lum_data"],99.95))*1.08,1e-4)
        stretch_scale = max(1.0,*(wb["hi_"+channel] for channel in ("r","g","b","lum")))
        if stretch_scale > 1:
            wb["color_data"] = wb["color_data"]/stretch_scale
            wb["lum_data"] = wb["lum_data"]/stretch_scale
            for channel in ("r","g","b","lum"):
                wb["bg_"+channel] /= stretch_scale
                wb["hi_"+channel] /= stretch_scale
        wb["stretch_scale"] = stretch_scale
        lum_for_sharp = wb["lum_data"]
    else:
        plane = d if d.ndim == 2 else d[0]
        bg_val = _estimate_pedestal(plane)
        p999_val = float(np.percentile(plane, 99.95))
        hi_val = max(p999_val * 1.10, bg_val + 0.01)
        if lock_stretch:
            bg_val, hi_val = lock_stretch[0], lock_stretch[1]
            log(f"monochrome stretch ceiling LOCKED: bg={bg_val:.5f}, hi={hi_val:.5f}")
        elif locked_profile and "histogram" in locked_profile:
            hist_prof = locked_profile["histogram"]
            hi_val = float(hist_prof.get("hi_lum", hist_prof.get("hi_unified", hi_val)))
            bg_val = float(hist_prof.get("bg_lum", bg_val))
            log(f"monochrome stretch ceiling LOCKED from profile: bg={bg_val:.5f}, hi={hi_val:.5f}")
        mono_target = master.name
        mono_scale = max(1.0,hi_val)
        if mono_scale > 1:
            plane = plane/mono_scale
            bg_val /= mono_scale
            hi_val /= mono_scale
            mono_target = "moon_mono_linear.fit"
            fits.writeto(work/mono_target,plane.astype(np.float32),header=hdr,overwrite=True)
        lum_for_sharp = plane

        if locked_profile and "histogram" in locked_profile and "midtone" in locked_profile["histogram"] and user_mid is None:
            mid_val = float(locked_profile["histogram"]["midtone"])
            log(f"monochrome midtone LOCKED from profile: m={mid_val:.3f}")
        elif user_mid is not None:
            mid_val = user_mid
            log(f"monochrome midtone specified: m={mid_val:.3f}")
        else:
            mid_res = _estimate_adaptive_midtone(np.maximum(0.0, plane - bg_val), hi_val=hi_val - bg_val)
            mid_val = mid_res["midtone"]
            log(f"monochrome adaptive midtone solved: m={mid_val:.3f} (median={mid_res['median_raw']:.3f})")

    # Dynamic Organic Sharpening Parameter Generation
    has_deconv = (deconv_method in ("sb", "wiener", "rl"))
    sharp_mode = getattr(args, "sharp_mode", "auto")
    user_wavelet_l1 = getattr(args, "wavelet_l1", None)
    user_clahe_clip = getattr(args, "clahe_clip", None)
    user_unsharp = getattr(args, "unsharp", None)
    anti_ringing = getattr(args, "anti_ringing", "auto")
    damping_factor = getattr(args, "damping_factor", None)

    sharp_info = _estimate_adaptive_sharpening(
        lum_for_sharp,
        has_deconv=has_deconv,
        interp_used=interp_used,
        sharp_mode=sharp_mode,
        user_wavelet_l1=user_wavelet_l1,
        user_clahe_clip=user_clahe_clip,
        user_unsharp=user_unsharp,
        anti_ringing=anti_ringing,
        damping_factor=damping_factor,
    )
    wrecons_cmd = sharp_info["wrecons_cmd"]
    clahe_lines = sharp_info["clahe_lines"]
    unsharp_lines = sharp_info["unsharp_lines"]
    wavelet_mode = getattr(args, "wavelet_adapt", "auto")
    detail_lines = (["wavelet 5 2", wrecons_cmd, *clahe_lines, *unsharp_lines]
                    if wavelet_mode == "off" else [])

    log(f"adaptive organic sharpening: mode='{sharp_mode}', contrast_ratio={sharp_info['contrast_ratio']:.2f}, seeing_cutoff={sharp_info.get('seeing_cutoff', 1.0):.2f}, mare_noise={sharp_info['sigma_noise']:.6f}")
    log(f"  - wavelet: {wrecons_cmd} (deconv discount={sharp_info['deconv_discount']:.2f}x, noise penalty={sharp_info['noise_penalty']:.2f})")
    if clahe_lines:
        log(f"  - CLAHE: {sharp_info['clahe_clip']:.2f} clip (dynamic contrast-aware)")
    else:
        log("  - CLAHE: bypassed")
    if unsharp_lines:
        log(f"  - USM unsharp: amount={sharp_info['unsharp_amount']:.2f}")
    else:
        log("  - USM unsharp: bypassed (anti-redundancy protection)")
    if anti_ringing != "off" and sharp_info["damping_strength"] > 0:
        log(f"  - anti-ringing: mode='{anti_ringing}', strength={sharp_info['damping_strength']:.2f} (shadow-side undershoot protection)")
    else:
        log("  - anti-ringing: bypassed")

    if is_rgb:
        if mineral_mode == "lrgb":
            log("executing professional L/RGB separation pipeline (Luminance detail deconv/wavelets + Chrominance saturation)...")
            lum_path = work / "moon_lum.fit"
            fits.writeto(lum_path, wb["lum_data"], header=hdr, overwrite=True)
            log(f"calibrated Luminance (bg_lum={wb['bg_lum']:.5f}, hi_lum={wb['hi_lum']:.5f})")

            color_target = master.stem
            if (wb_mode == "gray-world" or wb.get("stretch_scale",1)>1) and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                # --- Stage 1: Luminance High-Frequency Processing ---
                "load moon_lum",
                f"mtf {wb['bg_lum']:.6f} {mid_val:.2f} {wb['hi_lum']:.6f}",
                "save moon_unsharpened",
                "load moon_lum",
                *deconv_lines,
                f"mtf {wb['bg_lum']:.6f} {mid_val:.2f} {wb['hi_lum']:.6f}",
                "save moon_lum_base",
                *detail_lines,
                "save moon_lum_sharp",
                # --- Stage 2: Chrominance Balancing & Saturation ---
                f"load {color_target}",
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_color_clean",
                *sat_lines,
                "save moon_color_sat",
                "exit",
            ]
        else:
            # Legacy monolithic RGB pipeline
            log("executing legacy monolithic RGB wavelet pipeline...")
            color_target = master.stem
            if (wb_mode == "gray-world" or wb.get("stretch_scale",1)>1) and "color_data" in wb:
                color_path = work / "moon_color_balanced.fit"
                fits.writeto(color_path, wb["color_data"], header=hdr, overwrite=True)
                color_target = "moon_color_balanced"

            lines = [
                "requires 1.4.4",
                f"load {color_target}",
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_unsharpened",
                f"load {color_target}",
                *deconv_lines,
                f"mtf {wb['bg_r']:.6f} {mid_val:.2f} {wb['hi_r']:.6f} R",
                f"mtf {wb['bg_g']:.6f} {mid_val:.2f} {wb['hi_g']:.6f} G",
                f"mtf {wb['bg_b']:.6f} {mid_val:.2f} {wb['hi_b']:.6f} B",
                "save moon_base",
                *detail_lines,
                "save moon_sharp",
                "exit",
            ]

    else:
        # Monochrome pipeline
        log("executing monochrome lunar detail pipeline...")

        lines = [
            "requires 1.4.4",
            f"load {mono_target}",
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
            "save moon_unsharpened",
            f"load {mono_target}",
            *deconv_lines,
            f"mtf {bg_val:.6f} {mid_val:.2f} {hi_val:.6f}",
            "save moon_base",
            *detail_lines,
            "save moon_sharp",
            "exit",
        ]

    receipt = run_siril_script(args.siril, lines, work, logs_dir / "03_postprocess.log", args.timeout)
    if receipt["exit_code"] != 0:
        die(f"postprocessing failed (exit {receipt['exit_code']}); see {receipt['log']}")

    guard_mode = getattr(args, "sharp_guard", "auto")
    detail_stem = "moon_lum" if is_rgb and mineral_mode == "lrgb" else "moon"
    baseline = fits.getdata(work / "moon_unsharpened.fit", memmap=False).astype(np.float32)
    post_deconv = fits.getdata(work / f"{detail_stem}_base.fit", memmap=False).astype(np.float32)
    detail_support = None
    wavelet_only = None
    if wavelet_mode == "auto":
        wavelet_only, support, wavelet_receipt = _run_wavelet_adaptation(
            args, work, baseline, post_deconv, detail_stem, sharp_info["wavelet_coeffs"], px_scale, has_deconv)
        fits.writeto(work / "moon_wavelet_support.fit", support.astype(np.uint8), overwrite=True)
        if wavelet_receipt["status"] != "bypassed":
            detail_support = support
        wavelet_receipt["support_file"] = "moon_wavelet_support.fit"
        header = fits.getheader(work / f"{detail_stem}_base.fit")
        fits.writeto(work / "moon_wavelet.fit", wavelet_only, header=header, overwrite=True)
        if clahe_lines or unsharp_lines:
            finish_lines = ["load moon_wavelet", *clahe_lines, *unsharp_lines, f"save {detail_stem}_sharp", "exit"]
            finish = run_siril_script(args.siril, finish_lines, work, logs_dir / "03_wavelet_finish.log", args.timeout)
            if finish["exit_code"] != 0:
                die(f"wavelet contrast finishing failed; see {finish['log']}")
        else:
            fits.writeto(work / f"{detail_stem}_sharp.fit", wavelet_only, header=header, overwrite=True)
    else:
        (work / "moon_wavelet_support.fit").unlink(missing_ok=True)
        (work / "moon_wavelet.fit").unlink(missing_ok=True)
        wavelet_receipt = {"mode": "off", "status": "off", "support_coverage": None,
                           "requested_coefficients": sharp_info["wavelet_coeffs"]}
    receipt["wavelet_adaptation"] = wavelet_receipt
    log(f"wavelet adaptation: {wavelet_receipt['status']}, trusted pixels={wavelet_receipt.get('support_pixels', 'N/A')}")
    raw_candidate = fits.getdata(work / f"{detail_stem}_sharp.fit", memmap=False).astype(np.float32)
    ctx = _sharp_guard_context(baseline, px_scale, detail_support)
    candidate = raw_candidate
    strength = sharp_info["damping_strength"]
    damping_enabled = anti_ringing != "off" and strength > 0
    if guard_mode == "off" and is_rgb and mineral_mode == "legacy":
        damping_enabled = False
    if damping_enabled and np.all(np.isfinite(raw_candidate)):
        damping_base = baseline if guard_mode == "auto" else post_deconv
        base_lum = _guard_luminance(damping_base)
        raw_lum = _guard_luminance(raw_candidate)
        neg, pos = (ctx["neg"], ctx["pos"]) if guard_mode == "auto" else _compute_edge_ringing_damping_mask(
            base_lum, return_positive=True, px_scale=px_scale)
        damped_lum = _apply_anti_ringing_damping(
            base_lum, raw_lum, neg, strength=strength, pos_mask=pos,
            pos_strength=0.75, soft_knee=guard_mode == "off")
        if raw_candidate.ndim == 3:
            delta = raw_lum - base_lum
            factor = np.divide(damped_lum - base_lum, delta, out=np.ones_like(delta), where=np.abs(delta) > 1e-8)
            candidate = damping_base + factor * (raw_candidate - damping_base)
        else:
            candidate = damped_lum
        before = _sharp_guard_metrics(ctx, raw_candidate)["dhr"]
        after = _sharp_guard_metrics(ctx, candidate)["dhr"]
        receipt["anti_ringing"] = {"mode": anti_ringing, "strength": strength,
                                  "dhr_raw": before, "dhr_damped": after}

    if is_rgb and mineral_mode == "lrgb":
        clean_color = fits.getdata(work / "moon_color_clean.fit", memmap=False)
        def render_natural(lum):
            return _compose_natural_lrgb(lum, clean_color, wb.get("glare_meta"), px_scale)
    else:
        def render_natural(data):
            return np.clip(data, 0.0, 1.0).astype(np.float32)

    def render_for_guard(data):
        return np.rint(render_natural(data) * 65535).astype(np.float32) / 65535

    selected, guard = _select_guarded_sharpening(baseline, candidate, ctx, guard_mode, render_for_guard)
    guard["stages"] = {
        "deconvolution": _sharp_guard_metrics(ctx, post_deconv),
        "wavelet": (_sharp_guard_metrics(_sharp_guard_context(post_deconv, px_scale), wavelet_only if wavelet_only is not None else raw_candidate)
                    if np.all(np.isfinite(post_deconv)) else {"finite": False}),
        "combined": _sharp_guard_metrics(ctx, raw_candidate),
    }
    guard["deconvolution"] = {"executed": deconv_method, "retained_alpha": guard["alpha"]}
    guard["baseline_file"] = "moon_unsharpened.fit"
    guard["detail_file"] = f"{detail_stem}_sharp.fit"
    if detail_support is not None:
        guard["detail_support_file"] = "moon_wavelet_support.fit"
        guard["detail_support_sha256"] = hashlib.sha256(detail_support.astype(np.uint8).tobytes()).hexdigest()
    receipt["sharp_guard"] = guard
    log(f"sharp guard: {guard['status']}, retained enhancement={guard['alpha']:.0%}")
    if guard["status"] == "fallback":
        log(f"sharp guard fallback: {guard['attempts'][0]['reasons']}")
    sharp_header = fits.getheader(work / f"{detail_stem}_sharp.fit")
    sharp_header["SHRPALPH"] = guard["alpha"]
    sharp_header["SHRPGARD"] = guard["status"]
    fits.writeto(work / f"{detail_stem}_sharp.fit", selected, header=sharp_header, overwrite=True)
    fits.writeto(work / "moon_natural_baseline.fit", render_for_guard(baseline), header=hdr, overwrite=True)
    _write_moon_product(work, "moon_natural", render_natural(selected), sharp_header)

    mineral_style = "natural" if severe_clipping else getattr(args, "mineral_style", "deep-cine")
    fe_boost = float(getattr(args, "mineral_fe_boost", 4.5))
    ti_boost = float(getattr(args, "mineral_ti_boost", 5.5))
    gamma = float(getattr(args, "mineral_gamma", 1.00))
    if is_rgb and mineral_mode == "lrgb" and mineral_style == "deep-cine":
        c_meta = wb.get("glare_meta")
        if mosaic_mode == "tile":
            c_meta = dict(c_meta or {}, is_tile=True, active=False)
        mineral = _render_deep_cine_mineral(
            selected, wb.get("color_data", d), circle_meta=c_meta, fe_boost=fe_boost,
            ti_boost=ti_boost, gamma=gamma, px_scale=px_scale, return_float=True)
        _write_moon_product(work, "moon_mineral", mineral, sharp_header)
    elif is_rgb:
        if mineral_mode == "lrgb":
            finish_lines = ["requires 1.4.4", "rgbcomp -lum=moon_lum_sharp moon_color_sat -out=moon_mineral_master", "exit"]
        else:
            finish_lines = ["requires 1.4.4", "load moon_sharp", *sat_lines, "save moon_mineral_master", "exit"]
        finish = run_siril_script(args.siril, finish_lines, work, logs_dir / "03_color_finish.log", args.timeout)
        if finish["exit_code"] != 0:
            die(f"color finishing failed; see {finish['log']}")
        _write_moon_product(work, "moon_mineral", fits.getdata(work / "moon_mineral_master.fit", memmap=False), sharp_header)

    receipt["mosaic_mode"] = mosaic_mode
    receipt["deconvolution"] = {"requested":requested_deconv,"applied":deconv_method,"reason":deconv_reason}
    receipt["source_saturation_rgb"] = saturation
    receipt["conservative_color"] = severe_clipping
    receipt["luminance_channel"] = getattr(args,"luminance_channel","weighted")

    # Global calibration profile (P2: Multi-panel stitching consistency)
    active_profile = {
        "version": "1.0",
        "source_master": str(master),
        "locked": bool(locked_profile or lock_wb or lock_stretch),
        "lock_source": lock_source,
        "histogram": {
            "bg_r": float(wb["bg_r"]) if is_rgb else float(bg_val),
            "bg_g": float(wb["bg_g"]) if is_rgb else float(bg_val),
            "bg_b": float(wb["bg_b"]) if is_rgb else float(bg_val),
            "bg_lum": float(wb["bg_lum"]) if is_rgb else float(bg_val),
            "hi_unified": float(wb["hi_lum"]) if is_rgb else float(hi_val),
            "hi_lum": float(wb["hi_lum"]) if is_rgb else float(hi_val),
            "midtone": float(mid_val),
        },
        "white_balance": {
            "mode": wb_mode if is_rgb else "mono",
            "k_r": float(wb["k_r"]) if is_rgb else 1.0,
            "k_b": float(wb["k_b"]) if is_rgb else 1.0,
            "ratio_r": float(wb.get("ratio_r", 1.0)) if is_rgb else 1.0,
            "ratio_b": float(wb.get("ratio_b", 1.0)) if is_rgb else 1.0,
        },
        "mineral": {
            "mineral_style": mineral_style,
            "fe_boost": fe_boost,
            "ti_boost": ti_boost,
            "gamma": gamma,
        },
        "optical_parameters": optics,
    }

    # Save lunar_profile.json
    profile_path = work / "lunar_profile.json"
    dump_json(profile_path, active_profile)

    export_path_str = getattr(args, "export_profile", None)
    if export_path_str:
        export_p = Path(export_path_str).expanduser().resolve()
        export_p.parent.mkdir(parents=True, exist_ok=True)
        dump_json(export_p, active_profile)
        log(f"calibration profile exported to {export_p}")
    else:
        log(f"calibration profile generated & saved to {profile_path}")

    # Export mosaic tile info for downstream siril-mosaic skill
    tile_info = {
        "mosaic_mode": mosaic_mode,
        "master": str(master),
        "shape": list(d.shape),
        "bitpix": int(hdr.get("BITPIX", 16)),
        "pixel_size": px_size,
        "focal_length": fl,
        "aperture": dia,
        "optical_inference": optics,
        "px_scale": px_scale,
        "drizzle": {
            "enabled": bool(px_scale != 1.0),
            "scale": px_scale,
            "pixfrac": hdr.get("DRZPIXFR"),
            "kernel": hdr.get("DRZKRENL"),
            "native_pixel_size": hdr.get("ORIGXPIX"),
        },
        "profile_locked": active_profile["locked"],
        "calibration_profile": active_profile,
        "products": {
            "natural_tif": str(work / "moon_natural.tif"),
            "natural_jpg": str(work / "moon_natural.jpg"),
            "mineral_jpg": str(work / "moon_mineral.jpg") if is_rgb and (work / "moon_mineral.jpg").exists() else None,
        },
    }
    # Visual finishing: Orientation rotation & 1:1 Square close-up export
    rotate_deg = int(getattr(args, "rotate", 0) or 0)
    tile_info["rotation_deg"] = rotate_deg
    square_crop = getattr(args, "square_crop", True)
    for name in ("moon_natural_square.tif", "moon_natural_square.jpg", "moon_mineral_square.jpg"):
        (work / name).unlink(missing_ok=True)
    if not is_rgb:
        for name in ("moon_mineral.jpg", "moon_mineral_master.fit"):
            (work / name).unlink(missing_ok=True)

    product_files = [
        work / "moon_natural.tif",
        work / "moon_natural.jpg",
        work / "moon_mineral.jpg",
    ]

    # 1. Apply orientation rotation if requested
    if rotate_deg in (90, 180, 270):
        rot_flag = {
            90: cv2.ROTATE_90_CLOCKWISE,
            180: cv2.ROTATE_180,
            270: cv2.ROTATE_90_COUNTERCLOCKWISE,
        }[rotate_deg]
        for pf in product_files:
            if pf.exists():
                img = cv2.imread(str(pf), cv2.IMREAD_UNCHANGED)
                if img is not None:
                    img_rot = cv2.rotate(img, rot_flag)
                    cv2.imwrite(str(pf), img_rot)
        log(f"orientation adjustment: rotated product images by {rotate_deg}° clockwise")

    # 2. Adaptive Lunar Sky Margins & Centering (Full-disc mode)
    auto_margin = getattr(args, "auto_margin", True)
    sky_margin_ratio = float(getattr(args, "sky_margin", 0.25) or 0.25)
    if auto_margin and mosaic_mode == "disc" and (work / "moon_natural.jpg").exists():
        nat_sample = cv2.imread(str(work / "moon_natural.jpg"), cv2.IMREAD_UNCHANGED)
        if nat_sample is not None:
            gray_s = cv2.cvtColor(nat_sample, cv2.COLOR_BGR2GRAY) if nat_sample.ndim == 3 else nat_sample
            lunar_fit = _fit_lunar_limb_circle(gray_s.astype(np.float32))
            if lunar_fit:
                _, margin_meta = _apply_adaptive_sky_margins(
                    nat_sample,
                    circle=lunar_fit,
                    sky_margin_ratio=sky_margin_ratio,
                    auto_center=True,
                    bg_val=0,
                )
                if margin_meta.get("applied") and any(p > 0 for p in margin_meta.get("padding", [])):
                    padding = margin_meta["padding"]
                    for pf in product_files:
                        if pf.exists():
                            img = cv2.imread(str(pf), cv2.IMREAD_UNCHANGED)
                            if img is not None:
                                img_padded = cv2.copyMakeBorder(img, *padding, cv2.BORDER_CONSTANT, value=0)
                                cv2.imwrite(str(pf), img_padded)
                    tile_info["adaptive_sky_margin"] = margin_meta
                    log(f"adaptive sky margin: expanded & centered to {margin_meta['new_shape'][1]}x{margin_meta['new_shape'][0]} "
                        f"(sky margin={sky_margin_ratio*100:.0f}%, padding={padding})")

    # 3. Export 1:1 Square Close-Up Master
    if square_crop and (work / "moon_natural.jpg").exists():
        nat_sample = cv2.imread(str(work / "moon_natural.jpg"), cv2.IMREAD_UNCHANGED)
        if nat_sample is not None:
            sh, sw = nat_sample.shape[:2]
            gray_s = cv2.cvtColor(nat_sample, cv2.COLOR_BGR2GRAY) if nat_sample.ndim == 3 else nat_sample
            count,labels,stats,centers = cv2.connectedComponentsWithStats((gray_s > 15).astype(np.uint8))
            component = int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))+1 if count>1 else 0
            if component and stats[component,cv2.CC_STAT_AREA] > 100:
                x0,y0,width,height,_ = map(int,stats[component])
                x1,y1 = x0+width,y0+height
                margin = max(width,height)
                ax,ay = max(0,x0-margin),max(0,y0-margin)
                bx,by = min(sw,x1+margin),min(sh,y1+margin)
                lunar_fit = _fit_lunar_limb_circle(gray_s[ay:by,ax:bx].astype(np.float32))
                if lunar_fit:
                    cx_fit,cy_fit,radius,_ = lunar_fit
                    cx_fit,cy_fit = cx_fit+ax,cy_fit+ay
                    x0,x1 = min(x0,int(np.floor(cx_fit-radius))),max(x1,int(np.ceil(cx_fit+radius)))
                    y0,y1 = min(y0,int(np.floor(cy_fit-radius))),max(y1,int(np.ceil(cy_fit+radius)))
                    side = int(np.ceil(2.0 * radius * (1.0 + 2.0 * sky_margin_ratio)))
                    cx, cy = int(np.round(cx_fit)), int(np.round(cy_fit))
                else:
                    side = int(np.ceil(max(x1-x0,y1-y0) * (1.0 + 2.0 * sky_margin_ratio)))
                    cx, cy = (x0+x1)//2, (y0+y1)//2
                side += side % 2
                left, top = cx-side//2, cy-side//2
                right, bottom = left+side, top+side
                padding = [max(0,-top),max(0,bottom-sh),max(0,-left),max(0,right-sw)]
                for pf in product_files:
                    if pf.exists():
                        image = cv2.imread(str(pf),cv2.IMREAD_UNCHANGED)
                        if image is not None:
                            padded = cv2.copyMakeBorder(image,*padding,cv2.BORDER_CONSTANT,value=0)
                            crop = padded[top+padding[0]:bottom+padding[0],left+padding[2]:right+padding[2]]
                            cv2.imwrite(str(work/(pf.stem+"_square"+pf.suffix)),crop)
                tile_info["square_crop"] = {"box":[left,top,right,bottom],"padding":padding, "side": side}
                log(f"square export: full visible lunar bounds preserved, side={side}, sky_margin={sky_margin_ratio*100:.0f}%, padding={padding}")


    dump_json(work / "mosaic_tile_info.json", tile_info)
    receipt["sharp_guard"]["delivery"] = _verify_sharp_guard(work, guard, tile_info, px_scale)
    dump_json(work / "postprocess_receipt.json", receipt)
    if receipt["sharp_guard"]["delivery"]["status"] == "failed":
        die(f"delivered TIFF failed sharp guard verification: {receipt['sharp_guard']['delivery']}")
    log(f"mosaic tile metadata exported to {work / 'mosaic_tile_info.json'}")
    log(f"postprocessing complete! Products in {work}:")
    log(f"  - Natural master TIFF: {work / 'moon_natural.tif'}")
    log(f"  - Natural JPG:         {work / 'moon_natural.jpg'}")
    if (work / "moon_mineral.jpg").exists():
        log(f"  - Mineral Moon JPG:    {work / 'moon_mineral.jpg'}")
    if (work / "moon_natural_square.jpg").exists():
        log(f"  - Natural Square JPG:  {work / 'moon_natural_square.jpg'}")
    if (work / "moon_mineral_square.jpg").exists():
        log(f"  - Mineral Square JPG:  {work / 'moon_mineral_square.jpg'}")

    # Generate interactive HTML dual-window comparison viewer
    if getattr(args, "html_report", True):
        _render_reference_frame(
            work=work,
            active_profile=active_profile,
            seq_name=getattr(args, "seq", "moon_"),
            rotate_deg=rotate_deg,
            square_crop=square_crop,
            tile_info=tile_info,
        )
        html_file = _generate_comparison_html(
            work=work,
            report=None,
            tile_info=tile_info,
            active_profile=active_profile,
        )
        log(f"  - Comparison viewer:   {html_file}")



