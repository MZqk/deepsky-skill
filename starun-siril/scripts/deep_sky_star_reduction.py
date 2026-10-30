#!/usr/bin/env python3
"""Agent-proposed display-domain recomposition; legacy strength estimates are reference-only."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from io import BytesIO
import time
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np

from deep_sky_siril_contract import (ContractError, SKILL_ROOT, canonical_json, fingerprint, fingerprint_matches, load_json,
                                      protocol_reference_bindings, session_path as bound_path, _fsync_directory,
    RUN_ID_PATTERN, atomic_write_json, sha256_file, EXECUTION_POLICY)
from deep_sky_siril_stretch import bound_file, record
from deep_sky_siril_artifacts import read_scientific_pixels, read_scientific_header, read_fits_header
from deep_sky_siril_metrics import measure_regions


POLICY = 'agent_proposal_v1'
REQUEST = 'starun-siril.recomposition-request.v2'
LEGACY_REQUEST = 'starun-siril.recomposition-request.v1'
CONTOUR_POLICY = 'local_peak_mtf_v1'
PREPARED = 'starun-siril.recomposition-prepared.v1'
GEOMETRY_POLICY = 'fixed_catalog_profiles_v1'
COLOR_REVIEW_POLICY = 'stellar_color_assessment_v2'
STELLAR_CHECKS = ('core', 'color', 'halo', 'endpoints')
PRE_GEOMETRY_HELPER = {'sha256': '5d0efd3786d7f929bf35415ebbb529aaa2d333498e92da229d10ff53d24bc626', 'size': 49759}


def geometry_enabled(payload):
    label = payload.get('execution_policy', {}).get('recomposition_geometry')
    if label not in (None, GEOMETRY_POLICY):
        fail('Unsupported recomposition geometry policy')
    return label == GEOMETRY_POLICY


def stellar_color_enabled(payload):
    return payload.get('execution_policy', {}).get('recomposition_review') == COLOR_REVIEW_POLICY


def _color_sample(values):
    from deep_sky_siril_color import rgb_statistics
    values = np.asarray(values, dtype=float)
    result = {'pixels': values.shape[1], 'rgb_median': np.median(values, axis=1).tolist(),
              'signed_flux': values.sum(axis=1).tolist()}
    if len(values) != 3:
        return result
    peak, floor = values.max(axis=0), values.min(axis=0)
    valid = (floor >= 0) & (peak > 0)
    chromatic = valid & (peak-floor > 1e-12)
    saturation, hue = rgb_statistics(values)
    weights = (peak-floor)[chromatic]
    vector = np.sum(weights*np.exp(1j*np.radians(hue[chromatic])))
    measurable = bool(weights.size and abs(vector) > 1e-12*weights.sum())
    result.update(nonnegative_pixels=int(valid.sum()), chromatic_pixels=int(chromatic.sum()),
                  saturation_median=float(np.median(saturation[valid])) if valid.any() else None,
                  hue_circular_mean_deg=float(np.degrees(np.angle(vector)) % 360) if measurable else None,
                  hue_status='measured' if measurable else 'not_measurable',
                  hue_reason=None if measurable else 'No valid chromatic signal or cancelling directions')
    return result


def _endpoint_positions(mask, x0, y0):
    points = np.argwhere(mask)
    return {'count': len(points), 'positions': [[int(x+x0), int(y+y0)] for y, x in points[:32]],
            'positions_truncated': len(points) > 32,
            'bounds': [int(points[:,1].min()+x0), int(points[:,0].min()+y0),
                       int(points[:,1].max()+x0+1), int(points[:,0].max()+y0+1)] if len(points) else None}


def stellar_color_diagnostics(images, table, regions, geometry):
    """Read-only display color evidence; background subtraction is diagnostic only."""
    subject = images['subject']
    if any(v.shape != subject.shape or not np.isfinite(v).all() for v in images.values()):
        fail('Stellar color requires finite aligned scientific pixels')
    height = subject.shape[1]
    profiles = {r['region_id']: r for r in geometry['regions']}
    result = {'policy': 'fixed_catalog_color_v1', 'status': 'measured' if len(subject) == 3 else 'not_applicable',
              'coordinate_frame': 'preview_top_left_zero_based_half_open',
              'hue_weight': 'nonnegative_net_RGB_positive_peak_chroma_above_1e-12_weighted_by_chroma',
              'background': 'common_subject_RGB_median_in_outer_20_percent_radius',
              'requires_visual_review': True, 'regions': [],
              'limitations': 'Display colors are not physical stellar colors; subject structure, noise and separation leakage can bias background-subtracted measurements'}
    for region in (r for r in regions if r['role'] == 'stars'):
        x0,y0,x1,y1 = region['rect']
        crops = {key: v[:,height-y1:height-y0,x0:x1][:,::-1].astype(float) for key,v in images.items()}
        row = {'region_id': region['id'], 'raw': {}, 'zones': {}, 'status': 'not_measurable', 'reasons': []}
        result['regions'].append(row)
        for key,crop in crops.items():
            raw = _color_sample(crop.reshape(len(crop),-1))
            raw['endpoints'] = [{kind: _endpoint_positions(test, x0,y0) for kind,test in
                                (('nonpositive', plane <= 0), ('highlight', plane >= 1))} for plane in crop]
            raw['new_endpoints'] = {ref: [{kind: _endpoint_positions(test,x0,y0) for kind,test in
                (('nonpositive', (plane <= 0) & (before > 0)), ('highlight', (plane >= 1) & (before < 1)))}
                for plane,before in zip(crop,reference)] for ref,reference in crops.items() if ref != key}
            row['raw'][key] = raw
        profile = profiles.get(region['id'], {})
        if len(subject) != 3:
            row.update(status='not_applicable', reasons=['Monochrome source'])
            continue
        if 'profiles' not in profile:
            row['reasons'] = profile.get('reasons', ['No reliable fixed catalog aperture'])
            continue
        cx,cy = profile['native_position']
        radius,aperture = profile['context_radius_px'],profile['aperture_radius_px']
        star = next(r for r in table['rows'] if r['line_number'] == profile['catalog_line'])
        core = max(.5,(star['fwhmx']+star['fwhmy'])/4)
        yy,xx = np.mgrid[y0:y1,x0:x1]
        distance = np.hypot(xx-cx,yy-cy)
        annulus = (distance >= aperture) & (distance < radius)
        background = np.median(crops['subject'][:,annulus],axis=1)
        row['background'] = {'rgb_median': background.tolist(), 'pixels': int(annulus.sum()),
            'rgb_mad': np.median(np.abs(crops['subject'][:,annulus]-background[:,None]),axis=1).tolist()}
        row['aperture'] = {'native_position': [cx,cy], 'core_radius_px': core, 'outer_radius_px': aperture}
        if core >= aperture:
            row['reasons'].append('Fixed core is truncated by the available aperture')
            continue
        for zone,mask in (('core',distance <= core), ('halo',(distance > core) & (distance < aperture))):
            row['zones'][zone] = {key: {'raw': _color_sample(crop[:,mask]),
                'sky_subtracted': _color_sample(crop[:,mask]-background[:,None])} for key,crop in crops.items()} if mask.any() else {
                    'status': 'not_measurable', 'reason': 'Empty fixed aperture'}
        row['status'] = 'measured' if all('status' not in v for v in row['zones'].values()) else 'partial'
    return result


def catalog_recipe(session, payload, bindings, path):
    original = science_path(session, payload, bindings['original'])
    header = read_scientific_header(original)
    channels = header.get('NAXIS3', 1)
    if channels not in (1, 3):
        fail('Recomposition catalog requires a mono or RGB scientific source')
    layer = 1 if channels == 3 else 0
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    return ['load '+q(original), 'setfindstar reset',
            f'findstar -layer={layer} -maxstars=500 '+q('-out='+str(path)), 'close'], layer


def catalog_binding(session, payload, bindings, path):
    from deep_sky_siril_psf import parse_native_stars, eligible_stars
    table = parse_native_stars(path, spatial=True)
    _, layer = catalog_recipe(session, payload, bindings, path)
    header = read_scientific_header(science_path(session, payload, bindings['original']))
    shape = (header.get('NAXIS3', 1), header['NAXIS2'], header['NAXIS1'])
    if (table['status'] != 'measured' or table['settings'].get('layer') != layer
        or table['settings'].get('max_stars') != 500 or len(table['rows']) > 500
        or any(r['layer'] != layer or not 0 <= r['xpos'] < shape[2]
               or not 0 <= r['ypos'] < shape[1] for r in table['rows'])):
        fail('Native recomposition catalog has unsupported settings, coordinates or geometry')
    return {'table': record(session, path), 'source': bindings['original'],
            'geometry': {'width': shape[2], 'height': shape[1], 'channels': shape[0]},
            'detection': {'setfindstar': ['reset'], 'layer': layer, 'maxstars': 500},
            'native_settings': table['settings'],
            'coordinate_frame': 'native_siril_top_left_zero_based_pixel_centres',
            'coverage': {'detected': len(table['rows']), 'invalid': len(table['invalid_rows']),
                         'duplicates': table['duplicate_count'],
                         'saturated': sum(r['has_saturated'] for r in table['rows']),
                         'eligible': len(eligible_stars(table)), 'at_catalog_limit': len(table['rows']) == 500}}


def _luminance(values):
    return values[0] if len(values) == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], values)


def _display_profile(values, distance, aperture, background):
    inside = distance < aperture
    net, radii = values[inside]-background, distance[inside]
    result = {'fwhm_px': None, 'r90_px': None, 'radii_px': [], 'profile': [], 'reasons': [],
              'aperture_pixels': int(inside.sum())}
    if not len(net):
        result['reasons'].append('Empty measurement aperture')
        return result
    result.update(peak=float(net.max()), positive_flux=float(np.maximum(net, 0).sum()),
                  negative_flux=float(-np.minimum(net, 0).sum()), signed_flux=float(net.sum()))
    for start in range(math.ceil(aperture)):
        ring = (radii >= start) & (radii < min(start+1, aperture))
        result['radii_px'].append(float(np.median(radii[ring])) if ring.any() else None)
        result['profile'].append(float(np.median(net[ring])) if ring.any() else None)
    p, x = result['profile'], result['radii_px']
    if len(p) < 2 or any(v is None for v in p) or result['peak'] <= 0:
        result['reasons'].append('Insufficient positive radial context; empty rings are not interpolated')
    else:
        half = result['peak']/2
        crossings = [(i, i+1) for i in range(len(p)-1) if (p[i] >= half) != (p[i+1] >= half)]
        if p[0] >= half and len(crossings) == 1 and p[-1] < half:
            i, j = crossings[0]
            result['fwhm_px'] = 2*(x[i]+(half-p[i])*(x[j]-x[i])/(p[j]-p[i]))
        else:
            result['reasons'].append('Half-height crossing is missing, truncated or ambiguous')
    if result['positive_flux'] > 0:
        positive = net > 0
        order = np.argsort(radii[positive], kind='stable')
        unique, first = np.unique(radii[positive][order], return_index=True)
        cumulative = np.cumsum(np.add.reduceat(net[positive][order], first))
        result['r90_px'] = float(np.interp(.9*result['positive_flux'], np.r_[0., cumulative], np.r_[0., unique]))
    else:
        result['reasons'].append('No positive background-subtracted aperture flux')
    return result


def geometry_profiles(parent, control, candidate, table, regions, *, color=False):
    """Measure fixed catalog positions; display profiles are not optical PSF fits."""
    from deep_sky_siril_psf import eligible_stars
    if (parent.shape != control.shape or parent.shape != candidate.shape
        or not all(np.isfinite(v).all() for v in (parent, control, candidate))):
        fail('Geometry diagnostics require finite aligned scientific pixels')
    height, width = parent.shape[1:]
    planes = [_luminance(v) for v in (parent, control, candidate)]
    eligible = {r['line_number'] for r in eligible_stars(table)}
    result = {'policy': GEOMETRY_POLICY, 'units': 'px', 'catalog_status': table['status'],
              'matching': 'fixed_linear_full_catalog_no_candidate_redetection',
              'coordinate_conversion': 'x_array=x_native; y_array=height-1-y_native',
              'display_profile': 'circular_1px_annular_median_with_linear_half_height_crossing',
              'r90_definition': '90_percent_positive_background_subtracted_flux_inside_fixed_finite_aperture',
              'background': 'common_subject_median_in_outer_20_percent_radius',
              'regions': [], 'requires_visual_review': True,
              'limitations': 'Finite-aperture display profiles are not optical PSFs or total halo radii; a structured or residual-bearing subject can bias measurements; catalog coverage is incomplete and isolation requires visual review'}
    for region in (r for r in regions if r['role'] == 'stars'):
        x0, y0, x1, y1 = region['rect']
        inside = lambda r: 'xpos' in r and 'ypos' in r and x0 <= r['xpos'] < x1 and y0 <= r['ypos'] < y1
        members = [r for r in table['rows'] if inside(r)]
        row = {'region_id': region['id'], 'status': 'not_measurable', 'reasons': [],
               'catalog_count': len(members), 'comparisons': {'fwhm_ratio': None, 'r90_ratio': None}}
        result['regions'].append(row)
        if table['status'] != 'measured' or len(members) != 1:
            row['reasons'].append('Select exactly one catalog star; empty, unsupported or crowded ROI')
            continue
        star = members[0]
        row.update(star_id=star['id'], catalog_line=star['line_number'],
                   native_position=[star['xpos'], star['ypos']],
                   array_position=[star['xpos'], height-1-star['ypos']],
                   linear_psf={'fwhmx_px': star['fwhmx'], 'fwhmy_px': star['fwhmy'], 'profile': star['profile']})
        if star['line_number'] not in eligible or any(inside(r) for r in table['invalid_rows']):
            row['reasons'].append('Saturated, duplicate or locally invalid catalog sample')
            continue
        cx, cy = star['xpos'], star['ypos']
        distances = [cx-x0+.5, x1-.5-cx, cy-y0+.5, y1-.5-cy]
        radius = min(distances)
        row.update(context_radius_px=radius, aperture_radius_px=.8*radius)
        image_edges = [x0 == 0, x1 == width, y0 == 0, y1 == height]
        if radius <= 0 or any(edge and math.isclose(d, radius) for edge, d in zip(image_edges, distances)):
            row['reasons'].append('Context circle is truncated by an image edge')
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        distance = np.hypot(xx-cx, yy-cy)
        annulus = (distance >= .8*radius) & (distance < radius)
        crops = [v[height-y1:height-y0, x0:x1][::-1] for v in planes]
        if annulus.sum() < 3 or not (distance < .8*radius).any():
            row['reasons'].append('Insufficient independent background samples')
            continue
        bg = float(np.median(crops[0][annulus]))
        row['background'] = {'median': bg, 'mad': float(np.median(np.abs(crops[0][annulus]-bg))),
                             'pixels': int(annulus.sum()),
                             'subject_aperture_range': [float(crops[0][distance < .8*radius].min()),
                                                        float(crops[0][distance < .8*radius].max())]}
        others = [math.hypot(r['xpos']-cx, r['ypos']-cy) for r in table['rows'] if r['line_number'] != star['line_number']]
        row['nearest_catalog_star_px'] = min(others) if others else None
        row['profiles'] = {key: _display_profile(crop, distance, .8*radius, bg)
                           for key, crop in zip(('subject', 'full_contribution', 'candidate'), crops)}
        if color and parent.shape[0] == 3:
            from deep_sky_siril_color import rgb_statistics
            core_radius = max(.5, (star['fwhmx']+star['fwhmy'])/4)
            zones = {'core': distance <= core_radius, 'halo': (distance > core_radius) & (distance < .8*radius)}
            row['color'] = {}
            for key, image in zip(('subject','full_contribution','candidate'), (parent,control,candidate)):
                crop = image[:,height-y1:height-y0,x0:x1][:,::-1].astype(float)
                saturation,hue = rgb_statistics(crop)
                row['color'][key] = {zone: {'rgb_median': np.median(crop[:,mask],axis=1).tolist(),
                    'saturation_median': float(np.median(saturation[mask])),
                    'hue_circular_mean_deg': float(np.degrees(np.angle(np.mean(np.exp(1j*np.radians(hue[mask]))))) % 360),
                    'clipped_channel_counts': [int(np.sum(v[mask] >= 1)) for v in crop]}
                    if mask.any() else {'status': 'unavailable', 'reason': 'Empty fixed aperture'} for zone,mask in zones.items()}
        for metric in ('fwhm', 'r90'):
            old, new = (row['profiles'][key][metric+'_px'] for key in ('full_contribution', 'candidate'))
            if old is not None and new is not None and old > 0:
                row['comparisons'][metric+'_ratio'] = new/old
        row['status'] = 'measured' if all(v is not None for v in row['comparisons'].values()) else 'partial'
    result['coverage'] = {'selected_regions': len(result['regions']),
                          'measured': sum(r['status'] == 'measured' for r in result['regions']),
                          'partial': sum(r['status'] == 'partial' for r in result['regions']),
                          'not_measurable': sum(r['status'] == 'not_measurable' for r in result['regions'])}
    return result


TARGET_BASELINES = {
    "cluster": 0.90,     # Open/globular clusters: stars are the primary subject
    "galaxy": 0.65,      # Galaxies: moderate reduction to bring out outer disk/dust
    "nebula": 0.55,      # Nebulae: prominent reduction to unmask faint gas/filaments
    "unknown": 0.65,     # Default fallback
}

RECOMMENDED_MIN_STRENGTH = 0.40
RECOMMENDED_MAX_STRENGTH = 0.95
PROTOCOL_MIN_STRENGTH = 0.30
PROTOCOL_MAX_STRENGTH = 1.00


def analyze_stellar_properties_from_previews(
    full_image_path: Path,
    starless_image_path: Path
) -> Tuple[float, float]:
    """Calculate stellar flux ratio and bright bloated star pixel ratio from previews."""
    from PIL import Image
    im_full = np.array(Image.open(full_image_path), dtype=np.float32) / 255.0
    im_starless = np.array(Image.open(starless_image_path), dtype=np.float32) / 255.0

    if im_full.shape != im_starless.shape:
        # Resize starless to full dimensions if geometry slightly differs
        resized_starless = Image.open(starless_image_path).resize((im_full.shape[1], im_full.shape[0]))
        im_starless = np.array(resized_starless, dtype=np.float32) / 255.0

    sd = np.maximum(0.0, im_full - im_starless)
    star_flux_ratio = float(np.sum(sd) / (np.sum(im_full) + 1e-6))
    bright_star_ratio = float(np.mean(im_full > 0.85))

    return star_flux_ratio, bright_star_ratio


def analyze_stellar_properties_from_fits(
    full_fits_path: Path,
    starless_fits_path: Path,
    *, strict: bool = False,
) -> Tuple[float, float]:
    """Calculate stellar flux ratio and bright star ratio directly from FITS via strided sampling."""
    try:
        from deep_sky_siril_artifacts import read_fits_pixels
        raw_f, scale_f, zero_f = read_fits_pixels(full_fits_path)
        raw_s, scale_s, zero_s = read_fits_pixels(starless_fits_path)

        # Fast strided sampling (every 4th pixel along spatial axes)
        sl = (slice(None), slice(None, None, 4), slice(None, None, 4))
        f_pix = np.asarray(raw_f[sl], dtype=np.float32) * scale_f + zero_f
        s_pix = np.asarray(raw_s[sl], dtype=np.float32) * scale_s + zero_s

        sd = np.maximum(0.0, f_pix - s_pix)
        star_flux_ratio = float(np.sum(sd) / (np.sum(f_pix) + 1e-6))
        
        max_val = float(np.max(f_pix))
        if max_val > 0.5:
            bright_star_ratio = float(np.mean(f_pix > 0.85))
        else:
            # Linear domain heuristic: top 0.5% high-intensity pixels
            bright_star_ratio = float(np.mean(f_pix > np.percentile(f_pix, 99.5)))

        return star_flux_ratio, bright_star_ratio
    except Exception:
        if strict:
            raise
        return 0.20, 0.005


def compute_adaptive_star_strength(
    target_type: str,
    filter_type: str,
    star_flux_ratio: float,
    bright_star_ratio: float
) -> Tuple[float, Dict[str, Any]]:
    """Return a legacy heuristic reference; proposal generation never consumes it."""
    norm_type = (target_type or "unknown").lower()
    if "cluster" in norm_type:
        cat = "cluster"
    elif "galaxy" in norm_type:
        cat = "galaxy"
    elif "nebula" in norm_type:
        cat = "nebula"
    else:
        cat = "unknown"

    alpha_base = TARGET_BASELINES.get(cat, 0.65)

    # Flux correction: dense star fields (r > 0.25) need more reduction
    if star_flux_ratio > 0.25:
        delta_flux = -0.15 * min(1.0, (star_flux_ratio - 0.25) / 0.20)
    elif star_flux_ratio < 0.15:
        delta_flux = 0.10 * min(1.0, (0.15 - star_flux_ratio) / 0.10)
    else:
        delta_flux = 0.0

    # Bloating correction: prominent large star halos
    delta_bloat = -0.05 if bright_star_ratio > 0.01 else 0.0

    # Filter correction: narrowband filters suppress background sky, cloud envelope needs boost
    norm_filter = (filter_type or "").lower()
    is_narrowband = any(k in norm_filter for k in ("narrow", "dnb", "duo", "dual", "lp", "sii", "ha", "oiii", "sho"))
    delta_filter = -0.05 if is_narrowband else +0.05

    raw_alpha = alpha_base + delta_flux + delta_bloat + delta_filter
    clamped_alpha = round(max(RECOMMENDED_MIN_STRENGTH, min(RECOMMENDED_MAX_STRENGTH, raw_alpha)), 2)

    diagnostics = {
        "target_category": cat,
        "base_strength": alpha_base,
        "star_flux_ratio": round(star_flux_ratio, 4),
        "delta_flux": round(delta_flux, 4),
        "bright_star_ratio": round(bright_star_ratio, 4),
        "delta_bloat": round(delta_bloat, 4),
        "is_narrowband": is_narrowband,
        "delta_filter": round(delta_filter, 4),
        "raw_strength": round(raw_alpha, 4),
        "final_strength": clamped_alpha,
        "recommended_min": RECOMMENDED_MIN_STRENGTH,
        "recommended_max": RECOMMENDED_MAX_STRENGTH,
        "protocol_min": PROTOCOL_MIN_STRENGTH,
        "protocol_max": PROTOCOL_MAX_STRENGTH
    }
    return clamped_alpha, diagnostics


def inspect_session(session_path: Path) -> Dict[str, Any]:
    """Inspect an existing session to collect target metadata and run assets."""
    session = session_path.resolve()

    # 1. Read metadata from session.json
    session_file = session / "session.json"
    target_type = "unknown"
    filter_type = "broadband"
    if session_file.exists():
        with open(session_file) as f:
            sess_data = json.load(f)
        ctx = sess_data.get("context", {})
        target_type = ctx.get("target_type", "unknown")
        filter_parts = [
            ctx.get("channel_mode", ""),
            ctx.get("filter_profile", {}).get("class", ""),
            ctx.get("filter_profile", {}).get("normalized", ""),
            ctx.get("filter_profile", {}).get("raw_filter", ""),
        ]
        filter_type = " ".join(str(p) for p in filter_parts if p).strip() or "broadband"
    else:
        init_file = session / "reports" / "session-init.json"
        if init_file.exists():
            with open(init_file) as f:
                init_data = json.load(f)
            target_type = init_data.get("target_type", "unknown")
            filter_type = init_data.get("filter", "broadband")

    # 2. Discover full vs starless preview or FITS assets
    measured = False
    star_flux_ratio = 0.20
    bright_star_ratio = 0.005

    # Prioritize Stage 6 FITS if available (most accurate scientific stellar measurement)
    full_fit = session / "artifacts" / "060-full-source.fit"
    starless_fit = session / "artifacts" / "060-starless.fit"
    if full_fit.exists() and starless_fit.exists():
        try:
            star_flux_ratio, bright_star_ratio = analyze_stellar_properties_from_fits(full_fit, starless_fit)
            measured = True
        except Exception:
            pass

    if not measured:
        previews_dir = session / "previews"
        candidate_pairs = [
            (previews_dir / "100-full-hdr.jpg", previews_dir / "070-hdr.jpg"),
            (session / "artifacts" / "110-final-candidate.jpg", previews_dir / "070-hdr.jpg"),
            (previews_dir / "010-inspect-autostretch.jpg", previews_dir / "070-hdr.jpg"),
            (previews_dir / "010-inspect-autostretch.jpg", previews_dir / "070-stretch.jpg"),
            (previews_dir / "060-stars.jpg", previews_dir / "070-stretch.jpg"),
        ]
        for pf, ps in candidate_pairs:
            if pf.exists() and ps.exists():
                try:
                    star_flux_ratio, bright_star_ratio = analyze_stellar_properties_from_previews(pf, ps)
                    measured = True
                    break
                except Exception:
                    pass

    if not measured:
        # Fall back to Stage 6 FITS
        sep_run_file = session / "runs" / "060-stars.json"
        if sep_run_file.exists():
            try:
                with open(sep_run_file) as f:
                    sep_rec = json.load(f)
                orig_rel = sep_rec.get("source_path")
                starless_rel = sep_rec.get("primary_output")
                orig_path = (Path(init_data["input"]["path"]) if orig_rel == "@input" and "input" in init_data
                             else session / orig_rel if orig_rel else None)
                starless_path = session / starless_rel if starless_rel else None

                if orig_path and starless_path and orig_path.exists() and starless_path.exists():
                    star_flux_ratio, bright_star_ratio = analyze_stellar_properties_from_fits(orig_path, starless_path)
                    measured = True
            except Exception:
                pass

    strength, diag = compute_adaptive_star_strength(target_type, filter_type, star_flux_ratio, bright_star_ratio)
    diag["measured_from_source"] = measured

    return {
        "session": str(session),
        "strength": strength,
        "diagnostics": diag
    }


def _generation_context(session_value, source):
    """Resolve the accepted branch and capture all generation evidence."""
    import deep_sky_siril_core as core
    import deep_sky_siril_session as session_state
    from deep_sky_siril_processing import _recomposition_context, source_state

    session, frozen, manifest = session_state.load_session(session_value)
    if not session_state.execution_policy_is_current(frozen):
        raise ContractError('legacy_session_read_only', 'Initialize a new session for Stage 9 generation')
    if manifest.get('finalization'):
        raise ContractError('session_finalized', 'Finalized sessions cannot generate new Stage 9 assets')
    payload = session_state.effective_session_payload(session, frozen, manifest)
    if payload['execution_policy'].get('recomposition_parameters') != POLICY:
        fail('New recomposition requires the frozen Agent-proposal policy')
    if payload['context'].get('stars') == 'standalone-starless':
        fail('Standalone-starless intent skips recomposition')
    try:
        source = bound_path(session, source, must_exist=True, allowed_roots=('artifacts',))
    except OSError as exc:
        raise ContractError('artifact_missing', f'Stage 9 source is missing or unreadable: {source}') from exc
    observed = {}

    def capture(path):
        binding = fingerprint(path, include_path=False)
        if path in observed and observed[path] != binding:
            raise ContractError('source_unbound', f'Stage 9 material changed during generation: {path}')
        observed[path] = binding

    runtime = load_json(session / 'reports/tool-probe.json')['tools']['siril_cli']
    if not runtime.get('compatible') or not fingerprint_matches(runtime.get('fingerprint')):
        raise ContractError('runtime_dependency_missing', 'Frozen Siril runtime changed or is unavailable')
    capture(Path(runtime['fingerprint']['path']))

    def load_run(name):
        path, receipt = core._load_run(session, name)
        capture(path)
        bound = [row for row in manifest['runs'] if row.get('id') == name]
        if len(bound) != 1 or observed[path]['sha256'] != bound[0]['receipt_sha256']:
            raise ContractError('source_unbound', f'Stage 9 receipt changed during generation: {path}')
        for record in receipt['outputs']:
            output = bound_path(session, record['path'], must_exist=True)
            capture(output)
            if observed[output] != {key: record[key] for key in ('sha256', 'size')}:
                raise ContractError('source_unbound', f'Stage 9 output changed during generation: {output}')
        return path, receipt

    def review_run(name):
        _, binding = core._accepted_review(session, name)
        path = bound_path(session, binding['path'], must_exist=True, allowed_roots=('reviews',))
        capture(path)
        if observed[path]['sha256'] != binding['sha256']:
            raise ContractError('source_unbound', f'Stage 9 review changed during generation: {path}')

    try:
        state = source_state(session, payload, source)
    except OSError as exc:
        raise ContractError('source_unbound', f'Stage 9 source evidence is missing: {source}') from exc
    stretches = [r for r in state.get('lineage', []) if r['protocol'] == 'stretch']
    if not state.get('separation_run') or not stretches:
        raise ContractError('source_unbound', 'Stage 9 requires a verified starless stretch ancestor')
    _, sep_receipt, _, stretch_receipt, original_input, starless_input = _recomposition_context(
        session, payload, source, state['separation_run'], stretches[-1]['id'], load_run, review_run)
    for path in (source, original_input, starless_input):
        capture(path)
    full = bound_path(session, sep_receipt['star_separation']['full_source'], must_exist=True, allowed_roots=('artifacts',))
    capture(full)
    load_run(state['run']['id'])
    bindings = {'source': record(session, source), 'original': science_record(session, original_input, payload),
                'starless': record(session, starless_input), 'separation': record(session, session/'runs'/(sep_receipt['id']+'.json')),
                'stretch': record(session, session/'runs'/(stretch_receipt['id']+'.json')),
                'parent_run': record(session, session/'runs'/(state['run']['id']+'.json')),
                'reviews': [record(session, path) for path in sorted(observed) if path.parent == session/'reviews'],
                'runtime': runtime, 'helper': fingerprint(Path(__file__), include_path=False)}
    if geometry_enabled(payload):
        from deep_sky_siril_stretch_v2 import implementation
        bindings['implementation'] = implementation(session)
        for name in bindings['implementation']:
            capture(SKILL_ROOT/'scripts'/name)
    return session, payload, manifest, bindings, stretch_receipt, observed


def fail(message):
    raise ContractError('recomposition_invalid', message)


def science_record(session, path, payload):
    return {'path': '@input' if path == Path(payload['input']['path']) else str(path.relative_to(session)),
            **fingerprint(path, include_path=False)}


def science_path(session, payload, binding):
    if binding.get('path') == '@input':
        path = Path(payload['input']['path'])
        if fingerprint(path, include_path=False) != {k: binding[k] for k in ('sha256', 'size')}:
            fail('Frozen input changed')
        return path
    return bound_file(session, binding, ('artifacts', 'reports', 'runs', 'reviews', 'previews'))


def valid_text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def check_run_id(run_id):
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        fail('A unique explicit run ID is required')


def contour_parameters(value, shape):
    if (not isinstance(value, dict) or set(value) != {'amount', 'radius_px'}
        or type(value['amount']) not in (int, float) or not math.isfinite(value['amount'])
        or not 0 <= value['amount'] <= 1 or type(value['radius_px']) is not int
        or value['radius_px'] < 1 or 2*value['radius_px']+1 > min(shape[-2:])):
        fail('Contour requires finite amount in [0, 1] and a positive integer radius fitting the image')
    return value if value['amount'] else None


def contour_weights(stars, parameters):
    """Return a shared auxiliary weight; scientific pixels are multiplied by Siril."""
    stars = np.asarray(stars)
    if stars.ndim != 3 or len(stars) not in (1, 3) or not np.isfinite(stars).all():
        fail('Contour weights require finite mono/RGB scientific pixels')
    active = contour_parameters(parameters, stars.shape)
    if active is None:
        return np.ones(stars.shape[1:], dtype=np.float32)
    lum = _luminance(stars.astype(float))
    eligible = (stars.min(axis=0) >= 0) & (lum > 0)
    peak, radius = lum, parameters['radius_px']
    for axis in (0, 1):
        padding = [(0, 0), (0, 0)]
        padding[axis] = (radius, radius)
        peak = np.lib.stride_tricks.sliding_window_view(
            np.pad(peak, padding, mode='edge'), 2*radius+1, axis=axis).max(axis=-1)
    t = np.divide(lum, peak, out=np.zeros_like(lum), where=peak > 0)
    m = .5 + .4*parameters['amount']
    return np.where(eligible, (1-m)/(m-(2*m-1)*t), 1).astype(np.float32)


def contour_assets(session, request, binding, prepared, *, create=False):
    """Freeze or verify deterministic auxiliary files, without executing Siril."""
    from PIL import Image
    parameters = binding.get('contour')
    if not parameters:
        return None
    directory = bound_path(session, 'reports/'+binding['run_id'], allowed_roots=('reports',))
    report_path, weight_path, preview_path = [directory/name for name in
        ('contour.json', 'contour-weights.tif', 'contour-weights.png')]
    raw = read_scientific_pixels(bound_file(session, prepared['layers']['display_stars']))[0]
    weight = contour_weights(raw, parameters)
    image = Image.fromarray(weight[::-1])
    preview = Image.fromarray(np.rint(weight[::-1]*255).astype('uint8'))
    preview.thumbnail((1600, 1600))
    stream = BytesIO(); preview.save(stream, format='PNG')
    if create:
        directory.mkdir()
        with weight_path.open('xb') as output:
            image.save(output, format='TIFF')
        with preview_path.open('xb') as output:
            output.write(stream.getvalue())
    if not weight_path.is_file() or weight_path.is_symlink() or not preview_path.is_file():
        fail('Contour auxiliary evidence is missing')
    with Image.open(weight_path) as actual:
        if actual.mode != 'F' or not np.array_equal(np.asarray(actual), weight[::-1]):
            fail('Contour auxiliary weights or orientation changed')
    if preview_path.read_bytes() != stream.getvalue():
        fail('Contour weight preview changed')
    report = {'schema': 'starun-siril.star-contour.v1', 'policy': CONTOUR_POLICY,
        'request': binding['request'], 'prepared': binding['prepared'], 'source': binding['source'],
        'regions': binding['regions'], 'parameters': parameters,
        'raw_stars': prepared['layers']['display_stars'],
        'implementation': prepared['bindings']['implementation'],
        'runtime': prepared['bindings']['runtime'],
        'weights': record(session, weight_path), 'preview': record(session, preview_path),
        'requires_visual_review': True}
    if create:
        atomic_write_json(report_path, report)
    elif load_json(report_path) != report:
        fail('Contour source, parameters, ROI or auxiliary bindings changed')
    return report, record(session, report_path)


def contour_input(session, payload, text):
    """Authorize only the hash-bound TIFF consumed by the complete contour recipe."""
    from siril_manual_bundle import strict_json_bytes
    rows = [row.split(':', 1)[1] for row in text.splitlines() if row.startswith('# star-contour:')]
    if not rows:
        return None
    if len(rows) != 1 or payload.get('execution_policy', {}).get('recomposition_contour') != CONTOUR_POLICY:
        fail('Contour requires one declaration and its frozen policy')
    item = strict_json_bytes(rows[0].encode(), document='star contour declaration')
    if not isinstance(item, dict) or set(item) != {'path', 'sha256', 'size'} or type(item['size']) is not int:
        fail('Contour declaration requires an exact path/hash/size binding')
    path = bound_file(session, item)
    if record(session, path) != item:
        fail('Contour report size binding changed')
    report = load_json(path)
    if not isinstance(report, dict) or not {'request', 'parameters', 'weights', 'preview'} <= set(report):
        fail('Malformed contour report')
    declarations = [row.split(':', 1)[1] for row in text.splitlines() if row.startswith('# recomposition-proposal:')]
    if len(declarations) != 1 or strict_json_bytes(declarations[0].encode(), document='recomposition proposal') != report['request']:
        fail('Contour and recomposition must bind the same request')
    proposal_path = bound_file(session, report['request'])
    run_id = path.parent.name
    request, binding, prepared, _ = request_binding(session, payload, proposal_path, run_id, verify_parent=False)
    expected, actual = contour_assets(session, request, binding, prepared)
    if actual != item or expected != report:
        fail('Contour declaration differs from its proposal')
    return report, item


def contour_paths(session, run_id, channels):
    names = ('contour_weight', 'contour_stars', 'contribution_only')
    if channels == 3:
        names += ('contour_weight_rgb',)
    return {name: session/'artifacts'/(run_id+'-'+name.replace('_', '-')+'.fit') for name in names}


def exposure_rows(source):
    from deep_sky_siril_processing import finite_number
    header = read_fits_header(source)
    return [f'update_key {key} {finite_number(header[key]):.17g}'
            for key in ('EXPTIME', 'LIVETIME', 'STACKCNT') if key in header]


def contour_rows(session, source, raw, paths, weight, strength, *, raw_reference=None):
    from deep_sky_siril_processing import _variable
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    v = lambda p: _variable(p, session)
    rows = ['load '+q(weight), 'save '+q(paths['contour_weight'])+' -chksum', 'close']
    gain = paths['contour_weight']
    if 'contour_weight_rgb' in paths:
        gain = paths['contour_weight_rgb']
        rows += ['rgbcomp '+' '.join([q(paths['contour_weight'])]*3)+
                 ' '+q('-out='+str(gain.with_suffix('')))+' -nosum', 'close']
    # -nosum still averages EXPTIME; auxiliary masks must not change exposure metadata.
    rows += ['pm '+q(f'{v(raw)} * {v(gain)}')+' -nosum',
             *exposure_rows(raw_reference or raw), 'save '+q(paths['contour_stars'])+' -chksum', 'close',
             'pm '+q(f'{v(source)} + {strength:.17g} * {v(raw)}')+' -nosum',
             *exposure_rows(source), 'save '+q(paths['contribution_only'])+' -chksum', 'close']
    return rows


def validate_contour_recomposition(session, payload, commands, text, source, original, starless, primary, stretch, run_id):
    from deep_sky_siril_processing import script_commands
    report, item = contour_input(session, payload, text)
    request, binding, prepared, parent = request_binding(session, payload,
        bound_file(session, report['request']), run_id, verify_parent=False)
    actual_stretch = star_stretch(stretch, prepared)
    if (parent != source or science_path(session, payload, prepared['bindings']['original']) != original
        or science_path(session, payload, prepared['bindings']['starless']) != starless):
        fail('Contour belongs to a different scientific branch')
    expected, output, _ = script_text(session, binding, prepared, stretch, source)
    if output != primary or script_commands(expected) != commands:
        fail('Contour must execute the complete frozen replay, shared weight and additive recipe')
    layers = layer_paths(session/'artifacts', run_id, actual_stretch.get('hdr_transfer'))
    paths = contour_paths(session, run_id, read_scientific_pixels(source)[0].shape[0])
    roles = {k: str(p.relative_to(session)) for k, p in {**layers, **paths}.items()
             if k in ('full_baseline', 'original_starless', 'display_stars') or k in paths}
    roles.update(strength=request['strength'], contour_report=item)
    pairs = [{key: str(layers[name].relative_to(session)) for key, name in zip(
        ('gentle', 'faint', 'mixed'), names)} for names in
        (('ig', 'if', 'full_baseline'), ('ag', 'af', 'original_starless'))] if actual_stretch.get('hdr_transfer') else []
    return roles, pairs


def layer_paths(directory, prefix, hdr):
    roles = ['full_baseline', 'original_starless', 'display_stars']
    if hdr:
        roles += ['ig', 'if', 'ag', 'af']
    return {role: directory/(prefix+'-'+role.replace('_', '-')+'.fit') for role in roles}


def replay_rows(session, bindings, stretch, layers, *, absolute=False):
    from deep_sky_siril_processing import _variable
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    v = (lambda p: '$'+str(p.with_suffix(''))+'$') if absolute else (lambda p: _variable(p, session))
    payload = load_json(session/'session.json')
    original = science_path(session, payload, bindings['original'])
    starless = science_path(session, payload, bindings['starless'])
    rows, hdr = [], stretch.get('hdr_transfer')
    for parent, role, pair in ((original, 'full_baseline', ('ig', 'if')), (starless, 'original_starless', ('ag', 'af'))):
        if hdr:
            for key, chain in zip(pair, ('gentle_chain', 'faint_chain')):
                rows += ['load '+q(parent), *[' '.join([step['command'], *step['parameters']]) for step in hdr[chain]],
                         'save '+q(layers[key])+' -chksum', 'close']
            mask = session/hdr['mask']['blend_path']
            rows += ['pm '+q(f'{v(layers[pair[0]])} * {v(mask)} + {v(layers[pair[1]])} * (1 - {v(mask)})')+' -nosum',
                     'save '+q(layers[role])+' -chksum', 'close']
        else:
            rows += ['load '+q(parent), *[' '.join([step['command'], *step['parameters']]) for step in stretch['transfer_chain']],
                     'save '+q(layers[role])+' -chksum', 'close']
    rows += ['pm '+q(f'{v(layers["full_baseline"])} - {v(layers["original_starless"])}')+(' -nosum' if hdr else ''),
             'save '+q(layers['display_stars'])+' -chksum', 'close']
    return rows


def _unchanged(session, payload, manifest, observed):
    from deep_sky_siril_session import load_session, effective_session_payload
    _, frozen, latest = load_session(session)
    if latest != manifest or effective_session_payload(session, frozen, latest) != payload:
        fail('Session changed during recomposition preparation or generation')
    if any(fingerprint(path, include_path=False) != value for path, value in observed.items()):
        fail('Recomposition input changed during preparation or generation')


def star_curve_request(session, payload, binding, *, verify_parent=True):
    from deep_sky_siril_processing import validate_parameters
    if payload.get('execution_policy', {}).get('color_preservation') != 'vivid_preservation_v1':
        fail('Independent star curves require the frozen preservation policy')
    path = bound_file(session, binding)
    request = load_json(path)
    keys = {'schema','source','baseline','regions','transfer_chain','goal','parameter_reason'}
    if (not isinstance(request,dict) or set(request) != keys
        or request['schema'] != 'starun-siril.star-curve-request.v1'
        or not valid_text(request['goal']) or not valid_text(request['parameter_reason'])):
        fail('Star curve requires a bound baseline, ROI, explicit curve and observed goal')
    for key in ('source','baseline','regions'):
        item = request[key]
        if (not isinstance(item,dict) or set(item) != {'path','sha256','size'}
            or record(session,bound_file(session,item, ('artifacts',) if key == 'source' else ('reports',))) != item):
            fail('Star curve input bindings changed')
    baseline_path = bound_file(session,request['baseline'])
    if load_json(baseline_path).get('star_curve'):
        fail('Star curves must compare with the original body-curve baseline, without stacking curves')
    baseline,parent,_ = prepared_input(session,payload,request['baseline'],verify_parent=verify_parent)
    if request['source'] != baseline['bindings']['source']:
        fail('Star curve source differs from its baseline')
    if measure_regions(parent,None,bound_file(session,request['regions']),scientific=True)['status'] != 'measured':
        fail('Star curve regions must match the current subject')
    regions = load_json(bound_file(session,request['regions']))['regions']
    if not any(r['role'] == 'stars' for r in regions) or not any(r['role'] in ('core','faint') for r in regions):
        fail('Star curve requires fixed stellar and subject regions')
    chain = request['transfer_chain']
    if not isinstance(chain,list) or not 1 <= len(chain) <= 8:
        fail('Star curve requires one to eight explicit native operators')
    for step in chain:
        if (not isinstance(step,dict) or set(step) != {'command','parameters'}
            or step['command'] not in ('mtf','asinh','ght','modasinh') or not isinstance(step['parameters'],list)
            or any(not isinstance(v,str) for v in step['parameters'])):
            fail('Star curves permit only explicit MTF, Asinh, Modified Asinh or luminance GHS, never residual saturation')
        if step['command'] == 'modasinh' and payload.get('execution_policy', {}).get('guidance') not in {'stellar_stretch_flow_v28', EXECUTION_POLICY['guidance']}:
            fail('Modified Asinh requires the frozen v28 stellar stretch guidance')
        validate_parameters([step['command'],*step['parameters']], 'stars.recompose')
        if step['command'] == 'ght' and {v.split('=')[0] for v in step['parameters']} != {'-D','-B','-LP','-SP','-HP','-human','-clipmode'}:
            fail('Star GHS must explicitly specify D/B/LP/SP/HP, human luminance and rgbblend')
    curve = {k: request[k] for k in ('baseline','regions','transfer_chain','goal','parameter_reason')}
    return {'request': record(session,path), **curve}, baseline


def star_curve_for_script(session,payload,text,run_id):
    from siril_manual_bundle import strict_json_bytes
    declarations = [row.split(':',1)[1] for row in text.splitlines() if row.startswith('# recomposition-proposal:')]
    if len(declarations) != 1:
        fail('Independent star transfer requires one bound recomposition proposal')
    declaration = strict_json_bytes(declarations[0].encode(), document='recomposition proposal')
    _,_,prepared,_ = request_binding(session,payload,bound_file(session,declaration),run_id,verify_parent=False)
    return prepared.get('star_curve')


def star_stretch(stretch, prepared):
    return {'transfer_chain': prepared['star_curve']['transfer_chain']} if prepared.get('star_curve') else stretch


def prepare(session_value, source, run_id, timeout=1800, *, star_request=None):
    from deep_sky_siril_dualband import _native
    check_run_id(run_id)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 86400:
        fail('Invalid preparation timeout')
    session, payload, manifest, bindings, stretch, observed = _generation_context(session_value, source)
    curve, baseline = (None, None)
    if star_request is not None:
        curve, baseline = star_curve_request(session,payload,record(session, bound_path(session,star_request,must_exist=True,allowed_roots=('reports',))))
        if baseline['bindings'] != bindings:
            fail('Independent star curve baseline belongs to a different accepted parent')
        observed[bound_file(session,curve['request'])] = {k: curve['request'][k] for k in ('sha256','size')}
        stretch = {'transfer_chain': curve['transfer_chain']}
    directory = bound_path(session, 'reports/'+run_id, allowed_roots=('reports',))
    if directory.exists() or (session/'runs'/(run_id+'.json')).exists():
        fail('Preparation evidence already exists; use a new run ID')
    directory.mkdir()
    layers = layer_paths(directory, 'prepared', stretch.get('hdr_transfer'))
    control = directory/'full-contribution.fit'
    views = {role: directory/(role+'.jpg') for role in ('parent', 'full-contribution')}
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    v = lambda p: '$'+str(p.with_suffix(''))+'$'
    parent = science_path(session, payload, bindings['source'])
    catalog = directory/'linear-full-stars.tsv'
    if baseline is not None:
        catalog = bound_file(session,baseline['geometry_catalog']['table'])
    rows = catalog_recipe(session, payload, bindings, catalog)[0] if geometry_enabled(payload) and baseline is None else []
    rows += replay_rows(session, bindings, stretch, layers, absolute=True)
    rows += ['pm '+q(f'{v(parent)} + 1 * {v(layers["display_stars"])}')+' -nosum',
             'save '+q(control)+' -chksum', 'close']
    for image, view in ((parent, views['parent']), (control, views['full-contribution'])):
        rows += ['load '+q(image), 'savejpg '+q(view.with_suffix(''))+' 95', 'close']
    evidence = _native(session, bindings['runtime'], directory, 'prepare', rows, views['full-contribution'], time.monotonic()+timeout)
    _unchanged(session, payload, manifest, observed)
    signed, scale = _residual_preview(read_scientific_pixels(layers['display_stars'])[0])
    views['signed-stars'] = directory/'signed-stars.png'
    views['signed-stars'].write_bytes(signed)
    report = {'schema': PREPARED, 'policy': POLICY, 'run_id': run_id, 'bindings': bindings,
              'layers': {key: record(session, path) for key, path in layers.items()},
              'control': record(session, control), 'previews': {key: record(session, path) for key, path in views.items()},
              'execution': evidence, 'signed_preview_abs_scale': scale, 'requires_visual_review': True}
    if curve is not None:
        report['star_curve'] = curve
    if geometry_enabled(payload):
        report['geometry_catalog'] = baseline['geometry_catalog'] if baseline else catalog_binding(session, payload, bindings, catalog)
    path = directory/'recomposition-prepared.json'
    atomic_write_json(path, report)
    prepared_input(session, payload, record(session, path))
    return {'prepared': record(session, path), 'source': bindings['source'],
            'previews': report['previews'], 'status': 'prepared'}


def prepared_input(session, payload, binding, *, verify_parent=True):
    from deep_sky_siril_processing import script_commands, _measure_recomposition
    from deep_sky_siril_hdr import mask_bindings
    path = bound_file(session, binding)
    report = load_json(path)
    if (report.get('schema') != PREPARED or report.get('policy') != POLICY
        or payload.get('execution_policy', {}).get('recomposition_parameters') != POLICY
        or report.get('requires_visual_review') is not True):
        fail('Invalid proposal-bound prepared recomposition')
    bindings = report['bindings']
    from deep_sky_siril_session import _PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY
    old_helper = (payload.get('execution_policy') == _PRE_RECOMPOSITION_GEOMETRY_EXECUTION_POLICY
                  and bindings.get('helper') == PRE_GEOMETRY_HELPER)
    from deep_sky_siril_session import _PRE_VIVID_BINDINGS
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_VIVID_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_VIVID_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    from deep_sky_siril_session import _PRE_STAR_COLOR_BINDINGS, _PRE_CHROMA_BINDINGS, _PRE_REGION_COLOR_BINDINGS, _PRE_STAR_STRETCH_BINDINGS
    from deep_sky_siril_session import _PRE_STRETCH_ROUTING_BINDINGS
    from deep_sky_siril_session import _PRE_CONTOUR_BINDINGS
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_CONTOUR_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_CONTOUR_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_STRETCH_ROUTING_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_STRETCH_ROUTING_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_STAR_STRETCH_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_STAR_STRETCH_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_REGION_COLOR_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_REGION_COLOR_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_CHROMA_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_CHROMA_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    old_helper = old_helper or (payload.get('execution_policy') == _PRE_STAR_COLOR_BINDINGS['execution_policy']
                              and bindings.get('helper') == _PRE_STAR_COLOR_BINDINGS['helpers']['deep_sky_star_reduction.py'])
    if bindings.get('helper') != fingerprint(Path(__file__), include_path=False) and not old_helper:
        fail('Recomposition implementation changed')
    if geometry_enabled(payload):
        from deep_sky_siril_stretch_v2 import implementation_matches
        if not implementation_matches(session, bindings.get('implementation')):
            fail('Prepared recomposition implementation set changed')
        item = report.get('geometry_catalog')
        if (not isinstance(item, dict) or set(item) != {'table', 'source', 'geometry', 'detection',
                'native_settings', 'coordinate_frame', 'coverage'}):
            fail('New recomposition requires the complete frozen geometry catalog')
        catalog = bound_file(session, report['geometry_catalog']['table'])
        if catalog_binding(session, payload, bindings, catalog) != report['geometry_catalog']:
            fail('Prepared recomposition catalog changed')
    elif 'geometry_catalog' in report or 'implementation' in bindings:
        fail('Geometry evidence requires its frozen recomposition policy')
    parent = science_path(session, payload, bindings['source'])
    if verify_parent:
        _, _, _, actual, _, _ = _generation_context(session, parent)
        if actual != bindings:
            fail('Prepared recomposition differs from its accepted branch')
    for key in ('original', 'starless', 'separation', 'stretch', 'parent_run'):
        science_path(session, payload, bindings[key])
    for item in bindings['reviews']:
        review = load_json(bound_file(session, item, ('reviews',)))
        receipt = session/'runs'/Path(item['path']).name
        if review.get('verdict') != 'accept' or review.get('run_receipt_sha256') != sha256_file(receipt):
            fail('Prepared parent acceptance changed')
    runtime = load_json(session/'reports/tool-probe.json')['tools']['siril_cli']
    if runtime != bindings['runtime'] or not fingerprint_matches(runtime['fingerprint']):
        fail('Prepared Siril runtime changed')
    stretch = load_json(bound_file(session, bindings['stretch'], ('runs',)))
    if stretch.get('hdr_transfer'):
        mask_bindings(session, stretch['hdr_transfer'], stretch)
    baseline = None
    if report.get('star_curve'):
        curve,baseline = star_curve_request(session,payload,report['star_curve']['request'],verify_parent=verify_parent)
        if curve != report['star_curve'] or baseline['bindings'] != bindings or report['geometry_catalog'] != baseline['geometry_catalog']:
            fail('Star curve, baseline, source pair or fixed catalog changed')
        stretch = star_stretch(stretch,report)
    layers = {key: bound_file(session, value) for key, value in report['layers'].items()}
    control = bound_file(session, report['control'])
    views = {key: bound_file(session, value) for key, value in report['previews'].items()}
    shape = read_scientific_pixels(parent)[0].shape
    for file in (parent, *layers.values(), control):
        values = read_scientific_pixels(file)[0]
        if values.shape != shape or not np.isfinite(values).all() or file != parent and read_fits_header(file).get('BITPIX') != -32:
            fail('Prepared recomposition requires finite float32 science with unchanged geometry')
    del values
    branch = {key: str(layers[key].relative_to(session)) for key in ('full_baseline', 'original_starless', 'display_stars')}
    _measure_recomposition(session, {**branch, 'strength': 1.}, payload['context']['target_type'],
                           source=parent, primary=control, policy=payload['execution_policy']['star_diagnostics'])
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    v = lambda p: '$'+str(p.with_suffix(''))+'$'
    rows = catalog_recipe(session, payload, bindings, catalog)[0] if geometry_enabled(payload) and baseline is None else []
    rows += replay_rows(session, bindings, stretch, layers, absolute=True)
    rows += ['pm '+q(f'{v(parent)} + 1 * {v(layers["display_stars"])}')+' -nosum', 'save '+q(control)+' -chksum', 'close']
    for image, view in ((parent, views['parent']), (control, views['full-contribution'])):
        rows += ['load '+q(image), 'savejpg '+q(view.with_suffix(''))+' 95', 'close']
    signed, scale = _residual_preview(read_scientific_pixels(layers['display_stars'])[0])
    if views['signed-stars'].read_bytes() != signed or report['signed_preview_abs_scale'] != scale:
        fail('Prepared signed-residual diagnostic differs from its scientific pixels')
    script = bound_file(session, report['execution']['script'])
    bound_file(session, report['execution']['log'])
    if script_commands(script.read_text()) != script_commands('\n'.join(['requires 1.4.4 1.5.0', 'set32bits', *rows, 'close'])):
        fail('Prepared recomposition recipe changed')
    return report, parent, stretch


def analyze(session, payload, prepared, regions, *, verify_parent=True):
    report, parent, _ = prepared_input(session, payload, prepared, verify_parent=verify_parent)
    region_path = bound_file(session, regions)
    baseline = measure_regions(bound_file(session, report['control']), parent, region_path, scientific=True)
    if baseline['status'] != 'measured':
        fail('Recomposition ROIs must bind finite, aligned scientific pixels')
    rows = load_json(region_path)['regions']
    if not any(r['role'] == 'stars' for r in rows) or not any(r['role'] in {'core', 'faint'} for r in rows):
        fail('Select representative stars and protected subject ROIs')
    stars = read_scientific_pixels(bound_file(session, report['layers']['display_stars']))[0]
    analysis = {'schema': 'starun-siril.recomposition-analysis.v1', 'prepared': prepared, 'regions': regions,
            'full_contribution': baseline, 'signed_residual': residual_statistics(stars, rows),
            'requires_visual_review': True,
            'limitations': 'Pixel statistics do not measure star diameter, halo geometry or subject retention'}
    if geometry_enabled(payload):
        from deep_sky_siril_psf import parse_native_stars
        a = read_scientific_pixels(parent)[0]
        c = read_scientific_pixels(bound_file(session, report['control']))[0]
        analysis['geometry'] = geometry_profiles(a, c, c, parse_native_stars(
            bound_file(session, report['geometry_catalog']['table']), spatial=True), rows)
        analysis['geometry']['catalog'] = report['geometry_catalog']
        analysis['geometry']['comparison_role'] = 'full_contribution_identity_before_proposal'
        analysis['limitations'] = analysis['geometry']['limitations']
        if stellar_color_enabled(payload):
            images = {'subject': a, 'full_contribution': c}
            if report.get('star_curve'):
                old,_,_ = prepared_input(session,payload,report['star_curve']['baseline'],verify_parent=False)
                images['original_baseline'] = read_scientific_pixels(bound_file(session,old['control']))[0]
            analysis['stellar_color'] = stellar_color_diagnostics(images, parse_native_stars(
                bound_file(session,report['geometry_catalog']['table']),spatial=True), rows, analysis['geometry'])
    return analysis, report, parent


def request_binding(session, payload, request_value, run_id, *, verify_parent=True):
    check_run_id(run_id)
    path = bound_path(session, request_value, must_exist=True, allowed_roots=('reports',))
    request = load_json(path)
    fields = {'schema', 'source', 'prepared', 'regions', 'strength', 'goal', 'parameter_reason', 'manual_lookup'}
    if (not isinstance(request, dict) or not fields <= set(request) <= fields | {'revision', 'contour'}
        or request['schema'] not in (REQUEST, LEGACY_REQUEST) or not valid_text(request['goal']) or not valid_text(request['parameter_reason'])
        or len(request['parameter_reason']) > 1000
        or type(request['strength']) not in (int, float) or not math.isfinite(request['strength'])
        or not PROTOCOL_MIN_STRENGTH <= request['strength'] <= PROTOCOL_MAX_STRENGTH):
        fail('An explicit proposal, goal, parameter rationale and finite strength in [0.30, 1.00] are required; no defaults')
    for key in ('source', 'prepared', 'regions'):
        item = request[key]
        path_roots = ('artifacts',) if key == 'source' else ('reports',)
        if (not isinstance(item, dict) or set(item) != {'path', 'sha256', 'size'} or type(item['size']) is not int
            or record(session, bound_file(session, item, path_roots)) != item):
            fail('Recomposition source, prepared material and ROI require exact path/hash/size bindings')
    lookup = request['manual_lookup']
    if (not isinstance(lookup, dict) or set(lookup) != {'status', 'reason', 'evidence'}
        or lookup['status'] not in ('performed', 'not_needed') or not valid_text(lookup['reason'])
        or not isinstance(lookup['evidence'], list) or len(lookup['evidence']) > 32
        or (lookup['status'] == 'performed') != bool(lookup['evidence'])):
        fail('Declare authentic manual lookup evidence or a complete-skeleton rationale')
    documents = []
    for item in lookup['evidence']:
        if not isinstance(item, dict) or set(item) != {'path', 'sha256'} or not str(item['path']).startswith('reports/manual-evidence/'):
            fail('Manual evidence must bind reports/manual-evidence paths and hashes')
        documents.append(load_json(bound_file(session, item)))
    if documents:
        from query_siril_manual import verify_query_evidence_documents
        verify_query_evidence_documents(documents, skill_root=SKILL_ROOT, session_payload=payload)
    _, prepared, source = analyze(session, payload, request['prepared'], request['regions'], verify_parent=verify_parent)
    contour = None
    if 'contour' in request:
        if (request['schema'] != REQUEST
            or payload.get('execution_policy', {}).get('recomposition_contour') != CONTOUR_POLICY):
            fail('Contour requires a v2 proposal and its frozen execution policy')
        contour = contour_parameters(request['contour'], read_scientific_pixels(source)[0].shape)
    if prepared.get('star_curve') and request['regions'] != prepared['star_curve']['regions']:
        fail('Recomposition must retain the independent star curve ROI binding')
    if request['source'] != prepared['bindings']['source']:
        fail('Recomposition proposal source differs from the prepared current parent')
    revision = request.get('revision')
    if verify_parent and revision is None:
        for entry in load_json(session/'manifest.json')['runs']:
            if entry['id'] == run_id or entry.get('status') != 'success':
                continue
            prior = load_json(bound_path(session, 'runs/'+entry['id']+'.json', must_exist=True, allowed_roots=('runs',)))
            if prior.get('recomposition_proposal', {}).get('source') == request['source']:
                fail('An existing candidate from this subject requires an observation-bound revision')
    if revision is not None:
        if (not isinstance(revision, dict) or set(revision) != {'request', 'run_id', 'evidence', 'reason'}
            or not valid_text(revision['reason']) or not isinstance(revision['evidence'], list) or not revision['evidence']):
            fail('Revisions require the prior run/proposal and actual observation evidence')
        check_run_id(revision['run_id'])
        if revision['run_id'] == run_id or bound_file(session, revision['request']) == path:
            fail('A revision requires a new run ID and proposal')
        prior = load_json(bound_file(session, revision['request']))
        prior_path = bound_path(session, 'runs/'+revision['run_id']+'.json', must_exist=True, allowed_roots=('runs',))
        if verify_parent:
            from deep_sky_siril_core import _load_run
            _, prior_run = _load_run(session, revision['run_id'])
        else:
            prior_run = load_json(prior_path)
            entries = [row for row in load_json(session/'manifest.json')['runs'] if row['id'] == revision['run_id']]
            if (len(entries) != 1 or entries[0].get('receipt_sha256') != sha256_file(prior_path)
                or entries[0].get('status') != 'success' or entries[0].get('receipt') != prior_path.name
                or prior_run.get('status') != 'success' or prior_run.get('id') != revision['run_id']):
                fail('Revision observation run changed')
        if (prior.get('source') != request['source'] or prior.get('prepared') != request['prepared']
            or prior_run.get('recomposition_proposal', {}).get('request') != revision['request']):
            fail('Revise from the same current parent and frozen star residual; do not accumulate recomposition')
        from deep_sky_siril_core import _recomposition_review_paths
        materials = {**_recomposition_review_paths(session, prior_run), **review_paths(session, prior_run)}
        for evidence in revision['evidence']:
            observed = bound_file(session, evidence, ('reports', 'previews', 'runs', 'artifacts'))
            if record(session, observed) != evidence:
                fail('Revision observation size binding changed')
            if evidence['path'] not in materials:
                fail('Revision observations must bind actual prior candidate materials')
    return request, {'policy': POLICY, 'request': record(session, path), 'prepared': request['prepared'],
        'source': request['source'], 'regions': request['regions'], 'strength': request['strength'], 'run_id': run_id,
        **({'contour': contour} if contour else {}),
        **({'star_curve': prepared['star_curve']} if prepared.get('star_curve') else {})}, prepared, source


def script_text(session, binding, prepared, stretch, source):
    from deep_sky_siril_processing import _variable
    run_id = binding['run_id']
    stretch = star_stretch(stretch,prepared)
    layers = layer_paths(session/'artifacts', run_id, stretch.get('hdr_transfer'))
    primary = session/'artifacts'/(run_id+'-recomposed.fit')
    preview = session/'previews'/(run_id+'-candidate.jpg')
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    rows = ['requires 1.4.4 1.5.0', 'set32bits', '# recomposition-proposal: '+json.dumps(binding['request']),
            *replay_rows(session, prepared['bindings'], stretch, layers)]
    stars, extra = layers['display_stars'], {}
    if binding.get('contour'):
        report = session/'reports'/run_id/'contour.json'
        rows.insert(3, '# star-contour: '+json.dumps(record(session, report)))
        weight = bound_file(session, load_json(report)['weights'])
        extra = contour_paths(session, run_id, read_scientific_pixels(source)[0].shape[0])
        rows += contour_rows(session, source, stars, extra, weight, binding['strength'],
            raw_reference=bound_file(session, prepared['layers']['display_stars']))
        stars = extra['contour_stars']
    rows += ['pm '+q(f'{_variable(source, session)} + {binding["strength"]:.17g} * {_variable(stars, session)}')+
             (' -nosum' if stretch.get('hdr_transfer') or extra else '')]
    if extra:
        rows += exposure_rows(source)
    rows += ['save '+q(primary)+' -chksum', 'stat main', 'savejpg '+q(preview.with_suffix(''))+' 95', 'close']
    return '\n'.join(rows)+'\n', primary, [*layers.values(), *extra.values(), primary, preview]


def bind_script(session, payload, source, primary, value, branch, run_id, *, verify_parent=True):
    from siril_manual_bundle import strict_json_bytes
    from deep_sky_siril_processing import script_commands, validate_recomposition
    rows = [row.strip() for row in value.splitlines() if row.strip().startswith('# recomposition-proposal')]
    if len(rows) != 1 or not rows[0].startswith('# recomposition-proposal:'):
        fail('Exactly one hash-bound recomposition-proposal declaration is required')
    declared = strict_json_bytes(rows[0].split(':', 1)[1].encode(), document='recomposition proposal declaration')
    request, binding, prepared, parent = request_binding(session, payload, bound_file(session, declared), run_id, verify_parent=verify_parent)
    stretch = load_json(bound_file(session, prepared['bindings']['stretch'], ('runs',)))
    stretch = star_stretch(stretch,prepared)
    original = science_path(session, payload, prepared['bindings']['original'])
    starless = science_path(session, payload, prepared['bindings']['starless'])
    if binding.get('contour'):
        roles, _ = validate_contour_recomposition(session, payload, script_commands(value), value, source,
            original, starless, primary, stretch, run_id)
    elif stretch.get('hdr_transfer'):
        from deep_sky_siril_hdr import validate_recomposition as validate_hdr
        roles, _ = validate_hdr(session, script_commands(value), source, original, starless, primary, stretch['hdr_transfer'])
    else:
        roles = validate_recomposition(session, script_commands(value), source, original, starless, primary, stretch['transfer_chain'])
    if (declared != binding['request'] or source != parent or branch['strength'] != request['strength']
        or branch['separation_run'] != Path(prepared['bindings']['separation']['path']).stem
        or branch['stretch_run'] != Path(prepared['bindings']['stretch']['path']).stem
        or any(branch.get(key) != item for key, item in roles.items())):
        fail('Actual strength, source, frozen replay or output roles differ from the Agent proposal')
    return binding


def generate_recompose_assets(session_path: Path, *, request: Path, run_id: str) -> Dict[str, Any]:
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    session, payload, manifest = load_session(session_path)
    if not execution_policy_is_current(payload):
        raise ContractError('legacy_session_read_only', 'Initialize a new session for Stage 9 generation')
    if manifest.get('finalization'):
        fail('Finalized sessions cannot generate new Stage 9 assets')
    payload = effective_session_payload(session, payload, manifest)
    proposal, binding, prepared, source = request_binding(session, payload, request, run_id)
    stretch = load_json(bound_file(session, prepared['bindings']['stretch'], ('runs',)))
    script = bound_path(session, 'scripts/'+run_id+'.ssf', allowed_roots=('scripts',))
    provenance = script.with_suffix('.provenance.json')
    layers = layer_paths(session/'artifacts', run_id, star_stretch(stretch, prepared).get('hdr_transfer'))
    outputs = [*layers.values(), session/'artifacts'/(run_id+'-recomposed.fit'), session/'previews'/(run_id+'-candidate.jpg')]
    if binding.get('contour'):
        outputs += list(contour_paths(session, run_id, read_scientific_pixels(source)[0].shape[0]).values())
    if any(p.exists() or p.is_symlink() for p in (script, provenance, *outputs, session/'runs'/(run_id+'.json'), session/'reports'/run_id)):
        fail('Recomposition assets already exist; use a new run ID')
    references = protocol_reference_bindings('stars.recompose')
    policy = SKILL_ROOT/'references/command-policy.json'
    observed = {policy: fingerprint(policy, include_path=False)}
    for reference in references:
        path = SKILL_ROOT/reference['path']
        observed[path] = fingerprint(path, include_path=False)
        if observed[path]['sha256'] != reference['sha256']:
            raise ContractError('knowledge_binding_drift', f'Stage 9 reference changed: {path}')
    if binding.get('contour'):
        contour_assets(session, proposal, binding, prepared, create=True)
    value, primary, outputs = script_text(session, binding, prepared, stretch, source)
    document = {'schema': 'starun-siril.ssf-provenance.v1', 'contract_version': '1', 'run_id': run_id,
        'protocol': 'stars.recompose', 'script': {'path': str(script.relative_to(session)), 'sha256': hashlib.sha256(value.encode()).hexdigest()},
        'references': references,
        'command_policy': {'path': 'references/command-policy.json', 'sha256': observed[policy]['sha256']},
        'manual_lookup': proposal['manual_lookup'], 'rationale': {'applicability': proposal['goal'],
        'parameter_choices': [proposal['parameter_reason'], f'Explicit star contribution: {proposal["strength"]:.17g}']}}
    _unchanged(session, payload, manifest, observed)
    bound_file(session, binding['request']); bound_file(session, binding['regions'])
    prepared_input(session, payload, binding['prepared'], verify_parent=False)
    for path, data in ((script, value.encode()), (provenance, canonical_json(document)+b'\n')):
        with path.open('xb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
    _fsync_directory(script.parent)
    return {'status': 'proposed', 'mode': 'hdr' if stretch.get('hdr_transfer') else 'single_chain',
            'source': str(source), 'primary_output': str(primary), 'expected_outputs': list(map(str, outputs)),
            'script_path': str(script), 'provenance_path': str(provenance), 'strength': proposal['strength'],
            'separation_run': Path(prepared['bindings']['separation']['path']).stem,
            'stretch_run': Path(prepared['bindings']['stretch']['path']).stem}


def residual_statistics(values, regions, *, strength=1.):
    rows, height = [], values.shape[1]
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        crop = values[:, height-y1:height-y0, x0:x1]
        rows.append({**region, 'channels': [{'channel': i, 'min': float(v.min()), 'max': float(v.max()),
            'mean': float(v.mean()), 'mean_abs': float(np.mean(np.abs(v))),
            'contribution_mean': float(v.mean())*strength, 'contribution_mean_abs': float(np.mean(np.abs(v)))*strength,
            'negative_count': int(np.sum(v < 0)), 'negative_energy': float(np.sum(np.maximum(-v, 0)))}
            for i, v in enumerate(crop)]})
    return rows


def _signed_display(values):
    lum = values[0] if values.shape[0] == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], values)
    scale = float(np.max(np.abs(lum)))
    signed = np.full((*lum.shape, 3), .5)
    if scale:
        signed[:, :, 0] += lum/(2*scale); signed[:, :, 2] -= lum/(2*scale)
    return signed, scale


def _residual_preview(values):
    from PIL import Image
    signed, scale = _signed_display(values[:, ::-1])
    preview = Image.fromarray(np.rint(np.clip(signed, 0, 1)*255).astype('uint8'))
    preview.thumbnail((1600, 1600))
    stream = BytesIO(); preview.save(stream, format='PNG')
    return stream.getvalue(), scale


def _roi_panel(parent, control, candidate, stars, regions, geometry=None, *, labels=('subject', 'full stars', 'candidate', 'signed stars +/-')):
    from PIL import Image, ImageDraw
    widths = [r['rect'][2]-r['rect'][0] for r in regions]
    heights = [r['rect'][3]-r['rect'][1] for r in regions]
    profiles = {r['region_id']: r for r in geometry['regions']} if geometry else {}
    row_heights = [max(h+24, 200) if r['id'] in profiles else h+24 for r, h in zip(regions, heights)]
    cell_width = max(220, max(widths)) if geometry else max(widths)
    panel = Image.new('RGB', (4*cell_width+(340 if geometry else 0), sum(row_heights)), '#202020')
    draw, top, scales = ImageDraw.Draw(panel), 0, {}
    height = parent.shape[1]
    for region, h, row_height in zip(regions, heights, row_heights):
        x0, y0, x1, y1 = region['rect']
        crops = [v[:, height-y1:height-y0, x0:x1][:, ::-1] for v in (parent, control, candidate, stars)]
        images = []
        for crop in crops[:3]:
            rgb = np.repeat(crop, 3, axis=0) if crop.shape[0] == 1 else crop
            images.append(np.moveaxis(rgb, 0, -1))
        signed, scales[region['id']] = _signed_display(crops[-1])
        images.append(signed)
        for i, (data, label) in enumerate(zip(images, labels)):
            panel.paste(Image.fromarray(np.rint(np.clip(data, 0, 1)*255).astype('uint8')), (i*cell_width, top+24))
            draw.text((i*cell_width, top+3), region['id']+' '+label, fill='white')
        if region['id'] in profiles:
            row = profiles[region['id']]
            left = 4*cell_width+8
            draw.text((left, top+3), 'Catalog '+str(row.get('star_id', '?'))+': '+row['status'], fill='white')
            if 'profiles' in row:
                values = [(key, color, p) for (key, p), color in zip(row['profiles'].items(), ('#aaaaaa', '#55ccff', '#ffaa44'))]
                samples = [v for _, _, p in values for v in p['profile'] if v is not None]
                low, high = min(0., min(samples, default=0.)), max(samples, default=0.)
                span = high-low
                for i, (key, color, p) in enumerate(values):
                    draw.text((left, top+20+14*i), key, fill=color)
                    points = [(left+300*r/row['aperture_radius_px'], top+148-70*(v-low)/span)
                              for r, v in zip(p['radii_px'], p['profile'])] if span and None not in p['profile'] else []
                    if len(points) > 1:
                        draw.line(points, fill=color, width=1)
                for i, (key, value) in enumerate(row['comparisons'].items()):
                    draw.text((left, top+158+14*i), key+': '+('unmeasurable' if value is None else format(value, '.6g')), fill='white')
                draw.text((left, top+186), f'r:0..{row["aperture_radius_px"]:.3g} px | Y:{low:.3g}..{high:.3g}', fill='white')
            else:
                for i, reason in enumerate(row['reasons']):
                    draw.text((left, top+24+16*i), reason[:48], fill='#ffaa44')
        top += row_height
    stream = BytesIO(); panel.save(stream, format='PNG')
    return stream.getvalue(), scales


def measure_contour(session, source, primary, metadata, proposal, binding, prepared, stars, rows):
    from deep_sky_siril_processing import verify_color_metadata
    from deep_sky_siril_psf import parse_native_stars
    report, item = contour_assets(session, proposal, binding, prepared)
    if metadata['branch_binding'].get('contour_report') != item:
        fail('Formal contour report differs from its auxiliary evidence')
    paths = contour_paths(session, binding['run_id'], len(stars))
    if any(metadata['branch_binding'].get(key) != str(path.relative_to(session)) for key, path in paths.items()):
        fail('Contour output roles changed')
    arrays = {key: read_scientific_pixels(path)[0] for key, path in paths.items()}
    for key, values in arrays.items():
        shape = (1, *stars.shape[1:]) if key == 'contour_weight' else stars.shape
        if values.shape != shape or not np.isfinite(values).all() or read_fits_header(paths[key]).get('BITPIX') != -32:
            fail('Contour layers require finite float32 pixels and unchanged geometry')
    weight = contour_weights(read_scientific_pixels(bound_file(session, prepared['layers']['display_stars']))[0], binding['contour'])
    a, b = (read_scientific_pixels(path)[0] for path in (source, primary))
    control, shaped = arrays['contribution_only'], arrays['contour_stars']
    errors = {'weight': float(np.max(np.abs(arrays['contour_weight'][0]-weight))),
        'multiply': float(np.max(np.abs(shaped-stars*weight))),
        'control': float(np.max(np.abs(control-(a+proposal['strength']*stars))))}
    if 'contour_weight_rgb' in arrays:
        errors['rgb_copy'] = float(np.max(np.abs(arrays['contour_weight_rgb']-weight)))
    protected = (stars.min(axis=0) < 0) | (_luminance(stars) <= 0)
    if max(errors.values()) > 1e-6 or not np.array_equal(shaped[:, protected], stars[:, protected]):
        fail('Contour weight, RGB copy, multiplication, control or signed-pixel preservation failed')
    verify_color_metadata(source, primary)
    verify_color_metadata(source, paths['contribution_only'])
    verify_color_metadata(session/metadata['branch_binding']['display_stars'], paths['contour_stars'])
    table = parse_native_stars(bound_file(session, prepared['geometry_catalog']['table']), spatial=True)
    full = read_scientific_pixels(bound_file(session, prepared['control']))[0]
    result = {'policy': CONTOUR_POLICY, 'report': item, 'parameters': binding['contour'],
        'errors': errors, 'protected_pixel_count': int(protected.sum()),
        'weight_range': [float(weight.min()), float(weight.max())],
        'contribution_only': record(session, paths['contribution_only']),
        'contribution_geometry': geometry_profiles(a, full, control, table, rows),
        'contour_geometry': geometry_profiles(a, control, b, table, rows),
        'signed_residual': residual_statistics(shaped, rows, strength=proposal['strength']),
        'requires_visual_review': True}
    return result, control


def measure(session, source, primary, metadata, *, write=False):
    payload = load_json(session/'session.json')
    binding = metadata['recomposition_proposal']
    proposal, actual, prepared, parent = request_binding(session, payload,
        bound_file(session, binding['request']), binding['run_id'], verify_parent=False)
    if actual != binding or parent != source:
        fail('Recomposition proposal changed during execution')
    regions_path = bound_file(session, binding['regions'])
    rows = load_json(regions_path)['regions']
    baseline = measure_regions(bound_file(session, prepared['control']), source, regions_path, scientific=True)
    compared = measure_regions(primary, source, regions_path, scientific=True)
    if baseline['status'] != 'measured' or compared['status'] != 'measured':
        fail('Recomposition candidate ROI measurements are unavailable')
    for role in ('full_baseline', 'original_starless', 'display_stars'):
        before = read_scientific_pixels(bound_file(session, prepared['layers'][role]))[0]
        after_path = bound_path(session, metadata['branch_binding'][role], must_exist=True, allowed_roots=('artifacts',))
        after = read_scientific_pixels(after_path)[0]
        if (before.shape != after.shape or not np.isfinite(after).all()
            or read_fits_header(after_path).get('BITPIX') != -32 or np.max(np.abs(before-after)) > 1e-5):
            fail('Formal frozen star replay differs from the prepared scientific material')
    a = read_scientific_pixels(source)[0]
    c = read_scientific_pixels(bound_file(session, prepared['control']))[0]
    b = read_scientific_pixels(primary)[0]
    stars = read_scientific_pixels(session/metadata['branch_binding']['display_stars'])[0]
    if (a.shape != b.shape or not np.isfinite(b).all() or read_fits_header(primary).get('BITPIX') != -32):
        fail('Recomposition requires finite float32 candidate pixels and unchanged geometry')
    geometry = None
    if geometry_enabled(payload):
        from deep_sky_siril_psf import parse_native_stars
        geometry = geometry_profiles(a, c, b, parse_native_stars(
            bound_file(session, prepared['geometry_catalog']['table']), spatial=True), rows,
            color=not stellar_color_enabled(payload) and payload.get('execution_policy', {}).get('color_preservation') == 'vivid_preservation_v1')
        geometry['catalog'] = prepared['geometry_catalog']
    panel, scales = _roi_panel(a, c, b, stars, rows, geometry)
    endpoints = {key: [{'channel': i, 'nonpositive': int(np.sum(plane <= 0)), 'highlight': int(np.sum(plane >= 1))}
                      for i, plane in enumerate(values)] for key, values in (('subject', a), ('full_contribution', c), ('candidate', b))}
    report = {'schema': 'starun-siril.recomposition-diagnostics.v1', 'policy': POLICY,
        'request': binding['request'], 'prepared': binding['prepared'], 'source': binding['source'],
        'candidate': record(session, primary), 'regions': binding['regions'], 'strength': proposal['strength'], 'goal': proposal['goal'],
        'full_contribution': baseline, 'candidate_regions': compared,
        'signed_residual': residual_statistics(stars, rows, strength=proposal['strength']),
        'endpoints': endpoints, 'color': {'status': 'measured', 'evidence': 'Per-channel regional statistics'} if a.shape[0] == 3
            else {'status': 'unmeasurable', 'reason': 'Monochrome source'},
        'display': {'pixel_scale': '1:1', 'transfer': 'unchanged nonlinear values', 'residual': 'Rec.709 luminance, red positive / blue negative',
                    'residual_abs_scale': scales}, 'requires_visual_review': True, 'recommended_verdict': 'uncertain',
        'limitations': 'Contribution weight and pixel statistics do not establish star diameter, halo geometry or real structure retention'}
    if prepared.get('star_curve'):
        old,_,_ = prepared_input(session,payload,prepared['star_curve']['baseline'],verify_parent=False)
        old_control = read_scientific_pixels(bound_file(session,old['control']))[0]
        report['star_curve'] = prepared['star_curve']
        report['original_baseline'] = measure_regions(bound_file(session,old['control']),source,regions_path,scientific=True)
        report['baseline_geometry'] = geometry_profiles(a,old_control,c,parse_native_stars(
            bound_file(session,prepared['geometry_catalog']['table']),spatial=True),rows,color=not stellar_color_enabled(payload))
        panel, scales = _roi_panel(old_control,c,b,stars,rows,geometry, labels=('original baseline','new full stars','final','signed stars +/-'))
    if stellar_color_enabled(payload):
        images = {'subject': a, 'full_contribution': c, 'candidate': b}
        if prepared.get('star_curve'):
            images['original_baseline'] = old_control
        report['stellar_color'] = stellar_color_diagnostics(images,parse_native_stars(
            bound_file(session,prepared['geometry_catalog']['table']),spatial=True),rows,geometry)
    if binding.get('contour'):
        report['contour'], control = measure_contour(session, source, primary, metadata, proposal,
            binding, prepared, stars, rows)
        panel, scales = _roi_panel(a, control, b, stars, rows, report['contour']['contour_geometry'],
            labels=('subject', 'contribution only', 'contour candidate', 'raw signed stars +/-'))
        report['contour']['panel_labels'] = ['subject', 'contribution_only', 'candidate', 'raw_signed_stars']
        if stellar_color_enabled(payload):
            images['contribution_only'] = control
            report['stellar_color'] = stellar_color_diagnostics(images, parse_native_stars(
                bound_file(session, prepared['geometry_catalog']['table']), spatial=True), rows, geometry)
    if geometry is not None:
        report['geometry'] = geometry
        report['limitations'] = geometry['limitations']
        bound_file(session, prepared['geometry_catalog']['table'])
    for comparison in (baseline, compared):
        if any(sha256_file(Path(item['path'])) != item['sha256'] for item in comparison['images'].values()):
            fail('Scientific material changed during recomposition diagnostics')
    directory = session/'reports'/binding['run_id']
    report_path, panel_path = directory/'recomposition-diagnostics.json', directory/'recomposition-regions.png'
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        if report_path.exists() or panel_path.exists():
            fail('Recomposition diagnostics already exist; use a new run ID')
        panel_path.write_bytes(panel); atomic_write_json(report_path, report)
    elif load_json(report_path) != report or panel_path.read_bytes() != panel:
        fail('Recomposition diagnostics changed; immutable replay cannot repair evidence')
    return {'status': 'measured', 'report': record(session, report_path), 'panel': record(session, panel_path), 'requires_visual_review': True}


def review_paths(session, run):
    binding = run.get('recomposition_proposal')
    if not binding:
        return {}
    checked = run.get('processing_checks', {}).get('recomposition')
    if not checked:
        fail('Recomposition review requires actual candidate diagnostics')
    prepared = load_json(bound_file(session, binding['prepared']))
    items = [binding['request'], binding['prepared'], binding['regions'], checked['report'], checked['panel'], *prepared['previews'].values()]
    if prepared.get('star_curve'):
        items += [prepared['star_curve']['request'], prepared['star_curve']['baseline']]
        baseline = load_json(bound_file(session,prepared['star_curve']['baseline']))
        items.append(baseline['previews']['full-contribution'])
    if 'geometry_catalog' in prepared:
        items.append(prepared['geometry_catalog']['table'])
    if binding.get('contour'):
        item = run['branch_binding']['contour_report']
        contour = load_json(bound_file(session, item))
        items += [item, contour['preview']]
    return {item['path']: bound_file(session, item) for item in items}


def assessment_skeleton(session, run):
    binding = run.get('recomposition_proposal')
    if not binding:
        return None
    rows = load_json(bound_file(session, binding['regions']))['regions']
    result = {'diagnostics': run['processing_checks']['recomposition']['report'], 'goal_status': None,
            'regions': [{'region': {'regions': binding['regions'], 'id': row['id']}, 'assessment': None, 'observation': ''}
                        for row in rows], 'adoption_reason': ''}
    payload = load_json(session/'session.json')
    if payload.get('execution_policy', {}).get('recomposition_contour') == CONTOUR_POLICY:
        result['contribution_assessment'] = {'assessment': None, 'observation': ''}
    if binding.get('contour'):
        result['contour_assessment'] = {'benefit': None, 'regions': [], 'observation': ''}
    if stellar_color_enabled(payload):
        report = load_json(bound_file(session,result['diagnostics']))
        if report['stellar_color']['status'] != 'not_applicable':
            for row,region in zip(result['regions'],rows):
                if region['role'] == 'stars':
                    row['stellar_checks'] = {key: {'assessment': None, 'observation': ''} for key in STELLAR_CHECKS}
    return result


def validate_assessment(session, run, review):
    try:
        _validate_assessment(session, run, review)
    except (OSError, KeyError, TypeError, AttributeError, ValueError, IndexError) as exc:
        fail('Malformed recomposition assessment: '+type(exc).__name__)


def _validate_assessment(session, run, review):
    block = review.get('recomposition_assessment')
    if not run.get('recomposition_proposal'):
        if 'recomposition_assessment' in review:
            fail('Recomposition assessment requires a proposal-bound candidate')
        return
    expected = assessment_skeleton(session, run)
    if (not isinstance(block, dict) or set(block) != set(expected) or block['diagnostics'] != expected['diagnostics']
        or block['goal_status'] not in ('met', 'not_met', 'uncertain') or not valid_text(block['adoption_reason'])
        or not isinstance(block['regions'], list) or len(block['regions']) != len(expected['regions'])):
        fail('Assess the actual recomposition goal and every bound subject/star region')
    if 'contribution_assessment' in expected:
        value = block['contribution_assessment']
        if (not isinstance(value, dict) or set(value) != {'assessment', 'observation'}
            or value['assessment'] not in ('acceptable', 'degraded', 'uncertain') or not valid_text(value['observation'])):
            fail('Assess contribution balance separately from stellar contour')
        if review['verdict'] == 'accept' and value['assessment'] != 'acceptable':
            fail('Accept only an acceptable contribution balance')
    if 'contour_assessment' in expected:
        value = block['contour_assessment']
        if (not isinstance(value, dict) or set(value) != {'benefit', 'regions', 'observation'}
            or value['benefit'] not in ('observed', 'not_observed', 'uncertain')
            or not isinstance(value['regions'], list) or any(not isinstance(v, str) for v in value['regions'])
            or len(set(value['regions'])) != len(value['regions']) or not valid_text(value['observation'])):
            fail('Assess actual contour benefit and its fixed stellar regions')
        diagnostics = load_json(bound_file(session, block['diagnostics']))
        measurable = {row['region_id'] for row in diagnostics['contour']['contour_geometry']['regions']
                      if row['status'] in ('measured', 'partial') and any(
                          ratio is not None and 0 < ratio < 1 for ratio in row['comparisons'].values())}
        if not set(value['regions']) <= measurable:
            fail('Contour benefit must cite fixed regions with measurable FWHM or r90 contraction')
        if review['verdict'] == 'accept' and (value['benefit'] != 'observed' or not value['regions']):
            fail('Accept contour only with observed benefit and measurable contraction')
    for row, required in zip(block['regions'], expected['regions']):
        if (not isinstance(row, dict) or set(row) != set(required) or row['region'] != required['region']
            or row['assessment'] not in ('acceptable', 'degraded', 'uncertain') or not valid_text(row['observation'])):
            fail('Record actual subject, stellar-core, halo and color observations in each protected ROI')
        if 'stellar_checks' in required:
            checks = row['stellar_checks']
            if not isinstance(checks,dict) or set(checks) != set(STELLAR_CHECKS):
                fail('Assess stellar core, color, halo and endpoints separately')
            for value in checks.values():
                if (not isinstance(value,dict) or set(value) != {'assessment','observation'}
                    or value['assessment'] not in ('acceptable','degraded','uncertain') or not valid_text(value['observation'])):
                    fail('Each stellar check requires an assessment and actual observation')
                if review['verdict'] == 'accept' and value['assessment'] != 'acceptable':
                    fail('Accept recomposition only when all stellar checks are acceptable')
    if not set(review_paths(session, run)) <= {item['path'] for item in review['inspected_materials']}:
        fail('Recomposition review is missing its actual proposal, prepared control or regional diagnostics')
    if review['verdict'] == 'accept' and (block['goal_status'] != 'met'
        or any(row['assessment'] != 'acceptable' for row in block['regions'])):
        fail('Accept recomposition only when the explicit goal is met and all protected regions are acceptable')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['compute', 'prepare', 'analyze', 'generate'], nargs='?', default='compute')
    parser.add_argument("--session", required=True, help="Session directory path")
    parser.add_argument('--star-request', type=Path, help='Explicit star curve bound to an existing prepared baseline')
    parser.add_argument('--source', type=Path, help='Accepted nonlinear starless primary for prepare')
    parser.add_argument('--strength', type=float, help='Legacy compute reference override only')
    parser.add_argument('--request', type=Path)
    parser.add_argument('--run-id')
    parser.add_argument('--prepared', type=Path)
    parser.add_argument('--regions', type=Path, help='Current subject ROIs; select one isolated catalog star per geometry ROI')
    parser.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args()
    session_dir = Path(args.session)
    try:
        if args.action == 'compute':
            res = inspect_session(session_dir)
            res.update(role='legacy_heuristic_reference', authorizes_generation=False)
            if args.strength is not None:
                if not math.isfinite(args.strength) or not PROTOCOL_MIN_STRENGTH <= args.strength <= PROTOCOL_MAX_STRENGTH:
                    fail('Invalid reference contribution')
                res['strength'] = args.strength; res['diagnostics']['override'] = True
        elif args.action == 'prepare':
            if args.source is None or args.run_id is None:
                parser.error('prepare requires --source and --run-id')
            res = prepare(session_dir, args.source, args.run_id, args.timeout, star_request=args.star_request)
        elif args.action == 'generate':
            if args.request is None or args.run_id is None or args.source is not None or args.strength is not None:
                parser.error('generate requires --request and --run-id; migrate --source/--strength into a prepared, evidence-bound proposal')
            res = generate_recompose_assets(session_dir, request=args.request, run_id=args.run_id)
        else:
            if args.prepared is None or args.regions is None:
                parser.error('analyze requires --prepared and --regions')
            from deep_sky_siril_session import load_session, effective_session_payload
            session, payload, manifest = load_session(session_dir)
            payload = effective_session_payload(session, payload, manifest)
            res, _, _ = analyze(session, payload,
                record(session, bound_path(session, args.prepared, must_exist=True, allowed_roots=('reports',))),
                record(session, bound_path(session, args.regions, must_exist=True, allowed_roots=('reports',))))
        print(json.dumps(res, indent=2, allow_nan=False))
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == "__main__":
    main()
