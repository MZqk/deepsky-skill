"""Validation, processing, and additive recomposition of StarNet stars."""

import numpy as np
from scipy.ndimage import gaussian_filter

from starless_diagnostics import ensure_rgb_float32


def luminance(image):
    return (
        .2126 * image[..., 0]
        + .7152 * image[..., 1]
        + .0722 * image[..., 2]
    )


def validate_stars_layer(stars):
    source = ensure_rgb_float32(stars)
    gray = luminance(source)
    background = float(np.percentile(gray, 50))
    nonzero = float(np.mean(gray > .002))
    if background > .01 or nonzero > .35:
        raise ValueError(
            "stars_input must be an independent positive stars layer "
            "on a black background"
        )
    return {
        "background_median": background,
        "nonzero_ratio": nonzero,
        "highlight_clip_ratio": float(np.mean(source >= .995)),
    }


def process_stars_layer(stars, params):
    validation = validate_stars_layer(stars)
    layer = ensure_rgb_float32(stars)
    support = luminance(layer) > .002
    if params.star_softness > 0:
        softened = gaussian_filter(
            layer, (params.star_softness, params.star_softness, 0))
        layer = np.maximum(layer * .78, softened * .92)
        layer *= support[..., None]
    gray = luminance(layer)
    layer = np.clip(
        gray[..., None]
        + (layer - gray[..., None]) * params.star_saturation,
        0, 1,
    )
    layer = np.clip(layer * params.star_strength, 0, 1).astype(np.float32)
    return layer, {
        **validation,
        "star_strength": params.star_strength,
        "star_saturation": params.star_saturation,
        "star_softness": params.star_softness,
    }


def recompose_additive(starless, processed_stars):
    if starless.shape != processed_stars.shape:
        raise ValueError("starless and stars layers must have identical shapes")
    return np.clip(starless + processed_stars, 0, 1).astype(np.float32)
