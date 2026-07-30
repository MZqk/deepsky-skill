#!/usr/bin/env python3
"""Quantitative diagnostics for deep-sky image processing advice."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np


SCHEMA_VERSION = "2.0"
IMAGE_EXTENSIONS = {".fit", ".fits", ".fts", ".xisf", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def _json_value(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, np.generic):
        return value.item()
    return str(value)


def _header_subset(header):
    keys = (
        "SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3",
        "OBJECT", "IMAGETYP", "FRAME", "TELESCOP", "INSTRUME", "OBSERVER",
        "EXPTIME", "EXPOSURE", "FILTER", "BAYERPAT", "XBAYROFF", "YBAYROFF",
        "DATE-OBS", "RA", "DEC", "OBJCTRA", "OBJCTDEC", "AIRMASS", "GAIN",
        "EGAIN", "OFFSET", "CCD-TEMP", "XPIXSZ", "YPIXSZ", "FOCALLEN",
        "XBINNING", "YBINNING", "STACKCNT", "NCOMBINE", "WCSAXES",
    )
    return {key: _json_value(header[key]) for key in keys if key in header}


def _normalize_layout(data):
    array = np.asarray(data)
    array = np.squeeze(array)
    if array.ndim == 2:
        return array
    if array.ndim != 3:
        raise ValueError(f"Unsupported image dimensionality after squeeze: {array.shape}")
    if array.shape[-1] in (1, 3, 4):
        return array[..., 0] if array.shape[-1] == 1 else array
    if array.shape[0] in (1, 3, 4):
        moved = np.moveaxis(array, 0, -1)
        return moved[..., 0] if moved.shape[-1] == 1 else moved
    raise ValueError(
        f"Ambiguous 3D image layout {array.shape}; expected [H,W,C] or [C,H,W] with 1/3/4 channels"
    )


def _read_fits(path):
    from astropy.io import fits

    with fits.open(path, memmap=False) as hdul:
        selected = None
        for index, hdu in enumerate(hdul):
            if hdu.data is not None and np.asarray(hdu.data).ndim >= 2:
                selected = (index, hdu)
                break
        if selected is None:
            raise ValueError("No image HDU with at least two dimensions was found")
        index, hdu = selected
        data = _normalize_layout(np.asarray(hdu.data))
        header = hdu.header.copy()
        primary_header = hdul[0].header.copy()
        for key in primary_header:
            if key not in header and key not in ("", "COMMENT", "HISTORY"):
                try:
                    header[key] = primary_header[key]
                except Exception:
                    pass
    return data, {
        "format": "fits",
        "selected_hdu": index,
        "header": _header_subset(header),
        "is_astronomical_container": True,
    }


def _read_xisf(path):
    try:
        from xisf import XISF
    except ImportError as exc:
        raise ImportError("XISF input requires the 'xisf' package") from exc

    xisf_file = XISF(str(path))
    data = _normalize_layout(xisf_file.read_image(0))
    images = xisf_file.get_images_metadata()
    image_meta = images[0] if images else {}
    raw_keywords = image_meta.get("FITSKeywords", {})
    header = {}
    for key, entries in raw_keywords.items():
        if isinstance(entries, list) and entries:
            header[key] = entries[0].get("value")
        elif isinstance(entries, dict):
            header[key] = entries.get("value")
        else:
            header[key] = entries
    return data, {
        "format": "xisf",
        "selected_hdu": None,
        "header": _header_subset(header),
        "is_astronomical_container": True,
        "xisf_color_space": image_meta.get("colorSpace"),
        "xisf_sample_format": image_meta.get("sampleFormat"),
    }


def _read_raster(path):
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff"):
        import tifffile
        data = tifffile.imread(path)
    else:
        from PIL import Image
        data = np.asarray(Image.open(path))
    return _normalize_layout(data), {
        "format": suffix.lstrip("."),
        "selected_hdu": None,
        "header": {},
        "is_astronomical_container": False,
    }


def read_image(path):
    suffix = path.suffix.lower()
    if suffix not in IMAGE_EXTENSIONS:
        raise ValueError(f"Unsupported input format: {suffix or '<none>'}")
    if suffix in (".fit", ".fits", ".fts"):
        return _read_fits(path)
    if suffix == ".xisf":
        return _read_xisf(path)
    return _read_raster(path)


def _finite_array(data):
    array = np.asarray(data)
    finite = np.isfinite(array)
    if not np.any(finite):
        raise ValueError("Image contains no finite pixels")
    replacement = float(np.median(array[finite]))
    clean = np.nan_to_num(
        array.astype(np.float64),
        nan=replacement,
        posinf=replacement,
        neginf=replacement,
    )
    return clean, finite


def _luminance(image):
    if image.ndim == 2:
        return image
    rgb = image[..., :3]
    return 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]


def _robust_limits(values, low=0.1, high=99.9):
    finite = values[np.isfinite(values)]
    lo, hi = np.percentile(finite, [low, high])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.min(finite))
        hi = float(np.max(finite))
    if hi <= lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def _normalize_for_analysis(image):
    gray = _luminance(image)
    lo, hi = _robust_limits(gray)
    normalized = np.clip((image - lo) / (hi - lo), 0.0, 1.0)
    return normalized.astype(np.float32), {"p0_1": lo, "p99_9": hi, "span": hi - lo}


def _mad_sigma(values):
    values = np.asarray(values)
    median = np.median(values)
    return float(1.4826 * np.median(np.abs(values - median)))


def analyze_statistics(raw, finite_mask, scale):
    gray = _luminance(raw)
    finite_gray = gray[np.isfinite(gray)]
    percentiles = (0.01, 0.1, 1, 5, 25, 50, 75, 95, 99, 99.9, 99.99)
    p = {f"p{str(q).replace('.', '_')}": float(np.percentile(finite_gray, q)) for q in percentiles}
    raw_min = float(np.min(finite_gray))
    raw_max = float(np.max(finite_gray))
    span = max(raw_max - raw_min, 1e-12)
    return {
        "evidence": "measured",
        "shape": list(raw.shape),
        "dtype_original": str(raw.dtype),
        "finite_pixel_ratio": float(np.mean(finite_mask)),
        "nan_pixel_ratio": float(np.mean(np.isnan(raw))),
        "positive_inf_pixel_ratio": float(np.mean(np.isposinf(raw))),
        "negative_inf_pixel_ratio": float(np.mean(np.isneginf(raw))),
        "min": raw_min,
        "max": raw_max,
        "mean": float(np.mean(finite_gray)),
        "median": float(np.median(finite_gray)),
        "std": float(np.std(finite_gray)),
        "mad_sigma": _mad_sigma(finite_gray),
        "percentiles": p,
        "exact_min_ratio": float(np.mean(finite_gray <= raw_min)),
        "exact_max_ratio": float(np.mean(finite_gray >= raw_max)),
        "near_min_ratio": float(np.mean(finite_gray <= raw_min + span * 1e-5)),
        "near_max_ratio": float(np.mean(finite_gray >= raw_max - span * 1e-5)),
        "analysis_normalization": scale,
    }


def analyze_clipping(normalized):
    gray = _luminance(normalized)
    channels = normalized[..., :3] if normalized.ndim == 3 else normalized[..., None]
    labels = ("r", "g", "b") if channels.shape[-1] >= 3 else ("mono",)
    per_channel = {}
    for index, label in enumerate(labels):
        channel = channels[..., index]
        per_channel[label] = {
            "shadow_ratio_le_0_001": float(np.mean(channel <= 0.001)),
            "highlight_ratio_ge_0_999": float(np.mean(channel >= 0.999)),
        }
    return {
        "evidence": "measured_on_robust_normalization",
        "interpretation": "Potential display-range clipping; not a sensor saturation measurement",
        "shadow_ratio_le_0_001": float(np.mean(gray <= 0.001)),
        "highlight_ratio_ge_0_999": float(np.mean(gray >= 0.999)),
        "per_channel": per_channel,
    }


def analyze_noise(normalized):
    from scipy.ndimage import median_filter

    gray = _luminance(normalized).astype(np.float64)
    background_limit = float(np.percentile(gray, 35))
    background = gray <= background_limit
    smooth = median_filter(gray, size=3, mode="reflect")
    residual = gray - smooth
    residual_values = residual[background]
    sigma = _mad_sigma(residual_values) if residual_values.size else 0.0

    h, w = gray.shape
    block = max(8, min(64, min(h, w) // 8))
    block_sigmas = []
    for y in range(0, h - block + 1, block):
        for x in range(0, w - block + 1, block):
            patch = gray[y:y + block, x:x + block]
            if np.median(patch) <= background_limit:
                patch_residual = patch - median_filter(patch, size=3, mode="reflect")
                block_sigmas.append(_mad_sigma(patch_residual))
    return {
        "evidence": "measured",
        "method": "MAD sigma of 3x3 median-filter high-pass residual in the darkest 35%",
        "background_limit_normalized": background_limit,
        "background_noise_sigma_normalized": sigma,
        "background_sample_pixels": int(np.sum(background)),
        "block_size_px": block,
        "block_count": len(block_sigmas),
        "block_sigma_median": float(np.median(block_sigmas)) if block_sigmas else None,
        "block_sigma_p90": float(np.percentile(block_sigmas, 90)) if block_sigmas else None,
        "warning": "Correlated noise, resampling, denoising, and faint real signal can bias this estimate",
    }


def _fit_background_plane(channel):
    h, w = channel.shape
    step = max(1, min(h, w) // 96)
    sample = channel[::step, ::step]
    sh, sw = sample.shape
    yy, xx = np.mgrid[0:sh, 0:sw]
    values = sample.ravel()
    cutoff = np.percentile(values, 45)
    mask = values <= cutoff
    x = xx.ravel()[mask] / max(sw - 1, 1)
    y = yy.ravel()[mask] / max(sh - 1, 1)
    z = values[mask]
    design = np.column_stack([np.ones_like(x), x, y])
    coeffs, _, _, _ = np.linalg.lstsq(design, z, rcond=None)
    predicted = design @ coeffs
    residual = z - predicted
    total = float(np.sum((z - np.mean(z)) ** 2))
    r_squared = 1.0 - float(np.sum(residual ** 2)) / max(total, 1e-12)
    return {
        "offset": float(coeffs[0]),
        "x_change_across_frame": float(coeffs[1]),
        "y_change_across_frame": float(coeffs[2]),
        "magnitude_across_frame": float(math.hypot(coeffs[1], coeffs[2])),
        "angle_degrees": float(math.degrees(math.atan2(coeffs[2], coeffs[1]))),
        "r_squared": r_squared,
        "residual_rms": float(np.sqrt(np.mean(residual ** 2))),
        "sample_count": int(z.size),
    }


def analyze_background(normalized):
    gray = _luminance(normalized).astype(np.float64)
    h, w = gray.shape
    size = max(4, min(h, w) // 8)
    regions = {
        "top_left": gray[:size, :size],
        "top_right": gray[:size, -size:],
        "bottom_left": gray[-size:, :size],
        "bottom_right": gray[-size:, -size:],
        "center": gray[h // 2 - size // 2:h // 2 + (size + 1) // 2,
                       w // 2 - size // 2:w // 2 + (size + 1) // 2],
    }
    region_medians = {name: float(np.median(values)) for name, values in regions.items()}
    corner_values = [region_medians[name] for name in (
        "top_left", "top_right", "bottom_left", "bottom_right"
    )]
    plane = _fit_background_plane(gray)
    center = max(region_medians["center"], 1e-9)
    corner_mean = float(np.mean(corner_values))

    channel_planes = None
    if normalized.ndim == 3 and normalized.shape[-1] >= 3:
        channel_planes = {
            label: _fit_background_plane(normalized[..., index])
            for index, label in enumerate(("r", "g", "b"))
        }

    return {
        "evidence": "measured",
        "method": "Low-signal plane fit plus center/corner robust medians",
        "region_size_px": size,
        "region_medians_normalized": region_medians,
        "corner_median_range": float(max(corner_values) - min(corner_values)),
        "corner_mean_over_center": corner_mean / center,
        "plane": plane,
        "channel_planes": channel_planes,
        "interpretation_warning": (
            "A fitted low-frequency trend is not proof of removable gradient; "
            "real nebula, IFN, dust, galaxy halos, and mosaics can produce the same signature"
        ),
    }


def _star_patch_metrics(gray, y, x, radius, saturation_limit):
    h, w = gray.shape
    if y - radius < 0 or x - radius < 0 or y + radius >= h or x + radius >= w:
        return None
    patch = gray[y - radius:y + radius + 1, x - radius:x + radius + 1].astype(np.float64)
    border = np.concatenate([patch[0], patch[-1], patch[1:-1, 0], patch[1:-1, -1]])
    background = float(np.median(border))
    signal = np.clip(patch - background, 0, None)
    flux = float(np.sum(signal))
    if flux <= 1e-8 or float(np.max(patch)) >= saturation_limit:
        return None
    yy, xx = np.mgrid[-radius:radius + 1, -radius:radius + 1]
    cx = float(np.sum(xx * signal) / flux)
    cy = float(np.sum(yy * signal) / flux)
    dx = xx - cx
    dy = yy - cy
    cov_xx = float(np.sum(signal * dx * dx) / flux)
    cov_yy = float(np.sum(signal * dy * dy) / flux)
    cov_xy = float(np.sum(signal * dx * dy) / flux)
    eigenvalues = np.linalg.eigvalsh([[cov_xx, cov_xy], [cov_xy, cov_yy]])
    minor_var = max(float(eigenvalues[0]), 0.04)
    major_var = max(float(eigenvalues[1]), minor_var)
    fwhm_minor = 2.35482 * math.sqrt(minor_var)
    fwhm_major = 2.35482 * math.sqrt(major_var)
    if not (0.8 <= fwhm_minor <= radius * 2.2 and 0.8 <= fwhm_major <= radius * 2.5):
        return None
    angle = 0.5 * math.degrees(math.atan2(2 * cov_xy, cov_xx - cov_yy))
    axis_ratio = fwhm_minor / max(fwhm_major, 1e-9)
    return {
        "fwhm_major_px": fwhm_major,
        "fwhm_minor_px": fwhm_minor,
        "axis_ratio": axis_ratio,
        "eccentricity": math.sqrt(max(0.0, 1.0 - axis_ratio * axis_ratio)),
        "position_angle_deg": angle,
        "peak_normalized": float(np.max(patch)),
    }


def analyze_stars(normalized, noise):
    from scipy.ndimage import maximum_filter

    gray = _luminance(normalized).astype(np.float64)
    sigma = max(float(noise.get("background_noise_sigma_normalized") or 0), 1e-4)
    background = float(np.median(gray))
    threshold = max(background + 8.0 * sigma, float(np.percentile(gray, 99.0)))
    local_max = maximum_filter(gray, size=5, mode="reflect")
    candidates = np.argwhere((gray == local_max) & (gray >= threshold))
    if candidates.size == 0:
        return {
            "evidence": "unavailable",
            "reason": "No isolated high-SNR star candidates detected",
            "candidate_count": 0,
            "usable_star_count": 0,
        }
    values = gray[candidates[:, 0], candidates[:, 1]]
    order = np.argsort(values)[::-1][:500]
    candidates = candidates[order]
    metrics = []
    radius = max(5, min(10, int(round(min(gray.shape) / 200))))
    for y, x in candidates:
        result = _star_patch_metrics(gray, int(y), int(x), radius, saturation_limit=0.998)
        if result is not None:
            metrics.append(result)
    if len(metrics) < 5:
        return {
            "evidence": "unavailable",
            "reason": "Fewer than five unsaturated isolated star-like candidates passed shape checks",
            "candidate_count": int(len(candidates)),
            "usable_star_count": len(metrics),
        }

    def values(key):
        return np.asarray([item[key] for item in metrics], dtype=np.float64)

    major = values("fwhm_major_px")
    minor = values("fwhm_minor_px")
    eccentricity = values("eccentricity")
    axis_ratio = values("axis_ratio")
    angles = values("position_angle_deg")
    density = len(metrics) / (gray.size / 1_000_000.0)
    return {
        "evidence": "measured",
        "method": "Local maxima plus background-subtracted second moments of unsaturated patches",
        "candidate_count": int(len(candidates)),
        "usable_star_count": len(metrics),
        "density_per_megapixel": float(density),
        "fwhm_major_median_px": float(np.median(major)),
        "fwhm_minor_median_px": float(np.median(minor)),
        "fwhm_major_p90_px": float(np.percentile(major, 90)),
        "axis_ratio_median": float(np.median(axis_ratio)),
        "eccentricity_median": float(np.median(eccentricity)),
        "eccentricity_p90": float(np.percentile(eccentricity, 90)),
        "position_angle_median_deg": float(np.median(angles)),
        "warning": (
            "Moment-based estimates are diagnostic, not a full PSF fit. Nebular knots, blends, "
            "undersampling, and processed stars can bias the result"
        ),
    }


def analyze_color(normalized):
    if normalized.ndim != 3 or normalized.shape[-1] < 3:
        return {"evidence": "not_applicable", "channel_model": "mono"}
    rgb = normalized[..., :3].astype(np.float64)
    gray = _luminance(rgb)
    background = gray <= np.percentile(gray, 30)
    bg = np.median(rgb[background], axis=0)
    bg_mean = max(float(np.mean(bg)), 1e-9)
    p99 = np.percentile(rgb.reshape(-1, 3), 99, axis=0)
    correlations = np.corrcoef(rgb.reshape(-1, 3), rowvar=False)
    return {
        "evidence": "measured",
        "channel_model": "rgb_or_three_channel",
        "background_medians_normalized": {
            label: float(value) for label, value in zip(("r", "g", "b"), bg)
        },
        "background_ratios_to_mean": {
            label: float(value / bg_mean) for label, value in zip(("r", "g", "b"), bg)
        },
        "channel_p99_normalized": {
            label: float(value) for label, value in zip(("r", "g", "b"), p99)
        },
        "channel_correlation": correlations.tolist(),
        "collapsed_channels": [
            label for label, value in zip(("r", "g", "b"), p99)
            if value < max(float(np.max(p99)) * 0.01, 1e-5)
        ],
        "interpretation_warning": (
            "Channel imbalance may be real emission or filter response; it is not automatically a color cast"
        ),
    }


def classify_input(path, metadata, raw):
    header = metadata.get("header", {})
    filename = path.stem.lower()
    image_type = str(header.get("IMAGETYP") or header.get("FRAME") or "").lower()
    frame_role = "unknown"
    role_tokens = {
        "bias": ("bias", "offset"),
        "dark": ("dark",),
        "flat": ("flat",),
        "light": ("light", "object"),
    }
    source = f"{image_type} {filename}"
    for role, tokens in role_tokens.items():
        if any(token in source for token in tokens):
            frame_role = role
            break
    combined = header.get("NCOMBINE") or header.get("STACKCNT")
    stage = "unknown"
    if combined not in (None, "", 0, "0"):
        stage = "stacked_or_integrated"
    elif "master" in filename and frame_role in ("bias", "dark", "flat"):
        stage = "master_calibration"
    elif any(token in filename for token in ("stack", "integrat", "masterlight")):
        stage = "stacked_or_integrated"

    if raw.ndim == 2:
        filter_name = str(header.get("FILTER") or "").strip()
        channel_model = filter_name if filter_name else "mono_or_cfa"
    else:
        channel_model = "rgb" if raw.shape[-1] >= 3 else f"{raw.shape[-1]}_channel"
    bayer = header.get("BAYERPAT")
    if bayer and raw.ndim == 2:
        channel_model = f"osc_cfa_{bayer}"

    suffix = path.suffix.lower()
    if suffix in (".fit", ".fits", ".fts", ".xisf"):
        transfer_state = "likely_linear"
        transfer_confidence = "medium"
    else:
        transfer_state = "unknown"
        transfer_confidence = "low"
    return {
        "evidence": "metadata_and_filename_heuristic",
        "frame_role": frame_role,
        "processing_stage": stage,
        "transfer_state": transfer_state,
        "transfer_state_confidence": transfer_confidence,
        "channel_model": channel_model,
        "filter": header.get("FILTER"),
        "object": header.get("OBJECT"),
        "warnings": [
            "File names and headers are not guaranteed truth",
            "FITS/XISF containers can contain nonlinear processed data",
        ],
    }


def _stretch_luminance(values, black, white, strength=10.0):
    scaled = np.clip((values - black) / max(white - black, 1e-9), 0, 1)
    return np.arcsinh(scaled * strength) / np.arcsinh(strength)


def _to_uint8(image):
    return np.clip(np.rint(image * 255.0), 0, 255).astype(np.uint8)


def _save_png(path, image):
    from PIL import Image
    array = _to_uint8(image)
    mode = "L" if array.ndim == 2 else "RGB"
    if array.ndim == 3:
        array = array[..., :3]
    Image.fromarray(array, mode=mode).save(path)


def generate_previews(normalized, output_dir, stem):
    gray = _luminance(normalized)
    files = {}

    full = _stretch_luminance(normalized, 0.0, 1.0, strength=12.0)
    path = output_dir / f"{stem}_preview.png"
    _save_png(path, full)
    files["full"] = str(path)

    bg_white = float(np.percentile(gray, 80))
    background = _stretch_luminance(normalized, 0.0, max(bg_white, 1e-4), strength=5.0)
    path = output_dir / f"{stem}_preview_background.png"
    _save_png(path, background)
    files["background_enhanced"] = str(path)

    high_black = float(np.percentile(gray, 90))
    highlights = np.clip((normalized - high_black) / max(1.0 - high_black, 1e-5), 0, 1)
    path = output_dir / f"{stem}_preview_highlights.png"
    _save_png(path, highlights)
    files["highlights"] = str(path)

    if normalized.ndim == 3 and normalized.shape[-1] >= 3:
        channel_panels = []
        for index in range(3):
            channel = _stretch_luminance(normalized[..., index], 0.0, 1.0, strength=12.0)
            channel_panels.append(np.repeat(channel[..., None], 3, axis=2))
        channels = np.concatenate(channel_panels, axis=1)
        path = output_dir / f"{stem}_preview_channels.png"
        _save_png(path, channels)
        files["channels_rgb_order"] = str(path)
    return files


def analyze_image_file(input_path, output_dir=None):
    path = Path(input_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    destination = Path(output_dir).expanduser().resolve() if output_dir else path.parent
    destination.mkdir(parents=True, exist_ok=True)

    original, metadata = read_image(path)
    original_array = np.asarray(original)
    clean, finite_mask = _finite_array(original_array)
    normalized, scale = _normalize_for_analysis(clean)

    noise = analyze_noise(normalized)
    report = {
        "schema_version": SCHEMA_VERSION,
        "file": {
            "filename": path.name,
            "filepath": str(path),
            "format": metadata["format"],
            "selected_hdu": metadata.get("selected_hdu"),
            "header": metadata.get("header", {}),
        },
        "classification": classify_input(path, metadata, clean),
        "statistics": analyze_statistics(original_array, finite_mask, scale),
        "clipping": analyze_clipping(normalized),
        "noise": noise,
        "background": analyze_background(normalized),
        "stars": analyze_stars(normalized, noise),
        "color": analyze_color(normalized),
        "limitations": [
            "No plate solving or catalog-based object identification",
            "No photometric color validation",
            "No physical SNR without calibrated regional measurements",
            "Background trends require visual confirmation before removal",
        ],
    }
    json_path = destination / f"{path.stem}_analysis.json"
    report["previews"] = generate_previews(normalized, destination, path.stem)
    report["analysis_json"] = str(json_path)
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Measure deep-sky image diagnostics and generate review previews")
    parser.add_argument("input", help="FITS/XISF/TIFF/PNG/JPEG input")
    parser.add_argument("output_dir", nargs="?", help="Output directory; defaults to the input directory")
    parser.add_argument("--stdout", action="store_true", help="Print the complete JSON report")
    args = parser.parse_args(argv)
    try:
        result = analyze_image_file(args.input, args.output_dir)
    except Exception as exc:
        print(f"Analysis failed: {exc}", file=sys.stderr)
        return 1
    if args.stdout:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Analysis JSON: {result['analysis_json']}")
        for label, filename in result["previews"].items():
            print(f"Preview ({label}): {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
