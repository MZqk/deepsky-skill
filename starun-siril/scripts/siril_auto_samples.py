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
    """Read NAXIS1 (width) and NAXIS2 (height) from the primary FITS header."""
    header_bytes = bytearray()
    with path.open("rb") as stream:
        while True:
            block = stream.read(2880)
            if not block or len(block) < 2880:
                break
            header_bytes.extend(block)
            if b"END " in block:
                break
            if len(header_bytes) > 2880 * 100:  # 100 blocks max
                break

    text = header_bytes.decode("ascii", errors="ignore")
    cards = [text[i : i + 80] for i in range(0, len(text), 80)]
    values: dict[str, int] = {}
    for card in cards:
        if card.startswith("NAXIS1 ") or card.startswith("NAXIS2 "):
            key = card[:8].strip()
            val_part = card[10:30].strip()
            try:
                values[key] = int(val_part)
            except ValueError:
                pass
    if "NAXIS1" not in values or "NAXIS2" not in values:
        raise ValueError(f"Could not read NAXIS1/NAXIS2 from FITS header: {path}")
    return values["NAXIS1"], values["NAXIS2"]


def _load_luminance_thumbnail(
    source_path: Path,
    preview_path: Path | None,
    target_dim: int = 256,
) -> tuple[list[list[float]], int, int]:
    """Load a downsampled 2D float luminance map and return (map, orig_w, orig_h)."""
    orig_w, orig_h = _read_fits_dimensions(source_path)

    # 1. If a matching preview image exists and PIL is available, use it for super fast luminance
    pil_loaded = False
    lum_grid: list[list[float]] = []

    if preview_path is not None and preview_path.is_file():
        try:
            from PIL import Image

            with Image.open(preview_path) as img:
                thumb = img.convert("L").resize(
                    (target_dim, target_dim), Image.Resampling.BILINEAR
                )
                getter = getattr(thumb, "get_flattened_data", None) or thumb.getdata
                raw_data = list(getter())
                lum_grid = [
                    [
                        raw_data[y * target_dim + x] / 255.0
                        for x in range(target_dim)
                    ]
                    for y in range(target_dim)
                ]
                pil_loaded = True
        except Exception:
            pil_loaded = False

    if pil_loaded and lum_grid:
        return lum_grid, orig_w, orig_h

    # 2. Direct FITS sub-sampling (reads primary HDU without external dependencies)
    try:
        import numpy as np

        # Fast path with numpy
        with source_path.open("rb") as stream:
            header_bytes = bytearray()
            while True:
                block = stream.read(2880)
                header_bytes.extend(block)
                if b"END " in block or len(block) < 2880:
                    break
            data_offset = len(header_bytes)

        # Read dimensions, channels, bitpix, and bzero
        header_text = header_bytes.decode("ascii", errors="ignore")
        bitpix = -32
        bzero = 0.0
        bscale = 1.0
        naxis3 = 1
        for card in [header_text[i : i + 80] for i in range(0, len(header_text), 80)]:
            if card.startswith("BITPIX  "):
                try:
                    bitpix = int(card[10:30].strip())
                except ValueError:
                    pass
            elif card.startswith("BZERO   "):
                try:
                    bzero = float(card[10:30].strip())
                except ValueError:
                    pass
            elif card.startswith("BSCALE  "):
                try:
                    bscale = float(card[10:30].strip())
                except ValueError:
                    pass
            elif card.startswith("NAXIS3  "):
                try:
                    naxis3 = int(card[10:30].strip())
                except ValueError:
                    pass

        # Select numpy dtype
        if bitpix == 16:
            dtype = ">i2"
        elif bitpix == 8:
            dtype = ">u1"
        elif bitpix == 32:
            dtype = ">i4"
        elif bitpix in (-32, -64):
            dtype = ">f4" if bitpix == -32 else ">f8"
        else:
            dtype = ">f4"

        shape = (naxis3, orig_h, orig_w) if naxis3 > 1 else (orig_h, orig_w)
        mmap_arr = np.memmap(
            source_path,
            dtype=dtype,
            mode="r",
            offset=data_offset,
            shape=shape,
        )

        step_x = max(1, orig_w // target_dim)
        step_y = max(1, orig_h // target_dim)

        if naxis3 >= 3:
            # Green channel (index 1) has best SNR for luminance
            layer = mmap_arr[1]
        elif naxis3 == 2:
            layer = mmap_arr[0]
        else:
            layer = mmap_arr

        raw_sub = layer[::step_y, ::step_x][:target_dim, :target_dim].astype(np.float64)
        sub = raw_sub * bscale + bzero
        # Normalize to 0..1
        min_v = float(np.percentile(sub, 1))
        max_v = float(np.percentile(sub, 99))
        denom = max(1e-7, max_v - min_v)
        norm_sub = np.clip((sub - min_v) / denom, 0.0, 1.0)
        return norm_sub.tolist(), orig_w, orig_h
    except Exception:
        pass

    # 3. Fallback: uniform synthetic map if image is unreadable (graceful degradation)
    fallback = [[0.1 for _ in range(target_dim)] for _ in range(target_dim)]
    return fallback, orig_w, orig_h


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

    thumb_dim = 256
    lum_grid, orig_w, orig_h = _load_luminance_thumbnail(
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

    # If too few points due to dense nebula, relax dilation
    if len(candidates) < min(12, target_count // 2):
        candidates = []
        for gy in range(grid_steps):
            for gx in range(grid_steps):
                x_mid = min(thumb_w - 1, max(0, int((gx + 0.5) * cell_w)))
                y_mid = min(thumb_h - 1, max(0, int((gy + 0.5) * cell_h)))
                if not mask[y_mid][x_mid]:
                    candidates.append(
                        (float(x_mid), float(y_mid), lum_grid[y_mid][x_mid])
                    )

    # Sigma clipping on candidate values
    if len(candidates) > target_count:
        vals = [c[2] for c in candidates]
        c_med, c_mad = _median_and_mad(vals)
        c_sig = max(1e-6, 1.4826 * c_mad)
        # Filter outliers
        filtered = [
            c for c in candidates if abs(c[2] - c_med) <= 2.5 * c_sig
        ]
        if len(filtered) >= min(12, target_count // 2):
            candidates = filtered

    # Limit to target_count evenly
    if len(candidates) > target_count:
        step = len(candidates) / float(target_count)
        selected_candidates = [
            candidates[int(i * step)] for i in range(target_count)
        ]
    else:
        selected_candidates = candidates

    # Map back to original FITS coordinates
    scale_x = orig_w / float(thumb_w)
    scale_y = orig_h / float(thumb_h)

    fit_samples = []
    seen_coords: set[tuple[float, float]] = set()

    for idx, (tx, ty, _) in enumerate(selected_candidates, 1):
        # Center in original coordinate space (integer pixel center)
        orig_x = float(int(min(orig_w - 1, max(0, (tx + 0.5) * scale_x))))
        orig_y = float(int(min(orig_h - 1, max(0, (ty + 0.5) * scale_y))))
        coord = (orig_x, orig_y)
        if coord in seen_coords:
            continue
        seen_coords.add(coord)
        fit_samples.append({"id": f"bg-{idx:03d}", "x": orig_x, "y": orig_y})

    source_sha = sha256_file(resolved_source)

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
