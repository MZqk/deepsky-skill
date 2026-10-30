"""Read-only neutral partition, held-out benefit and paired safety evidence."""
from __future__ import annotations

import math
from pathlib import Path

from deep_sky_siril_contract import fingerprint, stable_hash

NEUTRAL_POLICY = 'heldout_benefit_safety_v1'
NATIVE_NEUTRAL_POLICY = 'scientific_native_flow_v2'
NEUTRAL_POLICIES = frozenset({NEUTRAL_POLICY, NATIVE_NEUTRAL_POLICY})
NEUTRAL_SCHEMA = 'starun-siril.neutral-validation.v1'


def _center(region, width, height):
    x0, y0, x1, y1 = region['rect']
    return ((x0 + x1) / (2 * width), (y0 + y1) / (2 * height))


def validate_partition(regions, partition):
    """Keep regions.v1 intact; bind every sky ID to exactly one purpose."""
    if not isinstance(partition, dict) or set(partition) != {'fit', 'validation'}:
        raise ValueError('Neutral background_partition requires fit and validation ID lists')
    ids = []
    for role in ('fit', 'validation'):
        names = partition[role]
        if not isinstance(names, list) or len(names) < 3 or any(not isinstance(n, str) for n in names):
            raise ValueError('Neutral partition requires at least three fit and three validation regions')
        ids.extend(names)
    sky = [r for r in regions if r['role'] == 'background']
    if len(set(ids)) != len(ids) or set(ids) != {r['id'] for r in sky}:
        raise ValueError('Neutral partition must cover all and only background IDs without duplication')
    for region in sky:
        a = region['rect']
        for other in regions:
            b = other['rect']
            if region['id'] != other['id'] and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                raise ValueError('Neutral sky rectangles must not overlap sky, subject or stellar regions')
    return {role: sorted(partition[role]) for role in ('fit', 'validation')}


def propose_partition(contract):
    """Reserve geometry-only farthest-point validation proposals, never approve sky."""
    sky = sorted((r for r in contract['regions'] if r['role'] == 'background'),
                 key=lambda r: (r['rect'][1] + r['rect'][3], r['rect'][0] + r['rect'][2], r['id']))
    if len(sky) < 6:
        raise ValueError('Neutral partition requires at least six background regions')
    reference = contract['reference']
    points = {r['id']: _center(r, reference['width'], reference['height']) for r in sky}
    selected = [sky[0]['id']]
    remaining = [r['id'] for r in sky[1:]]
    count = max(3, math.ceil(len(sky) / 3))
    while len(selected) < count:
        distances = {name: min(sum((a-b)**2 for a, b in zip(points[name], points[p])) for p in selected)
                     for name in remaining}
        # remaining retains the (y, x, id) tie order.
        chosen = max(remaining, key=distances.__getitem__)
        selected.append(chosen)
        remaining.remove(chosen)
    return validate_partition(contract['regions'], {'fit': remaining, 'validation': selected})


def _coverage(regions, width, height):
    points = [_center(r, width, height) for r in regions]
    cells = [[0] * 3 for _ in range(3)]
    for x, y in points:
        cells[min(2, int(y*3))][min(2, int(x*3))] += 1
    return {'count': len(points), 'grid_top_left': cells,
            'x_span_fraction': max(p[0] for p in points)-min(p[0] for p in points),
            'y_span_fraction': max(p[1] for p in points)-min(p[1] for p in points)}


def endpoint_transitions(parent, candidate):
    """Count paired scientific crossings; float endpoints do not prove hard clipping."""
    import numpy as np
    if parent.shape != candidate.shape or parent.ndim != 2 or not parent.size:
        raise ValueError('Endpoint comparisons require matching nonempty planes')
    if not np.isfinite(parent).all() or not np.isfinite(candidate).all():
        raise ValueError('Endpoint comparisons require finite scientific pixels')
    masks, record = {}, {'pixel_count': int(parent.size)}
    for name, before, after, overshoot in (
        ('shadow', parent <= 0, candidate <= 0, -candidate),
        ('highlight', parent >= 1, candidate >= 1, candidate-1),
    ):
        new = ~before & after
        masks[name] = new
        record[name] = {'parent': int(before.sum()), 'candidate': int(after.sum()),
                        'new': int(new.sum()), 'persisting': int((before & after).sum()),
                        'recovered': int((before & ~after).sum()),
                        'new_rate': float(new.sum()/parent.size),
                        'maximum_new_overshoot': float(np.max(overshoot[new])) if new.any() else None}
    height, width = parent.shape
    tiles = []
    for y0, y1 in zip(np.linspace(0, height, 9, dtype=int)[:-1], np.linspace(0, height, 9, dtype=int)[1:]):
        for x0, x1 in zip(np.linspace(0, width, 9, dtype=int)[:-1], np.linspace(0, width, 9, dtype=int)[1:]):
            if y0 == y1 or x0 == x1:
                continue
            crop = (slice(height-y1, height-y0), slice(x0, x1))
            tiles.append({'rect': list(map(int, (x0, y0, x1, y1))),
                          'new_shadow': int(masks['shadow'][crop].sum()),
                          'new_highlight': int(masks['highlight'][crop].sum())})
    record['new_crossing_grid'] = {'shape': [8, 8], 'tiles': tiles}
    record['interpretation'] = 'Exact 0/1 crossings in scientific float data; not proof of hard clipping.'
    return record


def measure_neutral_validation(source: Path, candidate: Path, regions_path: Path, coefficients, partition,
                               *, policy=NEUTRAL_POLICY):
    """Measure fixed held-out regions and independent safety; never accept or write pixels."""
    import numpy as np
    from deep_sky_siril_artifacts import read_fits_pixels, read_scientific_pixels
    from deep_sky_siril_metrics import measure_regions

    bindings = {'parent': fingerprint(source, include_path=False),
                'candidate': fingerprint(candidate, include_path=False),
                'regions': fingerprint(regions_path, include_path=False),
                'coefficients_sha256': stable_hash(coefficients)}
    if policy not in NEUTRAL_POLICIES:
        raise ValueError('Unknown frozen neutral policy')
    scientific = policy == NATIVE_NEUTRAL_POLICY
    measured = measure_regions(candidate, source, regions_path, scientific=scientific)
    if measured['status'] != 'measured':
        raise ValueError('Neutral evidence requires finite matching scientific RGB region measurements')
    partition = validate_partition(measured['regions'], partition)
    if coefficients.get('regions') != bindings['regions'] or coefficients.get('background_partition') != partition:
        raise ValueError('Neutral coefficients differ from the bound region partition')
    rows = {r['id']: r for r in measured['regions']}
    for name in partition['fit'] + partition['validation']:
        for layer in ('parent', 'candidate'):
            channels = rows[name][layer]['channels']
            if len(channels) != 3 or any(c['finite_count'] < 64 for c in channels):
                raise ValueError('Neutral evidence requires 64 finite RGB pixels in every sky region')
    sky = [rows[name] for name in partition['validation']]
    rgb = {layer: np.asarray([[c['median'] for c in r[layer]['channels']] for r in sky])
           for layer in ('parent', 'candidate')}
    errors = {layer: np.ptp(values, axis=1) for layer, values in rgb.items()}
    delta = errors['parent'] - errors['candidate']
    benefit = {'statistic': 'range_of_ROI_RGB_medians', 'status': 'measured',
               'regions': [{'id': r['id'], 'rect': r['rect'], 'parent_rgb': rgb['parent'][i].tolist(),
                            'candidate_rgb': rgb['candidate'][i].tolist(),
                            'parent_error': float(errors['parent'][i]), 'candidate_error': float(errors['candidate'][i]),
                            'error_reduction': float(delta[i])} for i, r in enumerate(sky)],
               'summary': {layer: {'median': float(np.median(values)), 'p90': float(np.percentile(values, 90))}
                           for layer, values in errors.items()},
               'paired_reduction': {'median': float(np.median(delta)), 'p90': float(np.percentile(delta, 90))},
               'numerically_improved_fraction': float(np.mean(delta > 0)),
               'numerically_worsened_fraction': float(np.mean(delta < 0)),
               'spatial_channel_span_p90_p10': {layer: (np.percentile(values, 90, axis=0)-np.percentile(values, 10, axis=0)).tolist()
                                               for layer, values in rgb.items()},
               'interpretation': 'Confirmed sampled sky neutrality only; no calibrated significance or physical color claim.'}
    width, height = measured['reference']['width'], measured['reference']['height']
    subject = []
    for r in measured['regions']:
        if r['role'] == 'background':
            continue
        center = _center(r, width, height)
        local = min(sky, key=lambda s: (sum((a-b)**2 for a,b in zip(center, _center(s,width,height))), s['id']))
        signals = {layer: np.asarray([c['median'] for c in r[layer]['channels']]) -
                         np.asarray([c['median'] for c in local[layer]['channels']])
                   for layer in ('parent', 'candidate')}
        colors = {}
        for layer, values in signals.items():
            usable = bool(np.isfinite(values).all() and np.all(values > 1e-7))
            colors[layer] = {'status': 'measured' if usable else 'unavailable',
                             'rgb_fraction': (values/values.sum()).tolist() if usable else None,
                             'reason': None if usable else 'Nonpositive or near-zero background-subtracted signal'}
        subject.append({'id': r['id'], 'role': r['role'], 'rect': r['rect'], 'local_sky_id': local['id'],
                        'signal_rgb': {layer: values.tolist() for layer, values in signals.items()},
                        'gain_adjusted_signal_delta': (signals['candidate']-np.asarray(coefficients['gains'])*signals['parent']).tolist(),
                        'signal_chromaticity': colors})
    if scientific:
        a, parent_selection = read_scientific_pixels(source)
        b, candidate_selection = read_scientific_pixels(candidate)
        if measured['scientific_sources'] != {'parent': parent_selection, 'candidate': candidate_selection}:
            raise ValueError('Neutral scientific source selection changed during measurement')
        bindings['scientific_sources'] = measured['scientific_sources']
        sa, za, sb, zb = 1, 0, 1, 0
    else:
        a, sa, za = read_fits_pixels(source)
        b, sb, zb = read_fits_pixels(candidate)
    if a.shape != b.shape or a.shape[0] != 3:
        raise ValueError('Neutral evidence requires matching RGB geometry')
    channels = [endpoint_transitions(np.asarray(a[ch], dtype=float)*sa+za, np.asarray(b[ch], dtype=float)*sb+zb)
                for ch in range(3)]
    if bindings['parent'] != fingerprint(source, include_path=False) or bindings['candidate'] != fingerprint(candidate, include_path=False) or bindings['regions'] != fingerprint(regions_path, include_path=False):
        raise ValueError('Neutral evidence changed during measurement')
    return {'schema': NEUTRAL_SCHEMA, 'policy': policy, 'status': 'measured',
            'recommended_verdict': 'uncertain', 'coordinate_frame': measured['coordinate_frame'],
            'method': 'reference_white_balance' if coefficients['white_reference'] else 'pure_additive',
            'bindings': bindings, 'background_partition': partition,
            'coverage': {role: _coverage([rows[n] for n in partition[role]], width, height) for role in partition},
            'benefit': benefit, 'safety': {'channels': channels, 'regions': subject,
                'region_status': 'measured' if subject else 'unavailable',
                'interpretation': 'Gain-adjusted signal preservation and endpoint risks do not replace actual visual review.'}}


def validate_neutral_binding(report, source, candidate, regions_path, coefficients, partition, *, policy=NEUTRAL_POLICY):
    """Recompute receipt evidence deterministically without changing legacy receipts."""
    expected = measure_neutral_validation(source, candidate, regions_path, coefficients, partition, policy=policy)
    if report != expected:
        raise ValueError('Neutral evidence differs from its bound scientific inputs')
    return report
