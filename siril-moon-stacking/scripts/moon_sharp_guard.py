"""Sharpness and artifact guard module for siril-moon-stacking.

Safeguards against over-sharpening, ringing, dark halos, chalky saturation,
and crunchy high-kurtosis textures. Solves optimal MTF midtone stretch,
estimates seeing cutoff, and arbitrates adaptive deconvolution and wavelet gains.
"""

from __future__ import annotations

import cv2
import numpy as np

from moon_common import (
    _px,
    _odd,
)
from moon_wavelets import (
    _guard_luminance,
    _compute_edge_ringing_damping_mask,
    _wavelet_gain_schedule,
)
from moon_register import (
    _locate_high_contrast_roi,
)


def _sharp_guard_context(baseline: np.ndarray, px_scale: float = 1.0, detail_support=None) -> dict:
    """Freeze all measurement regions and intensity units before inspecting candidates."""
    if not np.all(np.isfinite(baseline)):
        raise ValueError("sharp guard baseline contains non-finite pixels")
    base = _guard_luminance(baseline)
    scale = max(float(np.percentile(base, 99.95)), 1e-6)
    norm = base / scale
    valid = norm > 0.05
    size = _odd(max(3, int(round(_px(5.0, px_scale)))))
    interior = cv2.erode(
        (valid & (norm < 0.95)).astype(np.uint8), np.ones((size, size), np.uint8),
        borderType=cv2.BORDER_CONSTANT, borderValue=0,
    ).astype(bool)
    grad = cv2.magnitude(cv2.Sobel(norm, cv2.CV_32F, 1, 0), cv2.Sobel(norm, cv2.CV_32F, 0, 1))
    neg, pos = _compute_edge_ringing_damping_mask(base, return_positive=True, px_scale=px_scale)
    ctx = {
        "base": base,
        "source": baseline,
        "scale": scale,
        "valid": valid,
        "interior": interior,
        "neg": neg,
        "pos": pos,
        "sigma": _px(1.0, px_scale),
        "px_scale": px_scale,
        "detail_support": detail_support,
        "reason": None,
    }
    if detail_support is not None and (detail_support.shape != base.shape or not np.all(np.isfinite(detail_support))):
        raise ValueError("invalid frozen wavelet support dimensions or pixels")
    if np.count_nonzero(valid) < 500 or np.count_nonzero(interior) < 100:
        ctx["reason"] = "insufficient_terrain"
        return ctx
    flat_limit, structure_limit, trim_limit = np.percentile(grad[interior], [20, 50, 99.8])
    ctx["flat"] = interior & (grad <= flat_limit) & (neg <= 0.3) & (pos <= 0.3)
    ctx["structure"] = interior & (grad > max(float(structure_limit), 1e-6))
    if detail_support is not None:
        ctx["structure"] = detail_support.astype(bool)
    ctx["interior"] = interior & (grad <= trim_limit)
    if np.count_nonzero(ctx["flat"]) < 32 or np.count_nonzero(ctx["structure"]) < 32:
        ctx["reason"] = "unmeasurable_detail_or_noise"
        return ctx
    ctx["baseline"] = _sharp_guard_metrics(ctx, baseline)
    return ctx


def _sharp_guard_metrics(ctx: dict, candidate: np.ndarray) -> dict:
    """Measure candidates in the baseline's fixed regions without clipping their peaks."""
    if not np.all(np.isfinite(candidate)):
        return {"finite": False}
    lum = _guard_luminance(candidate)
    base = ctx["base"]
    if lum.shape != base.shape:
        raise ValueError("sharp guard candidate and baseline dimensions differ")
    norm = lum / ctx["scale"]
    grad = cv2.magnitude(cv2.Sobel(norm, cv2.CV_32F, 1, 0), cv2.Sobel(norm, cv2.CV_32F, 0, 1))
    metrics = {
        "finite": True,
        "dhr": None,
        "positive_overshoot_ratio": None,
        "csi": None,
        "gkm": None,
        "noise_mad": None,
        "detail_gradient": None,
    }
    for name, mask, delta in (
        ("dhr", ctx["neg"], base - lum),
        ("positive_overshoot_ratio", ctx["pos"], lum - base),
    ):
        active = mask > 0.3
        if np.count_nonzero(active) >= 10:
            metrics[name] = float(np.maximum(delta[active], 0).sum() / (base[active].sum() + 1e-6))
    valid = ctx["valid"]
    if np.any(valid):
        metrics["csi"] = float(np.count_nonzero(valid & (norm >= 0.88) & (grad < 0.008)) / np.count_nonzero(valid))
    values = grad[ctx["interior"]]
    if values.size >= 50:
        std = float(np.std(values))
        metrics["gkm"] = float(np.mean(((values - np.mean(values)) / std) ** 4)) if std > 1e-7 else 0.0
    if "flat" in ctx and np.count_nonzero(ctx["flat"]) >= 32:
        residual = norm - cv2.GaussianBlur(norm, (0, 0), ctx["sigma"])
        values = residual[ctx["flat"]]
        metrics["noise_mad"] = float(np.median(np.abs(values - np.median(values))))
    if "structure" in ctx and np.count_nonzero(ctx["structure"]) >= 32:
        metrics["detail_gradient"] = float(np.median(grad[ctx["structure"]]))
    source = ctx["source"]
    clipped = ((candidate >= 1) & (source < 1)) | ((candidate <= 0) & (source > 0)) | ((candidate < 0) & (source >= 0))
    metrics["new_clipped_pixels"] = int(np.count_nonzero(clipped))
    return metrics


def _sharp_guard_reasons(ctx: dict, metrics: dict, require_gain: bool = True) -> list[str]:
    if not metrics["finite"]:
        return ["non_finite_candidate"]
    if ctx["reason"]:
        return [ctx["reason"]]
    baseline = ctx["baseline"]
    limits = {
        "dhr": 0.035,
        "positive_overshoot_ratio": 0.035,
        "csi": max(0.025, baseline["csi"] + 0.005),
        "gkm": max(14.0, baseline["gkm"] * 1.10),
        "noise_mad": max(baseline["noise_mad"] * 1.10, 1e-6),
    }
    reasons = []
    for name, limit in limits.items():
        value = metrics[name]
        if value is None and name in ("dhr", "positive_overshoot_ratio"):
            continue
        if value is None or not np.isfinite(value):
            reasons.append(f"invalid_{name}")
        elif value > limit:
            reasons.append(name)
    if metrics["new_clipped_pixels"]:
        reasons.append("new_clipping")
    detail = metrics["detail_gradient"]
    if detail is None or not np.isfinite(detail):
        reasons.append("invalid_detail_gain")
    elif require_gain and detail < baseline["detail_gradient"] * 1.005:
        reasons.append("insufficient_detail_gain")
    return reasons


def _select_guarded_sharpening(baseline: np.ndarray, candidate: np.ndarray, ctx: dict, mode: str = "auto", render=None) -> tuple[np.ndarray, dict]:
    if candidate.shape != baseline.shape:
        raise ValueError("sharp guard candidate and baseline shapes differ")
    result = {
        "mode": mode,
        "status": "off" if mode == "off" else "fallback",
        "alpha": 1.0 if mode == "off" else 0.0,
        "baseline": _sharp_guard_metrics(ctx, baseline),
        "attempts": [],
    }
    delivery_baseline = render(baseline) if render is not None else None
    delivery_ctx = _sharp_guard_context(delivery_baseline, ctx["px_scale"], ctx["detail_support"]) if render is not None else None
    if delivery_ctx is not None:
        result["delivery_baseline"] = _sharp_guard_metrics(delivery_ctx, delivery_baseline)
    if mode == "off":
        if not np.all(np.isfinite(candidate)):
            raise ValueError("sharpened image contains non-finite pixels")
        result["selected"] = _sharp_guard_metrics(ctx, candidate)
        if delivery_ctx is not None:
            result["delivery_selected"] = _sharp_guard_metrics(delivery_ctx, render(candidate))
        return candidate, result
    for alpha in (1.0, 0.75, 0.5, 0.25):
        trial = baseline + alpha * (candidate - baseline)
        metrics = _sharp_guard_metrics(ctx, trial)
        reasons = _sharp_guard_reasons(ctx, metrics)
        attempt = {"alpha": alpha, "metrics": metrics, "reasons": reasons}
        if delivery_ctx is not None and not reasons:
            delivery = _sharp_guard_metrics(delivery_ctx, render(trial))
            attempt["delivery"] = delivery
            reasons.extend(f"delivery_{reason}" for reason in _sharp_guard_reasons(delivery_ctx, delivery))
        result["attempts"].append(attempt)
        if not reasons:
            result.update(status="accepted" if alpha == 1.0 else "reduced", alpha=alpha, selected=metrics)
            if delivery_ctx is not None:
                result["delivery_selected"] = attempt["delivery"]
            return trial.astype(np.float32), result
    result["selected"] = result["baseline"]
    if delivery_ctx is not None:
        result["delivery_selected"] = result["delivery_baseline"]
    return baseline.copy(), result


def _estimate_adaptive_midtone(
    lum_net: np.ndarray,
    moon_mask: np.ndarray | None = None,
    hi_val: float | None = None,
    target_mare_lum: float = 0.54,
) -> dict:
    """Analytically solve the optimal Siril MTF midtone stretch parameter."""
    lum_2d = lum_net.astype(np.float32)
    p999 = float(np.percentile(lum_2d, 99.95))
    if hi_val is None or hi_val <= 1e-6:
        hi_val = max(p999 * 1.08, 1e-4)

    if moon_mask is None:
        moon_mask = (lum_2d > 0.05 * p999) & (lum_2d < 0.95 * p999)

    valid_vals = lum_2d[moon_mask]
    if valid_vals.size < 200:
        return {"midtone": 0.42, "median_raw": 0.42, "contrast_ratio": 2.5, "target": target_mare_lum}

    x_med = float(np.median(valid_vals))
    x_norm = float(np.clip(x_med / hi_val, 0.01, 0.95))

    p10 = float(np.percentile(valid_vals, 10))
    p90 = float(np.percentile(valid_vals, 90))
    contrast_ratio = float((p90 - p10) / max(p10, 1e-4))

    y_target = float(np.clip(target_mare_lum, 0.40, 0.60))
    if contrast_ratio < 2.0:
        y_target = max(0.50, y_target - 0.015)

    denom = x_norm * (1.0 - 2.0 * y_target) + y_target
    if abs(denom) < 1e-6:
        m_opt = x_norm
    else:
        m_opt = (x_norm * (1.0 - y_target)) / denom

    m_opt = float(np.clip(round(m_opt, 3), 0.10, 0.65))
    return {
        "midtone": m_opt,
        "median_raw": round(x_norm, 3),
        "contrast_ratio": round(contrast_ratio, 2),
        "target": round(y_target, 3),
    }


def _estimate_seeing_cutoff(lum_2d: np.ndarray, patch_size: int = 512) -> float:
    """Estimate atmospheric seeing spatial cutoff frequency using 2D-FFT Radial PSD."""
    lum = lum_2d.astype(np.float32)
    H, W = lum.shape
    if H < 128 or W < 128:
        return 0.50

    actual_patch = min(patch_size, H, W)
    actual_patch = (actual_patch // 2) * 2
    if actual_patch < 64:
        return 0.50

    x0, y0, x1, y1 = _locate_high_contrast_roi(lum, roi_size=actual_patch)
    patch = lum[y0:y1, x0:x1]
    if patch.shape[0] < 32 or patch.shape[1] < 32:
        return 0.50

    hann_2d = np.outer(np.hanning(patch.shape[0]), np.hanning(patch.shape[1]))
    patch_w = (patch - np.mean(patch)) * hann_2d

    F = np.fft.fftshift(np.fft.fft2(patch_w))
    psd = np.abs(F) ** 2

    pH, pW = psd.shape
    cy, cx = pH // 2, pW // 2
    y, x = np.ogrid[:pH, :pW]
    r = np.sqrt((x - cx) ** 2 + (y - cy) ** 2).astype(int)

    r_max = min(cx, cy)
    if r_max < 16:
        return 0.50

    radial_prof = np.zeros(r_max, dtype=np.float64)
    for radius in range(r_max):
        mask = (r == radius)
        if np.any(mask):
            radial_prof[radius] = np.mean(psd[mask])

    noise_floor = float(np.median(radial_prof[int(r_max * 0.85):]))
    snr_prof = radial_prof / max(noise_floor, 1e-12)

    cutoff_idx = np.where(snr_prof < 2.0)[0]
    if len(cutoff_idx) > 0 and cutoff_idx[0] > 5:
        k_cutoff = float(cutoff_idx[0]) / float(r_max)
    else:
        k_cutoff = 1.0

    return float(np.clip(round(k_cutoff, 3), 0.15, 1.00))


def _measure_gradient_kurtosis(lum_data: np.ndarray) -> float:
    """Measure the Gradient Kurtosis Metric (GKM) to quantify brittle / crunchy texture."""
    lum = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum, 99.95))
    if p999 <= 1e-5:
        return 0.0

    norm = np.clip(lum / p999, 0.0, 1.0)
    valid_mask = (norm > 0.08) & (norm < 0.95)
    if np.count_nonzero(valid_mask) < 200:
        return 0.0

    H, W = lum.shape
    er_radius = max(5, min(51, int(min(H, W) * 0.025) | 1))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (er_radius, er_radius))
    interior_mask = cv2.erode(valid_mask.astype(np.uint8), kernel).astype(bool)
    if np.count_nonzero(interior_mask) < 100:
        kernel_small = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        interior_mask = cv2.erode(valid_mask.astype(np.uint8), kernel_small).astype(bool)
        if np.count_nonzero(interior_mask) < 50:
            interior_mask = valid_mask

    gx = cv2.Sobel(norm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(norm, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)
    g_samples = grad[interior_mask]

    p998 = float(np.percentile(g_samples, 99.8))
    trimmed = g_samples[g_samples <= p998]

    mean_g = float(np.mean(trimmed))
    std_g = float(np.std(trimmed))
    if std_g < 1e-7:
        return 0.0

    z = (trimmed - mean_g) / std_g
    kurt = float(np.mean(z ** 4))
    return kurt


def _estimate_adaptive_sharpening(
    lum_data: np.ndarray,
    has_deconv: bool = True,
    interp_used: str = "cu",
    sharp_mode: str = "auto",
    user_wavelet_l1: float | None = None,
    user_clahe_clip: float | None = None,
    user_unsharp: float | None = None,
    anti_ringing: str = "auto",
    damping_factor: float | None = None,
) -> dict:
    """Calculate organic, physically-adaptive lunar wavelet and contrast parameters."""
    lum_2d = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum_2d, 99.95))
    moon_mask = (lum_2d > 0.05 * p999) & (lum_2d < 0.95 * p999)
    valid_pixels = lum_2d[moon_mask]

    seeing_cutoff = _estimate_seeing_cutoff(lum_2d)

    if valid_pixels.size < 500:
        contrast_ratio = 3.5
        sigma_noise = 0.0005
    else:
        p10 = float(np.percentile(valid_pixels, 10))
        p90 = float(np.percentile(valid_pixels, 90))
        contrast_ratio = float((p90 - p10) / max(p10, 1e-4))

        diff_h = np.abs(np.diff(lum_2d, axis=1)[:-1, :])
        diff_v = np.abs(np.diff(lum_2d, axis=0)[:, :-1])
        std_local = diff_h + diff_v
        sub_mask = moon_mask[:-1, :-1]
        if np.count_nonzero(sub_mask) > 500:
            flat_thresh = np.percentile(std_local[sub_mask], 20)
            flat_mask = sub_mask & (std_local <= flat_thresh)
            sigma_noise = float(np.median(std_local[flat_mask]) / 1.4826)
        else:
            sigma_noise = float(np.std(lum_2d[~moon_mask])) if np.count_nonzero(~moon_mask) > 100 else 0.001

    if sharp_mode == "none":
        w_coeffs = [1.00, 1.00, 1.00, 1.00, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mellow":
        w_coeffs = [1.01, 1.02, 1.03, 1.01, 1.00]
        clahe_clip = 0.0
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "mild":
        w_coeffs = [1.01, 1.04, 1.06, 1.03, 1.00]
        clahe_clip = 0.08
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    elif sharp_mode == "crisp":
        w_coeffs = [1.04, 1.10, 1.12, 1.08, 1.00]
        clahe_clip = 0.30
        unsharp_amt = 0.0
        deconv_discount = 1.0
        noise_penalty = 1.0
    else:  # "auto"
        deconv_discount = 0.80 if has_deconv else 1.0
        noise_penalty = float(np.clip(1.0 - (sigma_noise / max(p999 * 0.005, 1e-6)), 0.45, 1.0))
        if sigma_noise > 0.0006 or seeing_cutoff < 0.42:
            l1_base = 0.00
        elif has_deconv:
            l1_base = 0.025 if interp_used == "cu" else 0.035
        else:
            l1_base = 0.035 if interp_used == "cu" else 0.05

        effective_factor = deconv_discount * noise_penalty
        w1 = 1.0 + l1_base * effective_factor
        w2 = 1.0 + 0.08 * effective_factor
        w3 = 1.0 + 0.11 * effective_factor
        w4 = 1.0 + 0.05 * effective_factor

        if seeing_cutoff >= 0.60 and sigma_noise < 0.0001:
            w1 = max(w1, 1.02)
            w2 = max(w2, 1.04)
            w3 = max(w3, 1.06)
            w4 = max(w4, 1.03)

        w_coeffs = [round(w1, 2), round(w2, 2), round(w3, 2), round(w4, 2), 1.00]
        if has_deconv or sigma_noise > 0.0006:
            clahe_clip = 0.0
        else:
            clahe_clip = round(float(np.clip(0.40 / max(contrast_ratio, 1.0), 0.05, 0.15)), 2)
        unsharp_amt = 0.0

    if user_wavelet_l1 is not None:
        w_coeffs[0] = round(float(user_wavelet_l1), 2)
    if user_clahe_clip is not None:
        clahe_clip = round(float(user_clahe_clip), 2)
    if user_unsharp is not None:
        unsharp_amt = round(float(user_unsharp), 2)

    if damping_factor is not None:
        rec_damping = float(np.clip(damping_factor, 0.0, 1.0))
    elif anti_ringing == "off":
        rec_damping = 0.0
    elif anti_ringing == "mild":
        rec_damping = 0.35
    elif anti_ringing == "aggressive":
        rec_damping = 0.85
    else:  # "auto"
        if has_deconv and interp_used == "cu":
            rec_damping = 0.65
        elif interp_used == "li":
            rec_damping = 0.45
        else:
            rec_damping = 0.55

    _wavelet_gain_schedule(w_coeffs, 5)
    wrecons_cmd = "wrecons " + " ".join(f"{value:.2f}" for value in w_coeffs)
    clahe_lines = [f"clahe {clahe_clip:.2f} 32"] if clahe_clip > 0 else []
    unsharp_lines = [f"unsharp 1.0 {unsharp_amt:.2f}"] if unsharp_amt > 0 else []

    return {
        "sharp_mode": sharp_mode,
        "wrecons_cmd": wrecons_cmd,
        "wavelet_coeffs": w_coeffs,
        "clahe_lines": clahe_lines,
        "clahe_clip": clahe_clip,
        "unsharp_lines": unsharp_lines,
        "unsharp_amount": unsharp_amt,
        "contrast_ratio": contrast_ratio,
        "sigma_noise": sigma_noise,
        "seeing_cutoff": seeing_cutoff,
        "noise_penalty": noise_penalty if sharp_mode == "auto" else 1.0,
        "deconv_discount": deconv_discount if sharp_mode == "auto" else 1.0,
        "anti_ringing": anti_ringing,
        "damping_strength": rec_damping,
    }


def _deconvolution_choice(requested: str, trusted: bool) -> tuple[str, str]:
    if requested == "auto":
        return ("sb", "trusted_optics") if trusted else ("none", "missing_optical_metadata")
    return requested, "user_disabled" if requested == "none" else "user_selected"
