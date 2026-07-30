"""Deterministic multi-scale enhancement for stretched starless images."""

from dataclasses import asdict

import numpy as np
from scipy.ndimage import gaussian_filter

from color_conv import safe_lab2rgb, safe_rgb2lab
from starless_diagnostics import ensure_rgb_float32


def choose_adaptive_scales(shape):
    short = float(min(shape))
    return {
        "small": max(1.2, short / 160.),
        "medium": max(4., short / 40.),
        "large": max(16., short / 10.),
    }


def decompose_luminance(luminance, scales):
    smooth_small = gaussian_filter(luminance, scales["small"])
    smooth_medium = gaussian_filter(luminance, scales["medium"])
    smooth_large = gaussian_filter(luminance, scales["large"])
    return {
        "small": luminance - smooth_small,
        "medium": smooth_small - smooth_medium,
        "large": smooth_medium - smooth_large,
        "base": smooth_large,
    }


def enhance_starless_candidate(image, masks, params):
    source = ensure_rgb_float32(image)
    lab = safe_rgb2lab(source)
    luma = np.clip(lab[..., 0] / 100., 0, 1)
    scales = choose_adaptive_scales(luma.shape)
    layers = decompose_luminance(luma, scales)
    allowed = np.clip(
        masks["subject"] * (1. - masks["protection"]), 0, 1)
    detail = sum(
        layers[name] * params.scale_weights[name]
        for name in ("large", "medium", "small")
    ) * allowed

    dark_context = np.clip(gaussian_filter(luma, 6.) - luma, 0, None)
    dark_scale = max(float(np.percentile(dark_context, 99)), 1e-8)
    dark_lift = (
        np.clip(dark_context / dark_scale, 0, 1)
        * masks["dark_structure"]
        * (1. - masks["protection"])
    )
    tone = params.tone_strength * allowed * (luma - .5) * 4 * luma * (1-luma)
    target = np.clip(
        luma + detail + tone
        + params.dark_structure_weight * dark_lift * (1. - luma) * .04,
        0, 1,
    )
    lab[..., 0] = target * 100.
    chroma_gain = 1. + params.saturation_gain * masks["subject"][..., None]
    lab[..., 1:3] *= chroma_gain
    result = np.clip(safe_lab2rgb(lab), 0, 1).astype(np.float32)

    change = np.mean(np.abs(result - source), axis=2)
    total = float(change.sum()) or 1.
    structure = np.maximum(masks["subject"], masks["dark_structure"])
    report = {
        "level": params.level.value,
        "scales": scales,
        "effective_change_ratio": float(np.mean(change > 1e-4)),
        "subject_change_energy_ratio": float(
            np.sum(change * structure) / total),
        "params": asdict(params),
    }
    report["params"]["level"] = params.level.value
    artifacts = {
        f"detail_{name}": layers[name].astype(np.float32)
        for name in ("large", "medium", "small")
    }
    return result, report, artifacts
