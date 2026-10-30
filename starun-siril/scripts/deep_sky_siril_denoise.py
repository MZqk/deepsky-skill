#!/usr/bin/env python3
"""Agent-proposed linear denoise with read-only scientific diagnostics."""
from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path
import re

import numpy as np

from deep_sky_siril_artifacts import read_fits_header, read_scientific_pixels
from deep_sky_siril_contract import (
    ContractError, RUN_ID_PATTERN, atomic_write_json, fingerprint, load_json,
    protocol_reference_bindings, session_path, sha256_file,
)
from deep_sky_siril_metrics import measure_regions
from deep_sky_siril_stretch import bound_file, record, source_file

POLICY = 'agent_proposal_v1'
SCHEMA = 'starun-siril.linear-denoise-request.v1'
SCHEMA_V2 = 'starun-siril.linear-denoise-request.v2'
BACKEND_POLICY = 'deepsnr_primary_v1'
ROOT = Path(__file__).resolve().parents[1]
SCALES = (1, 2, 4, 8)
VIEW_MIN, VIEW_MAX, SLICE, ZOOM_MAX = 300, 1024, 512, 8
HIGHLIGHT_TILE, HIGHLIGHT_CROP, HIGHLIGHT_ZOOM = 32, 40, 3
HIGHLIGHT_TILES, HIGHLIGHT_PIXELS = 256, 1024
HIGHLIGHT_COLUMNS = 8
REVIEW_MATERIALS = 'starun-siril.denoise-review-materials.v1'


def fail(message):
    raise ContractError('linear_denoise_invalid', message)


def text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 2000


def display_parameters(values):
    if (not isinstance(values, list) or len(values) != 3
        or any(type(v) not in (int, float) or not np.isfinite(v) for v in values)
        or not (0 <= values[0] < values[2] <= 1 and 0 < values[1] < 1)):
        fail('Provide the same explicit low/mid/high display MTF for both images')
    return values


def operator(parameters):
    if (not isinstance(parameters, dict) or set(parameters) != {'modulation', 'cosmetic'}
        or type(parameters['modulation']) not in (int, float)
        or not np.isfinite(parameters['modulation']) or not 0 <= parameters['modulation'] <= 1
        or type(parameters['cosmetic']) is not bool):
        fail('Explicit modulation in [0, 1] and a cosmetic boolean are required; no defaults')
    if parameters['modulation'] == 0:
        return None
    return ['denoise', *([] if parameters['cosmetic'] else ['-nocosmetic']),
            '-mod='+format(parameters['modulation'], '.17g')]


def source_context(session, payload, source, context=None, *, verify_parent=True):
    if context is not None:
        from deep_sky_siril_dualband import prepared_input
        prepared, _ = prepared_input(session, payload, context, verify_parent=verify_parent)
        if source != {k: prepared['derived_sources']['oiii'][k] for k in ('path', 'sha256')}:
            fail('The proposal must bind the actual prepared, undenoised OIII input')
        path = bound_file(session, source)
    else:
        path = source_file(session, payload, source)
        if verify_parent:
            from deep_sky_siril_processing import source_state
            from deep_sky_siril_core import _accepted_review
            state = source_state(session, payload, path)
            if state['domain'] != 'linear':
                fail('Linear denoise requires a trusted linear parent')
            ancestors = [*state.get('lineage', []), *([state['run']] if state.get('run') else [])]
            if any(row.get('protocol') == 'restoration.denoise' for row in ancestors):
                fail('Do not accumulate linear denoise; revise from the original trusted parent')
            if state.get('run'):
                _accepted_review(session, state['run']['id'])
    return path


def analyze_source(session, payload, source, regions, display_mtf, context=None, *, verify_parent=True):
    display_parameters(display_mtf)
    path = source_context(session, payload, source, context, verify_parent=verify_parent)
    region_path = bound_file(session, regions)
    measured = measure_regions(path, None, region_path, scientific=True)
    if measured['status'] != 'measured':
        fail('Finite, geometry-bound scientific ROIs are required: '+measured.get('reason', measured['status']))
    rows = load_json(region_path)['regions']
    values, selection = read_scientific_pixels(path)
    if not np.isfinite(values).all() or selection.get('bounds') not in (None, [0., 1.]):
        fail('Finite scientific pixels and unit XISF bounds are required')
    return {'schema': 'starun-siril.linear-denoise-analysis.v1', 'source': source,
            'scientific_source': selection, 'regions': regions, 'display_mtf': display_mtf,
            'measurements': measured, 'multiscale': multiscale(values, rows),
            'interpretation': 'Diagnostics only; neither noise strength nor true texture is identified automatically'}, path


def request_binding(session, payload, request_path, *, verify_parent=True):
    path = session_path(session, request_path, must_exist=True, allowed_roots=('reports',))
    request = load_json(path)
    required = {'schema', 'source', 'regions', 'display_mtf', 'decision', 'reason', 'manual_lookup'}
    v2 = request.get('schema') == SCHEMA_V2 if isinstance(request, dict) else False
    optional = {'parameters', 'parameter_reason', 'derived_context', 'revision'}
    if v2:
        optional |= {'backend', 'fallback'}
    if (not isinstance(request, dict) or not required <= set(request) <= required | optional
        or request['schema'] not in (SCHEMA, SCHEMA_V2) or request['decision'] not in ('trial', 'skip') or not text(request['reason'])):
        fail('Invalid linear denoise proposal fields or decision')
    if v2 and (request.get('backend') not in ('deepsnr', 'nlbayes') or request.get('derived_context')):
        fail('Stage 5 v2 requires an explicit backend and cannot change HOO/OIII')
    if (not request.get('derived_context') and payload.get('execution_policy', {}).get('linear_denoise_backend') == BACKEND_POLICY and not v2):
        fail('New Stage 5 execution requires a v2 backend proposal; v1 is reserved for historical replay and HOO')
    backend = request.get('backend', 'nlbayes')
    if backend == 'deepsnr' and 'fallback' in request:
        fail('Only NL-Bayes proposals may declare fallback evidence')
    if backend == 'deepsnr' and 'parameters' in request:
        from siril_deepsnr import validate_parameters
        validate_parameters(request['parameters'])
        command = ['pyscript'] if request['parameters']['modulation'] else None
    else:
        command = operator(request['parameters']) if 'parameters' in request else None
    if request['decision'] == 'trial' and ('parameters' not in request or not text(request.get('parameter_reason'))):
        fail('A trial requires explicit modulation/cosmetic parameters and their observation-based rationale')
    analysis, source = analyze_source(session, payload, request['source'], request['regions'], request['display_mtf'],
                                      request.get('derived_context'), verify_parent=verify_parent)
    if v2 and backend == 'nlbayes' and command is not None and request['decision'] == 'trial':
        validate_fallback(session, payload, request)
    regions = analysis['measurements']['regions']
    if request['decision'] == 'trial' and command is not None and (
        not any(r['role'] == 'background' for r in regions) or not any(r['role'] in {'faint', 'core'} for r in regions)):
        fail('A trial requires credible background and at least one protected subject region')
    revision = request.get('revision')
    if revision is not None:
        if (not isinstance(revision, dict) or set(revision) != {'request', 'run_id', 'evidence', 'reason'}
            or not text(revision['reason']) or not isinstance(revision['evidence'], list) or not revision['evidence']
            or not isinstance(revision['run_id'], str) or not RUN_ID_PATTERN.fullmatch(revision['run_id'])):
            fail('A revision requires the prior proposal/run and actual observation evidence')
        prior_path = bound_file(session, revision['request'])
        if prior_path == path:
            fail('A revision must bind a distinct prior proposal')
        prior = load_json(prior_path)
        receipt_path = session_path(session, 'runs/'+revision['run_id']+'.json', must_exist=True, allowed_roots=('runs',))
        if verify_parent:
            from deep_sky_siril_core import _load_run
            _, receipt = _load_run(session, revision['run_id'])
        else:
            receipt = load_json(receipt_path)
            entries = [r for r in load_json(session/'manifest.json').get('runs', []) if r.get('id') == revision['run_id']]
            if (len(entries) != 1 or entries[0].get('status') != 'success' or receipt.get('status') != 'success'
                or entries[0].get('receipt') != receipt_path.name or entries[0].get('receipt_sha256') != sha256_file(receipt_path)):
                fail('Revision observation run is not immutable successful evidence')
        previous = run_binding(receipt)
        if (prior.get('source') != request['source'] or previous is None
            or previous['request'] != revision['request'] or receipt.get('id') != revision['run_id']):
            fail('Revisions must start from the same trustworthy, undenoised parent')
        inspected = set(review_paths(session, receipt)) | set(material_records(diagnostics(receipt)))
        for item in revision['evidence']:
            bound_file(session, item, ('reports', 'previews', 'runs', 'reviews', 'artifacts'))
            if item['path'] not in inspected:
                fail('Revision evidence must belong to the prior candidate diagnostics or review materials')
    binding = {'policy': POLICY, 'request': record(session, path), 'source': request['source'],
               'regions': request['regions'], 'scientific_source': analysis['scientific_source'],
               'display_mtf': request['display_mtf'], 'parameters': request.get('parameters'),
               'helper': fingerprint(Path(__file__), include_path=False)}
    if not verify_parent:
        from deep_sky_siril_stretch_v2 import _historical_bindings
        historical = _historical_bindings(session)
        if historical:
            binding['helper'] = historical['implementation'].get(Path(__file__).name, binding['helper'])
    if 'derived_context' in request:
        binding['derived_context'] = request['derived_context']
    if v2:
        binding['backend'] = backend
        if 'fallback' in request:
            binding['fallback'] = request['fallback']
    return request, binding, source, command if request['decision'] == 'trial' else None


def validate_fallback(session, payload, request):
    fallback = request.get('fallback')
    if (not isinstance(fallback, dict) or set(fallback) != {'reason', 'evidence'}
        or not text(fallback['reason']) or not isinstance(fallback['evidence'], list) or not fallback['evidence']):
        fail('NL-Bayes requires an explicit fallback reason and actual DeepSNR evidence')
    qualified = False
    for item in fallback['evidence']:
        path = bound_file(session, item, ('reports', 'runs', 'reviews'))
        evidence = load_json(path)
        if item['path'] == 'reports/tool-probe.json':
            if payload.get('tool_probe', {}).get('sha256') != item['sha256']:
                fail('Fallback probe differs from the frozen session probe')
            qualified |= evidence.get('tools', {}).get('deepsnr', {}).get('compatible') is False
        elif evidence.get('schema') == 'starun-siril.deepsnr-applicability.v1':
            qualified |= (evidence.get('source') == request['source']
                          and evidence.get('decision') == 'inapplicable' and text(evidence.get('reason')))
        elif path.parent == session/'reviews':
            from deep_sky_siril_core import _load_run, _validate_review
            _, run = _load_run(session, path.stem)
            _validate_review(session, str(path), path.stem)
            qualified |= (run.get('linear_denoise', {}).get('backend') == 'deepsnr'
                          and run['linear_denoise']['source'] == request['source']
                          and evidence.get('verdict') in ('reject', 'uncertain'))
    if not qualified:
        fail('Fallback needs a frozen unavailable probe, same-source applicability assessment or reviewed DeepSNR rejection')


def declaration(value):
    rows = [row.strip() for row in value.splitlines() if row.strip().startswith('# linear-denoise')]
    if len(rows) != 1 or not rows[0].startswith('# linear-denoise:'):
        fail('Exactly one hash-bound linear-denoise proposal declaration is required')
    from siril_manual_bundle import strict_json_bytes
    return strict_json_bytes(rows[0].split(':', 1)[1].encode(), document='linear denoise declaration')


def prepare(session, payload, source, value, commands, restoration, run_id, *, verify_parent=True):
    declared = declaration(value)
    path = bound_file(session, declared)
    request, binding, parent, command = request_binding(session, payload, path, verify_parent=verify_parent)
    if binding.get('backend') == 'deepsnr':
        from siril_deepsnr import validate_contract
        rows = [row for row in commands if row[0] == 'pyscript']
        if len(rows) != 1 or len(rows[0]) != 10:
            fail('DeepSNR requires exactly one pinned bridge')
        row = rows[0]
        contract_path = Path(row[3]); receipt_path = Path(row[9])
        runtime = load_json(session/'reports/tool-probe.json')['tools']['deepsnr']
        document = load_json(contract_path)
        validate_contract(document, source, runtime, session, run_id, replaying=not verify_parent)
        adapter = str(ROOT/'scripts/siril_deepsnr.py') if verify_parent else runtime['adapter']['path']
        if (row != ['pyscript', adapter, '--contract', str(contract_path),
                    '--contract-sha256', sha256_file(contract_path), '--expected-source', str(source), '--receipt', str(receipt_path)]
            or contract_path.parent != session/'reports'/run_id or receipt_path != contract_path.parent/'bridge.json'
            or document['parameters'] != binding['parameters'] or parent != source
            or restoration['display']['transfer']['parameters'] != binding['display_mtf']
            or request.get('revision', {}).get('run_id') == run_id
            or command is None or declared != binding['request']):
            fail('DeepSNR bridge differs from the actual proposal/source/parameters')
        return {**binding, 'run_id': run_id, 'display': restoration['display'],
                'runtime': runtime, 'contract': record(session, contract_path),
                'bridge_receipt': str(receipt_path.relative_to(session))}
    actual = [row for row in commands if row[0] == 'denoise']
    if len(actual) == 1:
        try:
            actual = [['-mod='+format(float(token.split('=', 1)[1]), '.17g') if token.startswith('-mod=') else token
                       for token in row] for row in actual]
        except ValueError:
            fail('Invalid denoise modulation')
    if (declared != binding['request'] or parent != source or command is None
        or actual != [command]
        or restoration['display']['transfer']['parameters'] != binding['display_mtf']):
        fail('Actual source, denoise command or same-display MTF differs from the Agent proposal')
    if request.get('derived_context'):
        fail('Prepared OIII proposals execute through HOO v4')
    if request.get('revision', {}).get('run_id') == run_id:
        fail('A revision requires a new run ID')
    return {**binding, 'run_id': run_id, 'display': restoration['display']}


def multiscale(values, regions):
    from deep_sky_siril_local_contrast import energies
    height, width = values.shape[1:]
    rows = []
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        for sigma in SCALES:
            available = min(x0, y0, width-x1, height-y1) >= 4*sigma
            row = {'id': region['id'], 'role': region['role'], 'sigma': sigma,
                   'amplitude': None, 'status': 'unmeasurable', 'reason': 'Insufficient real 4-sigma context'}
            if available:
                row.update(amplitude=energies(values, [{**region, 'role': 'faint'}], (sigma,))[0]['amplitude'],
                           status='measured', reason=None)
            rows.append(row)
    return rows


def _display(values, transfer):
    low, mid, high = transfer
    x = np.clip((values-low)/(high-low), 0, 1)
    return (mid-1)*x/((2*mid-1)*x-mid)


def _slug(value):
    return re.sub(r'[^A-Za-z0-9._-]+', '-', str(value)).strip('-') or 'region'


def _zoom(width, height):
    """Integer magnification that brings a small ROI to a reviewable size."""
    return min(ZOOM_MAX, max(1, -(-VIEW_MIN // min(width, height))))


def _crop(values, rect):
    x0, y0, x1, y1 = rect
    return values[:, values.shape[1]-y1:values.shape[1]-y0, x0:x1][:, ::-1]


def _luminance(crop):
    return crop[0] if crop.shape[0] == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], crop)


def _repeat(array, zoom):
    return np.repeat(np.repeat(array, zoom, axis=0), zoom, axis=1) if zoom > 1 else array


def _tone(crop, transfer, zoom):
    display = _display(crop, transfer)
    rgb = np.repeat(display, 3, axis=0) if display.shape[0] == 1 else display
    return _repeat(np.rint(np.clip(np.moveaxis(rgb, 0, -1), 0, 1)*255).astype('uint8'), zoom)


def _signed(lum, scale, zoom):
    """Gray is zero; red removes signal, blue adds it, at an explicit absolute scale."""
    signed = np.zeros((*lum.shape, 3))+.5
    if scale:
        signed[..., 0] += lum/(2*scale)
        signed[..., 2] -= lum/(2*scale)
    return _repeat(np.rint(np.clip(signed, 0, 1)*255).astype('uint8'), zoom)


def _scale_text(value):
    return format(float(value), '.6g')


def _compose(tiles, labels, title, meta):
    from PIL import Image, ImageDraw
    height, width = tiles[0].shape[:2]
    header = 44
    image = Image.new('RGB', (width*len(tiles), height+header), '#202020')
    draw = ImageDraw.Draw(image)
    draw.text((2, 2), title, fill='white')
    draw.text((2, 16), meta, fill='#c8c8c8')
    for index, (tile, label) in enumerate(zip(tiles, labels)):
        image.paste(Image.fromarray(tile), (index*width, header))
        draw.text((index*width+2, header-12), label, fill='#e8e8e8')
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue()


def _window(center_x, center_y, width, height, size):
    x0 = int(np.clip(center_x-size//2, 0, max(0, width-size)))
    y0 = int(np.clip(center_y-size//2, 0, max(0, height-size)))
    return [x0, y0, min(width, x0+size), min(height, y0+size)]


def _region_file(before, after, region, transfer, common, *, zoom, name, title):
    """One region at native detail: parent, candidate and both signed residual scales."""
    crop_before, crop_after = _crop(before, region['rect']), _crop(after, region['rect'])
    lum = _luminance(crop_before-crop_after)
    own = float(np.max(np.abs(lum))) if lum.size else 0.
    tiles = [_tone(crop_before, transfer, zoom), _tone(crop_after, transfer, zoom), _signed(lum, common, zoom)]
    labels = ['parent', 'candidate', 'residual common']
    if own != common:
        tiles.append(_signed(lum, own, zoom)); labels.append('residual region')
    meta = ('rect=[{0[0]},{0[1]},{0[2]},{0[3]}] zoom={1}x mtf=[{2}] '
            'residual red=parent-candidate>0 blue=<0 common={3} region={4}').format(
                region['rect'], zoom, ', '.join(_scale_text(v) for v in transfer), _scale_text(common), _scale_text(own))
    return {'name': name, 'data': _compose(tiles, labels, title, meta), 'zoom': zoom, 'scale': own}


def _region_materials(before, after, regions, transfer):
    """Per-region lossless views plus native 1:1 slices for ROIs above the view cap."""
    material, scales = [], []
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        zoom = _zoom(x1-x0, y1-y0)
        material.append((region, zoom))
        scales.append(float(np.max(np.abs(_luminance(_crop(before, region['rect'])-_crop(after, region['rect']))))))
    common = max(scales) if scales else 0.
    files, rows = [], []
    for (region, zoom), own in zip(material, scales):
        title = 'region %s (%s)' % (region['id'], region['role'])
        view = _region_file(before, after, region, transfer, common,
                            zoom=zoom, name='denoise-region-'+_slug(region['id'])+'.png', title=title)
        files.append((view['name'], view['data']))
        width, height = region['rect'][2]-region['rect'][0], region['rect'][3]-region['rect'][1]
        slices = []
        if max(width, height) > VIEW_MAX:
            for row, offset_y in enumerate(range(0, height, SLICE)):
                for column, offset_x in enumerate(range(0, width, SLICE)):
                    rect = [region['rect'][0]+offset_x, region['rect'][1]+offset_y,
                            min(region['rect'][2], region['rect'][0]+offset_x+SLICE),
                            min(region['rect'][3], region['rect'][1]+offset_y+SLICE)]
                    slice_name = 'denoise-region-%s-slice-%d-%d.png' % (_slug(region['id']), row, column)
                    sliced = _region_file(before, after, {**region, 'rect': rect}, transfer, common, zoom=1,
                                          name=slice_name, title='region %s slice %d-%d' % (region['id'], row, column))
                    files.append((sliced['name'], sliced['data']))
                    slices.append({'name': slice_name, 'rect': rect, 'zoom': 1, 'scale': sliced['scale']})
        rows.append({'id': region['id'], 'role': region['role'], 'rect': region['rect'], 'zoom': zoom,
                     'native': zoom == 1, 'common_scale': common, 'region_scale': own, 'slices': slices,
                     'name': view['name']})
    return files, rows, common


def _highlight_materials(before, after, transfer):
    """Index every sample at or above one with its location and a magnified star-core comparison."""
    files, rows = [], []
    width, height = before.shape[2], before.shape[1]
    for channel in range(before.shape[0]):
        parent, candidate = before[channel], after[channel]
        mask = (parent >= 1) | (candidate >= 1)
        count = int(np.count_nonzero(mask))
        row = {'channel': channel, 'coordinate_system': 'display_top_down', 'count': count,
               'parent_count': int(np.count_nonzero(parent >= 1)),
               'candidate_count': int(np.count_nonzero(candidate >= 1)),
               'bbox': None, 'pixels': [], 'pixels_truncated': False,
               'tiles': 0, 'tiles_truncated': False, 'scale': None, 'name': None}
        if not count:
            rows.append(row); continue
        storage_y, xs = np.nonzero(mask)
        ys = height-1-storage_y
        row['bbox'] = [int(xs.min()), int(ys.min()), int(xs.max())+1, int(ys.max())+1]
        order = np.lexsort((xs, ys)); ys, xs, storage_y = ys[order], xs[order], storage_y[order]
        row['pixels'] = [{'x': int(xs[i]), 'y': int(ys[i]),
                          'parent': float(parent[storage_y[i], xs[i]]), 'candidate': float(candidate[storage_y[i], xs[i]])}
                         for i in range(min(count, HIGHLIGHT_PIXELS))]
        row['pixels_truncated'] = count > HIGHLIGHT_PIXELS
        _, inverse = np.unique((ys//HIGHLIGHT_TILE)*10**6+(xs//HIGHLIGHT_TILE), return_inverse=True)
        total = int(inverse.max())+1
        row['tiles'], row['tiles_truncated'] = total, total > HIGHLIGHT_TILES
        row['scale'] = float(np.max(np.abs(_luminance((parent-candidate)[None]))))
        cells = []
        for group in range(min(total, HIGHLIGHT_TILES)):
            selected = inverse == group
            rect = _window(int(xs[selected].mean()), int(ys[selected].mean()), width, height, HIGHLIGHT_CROP)
            parent_values = parent[storage_y[selected], xs[selected]]
            candidate_values = candidate[storage_y[selected], xs[selected]]
            cells.append((rect, int(np.count_nonzero(selected)), float(parent_values.max()),
                          float(candidate_values.max()), float(np.max(np.abs(parent_values-candidate_values)))))
        name = 'denoise-highlight-%d.png' % channel
        files.append((name, _highlight_montage(before, after, transfer, cells, row['scale'],
                                               'channel %d samples >= 1: %d (%d tiles%s)' % (
                                                   channel, count, total, ', truncated' if row['tiles_truncated'] else ''))))
        row['name'] = name
        rows.append(row)
    return files, rows


def _highlight_montage(before, after, transfer, cells, scale, title):
    from PIL import Image, ImageDraw
    columns = min(HIGHLIGHT_COLUMNS, max(1, len(cells)))
    rows = -(-len(cells)//columns)
    side = HIGHLIGHT_CROP*HIGHLIGHT_ZOOM
    cell_width, cell_height = 3*side+6, side+14
    image = Image.new('RGB', (columns*cell_width+6, rows*cell_height+34), '#202020')
    draw = ImageDraw.Draw(image)
    draw.text((2, 2), title, fill='white')
    draw.text((2, 16), 'red=parent-candidate>0 blue=<0 scale='+_scale_text(scale), fill='#c8c8c8')
    for index, (rect, samples, parent_peak, candidate_peak, cell_scale) in enumerate(cells):
        column, row = index % columns, index//columns
        left, top = 3+column*cell_width, 32+row*cell_height
        tiles = [_tone(_crop(before, rect), transfer, HIGHLIGHT_ZOOM),
                 _tone(_crop(after, rect), transfer, HIGHLIGHT_ZOOM),
                 _signed(_luminance(_crop(before, rect)-_crop(after, rect)), scale, HIGHLIGHT_ZOOM)]
        draw.text((left, top), 'x%d y%d n%d p%.4g c%.4g r%.3g' % (
            rect[0], rect[1], samples, parent_peak, candidate_peak, cell_scale), fill='#e8e8e8')
        for offset, tile in enumerate(tiles):
            image.paste(Image.fromarray(tile), (left+offset*(side+3), top+12))
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue()


def _index_map(before, regions, highlights, transfer):
    """Full-frame locator; per-region files carry the detail."""
    from PIL import Image, ImageDraw
    height, width = before.shape[1], before.shape[2]
    lum = _display(_luminance(before)[None], transfer)[0]
    step = max(1, -(-max(width, height)//VIEW_MAX))
    rows = np.rint(np.clip(lum[::step, ::step], 0, 1)*255).astype('uint8')[::-1]
    image = Image.fromarray(np.repeat(rows[..., None], 3, axis=-1))
    draw = ImageDraw.Draw(image)
    for region in regions:
        x0, y0, x1, y1 = region['rect']
        box = [x0//step, y0//step, x1//step, y1//step]
        draw.rectangle(box, outline='#ffd400')
        draw.text((box[0]+2, max(0, box[1]+1)), _slug(region['id']), fill='#ffd400')
    for row in highlights:
        if row['bbox']:
            x0, y0, x1, y1 = row['bbox']
            draw.rectangle([max(0, x0//step-1), max(0, y0//step-1), x1//step+1, y1//step+1], outline='#ff40ff')
    draw.text((2, 2), 'full-frame index (parent, same MTF, %dx downsample)%s' % (
        step, '  magenta = samples >= 1' if any(row['bbox'] for row in highlights) else ''), fill='white')
    output = BytesIO(); image.save(output, format='PNG')
    return output.getvalue(), step


def _review_materials(before, after, regions, transfer):
    """Per-region lossless views, a full-frame locator and sample-level highlight index."""
    files, region_rows, common = _region_materials(before, after, regions, transfer)
    highlight_files, highlight_rows = _highlight_materials(before, after, transfer)
    index, step = _index_map(before, regions, highlight_rows, transfer)
    return {'files': [('denoise-index.png', index), *files, *highlight_files],
            'index': {'name': 'denoise-index.png', 'downsample': step},
            'common_scale': common, 'regions': region_rows, 'highlights': highlight_rows}


def _bind_review_materials(session, directory, material):
    """Bind the actual path, hash and size of every material the review must inspect."""

    def bound(name):
        return record(session, directory/name)

    regions = [{'id': row['id'], 'role': row['role'], 'rect': row['rect'], 'zoom': row['zoom'],
                'native': row['native'],
                'residual_scales': {'common': row['common_scale'], 'region': row['region_scale']},
                'view': bound(row['name']),
                'slices': [{key: value for key, value in sliced.items() if key != 'name'} | {'view': bound(sliced['name'])}
                           for sliced in row['slices']]} for row in material['regions']]
    highlights = [{key: value for key, value in row.items() if key != 'name'}
                  | {'view': bound(row['name']) if row['name'] else None} for row in material['highlights']]
    return {'schema': REVIEW_MATERIALS, 'coordinate_system': 'display_top_down',
            'index': {'view': bound(material['index']['name']), 'downsample': material['index']['downsample'],
                      'long_edge': VIEW_MAX, 'common_residual_scale': material['common_scale']},
            'regions': regions, 'highlights': highlights,
            'limits': {'zoom_max': ZOOM_MAX, 'view_max': VIEW_MAX, 'slice': SLICE,
                       'highlight_pixels': HIGHLIGHT_PIXELS, 'highlight_tiles': HIGHLIGHT_TILES}}


def _panel(before, after, regions, transfer):
    from PIL import Image, ImageDraw
    height = before.shape[1]
    widths = [r['rect'][2]-r['rect'][0] for r in regions]
    heights = [r['rect'][3]-r['rect'][1] for r in regions]
    panel = Image.new('RGB', (3*max(widths), sum(h+24 for h in heights)), '#202020')
    draw, top, scales = ImageDraw.Draw(panel), 0, {}
    for row, width, h in zip(regions, widths, heights):
        x0, y0, x1, y1 = row['rect']
        a, b = [v[:, height-y1:height-y0, x0:x1][:, ::-1] for v in (before, after)]
        residual = a-b
        lum = residual[0] if residual.shape[0] == 1 else np.einsum('c,cyx->yx', [.2126, .7152, .0722], residual)
        scale = float(np.max(np.abs(lum)))
        scales[row['id']] = scale
        signed = np.zeros((h, width, 3))+.5
        if scale:
            signed[:, :, 0] += lum/(2*scale)
            signed[:, :, 2] -= lum/(2*scale)
        images = []
        for crop in (a, b):
            rgb = np.repeat(crop, 3, axis=0) if crop.shape[0] == 1 else crop
            images.append(np.moveaxis(_display(rgb, transfer), 0, -1))
        images.append(signed)
        for index, (data, label) in enumerate(zip(images, ('parent', 'candidate', 'removed +/-'))):
            panel.paste(Image.fromarray(np.rint(np.clip(data, 0, 1)*255).astype('uint8')), (index*max(widths), top+24))
            draw.text((index*max(widths), top+3), row['id']+' '+label, fill='white')
        top += h+24
    output = BytesIO(); panel.save(output, format='PNG')
    return output.getvalue(), scales


def measure(session, parent, candidate, binding, *, write=False):
    from deep_sky_siril_session import registered_guidance_reference
    helper_ok = binding.get('helper') == fingerprint(Path(__file__), include_path=False)
    if not helper_ok and not write:
        helper_ok = registered_guidance_reference(load_json(session/'session.json'),
            {'path': 'scripts/deep_sky_siril_denoise.py', **binding.get('helper', {})})
    if binding.get('policy') != POLICY or not helper_ok:
        fail('Linear denoise implementation or policy changed')
    request = load_json(bound_file(session, binding['request']))
    for key in ('source', 'regions', 'parameters', 'display_mtf', *(['backend', 'fallback'] if 'backend' in binding else [])):
        if request.get(key) != binding.get(key):
            fail('Linear denoise proposal drift: '+key)
    expected_parent = (Path(load_json(session/'session.json')['input']['path']) if binding['source']['path'] == '@input'
                       else session/binding['source']['path'])
    if sha256_file(expected_parent) != binding['source']['sha256']:
        fail('Linear denoise parent pixels changed')
    regions_path = bound_file(session, binding['regions'])
    bridge_check = None
    if binding.get('backend') == 'deepsnr':
        from siril_deepsnr import validate_receipt
        bridge_check = validate_receipt(session, expected_parent, candidate, binding)
    regional = measure_regions(candidate, expected_parent, regions_path, scientific=True)
    if regional['status'] != 'measured':
        fail('Linear denoise regional measurements are unavailable')
    a, selection = read_scientific_pixels(expected_parent); b, _ = read_scientific_pixels(candidate)
    actual, actual_selection = (a, selection) if parent == expected_parent else read_scientific_pixels(parent)
    if (actual.shape != a.shape or not np.isfinite(actual).all()
        or (np.max(np.abs(actual-a)) > 1e-7 if binding.get('derived_context') else sha256_file(parent) != binding['source']['sha256'])):
        fail('Actual denoise parent differs from the proposal input')
    del actual
    if (selection != binding['scientific_source'] or a.shape != b.shape
        or read_fits_header(candidate).get('BITPIX') != -32
        or not np.isfinite(a).all() or not np.isfinite(b).all()):
        fail('Linear denoise scientific selection, geometry or finite pixels changed')
    regions = load_json(regions_path)['regions']
    directory = session/'reports'/binding['run_id']
    report_path, panel_path = directory/'denoise-diagnostics.json', directory/'denoise-regions.png'
    manifest_path = directory/'denoise-review-materials.json'
    legacy = not write and report_path.exists() and 'review_materials' not in load_json(report_path)
    panel, scales = _panel(a, b, regions, binding['display_mtf'])
    material = None if legacy else _review_materials(a, b, regions, binding['display_mtf'])
    changes = []
    height = a.shape[1]
    for row in regions:
        x0, y0, x1, y1 = row['rect']
        residual = a[:, height-y1:height-y0, x0:x1]-b[:, height-y1:height-y0, x0:x1]
        changes.append({'id': row['id'], 'role': row['role'], 'removed_channels': [
            {'median': float(np.median(v)), 'mad': float(np.median(np.abs(v-np.median(v)))),
             'min': float(v.min()), 'max': float(v.max())} for v in residual]})
    before_scales, after_scales = multiscale(a, regions), multiscale(b, regions)
    for left, right in zip(before_scales, after_scales):
        left.update(candidate_amplitude=right['amplitude'], change=None if left['amplitude'] is None
                    else right['amplitude']-left['amplitude'])
    regional_changes = [{'id': row['id'], 'role': row['role'], 'channels': [
        {'channel': i, 'parent_mad': x['mad'], 'candidate_mad': y['mad'], 'mad_change': y['mad']-x['mad'],
         'median_change': y['median']-x['median']}
        for i, (x, y) in enumerate(zip(row['parent']['channels'], row['candidate']['channels']))]}
        for row in regional['regions']]
    endpoints = [{'channel': i, 'parent_nonpositive': int(np.sum(x <= 0)), 'candidate_nonpositive': int(np.sum(y <= 0)),
                  'parent_highlight': int(np.sum(x >= 1)), 'candidate_highlight': int(np.sum(y >= 1)),
                  'new_nonpositive': int(np.sum((x > 0) & (y <= 0))),
                  'new_highlight': int(np.sum((x < 1) & (y >= 1))),
                  'median_change': float(np.median(y)-np.median(x))} for i, (x, y) in enumerate(zip(a, b))]
    report = {'schema': 'starun-siril.linear-denoise-diagnostics.v1', 'policy': POLICY,
              'request': binding['request'], 'regions': binding['regions'],
              'parent': binding['source'], 'actual_parent': record(session, parent) if parent.is_relative_to(session) else {'path': '@input', **fingerprint(parent, include_path=False)},
              'candidate': record(session, candidate),
              'display_mtf': binding['display_mtf'], 'regional': regional, 'regional_changes': regional_changes, 'multiscale': before_scales,
              'signed_removed_residual': changes, 'residual_display_abs_scale': scales,
              'residual_display': {'quantity': 'Rec.709 luminance(parent - candidate)',
                                   'positive': 'red', 'negative': 'blue', 'zero': 'gray', 'pixel_scale': '1:1'},
              'endpoints_and_brightness': endpoints,
              'color': {'status': 'measured', 'channel_median_changes': [v['median_change'] for v in endpoints]}
                       if a.shape[0] == 3 else {'status': 'unmeasurable', 'reason': 'Monochrome input'},
              'requires_visual_review': True, 'recommended_verdict': 'uncertain',
              'limitations': 'MAD and multiscale amplitudes cannot identify real texture or establish retention'}
    if bridge_check:
        report['deepsnr'] = bridge_check
    if (sha256_file(candidate) != regional['images']['candidate']['sha256']
        or sha256_file(parent) != actual_selection['sha256'] or sha256_file(expected_parent) != selection['sha256']):
        fail('Scientific pixels changed during denoise measurement')
    if write:
        directory.mkdir(parents=True, exist_ok=True)
        if (report_path.exists() or panel_path.exists() or manifest_path.exists()
            or (material is not None and any((directory/name).exists() for name, _ in material['files']))):
            fail('Denoise diagnostics already exist; use a new run ID')
        panel_path.write_bytes(panel)
        for name, data in (material['files'] if material is not None else []):
            (directory/name).write_bytes(data)
    elif material is not None:
        for name, data in material['files']:
            path = directory/name
            if not path.exists() or path.read_bytes() != data:
                fail('Denoise review materials changed; immutable replay cannot repair evidence')
    if material is not None:
        block = _bind_review_materials(session, directory, material)
        if write:
            atomic_write_json(manifest_path, block)
        elif not manifest_path.exists() or load_json(manifest_path) != block:
            fail('Denoise review material manifest changed; immutable replay cannot repair evidence')
        report['review_materials'] = {**block, 'manifest': record(session, manifest_path)}
    if write:
        atomic_write_json(report_path, report)
    elif (load_json(report_path) != report or panel_path.read_bytes() != panel):
        fail('Denoise diagnostics changed; immutable replay cannot repair evidence')
    result = {'policy': POLICY, 'status': 'measured', 'report': record(session, report_path),
              'panel': record(session, panel_path), 'requires_visual_review': True}
    if material is not None:
        result['review_files'] = [report['review_materials']['manifest'], report['review_materials']['index']['view']]
    return result


def run_binding(run):
    return run.get('linear_denoise') or run.get('dualband_transfer', {}).get('linear_denoise')


def diagnostics(run):
    checks = run.get('processing_checks', {})
    return checks.get('linear_denoise') or checks.get('dualband', {}).get('linear_denoise')


def material_records(checked):
    """Every path bound by the actual candidate diagnostics, including the legacy panel."""
    records = {}
    if not checked:
        return records
    for key in ('report', 'panel'):
        if isinstance(checked.get(key), dict):
            records[checked[key]['path']] = checked[key]
    for item in checked.get('review_files', []):
        records[item['path']] = item
    block = checked.get('review_materials') or {}
    for row in [block.get('index', {}).get('view'), block.get('manifest')]:
        if isinstance(row, dict):
            records[row['path']] = row
    for region in block.get('regions', []):
        for row in [region.get('view'), *(sliced.get('view') for sliced in region.get('slices', []))]:
            if isinstance(row, dict):
                records[row['path']] = row
    for highlight in block.get('highlights', []):
        if isinstance(highlight.get('view'), dict):
            records[highlight['view']['path']] = highlight['view']
    return records


def review_paths(session, run):
    binding, checked = run_binding(run), diagnostics(run)
    if not binding:
        return {}
    if not checked:
        fail('Denoise adoption requires the actual candidate diagnostics')
    items = [binding['request'], binding['regions'], checked['report'],
             *(checked['review_files'] if checked.get('review_files') else [checked['panel']])]
    if binding.get('backend') == 'deepsnr':
        items += [binding['contract'], record(session, session/binding['bridge_receipt'])]
    paths = {item['path']: bound_file(session, item) for item in items}
    paths.update({p: session_path(session, p, must_exist=True) for p in binding['display'].values() if isinstance(p, str)})
    return paths


def assessment_skeleton(session, run):
    binding, checked = run_binding(run), diagnostics(run)
    if not binding:
        return None
    regions = load_json(bound_file(session, binding['regions']))['regions']
    materials = load_json(bound_file(session, checked['report'])).get('review_materials') or {}
    views = {row['id']: row for row in materials.get('regions', [])}
    highlights = [{'channel': item['channel'], 'count': item['count'], 'view': item['view']}
                  for item in materials.get('highlights', []) if item['count']]
    structures = []
    for region in (r for r in regions if r['role'] != 'background'):
        row = {'region': {'regions': binding['regions'], 'id': region['id']}, 'retention': None, 'observation': ''}
        if region['id'] in views:
            view = views[region['id']]
            row.update(view=view['view'], slices=view['slices'], residual_scales=view['residual_scales'],
                       highlights=highlights)
        structures.append(row)
    return {'diagnostics': checked['report'], 'noise_benefit': None, 'noise_observation': '',
            'structures': structures, 'adoption_reason': ''}


def validate_assessment(session, run, review):
    try:
        _validate_assessment(session, run, review)
    except (KeyError, TypeError, AttributeError, IndexError, ValueError, OSError) as exc:
        fail('Malformed denoise assessment: '+type(exc).__name__)


def _validate_assessment(session, run, review):
    binding = run_binding(run)
    block = review.get('denoise_assessment')
    if not binding:
        if 'denoise_assessment' in review:
            fail('Denoise assessment requires a proposal-bound candidate')
        return
    expected = assessment_skeleton(session, run)
    if (not isinstance(block, dict) or set(block) != set(expected) or block['diagnostics'] != expected['diagnostics']
        or block['noise_benefit'] not in {'observed', 'not_observed', 'uncertain'}
        or not text(block['noise_observation']) or not text(block['adoption_reason'])
        or not isinstance(block['structures'], list) or len(block['structures']) != len(expected['structures'])):
        fail('Denoise assessment must bind the actual diagnostics, noise benefit and protected structures')
    for row, required in zip(block['structures'], expected['structures']):
        if (not isinstance(row, dict) or set(row) != set(required) or row['region'] != required['region']
            or row['retention'] not in {'retained', 'loss', 'uncertain'} or not text(row['observation'])):
            fail('Review every protected region using its bound native-resolution comparison and signed residual')
    inspected = {r['path'] for r in review['inspected_materials']}
    if not set(review_paths(session, run)) <= inspected:
        fail('Denoise review is missing its actual proposal, regions, previews or residual diagnostics')
    if review['verdict'] == 'accept' and (block['noise_benefit'] != 'observed'
        or any(row['retention'] != 'retained' for row in block['structures'])
        or any(review['gates'][key] != 'pass' for key in ('structure', 'background'))):
        fail('Accept denoise only with reviewable noise benefit and retained structures; otherwise keep the parent')
    report = load_json(bound_file(session, expected['diagnostics']))
    if review['verdict'] == 'accept' and not any(
        channel['min'] != 0 or channel['max'] != 0
        for region in report['signed_removed_residual'] for channel in region['removed_channels']):
        fail('An unchanged candidate cannot demonstrate denoise benefit; keep the parent')


def generate(session_value, request_value, run_id):
    from deep_sky_siril_session import load_session, effective_session_payload, execution_policy_is_current
    session, payload, manifest = load_session(session_value)
    payload = effective_session_payload(session, payload, manifest)
    if not execution_policy_is_current(payload) or payload['execution_policy'].get('linear_denoise') != POLICY:
        fail('New denoise execution requires the current frozen Agent-proposal policy')
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        fail('Invalid denoise run ID')
    request, binding, source, command = request_binding(session, payload, request_value)
    if request.get('revision', {}).get('run_id') == run_id:
        fail('A revision requires a new run ID')
    directory = session_path(session, 'reports/'+run_id, allowed_roots=('reports',))
    script = session/'scripts'/(run_id+'.ssf'); provenance = script.with_suffix('.provenance.json')
    primary = session/'artifacts'/(run_id+'.fit')
    views = [session/'previews'/(run_id+'-'+name+'.jpg') for name in ('parent', 'candidate')]
    if any(p.exists() or p.is_symlink() for p in (directory, script, provenance, primary, *views, session/'runs'/(run_id+'.json'))):
        fail('Denoise generation preserves existing evidence; use a new run ID')
    directory.mkdir()
    atomic_write_json(directory/'denoise-proposal.json', {'binding': binding, 'status': 'skipped' if command is None else 'proposed',
                                                        'reason': request['reason']})
    if command is None:
        return {'status': 'skipped', 'report': str(directory/'denoise-proposal.json'), 'expected': []}
    if request.get('derived_context'):
        fail('Prepared OIII proposals execute through HOO v4 solve/generate')
    q = lambda p: json.dumps(str(p), ensure_ascii=False)
    if binding.get('backend') == 'deepsnr':
        from siril_deepsnr import validate_contract, CONTRACT_SCHEMA
        runtime = load_json(session/'reports/tool-probe.json')['tools']['deepsnr']
        document = {'schema': CONTRACT_SCHEMA, 'source': {'path': str(source), **fingerprint(source, include_path=False)},
                    'runtime': runtime, 'parameters': binding['parameters'], 'work_dir': str(session/'runtime/deepsnr'/run_id)}
        validate_contract(document, source, runtime, session, run_id)
        contract_path, receipt_path = directory/'contract.json', directory/'bridge.json'
        atomic_write_json(contract_path, document)
        command = ['pyscript', q(ROOT/'scripts/siril_deepsnr.py'), '--contract', q(contract_path),
                   '--contract-sha256', sha256_file(contract_path), '--expected-source', q(source), '--receipt', q(receipt_path)]
    rows = ['requires 1.4.4 1.5.0', 'set32bits', '# linear-denoise: '+json.dumps(binding['request']),
            'load '+q(source), 'fmul 1', ' '.join(command), 'stat main', 'save '+q(primary)+' -chksum', 'close']
    for image, view in zip((source, primary), views):
        rows += ['load '+q(image), 'mtf '+' '.join(format(v, '.17g') for v in binding['display_mtf']),
                 'savejpg '+q(view.with_suffix(''))+' 95', 'close']
    script.write_text('\n'.join(rows)+'\n')
    atomic_write_json(provenance, {'schema': 'starun-siril.ssf-provenance.v1', 'contract_version': '1',
        'run_id': run_id, 'protocol': 'restoration.denoise', 'script': {'path': str(script.relative_to(session)), 'sha256': sha256_file(script)},
        'references': protocol_reference_bindings('restoration.denoise'),
        'command_policy': {'path': 'references/command-policy.json', 'sha256': sha256_file(ROOT/'references/command-policy.json')},
        'manual_lookup': request['manual_lookup'], 'rationale': {'applicability': request['reason'],
        'parameter_choices': [request['parameter_reason'], 'Backend='+binding.get('backend', 'nlbayes')]}})
    expected = [primary, *views]
    if binding.get('backend') == 'deepsnr':
        expected.append(receipt_path)
    return {'status': 'proposed', 'script': str(script), 'primary_output': str(primary), 'expected': list(map(str, expected))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    analyze = commands.add_parser('analyze')
    analyze.add_argument('--session', required=True)
    analyze.add_argument('--source', required=True)
    analyze.add_argument('--regions', required=True)
    analyze.add_argument('--display-mtf', nargs=3, type=float, required=True)
    analyze.add_argument('--derived-context')
    create = commands.add_parser('generate')
    create.add_argument('--session', required=True)
    create.add_argument('--request', required=True)
    create.add_argument('--run-id', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'generate':
            result = generate(args.session, args.request, args.run_id)
        else:
            from deep_sky_siril_session import load_session, effective_session_payload
            session, payload, manifest = load_session(args.session)
            payload = effective_session_payload(session, payload, manifest)
            context = record(session, session_path(session, args.derived_context, must_exist=True, allowed_roots=('reports',))) if args.derived_context else None
            source = Path(payload['input']['path']) if args.source == '@input' else session_path(session, args.source, must_exist=True)
            binding = {'path': '@input' if args.source == '@input' else str(source.relative_to(session)), 'sha256': sha256_file(source)}
            regions = record(session, session_path(session, args.regions, must_exist=True, allowed_roots=('reports',)))
            result, _ = analyze_source(session, payload, binding, regions, args.display_mtf, context)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    except (ContractError, OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, str(exc)+'\n')


if __name__ == '__main__':
    main()
