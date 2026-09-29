"""Bounded linear stellar chromatic and PSF-shape diagnostics and repair.

The stage corrects only effects that can be measured and revalidated without
inventing structure:

* a systematic sub-pixel translation between RGB channels; and
* purple/magenta chroma confined to detected stellar wings; and
* bounded global or smoothly varying stellar PSF shape aberration.

Severe/discontinuous aberrations and tracking failures remain review-only.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
from scipy.ndimage import maximum_filter, shift

from stellar_shape_repair import (
    analyze_stellar_shape,
    repair_stellar_shape,
)

SCHEMA_VERSION = "starun.stellar-repair/v1"
MIN_PAIRED_STARS = 6
MAX_AUTOMATIC_CHANNEL_SHIFT_PX = 1.50
PURPLE_HALO_STRENGTH_LADDERS: dict[str, tuple[float, ...]] = {
    "conservative": (0.10, 0.15, 0.20, 0.30),
    "balanced": (0.15, 0.20, 0.30, 0.40),
    "strong": (0.20, 0.30, 0.40, 0.50),
}


def _luminance(image: np.ndarray) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.ndim == 3 and image.shape[2] == 1:
        return image[..., 0]
    return (
        0.2126 * image[..., 0]
        + 0.7152 * image[..., 1]
        + 0.0722 * image[..., 2]
    )


def _candidate_centers(
    luminance: np.ndarray,
    *,
    max_stars: int = 96,
    fit_radius: int = 7,
    min_separation: int = 9,
) -> list[tuple[int, int]]:
    margin = fit_radius + 2
    if min(luminance.shape) <= margin * 2:
        return []
    interior = luminance[margin:-margin, margin:-margin]
    threshold = max(
        float(np.percentile(interior, 99.35)),
        float(np.median(interior) + 6.0 * np.median(
            np.abs(interior - np.median(interior))
        )),
    )
    local_max = maximum_filter(interior, size=max(3, min_separation))
    peaks = (interior == local_max) & (interior > threshold)
    yy, xx = np.where(peaks)
    yy += margin
    xx += margin
    if yy.size == 0:
        return []
    order = np.argsort(luminance[yy, xx])[::-1]
    centers: list[tuple[int, int]] = []
    for index in order:
        cy, cx = int(yy[index]), int(xx[index])
        peak = float(luminance[cy, cx])
        if peak >= 0.995:
            continue
        if any(
            (cy - previous_y) ** 2 + (cx - previous_x) ** 2
            < min_separation ** 2
            for previous_y, previous_x in centers
        ):
            continue
        centers.append((cy, cx))
        if len(centers) >= max_stars:
            break
    return centers


def _channel_moments(
    channel: np.ndarray,
    center: tuple[int, int],
    *,
    fit_radius: int,
) -> dict[str, float] | None:
    cy, cx = center
    y0, y1 = cy - fit_radius, cy + fit_radius + 1
    x0, x1 = cx - fit_radius, cx + fit_radius + 1
    patch = np.asarray(channel[y0:y1, x0:x1], dtype=np.float64)
    background = float(np.percentile(patch, 20.0))
    signal = np.clip(patch - background, 0.0, None)
    peak = float(np.max(signal))
    if not np.isfinite(peak) or peak <= 1e-7:
        return None
    weights = np.where(signal >= peak * 0.05, signal, 0.0)
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
    covariance = np.array(
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
    eigenvalues = np.maximum(np.linalg.eigvalsh(covariance), 0.04)
    sigma_minor, sigma_major = np.sqrt(eigenvalues)
    return {
        "centroid_y": centroid_y,
        "centroid_x": centroid_x,
        "fwhm_major_px": float(2.355 * sigma_major),
        "fwhm_minor_px": float(2.355 * sigma_minor),
        "axis_ratio": float(sigma_minor / max(sigma_major, 1e-8)),
        "peak": peak,
    }


def _purple_halo_excess(
    image: np.ndarray,
    center: tuple[int, int],
    *,
    fit_radius: int,
) -> float:
    cy, cx = center
    y0, y1 = cy - fit_radius, cy + fit_radius + 1
    x0, x1 = cx - fit_radius, cx + fit_radius + 1
    patch = np.asarray(image[y0:y1, x0:x1, :3], dtype=np.float64)
    background = np.percentile(patch.reshape(-1, 3), 20.0, axis=0)
    signal = np.clip(patch - background, 0.0, None)
    luminance = _luminance(signal)
    peak = float(np.max(luminance))
    if peak <= 1e-7:
        return 0.0
    yy, xx = np.mgrid[-fit_radius:fit_radius + 1, -fit_radius:fit_radius + 1]
    radius = np.sqrt(xx * xx + yy * yy)
    annulus = (radius >= 1.5) & (radius <= min(5.5, fit_radius))
    annulus &= luminance >= peak * 0.015
    if np.count_nonzero(annulus) < 6:
        return 0.0
    purple = np.clip(
        0.5 * (signal[..., 0] + signal[..., 2]) - signal[..., 1],
        0.0,
        None,
    )
    return float(np.percentile(purple[annulus] / peak, 90.0))


def analyze_stellar_repair(
    image: np.ndarray,
    *,
    is_linear: bool = True,
    channel_semantics: str = "broadband_rgb",
    min_stars: int = MIN_PAIRED_STARS,
    fit_radius: int = 7,
) -> dict[str, Any]:
    """Measure correctable stellar color and geometric aberrations."""
    image = np.asarray(image, dtype=np.float32)
    shape_analysis = analyze_stellar_shape(
        image,
        is_linear=is_linear,
        fit_radius=max(int(fit_radius), 8),
    )
    public_shape_analysis = {
        key: value
        for key, value in shape_analysis.items()
        if key != "_profiles"
    }
    base = {
        "schema_version": SCHEMA_VERSION,
        "input_domain": "linear_mono_or_rgb",
        "channel_semantics": channel_semantics,
        "automatic_geometry_reconstruction": True,
        "shape_analysis": public_shape_analysis,
        "engineering_thresholds": {
            "minimum_paired_stars": int(min_stars),
            "systematic_channel_offset_min_px": 0.22,
            "systematic_channel_offset_max_px": MAX_AUTOMATIC_CHANNEL_SHIFT_PX,
            "channel_offset_scatter_max_px": 0.35,
            "purple_halo_excess_p90_min": 0.035,
            "purple_halo_affected_star_fraction_min": 0.25,
            "stellar_axis_ratio_review_below": 0.68,
        },
        }
    if not is_linear:
        return {
            **base,
            "status": "skipped",
            "decision": "skip",
            "reason": "input_is_not_confirmed_linear",
            "n_paired_stars": 0,
        }
    color_allowed = bool(
        image.ndim == 3
        and image.shape[2] >= 3
        and channel_semantics == "broadband_rgb"
    )
    if not color_allowed:
        return {
            **base,
            "status": shape_analysis.get("status", "skipped"),
            "decision": shape_analysis.get("decision", "skip"),
            "reason": shape_analysis.get("reason"),
            "reasons": shape_analysis.get("reasons") or [
                shape_analysis.get(
                    "reason",
                    "no_correctable_stellar_shape_aberration_detected",
                )
            ],
            "n_reference_candidates": shape_analysis.get(
                "n_reference_candidates",
                0,
            ),
            "n_paired_stars": shape_analysis.get("n_paired_stars", 0),
            "n_color_paired_stars": 0,
            "systematic_shift_detected": False,
            "purple_fringe_detected": False,
            "shape_repair_detected": bool(
                shape_analysis.get("shape_repair_detected")
            ),
            "shape_review_required": bool(
                shape_analysis.get("shape_review_required")
            ),
            "stellar_axis_ratio_median": shape_analysis.get(
                "stellar_axis_ratio_median"
            ),
            "stellar_axis_ratio_p10": shape_analysis.get(
                "stellar_axis_ratio_p10"
            ),
            "stellar_coma_index_median": shape_analysis.get(
                "stellar_coma_index_median"
            ),
            "stellar_coma_index_p90": shape_analysis.get(
                "stellar_coma_index_p90"
            ),
        }
    rgb = image[..., :3]
    if not np.all(np.isfinite(rgb)):
        return {
            **base,
            "status": "review_required",
            "decision": "review_required",
            "reason": "non_finite_input",
            "n_paired_stars": 0,
        }

    luminance = _luminance(rgb)
    centers = _candidate_centers(luminance, fit_radius=fit_radius)
    samples: list[dict[str, Any]] = []
    for center in centers:
        channel_profiles = [
            _channel_moments(rgb[..., channel], center, fit_radius=fit_radius)
            for channel in range(3)
        ]
        if any(profile is None for profile in channel_profiles):
            continue
        red, green, blue = channel_profiles
        red_to_green = np.array(
            [
                red["centroid_y"] - green["centroid_y"],
                red["centroid_x"] - green["centroid_x"],
            ],
            dtype=np.float64,
        )
        blue_to_green = np.array(
            [
                blue["centroid_y"] - green["centroid_y"],
                blue["centroid_x"] - green["centroid_x"],
            ],
            dtype=np.float64,
        )
        channel_axis_ratio = float(np.median([
            red["axis_ratio"],
            green["axis_ratio"],
            blue["axis_ratio"],
        ]))
        samples.append(
            {
                "center_rc": [int(center[0]), int(center[1])],
                "red_to_green_offset_rc": red_to_green.tolist(),
                "blue_to_green_offset_rc": blue_to_green.tolist(),
                "maximum_channel_separation_px": float(
                    max(
                        np.linalg.norm(red_to_green),
                        np.linalg.norm(blue_to_green),
                        np.linalg.norm(red_to_green - blue_to_green),
                    )
                ),
                "axis_ratio": channel_axis_ratio,
                "purple_halo_excess": _purple_halo_excess(
                    rgb,
                    center,
                    fit_radius=fit_radius,
                ),
            }
        )

    if len(samples) < int(min_stars):
        if shape_analysis.get("decision") in {"apply", "review_required"}:
            return {
                **base,
                "status": shape_analysis.get("status"),
                "decision": shape_analysis.get("decision"),
                "reasons": shape_analysis.get("reasons") or [],
                "n_reference_candidates": len(centers),
                "n_paired_stars": shape_analysis.get("n_paired_stars", 0),
                "n_color_paired_stars": len(samples),
                "systematic_shift_detected": False,
                "purple_fringe_detected": False,
                "shape_repair_detected": bool(
                    shape_analysis.get("shape_repair_detected")
                ),
                "shape_review_required": bool(
                    shape_analysis.get("shape_review_required")
                ),
                "stellar_axis_ratio_median": shape_analysis.get(
                    "stellar_axis_ratio_median"
                ),
                "stellar_axis_ratio_p10": shape_analysis.get(
                    "stellar_axis_ratio_p10"
                ),
                "stellar_coma_index_median": shape_analysis.get(
                    "stellar_coma_index_median"
                ),
                "stellar_coma_index_p90": shape_analysis.get(
                    "stellar_coma_index_p90"
                ),
            }
        return {
            **base,
            "status": "insufficient_samples",
            "decision": "skip",
            "reason": "too_few_valid_unsaturated_stars",
            "n_reference_candidates": len(centers),
            "n_paired_stars": len(samples),
            "n_color_paired_stars": len(samples),
        }

    red_offsets = np.asarray(
        [sample["red_to_green_offset_rc"] for sample in samples],
        dtype=np.float64,
    )
    blue_offsets = np.asarray(
        [sample["blue_to_green_offset_rc"] for sample in samples],
        dtype=np.float64,
    )
    red_median = np.median(red_offsets, axis=0)
    blue_median = np.median(blue_offsets, axis=0)
    offset_magnitudes = np.asarray(
        [sample["maximum_channel_separation_px"] for sample in samples],
        dtype=np.float64,
    )
    offset_scatter = float(
        max(
            np.median(np.linalg.norm(red_offsets - red_median, axis=1)),
            np.median(np.linalg.norm(blue_offsets - blue_median, axis=1)),
        )
    )
    systematic_offset = float(
        max(
            np.linalg.norm(red_median),
            np.linalg.norm(blue_median),
            np.linalg.norm(red_median - blue_median),
        )
    )
    purple_values = np.asarray(
        [sample["purple_halo_excess"] for sample in samples],
        dtype=np.float64,
    )
    purple_p90 = float(np.percentile(purple_values, 90.0))
    purple_fraction = float(np.mean(purple_values >= 0.035))
    axis_median = float(
        shape_analysis.get(
            "stellar_axis_ratio_median",
            np.median([sample["axis_ratio"] for sample in samples]),
        )
    )
    axis_p10 = float(
        shape_analysis.get(
            "stellar_axis_ratio_p10",
            np.percentile(
                [sample["axis_ratio"] for sample in samples],
                10.0,
            ),
        )
    )

    systematic_shift_detected = bool(
        0.22 <= systematic_offset <= MAX_AUTOMATIC_CHANNEL_SHIFT_PX
        and offset_scatter <= 0.35
    )
    excessive_or_inconsistent_shift = bool(
        systematic_offset > MAX_AUTOMATIC_CHANNEL_SHIFT_PX
        or (systematic_offset >= 0.22 and offset_scatter > 0.35)
    )
    purple_fringe_detected = bool(
        purple_p90 >= 0.035 and purple_fraction >= 0.25
    )
    shape_repair_detected = bool(
        shape_analysis.get("shape_repair_detected")
    )
    shape_review_required = bool(
        shape_analysis.get("shape_review_required")
    )

    if excessive_or_inconsistent_shift or shape_review_required:
        decision = "review_required"
        status = "review_required"
        reasons = []
        if excessive_or_inconsistent_shift:
            reasons.append("spatially_inconsistent_or_excessive_channel_displacement")
        if shape_review_required:
            reasons.append("significant_stellar_shape_aberration_requires_upstream_review")
    elif (
        systematic_shift_detected
        or purple_fringe_detected
        or shape_repair_detected
    ):
        decision = "apply"
        status = "candidate_recommended"
        reasons = [
            reason
            for enabled, reason in (
                (systematic_shift_detected, "systematic_rgb_channel_displacement"),
                (purple_fringe_detected, "stellar_purple_halo_excess"),
                (shape_repair_detected, "correctable_stellar_shape_aberration"),
            )
            if enabled
        ]
    else:
        decision = "skip"
        status = "not_needed"
        reasons = ["no_correctable_stellar_chromatic_aberration_detected"]

    return {
        **base,
        "status": status,
        "decision": decision,
        "reasons": reasons,
        "n_reference_candidates": len(centers),
        "n_paired_stars": shape_analysis.get("n_paired_stars", len(samples)),
        "n_color_paired_stars": len(samples),
        "red_to_green_offset_rc_median_px": red_median.tolist(),
        "blue_to_green_offset_rc_median_px": blue_median.tolist(),
        "systematic_channel_offset_px": systematic_offset,
        "channel_offset_scatter_px": offset_scatter,
        "maximum_channel_separation_median_px": float(
            np.median(offset_magnitudes)
        ),
        "purple_halo_excess_p90": purple_p90,
        "purple_halo_affected_star_fraction": purple_fraction,
        "stellar_axis_ratio_median": axis_median,
        "stellar_axis_ratio_p10": axis_p10,
        "stellar_coma_index_median": shape_analysis.get(
            "stellar_coma_index_median"
        ),
        "stellar_coma_index_p90": shape_analysis.get(
            "stellar_coma_index_p90"
        ),
        "systematic_shift_detected": systematic_shift_detected,
        "purple_fringe_detected": purple_fringe_detected,
        "shape_repair_detected": shape_repair_detected,
        "shape_review_required": shape_review_required,
        "sample_centers_rc": [
            sample["center_rc"] for sample in samples[:64]
        ],
    }


def _apply_purple_halo_suppression(
    image: np.ndarray,
    *,
    strength: float = 0.15,
    centers_rc: list[object] | None = None,
) -> tuple[np.ndarray, dict[str, Any]]:
    rgb = np.asarray(image[..., :3], dtype=np.float32)
    fit_radius = 7
    valid_centers: list[tuple[int, int]] = []
    for raw_center in centers_rc or []:
        if not isinstance(raw_center, (list, tuple)) or len(raw_center) != 2:
            continue
        try:
            cy = int(round(float(raw_center[0])))
            cx = int(round(float(raw_center[1])))
        except (TypeError, ValueError):
            continue
        if (
            fit_radius <= cy < rgb.shape[0] - fit_radius
            and fit_radius <= cx < rgb.shape[1] - fit_radius
        ):
            valid_centers.append((cy, cx))
    if len(valid_centers) < MIN_PAIRED_STARS:
        return rgb.copy(), {
            "method": "stellar_halo_luminance_preserving_chroma_suppression",
            "support_method": "insufficient_paired_star_radial_support",
            "support_center_count": len(valid_centers),
            "strength": float(strength),
            "mask_fraction": 0.0,
            "core_protection": True,
        }

    yy, xx = np.mgrid[
        -fit_radius : fit_radius + 1,
        -fit_radius : fit_radius + 1,
    ]
    radius = np.sqrt(xx * xx + yy * yy)
    radial_support = np.minimum(
        np.clip((radius - 1.1) / 0.4, 0.0, 1.0),
        np.clip((6.0 - radius) / 0.5, 0.0, 1.0),
    ).astype(np.float32)
    correction = np.zeros(rgb.shape[:2], dtype=np.float32)
    for cy, cx in valid_centers:
        y0, y1 = cy - fit_radius, cy + fit_radius + 1
        x0, x1 = cx - fit_radius, cx + fit_radius + 1
        patch = np.asarray(rgb[y0:y1, x0:x1], dtype=np.float32)
        background = np.percentile(
            patch.reshape(-1, 3),
            20.0,
            axis=0,
        )
        signal = np.clip(patch - background, 0.0, None)
        luminance = _luminance(signal)
        peak = float(np.max(luminance))
        if peak <= 1e-7:
            continue
        signal_support = luminance >= peak * 0.015
        # Correct only the measured blue-over-green stellar excess.  Treating
        # red and blue symmetrically can turn legitimate H-alpha emission near
        # a dense stellar core cyan/green by subtracting source-red signal.
        # Keeping red immutable protects emission structure while still
        # suppressing the blue component of purple/blue stellar annuli.
        blue_excess = np.clip(
            signal[..., 2] - signal[..., 1],
            0.0,
            None,
        )
        local_correction = (
            blue_excess
            * radial_support
            * signal_support
            * float(strength)
        )
        correction_view = correction[y0:y1, x0:x1]
        np.maximum(correction_view, local_correction, out=correction_view)

    # Reduce only the measured blue excess. The small green compensation keeps
    # Rec.709 luminance unchanged before clipping, while the 1.5 px inner
    # radius leaves star cores, source-red emission and geometry untouched.
    blue_delta = correction
    green_delta = blue_delta * (0.0722 / 0.7152)
    corrected = rgb.copy()
    corrected[..., 1] += green_delta
    corrected[..., 2] -= blue_delta
    affected = correction > 1e-7
    return np.asarray(np.clip(corrected, 0.0, 1.0), dtype=np.float32), {
        "method": "stellar_halo_luminance_preserving_chroma_suppression",
        "support_method": "paired_star_measured_radial_annulus",
        "support_center_count": len(valid_centers),
        "support_inner_radius_px": 1.5,
        "support_outer_radius_px": 5.5,
        "strength": float(strength),
        "mask_fraction": float(np.mean(affected)),
        "correction_max": float(np.max(correction)),
        "correction_p90_affected": (
            float(np.percentile(correction[affected], 90.0))
            if np.any(affected)
            else 0.0
        ),
        "core_protection": True,
        "source_red_preserved": True,
        "correction_basis": "stellar_blue_over_green_excess",
        "luminance_preservation": "rec709_analytic_compensation",
    }


def _apply_channel_registration(
    image: np.ndarray,
    analysis: dict[str, Any],
) -> tuple[np.ndarray, dict[str, Any]]:
    candidate = np.asarray(image, dtype=np.float32).copy()
    red_offset = np.asarray(
        analysis["red_to_green_offset_rc_median_px"],
        dtype=np.float64,
    )
    blue_offset = np.asarray(
        analysis["blue_to_green_offset_rc_median_px"],
        dtype=np.float64,
    )
    candidate[..., 0] = shift(
        candidate[..., 0],
        shift=(-float(red_offset[0]), -float(red_offset[1])),
        order=1,
        mode="nearest",
        prefilter=False,
    )
    candidate[..., 2] = shift(
        candidate[..., 2],
        shift=(-float(blue_offset[0]), -float(blue_offset[1])),
        order=1,
        mode="nearest",
        prefilter=False,
    )
    return candidate, {
        "method": "global_subpixel_rgb_channel_registration",
        "reference_channel": "green",
        "red_shift_rc_px": (-red_offset).tolist(),
        "blue_shift_rc_px": (-blue_offset).tolist(),
        "interpolation": "linear",
    }


def _paired_color_gate(
    reference: np.ndarray,
    candidate: np.ndarray,
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    require_alignment: bool = False,
    require_purple: bool = False,
) -> tuple[dict[str, bool], dict[str, Any], dict[str, float]]:
    from star_tools import measure_paired_star_profiles

    reference_rgb = np.asarray(reference[..., :3], dtype=np.float32)
    candidate_rgb = np.asarray(candidate[..., :3], dtype=np.float32)
    paired = measure_paired_star_profiles(
        reference_rgb,
        candidate_rgb,
        min_stars=MIN_PAIRED_STARS,
    )
    reference_luminance = _luminance(reference_rgb)
    candidate_luminance = _luminance(candidate_rgb)
    dark_mask = reference_luminance <= np.percentile(reference_luminance, 30.0)
    reference_background = np.median(reference_rgb[dark_mask], axis=0)
    candidate_background = np.median(candidate_rgb[dark_mask], axis=0)
    background_drift = float(
        np.max(np.abs(candidate_background - reference_background))
    )
    luminance_drift = float(
        np.median(np.abs(candidate_luminance - reference_luminance))
    )
    gates = {
        "finite_and_same_shape": bool(
            candidate.shape == reference.shape
            and np.all(np.isfinite(candidate))
        ),
        "paired_star_samples": bool(
            paired.get("status") != "insufficient_samples"
            and int(paired.get("n_paired_stars", 0)) >= MIN_PAIRED_STARS
        ),
        "fwhm_preserved": bool(
            float(paired.get("candidate_to_reference_fwhm_ratio_median", 99.0))
            <= 1.08
            and float(
                paired.get("candidate_to_reference_fwhm_ratio_p90", 99.0)
            )
            <= 1.18
        ),
        "roundness_not_regressed": bool(
            float(paired.get("candidate_axis_ratio_median", 0.0))
            >= float(paired.get("reference_axis_ratio_median", 1.0)) - 0.03
        ),
        "centroid_preserved": bool(
            float(
                paired.get(
                    "candidate_to_reference_centroid_shift_median_px",
                    99.0,
                )
            )
            <= 0.15
            and float(
                paired.get(
                    "candidate_to_reference_centroid_shift_p95_px",
                    99.0,
                )
            )
            <= 0.25
        ),
        "stellar_brightness_preserved": bool(
            0.82
            <= float(
                paired.get("candidate_to_reference_peak_ratio_median", 0.0)
            )
            <= 1.18
            and float(
                paired.get("candidate_to_reference_peak_ratio_p10", 0.0)
            )
            >= 0.70
            and float(
                paired.get("candidate_to_reference_peak_ratio_p90", 99.0)
            )
            <= 1.35
        ),
        "background_color_preserved": background_drift <= 0.01,
        "global_luminance_preserved": luminance_drift <= 0.002,
    }
    if require_alignment:
        gates["channel_alignment_improved"] = bool(
            float(after.get("systematic_channel_offset_px", 99.0))
            <= float(before.get("systematic_channel_offset_px", 0.0)) * 0.75
        )
    if require_purple:
        gates["purple_halo_improved"] = bool(
            float(after.get("purple_halo_excess_p90", 99.0))
            <= float(before.get("purple_halo_excess_p90", 0.0)) * 0.92
        )
    return gates, paired, {
        "background_channel_median_drift_max": background_drift,
        "median_absolute_luminance_drift": luminance_drift,
    }


def build_stellar_repair_preview(
    before: np.ndarray,
    after: np.ndarray,
    report: dict[str, Any],
    *,
    max_regions: int = 6,
) -> np.ndarray | None:
    """Build a linear before/after contact sheet from accepted paired regions."""
    source = np.asarray(before, dtype=np.float32)
    candidate = np.asarray(after, dtype=np.float32)
    if source.shape != candidate.shape:
        return None
    shape_report = report.get("shape_repair") or {}
    regions = shape_report.get("local_preview_regions") or []
    if not regions:
        centers = (report.get("before") or {}).get("sample_centers_rc") or []
        regions = [
            {"center_rc": center, "radius_px": 12}
            for center in centers
        ]
    rows: list[np.ndarray] = []
    for region in regions[:max(1, int(max_regions))]:
        center = region.get("center_rc")
        if not isinstance(center, (list, tuple)) or len(center) != 2:
            continue
        cy, cx = (int(value) for value in center)
        radius = max(4, min(int(region.get("radius_px", 12)), 24))
        y0, y1 = cy - radius, cy + radius + 1
        x0, x1 = cx - radius, cx + radius + 1
        if y0 < 0 or x0 < 0 or y1 > source.shape[0] or x1 > source.shape[1]:
            continue
        left = source[y0:y1, x0:x1]
        right = candidate[y0:y1, x0:x1]
        separator_shape = list(left.shape)
        separator_shape[1] = 2
        separator = np.zeros(separator_shape, dtype=np.float32)
        rows.append(np.concatenate([left, separator, right], axis=1))
    if not rows:
        return None
    row_width = max(row.shape[1] for row in rows)
    padded_rows = []
    for row in rows:
        if row.shape[1] < row_width:
            pad_shape = list(row.shape)
            pad_shape[1] = row_width - row.shape[1]
            row = np.concatenate(
                [row, np.zeros(pad_shape, dtype=np.float32)],
                axis=1,
            )
        padded_rows.append(row)
    separator_shape = list(padded_rows[0].shape)
    separator_shape[0] = 2
    row_separator = np.zeros(separator_shape, dtype=np.float32)
    contact_sheet = []
    for index, row in enumerate(padded_rows):
        if index:
            contact_sheet.append(row_separator)
        contact_sheet.append(row)
    return np.asarray(np.concatenate(contact_sheet, axis=0), dtype=np.float32)


def run_stellar_repair(
    image: np.ndarray,
    *,
    is_linear: bool = True,
    channel_semantics: str = "broadband_rgb",
    analysis: dict[str, Any] | None = None,
    mode: str = "auto",
    strength_profile: str = "balanced",
    max_runtime_seconds: float = 300.0,
    max_workers: int = 4,
    execute_repairs: bool = True,
    include_preview_image: bool = False,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Run transactional stellar color and shape repair with hard rollback gates."""
    if mode not in {"auto", "color_only", "shape_only"}:
        raise ValueError(
            "stellar_repair mode must be auto, color_only, or shape_only"
        )
    if strength_profile not in {"conservative", "balanced", "strong"}:
        raise ValueError(
            "stellar_repair strength_profile must be conservative, balanced, "
            "or strong"
        )
    source = np.asarray(image, dtype=np.float32)
    started = time.monotonic()
    runtime_limit = min(max(float(max_runtime_seconds), 1.0), 300.0)
    deadline = started + runtime_limit
    before = analyze_stellar_repair(
        source,
        is_linear=is_linear,
        channel_semantics=channel_semantics,
    )
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "skipped",
        "applied": False,
        "rollback_applied": False,
        "analysis_source": "runtime_remeasurement",
        "planning_analysis": analysis,
        "mode": mode,
        "strength_profile": strength_profile,
        "before": before,
        "operations": [],
        "quality_gates": {},
        "resource_policy": {
            "max_workers": max(1, min(int(max_workers), 4)),
            "max_runtime_seconds": runtime_limit,
            "memory_soft_target_bytes": 3 * 1024 * 1024 * 1024,
            "maximum_shape_fit_stars": 4096,
            "cpu_backend": "deterministic_numpy_scipy",
            "external_model": None,
        },
        "execution_policy": (
            "repair_enabled" if execute_repairs else "diagnostic_only"
        ),
    }
    if not is_linear:
        report.update(
            {
                "status": "skipped",
                "reason": "input_is_not_confirmed_linear",
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return source, report
    if time.monotonic() >= deadline:
        report.update(
            {
                "status": "review_required",
                "reason": "stellar_repair_stage_timeout",
                "timed_out": True,
                "rollback_applied": True,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return source, report
    if not execute_repairs:
        requires_review = before.get("decision") in {
            "apply",
            "review_required",
        }
        report.update(
            {
                "status": "review_required" if requires_review else "skipped",
                "reason": "upstream_background_not_trusted_diagnostic_only",
                "diagnostic_only": True,
                "elapsed_seconds": time.monotonic() - started,
            }
        )
        return source, report

    color_allowed = bool(
        source.ndim == 3
        and source.shape[2] >= 3
        and channel_semantics == "broadband_rgb"
        and mode != "shape_only"
    )
    shape_allowed = mode != "color_only"
    current = source.copy()
    any_applied = False
    any_rollback = False
    before_reasons = set(before.get("reasons") or [])
    shape_only_source_review = bool(
        before.get("decision") == "review_required"
        and before_reasons
        and before_reasons
        <= {"significant_stellar_shape_aberration_requires_upstream_review"}
    )
    unresolved_review = bool(
        before.get("decision") == "review_required"
        and not (mode == "color_only" and shape_only_source_review)
    )

    if color_allowed and before.get("systematic_shift_detected"):
        candidate, operation = _apply_channel_registration(current, before)
        after = analyze_stellar_repair(
            candidate,
            is_linear=True,
            channel_semantics=channel_semantics,
        )
        gates, paired, metrics = _paired_color_gate(
            current,
            candidate,
            before=before,
            after=after,
            require_alignment=True,
        )
        accepted = all(gates.values())
        report["operations"].append(
            {
                **operation,
                "status": "applied" if accepted else "rolled_back",
                "quality_gates": gates,
                "paired_star_profiles": paired,
                "metrics": metrics,
            }
        )
        report["quality_gates"].update(gates)
        if accepted:
            current = candidate
            any_applied = True
        else:
            any_rollback = True
            unresolved_review = True

    shape_before = analyze_stellar_shape(current, is_linear=is_linear)
    if shape_allowed and shape_before.get("decision") == "apply":
        remaining = max(1.0, deadline - time.monotonic())
        candidate, shape_report = repair_stellar_shape(
            current,
            analysis=shape_before,
            is_linear=is_linear,
            strength_profile=strength_profile,
            max_runtime_seconds=remaining,
            max_workers=max_workers,
        )
        report["operations"].append(
            {
                "method": "stellar_shape_psf_mapping",
                "status": (
                    "applied"
                    if shape_report.get("applied")
                    else "rolled_back"
                ),
                "report": shape_report,
            }
        )
        report["quality_gates"].update(
            {
                f"shape_{key}": value
                for key, value in (
                    shape_report.get("quality_gates") or {}
                ).items()
            }
        )
        report["shape_repair"] = shape_report
        if shape_report.get("applied"):
            current = candidate
            any_applied = True
            unresolved_review = False
        else:
            any_rollback = True
            unresolved_review = True
    elif shape_before.get("decision") == "review_required":
        report["shape_repair"] = {
            "status": (
                "preserved_unmodified"
                if mode == "color_only"
                else "review_required"
            ),
            "applied": False,
            "before": {
                key: value
                for key, value in shape_before.items()
                if key != "_profiles"
            },
            "reason": (
                "shape_preserved_unmodified_by_color_only_policy"
                if mode == "color_only"
                else shape_before.get("reasons")
            ),
            "automatic_geometry_reconstruction": False,
        }
        if mode != "color_only":
            unresolved_review = True

    if color_allowed:
        if time.monotonic() >= deadline:
            unresolved_review = True
            report["quality_gates"]["runtime_within_budget"] = False
        purple_before = (
            {}
            if time.monotonic() >= deadline
            else analyze_stellar_repair(
                current,
                is_linear=True,
                channel_semantics=channel_semantics,
            )
        )
        if (
            time.monotonic() < deadline
            and purple_before.get("purple_fringe_detected")
        ):
            candidate_trials: list[dict[str, Any]] = []
            selected: tuple[
                np.ndarray,
                dict[str, Any],
                dict[str, bool],
                dict[str, Any],
                dict[str, float],
            ] | None = None
            last_trial: tuple[
                dict[str, Any],
                dict[str, bool],
                dict[str, Any],
                dict[str, float],
            ] | None = None
            purple_before_p90 = float(
                purple_before.get("purple_halo_excess_p90", 0.0)
            )
            for strength in PURPLE_HALO_STRENGTH_LADDERS[strength_profile]:
                if time.monotonic() >= deadline:
                    report["quality_gates"]["runtime_within_budget"] = False
                    break
                candidate_rgb, operation = _apply_purple_halo_suppression(
                    current[..., :3],
                    strength=strength,
                    centers_rc=purple_before.get("sample_centers_rc"),
                )
                if current.shape[2] > 3:
                    candidate = current.copy()
                    candidate[..., :3] = candidate_rgb
                else:
                    candidate = candidate_rgb
                purple_after = analyze_stellar_repair(
                    candidate,
                    is_linear=True,
                    channel_semantics=channel_semantics,
                )
                gates, paired, metrics = _paired_color_gate(
                    current,
                    candidate,
                    before=purple_before,
                    after=purple_after,
                    require_purple=True,
                )
                purple_after_p90 = float(
                    purple_after.get("purple_halo_excess_p90", 99.0)
                )
                improvement_fraction = (
                    1.0 - purple_after_p90 / purple_before_p90
                    if purple_before_p90 > 0.0
                    else 0.0
                )
                candidate_trials.append(
                    {
                        "strength": float(strength),
                        "accepted": all(gates.values()),
                        "purple_halo_excess_p90_before": purple_before_p90,
                        "purple_halo_excess_p90_after": purple_after_p90,
                        "purple_halo_improvement_fraction": float(
                            improvement_fraction
                        ),
                        "failed_gates": [
                            key for key, passed in gates.items() if not passed
                        ],
                    }
                )
                last_trial = (operation, gates, paired, metrics)
                if all(gates.values()):
                    selected = (
                        candidate,
                        operation,
                        gates,
                        paired,
                        metrics,
                    )
                    break

            accepted = selected is not None
            if selected is not None:
                candidate, operation, gates, paired, metrics = selected
            elif last_trial is not None:
                operation, gates, paired, metrics = last_trial
            else:
                operation = {
                    "method": (
                        "stellar_halo_luminance_preserving_chroma_suppression"
                    ),
                    "strength": None,
                }
                gates = {"runtime_within_budget": False}
                paired = {}
                metrics = {}
            report["operations"].append(
                {
                    **operation,
                    "status": "applied" if accepted else "rolled_back",
                    "quality_gates": gates,
                    "paired_star_profiles": paired,
                    "metrics": metrics,
                    "candidate_trials": candidate_trials,
                    "selection_policy": (
                        "lowest_strength_passing_all_hard_gates"
                    ),
                }
            )
            report["quality_gates"].update(gates)
            if accepted:
                current = candidate
                any_applied = True
            else:
                any_rollback = True
                unresolved_review = True

    final = (
        {
            "status": "review_required",
            "decision": "review_required",
            "reason": "final_remeasurement_skipped_stage_timeout",
        }
        if time.monotonic() >= deadline
        else analyze_stellar_repair(
            current,
            is_linear=is_linear,
            channel_semantics=channel_semantics,
        )
    )
    final_reasons = set(final.get("reasons") or [])
    final_shape_only_review = bool(
        final.get("decision") == "review_required"
        and final_reasons
        and final_reasons
        <= {"significant_stellar_shape_aberration_requires_upstream_review"}
    )
    if final.get("decision") == "review_required" and not (
        mode == "color_only" and final_shape_only_review
    ):
        unresolved_review = True
    if (
        mode != "color_only"
        and final.get("shape_repair_detected")
        and not (report.get("shape_repair") or {}).get("applied")
    ):
        unresolved_review = True
    elapsed = time.monotonic() - started
    if elapsed >= runtime_limit:
        unresolved_review = True
        report["quality_gates"]["runtime_within_budget"] = False
    else:
        report["quality_gates"]["runtime_within_budget"] = True

    status = (
        "review_required"
        if unresolved_review
        else "applied"
        if any_applied
        else "skipped"
    )
    report.update(
        {
            "status": status,
            "applied": any_applied,
            "rollback_applied": any_rollback,
            "reason": (
                "one_or_more_substeps_applied_with_unresolved_shape_review"
                if unresolved_review and any_applied
                else "stellar_shape_aberration_requires_review"
                if unresolved_review
                else "all_applied_substeps_passed_hard_gates"
                if any_applied
                else "no_correctable_stellar_repair_detected"
            ),
            "after": final,
            "elapsed_seconds": elapsed,
            "transaction_policy": (
                "independent_substep_rollback_with_final_combined_review"
            ),
            "preserved_limitations": (
                ["source_stellar_shape_preserved_unmodified"]
                if mode == "color_only"
                and shape_before.get("decision") == "review_required"
                else []
            ),
        }
    )
    if any_applied and include_preview_image:
        preview = build_stellar_repair_preview(source, current, report)
        if preview is not None:
            report["_preview_image"] = preview
    return np.asarray(current, dtype=np.float32), report
