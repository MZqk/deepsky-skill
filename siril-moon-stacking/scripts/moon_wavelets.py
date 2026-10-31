"""Wavelet decomposition, gain scheduling, reconstruction and anti-ringing module for siril-moon-stacking.

Provides multi-scale B-spline wavelet analysis, noise-aware confidence weighting,
edge ringing damping masks, and dark halo / chalky saturation detection.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import cv2
import numpy as np

from moon_common import (
    log,
    die,
    run_siril_script,
    _px,
    _odd,
    _disk_available,
)


def _compute_edge_ringing_damping_mask(
    lum_base: np.ndarray,
    contrast_threshold: float = 1.0,
    return_positive: bool = False,
    px_scale: float = 1.0,
) -> np.ndarray | tuple[np.ndarray, np.ndarray]:
    """Compute subpixel edge ringing damping masks for lunar step edges."""
    base = lum_base.astype(np.float32)
    p_min = float(np.min(base))
    p_max = float(np.max(base))
    if p_max <= p_min + 1e-7:
        zero_m = np.zeros_like(base, dtype=np.float32)
        return (zero_m, zero_m) if return_positive else zero_m

    norm = (base - p_min) / (p_max - p_min)

    # 1. Gradient magnitude via Sobel
    gx = cv2.Sobel(norm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(norm, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)

    # 2. Local dynamic baseline (soft low-pass)
    _k7 = _odd(_px(7.0, px_scale))
    _k5 = _odd(_px(5.0, px_scale))
    _k3 = _odd(_px(3.0, px_scale))
    _dilate_iters = max(1, int(round(_px(2.0, px_scale))))
    _sig_big = _px(1.5, px_scale)
    _sig_small = _px(1.2, px_scale)
    smooth = cv2.GaussianBlur(norm, (_k7, _k7), _sig_big)

    # 3. Step edge relative contrast
    rel_contrast = grad / (smooth + 0.02)
    is_step_edge = rel_contrast > contrast_threshold

    # 4. Shadow side identification
    is_shadow_side = norm < (smooth - 0.005)
    hazard_neg = grad * (is_step_edge & is_shadow_side).astype(np.float32)

    # 5. Morphological dilation for shadow side
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (_k3, _k3))
    dilated_neg = cv2.dilate(hazard_neg, kernel, iterations=_dilate_iters)
    damp_mask_neg = cv2.GaussianBlur(dilated_neg, (_k5, _k5), _sig_small)

    active_pixels_neg = damp_mask_neg[damp_mask_neg > 0.001]
    if active_pixels_neg.size > 10:
        norm_val = float(np.percentile(active_pixels_neg, 95))
        damp_mask_neg = np.clip(damp_mask_neg / max(norm_val, 1e-5), 0.0, 1.0)
    else:
        damp_mask_neg = np.zeros_like(base, dtype=np.float32)

    if not return_positive:
        return damp_mask_neg.astype(np.float32)

    # 6. Bright side identification
    is_bright_side = norm > (smooth + 0.005)
    hazard_pos = grad * (is_step_edge & is_bright_side).astype(np.float32)
    dilated_pos = cv2.dilate(hazard_pos, kernel, iterations=_dilate_iters)
    damp_mask_pos = cv2.GaussianBlur(dilated_pos, (_k5, _k5), _sig_small)

    active_pixels_pos = damp_mask_pos[damp_mask_pos > 0.001]
    if active_pixels_pos.size > 10:
        norm_val_pos = float(np.percentile(active_pixels_pos, 95))
        damp_mask_pos = np.clip(damp_mask_pos / max(norm_val_pos, 1e-5), 0.0, 1.0)
    else:
        damp_mask_pos = np.zeros_like(base, dtype=np.float32)

    return damp_mask_neg.astype(np.float32), damp_mask_pos.astype(np.float32)


def _apply_anti_ringing_damping(
    lum_base: np.ndarray,
    lum_sharp: np.ndarray,
    damping_mask: np.ndarray,
    strength: float = 0.60,
    pos_mask: np.ndarray | None = None,
    pos_strength: float = 0.70,
    soft_knee: bool = False,
) -> np.ndarray:
    """Apply elastic bilateral damping to edge ringing dips and peaks."""
    if strength <= 0.0 and (pos_mask is None or pos_strength <= 0.0) and not soft_knee:
        return lum_sharp

    base = lum_base.astype(np.float32)
    sharp = lum_sharp.astype(np.float32)
    delta = sharp - base
    delta_damped = delta.copy()

    # 1. Negative undershoot damping
    if strength > 0.0 and damping_mask is not None:
        neg_mask = (delta < 0.0) & (damping_mask > 0.0)
        damping_factor = np.clip(1.0 - strength * damping_mask, 0.0, 1.0)
        delta_damped[neg_mask] = delta[neg_mask] * damping_factor[neg_mask]

    # 2. Positive overshoot damping
    if pos_mask is not None and pos_strength > 0.0:
        pos_active = (delta > 0.0) & (pos_mask > 0.0)
        pos_damp = np.clip(1.0 - pos_strength * pos_mask, 0.0, 1.0)
        delta_damped[pos_active] = delta[pos_active] * pos_damp[pos_active]

    res = base + delta_damped

    # 3. Soft-Knee Highlight Protection
    if soft_knee:
        knee = 0.85
        above_knee = res > knee
        if np.any(above_knee):
            res[above_knee] = knee + (1.0 - knee) * np.tanh((res[above_knee] - knee) / (1.0 - knee))

    p_min = float(np.min(base))
    p_max = float(np.max(base))
    if p_max > p_min:
        res = np.clip(res, p_min, max(p_max, float(np.max(sharp))))

    return res.astype(lum_sharp.dtype)


def _measure_dark_halo_ratio(
    lum_base: np.ndarray,
    lum_sharp: np.ndarray,
    damping_mask: np.ndarray,
) -> float:
    """Measure the Dark Halo Ratio (DHR) within step-edge shadow regions."""
    base = lum_base.astype(np.float32)
    sharp = lum_sharp.astype(np.float32)
    active = damping_mask > 0.3

    if np.count_nonzero(active) < 10:
        return 0.0

    undershoot = np.maximum(0.0, base[active] - sharp[active])
    base_energy = np.sum(base[active]) + 1e-6
    return float(np.sum(undershoot) / base_energy)


def _measure_chalky_saturation_index(lum_data: np.ndarray) -> float:
    """Measure the Chalky Saturation Index (CSI) for lunar highlights."""
    lum = lum_data.astype(np.float32)
    p999 = float(np.percentile(lum, 99.95))
    if p999 <= 1e-5:
        return 0.0

    norm = np.clip(lum / p999, 0.0, 1.0)
    valid_lunar = norm > 0.05
    valid_count = int(np.count_nonzero(valid_lunar))
    if valid_count < 100:
        return 0.0

    # Gradient magnitude via Sobel
    gx = cv2.Sobel(norm, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(norm, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.sqrt(gx * gx + gy * gy)

    highlight_mask = valid_lunar & (norm >= 0.88)
    highlight_count = int(np.count_nonzero(highlight_mask))
    if highlight_count < 20:
        return 0.0

    chalky_mask = highlight_mask & (grad < 0.008)
    chalky_count = int(np.count_nonzero(chalky_mask))

    return float(chalky_count / float(valid_count))


def _guard_luminance(data: np.ndarray) -> np.ndarray:
    if data.ndim == 2:
        return np.asarray(data, dtype=np.float32)
    if data.ndim == 3 and data.shape[0] == 3:
        return (0.299 * data[0] + 0.587 * data[1] + 0.114 * data[2]).astype(np.float32)
    raise ValueError("sharp guard requires a monochrome plane or channel-first RGB")


def _wavelet_plane_count(shape, px_scale=1.0):
    if not np.isfinite(px_scale) or px_scale < 1 or min(shape[-2:]) < 1:
        raise ValueError("wavelet adaptation requires a finite scale >= 1 and nonempty image")
    return max(0, min(5 + int(np.ceil(np.log2(px_scale))), int(np.floor(np.log2(min(shape[-2:])))) - 2))


def _wavelet_gain_schedule(coefficients, planes, px_scale=1.0):
    coefficients = np.asarray(coefficients, dtype=np.float64)
    if not np.isfinite(px_scale) or px_scale < 1:
        raise ValueError("wavelet scale must be finite and >= 1")
    if coefficients.shape != (5,) or not np.all(np.isfinite(coefficients)) or np.any(coefficients < 0) or coefficients[-1] != 1:
        raise ValueError("wavelet gains require four finite nonnegative detail gains and a unit residual")
    result = []
    for index in range(max(0, planes - 1)):
        native_index = float(index - np.log2(px_scale))
        increment = float(np.interp(native_index, np.arange(4), coefficients[:4] - 1)) if 0 <= native_index <= 3 else 0.0
        result.append({
            "index": index,
            "native_index": native_index,
            "requested_gain": 1 + increment,
            "reason": "subnative_scale" if native_index < 0 else "unsupported_coarse_scale" if native_index > 3 else "unity_gain" if increment == 0 else None,
        })
    return result


def _smoothstep(value):
    value = np.clip(value, 0.0, 1.0)
    return value * value * (3 - 2 * value)


def _wavelet_confidence(reference_detail, ctx, index):
    """Measure local support using only the unsharpened reference, never the candidate."""
    shape = ctx["base"].shape
    empty = np.zeros(shape, np.float32)
    flat = ctx.get("flat", np.zeros(shape, bool))
    if ctx["reason"] or np.count_nonzero(flat) < 128:
        return empty, {"reason": ctx["reason"] or "insufficient_noise_samples"}
    detail = _guard_luminance(reference_detail) / ctx["scale"]
    values = detail[flat]
    noise = max(1.4826 * float(np.median(np.abs(values - np.median(values)))), 1e-6)
    height, width = shape
    cell = max(1, int(round(32 * ctx["px_scale"])))
    rows, cols = int(np.ceil(height / cell)), int(np.ceil(width / cell))
    grid = np.full((rows, cols), noise, np.float32)
    for row in range(rows):
        for col in range(cols):
            region = np.s_[row * cell:min((row + 1) * cell, height), col * cell:min((col + 1) * cell, width)]
            sample = detail[region][flat[region]]
            if sample.size >= 64:
                grid[row, col] = max(noise, 1.4826 * float(np.median(np.abs(sample - np.median(sample)))))
    noise_map = cv2.resize(grid, (width, height), interpolation=cv2.INTER_LINEAR)
    valid = ctx["valid"].astype(np.float32)
    sigma = max(2 * ctx["px_scale"], 1.5 * 2**index)
    mass = cv2.GaussianBlur(valid, (0, 0), sigma)
    energy = cv2.GaussianBlur(detail * detail * valid, (0, 0), sigma) / np.maximum(mass, 1e-6)
    snr = np.sqrt(np.maximum(energy - noise_map**2, 0)) / noise_map
    confidence = _smoothstep((snr - 2) / 2)
    norm = ctx["base"] / ctx["scale"]
    confidence *= _smoothstep((norm - 0.05) / 0.10) * (1 - _smoothstep((norm - 0.85) / 0.13))
    confidence *= 1 - 0.75 * np.maximum(ctx["neg"], ctx["pos"])
    distance = cv2.distanceTransform(np.pad(ctx["valid"].astype(np.uint8), 1), cv2.DIST_L2, 5)[1:-1, 1:-1]
    confidence *= _smoothstep((distance - 2 * ctx["px_scale"]) / (6 * ctx["px_scale"]))
    yy, xx = np.ogrid[:height, :width]
    frame_distance = np.minimum(np.minimum(xx + 0.5, width - xx - 0.5), np.minimum(yy + 0.5, height - yy - 0.5))
    radius = 2 * (2**(index + 1) - 1)
    confidence *= _smoothstep((frame_distance - radius) / radius)
    confidence *= valid
    return confidence.astype(np.float32), {
        "reason": None,
        "noise_sigma": noise,
        "local_noise_range": [float(noise_map.min()), float(noise_map.max())],
    }


def _reconstruct_trusted_wavelets(baseline, post_deconv, reference_dir, candidate_dir, planes, coefficients, px_scale=1.0):
    from astropy.io import fits
    from moon_sharp_guard import _sharp_guard_context

    if baseline.shape != post_deconv.shape or not np.all(np.isfinite(post_deconv)):
        raise ValueError("invalid post-deconvolution wavelet source")
    ctx = _sharp_guard_context(baseline, px_scale)
    schedule = _wavelet_gain_schedule(coefficients, planes, px_scale)
    candidate = post_deconv.copy()
    reference_sum, candidate_sum = np.zeros_like(baseline), np.zeros_like(post_deconv)
    support = np.zeros(ctx["base"].shape, bool)
    for index in range(planes):
        reference = fits.getdata(reference_dir / f"layer{index:02d}.fit", memmap=False).astype(np.float32)
        detail = reference if reference_dir == candidate_dir else fits.getdata(candidate_dir / f"layer{index:02d}.fit", memmap=False).astype(np.float32)
        if reference.shape != baseline.shape or detail.shape != post_deconv.shape or not np.all(np.isfinite(reference)) or not np.all(np.isfinite(detail)):
            raise ValueError(f"invalid wavelet plane {index}: shape or non-finite pixels")
        reference_sum += reference
        candidate_sum += detail
        if index == planes - 1:
            continue
        layer = schedule[index]
        increment = layer["requested_gain"] - 1
        if increment:
            weight, statistics = _wavelet_confidence(reference, ctx, index)
            candidate += increment * weight * detail
            support |= weight >= 0.5
            layer.update(statistics)
        else:
            weight = np.zeros(support.shape, np.float32)
        layer["effective_gain_range"] = sorted([float(1 + increment * weight.min()), float(1 + increment * weight.max())])
        layer["support_pixels"] = int(np.count_nonzero(weight >= 0.5))
    errors = [float(np.max(np.abs(reference_sum - baseline))), float(np.max(np.abs(candidate_sum - post_deconv)))]
    if max(errors) > 1e-6:
        raise ValueError(f"Siril wavelet reconstruction mismatch: {errors}")
    support &= ctx.get("structure", np.zeros(support.shape, bool))
    pixels = int(np.count_nonzero(support))
    receipt = {
        "mode": "auto",
        "status": "applied" if pixels >= 32 else "unavailable",
        "px_scale": px_scale,
        "reference": "moon_unsharpened.fit",
        "requested_coefficients": list(coefficients),
        "planes": planes,
        "layers": schedule,
        "residual_gain": 1.0,
        "reconstruction_error": errors,
        "support_pixels": pixels,
        "support_coverage": pixels / max(int(np.count_nonzero(ctx["valid"])), 1),
        "reason": None if pixels >= 32 else "insufficient_trusted_detail",
    }
    return candidate.astype(np.float32), support, receipt


def _run_wavelet_adaptation(args, work, baseline, post_deconv, detail_stem, coefficients, px_scale, has_deconv):
    planes = _wavelet_plane_count(baseline.shape, px_scale)
    schedule = _wavelet_gain_schedule(coefficients, planes, px_scale)
    empty = np.zeros(baseline.shape[-2:], bool)
    identity = all(value == 1 for value in coefficients)
    free, reserve, available = _disk_available(work, args)
    plane_bytes = ((baseline.size * 4 + 2879) // 2880 + 1) * 2880
    scratch_planes = planes * (2 if has_deconv else 1) if not identity and planes >= 2 else 0
    needed = int(np.ceil(1.25 * ((scratch_planes + 1) * plane_bytes + empty.size + 5760)))
    if needed > available:
        die(f"insufficient wavelet disk budget: need {needed} bytes, available {available}; use more disk space or explicitly --wavelet-adapt off")
    budget = {
        "free_bytes": free,
        "reserve_bytes": reserve,
        "available_bytes": available,
        "estimated_peak_additional_bytes": needed,
    }
    if identity or planes < 2:
        reason = "identity_gains" if identity else "insufficient_image_size"
        return post_deconv.copy(), empty, {
            "mode": "auto",
            "status": "bypassed" if identity else "unavailable",
            "reason": reason,
            "planes": planes,
            "px_scale": px_scale,
            "layers": schedule,
            "requested_coefficients": list(coefficients),
            "residual_gain": 1.0,
            "disk_budget": budget,
            "support_pixels": 0,
            "support_coverage": 0.0,
        }
    with tempfile.TemporaryDirectory(prefix=".moon-wavelets-", dir=work) as scratch:
        reference_dir = Path(scratch) / "reference"
        candidate_dir = Path(scratch) / "candidate" if has_deconv else reference_dir
        reference_dir.mkdir()
        lines = [f'load "{work / "moon_unsharpened.fit"}"', f'cd "{reference_dir}"', f"extract {planes}"]
        if has_deconv:
            candidate_dir.mkdir()
            lines += [f'load "{work / (detail_stem + "_base.fit")}"', f'cd "{candidate_dir}"', f"extract {planes}"]
        lines.append("exit")
        extracted = run_siril_script(args.siril, lines, Path(scratch), work / "logs/03_wavelet_extract.log", args.timeout)
        if extracted["exit_code"] != 0:
            die(f"wavelet extraction failed; see {extracted['log']}")
        candidate, support, receipt = _reconstruct_trusted_wavelets(
            baseline, post_deconv, reference_dir, candidate_dir, planes, coefficients, px_scale)
    receipt["disk_budget"] = budget
    return candidate, support, receipt


def _load_wavelet_support(work, guard):
    from astropy.io import fits

    name = guard.get("detail_support_file")
    if name is None:
        return None
    if name != "moon_wavelet_support.fit":
        raise ValueError("invalid wavelet support filename")
    support = fits.getdata(work / name, memmap=False)
    if not np.all(np.isfinite(support)) or not np.all((support == 0) | (support == 1)):
        raise ValueError("invalid wavelet support pixels")
    digest = hashlib.sha256(support.astype(np.uint8).tobytes()).hexdigest()
    if digest != guard.get("detail_support_sha256"):
        raise ValueError("wavelet support hash mismatch")
    return support.astype(bool)
