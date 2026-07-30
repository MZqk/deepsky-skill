#!/usr/bin/env python3
"""Beautify a stretched StarNet2 starless layer, then recombine stars."""

import argparse
import json
from pathlib import Path

import numpy as np
from astropy.io import fits
from scipy.ndimage import gaussian_filter
import tifffile

from fits_io import write_image
from starless_profiles import CandidateLevel, CandidateParams
from stellar_recompose import process_stars_layer, recompose_additive


LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def read_rgb(path):
    suffix = Path(path).suffix.lower()
    if suffix in {".fit", ".fits", ".fts"}:
        image = fits.getdata(path)
        if image.ndim == 3 and image.shape[0] in (3, 4):
            image = np.moveaxis(image, 0, -1)
    elif suffix in {".tif", ".tiff"}:
        try:
            image = tifffile.imread(path)
        except ValueError:
            import cv2
            image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            if image is None:
                raise
            if image.ndim == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    else:
        from fits_io import read_image
        image, _ = read_image(path)
    original_dtype = np.asarray(image).dtype
    image = np.asarray(image)
    if np.issubdtype(original_dtype, np.integer):
        image = image.astype(np.float32) / float(np.iinfo(original_dtype).max)
    else:
        image = image.astype(np.float32)
    if image.ndim == 2:
        image = np.stack([image] * 3, axis=-1)
    return np.clip(image[..., :3], 0, 1)


def luminance(image):
    return np.sum(image * LUMA, axis=-1)


def beautify_starless(image, gamma=0.88, saturation=1.12,
                      local_contrast=0.12, target_median=0.135):
    source = np.clip(
        np.asarray(image, dtype=np.float32).copy(),
        0,
        1,
    )
    luma = luminance(source).astype(np.float32, copy=True)
    toned = np.power(np.clip(luma, 0, 1), float(gamma))
    current_median = float(np.median(toned))
    if current_median > 1e-6:
        toned = toned * (float(target_median) / current_median)
    toned = np.clip(toned, 0, 1).astype(np.float32)

    broad = gaussian_filter(toned, sigma=14).astype(np.float32, copy=False)
    # Use explicit output arrays. With the Python 3.14/NumPy build bundled in
    # some Siril environments, borrowed-reference binary ops may reuse the
    # left operand and silently replace ``toned`` with the detail layer.
    detail = np.empty_like(toned)
    np.subtract(toned, broad, out=detail)
    signal_mask = np.empty_like(toned)
    np.subtract(toned, 0.04, out=signal_mask)
    np.divide(signal_mask, 0.30, out=signal_mask)
    np.clip(signal_mask, 0, 1, out=signal_mask)
    contrast_delta = np.empty_like(detail)
    np.multiply(detail, signal_mask, out=contrast_delta)
    np.multiply(contrast_delta, float(local_contrast), out=contrast_delta)
    enhanced_tone = np.empty_like(toned)
    np.add(toned, contrast_delta, out=enhanced_tone)
    toned = np.clip(
        enhanced_tone,
        0,
        1,
    )

    ratio = np.divide(
        toned,
        np.maximum(luma, 1e-4),
        dtype=np.float32,
    )
    ratio = np.clip(ratio, 0.55, 2.5)
    result = np.empty_like(source)
    np.multiply(source, ratio[..., None], out=result)
    np.clip(result, 0, 1, out=result)
    result_luma = luminance(result)
    chroma = np.empty_like(result)
    np.subtract(result, result_luma[..., None], out=chroma)
    np.multiply(chroma, float(saturation), out=chroma)
    saturated = np.empty_like(result)
    np.add(result_luma[..., None], chroma, out=saturated)
    result = np.clip(
        saturated,
        0,
        1,
    )
    return result


def process_stars(stars, strength=0.82, saturation=0.92, softness=0.35):
    params = CandidateParams(
        level=CandidateLevel.MEDIUM,
        structure_multiplier=0.0,
        scale_weights={"large": 0.0, "medium": 0.0, "small": 0.0},
        dark_structure_weight=0.0,
        tone_strength=0.0,
        saturation_gain=0.0,
        star_strength=float(strength),
        star_saturation=float(saturation),
        star_softness=float(softness),
    )
    result, _report = process_stars_layer(stars, params)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stretched_with_stars")
    parser.add_argument("starless")
    parser.add_argument("output")
    parser.add_argument(
        "--master-output",
        default=None,
        help="Optional lossless TIFF/FITS master of the same recomposition.",
    )
    parser.add_argument(
        "--stars-input",
        default=None,
        help="Use StarNet2 unscreen star layer instead of subtracting starless.",
    )
    parser.add_argument("--starless-output", default=None)
    parser.add_argument("--stars-output", default=None)
    parser.add_argument("--gamma", type=float, default=0.88)
    parser.add_argument("--saturation", type=float, default=1.12)
    parser.add_argument("--local-contrast", type=float, default=0.12)
    parser.add_argument("--target-median", type=float, default=0.135)
    parser.add_argument("--star-strength", type=float, default=0.82)
    parser.add_argument("--star-saturation", type=float, default=0.92)
    parser.add_argument("--star-softness", type=float, default=0.35)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()

    base = read_rgb(args.stretched_with_stars)
    starless = read_rgb(args.starless)
    if base.shape != starless.shape:
        raise ValueError(f"shape mismatch: {base.shape} != {starless.shape}")

    raw_stars = (
        read_rgb(args.stars_input)
        if args.stars_input
        else np.clip(base - starless, 0, 1)
    )
    if raw_stars.shape != base.shape:
        raise ValueError(f"star layer shape mismatch: {raw_stars.shape} != {base.shape}")
    enhanced_starless = beautify_starless(
        starless,
        gamma=args.gamma,
        saturation=args.saturation,
        local_contrast=args.local_contrast,
        target_median=args.target_median,
    )
    processed_stars = process_stars(
        raw_stars,
        strength=args.star_strength,
        saturation=args.star_saturation,
        softness=args.star_softness,
    )
    combined = recompose_additive(enhanced_starless, processed_stars)

    report = {
        "method": "stretched_starnet_recomposition",
        "base_median": float(np.median(luminance(base))),
        "starless_median_before": float(np.median(luminance(starless))),
        "starless_median_after": float(np.median(luminance(enhanced_starless))),
        "star_layer_nonzero_ratio": float(np.mean(luminance(raw_stars) > 0.002)),
        "combined_median": float(np.median(luminance(combined))),
        "params": vars(args),
    }
    write_image(combined, args.output)
    if args.master_output:
        write_image(combined, args.master_output)
    if args.starless_output:
        write_image(enhanced_starless, args.starless_output)
    if args.stars_output:
        write_image(processed_stars, args.stars_output)
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
