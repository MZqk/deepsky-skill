"""Target masks for stretched starless enhancement."""

import numpy as np
from scipy.ndimage import gaussian_filter

from mask_tools import mask_statistics, multiscale_feather


def _luminance(image):
    return (
        .2126 * image[..., 0]
        + .7152 * image[..., 1]
        + .0722 * image[..., 2]
    )


def build_starless_masks(image, diagnostics, artifact_mask, profile):
    luma = _luminance(image)
    local = gaussian_filter(luma, 5.)
    signal = np.clip(local - np.percentile(local, 30), 0, None)
    signal_scale = max(float(np.percentile(signal, 99)), 1e-8)
    subject = multiscale_feather(
        np.clip(signal / signal_scale, 0, 1), (0., 2., 8.))

    dark_residual = np.clip(local - luma, 0, None)
    selected = dark_residual[subject > .1]
    dark_scale = max(
        float(np.percentile(selected, 98)) if selected.size else 1e-8,
        1e-8,
    )
    dark_structure = multiscale_feather(
        np.clip(dark_residual / dark_scale, 0, 1) * subject,
        (0., 1.5, 4.),
    )

    noise_floor = max(float(diagnostics["dark_noise_sigma"]) * 4., 1e-4)
    background = (1. - np.clip(signal / noise_floor, 0, 1))
    background *= profile.background_protection
    highlight = np.clip((luma - .82) / .15, 0, 1)
    highlight *= profile.highlight_protection
    protection = multiscale_feather(
        np.maximum.reduce([
            background,
            highlight,
            np.clip(artifact_mask, 0, 1),
        ]),
        (0., 2., 6.),
    )
    masks = {
        "subject": subject.astype(np.float32),
        "dark_structure": dark_structure.astype(np.float32),
        "protection": protection.astype(np.float32),
    }
    report = {key: mask_statistics(value) for key, value in masks.items()}
    warnings = []
    coverage = report["subject"]["coverage_soft"]
    if coverage < .005:
        warnings.append("SUBJECT_MASK_NEAR_EMPTY")
    if coverage > .85:
        warnings.append("SUBJECT_MASK_TOO_BROAD")
    report["warnings"] = warnings
    return masks, report
