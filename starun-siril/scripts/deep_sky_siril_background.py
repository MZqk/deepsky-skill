#!/usr/bin/env python3
"""Read-only held-out background evidence; never write scientific pixels."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from deep_sky_siril_artifacts import read_fits_pixels, read_scientific_pixels
from deep_sky_siril_contract import sha256_file
from siril_manual_bundle import strict_json_bytes

BLOCK_SIZE = 25
BOOTSTRAP_REPLICATES = 2048
# Keep the legacy helper default; sessions pass their frozen policy explicitly.
POLICY = 'heldout_material_gate_v4'
REVIEW_POLICY = 'heldout_review_gate_v5'
SCIENTIFIC_POLICIES = frozenset({POLICY, REVIEW_POLICY})
SAMPLING_POLICY = 'protected_regions_v1'
LAYOUTS = ('standard', 'limited_plane_v1')


def validate_layout(layout):
    if layout not in LAYOUTS:
        raise ValueError('Unsupported background layout')
    return layout


def plane_geometry(points, width, height):
    import numpy as np
    xy = (np.asarray(points, dtype=float).reshape(-1, 2) - [.04*width, .04*height]) / [.92*width, .92*height]
    matrix = np.column_stack((np.ones(len(xy)), xy))
    rank = int(np.linalg.matrix_rank(matrix)) if len(xy) else 0
    condition = float(np.linalg.cond(matrix)) if rank == 3 else None
    return {'rank': rank, 'condition_number': condition if condition is not None and np.isfinite(condition) else None}


def intersects(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def sample_rect(x, y, size=BLOCK_SIZE):
    # Siril's default sample uses integer centers and an odd 25-pixel footprint.
    # Conservatively cover fractional coordinates rather than assuming rounding.
    import math
    half = size // 2
    return (math.floor(x) - half, math.floor(y) - half,
            math.ceil(x) - half + size, math.ceil(y) - half + size)


def _protected_regions(regions, sampling_policy):
    if sampling_policy not in (None, SAMPLING_POLICY):
        raise ValueError('Unsupported background sampling policy')
    return [r for r in regions['regions'] if r['role'] != 'background'] if sampling_policy else []


def load_regions(path: Path, source: Path, *, scientific=False, sampling_policy=None, layout='standard'):
    """Validate the shared regions.v1 contract and the held-out layout."""
    limited = validate_layout(layout) == 'limited_plane_v1'
    raw = path.read_bytes()
    doc = strict_json_bytes(raw, document='background validation regions')
    if not isinstance(doc, dict) or set(doc) != {'schema', 'reference', 'regions'} or doc['schema'] != 'starun-siril.regions.v1':
        raise ValueError('Invalid background region contract fields or schema')
    ref = doc['reference']
    if not isinstance(ref, dict) or set(ref) != {'sha256', 'width', 'height'}:
        raise ValueError('Invalid background region reference')
    if not isinstance(ref['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', ref['sha256']):
        raise ValueError('Invalid background region reference SHA-256')
    w, h = ref['width'], ref['height']
    if any(type(n) is not int or n <= 0 for n in (w, h)):
        raise ValueError('Background reference dimensions must be positive integers')
    if sha256_file(source) != ref['sha256']:
        raise ValueError('Background region parent SHA-256 changed')
    if not scientific and source.suffix.lower() not in {'.fit', '.fits', '.fts'}:
        raise ValueError('Background validation requires uncompressed mono/RGB FITS; no display fallback')
    arr = read_scientific_pixels(source)[0] if scientific else read_fits_pixels(source)[0]
    if arr.shape[1:] != (h, w) or arr.shape[0] not in (1, 3):
        raise ValueError('Background region parent geometry changed')
    regions = doc['regions']
    if not isinstance(regions, list) or not 1 <= len(regions) <= 64:
        raise ValueError('Declare 1..64 explicit regions')
    seen, sky = set(), []
    for r in regions:
        if not isinstance(r, dict) or set(r) != {'id', 'role', 'rect'}:
            raise ValueError('Invalid background region fields')
        name, role, rect = r['id'], r['role'], r['rect']
        if not isinstance(name, str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name) or name in seen:
            raise ValueError('Region IDs must be unique safe identifiers')
        seen.add(name)
        if not isinstance(role, str) or role not in {'background', 'core', 'faint', 'stars'}:
            raise ValueError('Invalid region role')
        if not isinstance(rect, list) or len(rect) != 4 or any(type(n) is not int for n in rect):
            raise ValueError('Region rectangle requires four integers')
        x0, y0, x1, y1 = rect
        if not 0 <= x0 < x1 <= w or not 0 <= y0 < y1 <= h:
            raise ValueError('Region rectangle outside the reference')
        if role == 'background':
            size = 50 if limited else 75
            if x1 - x0 < size or y1 - y0 < size:
                raise ValueError(f'Background ROI must be at least {size}x{size}')
            if any(intersects(rect, s['rect']) for s in sky):
                raise ValueError('Background validation ROIs overlap')
            sky.append(r)
    protected = _protected_regions(doc, sampling_policy)
    for r in sky:
        for exclusion in protected:
            if intersects(r['rect'], exclusion['rect']):
                raise ValueError(f"Background ROI {r['id']} intersects protected region {exclusion['id']} ({exclusion['role']})")
    centers = [((r['rect'][0] + r['rect'][2]) / 2, (r['rect'][1] + r['rect'][3]) / 2) for r in sky]
    thirds = [sum(i / 3 <= (y - .04*h) / (.92*h) < (i + 1) / 3 for _, y in centers) for i in range(3)]
    if limited and len(sky) < 3:
        raise ValueError('Limited plane validation needs at least three ROIs')
    if not limited and (len(sky) < 6 or min(thirds) < 2):
        raise ValueError('Background validation needs at least six ROIs, two per horizontal third')
    if any(max(c[i] for c in centers) - min(c[i] for c in centers) < .60 * .92 * extent
           for i, extent in enumerate((w, h))):
        raise ValueError('Background ROI centers must span 60% of the usable width and height')
    if limited:
        geometry = plane_geometry(centers, w, h)
        if geometry['rank'] != 3 or geometry['condition_number'] is None or geometry['condition_number'] > 100:
            raise ValueError('Limited plane validation needs rank 3 and condition number <= 100')
    return doc, hashlib.sha256(raw).hexdigest()


def validate_samples(contract, regions, *, sampling_policy=None, layout='standard'):
    """Enforce coverage, disjoint fit footprints, and 25-pixel hold-out padding."""
    from siril_auto_samples import assess_sample_coverage
    ref = regions['reference']
    if any(contract.get('source', {}).get(k) != ref[k] for k in ('sha256', 'width', 'height')):
        raise ValueError('Fit and validation references differ')
    points = contract.get('fit_samples', [])
    coverage = assess_sample_coverage(points, ref['width'], ref['height'], layout=layout)
    if coverage['reasons']:
        raise ValueError('Background sampling coverage insufficient: ' + '; '.join(coverage['reasons']))
    padded = [(r['rect'][0]-25, r['rect'][1]-25, r['rect'][2]+25, r['rect'][3]+25)
              for r in regions['regions'] if r['role'] == 'background']
    protected = _protected_regions(regions, sampling_policy)
    boxes = []
    for p in points:
        box = sample_rect(p['x'], p['y'])
        if box[0] < 0 or box[1] < 0 or box[2] > ref['width'] or box[3] > ref['height']:
            raise ValueError('Background sample footprint outside the image')
        for exclusion in protected:
            if intersects(box, exclusion['rect']):
                raise ValueError(f"Background sample {p['id']} intersects protected region {exclusion['id']} ({exclusion['role']})")
        if any(intersects(box, prior) for prior in boxes):
            raise ValueError('Background sample footprints overlap')
        if any(intersects(box, roi) for roi in padded):
            raise ValueError('Background sample contaminates a padded validation ROI')
        boxes.append(box)
    return coverage


def measure_background(parent: Path, candidate: Path, regions_path: Path, *, regions_sha256: str, policy=POLICY, sampling_policy=None,
                       layout='standard', layout_reason=None):
    """Pair block resampling across native channels and pixel-derived luminance."""
    import numpy as np
    report = {'schema': 'starun-siril.background-validation.v2', 'policy': policy,
              'status': 'uncertain', 'decision': 'blocked', 'reasons': [],
              'warnings': [], 'benefit_dimensions': [], 'regions_sha256': regions_sha256,
              'estimator': 'median_of_nonoverlapping_25x25_block_medians',
              'spatial_span': 'p90_minus_p10_across_roi_estimates',
              'uncertainty': {'method': 'paired_roi_block_bootstrap', 'generator': 'PCG64',
                              'seed': 0, 'replicates': BOOTSTRAP_REPLICATES,
                              'threshold': 'max(3 * std(improvement), 1e-6)',
                              'interpretation': 'Engineering heuristic, not calibrated physical confidence'},
              'visual_review_required': True}
    hashes = {}
    report['images'] = hashes
    if sampling_policy is not None:
        report['sampling_policy'] = sampling_policy
    try:
        validate_layout(layout)
        if policy != REVIEW_POLICY and (layout != 'standard' or layout_reason is not None):
            raise ValueError('Background layout overrides require the v5 review policy')
        if policy == REVIEW_POLICY:
            if layout_reason is not None and (not isinstance(layout_reason, str) or not layout_reason.strip()):
                raise ValueError('Background layout_reason must be nonempty text')
            if layout == 'limited_plane_v1' and layout_reason is None:
                raise ValueError('Limited plane layout requires layout_reason')
            report['layout'] = layout
            if layout_reason is not None:
                report['layout_reason'] = layout_reason
        for k, path in (('parent', parent), ('candidate', candidate)):
            hashes[k] = sha256_file(path)
        if sha256_file(regions_path) != regions_sha256:
            raise ValueError('Background region contract changed during execution')
        doc, digest = load_regions(regions_path, parent, scientific=policy in SCIENTIFIC_POLICIES,
                                   sampling_policy=sampling_policy, layout=layout)
        if digest != regions_sha256:
            raise ValueError('Background region contract changed during execution')
        if policy == REVIEW_POLICY:
            sky = [r for r in doc['regions'] if r['role'] == 'background']
            centers = [((r['rect'][0]+r['rect'][2])/2, (r['rect'][1]+r['rect'][3])/2) for r in sky]
            spans = np.ptp(centers, axis=0) / [.92*doc['reference']['width'], .92*doc['reference']['height']]
            report['validation_coverage'] = {'count': len(sky), 'x_span_fraction': float(spans[0]),
                'y_span_fraction': float(spans[1]),
                'complete_block_counts': [((r['rect'][2]-r['rect'][0])//25)*((r['rect'][3]-r['rect'][1])//25) for r in sky]}
            if layout == 'limited_plane_v1':
                report['validation_coverage']['plane_geometry'] = plane_geometry(
                    centers, doc['reference']['width'], doc['reference']['height'])
        if candidate.suffix.lower() not in {'.fit', '.fits', '.fts'}:
            raise ValueError('Background candidate requires uncompressed mono/RGB FITS; no display fallback')
        if policy in SCIENTIFIC_POLICIES:
            decoded = [read_scientific_pixels(p) for p in (parent, candidate)]
            report['scientific_sources'] = dict(zip(('parent', 'candidate'), [m for _, m in decoded]))
            arrays = [(a, 1, 0) for a, _ in decoded]
        else:
            arrays = [read_fits_pixels(p) for p in (parent, candidate)]
        if arrays[0][0].shape != arrays[1][0].shape:
            raise ValueError('Background candidate geometry or channel count changed')
        channels, h, w = arrays[0][0].shape
        names = ['luminance', *(['mono'] if channels == 1 else ['R', 'G', 'B'])]
        if channels == 3:
            names.extend(['R_minus_G', 'B_minus_G'])
        report['luminance'] = 'per_pixel_Rec709_0.2126R_0.7152G_0.0722B' if channels == 3 else 'per_pixel_mono'
        # Check full science, not just held-out pixels. Never clip or normalize to a JPEG.
        clip = [np.zeros((channels, 2), dtype=np.int64) for _ in arrays]
        crossings = np.zeros(channels, dtype=np.int64)
        constant_channels = np.ones(channels, dtype=bool)
        offset_reference = None
        for row in range(0, h, 256):
            paired_rows = []
            for i, (raw, scale, zero) in enumerate(arrays):
                a = np.asarray(raw[:, row:row+256], dtype=float) * scale + zero
                if not np.isfinite(a).all():
                    raise ValueError('Background validation requires finite scientific pixels')
                clip[i][:, 0] += np.sum(a <= 0, axis=(1, 2))
                clip[i][:, 1] += np.sum(a >= 1, axis=(1, 2))
                paired_rows.append(a)
            crossings += np.sum((paired_rows[0] > 0) & (paired_rows[1] <= 0), axis=(1, 2))
            if policy == REVIEW_POLICY:
                before, after = paired_rows
                offsets = after - before
                if offset_reference is None:
                    offset_reference = offsets[:, 0, 0]
                tolerance = 2*np.maximum(np.abs(np.spacing(before.astype(np.float32))),
                                         np.abs(np.spacing(after.astype(np.float32)))).astype(float)
                constant_channels &= np.all(np.abs(offsets-offset_reference[:, None, None]) <= tolerance, axis=(1, 2))
        report['clipping_counts_shadow_highlight'] = {'parent': clip[0].tolist(), 'candidate': clip[1].tolist()}
        report['new_shadow_crossings'] = {
            'counts': crossings.tolist(), 'rates': (crossings / (w*h)).tolist(),
            'threshold': .0001, 'definition': 'parent > 0 and candidate <= 0',
            'interpretation': 'Engineering boundary protection, not proof of physical clipping'}
        if np.any(crossings / (w*h) >= .0001):
            report['reasons'].append('New scientific shadow boundary crossings reach 0.01% in a native channel')
        rng = np.random.Generator(np.random.PCG64(0))
        estimates, bootstraps, shifts, rows = [], [], [], []
        for r in doc['regions']:
            if r['role'] != 'background':
                continue
            x0, y0, x1, y1 = r['rect']
            nr, nc = (y1-y0)//25, (x1-x0)//25
            blocks = []
            for raw, scale, zero in arrays:
                pixels = np.asarray(raw[:, h-y0-nr*25:h-y0, x0:x0+nc*25], dtype=float) * scale + zero
                luma = pixels[0] if channels == 1 else np.einsum('c,cyx->yx', [0.2126, 0.7152, 0.0722], pixels)
                dimensions = np.concatenate((luma[None], pixels), axis=0)
                if channels == 3:
                    dimensions = np.concatenate((dimensions, (pixels[0]-pixels[1])[None],
                                                 (pixels[2]-pixels[1])[None]), axis=0)
                block = dimensions.reshape(len(names), nr, 25, nc, 25).transpose(0, 1, 3, 2, 4)
                blocks.append(np.median(block.reshape(len(names), nr*nc, 625), axis=2))
            paired = np.asarray(blocks)
            estimates.append(np.median(paired, axis=2))
            resampled = []
            for start in range(0, BOOTSTRAP_REPLICATES, 64):
                indices = rng.integers(0, nr*nc, size=(min(64, BOOTSTRAP_REPLICATES-start), nr*nc))
                resampled.append(np.median(paired[:, :, indices], axis=3))
            bootstraps.append(np.concatenate(resampled, axis=2))
            shifts.append(np.median(paired[1] - paired[0], axis=1))
            rows.append({'id': r['id'], 'rect': r['rect'], 'complete_block_count': nr*nc,
                         'parent': dict(zip(names, estimates[-1][0].tolist())),
                         'candidate': dict(zip(names, estimates[-1][1].tolist()))})
        report['heldout_regions'] = rows
        levels = np.stack(estimates, axis=-1)  # image, dimension, ROI
        boot = np.stack(bootstraps, axis=-1)  # image, dimension, replicate, ROI
        span = np.diff(np.quantile(levels, [.1, .9], axis=-1), axis=0)[0]
        boot_span = np.diff(np.quantile(boot, [.1, .9], axis=-1), axis=0)[0]
        delta = span[0] - span[1]
        sigma = np.std(boot_span[0] - boot_span[1], axis=1)
        thresholds = np.maximum(3*sigma, 1e-6)
        report['dimensions'] = {}
        for i, name in enumerate(names):
            offsets = np.asarray(shifts)[:, i]
            report['dimensions'][name] = {'parent_span': float(span[0, i]), 'candidate_span': float(span[1, i]),
                'improvement': float(delta[i]), 'improvement_std': float(sigma[i]), 'threshold': float(thresholds[i]),
                'constant_offset': float(np.median(offsets)),
                'offset_spatial_span': float(np.diff(np.quantile(offsets, [.1, .9]))[0]),
                'improved': bool(delta[i] > thresholds[i]), 'worsened': bool(delta[i] < -thresholds[i])}
            if delta[i] < -thresholds[i]:
                report['warnings'].append({'code': 'spatial_span_worsened', 'dimension': name,
                                          'improvement': float(delta[i]), 'threshold': float(thresholds[i])})
        benefit_names = {'luminance', 'R_minus_G', 'B_minus_G'}
        report['benefit_dimensions'] = [n for n in names if n in benefit_names and report['dimensions'][n]['improved']]
        if policy == REVIEW_POLICY:
            report['constant_offset_only'] = bool(constant_channels.all())
            report['benefit_dimensions'] = [n for n in names if n in benefit_names
                and report['dimensions'][n]['improvement'] > 0 and not report['constant_offset_only']]
            for name in report['benefit_dimensions']:
                dimension = report['dimensions'][name]
                if not dimension['improved']:
                    report['warnings'].append({'code': 'low_significance_benefit', 'dimension': name,
                        'improvement': dimension['improvement'], 'threshold': dimension['threshold']})
        report['status'] = 'failed' if report['reasons'] else 'passed'
        report['decision'] = 'blocked' if report['reasons'] else 'review_required' if report['benefit_dimensions'] else 'skip'
        if report['decision'] == 'skip':
            report['reasons'].append('No identifiable luminance or chromatic spatial benefit; keep the verified parent')
    except (ValueError, OSError, RuntimeError) as exc:
        report['reasons'].append(str(exc))
        report['status'] = 'uncertain'
    try:
        unchanged = sha256_file(regions_path) == regions_sha256 and all(
            sha256_file(p) == hashes.get(k) for k, p in (('parent', parent), ('candidate', candidate)))
    except (OSError, RuntimeError):
        unchanged = False
    if not unchanged:
        report.update(status='uncertain', decision='blocked')
        report['reasons'].append('Region contract or scientific image changed during measurement')
    return report
