"""Baseline-relative quality gates for stretched starless candidates."""

from dataclasses import replace

import numpy as np
from scipy.ndimage import gaussian_filter, sobel

from quality_metrics import calculate_metrics
from starless_profiles import CandidateLevel


HF_LIMITS = {
    CandidateLevel.LOW: .08,
    CandidateLevel.MEDIUM: .15,
    CandidateLevel.HIGH: .25,
}


def _gray(image):
    return np.mean(image[..., :3], axis=2, dtype=np.float32)


def _channel_clip(image):
    return np.mean(image[..., :3] >= .995, axis=(0, 1))


def _protected_hf(image, protection):
    gray = _gray(image)
    detail = np.abs(gray - gaussian_filter(gray, 1.5))
    weight = np.clip(protection, 0, 1)
    return float(np.sum(detail * weight) / max(float(weight.sum()), 1.))


def _continuity(image):
    gray = _gray(image)
    edge = np.hypot(sobel(gray, 0), sobel(gray, 1))
    threshold = np.percentile(edge, 85)
    return float(np.mean(edge >= threshold))


def _gate(code, failed, value, threshold):
    return {
        "code": code,
        "status": "failed" if failed else "passed",
        "value": float(value),
        "threshold": threshold,
    }


def evaluate_candidate(
    baseline_starless,
    candidate_starless,
    baseline_final,
    candidate_final,
    stars_input,
    masks,
    level,
    continuity_required,
):
    before = calculate_metrics(baseline_starless)
    after = calculate_metrics(candidate_starless)
    gates = []

    black_increase = after["nonpositive_pixel_ratio"] - before["nonpositive_pixel_ratio"]
    gates.append(_gate(
        "NEAR_BLACK_INCREASE", black_increase > .02,
        black_increase, "<=0.02 absolute increase"))

    clip_increase = float(np.max(
        _channel_clip(candidate_starless) - _channel_clip(baseline_starless)))
    gates.append(_gate(
        "CHANNEL_CLIP_INCREASE", clip_increase > .005,
        clip_increase, "<=0.005 absolute increase"))

    corner = (
        after["corner_uniformity_ratio"]
        / max(before["corner_uniformity_ratio"], 1e-8) - 1.
    )
    gates.append(_gate(
        "CORNER_UNIFORMITY_DEGRADATION", corner > .20,
        corner, "<=0.20 relative increase"))

    hf_before = _protected_hf(baseline_starless, masks["protection"])
    hf_after = _protected_hf(candidate_starless, masks["protection"])
    hf_increase = (hf_after / max(hf_before, 1e-8)) - 1.
    gates.append(_gate(
        "PROTECTED_HF_INCREASE", hf_increase > HF_LIMITS[level],
        hf_increase, f"<={HF_LIMITS[level]:.2f} relative increase"))

    difference = np.mean(
        np.abs(candidate_starless - baseline_starless), axis=2)
    difference = np.where(difference >= 1e-4, difference, 0.0)
    structure = (
        np.maximum(masks["subject"], masks["dark_structure"]) > .005
    ).astype(np.float32)
    diff_ratio = float(
        np.sum(difference * structure) / max(float(difference.sum()), 1e-8))
    gates.append(_gate(
        "DIFF_OUTSIDE_STRUCTURE", diff_ratio < .70,
        diff_ratio, ">=0.70 inside structure masks"))

    star_mask = np.max(stars_input, axis=2) > .002
    base_star_area = float(np.mean(
        (_gray(baseline_final) - _gray(baseline_starless)) > .002))
    final_star_area = float(np.mean(
        (_gray(candidate_final) - _gray(candidate_starless)) > .002))
    star_area_increase = (
        final_star_area / max(base_star_area, float(np.mean(star_mask)), 1e-8) - 1.
    )
    gates.append(_gate(
        "STAR_AREA_INCREASE", star_area_increase > .10,
        star_area_increase, "<=0.10 relative increase"))

    if continuity_required:
        continuity_loss = 1. - (
            _continuity(candidate_starless)
            / max(_continuity(baseline_starless), 1e-8)
        )
        gates.append(_gate(
            "STRUCTURE_CONTINUITY_LOSS", continuity_loss > .10,
            continuity_loss, "<=0.10 relative loss"))

    failed = [gate for gate in gates if gate["status"] == "failed"]
    return {
        "status": "failed" if failed else "success",
        "gates": gates,
        "failed_codes": [gate["code"] for gate in failed],
        "metrics": {"before": before, "after": after},
    }


def downgrade_params(params, failed_codes):
    weights = dict(params.scale_weights)
    saturation = params.saturation_gain
    tone = params.tone_strength
    star_strength = params.star_strength
    softness = params.star_softness
    if "PROTECTED_HF_INCREASE" in failed_codes:
        weights["small"] *= .45
        weights["medium"] *= .65
        weights["large"] *= .80
        tone *= .70
    if "HALO_OR_DOUBLE_EDGE" in failed_codes:
        weights["small"] *= .65
        weights["medium"] *= .80
    if "NEAR_BLACK_INCREASE" in failed_codes:
        tone *= .65
        weights["large"] *= .80
    if "CHANNEL_CLIP_INCREASE" in failed_codes:
        saturation *= .65
    if "STAR_AREA_INCREASE" in failed_codes:
        star_strength = min(star_strength + .08, 1.)
        softness *= .60
    if "DIFF_OUTSIDE_STRUCTURE" in failed_codes:
        weights = {key: value * .75 for key, value in weights.items()}
    return replace(
        params,
        scale_weights=weights,
        saturation_gain=saturation,
        tone_strength=tone,
        star_strength=star_strength,
        star_softness=softness,
    )
