#!/usr/bin/env python3
"""Deterministic multi-scale masks and masked adjustment operators."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter

from color_conv import safe_hsv2rgb, safe_rgb2hsv
from enhance import apply_curves, local_contrast_enhance
from fits_io import read_image, write_image
from star_tools import detect_stars_multiscale
from stretch import arcsinh_stretch


SCHEMA_VERSION = "1.0"
COLOR_PRESETS = {
    "ha": [(0.94, 1.0), (0.0, 0.06)],
    "oiii": [(0.43, 0.58)],
    "blue": [(0.55, 0.72)],
    "green": [(0.25, 0.45)],
}
MASK_TYPES = {"range", "color", "star", "combine"}
ADJUSTMENTS = {"arcsinh", "saturation", "local_contrast", "curves"}


def luminance(image):
    image = np.asarray(image, dtype=np.float32)
    if image.ndim == 2:
        return image
    return (
        0.299 * image[..., 0]
        + 0.587 * image[..., 1]
        + 0.114 * image[..., 2]
    ).astype(np.float32)


def _smoothstep(edge0, edge1, values):
    if edge1 <= edge0:
        return (values >= edge1).astype(np.float32)
    x = np.clip((values - edge0) / (edge1 - edge0), 0, 1)
    return (x * x * (3.0 - 2.0 * x)).astype(np.float32)


def multiscale_feather(mask, scales=(0.0, 2.0, 8.0), weights=None):
    """Blend several Gaussian scales into a soft, halo-resistant mask."""
    mask = np.clip(np.asarray(mask, dtype=np.float32), 0, 1)
    scales = [float(scale) for scale in scales]
    if not scales:
        return mask
    weights = weights or [1.0 / len(scales)] * len(scales)
    if len(weights) != len(scales):
        raise ValueError("multiscale weights must match scales")
    total = max(float(sum(weights)), 1e-8)
    result = np.zeros_like(mask, dtype=np.float32)
    for scale, weight in zip(scales, weights):
        layer = gaussian_filter(mask, sigma=scale) if scale > 0 else mask
        result += layer * float(weight)
    return np.clip(result / total, 0, 1)


def create_range_mask(image, low=0.0, high=1.0, feather=0.03,
                      multiscale=(0.0, 2.0, 8.0), invert=False):
    """Select a luminance range with soft low/high transitions."""
    low = float(np.clip(low, 0, 1))
    high = float(np.clip(high, 0, 1))
    if high <= low:
        raise ValueError("range mask requires high > low")
    gray = np.clip(luminance(image), 0, 1)
    feather = max(float(feather), 1e-6)
    rising = _smoothstep(low - feather, low + feather, gray)
    falling = 1.0 - _smoothstep(high - feather, high + feather, gray)
    mask = multiscale_feather(rising * falling, multiscale)
    return 1.0 - mask if invert else mask


def _hue_interval_mask(hue, low, high, feather):
    low = float(low) % 1.0
    raw_high = float(high)
    high = 1.0 if raw_high == 1.0 else raw_high % 1.0

    def linear_interval(start, end):
        rising = _smoothstep(start - feather, start + feather, hue)
        falling = 1.0 - _smoothstep(end - feather, end + feather, hue)
        return rising * falling

    if low <= high:
        return linear_interval(low, high)
    return np.maximum(
        linear_interval(low, 1.0),
        (
            _smoothstep(-feather, feather, hue)
            * (1.0 - _smoothstep(high - feather, high + feather, hue))
        ),
    )


def create_color_mask(image, hue_range=None, preset=None, saturation_min=0.12,
                      value_min=0.02, feather=0.025,
                      multiscale=(0.0, 2.0, 8.0), invert=False):
    """Select hues while suppressing neutral background and weak color noise."""
    source = np.asarray(image, dtype=np.float32)
    if source.ndim != 3 or source.shape[2] < 3:
        raise ValueError("color mask requires RGB input")
    hsv = safe_rgb2hsv(np.clip(source[..., :3], 0, 1))
    intervals = COLOR_PRESETS.get(str(preset).lower()) if preset else None
    if intervals is None:
        if hue_range is None or len(hue_range) != 2:
            raise ValueError("color mask requires preset or hue_range=[low,high]")
        intervals = [hue_range]
    hue_mask = np.zeros(hsv.shape[:2], dtype=np.float32)
    for low, high in intervals:
        hue_mask = np.maximum(
            hue_mask,
            _hue_interval_mask(hsv[..., 0], float(low), float(high), feather),
        )
    saturation_weight = _smoothstep(
        float(saturation_min) * 0.7,
        float(saturation_min),
        hsv[..., 1],
    )
    value_weight = _smoothstep(
        float(value_min) * 0.5,
        float(value_min),
        hsv[..., 2],
    )
    mask = multiscale_feather(
        hue_mask * saturation_weight * value_weight, multiscale
    )
    return 1.0 - mask if invert else mask


def create_star_mask(image, fwhm=None, threshold=0.85, expand=1,
                     multiscale=(0.0, 1.5, 4.0), invert=False):
    """Create an FWHM-aware multi-scale stellar protection mask."""
    detected, confidence, details = detect_stars_multiscale(
        image,
        fwhm=fwhm,
        star_threshold=float(threshold),
        gradient_aware=True,
        n_scales=4,
        return_details=True,
    )
    mask = detected > 0.2
    fallback_applied = False
    if not np.any(mask):
        gray = np.clip(luminance(image), 0, 1)
        if float(np.max(gray) - np.median(gray)) > 1e-5:
            threshold_value = max(
                float(np.percentile(gray, 99.7)),
                float(np.median(gray) + 5.0 * np.std(gray)),
            )
            mask = gray >= threshold_value
            fallback_applied = bool(np.any(mask))
    if int(expand) > 0:
        mask = binary_dilation(mask, iterations=int(expand))
    mask = multiscale_feather(mask.astype(np.float32), multiscale)
    if invert:
        mask = 1.0 - mask
    return mask, {
        "confidence": round(float(confidence), 4),
        "estimated_fwhm": details.get("fwhm"),
        "components_total": details.get("n_components_total", 0),
        "components_kept": details.get("n_components_kept", 0),
        "fallback_applied": fallback_applied,
        "fallback_reason": (
            "low_confidence_conservative_bright_source_protection"
            if fallback_applied else None
        ),
    }


def create_mask(image, spec):
    """Build one mask from a JSON-safe recursive specification."""
    if not isinstance(spec, dict):
        raise ValueError("mask spec must be an object")
    mask_type = spec.get("type")
    if mask_type not in MASK_TYPES:
        raise ValueError(f"unknown mask type: {mask_type}")
    metadata = {"type": mask_type}

    if mask_type == "range":
        mask = create_range_mask(
            image,
            low=spec.get("low", 0.0),
            high=spec.get("high", 1.0),
            feather=spec.get("feather", 0.03),
            multiscale=spec.get("scales", (0.0, 2.0, 8.0)),
            invert=spec.get("invert", False),
        )
    elif mask_type == "color":
        mask = create_color_mask(
            image,
            hue_range=spec.get("hue_range"),
            preset=spec.get("preset"),
            saturation_min=spec.get("saturation_min", 0.12),
            value_min=spec.get("value_min", 0.02),
            feather=spec.get("feather", 0.025),
            multiscale=spec.get("scales", (0.0, 2.0, 8.0)),
            invert=spec.get("invert", False),
        )
    elif mask_type == "star":
        mask, star_meta = create_star_mask(
            image,
            fwhm=spec.get("fwhm"),
            threshold=spec.get("threshold", 0.85),
            expand=spec.get("expand", 1),
            multiscale=spec.get("scales", (0.0, 1.5, 4.0)),
            invert=spec.get("invert", False),
        )
        metadata.update(star_meta)
    else:
        children = spec.get("masks")
        if not isinstance(children, list) or len(children) < 2:
            raise ValueError("combine mask requires at least two child masks")
        built = [create_mask(image, child) for child in children]
        masks = [item[0] for item in built]
        mode = spec.get("mode", "and")
        if mode == "and":
            mask = np.minimum.reduce(masks)
        elif mode == "or":
            mask = np.maximum.reduce(masks)
        elif mode == "subtract":
            mask = masks[0].copy()
            for child in masks[1:]:
                mask *= 1.0 - child
        else:
            raise ValueError("combine mode must be and, or, or subtract")
        mask = multiscale_feather(
            mask,
            spec.get("scales", (0.0, 1.5, 4.0)),
        )
        if spec.get("invert"):
            mask = 1.0 - mask
        metadata["mode"] = mode
        metadata["children"] = [item[1] for item in built]

    metadata.update(mask_statistics(mask))
    return np.clip(mask, 0, 1).astype(np.float32), metadata


def mask_statistics(mask):
    mask = np.asarray(mask, dtype=np.float32)
    return {
        "coverage_soft": round(float(np.mean(mask)), 6),
        "coverage_50pct": round(float(np.mean(mask >= 0.5)), 6),
        "min": round(float(np.min(mask)), 6),
        "max": round(float(np.max(mask)), 6),
    }


def apply_masked_adjustment(image, mask, adjustment):
    """Apply a deterministic operator and blend it through a soft mask."""
    if not isinstance(adjustment, dict):
        raise ValueError("adjustment must be an object")
    method = adjustment.get("method")
    if method not in ADJUSTMENTS:
        raise ValueError(f"unknown masked adjustment: {method}")
    source = np.clip(np.asarray(image, dtype=np.float32), 0, 1)
    strength = float(np.clip(adjustment.get("strength", 1.0), 0, 1.5))

    if method == "arcsinh":
        processed = arcsinh_stretch(
            source,
            factor=float(np.clip(adjustment.get("factor", 20.0), 1.0, 150.0)),
            black_point=float(np.clip(adjustment.get("black_point", 0.0), 0, 0.5)),
        )
    elif method == "saturation":
        if source.ndim != 3 or source.shape[2] < 3:
            raise ValueError("saturation adjustment requires RGB input")
        hsv = safe_rgb2hsv(source[..., :3])
        hsv[..., 1] = np.clip(
            hsv[..., 1]
            * float(np.clip(adjustment.get("factor", 1.2), 0.5, 2.5)),
            0,
            1,
        )
        processed = safe_hsv2rgb(hsv)
    elif method == "local_contrast":
        processed = local_contrast_enhance(
            source,
            radius=float(np.clip(adjustment.get("radius", 12.0), 1.0, 100.0)),
            strength=float(np.clip(adjustment.get("amount", 0.25), 0.0, 1.0)),
        )
    else:
        processed = apply_curves(
            source,
            shadows=float(np.clip(adjustment.get("shadows", 1.0), 0.5, 1.5)),
            midtones=float(np.clip(adjustment.get("midtones", 1.15), 0.5, 1.5)),
            highlights=float(np.clip(adjustment.get("highlights", 1.0), 0.5, 1.5)),
        )

    weight = np.clip(mask * strength, 0, 1)
    if source.ndim == 3:
        weight = weight[..., None]
    return np.clip(source * (1.0 - weight) + processed * weight, 0, 1)


def execute_masked_adjustment(image, mask_spec, adjustment):
    mask, metadata = create_mask(image, mask_spec)
    result = apply_masked_adjustment(image, mask, adjustment)
    coverage = metadata["coverage_soft"]
    gates = []
    if coverage < 0.005:
        gates.append({
            "code": "MASK_NEAR_EMPTY",
            "status": "failed",
            "value": coverage,
            "threshold": ">=0.005",
            "message": "蒙版覆盖率过低，局部处理可能无实际效果",
        })
    elif coverage > 0.85:
        gates.append({
            "code": "MASK_TOO_BROAD",
            "status": "warning",
            "value": coverage,
            "threshold": "<=0.85",
            "message": "蒙版覆盖大部分画面，需确认是否退化为全局处理",
        })
    return result, mask, {
        "schema_version": SCHEMA_VERSION,
        "status": "review_required" if gates else "success",
        "mask": metadata,
        "adjustment": adjustment,
        "quality_gates": gates,
    }


def main():
    parser = argparse.ArgumentParser(description="Multi-scale astronomical mask tool")
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--mask-spec", required=True, help="JSON object or JSON file")
    parser.add_argument("--adjustment", required=True, help="JSON object or JSON file")
    parser.add_argument("--mask-output")
    parser.add_argument("--report")
    args = parser.parse_args()

    def parse_json(value):
        path = Path(value)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return json.loads(value)

    image, _ = read_image(args.input)
    result, mask, report = execute_masked_adjustment(
        image, parse_json(args.mask_spec), parse_json(args.adjustment)
    )
    write_image(result, args.output)
    if args.mask_output:
        write_image(mask, args.mask_output)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
