#!/usr/bin/env python3
"""Lunar limb circle fitting, astronomical ephemeris geometry, and optical parameter inference."""

from __future__ import annotations

import argparse
from pathlib import Path

from astropy.io import fits
import numpy as np

from moon_common import (
    _estimate_pedestal,
    _hdr_positive_float,
    _hdr_source_label,
    _pixel_full_scale,
    _px,
    log,
)


def _fit_lunar_limb_circle(
    lum_2d: np.ndarray, px_scale: float = 1.0
) -> tuple[float, float, float, float] | None:
    """Fit a high-precision lunar physical circle using radial gradient inflection points."""
    lum_2d = np.ascontiguousarray(lum_2d, dtype=np.float32)
    H, W = lum_2d.shape

    ped = _estimate_pedestal(lum_2d)
    lum_net = np.maximum(0.0, lum_2d - ped)
    p999 = float(np.percentile(lum_net, 99.95))
    if p999 <= 1e-5:
        return None

    import cv2

    core_mask = (lum_net > 0.20 * p999).astype(np.uint8)
    M = cv2.moments(core_mask)
    if M["m00"] == 0:
        return None
    cx_init = float(M["m10"] / M["m00"])
    cy_init = float(M["m01"] / M["m00"])

    dense_angles = np.linspace(0, 2 * np.pi, 180, endpoint=False)
    r_samples = np.arange(min(H, W) * 0.15, max(H, W) * 0.85, _px(1.0, px_scale), dtype=np.float32)

    limb_points = []
    thresh_grad = -0.015 * p999
    for theta in dense_angles:
        xs = cx_init + r_samples * np.cos(theta)
        ys = cy_init + r_samples * np.sin(theta)
        valid = (xs >= 0) & (xs < W - 1) & (ys >= 0) & (ys < H - 1)
        if np.count_nonzero(valid) < 50:
            continue
        xs_val = xs[valid].astype(np.float32).reshape(-1, 1)
        ys_val = ys[valid].astype(np.float32).reshape(-1, 1)
        r_curr = r_samples[valid]

        vals = cv2.remap(lum_net, xs_val, ys_val, cv2.INTER_LINEAR).flatten()
        grad = np.diff(vals)
        if grad.size == 0:
            continue
        min_idx = int(np.argmin(grad))
        if vals[min_idx] > 0.03 * p999 and grad[min_idx] < thresh_grad:
            edge_x = cx_init + float(r_curr[min_idx]) * np.cos(theta)
            edge_y = cy_init + float(r_curr[min_idx]) * np.sin(theta)
            limb_points.append([edge_x, edge_y])

    if len(limb_points) < 15:
        return None

    pts = np.array(limb_points, dtype=np.float64)
    N = len(pts)

    best_inliers = []
    rng = np.random.default_rng(42)
    for _ in range(300):
        sample_idx = rng.choice(N, 3, replace=False)
        p = pts[sample_idx]
        A = np.column_stack([2.0 * p[:, 0], 2.0 * p[:, 1], np.ones(3)])
        b = p[:, 0]**2 + p[:, 1]**2
        try:
            c = np.linalg.solve(A, b)
            xc_cand, yc_cand = c[0], c[1]
            R_sq = c[2] + xc_cand**2 + yc_cand**2
            if R_sq <= 0:
                continue
            R_cand = np.sqrt(R_sq)
            dists = np.abs(np.sqrt((pts[:, 0] - xc_cand)**2 + (pts[:, 1] - yc_cand)**2) - R_cand)
            inliers = np.where(dists < _px(3.0, px_scale))[0]
            if len(inliers) > len(best_inliers):
                best_inliers = inliers
        except np.linalg.LinAlgError:
            continue

    if len(best_inliers) < 15:
        inlier_pts = pts
    else:
        inlier_pts = pts[best_inliers]

    x = inlier_pts[:, 0]
    y = inlier_pts[:, 1]
    A = np.column_stack([2.0 * x, 2.0 * y, np.ones_like(x)])
    b = x**2 + y**2
    c, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    xc, yc = float(c[0]), float(c[1])
    R_sq = float(c[2] + xc**2 + yc**2)
    if R_sq <= 0:
        return None
    R = float(np.sqrt(R_sq))

    dists = np.sqrt((x - xc)**2 + (y - yc)**2)
    res_std = float(np.std(dists - R))

    if not (0.15 * min(H, W) < R < 3.0 * max(H, W)) or res_std > _px(5.0, px_scale):
        return None

    return xc, yc, R, res_std


def _get_lunar_angular_diameter_rad(date_obs: str | None) -> tuple[float, str]:
    """Return precise lunar angular diameter in radians from ephemeris or mean constant."""
    THETA_MEAN_RAD = 0.009037905  # 31.07 arcmin
    MOON_RADIUS_KM = 1737.4      # IAU mean lunar radius

    if not date_obs:
        return THETA_MEAN_RAD, "mean (31.07')"

    try:
        from astropy.coordinates import get_body
        from astropy.time import Time
        import astropy.units as u

        t = Time(date_obs, scale="utc")
        moon = get_body("moon", t)
        dist_km = float(moon.distance.to(u.km).value)
        if dist_km < 300000.0 or dist_km > 420000.0:
            return THETA_MEAN_RAD, f"mean (31.07', ephemeris distance {dist_km:.0f}km out of range)"
        theta_rad = 2.0 * np.arctan(MOON_RADIUS_KM / dist_km)
        theta_arcmin = round(np.degrees(theta_rad) * 60.0, 2)
        return float(theta_rad), f"ephemeris ({theta_arcmin}' at {date_obs})"
    except Exception as exc:
        log(f"ephemeris lookup failed for DATE-OBS='{date_obs}': {exc}; using mean 31.07'")
        return THETA_MEAN_RAD, "mean (31.07', ephemeris unavailable)"


def _infer_optical_parameters(
    hdr: fits.Header,
    data: np.ndarray,
    args: argparse.Namespace,
    mosaic_mode: str = "disc",
    px_scale: float = 1.0,
) -> dict:
    """Intelligently infer telescope optical parameters (pixel size, focal length, aperture)."""
    user_px = getattr(args, "pixel_size", None)
    if user_px is not None and user_px > 0:
        px_size = float(user_px) / float(px_scale)
        px_source = "user" if px_scale == 1.0 else f"user / drizzle {px_scale:g}x"
    else:
        hdr_px = None
        for k in ["XPIXSZ", "YPIXSZ", "PIXSIZE", "PIXSIZE1", "PIXEL_SZ", "PIXELSIZE"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_px = fval
                        px_source = _hdr_source_label(hdr, k, f"FITS Header ({k})")
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_px is not None:
            px_size = hdr_px
        else:
            px_size = 0.0
            px_source = "unknown"

    user_dia = getattr(args, "aperture", None)
    if user_dia is not None and user_dia > 0:
        dia = float(user_dia)
        dia_source = "user"
    else:
        hdr_dia = None
        for k in ["APERTUR", "DIAMETER", "APERTURE"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_dia = fval
                        dia_source = _hdr_source_label(hdr, k, f"FITS Header ({k})")
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_dia is not None:
            dia = hdr_dia
        else:
            dia = 0.0
            dia_source = "unknown"

    user_fl = getattr(args, "focal", None)
    fl = None
    fl_source = None
    disc_info = None
    f_range = None

    if user_fl is not None and user_fl > 0:
        fl = float(user_fl)
        fl_source = "user"
    elif mosaic_mode != "tile" and px_size > 0:
        is_3d = (data.ndim == 3)
        if is_3d:
            lum_2d = 0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]
        else:
            lum_2d = data

        fit_res = _fit_lunar_limb_circle(lum_2d, px_scale=px_scale)
        if fit_res is not None:
            xc, yc, R, res_std = fit_res
            D_px = 2.0 * R
            sensor_dia_mm = D_px * px_size * 1e-3

            date_obs = hdr.get("DATE-OBS")
            theta_actual, theta_source = _get_lunar_angular_diameter_rad(date_obs)
            theta_max = 0.009744843
            theta_min = 0.008552113

            f_est = sensor_dia_mm / (2.0 * np.tan(theta_actual / 2.0))
            f_min = sensor_dia_mm / (2.0 * np.tan(theta_max / 2.0))
            f_max = sensor_dia_mm / (2.0 * np.tan(theta_min / 2.0))

            if 100.0 <= f_est <= 15000.0:
                fl = f_est
                f_range = [f_min, f_max]
                theta_arcmin = round(np.degrees(theta_actual) * 60.0, 2)
                fl_source = f"geometric inversion (disc D={D_px:.1f}px, θ={theta_arcmin}', {theta_source})"
                disc_info = {
                    "center": [round(xc, 1), round(yc, 1)],
                    "radius_px": round(R, 1),
                    "diameter_px": round(D_px, 1),
                    "res_std": round(res_std, 2),
                    "sensor_dia_mm": round(sensor_dia_mm, 3),
                    "angular_diam_arcmin": theta_arcmin,
                    "angular_diam_source": theta_source,
                    "focal_range": [round(f_min, 1), round(f_max, 1)],
                }

    if fl is None:
        hdr_fl = None
        for k in ["FOCALLEN", "FOCAL_LENGTH", "FOCAL"]:
            val = hdr.get(k)
            if val is not None:
                try:
                    fval = float(val)
                    if fval > 0:
                        hdr_fl = fval
                        fl_source = _hdr_source_label(hdr, k, f"FITS Header ({k})")
                        break
                except (ValueError, TypeError):
                    pass
        if hdr_fl is not None:
            fl = hdr_fl
        else:
            fl = 0.0
            fl_source = "unknown"

    f_ratio = fl / dia if dia > 0 else 0.0
    r_airy_px = (1.22 * 0.55 * fl) / (dia * px_size) if (dia > 0 and px_size > 0) else 0.0

    focal_crosscheck = None
    table_focal = _hdr_positive_float(hdr, ["FOCALLEN", "FOCAL_LENGTH", "FOCAL"]) if "FOCALLEN" in str(hdr.get("OPTPRIOR") or "") else None
    if table_focal and fl_source and str(fl_source).startswith("geometric inversion"):
        delta = abs(float(fl) - float(table_focal)) / float(table_focal)
        focal_crosscheck = {
            "device_table_focal_mm": round(float(table_focal), 1),
            "inversion_focal_mm": round(float(fl), 1),
            "delta_pct": round(delta * 100.0, 1),
            "suspicious": bool(delta > 0.15),
        }
        if focal_crosscheck["suspicious"]:
            log(f"optical cross-check: device-table focal {table_focal:.0f}mm vs measured "
                f"inversion {fl:.0f}mm differ by {delta * 100:.0f}% - verify the identified "
                f"device and the pixel pitch (the inversion scales linearly with it)")

    return {
        "pixel_size": round(px_size, 3) if px_size else None,
        "pixel_size_source": px_source,
        "focal_length": round(fl, 1) if fl else None,
        "focal_source": fl_source,
        "focal_crosscheck": focal_crosscheck,
        "focal_range": [round(f_range[0], 1), round(f_range[1], 1)] if f_range else None,
        "aperture": round(dia, 1) if dia else None,
        "aperture_source": dia_source,
        "f_ratio": round(f_ratio, 2) if fl and dia else None,
        "airy_radius_px": round(r_airy_px, 2) if fl and dia and px_size else None,
        "trusted": bool(fl and dia and px_size and np.all(np.isfinite([fl, dia, px_size]))),
        "lunar_disc": disc_info,
    }


def _measure_limb_psf_fwhm(
    lum_2d: np.ndarray, circle=None, px_scale: float = 1.0, full_scale: float | None = None
) -> dict | None:
    """Measure the effective PSF FWHM, in pixels, from the lunar limb edge."""
    if circle is not None:
        xc, yc, R = float(circle[0]), float(circle[1]), float(circle[2])
        res_std = None
    else:
        fit = _fit_lunar_limb_circle(lum_2d, px_scale=px_scale)
        if fit is None:
            return None
        xc, yc, R, res_std = fit

    band = _px(12.0, px_scale)
    H, W = lum_2d.shape
    yy, xx = np.mgrid[0:H, 0:W]
    r_grid = np.sqrt((xx - xc) ** 2 + (yy - yc) ** 2)
    sel = np.abs(r_grid - R) <= band
    if np.count_nonzero(sel) < 500:
        return None
    rs = r_grid[sel]
    vs = lum_2d[sel].astype(np.float64)

    step = max(0.05, _px(0.1, px_scale))
    bins = np.arange(R - band, R + band + step, step)
    if bins.size < 21:
        return None
    idx = np.digitize(rs, bins)
    prof = np.full(bins.size - 1, np.nan, dtype=np.float64)
    for i in range(1, bins.size):
        m = idx == i
        if np.any(m):
            prof[i - 1] = vs[m].mean()
    rc = 0.5 * (bins[:-1] + bins[1:])
    ok = np.isfinite(prof)
    rc, prof = rc[ok], prof[ok]
    if rc.size < 20:
        return None

    lo = float(np.percentile(prof, 2))
    hi = float(np.percentile(prof, 98))
    if hi - lo <= 1e-9:
        return None
    norm = (prof - lo) / (hi - lo)

    def crossing(level: float) -> float | None:
        sign = np.sign(norm - level)
        k = np.where(np.diff(sign) != 0)[0]
        if k.size == 0:
            return None
        i = int(k[0])
        denom = norm[i + 1] - norm[i]
        if abs(denom) < 1e-12:
            return float(rc[i])
        t = (level - norm[i]) / denom
        return float(rc[i] + t * (rc[i + 1] - rc[i]))

    r90 = crossing(0.9)
    r10 = crossing(0.1)
    if r90 is None or r10 is None or r10 <= r90:
        return None
    w1090 = r10 - r90

    peak = float(np.percentile(lum_2d, 99.99))
    if full_scale is None:
        full_scale = _pixel_full_scale(lum_2d)
    saturated = bool(peak >= 0.95 * full_scale)
    if saturated:
        log("drizzle auto: the lunar limb region reaches ~full scale; the measured PSF "
            "width is a lower bound and may understate the true FWHM")

    return {
        "fwhm_px": round(0.919 * w1090, 3),
        "esf_width_10_90_px": round(w1090, 3),
        "limb_radius_px": round(R, 2),
        "limb_fit_residual_std": None if res_std is None else round(float(res_std), 3),
        "possible_saturation": saturated,
    }
