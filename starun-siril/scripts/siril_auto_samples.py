#!/usr/bin/env python3
"""Deterministic automated background sample generator for starun-siril.

This module never alters image pixels.  It performs read-only luminance
sampling, star/nebulosity mask exclusion, and local variance minimization to
generate hash-bound background sample contracts matching
``references/background-sample-contract.schema.json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import struct
from typing import Any, Sequence

CONTRACT_SCHEMA = "starun-siril.background-sample-contract.v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_fits_dimensions(path: Path) -> tuple[int, int]:
    from deep_sky_siril_artifacts import fits_geometry
    geometry = fits_geometry(path)
    return geometry["width"], geometry["height"]


def _load_luminance_thumbnail(source_path: Path, preview_path: Path | None, target_dim: int = 256):
    """Sample the complete FITS extent and retain exact coordinates for backmapping."""
    import numpy as np
    from deep_sky_siril_artifacts import read_fits_pixels

    orig_w, orig_h = _read_fits_dimensions(source_path)
    xs = np.rint(np.linspace(0, orig_w - 1, min(orig_w, target_dim))).astype(int)
    ys = np.rint(np.linspace(0, orig_h - 1, min(orig_h, target_dim))).astype(int)
    raw, scale, zero = read_fits_pixels(source_path)
    if preview_path is not None:
        from PIL import Image
        with Image.open(preview_path) as image:
            if image.size != (orig_w, orig_h):
                raise ValueError("Preview geometry must match the scientific source")
            values = np.asarray(image.convert("L"), dtype=float)[np.ix_(ys, xs)] / 255
    else:
        layer = raw[1 if raw.shape[0] == 3 else 0]
        values = np.asarray(layer[np.ix_(ys, xs)], dtype=float) * scale + zero
    if not np.isfinite(values).all():
        raise ValueError("Non-finite background sampling data")
    lo, hi = np.percentile(values, [1, 99])
    values = np.clip((values - lo) / max(1e-7, hi - lo), 0, 1)
    return values.tolist(), orig_w, orig_h, xs.tolist(), ys.tolist()


def _median_and_mad(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    med = (
        sorted_v[n // 2]
        if n % 2 != 0
        else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2.0
    )
    diffs = sorted(abs(x - med) for x in values)
    mad = (
        diffs[n // 2]
        if n % 2 != 0
        else (diffs[n // 2 - 1] + diffs[n // 2]) / 2.0
    )
    return med, mad


def _dilate_mask(mask: list[list[bool]], radius: int = 2) -> list[list[bool]]:
    """Simple binary morphological dilation on 2D boolean grid."""
    h = len(mask)
    w = len(mask[0])
    dilated = [[False for _ in range(w)] for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if mask[y][x]:
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        ny, nx = y + dy, x + dx
                        if 0 <= ny < h and 0 <= nx < w:
                            dilated[ny][nx] = True
    return dilated


def generate_background_samples(
    source_path: Path,
    *,
    preview_path: Path | None = None,
    target_count: int = 36,
    margin_ratio: float = 0.04,
    target_type: str = "general",
) -> dict[str, Any]:
    """Extract reliable background sample points avoiding stars and bright nebulosity."""
    resolved_source = source_path.expanduser().resolve()
    resolved_preview = preview_path.expanduser().resolve() if preview_path else None

    if not 12 <= target_count <= 256:
        raise ValueError("Automatic sampling requires 12..256 target points")
    source_sha = sha256_file(resolved_source)
    thumb_dim = 256
    lum_grid, orig_w, orig_h, sample_xs, sample_ys = _load_luminance_thumbnail(
        resolved_source, resolved_preview, target_dim=thumb_dim
    )
    thumb_h = len(lum_grid)
    thumb_w = len(lum_grid[0]) if thumb_h > 0 else 0
    if thumb_h == 0 or thumb_w == 0:
        raise ValueError("Cannot sample background from empty image grid")

    # Flatten and find background level
    all_pixels = [val for row in lum_grid for val in row]
    bg_med, bg_mad = _median_and_mad(all_pixels)
    sigma = max(1e-6, 1.4826 * bg_mad)

    # Multiplier threshold based on target type
    if target_type in ("emission_nebula", "diffuse_nebula"):
        k_high = 1.0  # Conservative: avoid delicate gas filaments
        dilation_r = 3
    elif target_type in ("galaxy",):
        k_high = 1.3  # Allow closer samples to galaxy halo
        dilation_r = 2
    else:
        k_high = 1.5
        dilation_r = 2

    high_thresh = bg_med + k_high * sigma
    low_thresh = max(0.0, bg_med - 3.5 * sigma)

    # Build exclusion mask
    margin_x = int(thumb_w * margin_ratio)
    margin_y = int(thumb_h * margin_ratio)
    mask = [[False for _ in range(thumb_w)] for _ in range(thumb_h)]

    for y in range(thumb_h):
        for x in range(thumb_w):
            # Exclude margins
            if (
                x < margin_x
                or x >= thumb_w - margin_x
                or y < margin_y
                or y >= thumb_h - margin_y
            ):
                mask[y][x] = True
            elif lum_grid[y][x] > high_thresh or lum_grid[y][x] < low_thresh:
                mask[y][x] = True

    # Dilate high-signal zones to avoid transition halos
    dilated_mask = _dilate_mask(mask, radius=dilation_r)

    # Grid division
    # e.g., for 36 samples, use ~7x7 grid
    grid_steps = max(3, int(math.ceil(math.sqrt(target_count * 1.5))))
    cell_w = thumb_w / grid_steps
    cell_h = thumb_h / grid_steps

    candidates: list[tuple[float, float, float]] = []  # (thumb_x, thumb_y, val)

    patch_half = 2
    for gy in range(grid_steps):
        for gx in range(grid_steps):
            x_start = int(gx * cell_w)
            x_end = int((gx + 1) * cell_w)
            y_start = int(gy * cell_h)
            y_end = int((gy + 1) * cell_h)

            best_point: tuple[float, float, float] | None = None
            min_local_variance = float("inf")

            for y in range(y_start + patch_half, y_end - patch_half):
                for x in range(x_start + patch_half, x_end - patch_half):
                    if 0 <= y < thumb_h and 0 <= x < thumb_w:
                        if dilated_mask[y][x]:
                            continue
                        # Compute patch variance
                        patch = [
                            lum_grid[y + dy][x + dx]
                            for dy in range(-patch_half, patch_half + 1)
                            for dx in range(-patch_half, patch_half + 1)
                            if 0 <= y + dy < thumb_h and 0 <= x + dx < thumb_w
                        ]
                        if not patch:
                            continue
                        p_mean = sum(patch) / len(patch)
                        p_var = sum((v - p_mean) ** 2 for v in patch) / len(patch)

                        # Bias towards background median
                        penalty = abs(p_mean - bg_med) * 0.1
                        cost = p_var + penalty

                        if cost < min_local_variance:
                            min_local_variance = cost
                            best_point = (float(x), float(y), lum_grid[y][x])

            if best_point is not None:
                candidates.append(best_point)

    # Never relax the exclusion mask or restore outliers to satisfy a point count.
    if candidates:
        c_med, c_mad = _median_and_mad([c[2] for c in candidates])
        c_sig = max(1e-6, 1.4826 * c_mad)
        candidates = [c for c in candidates if abs(c[2] - c_med) <= 2.5 * c_sig]
    if len(candidates) < 12:
        raise ValueError(f"Only {len(candidates)} background points survive exclusion; at least 12 required. Use manual sampling.")

    # Limit to target_count evenly
    if len(candidates) > target_count:
        step = len(candidates) / float(target_count)
        selected_candidates = [
            candidates[int(i * step)] for i in range(target_count)
        ]
    else:
        selected_candidates = candidates

    # Map back to original FITS coordinates
    fit_samples = []
    seen_coords: set[tuple[float, float]] = set()

    for idx, (tx, ty, _) in enumerate(selected_candidates, 1):
        # Center in original coordinate space (integer pixel center)
        orig_x = float(sample_xs[int(tx)])
        orig_y = float(sample_ys[int(ty)])
        coord = (orig_x, orig_y)
        if coord in seen_coords:
            continue
        seen_coords.add(coord)
        fit_samples.append({"id": f"bg-{idx:03d}", "x": orig_x, "y": orig_y})

    if len(fit_samples) < 12:
        raise ValueError("Fewer than 12 distinct background points; use manual sampling")
    if sha256_file(resolved_source) != source_sha:
        raise ValueError("Scientific source changed during sampling")

    return {
        "schema": CONTRACT_SCHEMA,
        "source": {
            "path": str(resolved_source),
            "sha256": source_sha,
            "width": orig_w,
            "height": orig_h,
        },
        "fit_samples": fit_samples,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate automated, hash-bound background sample contract."
    )
    parser.add_argument(
        "--source", required=True, help="Path to the input FITS source file."
    )
    parser.add_argument(
        "--output", required=True, help="Destination path for background-sample-contract.json"
    )
    parser.add_argument(
        "--preview",
        required=False,
        default=None,
        help="Optional preview JPEG path for faster mask detection.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=36,
        help="Target number of background samples (default: 36).",
    )
    parser.add_argument(
        "--target-type",
        default="general",
        choices=["general", "emission_nebula", "diffuse_nebula", "galaxy", "cluster"],
        help="Object class for tailored mask expansion.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_path = Path(args.source).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    preview_path = Path(args.preview).expanduser().resolve() if args.preview else None

    if not source_path.is_file():
        raise FileNotFoundError(f"Source file not found: {source_path}")

    contract = generate_background_samples(
        source_path,
        preview_path=preview_path,
        target_count=args.samples,
        target_type=args.target_type,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output_path.with_name(f".{output_path.name}.tmp")
    temp_output.write_text(
        json.dumps(contract, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    temp_output.replace(output_path)
    print(
        f"Generated {len(contract['fit_samples'])} background samples into {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
