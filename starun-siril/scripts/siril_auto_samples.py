#!/usr/bin/env python3
"""Propose deterministic, hash-bound sky samples without changing image pixels."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Sequence

CONTRACT_SCHEMA = "starun-siril.background-sample-contract.v1"
REPORT_SCHEMA = "starun-siril.background-sampling-report.v1"
DEFAULT_SAMPLES_PER_LINE = 20
MAX_SAMPLES = 256
NATIVE_SAMPLE_SIZE = 25
REGION_PROPOSAL_SCHEMA = 'starun-siril.background-region-proposals.v1'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_fits_dimensions(path: Path) -> tuple[int, int]:
    from deep_sky_siril_artifacts import read_scientific_pixels
    pixels, _ = read_scientific_pixels(path)
    return pixels.shape[2], pixels.shape[1]


def preview_y(native_y: float, height: int) -> float:
    """SirilPy background positions already use displayed top-left pixel centers."""
    return native_y


def _load_sampling_image(source: Path, preview: Path | None):
    import numpy as np
    from deep_sky_siril_artifacts import read_scientific_pixels
    pixels, _ = read_scientific_pixels(source)
    planes = pixels[:, ::-1]
    height, width = planes.shape[1:]
    if preview is not None:
        from PIL import Image
        with Image.open(preview) as image:
            if image.size != (width, height):
                raise ValueError('Review preview geometry must match the scientific source')
    domain = 'scientific-xisf' if source.suffix.lower() == '.xisf' else 'scientific-fits'
    if planes.shape[1:] != (height, width) or planes.shape[0] not in (1, 3):
        raise ValueError("Sampling requires a mono or RGB primary image with matching geometry")
    if not np.isfinite(planes).all():
        raise ValueError("Non-finite background sampling data; no executable contract")
    return planes, width, height, domain


def _pool_planes(planes, target_dim: int = 512):
    """Pool complete square areas; a common pixel scale preserves aspect ratio."""
    import numpy as np

    channels, height, width = planes.shape
    step = max(1, math.ceil(max(width, height) / target_dim))
    rows, columns = math.ceil(height / step), math.ceil(width / step)
    arrays = [np.empty((channels, rows, columns), dtype=np.float32) for _ in range(4)]
    medians, peaks, p95, minima = arrays
    full_rows, full_columns = height // step, width // step
    if full_rows and full_columns:
        blocks = planes[:, :full_rows * step, :full_columns * step].reshape(
            channels, full_rows, step, full_columns, step).transpose(0, 1, 3, 2, 4)
        for destination, reducer in zip(arrays, (np.median, np.max,
                                                lambda a, axis: np.percentile(a, 95, axis=axis), np.min)):
            destination[:, :full_rows, :full_columns] = reducer(blocks, axis=(-2, -1))
    for y in range(rows):
        for x in range(columns):
            if y < full_rows and x < full_columns:
                continue
            block = planes[:, y * step:min(height, (y + 1) * step),
                           x * step:min(width, (x + 1) * step)]
            medians[:, y, x] = np.median(block, axis=(1, 2))
            peaks[:, y, x] = np.max(block, axis=(1, 2))
            p95[:, y, x] = np.percentile(block, 95, axis=(1, 2))
            minima[:, y, x] = np.min(block, axis=(1, 2))
    xs = np.minimum(np.arange(columns) * step + step // 2, width - 1)
    ys = np.minimum(np.arange(rows) * step + step // 2, height - 1)
    return medians, peaks, p95, minima, xs, ys, step


def _load_luminance_thumbnail(source_path: Path, preview_path: Path | None, target_dim: int = 512):
    """Compatibility helper returning area medians and exact native pixel centers."""
    import numpy as np
    planes, width, height, _ = _load_sampling_image(source_path, preview_path)
    medians, _, _, _, xs, ys, _ = _pool_planes(planes, target_dim)
    lo, hi = np.percentile(medians, [1, 99], axis=(1, 2))
    normalized = (medians - lo[:, None, None]) / np.maximum(1e-7, hi - lo)[:, None, None]
    return np.max(normalized, axis=0).tolist(), width, height, xs.tolist(), ys.tolist()


def _median_filter(values, size: int = 5):
    import numpy as np
    patches = np.lib.stride_tricks.sliding_window_view(np.pad(values, size // 2, mode="edge"), (size, size))
    return np.median(patches, axis=(-2, -1))


def _box_sum(values, radius: int):
    import numpy as np
    padded = np.pad(values, radius, mode="constant")
    summed = np.pad(padded, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    size = 2 * radius + 1
    return summed[size:, size:] - summed[:-size, size:] - summed[size:, :-size] + summed[:-size, :-size]


def _dilate_mask(mask, radius: int = 2):
    import numpy as np
    return _box_sum(np.asarray(mask, dtype=np.int64), radius) > 0


def _robust_plane(values):
    """Fit a detection-only first-order trend; never subtract it from the source."""
    import numpy as np
    y, x = np.indices(values.shape)
    design = np.column_stack((np.ones(values.size), x.ravel() / max(1, values.shape[1] - 1),
                              y.ravel() / max(1, values.shape[0] - 1)))
    flat = values.ravel()
    keep = np.ones(flat.size, dtype=bool)
    for _ in range(6):
        coefficients = np.linalg.lstsq(design[keep], flat[keep], rcond=None)[0]
        residual = flat - design @ coefficients
        center = np.median(residual[keep])
        sigma = max(1e-7, 1.4826 * np.median(np.abs(residual[keep] - center)))
        updated = (residual >= center - 3 * sigma) & (residual <= center + 2.5 * sigma)
        if updated.sum() < 12 or np.array_equal(updated, keep):
            break
        keep = updated
    return (design @ coefficients).reshape(values.shape)


def _exclusion_mask(medians, peaks, p95, target_type: str, pixel_noises=None,
                    block_size: int = 1, detection_floor: float = 1e-7, components=None):
    import numpy as np
    stellar = np.zeros(medians.shape[1:], dtype=bool)
    structure = stellar.copy()
    residuals = []
    measured_noises = []
    for channel, (median, peak, percentile) in enumerate(zip(medians, peaks, p95)):
        residual = median - _robust_plane(median)
        differences = np.concatenate((np.diff(residual, axis=0).ravel(), np.diff(residual, axis=1).ravel()))
        noise = max(detection_floor, 1.4826 * np.median(np.abs(differences - np.median(differences))) / math.sqrt(2))
        pixel_noise = max(noise, float(np.median(np.maximum(0, percentile - median))) / 1.645)
        if pixel_noises is not None:
            pixel_noise = max(pixel_noise, pixel_noises[channel])
        measured_noises.append(float(pixel_noise))
        smooth = _median_filter(residual)
        prominence = peak - (median - residual + smooth)
        neighbors = np.lib.stride_tricks.sliding_window_view(np.pad(prominence, 1, mode="edge"), (3, 3))
        stellar |= (prominence > 5 * pixel_noise) & (prominence >= np.max(neighbors, axis=(-2, -1)))
        extended = np.abs(smooth - np.median(smooth)) > 1.5 * noise
        # Require connected support; isolated grain is not continuous nebulosity.
        structure |= extended & (_box_sum(extended.astype(np.int64), 1) >= 5)
        residuals.append(residual)
    radius = 4 if target_type == "galaxy" else 3 if target_type in ("emission_nebula", "diffuse_nebula") else 2
    stellar_radius = max(1, math.ceil(6 / block_size))
    protected = np.asarray(_dilate_mask(stellar, stellar_radius)) | np.asarray(_dilate_mask(structure, radius))
    if components is not None:
        components.update(structure_protected=_dilate_mask(structure, radius),
                          structure=structure, stellar=stellar, pixel_noises=measured_noises)
    return protected, np.asarray(residuals), {
        "stellar_blocks": int(stellar.sum()), "structure_blocks": int(structure.sum()),
        "protected_blocks": int(protected.sum()),
    }


def _footprint_hits(mask, xs, ys, step, size=25):
    """Intersect native footprints with cells, without rounding a dilation radius."""
    import numpy as np
    integral = np.pad(np.asarray(mask, dtype=np.int64), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    half = size // 2
    x0 = np.clip((xs-half)//step, 0, mask.shape[1])
    y0 = np.clip((ys-half)//step, 0, mask.shape[0])
    x1 = np.clip((xs-half+size+step-1)//step, 0, mask.shape[1])
    y1 = np.clip((ys-half+size+step-1)//step, 0, mask.shape[0])
    return (integral[y1[:, None], x1] - integral[y0[:, None], x1]
            - integral[y1[:, None], x0] + integral[y0[:, None], x0]) > 0


def _rectangle_hits(rectangles, xs, ys, size=25):
    import numpy as np
    half = size // 2
    result = np.zeros((len(ys), len(xs)), dtype=bool)
    for x0, y0, x1, y1 in rectangles:
        result |= ((xs[None, :]-half < x1) & (xs[None, :]-half+size > x0)
                   & (ys[:, None]-half < y1) & (ys[:, None]-half+size > y0))
    return result


def _sparse_star_check(planes, x, y, pixel_noises):
    """Measure contamination only; never return replacement science samples."""
    import numpy as np
    noise = np.asarray(pixel_noises, dtype=float)
    xi = np.clip(np.arange(x-22, x+23), 0, planes.shape[2]-1)
    yi = np.clip(np.arange(y-22, y+23), 0, planes.shape[1]-1)
    patch = planes[:, yi[:, None], xi].astype(float)
    local = np.asarray([_median_filter(channel, 9) for channel in patch])
    positive = patch-local
    bright = np.any((positive > 20*noise[:, None, None]) | (patch >= 1), axis=0)
    bright_guard = _dilate_mask(bright, 6)[10:35, 10:35]
    compact = np.any(positive > 5*noise[:, None, None], axis=0)
    mask = _dilate_mask(compact, 2)[10:35, 10:35]
    values = patch[:, 10:35, 10:35]
    fraction = float(mask.mean())
    full = np.median(values, axis=(1, 2))
    delta = np.abs(full-np.median(values[:, ~mask], axis=1)) if not mask.all() else np.full(len(noise), np.inf)
    limits = np.maximum(.15*noise, 1e-6)
    reasons = []
    if bright_guard.any(): reasons.append('bright_star_or_saturation_guard')
    if fraction > .10: reasons.append('compact_star_fraction')
    if np.any(delta > limits): reasons.append('median_contamination')
    return {'x': int(x), 'y': int(y), 'accepted': not reasons, 'reasons': reasons,
            'compact_star_fraction': fraction,
            'median_difference': [float(v) if np.isfinite(v) else None for v in delta],
            'median_difference_limit': limits.tolist()}


def _grid_candidates(safe, stability, xs, ys, width, height, columns, rows, vet=None):
    import numpy as np
    candidates = []
    for row in range(rows):
        yi = np.flatnonzero((ys >= row*height/rows) & (ys < (row+1)*height/rows))
        for column in range(columns):
            xi = np.flatnonzero((xs >= column*width/columns) & (xs < (column+1)*width/columns))
            gy, gx = np.meshgrid(yi, xi, indexing='ij')
            gy, gx = gy[safe[gy, gx]], gx[safe[gy, gx]]
            distance = (xs[gx]-(column+.5)*width/columns)**2 + (ys[gy]-(row+.5)*height/rows)**2
            for index in np.lexsort((xs[gx], ys[gy], distance, stability[gy, gx])):
                x, y = int(xs[gx[index]]), int(ys[gy[index]])
                if vet is None or vet(x, y):
                    candidates.append((x, y, float(stability[gy[index], gx[index]])))
                    break
    return candidates


def assess_sample_coverage(points: Sequence[dict[str, Any]], width: int, height: int,
                           margin_ratio: float = 0.04, *, layout='standard') -> dict[str, Any]:
    """Apply the same spatial gate to automatic proposals and manual additions."""
    import numpy as np
    from deep_sky_siril_background import validate_layout, plane_geometry
    limited = validate_layout(layout) == 'limited_plane_v1'
    coordinates = np.asarray([(p["x"], p["y"]) for p in points], dtype=float).reshape(-1, 2)
    valid = np.isfinite(coordinates).all() and bool(np.all(coordinates >= 0)) and bool(
        np.all(coordinates < [width, height]))
    usable = np.asarray([width, height]) * (1 - 2 * margin_ratio)
    normalized = (coordinates - np.asarray([width, height]) * margin_ratio) / usable
    counts = [int(np.sum((normalized[:, 1] >= i / 3) & (normalized[:, 1] < (i + 1) / 3))) for i in range(3)]
    spans = np.ptp(normalized, axis=0) if len(points) else np.zeros(2)
    reasons = []
    if not valid:
        reasons.append("Invalid manual or automatic coordinates")
    minimum = 6 if limited else 12
    if len(points) < minimum:
        reasons.append(f"Only {len(points)} reliable background points; at least {minimum} required")
    if not limited and any(count < 2 for count in counts):
        reasons.append("Each horizontal third requires at least two sky points, including the middle third")
    if np.any(spans < .60):
        reasons.append("X/Y spans must each reach 60% of the usable frame")
    geometry = plane_geometry(coordinates, width, height) if limited and valid else None
    if limited and (geometry is None or geometry['rank'] != 3 or geometry['condition_number'] is None or geometry['condition_number'] > 100):
        reasons.append('Limited plane fitting needs rank 3 and condition number <= 100')
    return {**({'layout': layout, 'plane_geometry': geometry} if limited else {}),
            "status": "insufficient" if reasons else "sufficient", "count": len(points),
            "horizontal_thirds_top_to_bottom": counts, "x_span_fraction": float(spans[0]),
            "y_span_fraction": float(spans[1]), "reasons": reasons}


def _spatial_selection(candidates, limit: int, width: int, height: int, footprint_size: int = NATIVE_SAMPLE_SIZE):
    import numpy as np
    from deep_sky_siril_background import intersects, sample_rect
    candidates = sorted(candidates, key=lambda point: (point[1], point[0]))
    if not candidates:
        return []
    coords = np.asarray([point[:2] for point in candidates]) / [width, height]
    boxes = [sample_rect(*point[:2], footprint_size) for point in candidates]
    selected, available = [], set(range(len(candidates)))

    def select(index):
        selected.append(index)
        available.difference_update(i for i in list(available) if intersects(boxes[index], boxes[i]))

    # Preserve spatial seeding and farthest-point ordering at every candidate count.
    for row in range(3):
        for column in range(3):
            members = [i for i in sorted(available) if row/3 <= coords[i, 1] < (row+1)/3
                       and column/3 <= coords[i, 0] < (column+1)/3]
            if members and len(selected) < limit:
                center = np.asarray([(column+.5)/3, (row+.5)/3])
                select(members[int(np.argmin(np.sum((coords[members]-center)**2, axis=1)))])
    for row in range(3):
        while len(selected) < limit and sum(row/3 <= coords[i, 1] < (row+1)/3 for i in selected) < 2:
            members = [i for i in sorted(available) if row/3 <= coords[i, 1] < (row+1)/3]
            if not members:
                break
            distances = np.min(np.sum((coords[members, None]-coords[selected])**2, axis=2), axis=1)
            select(members[int(np.argmax(distances))])
    while available and len(selected) < limit:
        members = sorted(available)
        distances = np.min(np.sum((coords[members, None]-coords[selected])**2, axis=2), axis=1)
        select(members[int(np.argmax(distances))])
    return [candidates[index] for index in sorted(selected)]


def generate_background_samples(
    source_path: Path, *, preview_path: Path | None = None,
    target_count: int | None = None, samples_per_line: int | None = None,
    margin_ratio: float = 0.04, target_type: str = "general",
    footprint_size: int = NATIVE_SAMPLE_SIZE, exclude_regions_path: Path | None = None, diagnostics: dict[str, Any] | None = None,
    background_layout='standard',
) -> dict[str, Any]:
    import numpy as np
    from deep_sky_siril_background import validate_layout
    limited = validate_layout(background_layout) == 'limited_plane_v1'
    if limited and (exclude_regions_path is None or footprint_size != NATIVE_SAMPLE_SIZE):
        raise ValueError('Limited plane sampling requires --exclude-regions and scientific 25x25 fit samples')
    if target_count is not None and samples_per_line is not None:
        raise ValueError("--samples and --samples-per-line are mutually exclusive")
    minimum = 6 if limited else 12
    if target_count is not None and not minimum <= target_count <= MAX_SAMPLES:
        raise ValueError(f'Automatic sampling requires {minimum}..256 target points')
    density = DEFAULT_SAMPLES_PER_LINE if samples_per_line is None else samples_per_line
    if not 4 <= density <= 64 or not 0 <= margin_ratio < .25 or footprint_size < 1:
        raise ValueError("Sampling requires 4..64 points per line, a valid margin and footprint")
    source = source_path.expanduser().resolve()
    preview = preview_path.expanduser().resolve() if preview_path else None
    report = diagnostics if diagnostics is not None else {}
    report.update(schema=REPORT_SCHEMA, status="failed", reasons=[], source={"path": str(source), "sha256": sha256_file(source)})
    if preview is not None:
        report["preview"] = {"path": str(preview), "sha256": sha256_file(preview)}
    planes, width, height, domain = _load_sampling_image(source, preview)
    if limited and (preview is not None or domain not in {'scientific-fits', 'scientific-xisf'}):
        raise ValueError('Limited plane sampling requires scientific FITS/XISF without preview fallback')
    if limited:
        report['layout'] = background_layout
    report["source"].update(width=width, height=height)
    report.update(sampling_source=domain, coordinates="siril-bgsample-top-left-pixel-centers",
                  footprint_size=footprint_size, margin_ratio=margin_ratio,
                  preview_is_scientific_parent=False)
    medians, peaks, p95, _, xs, ys, step = _pool_planes(planes)
    report["thumbnail"] = {"width": len(xs), "height": len(ys), "block_size": step, "reducer": "area-median"}
    if target_count is None:
        columns, rows, limit = density, math.ceil(density * height / width), MAX_SAMPLES
        mode = "samples-per-line"
    else:
        columns = math.ceil(math.sqrt(target_count * 1.5 * width / height))
        if limited:
            columns = max(columns, density)
        rows, limit, mode = math.ceil(columns * height / width), target_count, "target-total"
    report["grid"] = {"columns": columns, "rows": rows, "cells": columns * rows,
                      "mode": mode, "requested": density if target_count is None else target_count, "limit": limit}
    pixel_noises = []
    for plane in planes:
        differences = np.concatenate(((plane[::step, :-1:step] - plane[::step, 1::step]).ravel(),
                                      (plane[:-1:step, ::step] - plane[1::step, ::step]).ravel()))
        floor = 1e-7
        pixel_noises.append(max(floor, float(1.4826 * np.median(np.abs(differences - np.median(differences))) / math.sqrt(2))))
    components = {}
    protected, residuals, exclusions = _exclusion_mask(medians, peaks, p95, target_type, pixel_noises, step,
                                                     1e-7,
                                                     components)
    # Protect zero-filled padding at pixel resolution; signed sky is not an invalid edge.
    invalid_pixels = np.all(planes == 0, axis=0)
    padded = np.pad(invalid_pixels, ((0, len(ys) * step - height), (0, len(xs) * step - width)))
    invalid = padded.reshape(len(ys), step, len(xs), step).any(axis=(1, 3))
    half = footprint_size // 2
    protected = _footprint_hits(protected, xs, ys, step, footprint_size)
    invalid_footprints = (_footprint_hits(invalid_pixels, xs, ys, 1, footprint_size)
                          if invalid_pixels.any() else np.zeros_like(protected))
    edge_x, edge_y = max(half, math.ceil(width * margin_ratio)), max(half, math.ceil(height * margin_ratio))
    edges = (xs[None, :] < edge_x) | (xs[None, :] >= width - edge_x) | (ys[:, None] < edge_y) | (ys[:, None] >= height - edge_y)
    heldout = np.zeros_like(protected, dtype=bool)
    foreground = np.zeros_like(protected, dtype=bool)
    if exclude_regions_path is not None:
        from deep_sky_siril_background import load_regions, validate_samples, SAMPLING_POLICY
        exclude_regions_path = exclude_regions_path.expanduser().resolve()
        regions, digest = load_regions(exclude_regions_path, source, scientific=True, sampling_policy=SAMPLING_POLICY,
                                       layout=background_layout)
        padded_regions = [(r['rect'][0]-25, r['rect'][1]-25, r['rect'][2]+25, r['rect'][3]+25)
                          for r in regions['regions'] if r['role'] == 'background']
        heldout = _rectangle_hits(padded_regions, xs, ys, footprint_size)
        foreground = _rectangle_hits([r['rect'] for r in regions['regions'] if r['role'] != 'background'],
                                     xs, ys, footprint_size)
        report['exclusion_regions'] = {'path': str(exclude_regions_path), 'sha256': digest}
    safe = ~(protected | invalid_footprints | edges | heldout | foreground)
    exclusions.update(invalid_blocks=int(invalid.sum()), footprint_protected_positions=int(protected.sum()),
                      invalid_footprint_positions=int(invalid_footprints.sum()), edge_positions=int(edges.sum()),
                      safe_positions=int(safe.sum()), heldout_positions=int(heldout.sum()),
                      foreground_positions=int(foreground.sum()))
    report["exclusions"] = exclusions
    local_variance = []
    for residual in residuals:
        mean = _box_sum(residual, 1) / 9
        local_variance.append(np.maximum(0, _box_sum(residual * residual, 1) / 9 - mean * mean))
    # Do not let floating-point plane-fit residuals choose cell edges on flat sky.
    stability = np.maximum(1e-14, np.max(local_variance, axis=0))
    candidates = _grid_candidates(safe, stability, xs, ys, width, height, columns, rows)
    exclusions["empty_grid_cells"] = columns * rows - len(candidates)
    selected = _spatial_selection(candidates, limit, width, height, footprint_size)
    points = [{"id": f"bg-{index:03d}", "x": float(x), "y": float(y)} for index, (x, y, _) in enumerate(selected, 1)]
    strict = assess_sample_coverage(points, width, height, margin_ratio, layout=background_layout)
    report['strict'] = {'coverage': strict, 'fit_samples': points, 'candidate_count': len(candidates),
                        'empty_grid_cells': columns * rows - len(candidates)}
    report['recovery'] = {'attempted': False}
    if (strict['reasons'] and exclude_regions_path is not None and domain in {'scientific-fits', 'scientific-xisf'}
            and footprint_size == NATIVE_SAMPLE_SIZE):
        structure = _footprint_hits(components['structure_protected'], xs, ys, step, footprint_size)
        recovery_safe = ~(structure | invalid_footprints | edges | heldout | foreground)
        checked = []
        def vet(x, y):
            item = _sparse_star_check(planes, x, y, components['pixel_noises'])
            checked.append(item)
            return item['accepted']
        candidates = _grid_candidates(recovery_safe, stability, xs, ys, width, height, columns, rows, vet)
        selected = _spatial_selection(candidates, limit, width, height, footprint_size)
        points = [{'id': f'bg-{i:03d}', 'x': float(x), 'y': float(y)} for i, (x, y, _) in enumerate(selected, 1)]
        report['recovery'] = {'attempted': True, 'method': 'sparse_compact_star_v1',
                              'interpretation': 'Engineering heuristic, not calibrated physical confidence',
                              'pixel_noises': components['pixel_noises'], 'box_diagnostics': checked,
                              'coverage': assess_sample_coverage(points, width, height, margin_ratio, layout=background_layout),
                              'fit_samples': points, 'candidate_count': len(candidates),
                              'empty_grid_cells': columns * rows - len(candidates)}
    exclusions['empty_grid_cells'] = columns * rows - len(candidates)
    report.update(candidate_count=len(candidates), final_count=len(points), fit_samples=points,
                  coverage=assess_sample_coverage(points, width, height, margin_ratio, layout=background_layout))
    if sha256_file(source) != report["source"]["sha256"]:
        raise ValueError("Scientific source changed during sampling; no executable contract")
    if preview is not None and sha256_file(preview) != report["preview"]["sha256"]:
        raise ValueError("Sampling preview changed during sampling; no executable contract")
    if exclude_regions_path is not None and sha256_file(exclude_regions_path) != report['exclusion_regions']['sha256']:
        raise ValueError('Validation regions changed during sampling; no executable contract')
    report["reasons"] = report["coverage"]["reasons"]
    if report["reasons"]:
        raise ValueError("; ".join(report["reasons"]) + ". Use manual sampling; do not relax protection.")
    contract = {"schema": CONTRACT_SCHEMA, "source": dict(report["source"]), "fit_samples": points}
    if exclude_regions_path is not None:
        validate_samples(contract, regions, sampling_policy=SAMPLING_POLICY, layout=background_layout)
    report["status"] = "proposed"
    return contract


def propose_validation_regions(source_path: Path, *, target_type='general', diagnostics=None, background_layout='standard'):
    """Propose source-only controls; the schema intentionally cannot authorize a fit."""
    import numpy as np
    from deep_sky_siril_background import intersects, validate_layout
    size = 50 if validate_layout(background_layout) == 'limited_plane_v1' else 75
    blocks_per_side = size // 25
    source = source_path.expanduser().resolve()
    if source.suffix.lower() not in {'.fit', '.fits', '.fts', '.xisf'}:
        raise ValueError('Validation region proposals require scientific mono/RGB FITS or XISF')
    digest = sha256_file(source)
    planes, width, height, _ = _load_sampling_image(source, None)
    medians, peaks, p95, _, xs, ys, step = _pool_planes(planes)
    noises = []
    for plane in planes:
        differences = np.concatenate(((plane[::step, :-1:step]-plane[::step, 1::step]).ravel(),
                                      (plane[:-1:step, ::step]-plane[1::step, ::step]).ravel()))
        noises.append(max(1e-7, float(1.4826*np.median(np.abs(differences-np.median(differences)))/math.sqrt(2))))
    components = {}
    _exclusion_mask(medians, peaks, p95, target_type, noises, step, components=components)
    candidates = []
    columns, rows = 20, math.ceil(20*height/width)
    for row in range(rows):
        for col in range(columns):
            x, y = int((col+.5)*width/columns), int((row+.5)*height/rows)
            rect = [x-size//2, y-size//2, x-size//2+size, y-size//2+size]
            if rect[0] < .04*width or rect[2] > .96*width or rect[1] < .04*height or rect[3] > .96*height:
                continue
            values = planes[:, rect[1]:rect[3], rect[0]:rect[2]]
            local = np.asarray([_median_filter(a, 9) for a in values])
            compact = np.any(values-local > 5*np.asarray(components['pixel_noises'])[:, None, None], axis=0)
            pollution = float(_dilate_mask(compact, 2).mean())
            sy = slice(rect[1]//step, math.ceil(rect[3]/step))
            sx = slice(rect[0]//step, math.ceil(rect[2]/step))
            structure_fraction = float(components['structure_protected'][sy, sx].mean())
            blocks = values.reshape(len(planes), blocks_per_side, 25, blocks_per_side, 25).transpose(0, 1, 3, 2, 4)
            block_medians = np.median(blocks, axis=(-2, -1)).reshape(len(planes), blocks_per_side**2)
            stability = float(np.max(np.median(np.abs(block_medians-np.median(block_medians, axis=1)[:, None]), axis=1)))
            third = min(2, int((y-.04*height)/(.92*height)*3))
            cell = (third, int(x >= width/2))
            candidates.append({'rect': rect, 'search_cell': list(cell),
                               'structure_fraction': structure_fraction,
                               'compact_star_fraction': pollution, 'block_median_mad_max': stability})
    chosen = []
    for third in range(3):
        for side in range(2):
            members = sorted((c for c in candidates if c['search_cell'] == [third, side]),
                             key=lambda c: (c['structure_fraction'], c['compact_star_fraction'],
                                            c['block_median_mad_max'], c['rect'][1], c['rect'][0]))
            count = 0
            for item in members:
                if any(intersects(item['rect'], p['rect']) for p in chosen):
                    continue
                chosen.append({'id': f'sky-proposal-{len(chosen)+1:02d}', **item})
                count += 1
                if count == 3: break
    if sha256_file(source) != digest:
        raise ValueError('Scientific source changed during region planning')
    result = {'schema': REGION_PROPOSAL_SCHEMA,
              'source': {'path': str(source), 'sha256': digest, 'width': width, 'height': height},
              'requires_visual_confirmation': True, 'proposals': chosen}
    if background_layout != 'standard':
        result['layout'] = background_layout
    if diagnostics is not None:
        diagnostics.update(schema=REGION_PROPOSAL_SCHEMA, status='proposed', source=result['source'],
                           requires_visual_confirmation=True, candidate_count=len(candidates),
                           proposal_count=len(chosen), proposals=chosen)
        if background_layout != 'standard':
            diagnostics['layout'] = background_layout
    return result


def generate_background_regions(
    source_path: Path, *, target_count: int = 36, target_type: str = "general",
    region_size: int = 16, diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Propose scientific RGB background ROIs; visual verification is still required."""
    import numpy as np
    from deep_sky_siril_artifacts import read_scientific_pixels

    if not 12 <= target_count <= 64 or not 8 <= region_size <= 64:
        raise ValueError("Background regions require 12..64 samples and an 8..64 pixel size")
    source_path = source_path.expanduser().resolve()
    raw, _ = read_scientific_pixels(source_path)
    scale, zero = 1, 0
    if raw.shape[0] != 3:
        raise ValueError("Background neutralization requires scientific RGB FITS or XISF")
    contract = generate_background_samples(source_path, target_count=target_count,
                                          target_type=target_type, footprint_size=region_size, diagnostics=diagnostics)
    width, height = contract["source"]["width"], contract["source"]["height"]
    proposals, medians = [], []
    for point in contract["fit_samples"]:
        x0, y0 = int(point["x"]) - region_size // 2, int(point["y"]) - region_size // 2
        x1, y1 = x0 + region_size, y0 + region_size
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            continue
        rect = [x0, y0, x1, y1]
        if any(x0 < r["rect"][2] and x1 > r["rect"][0] and
               rect[1] < r["rect"][3] and rect[3] > r["rect"][1] for r in proposals):
            continue
        pixels = np.asarray(raw[:, height - y1:height - y0, x0:x1], dtype=float) * scale + zero
        if not np.isfinite(pixels).all():
            raise ValueError("Non-finite background region data")
        if np.any((pixels <= 0) | (pixels >= 1)):
            continue
        proposals.append({"id": point["id"], "role": "background", "rect": rect})
        medians.append(np.median(pixels, axis=(1, 2)))
    if medians:
        levels = np.asarray(medians)
        center = np.median(levels, axis=0)
        spread = np.maximum(1e-7, 1.4826 * np.median(np.abs(levels - center), axis=0))
        keep = np.all(np.abs(levels - center) <= 2.5 * spread, axis=1)
        proposals = [region for region, survives in zip(proposals, keep) if survives]
    if len(proposals) < 6:
        raise ValueError("Fewer than six RGB background regions survive exclusion for independent neutral validation")
    if sha256_file(source_path) != contract["source"]["sha256"]:
        raise ValueError("Scientific source changed during region sampling")
    if diagnostics is not None:
        diagnostics.update(region_count=len(proposals), region_size=region_size)
    regions = {"schema": "starun-siril.regions.v1",
               "reference": {k: contract["source"][k] for k in ("sha256", "width", "height")},
               "regions": proposals}
    if diagnostics is not None:
        from deep_sky_siril_neutral import propose_partition
        diagnostics['background_partition'] = propose_partition(regions)
        diagnostics['partition_is_proposal'] = True
    return regions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate automated, hash-bound background sample proposals.")
    parser.add_argument("--source", required=True, help="Input scientific FITS or XISF.")
    parser.add_argument("--output", required=True, help="Destination sample contract or region contract JSON.")
    parser.add_argument("--exclude-regions", help="Frozen held-out regions.v1 contract, excluded with 25-pixel padding.")
    parser.add_argument("--preview", help="Optional same-size review preview; samples always read scientific pixels.")
    quantity = parser.add_mutually_exclusive_group()
    quantity.add_argument("--samples", type=int, help="Target total sample count (12..256; limited plane: 6..256); regions default to 36.")
    quantity.add_argument("--samples-per-line", type=int, help="Candidate columns (4..64, default: 20); not valid with --regions.")
    parser.add_argument("--report", help="Independent sampling diagnostics JSON, outside the strict contract.")
    parser.add_argument('--background-layout', choices=('standard', 'limited_plane_v1'), default='standard',
                        help='Stage 3 layout; executable limited-plane samples require --exclude-regions.')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--regions", action="store_true", help="Scientific RGB neutralization regions; preview and per-line density forbidden.")
    modes.add_argument('--validation-regions', action='store_true', help='Source-only 75x75 held-out ROI proposals; visual confirmation required.')
    parser.add_argument("--region-size", type=int, default=16, help="Neutralization ROI size (default: 16, range: 8..64).")
    parser.add_argument("--target-type", default="general", choices=["general", "emission_nebula", "diffuse_nebula", "galaxy", "cluster"])
    return parser


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = Path(args.source).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    preview = Path(args.preview).expanduser().resolve() if args.preview else None
    report_path = Path(args.report).expanduser().resolve() if args.report else None
    excluded = Path(args.exclude_regions).expanduser().resolve() if args.exclude_regions else None
    if output == excluded or report_path is not None and report_path == excluded:
        raise ValueError("Output and report must not overwrite validation regions")
    if output in (source, preview) or report_path is not None and report_path in (source, preview, output):
        raise ValueError("Output and report must be distinct from each other and source images")
    report: dict[str, Any] = {"schema": REPORT_SCHEMA, "status": "failed", "reasons": []}
    try:
        if args.validation_regions:
            if excluded is not None or preview is not None or args.samples is not None or args.samples_per_line is not None or args.region_size != 16:
                raise ValueError('Validation proposals use fixed source-only geometry; sampling/neutralization options are forbidden')
            contract = propose_validation_regions(source, target_type=args.target_type, diagnostics=report,
                                                   background_layout=args.background_layout)
        elif args.regions:
            if args.background_layout != 'standard':
                raise ValueError('Neutralization regions do not accept a background layout override')
            if excluded is not None:
                raise ValueError("Neutralization regions do not accept --exclude-regions")
            if preview is not None:
                raise ValueError("Background regions must be sampled from scientific FITS, not a preview")
            if args.samples_per_line is not None:
                raise ValueError("Background regions do not accept per-line density")
            contract = generate_background_regions(source, target_count=args.samples if args.samples is not None else 36,
                                                   target_type=args.target_type, region_size=args.region_size, diagnostics=report)
        else:
            contract = generate_background_samples(source, preview_path=preview, target_count=args.samples,
                                                   samples_per_line=args.samples_per_line, target_type=args.target_type,
                                                   exclude_regions_path=excluded, diagnostics=report,
                                                   background_layout=args.background_layout)
    except Exception as error:
        report.update(status="failed", reasons=report.get("reasons") or [str(error)])
        if report_path:
            _write_json(report_path, report)
        raise
    _write_json(output, contract)
    if report_path:
        _write_json(report_path, report)
    count = len(contract['proposals'] if args.validation_regions else contract["regions"] if args.regions else contract["fit_samples"])
    kind = 'region proposals' if args.validation_regions else 'regions' if args.regions else 'samples'
    print(f"Generated {count} background {kind} into {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
