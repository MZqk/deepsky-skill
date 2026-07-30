"""Diagnostics and validation for stretched starless images."""

import numpy as np
from scipy.ndimage import gaussian_filter


def ensure_rgb_float32(image):
    source = np.asarray(image)
    if source.ndim == 2:
        source = np.stack([source] * 3, axis=-1)
    if source.ndim != 3 or source.shape[2] < 3:
        raise ValueError("input must be grayscale or RGB")
    source = source[..., :3].astype(np.float32)
    if not np.isfinite(source).all():
        raise ValueError("input contains non-finite pixels")
    return np.clip(source, 0, 1)


def validate_stretched_input_meta(meta):
    fmt = str(meta.get("format", "")).lower()
    if fmt not in {"tif", "tiff", "png"} or meta.get("is_linear", False):
        raise ValueError("stretched starless workflow accepts TIFF or PNG only")


def _luminance(image):
    return (
        .2126 * image[..., 0]
        + .7152 * image[..., 1]
        + .0722 * image[..., 2]
    )


def _corner_ratio(gray):
    size = max(3, min(gray.shape) // 8)
    values = [
        gray[:size, :size].mean(), gray[:size, -size:].mean(),
        gray[-size:, :size].mean(), gray[-size:, -size:].mean(),
    ]
    return float(max(values) / max(min(values), 1e-8))


def diagnose_starless(image):
    source = ensure_rgb_float32(image)
    gray = _luminance(source)
    dark_limit = np.percentile(gray, 35)
    dark_pixels = gray[gray <= dark_limit]
    median_dark = float(np.median(dark_pixels))
    noise = float(1.4826 * np.median(np.abs(dark_pixels - median_dark)))

    small = gray - gaussian_filter(gray, 1.5)
    medium = gray - gaussian_filter(gray, 6.)
    large = gray - gaussian_filter(gray, 24.)
    local = gaussian_filter(gray, 2.)
    dark_pit = np.clip(local - gray, 0, None)
    ring = np.clip(
        gaussian_filter(gray, 1.) - gaussian_filter(gray, 3.), 0, None)
    artifact = dark_pit + .5 * ring
    scale = max(float(np.percentile(artifact, 99.5)), 1e-8)
    artifact_mask = gaussian_filter(
        np.clip(artifact / scale, 0, 1), 1.).astype(np.float32)

    background = source[gray <= dark_limit]
    chroma = background - background.mean(axis=1, keepdims=True)
    clips = np.mean(source >= .995, axis=(0, 1))
    report = {
        "median": float(np.median(gray)),
        "p1": float(np.percentile(gray, 1)),
        "p99": float(np.percentile(gray, 99)),
        "near_black_ratio": float(np.mean(gray <= .005)),
        "highlight_clip_ratio_by_channel": dict(zip(
            ("r", "g", "b"), map(float, clips))),
        "corner_uniformity_ratio": _corner_ratio(gray),
        "dark_noise_sigma": noise,
        "background_chroma_sigma": float(np.std(chroma)),
        "artifact_coverage": float(np.mean(artifact_mask > .5)),
        "scales": {
            "small_energy": float(np.mean(np.abs(small))),
            "medium_energy": float(np.mean(np.abs(medium))),
            "large_energy": float(np.mean(np.abs(large))),
        },
    }
    return report, artifact_mask
