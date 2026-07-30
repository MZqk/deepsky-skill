#!/usr/bin/env python3
"""Match a reference image's global tone and color without copying structure."""

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter
from skimage.transform import resize

from fits_io import read_image, write_image
from enhance import protected_hdr_compress
from stretch import very_dark_stretch


LUMA_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _rgb(image):
    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 2:
        source = np.stack([source] * 3, axis=-1)
    return np.clip(source[..., :3], 0, 1)


def _luminance(image):
    return np.sum(_rgb(image) * LUMA_WEIGHTS, axis=-1)


def _sample(values, max_samples=1_000_000):
    flat = np.asarray(values, dtype=np.float32).reshape(-1)
    if flat.size <= max_samples:
        return flat
    stride = max(1, flat.size // max_samples)
    return flat[::stride]


def _percentiles(values, points):
    return np.percentile(_sample(values), points).astype(np.float32)


def _strictly_increasing(values, epsilon=1e-5):
    result = np.asarray(values, dtype=np.float32).copy()
    for index in range(1, len(result)):
        result[index] = max(result[index], result[index - 1] + epsilon)
    return result


def _tone_match(source_luma, reference_luma, strength):
    percentiles = np.array([0.0, 1.0, 10.0, 35.0, 60.0, 85.0, 97.0, 99.7, 100.0])
    source_points = _strictly_increasing(_percentiles(source_luma, percentiles))
    reference_points = _percentiles(reference_luma, percentiles)

    # Keep reference matching bounded: avoid crushing shadows or blowing stars.
    reference_points[0] = 0.0
    reference_points[1] = max(reference_points[1], 0.003)
    reference_points[-1] = 1.0
    reference_points = np.maximum.accumulate(reference_points)

    matched = np.interp(
        source_luma.reshape(-1),
        source_points,
        reference_points,
    ).reshape(source_luma.shape)
    blended = source_luma * (1.0 - strength) + matched * strength
    return np.clip(blended, 0, 1), {
        "percentiles": percentiles.tolist(),
        "source": source_points.round(6).tolist(),
        "reference": reference_points.round(6).tolist(),
    }


def _background_channel_gains(source, reference, max_gain):
    source_luma = _luminance(source)
    reference_luma = _luminance(reference)
    source_limit = float(np.percentile(_sample(source_luma), 60))
    reference_limit = float(np.percentile(_sample(reference_luma), 60))
    source_mask = source_luma <= source_limit
    reference_mask = reference_luma <= reference_limit

    source_rgb = np.median(source[source_mask], axis=0)
    reference_rgb = np.median(reference[reference_mask], axis=0)
    source_chroma = source_rgb / max(float(np.mean(source_rgb)), 1e-6)
    reference_chroma = reference_rgb / max(float(np.mean(reference_rgb)), 1e-6)
    gains = reference_chroma / np.maximum(source_chroma, 1e-6)
    gains /= max(float(np.exp(np.mean(np.log(np.maximum(gains, 1e-6))))), 1e-6)
    return np.clip(gains, 1.0 / max_gain, max_gain).astype(np.float32)


def _saturation_stat(image):
    rgb = _rgb(image)
    high = np.max(rgb, axis=-1)
    low = np.min(rgb, axis=-1)
    saturation = (high - low) / np.maximum(high, 1e-5)
    mask = (_luminance(rgb) > 0.03) & (_luminance(rgb) < 0.85)
    values = saturation[mask]
    return float(np.median(values)) if values.size else float(np.median(saturation))


def _decision_preview(image, max_dimension=640):
    source = _rgb(image)
    h, w = source.shape[:2]
    scale = min(1.0, float(max_dimension) / max(h, w))
    if scale >= 1.0:
        return source
    return resize(
        source,
        (max(32, round(h * scale)), max(32, round(w * scale)), 3),
        preserve_range=True,
        anti_aliasing=True,
    ).astype(np.float32)


def _brightness_class(image):
    luma = _luminance(image)
    median = float(np.median(luma))
    p99 = float(np.percentile(luma, 99.0))
    nonzero = float(np.mean(luma > 0))
    very_dark = (
        nonzero >= 0.05
        and (
            (median < 0.001 and p99 < 0.02)
            or (median < 0.01 and p99 < 0.08)
        )
    )
    return {
        "median": median,
        "p99": p99,
        "nonzero_fraction": nonzero,
        "very_dark": bool(very_dark),
    }


def _apply_global_parameters(source, stretch_factor=0.0, gamma=1.0,
                             target_bg=None, saturation=1.0,
                             hdr_strength=0.0, very_dark_mode=False):
    """Apply only global, non-structural transformations."""
    result = _rgb(source)
    luma = _luminance(result)

    if very_dark_mode and stretch_factor > 0:
        result = very_dark_stretch(
            result,
            factor=float(stretch_factor),
            gamma=float(gamma),
            target_bg=float(target_bg or 0.10),
            min_p99=0.45,
        )
    elif stretch_factor > 0:
        scale = max(float(np.percentile(luma, 99.5)), 1e-6)
        normalized = np.clip(luma / scale, 0, None)
        target_luma = (
            np.arcsinh(normalized * float(stretch_factor))
            / np.arcsinh(float(stretch_factor))
        )
        target_luma = np.power(
            np.clip(target_luma, 0, 1),
            float(gamma),
        )
        gain = target_luma / np.maximum(luma, 1e-6)
        result = np.clip(result * gain[..., None], 0, 1)

    if target_bg is not None and not very_dark_mode:
        luma = _luminance(result)
        background = luma <= np.percentile(luma, 50.0)
        current_bg = float(np.median(luma[background]))
        if current_bg > 1e-5:
            result = np.clip(
                result * (float(target_bg) / current_bg),
                0,
                1,
            )

    if hdr_strength > 0:
        result, _ = protected_hdr_compress(
            result,
            strength=float(hdr_strength),
            knee_percentile=88.0,
        )

    if saturation != 1.0:
        luma = _luminance(result)
        result = np.clip(
            luma[..., None]
            + (result - luma[..., None]) * float(saturation),
            0,
            1,
        )
    return result.astype(np.float32)


def _global_match_score(candidate, reference):
    """Score global statistics only; never compare aligned pixels or structure."""
    candidate = _rgb(candidate)
    reference = _rgb(reference)
    points = np.array([1, 10, 35, 50, 75, 90, 97, 99.5])
    candidate_luma = _luminance(candidate)
    reference_luma = _luminance(reference)
    candidate_points = _percentiles(candidate_luma, points)
    reference_points = _percentiles(reference_luma, points)
    tone_error = float(np.mean(np.abs(candidate_points - reference_points)))

    saturation_error = abs(
        _saturation_stat(candidate) - _saturation_stat(reference)
    )
    candidate_bg = np.median(
        candidate[candidate_luma <= np.percentile(candidate_luma, 50)],
        axis=0,
    )
    reference_bg = np.median(
        reference[reference_luma <= np.percentile(reference_luma, 50)],
        axis=0,
    )
    candidate_chroma = candidate_bg / max(float(np.mean(candidate_bg)), 1e-6)
    reference_chroma = reference_bg / max(float(np.mean(reference_bg)), 1e-6)
    chroma_error = float(np.mean(np.abs(candidate_chroma - reference_chroma)))
    clip_penalty = float(np.mean(candidate_luma >= 0.995)) * 2.0
    score = tone_error + saturation_error * 0.15 + chroma_error * 0.08 + clip_penalty
    return score, {
        "tone_error": tone_error,
        "saturation_error": float(saturation_error),
        "background_chroma_error": chroma_error,
        "highlight_clip_penalty": clip_penalty,
    }


def optimize_reference_grade(
    source,
    reference,
    strength=0.85,
    max_color_gain=1.25,
    max_saturation=1.45,
    local_contrast=0.10,
    preview_size=640,
):
    """Search bounded global parameters on previews, then execute on full input."""
    source = _rgb(source)
    reference = _rgb(reference)
    source_preview = _decision_preview(source, preview_size)
    reference_preview = _decision_preview(reference, preview_size)
    brightness = _brightness_class(source_preview)

    reference_bg = float(np.median(_luminance(reference_preview)))
    target_bg_values = sorted({
        round(float(np.clip(reference_bg, 0.04, 0.18)), 4),
        round(float(np.clip(reference_bg * 0.8, 0.04, 0.18)), 4),
    })
    if brightness["very_dark"]:
        stretch_factors = (20.0, 30.0, 45.0)
        gammas = (0.40, 0.46, 0.52)
    else:
        stretch_factors = (0.0, 12.0)
        gammas = (0.65, 0.85, 1.0)

    source_sat = _saturation_stat(source_preview)
    reference_sat = _saturation_stat(reference_preview)
    desired_sat = float(np.clip(
        reference_sat / max(source_sat, 1e-4),
        0.85,
        max_saturation,
    ))
    saturation_values = sorted({1.0, round(desired_sat, 3)})
    hdr_values = (0.0, 0.25, 0.45)

    best = None
    evaluated = 0
    for factor in stretch_factors:
        for gamma in gammas:
            for target_bg in target_bg_values:
                for saturation in saturation_values:
                    for hdr_strength in hdr_values:
                        prepared = _apply_global_parameters(
                            source_preview,
                            stretch_factor=factor,
                            gamma=gamma,
                            target_bg=target_bg,
                            saturation=saturation,
                            hdr_strength=hdr_strength,
                        )
                        candidate, _ = match_reference_grade(
                            prepared,
                            reference_preview,
                            strength=strength,
                            max_color_gain=max_color_gain,
                            max_saturation=max_saturation,
                            local_contrast=local_contrast,
                        )
                        score, components = _global_match_score(
                            candidate,
                            reference_preview,
                        )
                        evaluated += 1
                        if best is None or score < best["score"]:
                            best = {
                                "score": score,
                                "components": components,
                                "params": {
                                    "stretch_factor": factor,
                                    "gamma": gamma,
                                    "target_bg": target_bg,
                                    "saturation": saturation,
                                    "hdr_strength": hdr_strength,
                                    "very_dark_mode": brightness["very_dark"],
                                },
                            }

    prepared_full = _apply_global_parameters(source, **best["params"])
    result, grade_report = match_reference_grade(
        prepared_full,
        reference,
        strength=strength,
        max_color_gain=max_color_gain,
        max_saturation=max_saturation,
        local_contrast=local_contrast,
    )
    final_score, final_components = _global_match_score(result, reference)
    report = {
        "method": "bounded_global_parameter_search",
        "structural_transfer": False,
        "spatial_alignment_used": False,
        "source_brightness": brightness,
        "preview_max_dimension": int(preview_size),
        "evaluated_candidates": evaluated,
        "selected_params": best["params"],
        "preview_score": float(best["score"]),
        "preview_score_components": best["components"],
        "final_global_score": float(final_score),
        "final_score_components": final_components,
        "grade": grade_report,
    }
    return result.astype(np.float32), report


def match_reference_grade(
    source,
    reference,
    strength=0.85,
    max_color_gain=1.25,
    max_saturation=1.45,
    local_contrast=0.10,
):
    source = _rgb(source)
    reference = _rgb(reference)
    source_luma = _luminance(source)
    reference_luma = _luminance(reference)

    target_luma, tone_report = _tone_match(source_luma, reference_luma, strength)
    ratio = target_luma / np.maximum(source_luma, 1e-4)
    ratio = np.clip(ratio, 0.45, 3.0)
    graded = np.clip(source * ratio[..., None], 0, 1)

    gains = _background_channel_gains(graded, reference, max_color_gain)
    graded = np.clip(graded * gains, 0, 1)

    source_sat = _saturation_stat(graded)
    reference_sat = _saturation_stat(reference)
    saturation_factor = np.clip(
        reference_sat / max(source_sat, 1e-4),
        0.8,
        max_saturation,
    )
    luma = _luminance(graded)
    graded = np.clip(
        luma[..., None] + (graded - luma[..., None]) * saturation_factor,
        0,
        1,
    )

    if local_contrast > 0:
        luma = _luminance(graded)
        broad = gaussian_filter(luma, sigma=12)
        detail = luma - broad
        signal_mask = np.clip((luma - 0.03) / 0.35, 0, 1)
        enhanced_luma = np.clip(
            luma + detail * signal_mask * float(local_contrast),
            0,
            1,
        )
        luma_ratio = np.clip(
            enhanced_luma / np.maximum(luma, 1e-4),
            0.8,
            1.25,
        )
        graded = np.clip(graded * luma_ratio[..., None], 0, 1)

    report = {
        "method": "global_reference_grade",
        "structural_transfer": False,
        "tone": tone_report,
        "channel_gains": gains.round(5).tolist(),
        "source_saturation": round(source_sat, 5),
        "reference_saturation": round(reference_sat, 5),
        "saturation_factor": round(float(saturation_factor), 5),
        "strength": float(strength),
        "local_contrast": float(local_contrast),
    }
    return graded.astype(np.float32), report


def main():
    parser = argparse.ArgumentParser(
        description="Match global tone/color to a reference without copying reference structure."
    )
    parser.add_argument("input")
    parser.add_argument("reference")
    parser.add_argument("output")
    parser.add_argument("--strength", type=float, default=0.85)
    parser.add_argument("--max-color-gain", type=float, default=1.25)
    parser.add_argument("--max-saturation", type=float, default=1.45)
    parser.add_argument("--local-contrast", type=float, default=0.10)
    parser.add_argument(
        "--match-orientation",
        action="store_true",
        help="Rotate output 90 degrees clockwise when reference orientation differs.",
    )
    parser.add_argument("--report", default=None)
    parser.add_argument(
        "--auto-search",
        action="store_true",
        help="在缩略图上搜索受约束的全局拉伸/背景/饱和/HDR参数",
    )
    parser.add_argument(
        "--preview-size",
        type=int,
        default=640,
        help="参数搜索缩略图最长边（默认: 640）",
    )
    args = parser.parse_args()

    source, _ = read_image(args.input)
    reference, _ = read_image(args.reference)
    grade_kwargs = {
        "strength": float(np.clip(args.strength, 0.0, 1.0)),
        "max_color_gain": max(1.0, args.max_color_gain),
        "max_saturation": max(1.0, args.max_saturation),
        "local_contrast": float(np.clip(args.local_contrast, 0.0, 0.35)),
    }
    if args.auto_search:
        result, report = optimize_reference_grade(
            source,
            reference,
            preview_size=max(128, args.preview_size),
            **grade_kwargs,
        )
    else:
        result, report = match_reference_grade(
            source,
            reference,
            **grade_kwargs,
        )

    orientation_changed = False
    if args.match_orientation:
        source_landscape = result.shape[1] >= result.shape[0]
        reference_landscape = reference.shape[1] >= reference.shape[0]
        if source_landscape != reference_landscape:
            result = np.rot90(result, k=3)
            orientation_changed = True

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_image(result, args.output)
    report["orientation_changed"] = orientation_changed
    report["output_shape"] = list(result.shape)

    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
