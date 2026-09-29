"""Deterministic, CPU-bounded stellar PSF shape diagnostics and repair.

The implementation is intentionally non-generative.  It measures isolated
stellar profiles, fits a robust global or smoothly varying Moffat-equivalent
PSF field, and maps the measured PSF toward a rounder bounded target with a
regularized Wiener transfer.  Only a soft stellar support mask is written back.
"""

from __future__ import annotations

from contextlib import nullcontext
import math
import os
import time
from typing import Any

# Production installs also use threadpoolctl below.  These caps cover the
# deterministic fallback environment before the first BLAS/OpenMP call.
for _thread_env_name in (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_thread_env_name, "4")

import numpy as np
from scipy.ndimage import gaussian_filter, maximum_filter

try:
    from threadpoolctl import threadpool_limits
except ImportError:  # NumPy/SciPy path remains sequential without this helper.
    threadpool_limits = None


SCHEMA_VERSION = "starun.stellar-shape-repair/v1"
MIN_GLOBAL_STARS = 30
MIN_SPATIAL_STARS = 80
MIN_SPATIAL_CELLS = 12
MAX_MODEL_STARS = 4096
DEFAULT_RUNTIME_SECONDS = 300.0
DEFAULT_MAX_WORKERS = 4
STRENGTH_LIMITS = {
    "conservative": 0.45,
    "balanced": 0.65,
    "strong": 0.80,
}


def luminance(image: np.ndarray) -> np.ndarray:
    value = np.asarray(image, dtype=np.float32)
    if value.ndim == 2:
        return value
    if value.ndim != 3 or value.shape[2] < 1:
        raise ValueError("stellar shape analysis requires mono or RGB input")
    if value.shape[2] == 1:
        return value[..., 0]
    return (
        0.2126 * value[..., 0]
        + 0.7152 * value[..., 1]
        + 0.0722 * value[..., 2]
    )


def _candidate_centers(
    gray: np.ndarray,
    *,
    max_stars: int = MAX_MODEL_STARS,
    fit_radius: int = 8,
    min_separation: int = 7,
) -> list[tuple[int, int]]:
    margin = fit_radius + 2
    if min(gray.shape) <= margin * 2:
        return []
    high_pass = np.clip(
        gray - gaussian_filter(gray, sigma=4.0),
        0.0,
        None,
    )
    interior = high_pass[margin:-margin, margin:-margin]
    median = float(np.median(interior))
    mad = float(np.median(np.abs(interior - median)))
    threshold = max(
        float(np.percentile(interior, 98.8)),
        median + 5.0 * max(mad, 1e-7),
        2e-5,
    )
    local_max = maximum_filter(
        interior,
        size=max(3, int(min_separation)),
    )
    peak_mask = (interior == local_max) & (interior > threshold)
    yy, xx = np.where(peak_mask)
    yy += margin
    xx += margin
    if yy.size == 0:
        return []

    order = np.argsort(high_pass[yy, xx])[::-1]
    bucket_size = max(1, int(min_separation))
    buckets: dict[tuple[int, int], list[tuple[int, int]]] = {}
    centers: list[tuple[int, int]] = []
    use_stratified_cap = len(order) > int(max_stars)
    stratum_counts: dict[tuple[int, int], int] = {}
    strata = 8
    per_stratum_limit = max(
        1,
        int(math.ceil(int(max_stars) / float(strata * strata))),
    )
    for index in order:
        cy, cx = int(yy[index]), int(xx[index])
        if float(gray[cy, cx]) >= 0.995:
            continue
        stratum = (
            min(strata - 1, int(cy * strata / gray.shape[0])),
            min(strata - 1, int(cx * strata / gray.shape[1])),
        )
        if (
            use_stratified_cap
            and stratum_counts.get(stratum, 0) >= per_stratum_limit
        ):
            continue
        bucket = (cy // bucket_size, cx // bucket_size)
        too_close = False
        for by in range(bucket[0] - 1, bucket[0] + 2):
            for bx in range(bucket[1] - 1, bucket[1] + 2):
                for previous_y, previous_x in buckets.get((by, bx), []):
                    if (
                        (cy - previous_y) ** 2 + (cx - previous_x) ** 2
                        < min_separation ** 2
                    ):
                        too_close = True
                        break
                if too_close:
                    break
            if too_close:
                break
        if too_close:
            continue
        centers.append((cy, cx))
        buckets.setdefault(bucket, []).append((cy, cx))
        stratum_counts[stratum] = stratum_counts.get(stratum, 0) + 1
        if len(centers) >= int(max_stars):
            break
    return centers


def _moffat_beta(
    signal: np.ndarray,
    dx: np.ndarray,
    dy: np.ndarray,
    covariance_xy: np.ndarray,
) -> float:
    inverse = np.linalg.pinv(covariance_xy)
    q2 = (
        inverse[0, 0] * dx * dx
        + 2.0 * inverse[0, 1] * dx * dy
        + inverse[1, 1] * dy * dy
    )
    candidates = (2.15, 2.4, 2.8, 3.3, 4.0, 5.0, 6.5)
    best_beta = 3.3
    best_error = float("inf")
    active = signal > float(np.max(signal)) * 0.015
    if np.count_nonzero(active) < 9:
        return best_beta
    target = signal[active]
    for beta in candidates:
        template = (
            1.0 + q2[active] / max(2.0 * (beta - 2.0), 0.3)
        ) ** (-beta)
        scale = float(
            np.sum(target * template)
            / max(np.sum(template * template), 1e-12)
        )
        error = float(np.median(np.abs(target - scale * template)))
        if error < best_error:
            best_error = error
            best_beta = beta
    return float(best_beta)


def profile_at(
    gray: np.ndarray,
    center: tuple[int, int],
    *,
    fit_radius: int = 8,
) -> dict[str, Any] | None:
    cy, cx = (int(center[0]), int(center[1]))
    y0, y1 = cy - fit_radius, cy + fit_radius + 1
    x0, x1 = cx - fit_radius, cx + fit_radius + 1
    if y0 < 0 or x0 < 0 or y1 > gray.shape[0] or x1 > gray.shape[1]:
        return None
    patch = np.asarray(gray[y0:y1, x0:x1], dtype=np.float64)
    if float(np.max(patch)) >= 0.995:
        return None
    background = float(np.percentile(patch, 20.0))
    signal = np.clip(patch - background, 0.0, None)
    peak = float(np.max(signal))
    if not np.isfinite(peak) or peak <= 1e-7:
        return None
    weights = np.where(signal >= peak * 0.035, signal, 0.0)
    total = float(np.sum(weights))
    if total <= 1e-8:
        return None
    yy, xx = np.mgrid[y0:y1, x0:x1]
    centroid_x = float(np.sum(xx * weights) / total)
    centroid_y = float(np.sum(yy * weights) / total)
    if (centroid_y - cy) ** 2 + (centroid_x - cx) ** 2 > 2.75 ** 2:
        return None
    dx = xx - centroid_x
    dy = yy - centroid_y
    covariance_xy = np.asarray(
        [
            [
                float(np.sum(weights * dx * dx) / total),
                float(np.sum(weights * dx * dy) / total),
            ],
            [
                float(np.sum(weights * dx * dy) / total),
                float(np.sum(weights * dy * dy) / total),
            ],
        ],
        dtype=np.float64,
    )
    eigenvalues, eigenvectors = np.linalg.eigh(covariance_xy)
    eigenvalues = np.maximum(eigenvalues, 0.04)
    sigma_minor, sigma_major = np.sqrt(eigenvalues)
    major_vector = eigenvectors[:, 1]
    major_coordinate = dx * major_vector[0] + dy * major_vector[1]
    coma = abs(
        float(np.sum(weights * major_coordinate ** 3) / total)
        / max(float(sigma_major ** 3), 1e-8)
    )
    beta = _moffat_beta(signal, dx, dy, covariance_xy)
    return {
        "center_rc": [cy, cx],
        "centroid_y": centroid_y,
        "centroid_x": centroid_x,
        "fwhm_major_px": float(2.355 * sigma_major),
        "fwhm_minor_px": float(2.355 * sigma_minor),
        "axis_ratio": float(sigma_minor / max(sigma_major, 1e-8)),
        "orientation_rad": float(
            math.atan2(major_vector[1], major_vector[0])
        ),
        "coma_index": coma,
        "flux": total,
        "peak_above_local_background": peak,
        "moffat_beta": beta,
        "covariance_xy": covariance_xy.tolist(),
    }


def _grid_coverage(
    profiles: list[dict[str, Any]],
    shape: tuple[int, int],
    *,
    grid_size: int = 4,
) -> tuple[int, dict[str, int]]:
    height, width = shape
    cells: dict[str, int] = {}
    for profile in profiles:
        cy, cx = profile["center_rc"]
        gy = min(grid_size - 1, int(cy * grid_size / max(height, 1)))
        gx = min(grid_size - 1, int(cx * grid_size / max(width, 1)))
        key = f"{gy},{gx}"
        cells[key] = cells.get(key, 0) + 1
    return len(cells), cells


def analyze_stellar_shape(
    image: np.ndarray,
    *,
    is_linear: bool = True,
    max_stars: int = MAX_MODEL_STARS,
    fit_radius: int = 8,
) -> dict[str, Any]:
    source = np.asarray(image, dtype=np.float32)
    base = {
        "schema_version": SCHEMA_VERSION,
        "input_domain": "linear_mono_or_rgb",
        "automatic_geometry_reconstruction": True,
        "algorithm": (
            "robust_moffat_psf_field_regularized_wiener_target_mapping"
        ),
        "engineering_thresholds": {
            "minimum_global_stars": MIN_GLOBAL_STARS,
            "minimum_spatial_stars": MIN_SPATIAL_STARS,
            "minimum_spatial_grid_cells": MIN_SPATIAL_CELLS,
            "maximum_model_stars": int(max_stars),
            "axis_ratio_trigger_below": 0.86,
            "axis_ratio_p10_trigger_below": 0.72,
            "coma_p90_trigger_above": 0.18,
            "severe_axis_ratio_p10_below": 0.30,
        },
    }
    if not is_linear:
        return {
            **base,
            "status": "skipped",
            "decision": "skip",
            "reason": "input_is_not_confirmed_linear",
            "shape_repair_detected": False,
            "shape_review_required": False,
            "n_paired_stars": 0,
        }
    if source.ndim not in (2, 3) or (
        source.ndim == 3 and source.shape[2] not in (1, 3, 4)
    ):
        return {
            **base,
            "status": "skipped",
            "decision": "skip",
            "reason": "linear_mono_or_rgb_required",
            "shape_repair_detected": False,
            "shape_review_required": False,
            "n_paired_stars": 0,
        }
    if not np.all(np.isfinite(source)):
        return {
            **base,
            "status": "review_required",
            "decision": "review_required",
            "reason": "non_finite_input",
            "shape_repair_detected": False,
            "shape_review_required": True,
            "n_paired_stars": 0,
        }

    gray = luminance(source)
    centers = _candidate_centers(
        gray,
        max_stars=max_stars,
        fit_radius=fit_radius,
    )
    profiles = [
        profile
        for center in centers
        if (profile := profile_at(gray, center, fit_radius=fit_radius))
        is not None
    ]
    if len(profiles) < 6:
        return {
            **base,
            "status": "insufficient_samples",
            "decision": "skip",
            "reason": "too_few_valid_unsaturated_isolated_stars",
            "shape_repair_detected": False,
            "shape_review_required": False,
            "n_reference_candidates": len(centers),
            "n_paired_stars": len(profiles),
        }

    axis = np.asarray(
        [profile["axis_ratio"] for profile in profiles],
        dtype=np.float64,
    )
    coma = np.asarray(
        [profile["coma_index"] for profile in profiles],
        dtype=np.float64,
    )
    fwhm = np.asarray(
        [profile["fwhm_major_px"] for profile in profiles],
        dtype=np.float64,
    )
    occupied, cells = _grid_coverage(profiles, gray.shape)
    axis_median = float(np.median(axis))
    axis_p10 = float(np.percentile(axis, 10.0))
    coma_median = float(np.median(coma))
    coma_p90 = float(np.percentile(coma, 90.0))
    suspicious = bool(
        axis_median < 0.86
        or axis_p10 < 0.72
        or coma_p90 > 0.18
    )
    severe = bool(
        axis_p10 < 0.30
        or float(np.percentile(fwhm, 90.0)) > 14.0
        or coma_p90 > 1.25
    )
    global_evidence = bool(
        len(profiles) >= MIN_GLOBAL_STARS and occupied >= 4
    )
    spatial_evidence = bool(
        len(profiles) >= MIN_SPATIAL_STARS
        and occupied >= MIN_SPATIAL_CELLS
    )

    cell_axis = []
    for key in cells:
        values = [
            profile["axis_ratio"]
            for profile in profiles
            if (
                min(3, int(profile["center_rc"][0] * 4 / gray.shape[0])),
                min(3, int(profile["center_rc"][1] * 4 / gray.shape[1])),
            )
            == tuple(int(value) for value in key.split(","))
        ]
        if values:
            cell_axis.append(float(np.median(values)))
    spatial_variation = bool(
        spatial_evidence
        and cell_axis
        and max(cell_axis) - min(cell_axis) >= 0.06
    )
    model_kind = (
        "spatial_quadratic"
        if spatial_variation
        else "global"
        if global_evidence
        else None
    )

    reasons: list[str] = []
    if severe:
        decision = "review_required"
        status = "review_required"
        reasons.append("stellar_shape_aberration_exceeds_safe_automatic_scope")
    elif suspicious and not global_evidence:
        decision = "review_required"
        status = "review_required"
        reasons.append("stellar_shape_evidence_insufficient_for_safe_model")
    elif suspicious:
        decision = "apply"
        status = "candidate_recommended"
        reasons.append("correctable_stellar_shape_aberration")
        if spatial_variation:
            reasons.append("smooth_spatial_psf_variation_detected")
    else:
        decision = "skip"
        status = "not_needed"
        reasons.append("no_correctable_stellar_shape_aberration_detected")

    return {
        **base,
        "status": status,
        "decision": decision,
        "reasons": reasons,
        "shape_repair_detected": bool(
            decision == "apply" and suspicious
        ),
        "shape_review_required": bool(decision == "review_required"),
        "shape_model_kind": model_kind,
        "spatial_variation_detected": spatial_variation,
        "n_reference_candidates": len(centers),
        "n_paired_stars": len(profiles),
        "occupied_grid_cells": occupied,
        "grid_cell_counts": cells,
        "stellar_axis_ratio_median": axis_median,
        "stellar_axis_ratio_p10": axis_p10,
        "stellar_coma_index_median": coma_median,
        "stellar_coma_index_p90": coma_p90,
        "stellar_fwhm_major_median_px": float(np.median(fwhm)),
        "stellar_fwhm_major_p90_px": float(np.percentile(fwhm, 90.0)),
        "sample_profiles": profiles[:256],
        "_profiles": profiles,
    }


def _features(
    centers_rc: np.ndarray,
    shape: tuple[int, int],
    *,
    spatial: bool,
) -> np.ndarray:
    height, width = shape
    y = centers_rc[:, 0] / max(height - 1, 1) * 2.0 - 1.0
    x = centers_rc[:, 1] / max(width - 1, 1) * 2.0 - 1.0
    if not spatial:
        return np.ones((len(centers_rc), 1), dtype=np.float64)
    return np.column_stack(
        (
            np.ones_like(x),
            x,
            y,
            x * x,
            x * y,
            y * y,
        )
    )


def _robust_fit(design: np.ndarray, values: np.ndarray) -> np.ndarray:
    active = np.ones(len(values), dtype=bool)
    limiter = (
        threadpool_limits(limits=DEFAULT_MAX_WORKERS)
        if threadpool_limits is not None
        else nullcontext()
    )
    with limiter:
        coefficients = np.linalg.lstsq(design, values, rcond=None)[0]
        for _ in range(3):
            residual = values - design @ coefficients
            median = float(np.median(residual[active]))
            mad = float(np.median(np.abs(residual[active] - median)))
            if mad <= 1e-10:
                break
            next_active = np.abs(residual - median) <= 3.5 * 1.4826 * mad
            if np.count_nonzero(next_active) < design.shape[1] + 3:
                break
            active = next_active
            coefficients = np.linalg.lstsq(
                design[active],
                values[active],
                rcond=None,
            )[0]
    return coefficients


def _fit_psf_field(
    analysis: dict[str, Any],
    shape: tuple[int, int],
) -> dict[str, Any]:
    profiles = list(analysis.get("_profiles") or [])
    if not profiles:
        profiles = list(analysis.get("sample_profiles") or [])
    spatial = analysis.get("shape_model_kind") == "spatial_quadratic"
    centers = np.asarray(
        [profile["center_rc"] for profile in profiles],
        dtype=np.float64,
    )
    design = _features(centers, shape, spatial=spatial)
    covariances = np.asarray(
        [profile["covariance_xy"] for profile in profiles],
        dtype=np.float64,
    )
    betas = np.asarray(
        [profile["moffat_beta"] for profile in profiles],
        dtype=np.float64,
    )
    components = np.column_stack(
        (
            covariances[:, 0, 0],
            covariances[:, 0, 1],
            covariances[:, 1, 1],
            betas,
        )
    )
    coefficients = np.column_stack(
        [
            _robust_fit(design, components[:, index])
            for index in range(components.shape[1])
        ]
    )
    return {
        "kind": "spatial_quadratic" if spatial else "global",
        "feature_order": (
            ["1", "x", "y", "x2", "xy", "y2"] if spatial else ["1"]
        ),
        "coefficients": coefficients.tolist(),
        "image_shape": [int(shape[0]), int(shape[1])],
        "n_fit_stars": len(profiles),
    }


def _predict_psf(
    model: dict[str, Any],
    center_rc: tuple[float, float],
) -> tuple[np.ndarray, float]:
    coefficients = np.asarray(model["coefficients"], dtype=np.float64)
    shape = tuple(int(value) for value in model["image_shape"])
    design = _features(
        np.asarray([center_rc], dtype=np.float64),
        shape,
        spatial=model["kind"] == "spatial_quadratic",
    )
    values = (design @ coefficients)[0]
    covariance = np.asarray(
        [[values[0], values[1]], [values[1], values[2]]],
        dtype=np.float64,
    )
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    eigenvalues = np.clip(eigenvalues, 0.16, 36.0)
    covariance = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
    beta = float(np.clip(values[3], 2.15, 6.5))
    return covariance, beta


def _moffat_psf(
    shape: tuple[int, int],
    covariance_xy: np.ndarray,
    beta: float,
) -> np.ndarray:
    height, width = shape
    yy, xx = np.mgrid[
        -(height // 2):height - height // 2,
        -(width // 2):width - width // 2,
    ]
    inverse = np.linalg.pinv(covariance_xy)
    q2 = (
        inverse[0, 0] * xx * xx
        + 2.0 * inverse[0, 1] * xx * yy
        + inverse[1, 1] * yy * yy
    )
    psf = (
        1.0 + q2 / max(2.0 * (beta - 2.0), 0.3)
    ) ** (-beta)
    psf = np.asarray(psf / max(float(np.sum(psf)), 1e-12), dtype=np.float32)
    return psf


def _wiener_transfer(
    shape: tuple[int, int],
    covariance_xy: np.ndarray,
    beta: float,
    strength: float,
) -> np.ndarray:
    eigenvalues = np.linalg.eigvalsh(covariance_xy)
    minor, major = (float(eigenvalues[0]), float(eigenvalues[1]))
    target_variance = major * (1.0 - 0.25 * float(strength))
    target_variance = float(np.clip(target_variance, minor, major))
    target_covariance = np.eye(2, dtype=np.float64) * target_variance
    observed_psf = _moffat_psf(shape, covariance_xy, beta)
    target_psf = _moffat_psf(shape, target_covariance, beta)
    observed_otf = np.fft.fft2(np.fft.ifftshift(observed_psf))
    target_otf = np.fft.fft2(np.fft.ifftshift(target_psf))
    regularization = 0.006 + 0.020 * (1.0 - float(strength))
    transfer = (
        target_otf * np.conj(observed_otf)
        / (np.abs(observed_otf) ** 2 + regularization)
    )
    dc = transfer[0, 0]
    if abs(dc) > 1e-12:
        transfer = transfer / dc
    maximum_gain = 1.0 + 1.5 * float(strength)
    magnitude = np.abs(transfer)
    transfer *= np.minimum(
        1.0,
        maximum_gain / np.maximum(magnitude, 1e-12),
    )
    fy = np.fft.fftfreq(shape[0])
    fx = np.fft.fftfreq(shape[1])
    radius = np.sqrt(fy[:, None] ** 2 + fx[None, :] ** 2)
    confidence = np.exp(-((radius / 0.42) ** 8))
    transfer = 1.0 + (transfer - 1.0) * confidence
    transfer[0, 0] = 1.0
    return transfer


def _map_patch(
    patch: np.ndarray,
    covariance_xy: np.ndarray,
    beta: float,
    strength: float,
) -> np.ndarray:
    transfer = _wiener_transfer(
        patch.shape,
        covariance_xy,
        beta,
        strength,
    )
    mapped = np.fft.ifft2(np.fft.fft2(patch) * transfer).real
    return np.asarray(mapped, dtype=np.float32)


def _strength_candidates(profile: str) -> list[float]:
    limit = STRENGTH_LIMITS.get(profile, STRENGTH_LIMITS["balanced"])
    return [
        value
        for value in (0.25, 0.35, 0.45, 0.55, 0.65, 0.80)
        if value <= limit + 1e-9
    ]


def _validate_strength(
    gray: np.ndarray,
    profiles: list[dict[str, Any]],
    model: dict[str, Any],
    strength: float,
    *,
    max_samples: int = 96,
) -> dict[str, Any]:
    selected = profiles[:max_samples]
    before_axis = []
    after_axis = []
    before_coma = []
    after_coma = []
    fwhm_ratios = []
    flux_ratios = []
    centroid_shifts = []
    radius = 10
    for profile in selected:
        cy, cx = profile["center_rc"]
        if (
            cy - radius < 0
            or cx - radius < 0
            or cy + radius + 1 > gray.shape[0]
            or cx + radius + 1 > gray.shape[1]
        ):
            continue
        patch = np.asarray(
            gray[
                cy - radius:cy + radius + 1,
                cx - radius:cx + radius + 1,
            ],
            dtype=np.float32,
        )
        covariance, beta = _predict_psf(model, (cy, cx))
        mapped = _map_patch(patch, covariance, beta, strength)
        local_center = (radius, radius)
        before = profile_at(patch, local_center, fit_radius=8)
        after = profile_at(mapped, local_center, fit_radius=8)
        if before is None or after is None:
            continue
        before_axis.append(before["axis_ratio"])
        after_axis.append(after["axis_ratio"])
        before_coma.append(before["coma_index"])
        after_coma.append(after["coma_index"])
        fwhm_ratios.append(
            after["fwhm_major_px"] / max(before["fwhm_major_px"], 1e-8)
        )
        flux_ratios.append(after["flux"] / max(before["flux"], 1e-8))
        centroid_shifts.append(
            math.hypot(
                after["centroid_y"] - before["centroid_y"],
                after["centroid_x"] - before["centroid_x"],
            )
        )
    if len(before_axis) < 12:
        return {
            "strength": float(strength),
            "status": "insufficient_samples",
            "n_paired_stars": len(before_axis),
            "passed": False,
        }
    before_axis_array = np.asarray(before_axis)
    after_axis_array = np.asarray(after_axis)
    before_coma_array = np.asarray(before_coma)
    after_coma_array = np.asarray(after_coma)
    roundness_improvement = float(
        1.0
        - np.median(1.0 - after_axis_array)
        / max(float(np.median(1.0 - before_axis_array)), 1e-8)
    )
    coma_improvement = float(
        1.0
        - np.median(after_coma_array)
        / max(float(np.median(before_coma_array)), 1e-8)
    )
    fwhm_array = np.asarray(fwhm_ratios)
    flux_drift = np.abs(np.asarray(flux_ratios) - 1.0)
    centroid_array = np.asarray(centroid_shifts)
    gates = {
        "minimum_effective_improvement": bool(
            roundness_improvement >= 0.20 or coma_improvement >= 0.15
        ),
        "fwhm_preflight": bool(
            0.75 <= float(np.median(fwhm_array)) <= 1.08
            and float(np.percentile(fwhm_array, 90.0)) <= 1.18
        ),
        "flux_preflight": bool(
            float(np.median(flux_drift)) <= 0.02
            and float(np.percentile(flux_drift, 90.0)) <= 0.05
        ),
        "centroid_preflight": bool(
            float(np.median(centroid_array)) <= 0.10
            and float(np.percentile(centroid_array, 90.0)) <= 0.25
        ),
    }
    return {
        "strength": float(strength),
        "status": "passed" if all(gates.values()) else "rejected",
        "passed": all(gates.values()),
        "n_paired_stars": len(before_axis),
        "roundness_deficit_improvement": roundness_improvement,
        "coma_improvement": coma_improvement,
        "fwhm_ratio_median": float(np.median(fwhm_array)),
        "fwhm_ratio_p90": float(np.percentile(fwhm_array, 90.0)),
        "flux_drift_median": float(np.median(flux_drift)),
        "flux_drift_p90": float(np.percentile(flux_drift, 90.0)),
        "centroid_shift_median_px": float(np.median(centroid_array)),
        "centroid_shift_p90_px": float(np.percentile(centroid_array, 90.0)),
        "gates": gates,
    }


def _tile_starts(size: int, tile_size: int, overlap: int) -> list[int]:
    if size <= tile_size:
        return [0]
    stride = tile_size - overlap
    values = list(range(0, max(size - tile_size, 0) + 1, stride))
    final = size - tile_size
    if not values or values[-1] != final:
        values.append(final)
    return sorted(set(values))


def _stellar_support_mask(
    shape: tuple[int, int],
    profiles: list[dict[str, Any]],
) -> np.ndarray:
    impulses = np.zeros(shape, dtype=np.float32)
    for profile in profiles:
        cy, cx = profile["center_rc"]
        impulses[int(cy), int(cx)] = 1.0
    median_fwhm = float(
        np.median([profile["fwhm_major_px"] for profile in profiles])
    )
    sigma = float(np.clip(median_fwhm / 2.355 * 1.8, 2.0, 6.0))
    blurred = gaussian_filter(impulses, sigma=sigma)
    normalizer = 2.0 * math.pi * sigma * sigma
    return np.asarray(
        np.clip(blurred * normalizer * 1.25, 0.0, 1.0),
        dtype=np.float32,
    )


def _preserve_stellar_aperture_flux(
    reference: np.ndarray,
    candidate: np.ndarray,
    profiles: list[dict[str, Any]],
) -> np.ndarray:
    result = np.asarray(candidate, dtype=np.float32).copy()
    reference_gray = luminance(reference)
    radius = 8
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    aperture = xx * xx + yy * yy <= 7.0 ** 2
    annulus = (xx * xx + yy * yy >= 6.5 ** 2) & (
        xx * xx + yy * yy <= 8.0 ** 2
    )
    template = np.exp(-(xx * xx + yy * yy) / (2.0 * 2.2 ** 2))
    template *= aperture
    template /= max(float(np.sum(template)), 1e-12)
    for profile in profiles:
        cy, cx = (int(value) for value in profile["center_rc"])
        y0, y1 = cy - radius, cy + radius + 1
        x0, x1 = cx - radius, cx + radius + 1
        if y0 < 0 or x0 < 0 or y1 > result.shape[0] or x1 > result.shape[1]:
            continue
        before_patch = reference_gray[y0:y1, x0:x1]
        after_patch = luminance(result[y0:y1, x0:x1, :])
        before_background = float(np.median(before_patch[annulus]))
        after_background = float(np.median(after_patch[annulus]))
        before_flux = float(
            np.sum(np.clip(before_patch - before_background, 0.0, None)[aperture])
        )
        after_flux = float(
            np.sum(np.clip(after_patch - after_background, 0.0, None)[aperture])
        )
        flux_delta = before_flux - after_flux
        if abs(flux_delta) > max(before_flux * 0.30, 1e-7):
            continue
        result[y0:y1, x0:x1, :] += (
            template[..., None] * flux_delta
        ).astype(np.float32)
    return result


def _apply_psf_field(
    image: np.ndarray,
    profiles: list[dict[str, Any]],
    model: dict[str, Any],
    strength: float,
    *,
    deadline: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    source = np.asarray(image, dtype=np.float32)
    original_ndim = source.ndim
    if original_ndim == 2:
        planes = source[..., None]
    else:
        planes = source[..., :3] if source.shape[2] >= 3 else source[..., :1]
    height, width, channels = planes.shape
    tile_size = min(256, max(height, width))
    tile_height = min(tile_size, height)
    tile_width = min(tile_size, width)
    overlap = min(64, max(8, min(tile_height, tile_width) // 4))
    y_starts = _tile_starts(height, tile_height, overlap)
    x_starts = _tile_starts(width, tile_width, overlap)
    window_y = np.hanning(tile_height) if tile_height > 2 else np.ones(tile_height)
    window_x = np.hanning(tile_width) if tile_width > 2 else np.ones(tile_width)
    window = np.maximum(
        np.outer(window_y, window_x).astype(np.float32),
        1e-3,
    )
    output = np.zeros_like(planes, dtype=np.float32)
    weights = np.zeros((height, width), dtype=np.float32)
    tile_count = 0
    for y0 in y_starts:
        for x0 in x_starts:
            if time.monotonic() >= deadline:
                raise TimeoutError("stellar_repair_shape_stage_timeout")
            center = (
                y0 + (tile_height - 1) * 0.5,
                x0 + (tile_width - 1) * 0.5,
            )
            covariance, beta = _predict_psf(model, center)
            transfer = _wiener_transfer(
                (tile_height, tile_width),
                covariance,
                beta,
                strength,
            )
            for channel in range(channels):
                tile = planes[
                    y0:y0 + tile_height,
                    x0:x0 + tile_width,
                    channel,
                ]
                mapped = np.fft.ifft2(
                    np.fft.fft2(tile) * transfer
                ).real.astype(np.float32)
                output[
                    y0:y0 + tile_height,
                    x0:x0 + tile_width,
                    channel,
                ] += mapped * window
            weights[
                y0:y0 + tile_height,
                x0:x0 + tile_width,
            ] += window
            tile_count += 1
    mapped = output / np.maximum(weights[..., None], 1e-6)
    mask = _stellar_support_mask((height, width), profiles)
    candidate_planes = planes + mask[..., None] * (mapped - planes)
    gray = luminance(planes)
    star_response = np.clip(
        gray - gaussian_filter(gray, sigma=4.0),
        0.0,
        None,
    )
    local_peak = maximum_filter(star_response, size=17)
    candidate_planes = np.maximum(
        candidate_planes,
        planes - 0.015 * local_peak[..., None],
    )
    candidate_planes = _preserve_stellar_aperture_flux(
        planes,
        candidate_planes,
        profiles,
    )
    candidate_planes = np.asarray(
        np.clip(candidate_planes, 0.0, 1.0),
        dtype=np.float32,
    )
    if original_ndim == 2:
        candidate = candidate_planes[..., 0]
    elif source.shape[2] > candidate_planes.shape[2]:
        candidate = source.copy()
        candidate[..., :candidate_planes.shape[2]] = candidate_planes
    else:
        candidate = candidate_planes
    return candidate, {
        "method": "spatial_moffat_psf_regularized_wiener_target_mapping",
        "strength": float(strength),
        "tile_size": [tile_height, tile_width],
        "tile_overlap": overlap,
        "tile_count": tile_count,
        "stellar_mask_fraction": float(np.mean(mask >= 0.05)),
        "stellar_mask_soft_mean": float(np.mean(mask)),
        "background_protection": True,
    }


def _paired_geometry(
    reference: np.ndarray,
    candidate: np.ndarray,
    profiles: list[dict[str, Any]],
    *,
    max_samples: int = 1024,
) -> dict[str, Any]:
    before_gray = luminance(reference)
    after_gray = luminance(candidate)
    pairs = []
    ringing = []
    aperture_flux_ratios = []
    for profile in profiles[:max_samples]:
        center = tuple(profile["center_rc"])
        before = profile_at(before_gray, center)
        after = profile_at(after_gray, center)
        if before is None or after is None:
            continue
        pairs.append((before, after))
        cy, cx = center
        radius = 8
        before_patch = before_gray[
            cy - radius:cy + radius + 1,
            cx - radius:cx + radius + 1,
        ]
        after_patch = after_gray[
            cy - radius:cy + radius + 1,
            cx - radius:cx + radius + 1,
        ]
        yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
        annulus = (xx * xx + yy * yy >= 3.0 ** 2) & (
            xx * xx + yy * yy <= 7.5 ** 2
        )
        residual = after_patch - before_patch
        aperture = xx * xx + yy * yy <= 7.0 ** 2
        before_background = float(np.percentile(before_patch, 20.0))
        after_background = float(np.percentile(after_patch, 20.0))
        before_flux = float(
            np.sum(np.clip(before_patch - before_background, 0.0, None)[aperture])
        )
        after_flux = float(
            np.sum(np.clip(after_patch - after_background, 0.0, None)[aperture])
        )
        aperture_flux_ratios.append(
            after_flux / max(before_flux, 1e-8)
        )
        negative = max(
            0.0,
            -float(np.percentile(residual[annulus], 5.0))
            / max(before["peak_above_local_background"], 1e-8),
        )
        ringing.append(negative)
    if len(pairs) < MIN_GLOBAL_STARS:
        return {
            "status": "insufficient_samples",
            "n_paired_stars": len(pairs),
        }
    before_axis = np.asarray([before["axis_ratio"] for before, _ in pairs])
    after_axis = np.asarray([after["axis_ratio"] for _, after in pairs])
    before_coma = np.asarray([before["coma_index"] for before, _ in pairs])
    after_coma = np.asarray([after["coma_index"] for _, after in pairs])
    fwhm_ratios = np.asarray(
        [
            after["fwhm_major_px"] / max(before["fwhm_major_px"], 1e-8)
            for before, after in pairs
        ]
    )
    flux_drift = np.abs(np.asarray(aperture_flux_ratios) - 1.0)
    centroid_shifts = np.asarray(
        [
            math.hypot(
                after["centroid_y"] - before["centroid_y"],
                after["centroid_x"] - before["centroid_x"],
            )
            for before, after in pairs
        ]
    )
    roundness_improvement = float(
        1.0
        - np.median(1.0 - after_axis)
        / max(float(np.median(1.0 - before_axis)), 1e-8)
    )
    coma_improvement = float(
        1.0
        - np.median(after_coma)
        / max(float(np.median(before_coma)), 1e-8)
    )
    region_regressions = []
    height, width = before_gray.shape
    for gy in range(4):
        for gx in range(4):
            values = [
                after["axis_ratio"] - before["axis_ratio"]
                for before, after in pairs
                if (
                    min(3, int(before["center_rc"][0] * 4 / height)) == gy
                    and min(3, int(before["center_rc"][1] * 4 / width)) == gx
                )
            ]
            if values:
                region_regressions.append(float(np.median(values)))
    return {
        "status": "ok",
        "n_paired_stars": len(pairs),
        "reference_axis_ratio_median": float(np.median(before_axis)),
        "candidate_axis_ratio_median": float(np.median(after_axis)),
        "roundness_deficit_improvement": roundness_improvement,
        "reference_coma_index_median": float(np.median(before_coma)),
        "candidate_coma_index_median": float(np.median(after_coma)),
        "coma_improvement": coma_improvement,
        "fwhm_ratio_median": float(np.median(fwhm_ratios)),
        "fwhm_ratio_p90": float(np.percentile(fwhm_ratios, 90.0)),
        "flux_drift_median": float(np.median(flux_drift)),
        "flux_drift_p90": float(np.percentile(flux_drift, 90.0)),
        "centroid_shift_median_px": float(np.median(centroid_shifts)),
        "centroid_shift_p90_px": float(np.percentile(centroid_shifts, 90.0)),
        "dark_ringing_increase_p90": float(np.percentile(ringing, 90.0)),
        "worst_region_axis_ratio_delta": (
            min(region_regressions) if region_regressions else 0.0
        ),
    }


def repair_stellar_shape(
    image: np.ndarray,
    *,
    analysis: dict[str, Any] | None = None,
    is_linear: bool = True,
    strength_profile: str = "balanced",
    max_runtime_seconds: float = DEFAULT_RUNTIME_SECONDS,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> tuple[np.ndarray, dict[str, Any]]:
    source = np.asarray(image, dtype=np.float32)
    started = time.monotonic()
    deadline = started + min(
        max(float(max_runtime_seconds), 1.0),
        DEFAULT_RUNTIME_SECONDS,
    )
    max_workers = max(1, min(int(max_workers), DEFAULT_MAX_WORKERS))
    before = (
        analysis
        if isinstance(analysis, dict) and analysis.get("_profiles")
        else analyze_stellar_shape(source, is_linear=is_linear)
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "skipped",
        "applied": False,
        "rollback_applied": False,
        "before": {
            key: value
            for key, value in before.items()
            if key != "_profiles"
        },
        "resource_policy": {
            "max_workers": max_workers,
            "max_runtime_seconds": min(
                float(max_runtime_seconds),
                DEFAULT_RUNTIME_SECONDS,
            ),
            "max_model_stars": MAX_MODEL_STARS,
            "memory_soft_target_bytes": 3 * 1024 * 1024 * 1024,
            "execution_mode": "single_instance_sequential_tiles",
        },
        "candidate_preflight": [],
    }
    if before.get("decision") != "apply":
        report["status"] = (
            "review_required"
            if before.get("decision") == "review_required"
            else "skipped"
        )
        report["reason"] = (
            before.get("reasons")
            or [before.get("reason", "shape_repair_not_needed")]
        )
        report["elapsed_seconds"] = time.monotonic() - started
        return source, report

    profiles = list(before.get("_profiles") or [])
    if not profiles:
        report.update(
            {
                "status": "review_required",
                "reason": "runtime_shape_profiles_unavailable",
                "rollback_applied": True,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return source, report
    model = _fit_psf_field(before, luminance(source).shape)
    report["psf_field"] = model
    selected_strength = None

    try:
        for strength in _strength_candidates(strength_profile):
            if time.monotonic() >= deadline:
                raise TimeoutError("stellar_repair_shape_stage_timeout")
            candidate_report = _validate_strength(
                luminance(source),
                profiles,
                model,
                strength,
            )
            report["candidate_preflight"].append(candidate_report)
            if candidate_report.get("passed"):
                selected_strength = strength
                break
        if selected_strength is None:
            report.update(
                {
                    "status": "review_required",
                    "reason": "no_shape_candidate_passed_preflight",
                    "rollback_applied": True,
                    "elapsed_seconds": time.monotonic() - started,
                }
            )
            return source, report
        candidate, operation = _apply_psf_field(
            source,
            profiles,
            model,
            selected_strength,
            deadline=deadline,
        )
    except TimeoutError as exc:
        report.update(
            {
                "status": "review_required",
                "reason": str(exc),
                "rollback_applied": True,
                "timed_out": True,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return source, report

    paired = _paired_geometry(source, candidate, profiles)
    source_gray = luminance(source)
    candidate_gray = luminance(candidate)
    support = _stellar_support_mask(source_gray.shape, profiles)
    background = support < 0.02
    background_drift = float(
        np.median(
            np.abs(candidate_gray[background] - source_gray[background])
        )
    ) if np.any(background) else 0.0
    gates = {
        "finite_and_same_shape": bool(
            candidate.shape == source.shape and np.all(np.isfinite(candidate))
        ),
        "paired_star_samples": bool(
            paired.get("status") == "ok"
            and int(paired.get("n_paired_stars", 0)) >= MIN_GLOBAL_STARS
        ),
        "minimum_effective_improvement": bool(
            float(paired.get("roundness_deficit_improvement", -99.0)) >= 0.20
            or float(paired.get("coma_improvement", -99.0)) >= 0.15
        ),
        "fwhm_preserved": bool(
            0.75 <= float(paired.get("fwhm_ratio_median", -99.0)) <= 1.08
            and float(paired.get("fwhm_ratio_p90", 99.0)) <= 1.18
        ),
        "flux_preserved": bool(
            float(paired.get("flux_drift_median", 99.0)) <= 0.02
            and float(paired.get("flux_drift_p90", 99.0)) <= 0.05
        ),
        "centroids_preserved": bool(
            float(paired.get("centroid_shift_median_px", 99.0)) <= 0.10
            and float(paired.get("centroid_shift_p90_px", 99.0)) <= 0.25
        ),
        "regional_roundness_not_regressed": bool(
            float(paired.get("worst_region_axis_ratio_delta", -99.0)) >= -0.03
        ),
        "background_preserved": background_drift <= 0.002,
        "dark_ringing_controlled": bool(
            float(paired.get("dark_ringing_increase_p90", 99.0)) <= 0.03
        ),
        "runtime_within_budget": time.monotonic() < deadline,
    }
    accepted = all(gates.values())
    report.update(
        {
            "status": "applied" if accepted else "review_required",
            "applied": accepted,
            "rollback_applied": not accepted,
            "reason": (
                "shape_candidate_passed_all_hard_gates"
                if accepted
                else "shape_candidate_failed_hard_gate"
            ),
            "selected_strength": selected_strength,
            "operation": operation,
            "paired_star_geometry": paired,
            "local_preview_regions": [
                {
                    "center_rc": profile["center_rc"],
                    "radius_px": 12,
                    "pairing": "before_after_same_coordinates",
                }
                for profile in profiles[:12]
            ],
            "quality_gates": gates,
            "metrics": {
                "background_median_absolute_drift": background_drift,
            },
            "elapsed_seconds": time.monotonic() - started,
            "engineering_thresholds": {
                "roundness_deficit_improvement_min": 0.20,
                "coma_improvement_min": 0.15,
                "fwhm_ratio_median_bounds": [0.75, 1.08],
                "fwhm_ratio_p90_max": 1.18,
                "flux_drift_median_max": 0.02,
                "flux_drift_p90_max": 0.05,
                "centroid_shift_median_px_max": 0.10,
                "centroid_shift_p90_px_max": 0.25,
                "regional_axis_ratio_regression_max": 0.03,
                "background_median_absolute_drift_max": 0.002,
                "dark_ringing_increase_p90_max": 0.03,
            },
        }
    )
    return (
        np.asarray(candidate, dtype=np.float32)
        if accepted
        else source,
        report,
    )
