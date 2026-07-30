#!/usr/bin/env python3
"""Local CV recognition for deep-sky images.

This module recognizes one image per invocation and writes a schema-valid JSON
artifact. It does not call external AI vision APIs.
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy.ndimage import binary_dilation, gaussian_filter, label as cc_label
from color_conv import safe_rgb2hsv as rgb2hsv
from skimage.filters import threshold_otsu
from skimage.morphology import disk, white_tophat
from skimage.transform import resize

sys.path.insert(0, str(Path(__file__).parent))
from fits_io import (
    read_capture_metadata,
    read_image,
    resolve_celestial_target,
    write_image,
)


SCHEMA_VERSION = "1.0"
TARGET_TYPES = [
    "emission_nebula",
    "reflection_nebula",
    "galaxy",
    "globular_cluster",
    "open_cluster",
    "planetary_nebula",
    "dark_nebula",
    "wide_field",
    "unknown_deep_sky",
]

ASTRO_INPUT_EXTENSIONS = {".fit", ".fits", ".fts", ".xisf"}


def clamp01(value):
    return float(max(0.0, min(1.0, value)))


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_image(img):
    original_shape = list(img.shape)
    has_alpha = bool(img.ndim == 3 and img.shape[2] == 4)
    if has_alpha:
        img = img[:, :, :3]
    if img.ndim == 2:
        original_shape = [int(img.shape[0]), int(img.shape[1]), 1]
        rgb = np.stack([img, img, img], axis=-1)
        gray = img.astype(np.float32)
    elif img.ndim == 3 and img.shape[2] >= 3:
        rgb = img[:, :, :3].astype(np.float32)
        gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    else:
        raise ValueError(f"unsupported image shape: {img.shape}")
    rgb = np.nan_to_num(rgb.astype(np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    gray = np.nan_to_num(gray.astype(np.float32), nan=0.0, posinf=1.0, neginf=0.0)
    if rgb.max() > 1.0:
        rgb = rgb / max(float(rgb.max()), 1.0)
    if gray.max() > 1.0:
        gray = gray / max(float(gray.max()), 1.0)
    return np.clip(rgb, 0, 1), np.clip(gray, 0, 1), original_shape, has_alpha


def safe_visual_preview(image, target_bg=0.12, gamma=0.45,
                        highlight_pctl=99.9, max_side=1800):
    """Create an audit-friendly preview without percentile shadow clipping."""
    source = np.asarray(image, dtype=np.float32)
    if source.ndim == 2:
        source = np.stack([source] * 3, axis=-1)
    elif source.ndim == 3 and source.shape[2] >= 3:
        source = source[..., :3]
    else:
        raise ValueError(f"unsupported preview shape: {source.shape}")

    source = np.nan_to_num(source, nan=0.0, posinf=0.0, neginf=0.0)
    gray = np.mean(source, axis=2)
    # Shift only by the actual negative floor. No positive low percentile is
    # subtracted, so faint positive signal is not discarded as background.
    floor = min(0.0, float(np.min(gray)))
    shifted = source - floor
    shifted_gray = np.mean(shifted, axis=2)
    high = float(np.percentile(shifted_gray, highlight_pctl))
    if high <= 1e-12:
        high = float(np.max(shifted_gray))
    if high <= 1e-12:
        preview = np.zeros_like(shifted)
    else:
        preview = np.clip(shifted / high, 0, 1)
        preview = np.power(preview, float(np.clip(gamma, 0.2, 1.0)))
        preview_gray = np.mean(preview, axis=2)
        bg_mask = shifted_gray <= np.percentile(shifted_gray, 50)
        positive_bg = preview_gray[bg_mask & (shifted_gray > 0)]
        bg_level = (
            float(np.median(positive_bg))
            if positive_bg.size else float(np.median(preview_gray[bg_mask]))
        )
        if bg_level > 1e-6:
            preview = np.clip(preview * (float(target_bg) / bg_level), 0, 1)

    height, width = preview.shape[:2]
    scale = min(1.0, float(max_side) / max(height, width))
    if scale < 1.0:
        preview = resize(
            preview,
            (max(1, round(height * scale)), max(1, round(width * scale)), 3),
            preserve_range=True,
            anti_aliasing=True,
        ).astype(np.float32)
    return np.clip(preview, 0, 1), {
        "method": "zero_shadow_clip_gamma",
        "negative_floor_shift": round(float(floor), 8),
        "shadow_pctl": 0.0,
        "highlight_pctl": float(highlight_pctl),
        "gamma": float(gamma),
        "target_bg": float(target_bg),
        "max_side": int(max_side),
        "source_shape": list(source.shape),
        "preview_shape": list(preview.shape),
    }


def _crop_primary_preview(preview, primary, padding=0.25):
    height, width = preview.shape[:2]
    x, y, box_width, box_height = primary["bbox"]
    pad_x = box_width * padding
    pad_y = box_height * padding
    x0 = max(0, int((x - pad_x) * width))
    y0 = max(0, int((y - pad_y) * height))
    x1 = min(width, int((x + box_width + pad_x) * width))
    y1 = min(height, int((y + box_height + pad_y) * height))
    if x1 <= x0 or y1 <= y0:
        return preview
    return preview[y0:y1, x0:x1]


def create_safe_preview_bundle(image_path, output_dir, target_bg=0.12,
                               gamma=0.45, highlight_pctl=99.9,
                               max_side=1800):
    """Write full-frame, target, and channel previews for visual review."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    image, meta = read_image(str(image_path))
    preview, parameters = safe_visual_preview(
        image,
        target_bg=target_bg,
        gamma=gamma,
        highlight_pctl=highlight_pctl,
        max_side=max_side,
    )
    preview_gray = np.mean(preview, axis=2)
    primary = estimate_primary_region(preview_gray)
    target = _crop_primary_preview(preview, primary)

    paths = {
        "full": str(output_dir / "safe_full.png"),
        "target": str(output_dir / "safe_target.png"),
        "preview_master": str(output_dir / "safe_full.tif"),
    }
    write_image(preview, paths["full"])
    write_image(target, paths["target"])
    write_image(preview, paths["preview_master"])
    channel_paths = {}
    for index, label in enumerate(("r", "g", "b")):
        channel = np.stack([preview[..., index]] * 3, axis=-1)
        path = str(output_dir / f"safe_channel_{label}.png")
        write_image(channel, path)
        channel_paths[label] = path
    paths["channels"] = channel_paths
    return {
        "source_format": meta.get("format"),
        "parameters": parameters,
        "primary_region": primary,
        "paths": paths,
        "warning": (
            "Previews are display transforms only. They are not processing "
            "inputs and must not be used as evidence for absent structure."
        ),
    }


def _header_wcs_evidence(image_path, plate_solution=None):
    capture_metadata, header = read_capture_metadata(str(image_path))
    resolved = resolve_celestial_target(
        header=header,
        target_name=capture_metadata.get("object"),
    )
    wcs_keys = {}
    if header:
        for key in (
            "CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2",
            "CD1_1", "CD1_2", "CD2_1", "CD2_2", "CDELT1", "CDELT2",
            "CROTA2",
        ):
            value = header.get(key)
            if value is not None:
                if isinstance(value, np.generic):
                    value = value.item()
                wcs_keys[key] = value
    return {
        "capture_metadata": capture_metadata,
        "resolved_target": resolved,
        "embedded_wcs": {
            "present": bool(
                {"CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2"} <= set(wcs_keys)
            ),
            "keywords": wcs_keys,
        },
        "plate_solution": plate_solution,
        "precedence": "header_or_wcs_before_visual_classification",
    }


def _load_or_run_analysis(image_path, analysis_report=None):
    if isinstance(analysis_report, dict):
        return analysis_report
    if analysis_report:
        return json.loads(Path(analysis_report).read_text(encoding="utf-8"))
    # Runtime import avoids the analyze.py -> recognize.py import cycle.
    from analyze import analyze_image
    return analyze_image(str(image_path))


def build_recognition_workflow(image_path, output_dir, analysis_report=None,
                               plate_solution=None, stage="input",
                               target_bg=0.12, gamma=0.45,
                               highlight_pctl=99.9, max_side=1800):
    """Build Header/WCS -> diagnostics -> preview -> AI review -> CV bundle."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    previews = create_safe_preview_bundle(
        image_path,
        output_dir / "previews",
        target_bg=target_bg,
        gamma=gamma,
        highlight_pctl=highlight_pctl,
        max_side=max_side,
    )
    diagnostics = _load_or_run_analysis(image_path, analysis_report)
    local_cv = recognize_image(
        previews["paths"]["full"],
        stage=f"{stage}_safe_preview",
        analysis_report=diagnostics,
    )
    local_cv["metadata"]["role"] = "auxiliary_validation_only"
    request = {
        "status": "required",
        "review_images": [
            previews["paths"]["full"],
            previews["paths"]["target"],
            *previews["paths"]["channels"].values(),
        ],
        "instructions": [
            "Judge visible morphology, background continuity, star dominance, and artifacts.",
            "Treat Header/WCS and raw diagnostics as higher-confidence identity evidence.",
            "Do not infer structures or colors absent from the safe previews.",
            "Return uncertainty explicitly when the preview signal is insufficient.",
        ],
        "expected_fields": {
            "target_type": "string|null",
            "target_name": "string|null",
            "confidence": "0..1",
            "visible_features": "array[string]",
            "quality_findings": "array[string]",
            "uncertainties": "array[string]",
        },
    }
    request_path = output_dir / "visual_review_request.json"
    write_json_atomic(request, request_path)
    payload = {
        "schema_version": "1.1",
        "status": "awaiting_ai_visual_review",
        "source": {
            "image_path": str(image_path),
            "stage": stage,
            "format": Path(image_path).suffix.lstrip(".").lower(),
        },
        "recognition_order": [
            "header_wcs",
            "raw_numeric_diagnostics",
            "safe_visual_preview",
            "ai_visual_review",
            "local_cv_auxiliary_validation",
        ],
        "header_wcs": _header_wcs_evidence(image_path, plate_solution),
        "raw_diagnostics": diagnostics,
        "safe_previews": previews,
        "ai_visual_review": {
            **request,
            "request_path": str(request_path),
            "result": None,
        },
        "local_cv_auxiliary_validation": local_cv,
    }
    bundle_path = output_dir / "recognition_workflow.json"
    write_json_atomic(payload, bundle_path)
    payload["bundle_path"] = str(bundle_path)
    return payload


def finalize_recognition_workflow(bundle, ai_review, output_path=None):
    """Attach AI visual judgment, then verify it against Header/WCS and CV."""
    if not isinstance(bundle, dict):
        bundle_path = Path(bundle)
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    else:
        bundle_path = None
        bundle = dict(bundle)
    if not isinstance(ai_review, dict):
        ai_review = json.loads(Path(ai_review).read_text(encoding="utf-8"))
    else:
        ai_review = dict(ai_review)

    confidence = float(ai_review.get("confidence", 0.0))
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("ai_review.confidence must be between 0 and 1")

    header_target = (
        bundle.get("header_wcs", {})
        .get("resolved_target", {})
        .get("resolved_type")
    )
    if header_target == "unknown_deep_sky":
        header_target = None
    visual_target = ai_review.get("target_type")
    cv_target = (
        bundle.get("local_cv_auxiliary_validation", {})
        .get("scene", {})
        .get("target_type")
    )
    checks = {
        "header_visual_consistent": (
            None if not header_target or not visual_target
            else header_target == visual_target
        ),
        "visual_cv_consistent": (
            None if not visual_target or not cv_target
            else visual_target == cv_target
        ),
        "cv_role": "auxiliary_only",
    }
    contradictions = [
        key for key, value in checks.items()
        if key.endswith("_consistent") and value is False
    ]
    if header_target:
        selected_type = header_target
        selected_source = "header_wcs"
    elif visual_target and confidence >= 0.5:
        selected_type = visual_target
        selected_source = "ai_visual_review"
    else:
        selected_type = cv_target
        selected_source = "local_cv_low_confidence_fallback"

    bundle["ai_visual_review"] = {
        **bundle.get("ai_visual_review", {}),
        "status": "completed",
        "result": ai_review,
    }
    bundle["local_cv_auxiliary_validation"]["verification"] = checks
    bundle["decision"] = {
        "target_type": selected_type,
        "source": selected_source,
        "contradictions": contradictions,
        "requires_human_review": bool(contradictions),
        "precedence": [
            "header_wcs",
            "ai_visual_review_if_confident",
            "local_cv_auxiliary_fallback",
        ],
    }
    bundle["status"] = (
        "review_required" if contradictions else "complete"
    )
    destination = Path(output_path) if output_path else bundle_path
    if destination:
        write_json_atomic(bundle, destination)
    return bundle


def analyze_starfield(gray):
    try:
        response = white_tophat(gray, disk(2))
        positive = response[response > 0]
        if positive.size < 5:
            return {"count": 0, "density": "sparse", "coverage": 0.0, "density_per_mpix": 0.0}
        threshold = max(float(np.percentile(positive, 85)), 0.02)
        mask = binary_dilation(response > threshold, structure=disk(1))
        _labeled, count = cc_label(mask)
        coverage = float(np.mean(mask))
        density_per_mpix = float(count / gray.size * 1_000_000)
        if density_per_mpix < 50:
            density = "sparse"
        elif density_per_mpix < 200:
            density = "moderate"
        elif density_per_mpix < 600:
            density = "dense"
        else:
            density = "very_dense"
        return {
            "count": int(count),
            "density": density,
            "coverage": round(coverage, 5),
            "density_per_mpix": round(density_per_mpix, 2),
        }
    except Exception as exc:
        return {
            "count": 0,
            "density": "unknown",
            "coverage": 0.0,
            "density_per_mpix": 0.0,
            "warning": str(exc),
        }


def estimate_primary_region(gray):
    threshold = max(float(np.percentile(gray, 80)), float(gray.mean() + gray.std()))
    mask = gray >= threshold
    if np.mean(mask) < 0.002:
        try:
            otsu = threshold_otsu(gray)
            mask = gray >= otsu
        except Exception:
            mask = gray >= float(np.percentile(gray, 95))
    ys, xs = np.where(mask)
    h, w = gray.shape
    if ys.size == 0:
        return {
            "bbox": [0.0, 0.0, 1.0, 1.0],
            "confidence": 0.2,
            "method": "full_frame_fallback",
        }
    x0 = max(0, int(xs.min()))
    x1 = min(w - 1, int(xs.max()))
    y0 = max(0, int(ys.min()))
    y1 = min(h - 1, int(ys.max()))
    area_ratio = ((x1 - x0 + 1) * (y1 - y0 + 1)) / float(w * h)
    contrast = float(gray[mask].mean() - gray[~mask].mean()) if np.any(~mask) else float(gray[mask].mean())
    return {
        "bbox": [
            round(x0 / w, 4),
            round(y0 / h, 4),
            round((x1 - x0 + 1) / w, 4),
            round((y1 - y0 + 1) / h, 4),
        ],
        "confidence": round(clamp01(0.35 + contrast + min(area_ratio, 0.35)), 3),
        "method": "luminance_saliency",
    }


def color_features(rgb, gray):
    mean_rgb = np.mean(rgb.reshape(-1, 3), axis=0)
    red_bias = float(mean_rgb[0] - max(mean_rgb[1], mean_rgb[2]))
    blue_bias = float(mean_rgb[2] - max(mean_rgb[0], mean_rgb[1]))
    green_bias = float(mean_rgb[1] - max(mean_rgb[0], mean_rgb[2]))
    try:
        hsv = rgb2hsv(np.clip(rgb, 0, 1))
        saturation = float(np.mean(hsv[..., 1]))
    except Exception:
        saturation = 0.0
    bright_ratio = float(np.mean(gray > max(0.2, np.percentile(gray, 95))))
    return {
        "mean_rgb": [round(float(v), 5) for v in mean_rgb],
        "red_bias": round(red_bias, 5),
        "blue_bias": round(blue_bias, 5),
        "green_bias": round(green_bias, 5),
        "saturation_mean": round(saturation, 5),
        "bright_ratio": round(bright_ratio, 5),
    }


def classify_scene(rgb, gray, starfield, colors):
    candidates = []
    density = starfield["density"]
    bright_ratio = colors["bright_ratio"]
    saturation = colors["saturation_mean"]
    red_bias = colors["red_bias"]
    blue_bias = colors["blue_bias"]

    # 发射星云结构特征：检测壳层/弥散结构（大尺度亮区但非致密核心）
    structure_score = 0.0
    try:
        # 检测是否存在大尺度弥散结构（非点状）
        broad = gaussian_filter(gray, sigma=max(gray.shape) / 25)
        detail = gray - gaussian_filter(gray, sigma=max(gray.shape) / 80)
        diffuse_mask = (broad > np.percentile(broad, 75)) & (np.abs(detail) < np.percentile(np.abs(detail), 85))
        diffuse_ratio = float(np.mean(diffuse_mask))
        # 若存在 >10% 面积的弥散亮区，加分
        structure_score = min(diffuse_ratio * 3.0, 0.25)
    except Exception:
        structure_score = 0.0

    # Hα 红色主导是发射星云的关键特征
    ha_dominant = 1.0 if red_bias > max(blue_bias, 0) + 0.02 else 0.0

    emission_score = clamp01(
        0.35 + max(red_bias, 0) * 2.5 + saturation * 0.45
        + structure_score + ha_dominant * 0.15
    )
    reflection_score = clamp01(0.25 + max(blue_bias, 0) * 2.0 + saturation * 0.35)
    # wide_field：dense 星场加分降低（0.35→0.18），避免发射星云误判
    wide_score = clamp01(
        0.25 + (0.18 if density in ("dense", "very_dense") else 0.05)
        + max(0.0, 0.12 - bright_ratio)
    )
    galaxy_score = clamp01(0.25 + min(bright_ratio * 4.0, 0.25) + (0.15 if density in ("sparse", "moderate") else 0.0))
    cluster_score = clamp01(0.2 + (0.35 if density in ("dense", "very_dense") else 0.05) + min(starfield["coverage"] * 3.0, 0.25))
    planetary_score = clamp01(0.18 + min(bright_ratio * 6.0, 0.35) + saturation * 0.2)

    scores = {
        "emission_nebula": emission_score,
        "reflection_nebula": reflection_score,
        "galaxy": galaxy_score,
        "globular_cluster": cluster_score,
        "planetary_nebula": planetary_score,
        "wide_field": wide_score,
    }
    for key, score in sorted(scores.items(), key=lambda item: item[1], reverse=True):
        candidates.append({"label": key, "confidence": round(float(score), 3)})
    best = candidates[0]
    target_type = best["label"] if best["confidence"] >= 0.35 else "unknown_deep_sky"
    confidence = best["confidence"] if target_type != "unknown_deep_sky" else 0.3
    return {
        "label": "deep_sky",
        "target_type": target_type,
        "confidence": round(float(confidence), 3),
        "candidates": candidates,
    }


def quality_tags(gray, starfield, colors):
    tags = []
    density = starfield["density"]
    if density in ("dense", "very_dense"):
        tags.append({"label": "dense_starfield", "confidence": 0.75 if density == "dense" else 0.88})
    else:
        tags.append({"label": "sparse_starfield", "confidence": 0.62 if density == "sparse" else 0.45})
    noise_est = float(np.std(gray - np.clip(gray, 0, 1).mean()))
    if noise_est > 0.18:
        tags.append({"label": "high_noise", "confidence": clamp01(noise_est * 2.5)})
    else:
        tags.append({"label": "low_noise", "confidence": clamp01(0.75 - noise_est)})
    max_bias = max(abs(colors["red_bias"]), abs(colors["blue_bias"]), abs(colors["green_bias"]))
    if max_bias > 0.18:
        tags.append({"label": "severe_color_cast", "confidence": clamp01(max_bias * 3.0)})
    elif max_bias > 0.06:
        tags.append({"label": "mild_color_cast", "confidence": clamp01(0.35 + max_bias * 2.0)})
    if float(np.percentile(gray, 99.5)) > 0.98 and float(np.mean(gray > 0.98)) > 0.01:
        tags.append({"label": "overstretched_core", "confidence": 0.58})
    if float(np.percentile(gray, 10)) < 0.08 and max_bias < 0.08:
        tags.append({"label": "clean_background", "confidence": 0.55})
    return [{"label": item["label"], "confidence": round(clamp01(item["confidence"]), 3)} for item in tags]


def make_detections(gray, primary_region, min_confidence):
    detections = []
    bbox = primary_region["bbox"]
    confidence = min(0.78, primary_region["confidence"] * 0.85)
    if confidence >= min_confidence:
        x, y, w, h = bbox
        h_px, w_px = gray.shape
        x0 = int(x * w_px)
        y0 = int(y * h_px)
        x1 = max(x0 + 1, int((x + w) * w_px))
        y1 = max(y0 + 1, int((y + h) * h_px))
        region = gray[y0:y1, x0:x1]
        detections.append(
            {
                "label": "primary_luminous_structure",
                "bbox": bbox,
                "confidence": round(float(confidence), 3),
                "metadata": {
                    "mean_luminance": round(float(np.mean(region)) if region.size else 0.0, 5),
                    "max_luminance": round(float(np.max(region)) if region.size else 0.0, 5),
                },
            }
        )
    return detections


def load_analysis_report(path):
    if not path:
        return None
    if isinstance(path, dict):
        report = path
    else:
        with open(path, "r", encoding="utf-8") as f:
            report = json.load(f)
    summary = {}
    for section, keys in {
        "brightness": ["darkness_level", "dynamic_range_ratio"],
        "noise": ["noise_level", "background_noise_std"],
        "gradient": ["gradient_severity", "gradient_pattern"],
        "color": ["color_health", "needs_scnr"],
        "starfield": ["star_density", "star_count_estimate"],
        "sharpness": ["sharpness_level"],
    }.items():
        if isinstance(report.get(section), dict):
            summary[section] = {key: report[section][key] for key in keys if key in report[section]}
    return summary


def recognize_image(image_path, stage="final", min_confidence=0.35, analysis_report=None):
    img, meta = read_image(image_path)
    rgb, gray, original_shape, has_alpha = normalize_image(img)
    starfield = analyze_starfield(gray)
    colors = color_features(rgb, gray)
    primary = estimate_primary_region(gray)
    scene = classify_scene(rgb, gray, starfield, colors)
    tags = quality_tags(gray, starfield, colors)
    detections = make_detections(gray, primary, min_confidence)
    warnings = []
    if "warning" in starfield:
        warnings.append(f"starfield: {starfield['warning']}")
    metadata = {
        "recognition_backend": "local_cv",
        "ai_visual_review": "agent_skill_optional",
        "created_at": now_iso(),
        "has_alpha": has_alpha,
        "features": {
            "starfield": starfield,
            "color": colors,
        },
    }
    analysis_summary = load_analysis_report(analysis_report)
    if analysis_summary is not None:
        metadata["analysis_report"] = analysis_summary
    if warnings:
        metadata["warnings"] = warnings
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "image_path": str(image_path),
            "stage": stage,
            "format": meta.get("format", Path(image_path).suffix.lstrip(".").lower() or "unknown"),
            "shape": original_shape,
        },
        "scene": scene,
        "primary_region": primary,
        "detections": detections,
        "quality_tags": tags,
        "metadata": metadata,
    }


def write_json_atomic(payload, output_path):
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(output.parent), delete=False) as tmp:
        json.dump(payload, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
        temp_name = tmp.name
    os.replace(temp_name, output)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Deep-sky image recognition JSON generator")
    parser.add_argument("input", help="Input image path")
    parser.add_argument("--output", required=True, help="Output recognition JSON path")
    parser.add_argument("--stage", choices=["input", "final"], default="final", help="Image stage label")
    parser.add_argument("--min-confidence", type=float, default=0.35, help="Minimum detection confidence")
    parser.add_argument("--analysis-report", default=None, help="Optional analyze.py JSON report")
    parser.add_argument(
        "--workflow-dir",
        default=None,
        help=(
            "Create Header/WCS -> diagnostics -> safe previews -> AI review "
            "request -> local CV auxiliary workflow artifacts"
        ),
    )
    parser.add_argument("--preview-target-bg", type=float, default=0.12)
    parser.add_argument("--preview-gamma", type=float, default=0.45)
    parser.add_argument("--preview-highlight-pctl", type=float, default=99.9)
    parser.add_argument("--preview-max-side", type=int, default=1800)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if not os.path.exists(args.input):
        print(f"[ERROR] 输入文件不存在: {args.input}", file=sys.stderr)
        return 1
    try:
        if args.workflow_dir:
            payload = build_recognition_workflow(
                args.input,
                args.workflow_dir,
                analysis_report=args.analysis_report,
                stage=args.stage,
                target_bg=args.preview_target_bg,
                gamma=args.preview_gamma,
                highlight_pctl=args.preview_highlight_pctl,
                max_side=args.preview_max_side,
            )
        else:
            payload = recognize_image(
                args.input,
                stage=args.stage,
                min_confidence=args.min_confidence,
                analysis_report=args.analysis_report,
            )
        write_json_atomic(payload, args.output)
    except Exception as exc:
        print(f"[ERROR] 识别失败: {exc}", file=sys.stderr)
        return 1
    print(f"[识别] JSON 已写入: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
